import { renderToStaticMarkup } from "react-dom/server";
import { expect, test, vi } from "vitest";
import type { Run, Snapshot } from "../contracts";
import { Workbench } from "./Workbench";

const snapshot: Snapshot = { runs: [], attempts: [], attention: [], fields: [], evidence: [], frame: null };
const company: Run = { id: "company-run", status: "running", created: "", config: { channels: ["web"], target_roles: [] } };

function screen(run: Run, starting = false, previewKey = "", current = snapshot) {
  return renderToStaticMarkup(<Workbench run={run} snapshot={current} messages={[]} busy={false} starting={starting}
    codexReady browserReady={Boolean(previewKey)} frame="" previewKey={previewKey} onBack={vi.fn()} onControl={vi.fn()}
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
    job: "示例岗位", channel: "web", status: "needs_user", reason: "已填写，等待你在当前网页确认并提交。", updated: "" }] };
  const manual = screen(paused, false, "", ready);
  expect(manual).toContain("待你提交");
  expect(manual).toContain("请在当前网页核对并提交");
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
