export type UiMethod =
  | "health"
  | "snapshot"
  | "export_records"
  | "read_image"
  | "preview_demo"
  | "start_run"
  | "control_run"
  | "open_simulation"
  | "extract_profile"
  | "save_profile"
  | "get_profile"
  | "preview_run"
  | "get_reply_settings"
  | "save_reply_settings"
  | "preview_reply"
  | "browser_status";
export interface TaskConfig {
  channels: string[];
  target_roles: string[];
  employment_type: string;
  boss_mode?: string;
  execution_mode: string;
  max_contacts: number;
  filters: string;
  window_title: string;
  company_table?: string;
  company_rows?: { company: string; url: string; source_row: number }[];
  resume_path?: string;
  profile_version?: string;
}
// UI 白名单在原生端同步核验。
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
  config: {
    target_roles: string[];
    channels: string[];
    simulation?: boolean;
    execution_mode?: string;
    employment_type?: string;
  };
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
