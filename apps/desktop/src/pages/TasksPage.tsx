import { useEffect, useState } from "react";
import { ArrowUpRight, BarChart3, Check, ChevronDown, ChevronRight, FileText, Inbox, LoaderCircle, MessageCircle, Pause, Play, Plus, Search, Square, X } from "lucide-react";
import { native } from "../bridge";
import { Button } from "../components/ui/button";
import type { Run, Snapshot } from "../contracts";
import { isActiveRun, runName, runStatus } from "../workspace";
import "./task-pages.css";

type Usage = {
  turns: number;
  images: number;
  tokens: { input: number; cached_input: number; output: number; total: number };
};

const tabs = [
  ["all", "全部"], ["active", "运行中"], ["pending", "待启动"], ["history", "历史"],
] as const;
type Scope = typeof tabs[number][0];

function classification(run: Run): Exclude<Scope, "all"> {
  if (isActiveRun(run) || run.status === "starting") return "active";
  if (run.status === "paused" && !run.started_at) return "pending";
  return "history";
}

function timeLabel(value?: string | null) {
  const date = new Date(value || "");
  if (Number.isNaN(date.getTime())) return "—";
  const time = date.toLocaleTimeString("zh-CN", {hour: "2-digit", minute: "2-digit", hour12: false});
  const today = new Date(), yesterday = new Date();
  yesterday.setDate(today.getDate() - 1);
  if (date.toDateString() === today.toDateString()) return time;
  if (date.toDateString() === yesterday.toDateString()) return "昨天 " + time;
  return date.toLocaleDateString("zh-CN", {month: "2-digit", day: "2-digit"}).replace("/", "-") + " " + time;
}

function statusAppearance(run: Run) {
  if (classification(run) === "pending") return { tone: "muted", icon: Play };
  switch (run.status) {
    case "running": case "starting": case "watching": return { tone: "violet", icon: LoaderCircle };
    case "paused": return { tone: "amber", icon: Pause };
    case "stopped": return { tone: "muted", icon: Square };
    case "completed": case "success": return { tone: "green", icon: Check };
    default: return { tone: "muted", icon: null };
  }
}

function totalUsage(rows: Usage[]): Usage {
  const total: Usage = { turns: 0, images: 0, tokens: {input: 0, cached_input: 0, output: 0, total: 0} };
  for (const row of rows) {
    total.turns += row.turns;
    total.images += row.images;
    total.tokens.input += row.tokens.input;
    total.tokens.cached_input += row.tokens.cached_input;
    total.tokens.output += row.tokens.output;
    total.tokens.total += row.tokens.total;
  }
  return total;
}

function UsageDetails({ title, subtitle, usage, notice, onClose }: {
  title: string;
  subtitle: string;
  usage?: Usage;
  notice?: string;
  onClose: () => void;
}) {
  const waiting = Boolean(usage?.turns && !usage.tokens.total);
  return <>
    <div className="task-pop-head"><strong>{title}</strong><button className="task-icon" onClick={onClose} aria-label="关闭 AI 用量"><X size={17}/></button></div>
    <p className="task-pop-subtitle">{subtitle}</p>
    {usage ? <>
      <p className="task-token-total"><strong>{waiting ? "待回传" : usage.tokens.total.toLocaleString("zh-CN")}</strong>{!waiting && " Token"}</p>
      <dl className="task-usage-detail">
        <div><dt>AI 回合</dt><dd>{usage.turns.toLocaleString("zh-CN")}</dd></div>
        <div><dt>输入</dt><dd>{waiting ? "—" : usage.tokens.input.toLocaleString("zh-CN")}</dd></div>
        <div><dt>其中缓存输入</dt><dd>{waiting ? "—" : usage.tokens.cached_input.toLocaleString("zh-CN")}</dd></div>
        <div><dt>输出</dt><dd>{waiting ? "—" : usage.tokens.output.toLocaleString("zh-CN")}</dd></div>
      </dl>
    </> : <p>{notice}</p>}
  </>;
}

export function TasksPage({ snapshot, onNew, onOpen }: {
  snapshot: Snapshot;
  onNew: (channel: "boss" | "web") => void;
  onOpen: (runId: string) => void;
}) {
  const [scope, setScope] = useState<Scope>("all");
  const [query, setQuery] = useState("");
  const [channel, setChannel] = useState("all");
  const [usage, setUsage] = useState<Record<string, Usage>>();
  const [usageError, setUsageError] = useState(false);
  const [popover, setPopover] = useState<{runId: string | null; top: number; left: number}>();
  useEffect(() => {
    let active = true;
    let requesting = false;
    const refresh = async () => {
      if (requesting) return;
      requesting = true;
      try {
        const value = await native<Record<string, Usage>>("ai_usage_overview");
        if (active) {
          setUsage(value);
          setUsageError(false);
        }
      } catch {
        if (active) setUsageError(true);
      } finally {
        requesting = false;
      }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 4000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);
  useEffect(() => {
    if (!popover) return;
    const close = () => setPopover(undefined);
    const onKey = (event: KeyboardEvent) => { if (event.key === "Escape") close(); };
    window.addEventListener("keydown", onKey);
    window.addEventListener("resize", close);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", close);
    };
  }, [popover]);

  const lookup = query.trim().toLocaleLowerCase();
  const counts: Record<Scope, number> = { all: 0, active: 0, pending: 0, history: 0 };
  const candidates = snapshot.runs.filter((run) => {
    if (channel === "simulation" && !run.config.simulation) return false;
    if (channel !== "all" && channel !== "simulation" && (run.config.simulation || !run.config.channels.includes(channel))) return false;
    return !lookup || [runName(run), ...run.config.target_roles, ...(run.config.cities || []), run.config.boss_filters?.city]
      .filter(Boolean).join(" ").toLocaleLowerCase().includes(lookup);
  });
  for (const run of candidates) {
    counts.all++;
    counts[classification(run)]++;
  }
  const runs = scope === "all" ? candidates : candidates.filter((run) => classification(run) === scope);
  const completed = new Map<string, number>();
  for (const action of snapshot.actions || []) {
    if (action.run_id && action.status === "succeeded" && ["greet", "submit"].includes(action.kind)) {
      completed.set(action.run_id, (completed.get(action.run_id) || 0) + 1);
    }
  }
  const toggleUsage = (runId: string | null, anchor: HTMLButtonElement) => {
    if (popover?.runId === runId) {
      setPopover(undefined);
      return;
    }
    const rect = anchor.getBoundingClientRect();
    setPopover({
      runId,
      top: Math.max(12, Math.min(rect.bottom + 8, window.innerHeight - 280)),
      left: Math.max(12, Math.min(rect.left, window.innerWidth - 320)),
    });
  };
  const selected = snapshot.runs.find((run) => run.id === popover?.runId);
  const measured = usage ? snapshot.runs.flatMap((run) => usage[run.id] ? [usage[run.id]] : []) : [];
  const selectedUsage = selected ? usage?.[selected.id] : usage ? totalUsage(measured) : undefined;

  return <section className="task-page task-list-page">
    <div className="task-page-heading">
      <h1>投递任务</h1>
      <div className="task-page-actions">
        <Button variant="secondary" onClick={(event) => toggleUsage(null, event.currentTarget)} aria-expanded={popover?.runId === null}>
          <BarChart3 size={17}/> AI 用量总览 <ChevronDown size={16}/>
        </Button>
        <Button onClick={() => onNew("boss")}><Plus size={18}/> 新建投递</Button>
      </div>
    </div>
    <div className="task-list-panel task-outline">
      <div className="task-filter-row task-list-toolbar">
        <label className="task-search"><Search size={17} aria-hidden="true"/><input aria-label="搜索任务" placeholder="搜索任务、岗位或城市" value={query} onChange={(event) => setQuery(event.target.value)} /></label>
        <div className="task-tabs" role="group" aria-label="任务状态">
          {tabs.map(([value, label]) => <button key={value} type="button" aria-pressed={scope === value} onClick={() => setScope(value)}>{label} <span>{counts[value]}</span></button>)}
        </div>
        <select aria-label="投递渠道" value={channel} onChange={(event) => setChannel(event.target.value)}>
          <option value="all">全部渠道</option><option value="boss">BOSS 直聘</option><option value="web">公司网申</option><option value="simulation">本地演示</option>
        </select>
      </div>
    <div className="task-table-wrap">
      <table className="task-table">
        <thead><tr><th>任务名称</th><th>渠道</th><th>状态</th><th>本轮结果</th><th>AI 用量（Token）</th><th>更新时间</th><th aria-label="操作"/></tr></thead>
        <tbody>
          {runs.map((run) => {
            const status = statusAppearance(run), boss = run.config.channels.includes("boss");
            const count = completed.get(run.id) ?? 0, limit = boss ? run.config.max_contacts : undefined;
            const result = count || run.started_at ? boss ? "已沟通" : "已提交" : "";
            const total = usage?.[run.id];
            const aiText = usageError && !usage ? "读取失败" : !usage ? "读取中…" : total
              ? total.turns && !total.tokens.total ? "待回传" : total.tokens.total.toLocaleString("zh-CN")
              : run.config.execution_mode === "script" ? "0"
                : classification(run) === "pending" ? "未启动" : "未记录";
            const ChannelIcon = boss && !run.config.simulation ? MessageCircle : FileText;
            return <tr key={run.id} data-active={classification(run) === "active"}>
              <td><button className="task-name" onClick={() => onOpen(run.id)}>{runName(run)}</button></td>
              <td><span className="task-channel"><ChannelIcon size={15} aria-hidden="true"/>{run.config.simulation ? "本地演示" : boss ? "BOSS 直聘" : "公司网申"}</span></td>
              <td><span className={"task-status " + status.tone}>{status.icon && <status.icon size={14}/>} {runStatus(run)}</span></td>
              <td>{result ? <><span>{count}{limit ? " / " + limit : ""} {result}</span>{limit && <progress className="task-progress" aria-label="本轮沟通进度" value={Math.min(count, limit)} max={limit}/>}</> : "—"}</td>
              <td><button className="task-usage-link" aria-label={runName(run) + " AI 用量"} aria-expanded={popover?.runId === run.id} onClick={(event) => toggleUsage(run.id, event.currentTarget)}>{aiText} <ChevronRight size={14}/></button></td>
              <td><time dateTime={run.updated_at || run.created}>{timeLabel(run.updated_at || run.created)}</time></td>
              <td><Button variant="secondary" onClick={() => onOpen(run.id)}>打开工作台 <ArrowUpRight size={14} aria-hidden="true"/></Button></td>
            </tr>;
          })}
          {!runs.length && <tr><td colSpan={7} className="task-table-empty"><Inbox size={22} aria-hidden="true"/>{snapshot.runs.length ? "没有符合条件的任务" : "还没有投递任务"}</td></tr>}
        </tbody>
      </table>
    </div>
    </div>
    {popover && <><button className="task-pop-backdrop" aria-label="关闭用量弹层" onClick={() => setPopover(undefined)}/>
      <section className="task-popover" aria-label={selected ? "本任务 AI 用量" : "AI 用量总览"} style={{ top: popover.top, left: popover.left }}>
        <UsageDetails title={selected ? "本任务 AI 用量" : "AI 用量总览"} subtitle={selected ? runName(selected) : "全部任务 · 本机已记录"}
          usage={selectedUsage} notice={usageError ? "暂时无法读取 AI 用量。" : !usage ? "正在读取…" : selected?.config.execution_mode === "script" ? "脚本任务当前未记录 AI 用量。" : "暂无该任务的用量记录。"}
          onClose={() => setPopover(undefined)}/>
      </section>
    </>}
  </section>;
}
