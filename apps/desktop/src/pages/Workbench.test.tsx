import { renderToStaticMarkup } from "react-dom/server";
import { expect, test, vi } from "vitest";
import type { CompanyQueue, CompanyQueueRow, Run, Snapshot } from "../contracts";
import { Workbench } from "./Workbench";

const snapshot: Snapshot = { runs: [], attempts: [], attention: [], fields: [], evidence: [], frame: null };
const company: Run = { id: "company-run", status: "running", created: "", config: { channels: ["web"], target_roles: [] } };

function screen(run: Run, starting = false, previewKey = "", current = snapshot, codexReady = true) {
  return renderToStaticMarkup(<Workbench run={run} snapshot={current} messages={[]} busy={false} starting={starting}
    codexReady={codexReady} browserReady={Boolean(previewKey)} frame="" previewKey={previewKey} onBack={vi.fn()} onControl={vi.fn()}
    onCompanyRow={vi.fn()} onProfile={vi.fn()} onConnectCodex={vi.fn()}
    onBrowserSetup={vi.fn()} onImage={vi.fn()} />);
}

test("公司网申在启动或运行时显示任务专属实时画面，无须绑定 BOSS", () => {
  for (const page of [screen(company), screen({ ...company, status: "paused" }, true)]) {
    expect(page).toContain('aria-label="公司网申实时画面"');
    expect(page).toContain("放大预览");
    expect(page).not.toContain("浏览器连接设置");
    expect(page).not.toContain("留档截图");
  }
  expect(screen({ ...company, status: "paused" })).toContain("任务启动后显示公司网页");
  const paused = screen({ ...company, status: "paused", started_at: "2026-11-21T00:00:00Z" });
  expect(paused).toContain("已暂停");
  expect(paused).not.toContain("浏览器连接设置");
  expect(paused).not.toContain("<canvas");
});

test("仅填写的公司网申停在手动提交时显示当前网页提示，历史截图仍可查看", () => {
  const paused = { ...company, status: "paused", started_at: "2026-11-21T00:00:00Z" };
  const ready: Snapshot = { ...snapshot, attempts: [{ id: "attempt", run_id: company.id, company: "示例公司",
    job: "示例岗位", channel: "web", status: "needs_user", reason: "已填写，等待你在当前网页确认并提交。", updated: "2026-11-21T01:00:00Z" }] };
  const manual = screen(paused, false, "", ready);
  expect(manual).toContain("待你提交");
  expect(manual).toContain("请在当前网页核对并提交");
  const resumed = { run_id: company.id, operation: "", phase: "", fresh_after: Date.parse("2026-11-21T02:00:00Z") / 1000 };
  expect(screen(paused, false, "", { ...ready, activity: [resumed] })).not.toContain("请在当前网页核对并提交");
  const resolved = { id: "todo", subject: "attempt", reason: ready.attempts[0].reason, resolved: 1 };
  expect(screen(paused, false, "", { ...ready, attention: [resolved] })).not.toContain("请在当前网页核对并提交");
  const auto = screen({ ...paused, config: { ...company.config, web_submit: true } }, false, "", ready);
  expect(auto).toContain("已自动提交 0");
  expect(auto).not.toContain("待你提交");
  expect(screen(paused, false, "", { ...ready, frame: { id: "frame", captured_at: 0, title: "历史截图", run_id: company.id } }))
    .toContain("查看历史截图");
});

test("BOSS 实时画面仍取自绑定的 Chrome 标签页", () => {
  const boss: Run = { ...company, config: { ...company.config, channels: ["boss"] } };
  expect(screen(boss)).toContain("浏览器连接设置");
  expect(screen(boss, false, "pinned-boss-tab")).toContain('aria-label="BOSS 实时画面"');
});

const batchRow = (source_row: number, company: string, state: CompanyQueueRow["state"], tab_available = true): CompanyQueueRow =>
  ({ source_row, company, state, tab_available, job: "软件开发", reason: "", url: "https://example.test" });
function batchScreen(phase: CompanyQueue["phase"], rows: CompanyQueueRow[], reason = "", active = false) {
  return screen({ ...company, status: active ? "running" : "paused" }, false, "",
    { ...snapshot, company_queue: { phase, rows, reason } }, false);
}

test("准备只需要 Chrome，按原表顺序列出公司，等待登录时不订阅画面", () => {
  const a = batchRow(2, "公司甲", "pending_open", false);
  const b = batchRow(5, "公司乙", "pending_open", false);
  const preparing = batchScreen("prepare", [b, a]);
  expect(preparing).toContain("打开本批网页");
  expect(preparing).not.toContain("disabled=\"\"");
  expect(preparing.indexOf("公司甲")).toBeLessThan(preparing.indexOf("公司乙"));
  expect(preparing).toContain("仅填写，我来提交");
  expect(preparing).not.toContain("<canvas");
  const login = batchScreen("waiting_login", [{ ...a, tab_available: true, state: "opened" }, b]);
  expect(login).toContain("我已登录，开始填写");
  expect(login).toContain("前往登录");
  expect(login).not.toContain("<canvas");
  const recheck = batchScreen("waiting_login", [{ ...a, state: "login_required", tab_available: true }, b], "请先完成登录");
  expect(recheck).toContain("继续检查");
  expect(recheck).toContain("请先完成登录");
});

test("只显示当前填写行的预览，核对、手动提交与待核查保留独立状态", () => {
  const filling = batchScreen("filling", [batchRow(2, "公司甲", "login_required"), batchRow(5, "公司乙", "filling")], "", true);
  expect(filling).toContain('aria-label="公司网申实时画面"');
  expect(filling).toContain("公司乙 · 软件开发");
  expect(batchScreen("checking", [batchRow(2, "公司甲", "opened")], "", true)).not.toContain("<canvas");
  const complete = batchScreen("waiting_review", [batchRow(2, "公司甲", "ready_for_review", false),
    batchRow(5, "公司乙", "ready_for_review"), batchRow(6, "公司丙", "manual_submitted"),
    batchRow(8, "公司丁", "uncertain")]);
  expect(complete).toContain("已填写待核对 2");
  expect(complete).toContain("用户标记已提交 1");
  expect(complete).toContain("已核验提交 0");
  expect(complete).toContain("提交待核查 1");
  expect(complete).toContain("填写结束，待你核对");
  expect(complete).toContain("打开核对");
  expect(complete).toContain("重新打开检查");
  expect(complete).toContain("标记已手动提交");
  expect(complete).not.toContain("开始 / 恢复");
  expect(complete).not.toContain("<canvas");
});

test("待核对表单占满工作位时保留核对入口和下一批入口", () => {
  const page = batchScreen("waiting_review", [batchRow(2, "公司甲", "ready_for_review"),
    batchRow(3, "公司乙", "pending_open", false)], "请核对并关闭已处理的标签，再打开下一批");
  expect(page).toContain("打开下一批网页");
  expect(page).toContain("打开核对");
  expect(page).toContain("请核对并关闭已处理的标签，再打开下一批");
  const draft = batchScreen("waiting_login", [{ ...batchRow(7, "公司丙", "pending_open", false), attempt_id: "stored-draft" }]);
  expect(draft).toContain("继续检查");
  expect(draft).not.toContain("打开下一批网页");
});
