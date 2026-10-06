export type UiMethod =
  | "health"
  | "snapshot"
  | "export_records"
  | "read_image"
  | "preview_demo"
  | "start_run"
  | "control_run"
  | "open_simulation";
export interface WorkerResponse<T> {
  id: string;
  ok: boolean;
  result?: T;
  error?: { code: string; message: string };
}
export interface Run {
  id: string;
  status: string;
  created: string;
  config: { target_roles: string[]; channels: string[]; simulation?: boolean };
}
export interface Attempt {
  id: string;
  run_id: string;
  company: string;
  job: string;
  channel: string;
  status: string;
  reason: string;
  updated: string;
}
export interface Snapshot {
  runs: Run[];
  attempts: Attempt[];
  attention: {
    id: string;
    subject: string;
    reason: string;
    resolved: number;
  }[];
  fields: {
    attempt_id: string;
    name: string;
    value: string;
    source: string;
    evidence_id: string | null;
  }[];
  evidence: { id: string; attempt_id: string; kind: string }[];
  sessions?: {
    run_id: string;
    thread_id: string | null;
    state: string;
    error: string;
  }[];
  frame: { id: string; captured_at: number; title: string } | null;
}
export interface Connection {
  state:
    | "disconnected"
    | "connecting"
    | "ready"
    | "not_installed"
    | "login_required"
    | "logging_in"
    | "error";
  message: string;
  version?: string;
}
export interface ExecutorEvent {
  runId: string;
  seq: number;
  type: "state" | "message" | "attention" | "error";
  payload: Record<string, unknown>;
}
