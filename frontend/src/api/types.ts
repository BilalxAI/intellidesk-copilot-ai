export type TicketStatus =
  | "New"
  | "Assigned"
  | "InProgress"
  | "Resolved"
  | "Closed"
  | "Reopened";

export type Priority = "P1" | "P2" | "P3" | "P4";

export interface Technician {
  id: string;
  name: string;
  aad_object_id: string;
  skills: string[];
  capacity: number;
  available: boolean;
  open_ticket_count: number;
  created_at: string;
  updated_at: string;
}

export interface Ticket {
  id: string;
  conversation_id: string;
  user_id: string;
  user_name: string | null;
  category: string;
  issue: string;
  priority: Priority | string;
  status: TicketStatus;
  assigned_technician_id: string | null;
  queue_position_at_assignment: number | null;
  eta_minutes_at_assignment: number | null;
  created_at: string;
  updated_at: string;
}

export interface TicketHistoryEntry {
  id: number;
  ticket_id: string;
  from_status: string | null;
  to_status: string;
  changed_by: string | null;
  created_at: string;
}

export interface LiveEvent {
  type: "ticket_created" | "ticket_updated" | "technician_updated";
  data: Record<string, unknown>;
}
