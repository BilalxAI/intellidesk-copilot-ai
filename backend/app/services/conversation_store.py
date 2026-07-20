"""Conversation/session storage (SQLite by default, MySQL for production).

Design goal (per your requirement):
- Keep schema minimal: conversation_id, user_id, session_id, timestamps + messages
- Sessions expire after inactivity (SESSION_EXPIRY_MINUTES)
- No guided-mode/category state stored in DB

Public API:
- get_conversation_store() -> ConversationStore
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol, Tuple
from urllib.parse import urlparse, unquote
from uuid import uuid4

from app.config import DATABASE_URL, MAX_MESSAGES_PER_CONVERSATION, SESSION_EXPIRY_MINUTES

logger = logging.getLogger(__name__)


class ConversationStore(Protocol):
    def get_or_create(self, conversation_id: str, user_id: str) -> Dict[str, Any]: ...

    def add_message(self, session_id: str, user_input: str, assistant_response: str) -> None: ...

    def get_history(self, session_id: str) -> List[Dict[str, Any]]: ...

    def touch(self, session_id: str) -> None: ...


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _now_sqlserver() -> str:
    """Return datetime string compatible with SQL Server DATETIME."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _is_expired(updated_at_iso: str) -> bool:
    try:
        # Handle SQL Server format: "2026-05-01 12:34:56.789"
        if " " in updated_at_iso and "." in updated_at_iso:
            updated = datetime.strptime(updated_at_iso, "%Y-%m-%d %H:%M:%S.%f")
        else:
            updated = datetime.fromisoformat(updated_at_iso)
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
    except Exception:
        return True
    return _now() - updated > timedelta(minutes=SESSION_EXPIRY_MINUTES)


class SQLiteConversationStore:
    def __init__(self, database_url: str = DATABASE_URL):
        self.database_path = self._parse_sqlite_url(database_url)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def get_or_create(self, conversation_id: str, user_id: str) -> Dict[str, Any]:
        if not conversation_id:
            raise ValueError("conversation_id is required")
        if not user_id:
            raise ValueError("user_id is required")

        session = self._get_active_session(conversation_id, user_id)
        if session:
            return session

        session_id = str(uuid4())
        now = _now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO sessions (session_id, conversation_id, user_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (session_id, conversation_id, user_id, now, now),
            )
            conn.commit()

        return self._get_session(session_id)

    def add_message(self, session_id: str, user_input: str, assistant_response: str) -> None:
        now = _now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO messages (session_id, user_input, assistant_response, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (session_id, user_input, assistant_response, now),
            )
            conn.execute("UPDATE sessions SET updated_at = ? WHERE session_id = ?", (now, session_id))
            conn.commit()

        self._trim_messages(session_id)

    def touch(self, session_id: str) -> None:
        now = _now_iso()
        with self._connect() as conn:
            conn.execute("UPDATE sessions SET updated_at = ? WHERE session_id = ?", (now, session_id))
            conn.commit()

    def get_history(self, session_id: str) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT user_input, assistant_response, created_at
                FROM messages
                WHERE session_id = ?
                ORDER BY id DESC
                LIMIT 12
                """,
                (session_id,),
            ).fetchall()

        rows.reverse()
        return [
            {"user": row["user_input"], "assistant": row["assistant_response"], "timestamp": row["created_at"]}
            for row in rows
        ]

    def _get_active_session(self, conversation_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT session_id, conversation_id, user_id, created_at, updated_at
                FROM sessions
                WHERE conversation_id = ? AND user_id = ?
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (conversation_id, user_id),
            ).fetchone()
        if not row:
            return None
        if _is_expired(row["updated_at"]):
            return None
        return self._get_session(row["session_id"])

    def _get_session(self, session_id: str) -> Dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT session_id, conversation_id, user_id, created_at, updated_at
                FROM sessions
                WHERE session_id = ?
                """,
                (session_id,),
            ).fetchone()
        if not row:
            raise KeyError("session not found")
        session: Dict[str, Any] = dict(row)
        session["messages"] = self.get_history(session_id)
        return session

    def _trim_messages(self, session_id: str) -> None:
        try:
            with self._connect() as conn:
                count = conn.execute(
                    "SELECT COUNT(*) FROM messages WHERE session_id = ?",
                    (session_id,),
                ).fetchone()[0]
                if count <= MAX_MESSAGES_PER_CONVERSATION:
                    return
                to_delete = count - MAX_MESSAGES_PER_CONVERSATION
                conn.execute(
                    """
                    DELETE FROM messages
                    WHERE session_id = ? AND id IN (
                        SELECT id FROM messages WHERE session_id = ? ORDER BY id ASC LIMIT ?
                    )
                    """,
                    (session_id, session_id, to_delete),
                )
                conn.commit()
        except Exception as exc:
            logger.error("SQLite trim_messages error: %s", exc)

    def _init_db(self) -> None:
        with self._lock:
            with self._connect() as conn:
                # If an older schema exists (messages table without session_id), recreate tables.
                try:
                    existing_cols = {
                        row[1]
                        for row in conn.execute("PRAGMA table_info(messages)").fetchall()
                    }
                    if existing_cols and "session_id" not in existing_cols:
                        conn.execute("DROP TABLE IF EXISTS messages")
                        conn.execute("DROP TABLE IF EXISTS sessions")
                        conn.commit()
                except Exception:
                    # PRAGMA/table may not exist yet; ignore and continue.
                    pass

                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS sessions (
                        session_id TEXT PRIMARY KEY,
                        conversation_id TEXT NOT NULL,
                        user_id TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        session_id TEXT NOT NULL,
                        user_input TEXT NOT NULL,
                        assistant_response TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_conv_user ON sessions(conversation_id, user_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_session_id ON messages(session_id)")
                conn.commit()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _parse_sqlite_url(self, database_url: str) -> Path:
        """
        Accept common SQLite URL forms:
        - sqlite:///relative/or/absolute/path.db
        - sqlite:////absolute/path.db (POSIX absolute path)
        - sqlite:///C:/absolute/windows/path.db
        - sqlite:///:memory:
        """
        if not database_url or not database_url.startswith("sqlite:"):
            raise ValueError("SQLiteConversationStore requires a sqlite:// DATABASE_URL")

        # Strip query string (e.g. ?mode=ro)
        base = database_url.split("?", 1)[0]

        if base in {"sqlite:///:memory:", "sqlite:///:memory"}:
            return Path(":memory:")

        if base.startswith("sqlite:////"):
            # POSIX absolute path (four slashes)
            path_str = base.replace("sqlite:////", "/", 1)
            return Path(path_str)

        if base.startswith("sqlite:///"):
            path_str = base.replace("sqlite:///", "", 1)
            return Path(path_str)

        raise ValueError(
            "Unsupported sqlite DATABASE_URL format. Use sqlite:///path.db or sqlite:////absolute/path.db"
        )


class MySQLConversationStore:
    """MySQL-backed store. Assumes schema already exists (no migrations)."""

    def __init__(self, database_url: str):
        self.database_url = database_url
        self._parsed = urlparse(database_url)

    def get_or_create(self, conversation_id: str, user_id: str) -> Dict[str, Any]:
        if not conversation_id:
            raise ValueError("conversation_id is required")
        if not user_id:
            raise ValueError("user_id is required")

        session = self._get_active_session(conversation_id, user_id)
        if session:
            return session

        session_id = str(uuid4())
        now = _now_iso()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO sessions (session_id, conversation_id, user_id, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s)
                    """,
                    (session_id, conversation_id, user_id, now, now),
                )
            conn.commit()

        return self._get_session(session_id)

    def add_message(self, session_id: str, user_input: str, assistant_response: str) -> None:
        now = _now_iso()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO messages (session_id, user_input, assistant_response, created_at)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (session_id, user_input, assistant_response, now),
                )
                cur.execute("UPDATE sessions SET updated_at = %s WHERE session_id = %s", (now, session_id))
            conn.commit()

        self._trim_messages(session_id)

    def touch(self, session_id: str) -> None:
        now = _now_iso()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE sessions SET updated_at = %s WHERE session_id = %s", (now, session_id))
            conn.commit()

    def get_history(self, session_id: str) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT user_input, assistant_response, created_at
                    FROM messages
                    WHERE session_id = %s
                    ORDER BY id DESC
                    LIMIT 12
                    """,
                    (session_id,),
                )
                rows = cur.fetchall()

        rows.reverse()
        return [{"user": r[0], "assistant": r[1], "timestamp": str(r[2])} for r in rows]

    def _get_active_session(self, conversation_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT session_id, updated_at
                    FROM sessions
                    WHERE conversation_id = %s AND user_id = %s
                    ORDER BY updated_at DESC
                    LIMIT 1
                    """,
                    (conversation_id, user_id),
                )
                row = cur.fetchone()

        if not row:
            return None
        if _is_expired(str(row[1])):
            return None
        return self._get_session(row[0])

    def _get_session(self, session_id: str) -> Dict[str, Any]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT session_id, conversation_id, user_id, created_at, updated_at
                    FROM sessions
                    WHERE session_id = %s
                    """,
                    (session_id,),
                )
                row = cur.fetchone()

        if not row:
            raise KeyError("session not found")

        session: Dict[str, Any] = {
            "session_id": row[0],
            "conversation_id": row[1],
            "user_id": row[2],
            "created_at": str(row[3]),
            "updated_at": str(row[4]),
        }
        session["messages"] = self.get_history(session_id)
        return session

    def _trim_messages(self, session_id: str) -> None:
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) FROM messages WHERE session_id = %s", (session_id,))
                    count = int(cur.fetchone()[0])
                    if count <= MAX_MESSAGES_PER_CONVERSATION:
                        return
                    to_delete = count - MAX_MESSAGES_PER_CONVERSATION
                    cur.execute(
                        """
                        DELETE FROM messages
                        WHERE id IN (
                            SELECT id FROM (
                                SELECT id FROM messages
                                WHERE session_id = %s
                                ORDER BY id ASC
                                LIMIT %s
                            ) AS t
                        )
                        """,
                        (session_id, to_delete),
                    )
                conn.commit()
        except Exception as exc:
            logger.error("MySQL trim_messages error: %s", exc)

    def _connect(self):
        try:
            import pymysql
        except Exception as exc:  # pragma: no cover
            raise RuntimeError("MySQL support requires pymysql (install requirements-prod.txt)") from exc

        host = self._parsed.hostname or "localhost"
        port = int(self._parsed.port or 3306)
        user = unquote(self._parsed.username or "")
        password = unquote(self._parsed.password or "")
        db = (self._parsed.path or "/").lstrip("/")
        if not db:
            raise ValueError("MySQL DATABASE_URL must include database name, e.g. mysql://user:pass@host:3306/db")

        return pymysql.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=db,
            autocommit=False,
            charset="utf8mb4",
        )


class SQLServerConversationStore:
    """SQL Server-backed store using ODBC. Assumes schema already exists (no migrations)."""

    def __init__(self, database_url: str):
        self.database_url = database_url
        self._trim_delete_allowed = True
        self._trim_permission_warned = False

    def get_or_create(self, conversation_id: str, user_id: str) -> Dict[str, Any]:
        if not conversation_id:
            raise ValueError("conversation_id is required")
        if not user_id:
            raise ValueError("user_id is required")

        session = self._get_active_session(conversation_id, user_id)
        if session:
            return session

        session_id = str(uuid4())
        now = _now_sqlserver()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO itsupport.sessions (session_id, conversation_id, user_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (session_id, conversation_id, user_id, now, now),
                )
            conn.commit()

        return self._get_session(session_id)

    def add_message(self, session_id: str, user_input: str, assistant_response: str) -> None:
        now = _now_sqlserver()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO itsupport.messages (session_id, user_input, assistant_response, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (session_id, user_input, assistant_response, now),
                )
                cur.execute("UPDATE itsupport.sessions SET updated_at = ? WHERE session_id = ?", (now, session_id))
            conn.commit()

        self._trim_messages(session_id)

    def touch(self, session_id: str) -> None:
        now = _now_sqlserver()
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("UPDATE itsupport.sessions SET updated_at = ? WHERE session_id = ?", (now, session_id))
            conn.commit()

    def get_history(self, session_id: str) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT TOP 12 user_input, assistant_response, created_at
                    FROM itsupport.messages
                    WHERE session_id = ?
                    ORDER BY id DESC
                    """,
                    (session_id,),
                )
                rows = cur.fetchall()

        rows.reverse()
        return [{"user": r[0], "assistant": r[1], "timestamp": str(r[2])} for r in rows]

    def _get_active_session(self, conversation_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT TOP 1 session_id, updated_at
                    FROM itsupport.sessions
                    WHERE conversation_id = ? AND user_id = ?
                    ORDER BY updated_at DESC
                    """,
                    (conversation_id, user_id),
                )
                row = cur.fetchone()

        if not row:
            return None
        if _is_expired(str(row[1])):
            return None
        return self._get_session(row[0])

    def _get_session(self, session_id: str) -> Dict[str, Any]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT session_id, conversation_id, user_id, created_at, updated_at
                    FROM itsupport.sessions
                    WHERE session_id = ?
                    """,
                    (session_id,),
                )
                row = cur.fetchone()

        if not row:
            raise KeyError("session not found")

        session: Dict[str, Any] = {
            "session_id": row[0],
            "conversation_id": row[1],
            "user_id": row[2],
            "created_at": str(row[3]),
            "updated_at": str(row[4]),
        }
        session["messages"] = self.get_history(session_id)
        return session

    def _trim_messages(self, session_id: str) -> None:
        if not self._trim_delete_allowed:
            return
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT COUNT(*) FROM itsupport.messages WHERE session_id = ?", (session_id,))
                    count = int(cur.fetchone()[0])
                    if count <= MAX_MESSAGES_PER_CONVERSATION:
                        return
                    to_delete = count - MAX_MESSAGES_PER_CONVERSATION
                    # Some SQL Server ODBC drivers fail on parameterized TOP (?).
                    # Fetch oldest IDs first, then delete by id with standard parameters.
                    cur.execute(
                        """
                        SELECT id
                        FROM itsupport.messages
                        WHERE session_id = ?
                        ORDER BY id ASC
                        """,
                        (session_id,),
                    )
                    ids = [row[0] for row in cur.fetchmany(to_delete)]
                    if ids:
                        cur.executemany(
                            "DELETE FROM itsupport.messages WHERE id = ?",
                            [(msg_id,) for msg_id in ids],
                        )
                conn.commit()
        except Exception as exc:
            msg = str(exc)
            if "DELETE permission was denied" in msg:
                self._trim_delete_allowed = False
                if not self._trim_permission_warned:
                    logger.warning(
                        "SQL Server trim_messages disabled: DELETE permission denied on itsupport.messages"
                    )
                    self._trim_permission_warned = True
                return
            logger.error("SQL Server trim_messages error: %s", exc)

    def _connect(self):
        try:
            import pyodbc
        except Exception as exc:
            raise RuntimeError("SQL Server support requires pyodbc (install requirements-prod.txt)") from exc

        conn_str = self.database_url
        if conn_str.startswith("mssql://"):
            parsed = urlparse(conn_str)
            driver = parsed.path.lstrip("/") if parsed.path else "SQL Server"
            parts = [
                f"DRIVER={{{driver}}}",
                f"SERVER={parsed.hostname}",
                f"DATABASE={parsed.path.lstrip('/')}",
            ]
            if parsed.username:
                parts.append(f"UID={unquote(parsed.username)}")
            if parsed.password:
                parts.append(f"PWD={unquote(parsed.password)}")
            if parsed.port:
                parts.append(f"PORT={parsed.port}")
            parts.append("TrustServerCertificate=yes")
            conn_str = ";".join(parts)

        return pyodbc.connect(conn_str, autocommit=False)


def _build_store(database_url: str) -> ConversationStore:
    lower = (database_url or "").lower()
    if lower.startswith("mysql://"):
        return MySQLConversationStore(database_url)
    if lower.startswith("mssql://") or (lower.startswith("driver=") and "server" in lower):
        return SQLServerConversationStore(database_url)
    return SQLiteConversationStore(database_url)


_store: ConversationStore = _build_store(DATABASE_URL)


def get_conversation_store() -> ConversationStore:
    return _store
