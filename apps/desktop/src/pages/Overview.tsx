import { Activity, ArrowUpRight, BriefcaseBusiness, Building2, CalendarDays, CheckCircle2, ChevronRight, ClipboardList, Ellipsis, MessagesSquare, Plus } from "lucide-react";
import { Button } from "../components/ui/button";
import type { Snapshot } from "../contracts";
import { beginningOfWeek, confirmedActions, displayTime, isActiveRun, runName, runStatus } from "../workspace";

const categories = [
  { id: "interview", text: "面试邀约", icon: CalendarDays },
  { id: "review", text: "岗位复核", icon: ClipboardList },
  { id: "other", text: "其他待办", icon: Ellipsis },
] as const;
export type OverviewCategory = typeof categories[number]["id"];

export function Overview({ snapshot, resumeReady, classify, onNew, onRun, onTodo, onRecords, onResume }: {
  snapshot: Snapshot;
  resumeReady?: boolean;
  classify: (reason: string) => OverviewCategory;
  onNew: () => void;
  onRun: (id: string) => void;
  onTodo: (category: OverviewCategory) => void;
  onRecords: () => void;
  onResume: () => void;
}) {
  const now = new Date();
  const realRuns = snapshot.runs.filter((run) => !run.config.simulation);
  const realIds = new Set(realRuns.map((run) => run.id));
  const active = realRuns.filter(isActiveRun);
  const current = [...active, ...realRuns.filter((run) => run.status === "paused")].slice(0, 3);
  const pending = snapshot.attention.filter((row) => !row.resolved);
  const recent = snapshot.attempts.filter((row) => realIds.has(row.run_id) &&
    ["success", "failed", "uncertain", "needs_user"].includes(row.status))
    .sort((a, b) => b.updated.localeCompare(a.updated)).slice(0, 3);
  const communicated = confirmedActions(snapshot, "greet").filter((action) =>
    Boolean(action.run_id && realIds.has(action.run_id)) && new Date(action.updated).getTime() >= beginningOfWeek(now)).length;
  return <div className="overview-page">
    <header className="workspace-title">
      <div className="overview-heading">
        <h1>今天的求职进展</h1>
        <time dateTime={now.toLocaleDateString("sv-SE")}>{now.toLocaleDateString("zh-CN", { month: "long", day: "numeric" })}</time>
      </div>
      <Button onClick={onNew}><Plus size={19} /> 新建投递</Button>
    </header>
    <dl className="overview-stats">
      <div className="card overview-stat"><dt>运行中</dt><dd>{active.length}</dd><Activity size={20} aria-hidden="true" /></div>
      <div className="card overview-stat"><dt>待办</dt><dd>{pending.length}</dd><ClipboardList size={20} aria-hidden="true" /></div>
      <div className="card overview-stat"><dt>本周已沟通</dt><dd>{communicated}</dd><MessagesSquare size={20} aria-hidden="true" /></div>
    </dl>
    <div className="overview-grid">
      <div className="overview-primary">
        <section className="card overview-panel" aria-label="当前任务">
          <h2>当前任务</h2>
          {current.length ? <div className="overview-runs">
            {current.map((run) => {
              const running = isActiveRun(run);
              const result = confirmedActions(snapshot, run.config.channels.includes("boss") ? "greet" : "submit", run.id).length;
              const activity = snapshot.activity?.find((item) => item.run_id === run.id);
              return <button className={`overview-run ${running ? "active" : ""}`} key={run.id} onClick={() => onRun(run.id)}>
                <BriefcaseBusiness size={22} />
                <div><strong>{runName(run)}</strong>
                  {running && <small>{activity?.operation ? `最近步骤 · ${activity.operation}　` : ""}
                    {run.config.channels.includes("boss") ? `${result}${run.config.max_contacts ? ` / ${run.config.max_contacts}` : ""} 已沟通` : `${result} 已提交`}</small>}
                </div>
                {running ? <span className="overview-open"><ArrowUpRight size={16} /> 打开工作台</span>
                  : <span className={`badge ${run.status}`}>{runStatus(run)}</span>}
              </button>;
            })}
          </div> : <div className="overview-empty">暂无进行中或暂停的投递任务</div>}
        </section>
        <section className="card overview-panel" aria-label="最近结果">
          <div className="section-title"><h2>最近结果</h2><button className="text-button" onClick={onRecords}>查看记录 <ChevronRight size={15} /></button></div>
          {recent.length ? <div className="overview-results">{recent.map((row) => <button key={row.id} onClick={onRecords}>
            <Building2 size={18} /><strong>{row.company}</strong><span>{row.job}</span>
            <span className={`badge ${row.status}`}>{row.status === "success" ? (row.channel === "boss" ? "已沟通" : "已提交") :
              row.status === "failed" ? "失败" : row.status === "uncertain" ? "待核查" : "待处理"}</span>
            <time dateTime={row.updated}>{displayTime(row.updated)}</time>
          </button>)}</div> : <div className="overview-empty">暂无投递结果</div>}
        </section>
      </div>
      <div className="overview-secondary">
        <section className="card overview-todo">
          <h2>需要你处理</h2>
          {categories.map(({ id, text, icon: Icon }) => <button key={id} onClick={() => onTodo(id)}>
            <Icon size={21} /><span>{text}</span><strong>{pending.filter((item) => classify(item.reason) === id).length}</strong><ChevronRight size={16} />
          </button>)}
        </section>
        <section className="card overview-profile">
          <h2><CheckCircle2 className={resumeReady ? "ready" : ""} size={21} />{resumeReady === undefined ? "简历" : resumeReady ? "已导入简历" : "待导入简历"}</h2>
          <button className="text-button" onClick={onResume}>管理简历 <ArrowUpRight size={16} /></button>
        </section>
      </div>
    </div>
  </div>;
}
