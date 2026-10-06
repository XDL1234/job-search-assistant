import { invoke, isTauri } from "@tauri-apps/api/core";
import type { UiMethod, WorkerResponse } from "./contracts";

export function unwrap<T>(response: WorkerResponse<T>): T {
  if (!response.ok) throw new Error(response.error?.message || "操作失败");
  if (response.result === undefined) throw new Error("执行器返回不完整结果");
  return response.result;
}

export async function request<T>(
  method: UiMethod,
  params: object = {},
): Promise<T> {
  if (!isTauri()) throw new Error("当前为界面预览。请通过桌面应用连接执行器。");
  return unwrap(
    await invoke<WorkerResponse<T>>("worker_request", {
      request: { id: crypto.randomUUID(), method, params },
    }),
  );
}

export async function native<T>(
  command: string,
  args: Record<string, unknown> = {},
): Promise<T> {
  if (!isTauri()) throw new Error("请在桌面应用中使用此功能");
  return invoke<T>(command, args);
}
