import { useCallback, useEffect, useState } from "react";
import { isTauri } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { BriefcaseBusiness, ChevronRight, ClipboardCheck, FileText, House, Minus, Send, Settings, Square, X } from "lucide-react";
import { Button } from "./components/ui/button";
import { native, request } from "./bridge";
import packageInfo from "../package.json";
import { TaskSetup } from "./pages/TaskSetup";
import { Inbox, attentionCategory } from "./pages/Inbox";
import { SettingsPage, ConnectionDiagnostics, type SettingsTab, type BrowserDiagnostic } from "./pages/SettingsPage";
import { TasksPage } from "./pages/TasksPage";
import { Records } from "./pages/Records";
import { Overview, type OverviewCategory } from "./pages/Overview";
import { Workbench, type ExecutionLine } from "./pages/Workbench";
import { isActiveRun } from "./workspace";
import type { BrowserState, Connection, ExecutorEvent, Run, Snapshot } from "./contracts";

const navigation = [
  ["home", "总览", House],
  ["tasks", "投递任务", Send],
  ["inbox", "待办", ClipboardCheck],
  ["history", "投递记录", FileText],
] as const;
type Page = (typeof navigation)[number][0] | "settings" | "boss" | "companies" | "running";
const empty: Snapshot = { runs: [], attempts: [], attention: [], fields: [], evidence: [], frame: null };
const taskPages = new Set<Page>(["tasks", "boss", "companies", "running"]);

export default function App() {
  const [page, setPage] = useState<Page>("home");
  const [settingsTab, setSettingsTab] = useState<SettingsTab>("profile");
  const [todoCategory, setTodoCategory] = useState<OverviewCategory>();
  const [snapshot, setSnapshot] = useState<Snapshot>(empty);
  const [connection, setConnection] = useState<Connection>({
    state: "disconnected",
    message: "AI 模式需要连接 Codex。脚本模式无需模型。",
  });
  const [browser, setBrowser] = useState<BrowserState>({ connected: false });
  const [diagnostic, setDiagnostic] = useState<BrowserDiagnostic>();
  const [profileReady, setProfileReady] = useState<boolean>();
  const [runId, setRunId] = useState("");
  const [editingRun, setEditingRun] = useState<Run>();
  const [runProblem, setRunProblem] = useState<{ id: string; text: string }>();
  const [startingRun, setStartingRun] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<{ ticket: string } | null>(null);
  const [image, setImage] = useState("");
  const [savedFrame, setSavedFrame] = useState<{ id: string; url: string }>();
  const [messages, setMessages] = useState<ExecutionLine[]>([]);
  const desktop = isTauri();
  const run = snapshot.runs.find((value) => value.id === runId);
  const frameId = page === "running" && snapshot.frame?.run_id === runId ? snapshot.frame?.id : undefined;
  const browserInUse = snapshot.runs.some(isActiveRun);
  const browserReady = Boolean(browser.connected && browser.pinned_target);
  const refresh = useCallback(async () => {
    if (!isTauri()) return;
    try {
      setSnapshot(await request<Snapshot>("snapshot", { run_id: runId }));
    } catch (failure) {
      setError(String(failure));
    }
  }, [runId]);
  const loadProfile = useCallback(() => {
    if (!isTauri()) return;
    void request("get_profile").then((profile) => setProfileReady(Boolean(profile))).catch(() => setProfileReady(undefined));
  }, []);
  useEffect(loadProfile, [loadProfile]);
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [page]);
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
      if (type === "error" || type === "state" && payload.state !== "starting") {
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
    void native<BrowserState>("browser_runtime_status").then(setBrowser).catch((failure) => setError(String(failure)));
    const subscription = listen<Connection>("codex-status", (event) => setConnection(event.payload));
    return () => { void subscription.then((unlisten) => unlisten()); };
  }, []);
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
  const openSettings = (tab: SettingsTab) => { setSettingsTab(tab); navigate("settings"); };
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
  const demo = () => perform(async () => setPreview(await request<{ ticket: string }>("preview_demo")));
  const confirmDemo = () => perform(async () => {
    if (!preview) return;
    const created = await request<{ run_id: string }>("start_run", { ticket: preview.ticket, confirmed: true });
    setPreview(null);
    openRun(created.run_id);
    await request("open_simulation", { run_id: created.run_id });
    setNotice("模拟页面已打开。点击开始后有 5 秒切换时间，请将模拟浏览器置于前台。");
  });
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
  const selectedNav = taskPages.has(page) ? "tasks" : page;
  const title = page === "settings" ? "设置" : navigation.find((item) => item[0] === selectedNav)?.[1];
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
      <div className="sidebar-bottom"><button className={`nav-item ${page === "settings" ? "selected" : ""}`}
        aria-current={page === "settings" ? "page" : undefined} onClick={() => openSettings("profile")}><Settings size={21} />设置</button></div>
    </aside>
    <main>
      <div className="workspace-topbar" data-tauri-drag-region>
        <div className="workspace-breadcrumb" data-tauri-drag-region><House size={17} />
          <button onClick={() => navigate(selectedNav)}>{title}</button>
          {subTitle && <><ChevronRight size={15} /><strong>{subTitle}</strong></>}
        </div>
        <div className="workspace-top-actions">
          <button onClick={() => openSettings("connections")}><i className={`connection-dot ${browser.connected ? "connected" : ""}`} />Chrome {browser.connected ? "已连接" : "未连接"}</button>
          <button onClick={() => openSettings("connections")}><i className={`connection-dot ${connection.state === "ready" ? "connected" : ""}`} />Codex {connection.state === "ready" ? "已连接" : connection.state === "connecting" ? "连接中" : "未连接"}</button>
          {desktop && <div className="workspace-window-controls">
            <button aria-label="最小化" onClick={() => windowControl("minimize")}><Minus size={17} /></button>
            <button aria-label="最大化或还原" onClick={() => windowControl("toggleMaximize")}><Square size={13} /></button>
            <button aria-label="关闭应用" onClick={() => windowControl("close")}><X size={17} /></button>
          </div>}
        </div>
      </div>
      <div className="workspace-content">
        {!desktop && <div className="banner">浏览器界面预览，执行器未连接。请从桌面应用启动任务。</div>}
        {error && <div className="banner error" role="alert">{error}<button aria-label="关闭错误" onClick={() => setError("")}><X size={16} /></button></div>}
        {notice && <div className="banner" role="status">{notice}</div>}
        {page === "home" && <Overview snapshot={snapshot} profileReady={profileReady} classify={attentionCategory}
          onNew={openTask} onRun={openRun} onTodo={(category) => { setTodoCategory(category); navigate("inbox"); }}
          onRecords={() => navigate("history")} onProfile={() => openSettings("profile")} />}
        {page === "tasks" && <TasksPage snapshot={snapshot} onNew={openTask} onOpen={openRun} />}
        {(page === "boss" || page === "companies") && <TaskSetup key={`${page}:${editingRun?.id ?? "new"}`} channel={page === "boss" ? "boss" : "web"}
          initialRun={editingRun} latestRun={snapshot.runs.find((item) => !item.config.simulation && item.config.channels.includes(page === "boss" ? "boss" : "web"))}
          onSwitchChannel={openTask} onBack={() => editingRun ? openRun(editingRun.id) : navigate("tasks")}
          browserReady={browserReady} codexReady={connection.state === "ready"}
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
          onBrowserSetup={() => run?.config.simulation ? void perform(async () => { await request("open_simulation", { run_id: runId }); }) : openSettings("connections")}
          onImage={(kind, identity) => void perform(async () => { setImage((await request<{ url: string }>("read_image", { kind, identity })).url); })} />}
        {page === "history" && <Records snapshot={snapshot} onExport={exportRecords} exporting={busy} />}
        {page === "inbox" && <Inbox key={todoCategory ?? "all"} snapshot={snapshot} onChanged={refresh} initialCategory={todoCategory} />}
        {page === "settings" && <><header className="workspace-title"><h1>设置</h1></header><SettingsPage active={settingsTab} onTabChange={setSettingsTab} onSaved={async () => { loadProfile(); await refresh(); }}
          diagnostics={<ConnectionDiagnostics browser={browser} connection={connection} busy={busy} browserInUse={browserInUse} desktop={desktop}
            diagnostic={diagnostic}
            onConnectChrome={() => void perform(async () => { setDiagnostic(undefined); setBrowser(await native<BrowserState>("browser_connect")); })}
            onSelectTab={(targetId) => void perform(async () => { setDiagnostic(undefined); setBrowser(await native<BrowserState>("browser_select_tab", { targetId })); })}
            onInspect={() => void perform(async () => setDiagnostic(await native<BrowserDiagnostic>("browser_inspect")))}
            onConnectCodex={() => void perform(async () => setConnection(await native<Connection>("codex_connect")))}
            onLogin={() => void perform(async () => setConnection(await native<Connection>("codex_login")))}
            onDemo={() => void demo()} />} /></>}
      </div>
    </main>
      {preview && (
        <div className="modal-backdrop">
          <section
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="confirm-title"
          >
            <div className="section-title">
              <h2 id="confirm-title">确认本轮模拟任务</h2>
              <button aria-label="关闭" onClick={() => setPreview(null)}>
                <X />
              </button>
            </div>
            <p>将使用虚构资料，在本地浏览器页面验证以下流程：</p>
            <ul>
              <li>填写姓名、邮箱与项目经历，截图留档</li>
              <li>提交模拟申请，核验页面回执</li>
              <li>触发一次模拟 BOSS 沟通，上限 1 次</li>
              <li>识别面试邀约并生成待办</li>
            </ul>
            <div className="banner">
              会控制鼠标和键盘。登录招聘网站、真实投递和发送真实消息均不在本轮范围内。
            </div>
            <div className="actions end">
              <Button variant="secondary" onClick={() => setPreview(null)}>
                取消
              </Button>
              <Button disabled={busy} onClick={() => void confirmDemo()}>
                确认范围，创建任务
              </Button>
            </div>
          </section>
        </div>
      )}
      {image && <div className="modal-backdrop image-modal" role="dialog" aria-modal="true" aria-label="截图预览">
        <button className="image-close" aria-label="关闭图片" onClick={() => setImage("")}><X /></button>
        <img src={image} alt="申请截图证据" />
      </div>}
    </div>
  );
}
