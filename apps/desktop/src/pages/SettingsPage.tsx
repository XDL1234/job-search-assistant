import type { ReactNode } from "react";
import { ArrowRight, Bot, ChevronDown, CircleCheck, FileText, Monitor, Play } from "lucide-react";
import { Button } from "../components/ui/button";
import type { BrowserState, Connection } from "../contracts";
import { Profile } from "./Profile";
import { Replies } from "./Replies";
import "./WorkspaceSettings.css";

export type SettingsTab = "profile" | "replies" | "connections";
const tabs: { key: SettingsTab; label: string }[] = [
  { key: "profile", label: "资料与简历" },
  { key: "replies", label: "回复规则" },
  { key: "connections", label: "连接与诊断" },
];
const connectionLabels: Record<Connection["state"], string> = {
  ready: "已连接",
  disconnected: "未连接",
  connecting: "正在连接",
  not_installed: "未安装",
  login_required: "待登录",
  logging_in: "正在登录",
  error: "连接异常",
};

export function SettingsPage({ active, onTabChange, onSaved, diagnostics }: {
  active: SettingsTab;
  onTabChange: (tab: SettingsTab) => void;
  onSaved: () => void | Promise<void>;
  diagnostics: ReactNode;
}) {
  return (
    <section className="settings-page">
      <nav className="workspace-tabs" aria-label="设置分类">
        {tabs.map(({ key, label }) =>
          <button key={key} type="button" aria-pressed={active === key}
            onClick={() => onTabChange(key)}>{label}</button>)}
      </nav>
      {active === "profile" && <Profile onSaved={onSaved} />}
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
  onConnectChrome, onSelectTab, onInspect, onConnectCodex, onLogin, onDemo,
}: {
  browser: BrowserState;
  connection: Connection;
  busy: boolean;
  browserInUse: boolean;
  desktop: boolean;
  diagnostic?: BrowserDiagnostic | null;
  onConnectChrome: () => void;
  onSelectTab: (id: string) => void;
  onInspect: () => void;
  onConnectCodex: () => void;
  onLogin: () => void;
  onDemo: () => void;
}) {
  const browserScope = browser.tabs?.filter((tab) => tab.url.startsWith("https://www.zhipin.com/")) ?? [];
  const connectingCodex = connection.state === "connecting" || connection.state === "logging_in";
  const browserStep = browser.pinned_target ? 3 : browser.connected ? 2 : 1;
  return (
    <section className="settings settings-diagnostics" aria-label="连接与诊断">
      <div className="settings-connection-grid">
        <article className="settings-connection-card">
          <h2>浏览器连接</h2>
          <div className="settings-connection-identity">
            <span className="settings-chrome-mark" aria-hidden="true" />
            <div>
              <div className="settings-connection-title"><h3>Chrome</h3>
                <span className={`workspace-tag ${browser.connected ? "success" : ""}`}>
                  {browser.connected ? "已连接" : browser.installed === false ? "组件未安装" : "未连接"}
                </span>
              </div>
              <p>{browser.pinned_target ? "已绑定投递标签页" : browser.connected ?
                "选择需要绑定的 BOSS 标签页" : "在 Chrome 允许远程调试，然后选择 BOSS 标签页。"}</p>
            </div>
            <Button disabled={!desktop || busy || browserInUse} onClick={onConnectChrome}>
              连接 Chrome
            </Button>
          </div>
          <ol className="settings-steps" aria-label="浏览器连接进度">
            {["连接 Chrome", "选择 BOSS 标签", "检查页面"].map((label, index) =>
              <li key={label} aria-current={browserStep === index + 1 ? "step" : undefined}>
                <span>{browserStep > index + 1 ? <CircleCheck size={17} /> : index + 1}</span> {label}
              </li>)}
          </ol>
          {browser.connected && <label className="settings-target-label">选择 BOSS 标签页
            <select aria-label="BOSS 标签页" value={browser.pinned_target || ""}
              disabled={busy || browserInUse || !browserScope.length} onChange={(event) => {
                if (event.target.value) onSelectTab(event.target.value);
              }}>
              <option value="">{browserScope.length ? "请选择已登录的 BOSS 页面" : "未发现可连接的 BOSS 页面"}</option>
              {browserScope.map((tab) => <option value={tab.targetId} key={tab.targetId}>{tab.title || tab.url}</option>)}
            </select>
          </label>}
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
          <p>{connection.message}</p>
          {connection.version && <p className="muted">版本 {connection.version}</p>}
          <div className="settings-model-actions">
            <Button variant="secondary" disabled={!desktop || busy || connectingCodex} onClick={onConnectCodex}>连接 Codex</Button>
            {connection.state === "login_required" && <Button disabled={busy} onClick={onLogin}>登录 ChatGPT</Button>}
          </div>
        </article>
      </div>
      <div className="settings-diagnostic-rows">
        <Button variant="secondary" className="settings-diagnostic-row" disabled={!desktop || busy || !browser.connected || !browser.pinned_target} onClick={onInspect}>
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
        <Button variant="secondary" className="settings-diagnostic-row" disabled={!desktop || busy} onClick={onDemo}>
          <Play size={21} /><strong>运行本地演示</strong><ArrowRight size={20} />
        </Button>
      </div>
    </section>
  );
}
