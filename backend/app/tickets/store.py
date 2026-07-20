"""
Ticket / technician persistence.

MVP scope: SQLite only. This mirrors app/services/conversation_store.py's
sqlite:// URL handling. Ticket assignment concurrency safety comes from the
single-worker AssignmentQueue (see assignment.py), not from this store, so
SQLite is acceptable for a pilot; move to Postgres before running this with
concurrent technician/manager dashboards under real load.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import TECHNICIANS_PATH, TICKETING_DATABASE_URL

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_sqlite_url(database_url: str) -> Path:
    if not database_url.startswith("sqlite:///"):
        raise ValueError("TICKETING_DATABASE_URL must be a sqlite:/// URL for this MVP store")
    if database_url in {"sqlite:///:memory:", "sqlite:///:memory"}:
        return Path(":memory:")
    return Path(database_url.replace("sqlite:///", "", 1))


class TicketStore:
    def __init__(self, database_url: str = TICKETING_DATABASE_URL):
        self.database_path = _parse_sqlite_url(database_url)
        if str(self.database_path) != ":memory:":
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()
        self._seed_technicians()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS technicians (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    aad_object_id TEXT,
                    skills_json TEXT NOT NULL DEFAULT '[]',
                    capacity INTEGER NOT NULL DEFAULT 3,
                    available INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tickets (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT,
                    user_id TEXT,
                    user_name TEXT,
                    category TEXT,
                    issue TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    status TEXT NOT NULL,
                    assigned_technician_id TEXT,
                    queue_position_at_assignment INTEGER,
                    eta_minutes_at_assignment INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ticket_status_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticket_id TEXT NOT NULL,
                    from_status TEXT,
                    to_status TEXT NOT NULL,
                    changed_by TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_references (
                    conversation_key TEXT PRIMARY KEY,
                    reference_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status)")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tickets_technician ON tickets(assigned_technician_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_history_ticket ON ticket_status_history(ticket_id)"
            )
            conn.commit()

    def _seed_technicians(self) -> None:
        path = Path(TECHNICIANS_PATH)
        if not path.exists():
            return
        with self._lock, self._connect() as conn:
            existing = conn.execute("SELECT COUNT(*) FROM technicians").fetchone()[0]
            if existing:
                return
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.error("Failed to load technicians seed file %s: %s", path, exc)
                return
            now = _now_iso()
            for entry in data:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO technicians
                        (id, name, aad_object_id, skills_json, capacity, available, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, 0, ?, ?)
                    """,
                    (
                        entry["id"],
                        entry["name"],
                        entry.get("aad_object_id") or "",
                        json.dumps(entry.get("skills") or []),
                        int(entry.get("capacity") or 3),
                        now,
                        now,
                    ),
                )
            conn.commit()
            logger.info("Seeded %d technicians from %s", len(data), path)

    # ---------------- Technicians ----------------

    def list_technicians(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM technicians ORDER BY name ASC").fetchall()
        return [self._technician_row_to_dict(conn=None, row=r) for r in rows]

    def get_technician(self, technician_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM technicians WHERE id = ?", (technician_id,)
            ).fetchone()
            if not row:
                return None
            return self._technician_row_to_dict(conn=conn, row=row)

    def set_technician_availability(self, technician_id: str, available: bool) -> Dict[str, Any]:
        now = _now_iso()
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE technicians SET available = ?, updated_at = ? WHERE id = ?",
                (1 if available else 0, now, technician_id),
            )
            conn.commit()
        technician = self.get_technician(technician_id)
        if not technician:
            raise KeyError(f"Unknown technician_id: {technician_id}")
        return technician

    def open_ticket_count(self, conn: sqlite3.Connection, technician_id: str) -> int:
        row = conn.execute(
            """
            SELECT COUNT(*) FROM tickets
            WHERE assigned_technician_id = ? AND status IN ('Assigned', 'InProgress')
            """,
            (technician_id,),
        ).fetchone()
        return int(row[0])

    def _technician_row_to_dict(self, conn: Optional[sqlite3.Connection], row: sqlite3.Row) -> Dict[str, Any]:
        own_conn = conn is None
        if own_conn:
            conn = self._connect()
        try:
            open_count = self.open_ticket_count(conn, row["id"])
        finally:
            if own_conn:
                conn.close()
        return {
            "id": row["id"],
            "name": row["name"],
            "aad_object_id": row["aad_object_id"],
            "skills": json.loads(row["skills_json"] or "[]"),
            "capacity": row["capacity"],
            "available": bool(row["available"]),
            "open_ticket_count": open_count,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    # ---------------- Tickets ----------------

    def next_ticket_id(self, conn: sqlite3.Connection) -> str:
        row = conn.execute("SELECT COUNT(*) FROM tickets").fetchone()
        seq = int(row[0]) + 1
        return f"INC-{seq:05d}"

    def create_ticket_row(
        self,
        conn: sqlite3.Connection,
        conversation_id: str,
        user_id: str,
        user_name: Optional[str],
        category: str,
        issue: str,
        priority: str,
    ) -> str:
        ticket_id = self.next_ticket_id(conn)
        now = _now_iso()
        conn.execute(
            """
            INSERT INTO tickets
                (id, conversation_id, user_id, user_name, category, issue, priority, status,
                 assigned_technician_id, queue_position_at_assignment, eta_minutes_at_assignment,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'New', NULL, NULL, NULL, ?, ?)
            """,
            (ticket_id, conversation_id, user_id, user_name, category, issue, priority, now, now),
        )
        self._record_history(conn, ticket_id, from_status=None, to_status="New", changed_by="system")
        return ticket_id

    def assign_ticket_row(
        self,
        conn: sqlite3.Connection,
        ticket_id: str,
        technician_id: str,
        queue_position: int,
        eta_minutes: int,
    ) -> None:
        now = _now_iso()
        conn.execute(
            """
            UPDATE tickets
            SET status = 'Assigned', assigned_technician_id = ?,
                queue_position_at_assignment = ?, eta_minutes_at_assignment = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (technician_id, queue_position, eta_minutes, now, ticket_id),
        )
        self._record_history(conn, ticket_id, from_status="New", to_status="Assigned", changed_by="system")

    def create_and_assign(
        self,
        category: str,
        issue: str,
        priority: str,
        conversation_id: str,
        user_id: str,
        user_name: Optional[str],
        pick_technician,
    ) -> Dict[str, Any]:
        """Create a ticket and assign it in one transaction.

        `pick_technician(store, conn, category)` selects the technician dict
        (or None). Called from within the single-worker AssignmentQueue, so
        this is race-safe without needing per-call locking beyond the store's
        own connection lock.
        """
        with self._lock, self._connect() as conn:
            ticket_id = self.create_ticket_row(
                conn,
                conversation_id=conversation_id,
                user_id=user_id,
                user_name=user_name,
                category=category,
                issue=issue,
                priority=priority,
            )

            technician = pick_technician(self, conn, category)
            result: Dict[str, Any] = {
                "ticket_id": ticket_id,
                "conversation_id": conversation_id,
                "priority": priority,
                "status": "New",
                "technician": None,
                "queue_position": None,
                "eta_minutes": None,
            }

            if technician is not None:
                from app.config import TICKET_AVG_HANDLE_MINUTES

                queue_position = technician["open_ticket_count"]
                avg_minutes = TICKET_AVG_HANDLE_MINUTES.get(priority, 30)
                eta_minutes = max(5, queue_position * avg_minutes)
                self.assign_ticket_row(
                    conn,
                    ticket_id=ticket_id,
                    technician_id=technician["id"],
                    queue_position=queue_position,
                    eta_minutes=eta_minutes,
                )
                result.update(
                    {
                        "status": "Assigned",
                        "technician": {"id": technician["id"], "name": technician["name"]},
                        "queue_position": queue_position,
                        "eta_minutes": eta_minutes,
                    }
                )

            conn.commit()

        return result

    def list_unassigned_tickets(self) -> List[Dict[str, Any]]:
        """Tickets still stuck in New with nobody assigned, oldest first (FIFO)."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM tickets
                WHERE status = 'New' AND assigned_technician_id IS NULL
                ORDER BY created_at ASC
                """
            ).fetchall()
        return [dict(r) for r in rows]

    def assign_existing_ticket(self, ticket_id: str, pick_technician) -> Optional[Dict[str, Any]]:
        """Attempt to assign an already-created, still-unassigned ticket.

        Used when a technician goes Available and there's a backlog of
        tickets nobody could be found for at creation time. Returns the
        assignment result, or None if no technician is available.
        """
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT category, priority, conversation_id FROM tickets WHERE id = ? AND assigned_technician_id IS NULL",
                (ticket_id,),
            ).fetchone()
            if not row:
                return None

            technician = pick_technician(self, conn, row["category"])
            if technician is None:
                return None

            from app.config import TICKET_AVG_HANDLE_MINUTES

            queue_position = technician["open_ticket_count"]
            avg_minutes = TICKET_AVG_HANDLE_MINUTES.get(row["priority"], 30)
            eta_minutes = max(5, queue_position * avg_minutes)
            self.assign_ticket_row(
                conn,
                ticket_id=ticket_id,
                technician_id=technician["id"],
                queue_position=queue_position,
                eta_minutes=eta_minutes,
            )
            conn.commit()

        return {
            "ticket_id": ticket_id,
            "conversation_id": row["conversation_id"],
            "priority": row["priority"],
            "status": "Assigned",
            "technician": {"id": technician["id"], "name": technician["name"]},
            "queue_position": queue_position,
            "eta_minutes": eta_minutes,
        }

    def get_ticket(self, ticket_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
            if not row:
                return None
            return dict(row)

    def list_tickets(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM tickets WHERE status = ? ORDER BY created_at DESC", (status,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM tickets ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]

    def list_tickets_for_technician(self, technician_id: str) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM tickets
                WHERE assigned_technician_id = ? AND status IN ('Assigned', 'InProgress')
                ORDER BY created_at ASC
                """,
                (technician_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def update_ticket_status(
        self, ticket_id: str, to_status: str, changed_by: str
    ) -> Dict[str, Any]:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT status FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
            if not row:
                raise KeyError(f"Unknown ticket_id: {ticket_id}")
            from_status = row["status"]
            now = _now_iso()
            conn.execute(
                "UPDATE tickets SET status = ?, updated_at = ? WHERE id = ?",
                (to_status, now, ticket_id),
            )
            self._record_history(conn, ticket_id, from_status=from_status, to_status=to_status, changed_by=changed_by)
            conn.commit()
        return self.get_ticket(ticket_id)

    def reopen_ticket(self, ticket_id: str, changed_by: str) -> Dict[str, Any]:
        """Reopen a resolved ticket against the same technician instead of creating a new one."""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT status, assigned_technician_id FROM tickets WHERE id = ?", (ticket_id,)
            ).fetchone()
            if not row:
                raise KeyError(f"Unknown ticket_id: {ticket_id}")
            from_status = row["status"]
            to_status = "Assigned" if row["assigned_technician_id"] else "New"
            now = _now_iso()
            conn.execute(
                "UPDATE tickets SET status = ?, updated_at = ? WHERE id = ?",
                (to_status, now, ticket_id),
            )
            self._record_history(conn, ticket_id, from_status=from_status, to_status=to_status, changed_by=changed_by)
            conn.commit()
        return self.get_ticket(ticket_id)

    def get_history(self, ticket_id: str) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM ticket_status_history WHERE ticket_id = ? ORDER BY id ASC",
                (ticket_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def _record_history(
        self,
        conn: sqlite3.Connection,
        ticket_id: str,
        from_status: Optional[str],
        to_status: str,
        changed_by: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO ticket_status_history (ticket_id, from_status, to_status, changed_by, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (ticket_id, from_status, to_status, changed_by, _now_iso()),
        )

    # ---------------- Conversation references (for proactive Teams notify) ----------------

    def save_conversation_reference(self, conversation_key: str, reference_json: str) -> None:
        now = _now_iso()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO conversation_references (conversation_key, reference_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(conversation_key) DO UPDATE SET
                    reference_json = excluded.reference_json,
                    updated_at = excluded.updated_at
                """,
                (conversation_key, reference_json, now),
            )
            conn.commit()

    def get_conversation_reference(self, conversation_key: str) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT reference_json FROM conversation_references WHERE conversation_key = ?",
                (conversation_key,),
            ).fetchone()
        return row["reference_json"] if row else None


_store: Optional[TicketStore] = None


def get_ticket_store() -> TicketStore:
    global _store
    if _store is None:
        _store = TicketStore()
    return _store
