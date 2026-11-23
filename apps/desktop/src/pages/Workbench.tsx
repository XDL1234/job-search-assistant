import { useEffect, useRef, useState } from "react";
import { ArrowLeft, Circle, ExternalLink, Info, LoaderCircle, MessageSquare, Monitor, Pause, Pencil, Play, Square, X } from "lucide-react";
import { Button } from "../components/ui/button";
import type { Run, Snapshot } from "../contracts";
import { Preview } from "../Preview";
import { Usage } from "../Usage";
import { displayTime, isActiveRun, runName, runStatus } from "../workspace";
import { companyBatchView, CompanyQueueList, type CompanyRowAction } from "./CompanyQueue";

export interface ExecutionLine { runId: string; text: string; at: string; key: number }
const timeText = (value: string) => new Date(value).toLocaleTimeString("zh-CN", { hour12: false });

export function Workbench({ run, snapshot, messages, busy, starting, codexReady, browserReady, frame, previewKey, problem, onBack,
  onControl, onBrowserSetup, onCompanyRow, onProfile, onConnectCodex, onEdit, onWatch, onImage }: {
  run?: Run;
  snapshot: Snapshot;
  messages: ExecutionLine[];
  busy: boolean;
  starting: boolean;
  codexReady: boolean;
  browserReady: boolean;
  frame: string;
  previewKey: string;
  problem?: string;
  onBack: () => void;
  onControl: (action: "running" | "paused" | "stopped" | "prepare_company") => void;
  onBrowserSetup: () => void;
  onCompanyRow?: (action: CompanyRowAction, sourceRow: number) => void;
  onProfile?: () => void;
  onConnectCodex?: () => void;
  onEdit?: () => void;
  onWatch?: () => void;
  onImage: (kind: "frame" | "evidence", id: string) => void;
}) {
  const [selectedTab, setTab] = useState<"progress" | "queue" | "usage">();
  const [screen, setScreen] = useState<"live" | "saved">("live");
  const [expanded, setExpanded] = useState(false);
  const expandButton = useRef<HTMLButtonElement>(null);
  const closePreview = () => {
    setExpanded(false);
    requestAnimationFrame(() => expandButton.current?.focus());
  };
  const [ending, setEnding] = useState(false);
  const [now, setNow] = useState(Date.now);
  const active = starting || Boolean(run && isActiveRun(run));
  useEffect(() => {
    if (!active) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [active]);
  if (!run) return <section className="card"><h1>执行工作台</h1><p>请选择一项投递任务。</p><Button onClick={onBack}>返回投递任务</Button></section>;

  const boss = run.config.channels.includes("boss");
  const company = !run.config.simulation && !boss && run.config.channels.includes("web");
  const queue = company ? snapshot.company_queue : undefined;
  const batch = queue ? companyBatchView(queue, run.config.target_roles) : undefined;
  const tab = selectedTab ?? (batch ? "queue" : "progress");
  const livePreviewKey = batch ? active && batch.fillingRow ? `${run.id}:${batch.fillingRow.source_row}` : ""
    : company && active ? run.id : previewKey;
  const script = run.config.execution_mode === "script";
  const stoppedBoss = boss && run.status === "stopped" && !run.config.simulation;
  const completed = run.status === "stopped" || run.status === "completed";
  const scope = new Set(snapshot.run_attempts?.filter((row) => row.run_id === run.id).map((row) => row.attempt_id));
  const attempts = snapshot.attempts.filter((row) => row.run_id === run.id || scope.has(row.id));
  const activity = snapshot.activity?.find((row) => row.run_id === run.id);
  const start = activity?.fresh_after ? Math.floor(activity.fresh_after * 1000) : Date.parse(run.started_at ?? "");
  const sessionError = problem || snapshot.sessions?.find((item) => item.run_id === run.id)?.error;
  const waitingForSubmission = company && !batch && !active && !sessionError && run.status === "paused" && run.config.web_submit !== true &&
    attempts.some((row) => row.run_id === run.id && row.status === "needs_user" && row.reason === "已填写，等待你在当前网页确认并提交。" &&
      (!Number.isFinite(start) || Date.parse(row.updated) >= start) &&
      !snapshot.attention.some((item) => item.subject === row.id && item.reason === row.reason && item.resolved));
  const status = run.status === "stopped" || (!active && (queue?.phase === "filling" || queue?.phase === "checking")) ? runStatus(run)
    : batch ? batch.status : waitingForSubmission ? "待你提交" : runStatus(run);
  const result = run.confirmed_counts?.[boss ? "greet" : "submit"] ?? 0;
  const elapsed = Math.max(0, Math.floor((now - start) / 1000));
  const elapsedLabel = Number.isFinite(elapsed) ? [Math.floor(elapsed / 3600), Math.floor(elapsed % 3600 / 60), elapsed % 60]
    .map((piece) => String(piece).padStart(2, "0")).join(":") : "";
  const eventRows = messages.filter((item) => item.runId === run.id && item.text.trim());
  const savedFrame = snapshot.frame?.run_id === run.id ? snapshot.frame : undefined;
  const showSaved = !company || Boolean(savedFrame);
  const live = screen === "live" || !showSaved;
  return <section className="workbench-page">
    <header className="workspace-title workbench-title">
      <div><button className="text-button workbench-back" onClick={onBack}><ArrowLeft size={16} /> 投递任务</button>
        <h1>{runName(run)}</h1>
        <div className="workbench-meta"><span>{boss ? "BOSS 直聘" : "公司网申"}</span>
          <span className={`badge ${run.status}`}>{starting ? "正在启动" : status}</span>
          <span>{boss ? `已沟通 ${result}${run.config.max_contacts ? ` / ${run.config.max_contacts}` : ""}`
            : batch ? (run.config.web_submit === true ? "填写并自动提交" : "仅填写，我来提交")
            : company ? (run.config.web_submit === true ? `已自动提交 ${result}` : "仅填写") : `已提交 ${result}`}</span>
          {batch && <><span>可填写 {batch.available}</span><span>待登录 {batch.login}</span><span>已填写待核对 {batch.review}</span></>}
        </div></div>
      <div className="workbench-actions">
        {active ? <Button disabled={busy} onClick={() => onControl("paused")}><Pause size={17} /> 暂停接管</Button>
          : batch ? <>
            {batch.canContinue && run.status !== "stopped" && <Button disabled={busy}
              onClick={() => onControl(batch.prepare ? "prepare_company" : "running")}><Play size={17} />{batch.primary}</Button>}
            {batch.canContinue && !batch.prepare && !codexReady && run.status !== "stopped" && onConnectCodex &&
              <Button variant="secondary" disabled={busy} onClick={onConnectCodex}>连接 Codex</Button>}
          </> : <Button disabled={busy || completed || (!script && !codexReady)} onClick={() => onControl("running")}><Play size={17} /> 开始 / 恢复</Button>}
        {onEdit && !active && (completed || run.status === "paused") &&
          <Button variant="secondary" disabled={busy} onClick={onEdit}><Pencil size={16} /> 编辑任务</Button>}
        {onWatch && boss && script && !active && (run.status === "paused" || run.status === "completed") &&
          <Button variant="secondary" disabled={busy} onClick={onWatch}><MessageSquare size={16} /> 消息值守</Button>}
        <Button variant="secondary" className="workbench-end" disabled={busy || completed} onClick={() => setEnding(true)}><Square size={15} /> 结束任务</Button>
      </div>
    </header>
    {sessionError && <div className="banner error" role="alert">{sessionError}</div>}
    {queue?.reason && queue.reason !== sessionError && <div className="banner" role="status">{queue.reason}</div>}
    <div className="workbench-grid">
      <section className="card workbench-card">
        <div className="workspace-tabs" aria-label="任务详情">
          {([["progress", "执行进度"], ["queue", "本轮队列"], ["usage", "AI 用量"]] as const).map(([id, label]) =>
            <button key={id} aria-pressed={tab === id} onClick={() => setTab(id)}>{label}</button>)}
        </div>
        <div className="workbench-body">
          {tab === "progress" && <>
            <div className="workbench-state">{active ? <LoaderCircle className="spin" size={28} /> : <Circle size={24} />}
              <div><strong>{starting ? "正在启动" : run.status === "watching" ? "正在值守消息" : active ? "任务运行中" : status}</strong>
                {active && !starting && elapsedLabel && <small>已运行 {elapsedLabel}</small>}</div>
            </div>
            <div className="workbench-timeline" role="log" aria-label="执行记录">
              {eventRows.length ? eventRows.map((item) => <div key={item.key}><Circle size={12} aria-hidden="true" /><time dateTime={item.at}>{timeText(item.at)}</time><p>{item.text}</p></div>)
                : attempts.length ? attempts.slice().sort((a, b) => a.updated.localeCompare(b.updated)).map((row) =>
                  <div key={row.id}><Circle size={15} /><time dateTime={row.updated}>{timeText(row.updated)}</time><p><strong>{row.company} · {row.job}</strong>{row.reason && <small>{row.reason}</small>}</p></div>)
                  : <p className="muted">任务开始后，执行记录会显示在这里。</p>}
            </div>
          </>}
          {tab === "queue" && (batch ? <>
            <CompanyQueueList rows={batch.rows} busy={busy} canReopen={!active && run.status !== "stopped"} onAction={onCompanyRow} onProfile={onProfile} />
            <div className="workbench-meta company-queue-results" aria-label="本轮提交结果">
              <span>用户标记已提交 {batch.manual}</span><span>已核验提交 {batch.verified}</span><span>提交待核查 {batch.uncertain}</span>
            </div>
          </> : attempts.length ? <div className="workbench-queue">{attempts.map((row) => <div key={row.id}>
            <strong>{row.company}</strong><span>{row.job}</span><small>{displayTime(row.updated)}</small>
            <p>{row.reason || ({ success: "已完成", failed: "失败", in_progress: "处理中", uncertain: "待核查" } as Record<string, string>)[row.status] || "已记录"}</p>
          </div>)}</div> : <p className="muted">当前暂无已记录的岗位或公司。</p>)}
          {tab === "usage" && <Usage runId={run.id} />}
          <p className="workbench-footnote"><Info size={16} />{script ? "脚本找岗和沟通不消耗 AI 用量。" : "用量来自 Codex 已收到的模型统计。"}</p>
        </div>
      </section>
      <section className={`card workbench-card workbench-screen ${expanded ? "expanded" : ""}`}
        role={expanded ? "dialog" : undefined} aria-modal={expanded || undefined}
        aria-label={expanded ? "浏览器画面" : undefined}
        onKeyDown={(event) => {
          if (!expanded) return;
          if (event.key === "Escape") closePreview();
          if (event.key !== "Tab") return;
          const buttons = event.currentTarget.querySelectorAll<HTMLButtonElement>("button:not(:disabled)");
          const first = buttons[0];
          const last = buttons[buttons.length - 1];
          if (event.target !== (event.shiftKey ? first : last)) return;
          event.preventDefault();
          (event.shiftKey ? last : first)?.focus();
        }}>
        <div className="workbench-screen-title"><h2>浏览器画面</h2>{browserReady && boss && !run.config.simulation && <span className="badge success">已绑定 Chrome</span>}
          {live && livePreviewKey && batch?.fillingRow && <span>{batch.fillingRow.company} · {batch.fillingRow.job}</span>}
          {expanded && <button autoFocus aria-label="关闭放大预览" onClick={closePreview}><X size={20} /></button>}
          {live && livePreviewKey && !expanded && <Button ref={expandButton} variant="secondary" onClick={() => setExpanded(true)}><ExternalLink size={15} /> 放大预览</Button>}
        </div>
        {showSaved && <div className="workspace-tabs" aria-label="画面类型">
          <button aria-pressed={live} onClick={() => setScreen("live")}>实时画面</button>
          <button aria-pressed={!live} onClick={() => setScreen("saved")}>{company ? "历史截图" : "留档截图"}</button>
        </div>}
        <div className="workbench-body">
          {live ? livePreviewKey ? <Preview key={livePreviewKey} runId={company ? run.id : undefined} /> : <div className="workbench-placeholder">
            <Monitor size={30} /><strong>{run.config.simulation ? "本地模拟" : stoppedBoss ? "任务已停止"
              : batch ? queue?.phase === "waiting_login" && run.status !== "stopped" ? "请在 Chrome 中完成登录或验证"
                : queue?.phase === "checking" && active ? "正在检查本批网页"
                : batch.finishedFilling ? "请从队列打开已填写的网页核对" : status
              : waitingForSubmission ? "请在当前网页核对并提交"
              : company ? (completed || run.started_at ? status : "任务启动后显示公司网页")
              : "等待 Chrome 连接"}</strong>
            {stoppedBoss || company
              ? savedFrame && <Button variant="secondary" onClick={() => setScreen("saved")}>{company ? "查看历史截图" : "查看留档截图"}</Button>
              : <Button variant="secondary" disabled={busy} onClick={onBrowserSetup}>{run.config.simulation ? "打开模拟浏览器" : "浏览器连接设置"}</Button>}
          </div> : <div className="workbench-saved">
            {savedFrame && frame ? <button className="image-button" onClick={() => onImage("frame", savedFrame.id)}>
              <img src={frame} alt="当前任务最新截图" />
            </button> : <p className="muted">本轮暂无留档截图。</p>}
          </div>}
          <div className="workbench-screen-footer">{live ? "只读预览" : "当前任务最新截图 · 各岗位留档见投递记录"}</div>
        </div>
      </section>
    </div>
    {ending && <div className="modal-backdrop"><section className="modal" role="dialog" aria-modal="true" aria-label="结束任务">
      <h2>结束这项任务？</h2><p>{onEdit ? "结束后会保留已有记录，可编辑任务条件并确认后继续投递。" : "结束后会保留已有记录。"}</p>
      <div className="actions end"><Button variant="secondary" onClick={() => setEnding(false)}>取消</Button><Button variant="danger" onClick={() => { setEnding(false); onControl("stopped"); }}>确认结束</Button></div>
    </section></div>}
  </section>;
}
