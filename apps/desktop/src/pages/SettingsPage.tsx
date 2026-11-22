import { useState, type ReactNode } from "react";
import { Bot, ChevronDown, CircleCheck, FileText, Monitor } from "lucide-react";
import { Button } from "../components/ui/button";
import type { BrowserState, Connection } from "../contracts";
import { hasResumeFields, Profile } from "./Profile";
import { Replies } from "./Replies";
import { Resume } from "./Resume";
import type { Extraction } from "./profile-draft";
import "./WorkspaceSettings.css";

const connectionLabels: Record<Connection["state"], string> = {
  ready: "已连接",
  disconnected: "未连接",
  connecting: "正在连接",
  not_installed: "未安装",
  login_required: "待登录",
  logging_in: "正在登录",
  error: "连接异常",
};

export function SettingsContent({ active, onProfile, onSaved, diagnostics }: {
  active: "resume" | "profile" | "replies" | "connections";
  onProfile: () => void;
  onSaved: () => void | Promise<void>;
  diagnostics: ReactNode;
}) {
  const [resumeExtraction, setResumeExtraction] = useState<Extraction>();
  return (
    <section className="settings-page">
      {active === "resume" && <Resume onSaved={onSaved} onExtracted={(result) => {
        if (!hasResumeFields(result)) return false;
        setResumeExtraction(result);
        onProfile();
        return true;
      }} />}
      <div hidden={active !== "profile"}>
        <Profile onSaved={onSaved} resumeExtraction={resumeExtraction} />
      </div>
      {active === "replies" && <Replies />}
      {active === "connections" && diagnostics}
    </section>
  );
}

export type BrowserDiagnostic = {
  message: string;
  counts: { cards: number; conversations: number; messages: number };
  status?: string;
  url_path?: string;
  page_kind?: string;
  diagnostic_path?: string;
};

export function ConnectionDiagnostics({
  browser, connection, busy, browserInUse, desktop, diagnostic,
  onConnectChrome, onRefreshTabs, onSelectTab, onInspect, onConnectCodex, onLogin, onCancelLogin,
}: {
  browser: BrowserState;
  connection: Connection;
  busy: boolean;
  browserInUse: boolean;
  desktop: boolean;
  diagnostic?: BrowserDiagnostic | null;
  onConnectChrome: () => void;
  onRefreshTabs: () => void;
  onSelectTab: (id: string) => void;
  onInspect: () => void;
  onConnectCodex: (reuseLogin?: boolean) => void;
  onLogin: () => void;
  onCancelLogin: () => void;
}) {
  const browserScope = browser.tabs?.filter((tab) => tab.url === "https://www.zhipin.com" || tab.url.startsWith("https://www.zhipin.com/")) ?? [];
  const loggingIn = connection.state === "logging_in";
  const browserConnected = browser.connected && !browser.connecting;
  const browserStep = browserConnected ? (browser.pinned_target ? 3 : 2) : 1;
  const canChangeBrowser = desktop && !busy && !browserInUse && !browser.connecting;
  const browserHint = browser.connecting ? "正在启动并连接求职专用 Chrome。" : !browserConnected ? "打开求职专用 Chrome 并连接。"
    : browser.pinned_target ? "已绑定 BOSS 标签页。" : "在求职专用 Chrome 中登录 BOSS 直聘，然后刷新标签并选择。";
  return (
    <section className="settings settings-diagnostics" aria-label="连接与诊断">
      <div className="settings-connection-grid">
        <article className="settings-connection-card">
          <h2>浏览器连接</h2>
          <div className="settings-connection-identity">
            <span className="settings-chrome-mark" aria-hidden="true" />
            <div>
              <div className="settings-connection-title"><h3>Chrome</h3>
                <span className={`workspace-tag ${browserConnected ? "success" : ""}`}>
                  {browser.connecting ? "连接中" : browserConnected ? "已连接" : browser.installed === false ? "组件未安装" : "未连接"}
                </span>
              </div>
              <p role="status">{browserHint}</p>
            </div>
            <Button disabled={!canChangeBrowser} onClick={onConnectChrome}>
              {browserConnected ? "重新连接" : "连接 Chrome"}
            </Button>
          </div>
          <ol className="settings-steps" aria-label="浏览器连接进度">
            {["连接 Chrome", "绑定 BOSS 标签", "检查页面"].map((label, index) =>
              <li key={label} aria-current={browserStep === index + 1 ? "step" : undefined}>
                <span>{browserStep > index + 1 ? <CircleCheck size={17} /> : index + 1}</span> {label}
              </li>)}
          </ol>
          {browser.connection_error && <p className="settings-browser-error" role="alert">{browser.connection_error}</p>}
          {browserConnected && <div className="settings-target-actions">
            <label className="settings-target-label">选择 BOSS 标签页
              <select aria-label="BOSS 标签页" value={browser.pinned_target || ""}
                disabled={!canChangeBrowser || !browserScope.length} onChange={(event) => {
                  if (event.target.value) onSelectTab(event.target.value);
                }}>
                <option value="" disabled>{browserScope.length ? "请选择已登录的 BOSS 页面" : "未发现可选择的 BOSS 标签页"}</option>
                {browserScope.map((tab) => <option value={tab.targetId} key={tab.targetId}>{tab.title || tab.url}</option>)}
              </select>
            </label>
            <Button variant="secondary" disabled={!canChangeBrowser} onClick={onRefreshTabs}>刷新标签</Button>
          </div>}
          {browserInUse && <p className="muted">在执行工作台暂停任务后可修改浏览器连接。</p>}
        </article>
        <article className="settings-connection-card settings-model-card">
          <h2>AI 执行器</h2>
          <div className="settings-connection-identity">
            <span className="workspace-icon"><Bot size={24} /></span>
            <div className="settings-connection-title"><h3>Codex</h3>
              <span className={`workspace-tag ${connection.state === "ready" ? "success" : ""}`}>
                {connectionLabels[connection.state]}
              </span>
            </div>
          </div>
          <p role="status">{connection.message}</p>
          {connection.version && <p className="muted">版本 {connection.version}</p>}
          <div className="settings-model-actions">
            {loggingIn
              ? <Button variant="secondary" disabled={!desktop || busy} onClick={onCancelLogin}>取消登录</Button>
              : <>
                <Button variant="secondary" disabled={!desktop || busy || connection.state === "connecting"} onClick={() => onConnectCodex()}>连接 Codex</Button>
                {connection.state !== "ready" && <Button variant="secondary" disabled={!desktop || busy || connection.state === "connecting"}
                  onClick={() => onConnectCodex(true)}>使用已有 Codex 登录</Button>}
              </>}
            {connection.state === "login_required" && <Button disabled={!desktop || busy} onClick={onLogin}>登录 ChatGPT</Button>}
          </div>
        </article>
      </div>
      <div className="settings-diagnostic-rows">
        <Button variant="secondary" className="settings-diagnostic-row" disabled={!desktop || busy || !browserConnected || !browser.pinned_target} onClick={onInspect}>
          <Monitor size={21} /><strong>检查当前页面</strong>
          {!browser.pinned_target && <span className="workspace-tag">绑定标签后可用</span>}
        </Button>
        <details className="settings-diagnostic-details" open={diagnostic ? true : undefined}>
          <summary className="settings-diagnostic-row"><FileText size={21} /><strong>诊断详情</strong><ChevronDown size={20} /></summary>
          {diagnostic ? <div className="settings-diagnostic-body">
            <p>{diagnostic.message}</p>
            {diagnostic.url_path && <p>页面 {diagnostic.url_path}</p>}
            <p>岗位 {diagnostic.counts.cards} · 会话 {diagnostic.counts.conversations} · 消息 {diagnostic.counts.messages}</p>
            {diagnostic.diagnostic_path && <code>{diagnostic.diagnostic_path}</code>}
          </div> : <p className="settings-diagnostic-body muted">检查当前页面后显示本次诊断。</p>}
        </details>
      </div>
    </section>
  );
}
