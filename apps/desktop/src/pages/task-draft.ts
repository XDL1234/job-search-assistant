import type { EmploymentType, JobPreferences, Run } from "../contracts";

type Channel = "boss" | "web";
export type DraftMode = "script" | "filtered" | "screened";
export type TaskDraft = {
  mode: DraftMode;
  scope: "saved" | "current";
  limit: number;
  roles?: string;
  tablePath: string;
};

export function preferencesFromRun(run?: Run): JobPreferences | null {
  const config = run?.config;
  if (!config) return null;
  return {
    employment_type: (config.employment_type ?? "any") as EmploymentType,
    target_roles: config.target_roles,
    boss_filters: config.boss_filters ?? (config.cities?.length === 1 ? { city: config.cities[0] } : {}),
  };
}

export function taskDraftFromRun(run?: Run): TaskDraft {
  const value = run?.config;
  return {
    mode: value?.execution_mode !== "ai" ? "script" : value.boss_mode === "screened" ? "screened" : "filtered",
    scope: value && !value.boss_filters && value.cities?.length !== 1 ? "current" : "saved",
    limit: value?.max_contacts ?? 3,
    roles: value?.target_roles.join("、"),
    tablePath: value?.company_table ?? "",
  };
}

export function storedDraft<T>(key: string): T | undefined {
  try {
    const json = localStorage.getItem(key);
    return json ? JSON.parse(json) as T : undefined;
  } catch {
    return undefined;
  }
}

export function rememberDraft(key: string, value: unknown) {
  try {
    if (value === undefined) localStorage.removeItem(key);
    else localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // 受限预览中仅保留当前表单状态。
  }
}

export const taskDraftKey = (channel: Channel) => `job-assistant.task-draft.v1.${channel}`;
