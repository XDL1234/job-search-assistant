export type UiMethod =
  | "health"
  | "snapshot"
  | "export_records"
  | "read_image"
  | "start_run"
  | "control_run"
  | "open_simulation"
  | "extract_profile"
  | "save_profile"
  | "get_profile"
  | "save_personal_info"
  | "get_resume"
  | "import_resume"
  | "extract_resume_profile"
  | "preview_resume"
  | "open_resume"
  | "get_job_preferences"
  | "save_job_preferences"
  | "inspect_company_table"
  | "open_company_site"
  | "preview_run"
  | "get_reply_settings"
  | "save_reply_settings"
  | "preview_reply"
  | "attention_update"
  | "browser_status";
export type EmploymentType = "internship" | "campus" | "full_time" | "part_time" | "any";
export interface BossFilters {
  city?: string;
  province?: string;
  district?: string;
  business_area?: string;
  metro_line?: string;
  metro_station?: string;
  query?: string;
  positions?: string[][];
  salary?: string;
  experience?: string;
  degree?: string;
  industries?: string[];
  company_size?: string;
  financing?: string;
}
export interface JobPreferences {
  target_roles: string[];
  employment_type: EmploymentType;
  boss_filters: BossFilters;
}
export interface TaskConfig {
  web_submit?: boolean;
  channels: string[];
  target_roles: string[];
  employment_type: string;
  boss_mode?: string;
  execution_mode: string;
  max_contacts: number;
  filters: string;
  cities?: string[];
  boss_filters?: BossFilters;
  window_title: string;
  company_table?: string;
  reviewed_company_table_sha256?: string;
  company_rows?: { company: string; url: string; job?: string; source_row: number }[];
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
  started_at?: string | null;
  updated_at?: string | null;
  config: {
    target_roles: string[];
    channels: string[];
    web_submit?: boolean;
    simulation?: boolean;
    execution_mode?: string;
    employment_type?: string;
    boss_mode?: string;
    boss_filters?: BossFilters;
    cities?: string[];
    filters?: string;
    max_contacts?: number;
    poll_seconds?: number;
    window_title?: string;
    company_table?: string;
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
  url?: string;
  started?: string | null;
  updated: string;
}
export interface RecordedAction {
  id?: string;
  run_id?: string;
  attempt_id: string;
  kind: string;
  status: string;
  evidence_id?: string | null;
  reason?: string;
  updated: string;
}
export interface JobReview {
  company: string;
  job: string;
  city?: string;
  url?: string;
  salary?: string;
  facts?: string[];
  reason: string;
}
export interface Snapshot {
  runs: Run[];
  attempts: Attempt[];
  attention: {
    id: string;
    subject: string;
    reason: string;
    resolved: number;
    created?: string;
    job_review?: JobReview;
  }[];
  messages?: { conversation_id: string; message_key: string; text: string; created?: string }[];
  conversations?: { id: string; status: string; latest_message: string; reason: string; updated?: string }[];
  notes?: { id: string; attention_id: string; message_key: string; text: string; created: string }[];
  actions?: RecordedAction[];
  run_attempts?: { run_id: string; attempt_id: string }[];
  activity?: { run_id: string; operation: string; phase: string; updated?: string; fresh_after?: number | null }[];
  reply_targets?: { run_id: string; conversation: string; attempt_id: string; message_key: string; text: string }[];
  fields: {
    attempt_id: string;
    name: string;
    value: string;
    source: string;
    evidence_id: string | null;
  }[];
  evidence: { id: string; attempt_id: string; kind: string; created?: string }[];
  sessions?: {
    run_id: string;
    thread_id: string | null;
    state: string;
    error: string;
  }[];
  frame: { id: string; captured_at: number; title: string; run_id?: string } | null;
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

export interface BrowserState {
  connected: boolean;
  connecting?: boolean;
  connection_error?: string | null;
  installed?: boolean;
  pinned_target?: string | null;
  url?: string | null;
  tabs?: { targetId: string; title?: string; url: string }[];
  preview_state?: string;
}
