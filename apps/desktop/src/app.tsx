import { useCallback, useEffect, useState } from "react";
import { isTauri } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import {
  ArrowRight,
  Bell,
  BriefcaseBusiness,
  Check,
  CircleHelp,
  Clock3,
  FileText,
  FolderOpen,
  House,
  LoaderCircle,
  MessageSquare,
  Monitor,
  Pause,
  Play,
  RefreshCw,
  Search,
  Send,
  Settings,
  ShieldCheck,
  Square,
  Target,
  UserRound,
  Users,
  X,
} from "lucide-react";
import { Button } from "./components/ui/button";
import { native, request } from "./bridge";
import type { Connection, ExecutorEvent, Snapshot } from "./contracts";

const navigation = [
  ["home", "首页", House],
  ["profile", "我的资料", UserRound],
  ["companies", "公司网申", FileText],
  ["boss", "BOSS 投递", Send],
  ["replies", "自动回复", MessageSquare],
  ["running", "执行工作台", Monitor],
  ["inbox", "消息中心", Bell],
  ["history", "投递记录", Clock3],
] as const;
type Page = (typeof navigation)[number][0] | "settings";
const empty: Snapshot = {
  runs: [],
  attempts: [],
  attention: [],
  fields: [],
  evidence: [],
  frame: null,
};
const statusText: Record<string, string> = {
  running: "执行中",
  watching: "回复值守",
  paused: "已暂停",
  stopped: "已停止",
  in_progress: "处理中",
  success: "已投递",
  uncertain: "待核查",
  failed: "失败",
  needs_user: "待处理",
  ready: "已连接",
  disconnected: "未连接",
  connecting: "连接中",
  login_required: "请登录",
  logging_in: "等待登录",
  not_installed: "未安装",
  error: "连接异常",
};

export default function App() {
  const [page, setPage] = useState<Page>("home");
  const [snapshot, setSnapshot] = useState<Snapshot>(empty);
  const [connection, setConnection] = useState<Connection>({
    state: "disconnected",
    message: "连接 Codex 后，可在应用中执行任务",
  });
  const [runId, setRunId] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [preview, setPreview] = useState<{ ticket: string } | null>(null);
  const [frame, setFrame] = useState("");
  const [image, setImage] = useState("");
  const [messages, setMessages] = useState<{ runId: string; text: string }[]>(
    [],
  );
  const [search, setSearch] = useState("");
  const [detail, setDetail] = useState("");
  const refresh = useCallback(async () => {
    if (!isTauri()) return;
    try {
      setSnapshot(await request<Snapshot>("snapshot"));
    } catch (e) {
      setError(String(e));
    }
  }, []);
  useEffect(() => {
    void refresh();
    if (!isTauri()) return;
    void native<Connection>("codex_status")
      .then(setConnection)
      .catch((e) => setError(String(e)));
    const timer = window.setInterval(() => void refresh(), 2500);
    const subscriptions = [
      listen<Connection>("codex-status", (e) => setConnection(e.payload)),
      listen<ExecutorEvent>("executor-event", (e) => {
        if (e.payload.type === "message")
          setMessages((rows) =>
            [
              ...rows,
              {
                runId: e.payload.runId,
                text: String(e.payload.payload.text || ""),
              },
            ].slice(-300),
          );
        if (e.payload.type === "error")
          setError(String(e.payload.payload.message || "执行已暂停"));
        if (e.payload.type !== "message") void refresh();
      }),
    ];
    return () => {
      window.clearInterval(timer);
      subscriptions.forEach((p) => void p.then((unlisten) => unlisten()));
    };
  }, [refresh]);
  useEffect(() => {
    if (!runId && snapshot.runs[0]) setRunId(snapshot.runs[0].id);
  }, [snapshot, runId]);
  useEffect(() => {
    let cancelled = false;
    if (!snapshot.frame) {
      setFrame("");
      return;
    }
    void request<{ url: string }>("read_image", {
      kind: "frame",
      identity: snapshot.frame.id,
    })
      .then((value) => {
        if (!cancelled) setFrame(value.url);
      })
      .catch(() => {
        if (!cancelled) setFrame("");
      });
    return () => {
      cancelled = true;
    };
  }, [snapshot.frame?.id]);
  const perform = async (action: () => Promise<void>) => {
    setError("");
    setNotice("");
    setBusy(true);
    try {
      await action();
      await refresh();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };
  const connect = () =>
    perform(async () =>
      setConnection(await native<Connection>("codex_connect")),
    );
  const demo = () =>
    perform(async () =>
      setPreview(await request<{ ticket: string }>("preview_demo")),
    );
  const confirmDemo = () =>
    perform(async () => {
      if (!preview) return;
      const created = await request<{ run_id: string }>("start_run", {
        ticket: preview.ticket,
        confirmed: true,
      });
      setPreview(null);
      setRunId(created.run_id);
      setPage("running");
      await request("open_simulation", { run_id: created.run_id });
      setNotice(
        "模拟页面已打开。点击开始后有 5 秒切换时间，请将模拟浏览器置于前台。",
      );
    });
  const control = (action: string) =>
    perform(async () => {
      await native("executor_control", { runId, action });
      if (action === "running")
        setNotice("5 秒后开始，请将本轮模拟浏览器置于前台。");
    });
  const run = snapshot.runs.find((r) => r.id === runId);
  const sessionError = snapshot.sessions?.find(
    (s) => s.run_id === runId,
  )?.error;
  const attempts = snapshot.attempts.filter((a) => a.run_id === runId);
  const attention = snapshot.attention.filter((a) => !a.resolved);
  const selected = snapshot.attempts.find((a) => a.id === detail);
  const title = navigation.find((n) => n[0] === page)?.[1] || "设置与帮助";
  const records = snapshot.attempts.filter((a) =>
    `${a.company} ${a.job}`.includes(search),
  );
  const exportRecords = () =>
    perform(async () => {
      const r = await request<{ path: string }>("export_records");
      setNotice(`记录已导出：${r.path}`);
    });

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span>
            <BriefcaseBusiness size={24} />
          </span>
          求职助手
        </div>
        <nav aria-label="主导航">
          {navigation.map(([key, label, Icon]) => (
            <button
              key={key}
              className={page === key ? "nav-item selected" : "nav-item"}
              onClick={() => setPage(key)}
            >
              <Icon size={21} />
              {label}
              {key === "inbox" && attention.length > 0 && (
                <b>{attention.length}</b>
              )}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <button
            className={`nav-item ${page === "settings" ? "selected" : ""}`}
            onClick={() => setPage("settings")}
          >
            <Settings size={21} />
            设置与帮助
          </button>
          <div className="account">
            <div className="avatar">
              <UserRound size={21} />
            </div>
            <div>
              我的账户<small>{statusText[connection.state]}</small>
            </div>
            <span
              className={`dot ${connection.state === "ready" ? "online" : ""}`}
            />
          </div>
        </div>
      </aside>
      <main>
        <div className="topline">
          <span>我的求职空间</span>
          <div>
            <span className="pill">本地模拟 · 开发预览</span>
            <span
              className={`dot ${connection.state === "ready" ? "online" : ""}`}
            />
            <button className="text-button" onClick={() => setPage("settings")}>
              Codex {statusText[connection.state]}
            </button>
          </div>
        </div>
        <header>
          <div>
            <h1>{page === "home" ? "今天，开始新的机会" : title}</h1>
            <p>
              {page === "home"
                ? "准备好资料，选择一种方式开始投递。"
                : page === "running"
                  ? "每一步看得见，需要时随时暂停接管。"
                  : page === "history"
                    ? "查看每一次申请，以及对应的填写内容和证据。"
                    : "让求职的下一步更清楚。"}
            </p>
          </div>
          {page === "home" ? (
            <Button variant="secondary" onClick={() => setPage("history")}>
              <Clock3 size={17} />
              查看投递记录
              <ArrowRight size={16} />
            </Button>
          ) : (
            <Button variant="ghost" onClick={() => void refresh()}>
              <RefreshCw size={16} />
              刷新
            </Button>
          )}
        </header>
        {!isTauri() && (
          <div className="banner">
            浏览器界面预览，执行器未连接。请从桌面应用启动任务。
          </div>
        )}
        {error && (
          <div className="banner error" role="alert">
            {error}
            <button aria-label="关闭错误" onClick={() => setError("")}>
              <X size={16} />
            </button>
          </div>
        )}
        {notice && (
          <div className="banner" role="status">
            {notice}
          </div>
        )}
        {page === "home" && (
          <>
            <section className="readiness card">
              {[
                ["简历与个人资料", "导入并核对后即可重复使用", FileText],
                ["任务范围", "开始前确认本轮公司与条件", ShieldCheck],
                ["执行器连接", connection.message, Monitor],
              ].map(([label, desc, Icon]) => {
                const I = Icon as typeof FileText;
                return (
                  <div key={String(label)}>
                    <div className="readiness-icon">
                      <I size={24} />
                    </div>
                    <div>
                      <strong>{String(label)}</strong>
                      <p>{String(desc)}</p>
                    </div>
                  </div>
                );
              })}
            </section>
            <div className="entry-grid">
              {[
                {
                  label: "公司官网网申",
                  description: "按公司清单填写申请，逐页留档",
                  button: "导入公司表",
                  icon: FileText,
                  page: "companies",
                },
                {
                  label: "BOSS 快速沟通",
                  description: "使用平台筛选结果，按顺序沟通",
                  button: "设置筛选条件",
                  icon: Users,
                  page: "boss",
                },
                {
                  label: "BOSS AI 精筛",
                  description: "阅读职位要求，优先投递匹配岗位",
                  button: "设置求职偏好",
                  icon: Target,
                  page: "boss",
                },
              ].map((item) => (
                <section key={item.label} className="entry card">
                  <div className="entry-title">
                    <div className="round-icon">
                      <item.icon size={29} />
                    </div>
                    <h2>{item.label}</h2>
                  </div>
                  <p>{item.description}</p>
                  <Button onClick={() => setPage(item.page as Page)}>
                    {item.button}
                    <ArrowRight size={17} />
                  </Button>
                </section>
              ))}
            </div>
            <div className="columns">
              <section className="card">
                <div className="section-title">
                  <h2>继续未完成的任务</h2>
                  <button
                    className="text-button"
                    onClick={() => setPage("running")}
                  >
                    查看全部 →
                  </button>
                </div>
                {snapshot.runs.length === 0 ? (
                  <div className="empty">
                    <FolderOpen />
                    <strong>还没有投递任务</strong>
                    <p>可以先运行一次本地模拟，熟悉完整流程。</p>
                    <Button
                      disabled={busy || !isTauri()}
                      onClick={() => void demo()}
                    >
                      <Play size={16} />
                      运行本地演示
                    </Button>
                  </div>
                ) : (
                  snapshot.runs.slice(0, 3).map((r) => (
                    <button
                      className="task-row"
                      key={r.id}
                      onClick={() => {
                        setRunId(r.id);
                        setPage("running");
                      }}
                    >
                      <div className="round-icon small">
                        <BriefcaseBusiness size={22} />
                      </div>
                      <div>
                        <strong>
                          {r.config.simulation
                            ? "本地模拟任务"
                            : r.config.target_roles.join("、")}
                        </strong>
                        <p>{new Date(r.created).toLocaleString("zh-CN")}</p>
                      </div>
                      <span className={`badge ${r.status}`}>
                        {statusText[r.status] || r.status}
                      </span>
                      <ArrowRight size={17} />
                    </button>
                  ))
                )}
              </section>
              <section className="card">
                <div className="section-title">
                  <h2>
                    需要你处理 <em>· {attention.length}</em>
                  </h2>
                  <button
                    className="text-button"
                    onClick={() => setPage("inbox")}
                  >
                    查看全部 →
                  </button>
                </div>
                {attention.length ? (
                  attention.slice(0, 3).map((a) => (
                    <div className="attention-row" key={a.id}>
                      <Bell size={22} />
                      <div>
                        <strong>{a.subject}</strong>
                        <p>{a.reason}</p>
                      </div>
                      <Button onClick={() => setPage("inbox")}>去处理</Button>
                    </div>
                  ))
                ) : (
                  <div className="empty">
                    <Check />
                    <strong>暂无待处理事项</strong>
                    <p>面试邀约和需要你判断的问题会出现在这里。</p>
                  </div>
                )}
              </section>
            </div>
            <div className="footer-stats">
              <span>
                <Send size={16} />
                已投递{" "}
                {snapshot.attempts.filter((a) => a.status === "success").length}
              </span>
              <span>
                <Clock3 size={16} />
                待核查{" "}
                {
                  snapshot.attempts.filter((a) => a.status === "uncertain")
                    .length
                }
              </span>
              <span>
                <Bell size={16} />
                待处理 {attention.length}
              </span>
            </div>
          </>
        )}
        {page === "running" && (
          <>
            {sessionError && (
              <div className="banner error" role="alert">
                {sessionError}
              </div>
            )}
            <section className="card toolbar">
              <select
                aria-label="当前任务"
                value={runId}
                onChange={(e) => setRunId(e.target.value)}
              >
                <option value="">选择任务</option>
                {snapshot.runs.map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.config.simulation
                      ? "本地模拟"
                      : r.config.target_roles.join("、")}{" "}
                    · {new Date(r.created).toLocaleTimeString("zh-CN")}
                  </option>
                ))}
              </select>
              <span className={`badge ${run?.status}`}>
                {run ? statusText[run.status] : "暂无任务"}
              </span>
              <div className="spacer" />
              <Button
                variant="secondary"
                disabled={busy || !run || run.status === "stopped"}
                onClick={() => void control("paused")}
              >
                <Pause size={16} />
                暂停接管
              </Button>
              <Button
                disabled={
                  busy ||
                  !run ||
                  run.status === "stopped" ||
                  connection.state !== "ready"
                }
                onClick={() => void control("running")}
              >
                <Play size={16} />
                {run?.status === "paused" ? "开始 / 恢复" : "继续执行"}
              </Button>
              <Button
                variant="danger"
                disabled={busy || !run || run.status === "stopped"}
                onClick={() => void control("stopped")}
              >
                <Square size={14} />
                停止
              </Button>
            </section>
            <div className="work-grid">
              <section className="card queue">
                <div className="section-title">
                  <h2>申请队列</h2>
                  <span>{attempts.length}</span>
                </div>
                {attempts.length ? (
                  attempts.map((a) => (
                    <div className="queue-row" key={a.id}>
                      <strong>{a.company}</strong>
                      <p>{a.job}</p>
                      <span className={`badge ${a.status}`}>
                        {statusText[a.status] || a.status}
                      </span>
                    </div>
                  ))
                ) : (
                  <div className="empty">
                    <FileText />
                    <p>执行后显示申请记录</p>
                  </div>
                )}
                <Button
                  variant="secondary"
                  disabled={busy || !run}
                  onClick={() =>
                    void perform(async () => {
                      await request("open_simulation", { run_id: runId });
                    })
                  }
                >
                  <FolderOpen size={16} />
                  打开模拟浏览器
                </Button>
              </section>
              <section className="card screen">
                <div className="section-title">
                  <h2>最近观察画面</h2>
                  <span>
                    {snapshot.frame
                      ? new Date(
                          snapshot.frame.captured_at * 1000,
                        ).toLocaleTimeString("zh-CN")
                      : "等待截图"}
                  </span>
                </div>
                {frame ? (
                  <button
                    className="image-button"
                    onClick={() => setImage(frame)}
                  >
                    <img src={frame} alt="执行器最近观察的浏览器画面" />
                  </button>
                ) : (
                  <div className="empty screen-empty">
                    <Monitor size={40} />
                    <strong>浏览器准备好后开始任务</strong>
                    <p>
                      开始后请将模拟浏览器放到前台。需要使用电脑时，先暂停接管。
                    </p>
                  </div>
                )}
                <div className="screen-caption">
                  最近截图供观察核对，不是实时视频。
                </div>
              </section>
            </div>
            <section className="card">
              <div className="section-title">
                <h2>执行进度</h2>
                <span>模型说明与业务记录分别核验</span>
              </div>
              <div className="stream" role="log">
                {messages
                  .filter((m) => m.runId === runId)
                  .map((m) => m.text)
                  .join("") || "任务开始后，执行说明会显示在这里。"}
              </div>
            </section>
          </>
        )}
        {page === "history" && (
          <section className="card">
            <div className="section-title">
              <div className="search">
                <Search size={17} />
                <input
                  aria-label="搜索记录"
                  placeholder="搜索公司或岗位"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
              </div>
              <Button
                variant="secondary"
                disabled={busy || !isTauri()}
                onClick={() => void exportRecords()}
              >
                导出 Excel
              </Button>
            </div>
            <table>
              <thead>
                <tr>
                  <th>公司与岗位</th>
                  <th>渠道</th>
                  <th>结果</th>
                  <th>更新时间</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                {records.map((a) => (
                  <tr key={a.id}>
                    <td>
                      <strong>{a.company}</strong>
                      <p>{a.job}</p>
                    </td>
                    <td>{a.channel === "web" ? "官网" : "BOSS"}</td>
                    <td>
                      <span className={`badge ${a.status}`}>
                        {statusText[a.status] || a.status}
                      </span>
                    </td>
                    <td>{new Date(a.updated).toLocaleString("zh-CN")}</td>
                    <td>
                      <button
                        className="text-button"
                        onClick={() => setDetail(a.id)}
                      >
                        查看证据 →
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!records.length && (
              <div className="empty">
                <Clock3 />
                <strong>暂无投递记录</strong>
                <p>成功、失败和待核查的申请都会保留在这里。</p>
              </div>
            )}
          </section>
        )}
        {page === "inbox" && (
          <section className="card">
            <div className="section-title">
              <h2>待我处理</h2>
              <span>面试邀约始终由你决定</span>
            </div>
            {attention.length ? (
              attention.map((a) => (
                <div className="attention-row" key={a.id}>
                  <Bell />
                  <div>
                    <strong>{a.subject}</strong>
                    <p>{a.reason}</p>
                    <span className="badge needs_user">会话已锁定</span>
                    <p>
                      本阶段请在原会话处理；应用内确认发送将在后续阶段接入。
                    </p>
                  </div>
                </div>
              ))
            ) : (
              <div className="empty">
                <ShieldCheck />
                <strong>暂无新待办</strong>
              </div>
            )}
          </section>
        )}
        {page === "settings" && (
          <section className="card settings">
            <div className="section-title">
              <h2>AI 执行器</h2>
              <span className="badge">{statusText[connection.state]}</span>
            </div>
            <p>{connection.message}</p>
            <div className="actions">
              <Button
                disabled={busy || connection.state === "connecting"}
                onClick={() => void connect()}
              >
                {busy ? (
                  <LoaderCircle className="spin" size={16} />
                ) : (
                  <RefreshCw size={16} />
                )}
                连接 Codex
              </Button>
              {connection.state === "login_required" && (
                <Button
                  variant="secondary"
                  disabled={busy}
                  onClick={() =>
                    void perform(async () => {
                      setConnection(await native<Connection>("codex_login"));
                    })
                  }
                >
                  登录 ChatGPT
                </Button>
              )}
              <Button
                variant="secondary"
                disabled={busy || !isTauri()}
                onClick={() => void demo()}
              >
                新建本地演示
              </Button>
            </div>
            <hr />
            <h2>关于当前版本</h2>
            <p>
              当前先开放本地模拟任务，使用虚构资料。真实公司网申与 BOSS
              投递尚未在桌面应用中开放。
            </p>
            <p>
              截图和台账保存在本地；AI 分析会将所需资料和截图发送到模型服务。
            </p>
            <p>退出应用将暂停任务，下次启动需手动恢复。</p>
          </section>
        )}
        {["profile", "companies", "boss", "replies"].includes(page) && (
          <section className="card">
            <div className="empty stage-empty">
              <CircleHelp size={36} />
              <h2>{title} · 尚未开放</h2>
              <p>本阶段先验证应用内执行与接管。该页面将按已确认设计接入。</p>
              <div className="actions">
                <Button onClick={() => setPage("running")}>
                  前往执行工作台
                  <ArrowRight size={16} />
                </Button>
                <Button
                  variant="secondary"
                  disabled={busy || !isTauri()}
                  onClick={() => void demo()}
                >
                  体验本地模拟
                </Button>
              </div>
            </div>
          </section>
        )}
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
              <li>识别面试邀约并转入消息中心</li>
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
      {selected && (
        <div className="modal-backdrop">
          <section
            className="modal wide"
            role="dialog"
            aria-modal="true"
            aria-label="申请证据"
          >
            <div className="section-title">
              <h2>{selected.company} · 填写与证据</h2>
              <button aria-label="关闭详情" onClick={() => setDetail("")}>
                <X />
              </button>
            </div>
            <p>{selected.reason || statusText[selected.status]}</p>
            {snapshot.fields
              .filter((f) => f.attempt_id === selected.id)
              .map((f) => (
                <div className="field-row" key={f.name}>
                  <strong>{f.name}</strong>
                  <span>{f.value}</span>
                  <small>来源：{f.source}</small>
                </div>
              ))}
            <div className="actions">
              {snapshot.evidence
                .filter((e) => e.attempt_id === selected.id)
                .map((e) => (
                  <Button
                    key={e.id}
                    variant="secondary"
                    onClick={() =>
                      void perform(async () =>
                        setImage(
                          (
                            await request<{ url: string }>("read_image", {
                              kind: "evidence",
                              identity: e.id,
                            })
                          ).url,
                        ),
                      )
                    }
                  >
                    查看截图 · {e.kind}
                  </Button>
                ))}
            </div>
          </section>
        </div>
      )}
      {image && (
        <div
          className="modal-backdrop image-modal"
          role="dialog"
          aria-modal="true"
          aria-label="截图预览"
        >
          <button
            className="image-close"
            aria-label="关闭图片"
            onClick={() => setImage("")}
          >
            <X />
          </button>
          <img src={image} alt="申请截图证据" />
        </div>
      )}
    </div>
  );
}
