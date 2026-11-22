import { useCallback, useEffect, useRef, useState } from "react";
import { isTauri } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { BriefcaseBusiness, Cable, ChevronRight, ClipboardCheck, ContactRound, FileText, FileUser, House, MessagesSquare, Minus, Send, Square, X } from "lucide-react";
import { native, request } from "./bridge";
import packageInfo from "../package.json";
import { TaskSetup } from "./pages/TaskSetup";
import { Inbox, attentionCategory } from "./pages/Inbox";
import { SettingsContent, ConnectionDiagnostics, type BrowserDiagnostic } from "./pages/SettingsPage";
import { TasksPage } from "./pages/TasksPage";
import { Records } from "./pages/Records";
import { Overview, type OverviewCategory } from "./pages/Overview";
import { Workbench, type ExecutionLine } from "./pages/Workbench";
import { isActiveRun } from "./workspace";
import type { BrowserState, Connection, ExecutorEvent, Run, Snapshot } from "./contracts";

const profileNavigation = [
  ["resume", "简历", FileUser],
  ["profile", "个人信息", ContactRound],
  ["replies", "回复规则", MessagesSquare],
  ["connections", "连接与诊断", Cable],
] as const;
const navigation = [
  ["home", "总览", House],
  ["tasks", "投递任务", Send],
  ["inbox", "待办", ClipboardCheck],
  ["history", "投递记录", FileText],
  ...profileNavigation,
] as const;
type Page = (typeof navigation)[number][0] | "boss" | "companies" | "running";
const empty: Snapshot = { runs: [], attempts: [], attention: [], fields: [], evidence: [], frame: null };
const taskPages = new Set<Page>(["tasks", "boss", "companies", "running"]);

export default function App() {
  const [page, setPage] = useState<Page>("home");
  const [todoCategory, setTodoCategory] = useState<OverviewCategory>();
  const [snapshot, setSnapshot] = useState<Snapshot>(empty);
  const [connection, setConnection] = useState<Connection>({
    state: "disconnected",
    message: "AI 模式需要连接 Codex。脚本模式无需模型。",
  });
  const [browser, setBrowser] = useState<BrowserState>({ connected: false });
  const [diagnostic, setDiagnostic] = useState<BrowserDiagnostic>();
  const [resumeReady, setResumeReady] = useState<boolean>();
  const [runId, setRunId] = useState("");
  const [editingRun, setEditingRun] = useState<Run>();
  const [runProblem, setRunProblem] = useState<{ id: string; text: string }>();
  const [startingRun, setStartingRun] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [image, setImage] = useState("");
  const [savedFrame, setSavedFrame] = useState<{ id: string; url: string }>();
  const [messages, setMessages] = useState<ExecutionLine[]>([]);
  const scroller = useRef<HTMLDivElement>(null);
  const desktop = isTauri();
  const run = snapshot.runs.find((value) => value.id === runId);
  const frameId = page === "running" && snapshot.frame?.run_id === runId ? snapshot.frame?.id : undefined;
  const browserInUse = snapshot.runs.some(isActiveRun);
  const browserReady = Boolean(browser.connected && !browser.connecting && browser.pinned_target);
  const refresh = useCallback(async () => {
    if (!isTauri()) return;
    try {
      const current = await request<Snapshot>("snapshot", { run_id: runId });
      setSnapshot(current);
      setStartingRun((id) => id && current.runs.some((row) => row.id === id && isActiveRun(row)) ? "" : id);
    } catch (failure) {
      setError(String(failure));
    }
  }, [runId]);
  const loadResume = useCallback(() => {
    if (!isTauri()) return;
    void request("get_resume").then((resume) => setResumeReady(Boolean(resume))).catch(() => setResumeReady(undefined));
  }, []);
  useEffect(loadResume, [loadResume]);
  useEffect(() => {
    scroller.current?.scrollTo(0, 0);
  }, [page, runId]);
  useEffect(() => {
    void refresh();
    if (!isTauri()) return;
    const timer = window.setInterval(() => void refresh(), 2500);
    const subscription = listen<ExecutorEvent>("executor-event", (event) => {
      const { runId: id, type, payload, seq } = event.payload;
      if (type === "message" && payload.text) {
        const row = { runId: id, text: String(payload.text), at: new Date().toISOString(), key: seq };
        setMessages((rows) => [...rows, row].slice(-300));
      }
      if (type === "error" || type === "state" && !["starting", "running", "watching"].includes(String(payload.state))) {
        setStartingRun((current) => current === id ? "" : current);
      }
      if (type === "error") setRunProblem({ id, text: String(payload.message || "执行已暂停") });
      if (type !== "message") void refresh();
    });
    return () => {
      window.clearInterval(timer);
      void subscription.then((unlisten) => unlisten());
    };
  }, [refresh]);
  useEffect(() => {
    if (!isTauri()) return;
    void native<Connection>("codex_status").then(setConnection).catch((failure) => setError(String(failure)));
    const subscription = listen<Connection>("codex-status", (event) => setConnection(event.payload));
    return () => { void subscription.then((unlisten) => unlisten()); };
  }, []);
  useEffect(() => {
    if (!desktop || busy) return;
    let active = true;
    const loadBrowser = () => {
      void native<BrowserState>("browser_runtime_status").then((state) => {
        if (active) setBrowser(state);
      }).catch(() => undefined);
    };
    loadBrowser();
    const timer = window.setInterval(loadBrowser, 1500);
    return () => { active = false; window.clearInterval(timer); };
  }, [desktop, busy]);
  useEffect(() => {
    if (!frameId) return;
    let active = true;
    void request<{ url: string }>("read_image", { kind: "frame", identity: frameId }).then((value) => {
      if (active) setSavedFrame({ id: frameId, url: value.url });
    }).catch(() => undefined);
    return () => { active = false; };
  }, [frameId]);

  const perform = async (action: () => Promise<void>) => {
    setError("");
    setNotice("");
    setBusy(true);
    try {
      await action();
      await refresh();
    } catch (failure) {
      setError(String(failure));
    } finally {
      setBusy(false);
    }
  };
  const navigate = (next: Page) => { setNotice(""); setError(""); setPage(next); };
  const openRun = (id: string) => { setEditingRun(undefined); setRunId(id); navigate("running"); };
  const openTask = (channel: "boss" | "web" = "boss") => {
    setEditingRun(undefined);
    navigate(channel === "boss" ? "boss" : "companies");
  };
  const editTask = () => {
    if (!run) return;
    setEditingRun(run);
    navigate(run.config.channels.includes("boss") ? "boss" : "companies");
  };
  const control = (action: string) => perform(async () => {
    setStartingRun(action === "running" || action === "watching" ? runId : "");
    setRunProblem(undefined);
    try {
      await native("executor_control", { runId, action });
    } catch (failure) {
      setStartingRun("");
      setRunProblem({ id: runId, text: failure instanceof Error ? failure.message : String(failure) });
    }
  });
  const exportRecords = () => perform(async () => {
    const path = await native<string | null>("export_records_dialog");
    if (path) setNotice(`记录已导出：${path}`);
  });
  const connectChrome = () => perform(async () => {
    setDiagnostic(undefined);
    setBrowser((current) => ({ ...current, connecting: true, connection_error: null }));
    try {
      setBrowser(await native<BrowserState>("browser_connect"));
    } catch (failure) {
      setBrowser((current) => ({ ...current, connecting: false }));
      throw failure;
    }
  });
  const selectedNav = taskPages.has(page) ? "tasks" : page;
  const title = navigation.find(([key]) => key === selectedNav)?.[1];
  const profilePage = profileNavigation.find(([key]) => key === page);
  const subTitle = editingRun && (page === "boss" || page === "companies") ? "编辑任务"
    : ({ boss: "BOSS 新建投递", companies: "公司网申", running: "执行工作台" } as Partial<Record<Page, string>>)[page];
  const attentionCount = snapshot.attention.filter((row) => !row.resolved).length;
  const windowControl = (command: "minimize" | "toggleMaximize" | "close") => {
    void getCurrentWindow()[command]().catch((failure) => setError(String(failure)));
  };

  return (<div className="shell workspace-shell">
    <aside className="sidebar">
      <div className="brand" title={`求职助手 ${packageInfo.version}`} data-tauri-drag-region><span><BriefcaseBusiness size={22} /></span>求职助手</div>
      <nav aria-label="主导航">
        {navigation.map(([key, label, Icon]) => <button key={key} className={`nav-item ${selectedNav === key ? "selected" : ""}`}
          aria-current={selectedNav === key ? "page" : undefined} onClick={() => {
            if (key === "inbox") setTodoCategory(undefined);
            navigate(key);
          }}>
          <Icon size={21} /><span>{label}</span>{key === "inbox" && attentionCount > 0 && <b aria-label={`${attentionCount} 项未处理`}>{attentionCount}</b>}
        </button>)}
      </nav>
    </aside>
    <main>
      <div className="workspace-topbar" data-tauri-drag-region>
        <div className="workspace-breadcrumb" data-tauri-drag-region><House size={17} />
          <button onClick={() => navigate(selectedNav)}>{title}</button>
          {subTitle && <><ChevronRight size={15} /><strong>{subTitle}</strong></>}
        </div>
        <div className="workspace-top-actions">
          <button onClick={() => navigate("connections")}><i className={`connection-dot ${browser.connected && !browser.connecting ? "connected" : ""}`} />Chrome {browser.connecting ? "连接中" : browser.connected ? "已连接" : "未连接"}</button>
          <button onClick={() => navigate("connections")}><i className={`connection-dot ${connection.state === "ready" ? "connected" : ""}`} />Codex {connection.state === "ready" ? "已连接" : connection.state === "connecting" ? "连接中" : "未连接"}</button>
          {desktop && <div className="workspace-window-controls">
            <button aria-label="最小化" onClick={() => windowControl("minimize")}><Minus size={17} /></button>
            <button aria-label="最大化或还原" onClick={() => windowControl("toggleMaximize")}><Square size={13} /></button>
            <button aria-label="关闭应用" onClick={() => windowControl("close")}><X size={17} /></button>
          </div>}
        </div>
      </div>
      <div className="workspace-scroll" ref={scroller}><div className="workspace-content">
        {!desktop && <div className="banner">浏览器界面预览，执行器未连接。请从桌面应用启动任务。</div>}
        {error && <div className="banner error" role="alert">{error}<button aria-label="关闭错误" onClick={() => setError("")}><X size={16} /></button></div>}
        {notice && <div className="banner" role="status">{notice}</div>}
        {page === "home" && <Overview snapshot={snapshot} resumeReady={resumeReady} classify={attentionCategory}
          onNew={openTask} onRun={openRun} onTodo={(category) => { setTodoCategory(category); navigate("inbox"); }}
          onRecords={() => navigate("history")} onResume={() => navigate("resume")} />}
        {page === "tasks" && <TasksPage snapshot={snapshot} onNew={openTask} onOpen={openRun} />}
        {(page === "boss" || page === "companies") && <TaskSetup key={`${page}:${editingRun?.id ?? "new"}`} channel={page === "boss" ? "boss" : "web"}
          initialRun={editingRun} latestRun={snapshot.runs.find((item) => !item.config.simulation && item.config.channels.includes(page === "boss" ? "boss" : "web"))}
          onSwitchChannel={openTask} onBack={() => editingRun ? openRun(editingRun.id) : navigate("tasks")}
          browserReady={browserReady} codexReady={connection.state === "ready"} onMaterials={navigate}
          onCreated={(id) => { setRunProblem(undefined); openRun(id); void refresh(); }} />}
        {page === "running" && <Workbench key={runId} run={run} snapshot={snapshot} messages={messages} busy={busy}
          starting={Boolean(run && startingRun === run.id && !isActiveRun(run))}
          browserReady={browserReady} codexReady={connection.state === "ready"}
          previewKey={run && !run.config.simulation && run.config.channels.includes("boss") && run.status !== "stopped" && browserReady
            ? `${runId}:${browser.pinned_target}` : ""}
          frame={savedFrame && savedFrame.id === frameId ? savedFrame.url : ""}
          problem={runProblem?.id === runId ? runProblem.text : undefined}
          onBack={() => navigate("tasks")} onControl={(action) => void control(action)}
          onEdit={run && !run.config.simulation ? editTask : undefined}
          onWatch={run && !run.config.simulation && ["paused", "completed"].includes(run.status) ? () => void control("watching") : undefined}
          onBrowserSetup={() => run?.config.simulation ? void perform(async () => { await request("open_simulation", { run_id: runId }); }) : navigate("connections")}
          onImage={(kind, identity) => void perform(async () => { setImage((await request<{ url: string }>("read_image", { kind, identity })).url); })} />}
        {page === "history" && <Records snapshot={snapshot} onExport={exportRecords} exporting={busy} />}
        {page === "inbox" && <Inbox key={todoCategory ?? "all"} snapshot={snapshot} onChanged={refresh} initialCategory={todoCategory} />}
        {profilePage && <><header className="workspace-title"><h1>{profilePage[1]}</h1></header><SettingsContent active={profilePage[0]} onProfile={() => navigate("profile")} onSaved={async () => { loadResume(); await refresh(); }}
          diagnostics={<ConnectionDiagnostics browser={browser} connection={connection} busy={busy} browserInUse={browserInUse} desktop={desktop}
            diagnostic={diagnostic}
            onConnectChrome={() => void connectChrome()}
            onRefreshTabs={() => void perform(async () => { setDiagnostic(undefined); setBrowser(await native<BrowserState>("browser_refresh_tabs")); })}
            onSelectTab={(targetId) => void perform(async () => { setDiagnostic(undefined); setBrowser(await native<BrowserState>("browser_select_tab", { targetId })); })}
            onInspect={() => void perform(async () => setDiagnostic(await native<BrowserDiagnostic>("browser_inspect")))}
            onConnectCodex={(reuseLogin) => void perform(async () => setConnection(await native<Connection>("codex_connect", { reuseLogin: reuseLogin ?? false })))}
            onCancelLogin={() => void perform(async () => setConnection(await native<Connection>("codex_cancel_login")))}
            onLogin={() => void perform(async () => setConnection(await native<Connection>("codex_login")))} />} /></>}
      </div></div>
    </main>
      {image && <div className="modal-backdrop image-modal" role="dialog" aria-modal="true" aria-label="截图预览">
        <button className="image-close" aria-label="关闭图片" onClick={() => setImage("")}><X /></button>
        <img src={image} alt="申请截图证据" />
      </div>}
    </div>
  );
}
