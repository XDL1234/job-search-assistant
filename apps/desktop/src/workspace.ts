import type { Run, Snapshot } from "./contracts";

export const isActiveRun = (run: Run) => run.status === "running" || run.status === "watching";

export function runName(run: Run) {
  if (run.config.simulation) return "本地演示任务";
  const location = run.config.boss_filters?.city || run.config.cities?.join("、");
  const direction = run.config.target_roles.join("、");
  return [location, direction].filter(Boolean).join(" · ") || (run.config.channels.includes("web") ? "公司网申" : "BOSS 投递任务");
}

export function runStatus(run: Run) {
  const labels: Record<string, string> = {
    running: "运行中", watching: "回复值守", paused: "已暂停", stopped: "已停止", completed: "已完成",
  };
  return run.status === "paused" && !run.started_at ? "待启动" : labels[run.status] ?? run.status;
}

export function confirmedActions(snapshot: Snapshot, kind: string, runId?: string) {
  return (snapshot.actions ?? []).filter((action) => action.kind === kind && action.status === "succeeded" &&
    (runId === undefined || action.run_id === runId));
}

export function beginningOfWeek(now: Date) {
  const monday = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  monday.setDate(monday.getDate() - (monday.getDay() + 6) % 7);
  return monday.getTime();
}

export function displayTime(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString("zh-CN", {
    month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false,
  });
}
