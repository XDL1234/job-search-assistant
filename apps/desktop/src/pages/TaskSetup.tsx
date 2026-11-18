import { useEffect, useState } from "react";
import {
  BriefcaseBusiness, Check, ChevronRight, Clipboard, ExternalLink, FileSpreadsheet, FileText, Info,
  Layers, MapPin, MessageCircle, Minus, Plus, RefreshCw, Search,
} from "lucide-react";
import { Button } from "../components/ui/button";
import { native, request } from "../bridge";
import type { EmploymentType, JobPreferences, Run, TaskConfig } from "../contracts";
import { BossFilterSummary, employmentLabels, JobPreferencesPage, splitLabels } from "./JobPreferences";
import { preferencesFromRun, rememberDraft, storedDraft, taskDraftFromRun, taskDraftKey, type TaskDraft } from "./task-draft";
import "./task-pages.css";

const modes = [
  ["script", "脚本快速沟通", "按卡片标题和标签核对 · 无需 Codex", MessageCircle],
  ["filtered", "AI 看岗位卡片", "根据卡片信息判断 · 需连接 Codex", Clipboard],
  ["screened", "AI 阅读岗位详情", "读取岗位职责后判断 · 需连接 Codex", FileText],
] as const;

type CompanyInspection = {
  path: string;
  name: string;
  sha256: string;
  rows: { company: string; url: string; job?: string; source_row: number; status: "valid" | "invalid" | "duplicate"; reason: string }[];
};

function fileName(path?: string) {
  return path?.split(/[\\/]/).at(-1) || "";
}

function Steps({ labels, current }: { labels: string[]; current: number }) {
  return <ol className="task-steps" aria-label="创建流程">
    {labels.map((label, index) => <li key={label} aria-current={index === current ? "step" : undefined} data-done={index < current}>
      <span>{index < current ? <Check size={17}/> : index + 1}</span>{label}
    </li>)}
  </ol>;
}

export function TaskSetup({
  channel,
  initialRun,
  latestRun,
  onCreated,
  onSwitchChannel,
  onBack,
  browserReady = false,
  codexReady = false,
}: {
  channel: "boss" | "web";
  initialRun?: Run;
  latestRun?: Run;
  onCreated: (id: string) => void;
  onSwitchChannel?: (channel: "boss" | "web") => void;
  onBack?: () => void;
  browserReady?: boolean;
  codexReady?: boolean;
}) {
  const [seed] = useState<TaskDraft>(() => {
    const base = taskDraftFromRun(initialRun ?? latestRun);
    if (initialRun) return base;
    return { ...base, ...storedDraft<TaskDraft>(taskDraftKey(channel)) };
  });
  const [fallbackPreferences] = useState(() => latestRun?.config.boss_filters ? preferencesFromRun(latestRun) : null);
  const [mode, setMode] = useState<typeof modes[number][0]>(seed.mode);
  const [scope, setScope] = useState<"saved" | "current">(seed.scope);
  const [preferences, setPreferences] = useState<JobPreferences | null>(() => preferencesFromRun(initialRun));
  const [editorOpen, setEditorOpen] = useState(false);
  const [roles, setRoles] = useState(seed.roles ?? "");
  const [title, setTitle] = useState(seed.title);
  const [webEmployment, setWebEmployment] = useState<EmploymentType | undefined>(seed.webEmployment);
  const [webRequirements, setWebRequirements] = useState(seed.webRequirements);
  const [limit, setLimit] = useState(seed.limit);
  const [tablePath, setTablePath] = useState(seed.tablePath);
  const [table, setTable] = useState<CompanyInspection>();
  const [preview, setPreview] = useState<{ticket: string; config: TaskConfig} | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [profile, setProfile] = useState<{ profile: { target_role?: string }; resume_path?: string } | null>();
  const isBoss = channel === "boss";
  const useSaved = isBoss && scope === "saved";
  const savedRoles = preferences?.target_roles.length ? preferences.target_roles : splitLabels(profile?.profile.target_role ?? "");
  const targetRoles = useSaved ? savedRoles : splitLabels(roles);
  const webKind = webEmployment ?? "any";
  const selectedCompanyCount = table?.rows.filter((row) => row.status === "valid").length ?? 0;
  const invalidCompanyCount = table?.rows.filter((row) => row.status === "invalid").length ?? 0;
  const duplicateCompanyCount = table?.rows.filter((row) => row.status === "duplicate").length ?? 0;
  const companyReady = Boolean(table && tablePath === table.path && selectedCompanyCount);
  const canPreview = Boolean(profile && targetRoles.length && (isBoss ? !useSaved || preferences : companyReady));

  useEffect(() => {
    let active = true;
    void request<{ profile: { target_role?: string }; resume_path?: string } | null>("get_profile").then((value) => {
      if (!active) return;
      setProfile(value);
      if (seed.roles === undefined) setRoles(value?.profile.target_role || "");
    }).catch((failure) => { if (active) setError(String(failure)); });
    if (isBoss && !initialRun) void request<JobPreferences | null>("get_job_preferences").then((value) => {
      if (active) setPreferences(value ?? fallbackPreferences);
    }).catch((failure) => { if (active) setError(String(failure)); });
    if (!isBoss && seed.tablePath) {
      setBusy(true);
      void request<CompanyInspection>("inspect_company_table", { path: seed.tablePath }).then((value) => {
        if (active) { setTable(value); setTablePath(value.path); }
      }).catch((failure) => { if (active) setError(String(failure)); }).finally(() => { if (active) setBusy(false); });
    }
    return () => { active = false; };
  }, [initialRun, isBoss, fallbackPreferences, seed]);
  useEffect(() => {
    if (!initialRun) rememberDraft(taskDraftKey(channel), {
      mode, scope, limit, roles: roles || undefined, title, webEmployment, webRequirements, tablePath,
    } satisfies TaskDraft);
  }, [initialRun, channel, mode, scope, limit, roles, title, webEmployment, webRequirements, tablePath]);

  const perform = async (action: () => Promise<void>) => {
    setError("");
    setNotice("");
    setBusy(true);
    try {
      await action();
    } catch (failure) {
      setError(String(failure));
    } finally {
      setBusy(false);
    }
  };

  const readTable = async (path: string) => {
    const file = await request<CompanyInspection>("inspect_company_table", { path });
    setTable(file);
    setTablePath(file.path);
  };
  const createPreview = () => perform(async () => {
    if (!canPreview) return;
    const config: TaskConfig = {
      channels: [channel],
      employment_type: isBoss ? useSaved ? preferences?.employment_type ?? "any" : initialRun?.config.employment_type ?? "any" : webKind,
      target_roles: targetRoles,
      max_contacts: limit,
      boss_mode: mode === "screened" ? "screened" : "filtered",
      execution_mode: !isBoss || mode !== "script" ? "ai" : "script",
      filters: isBoss ? useSaved ? "" : "沿用当前 BOSS 城市和筛选条件" : webRequirements.trim(),
      window_title: isBoss ? "BOSS直聘" : title,
      ...(useSaved && preferences ? { boss_filters: preferences.boss_filters } : {}),
      ...(isBoss && !useSaved && initialRun?.config.cities?.length ? { cities: initialRun.config.cities } : {}),
      ...(!isBoss && table ? { company_table: table.path, reviewed_company_table_sha256: table.sha256 } : {}),
    };
    setPreview(await request("preview_run", { config, ...(initialRun ? { run_id: initialRun.id } : {}) }));
  });
  const createTask = () => perform(async () => {
    if (!preview) return;
    const confirmation = { ticket: preview.ticket, confirmed: true };
    const { run_id } = initialRun
      ? await native<{run_id: string}>("executor_confirm_revision", confirmation)
      : await request<{run_id: string}>("start_run", confirmation);
    setPreview(null);
    onCreated(run_id);
  });
  const editSaved = () => {
    setPreview(null);
    setEditorOpen(true);
  };
  const selectedPreferences = useSaved ? preferences : null;
  const sourceName = [preferences?.boss_filters.city, savedRoles[0] || employmentLabels[preferences?.employment_type || "any"]].filter(Boolean).join(" · ");

  return <section className="task-page task-setup-page">
    <div className="task-page-heading"><h1>{preview ? "确认本轮范围" : initialRun ? "编辑任务" : "新建投递"}</h1>
      {onBack && <Button variant="ghost" onClick={onBack} disabled={busy}>{initialRun ? "返回工作台" : "返回任务列表"}</Button>}
    </div>
    {!initialRun && <div className="task-tabs" aria-label="投递渠道">
      {([["boss", "BOSS 直聘"], ["web", "公司网申"]] as const).map(([id, label]) =>
        <button key={id} type="button" aria-pressed={channel === id} disabled={busy || (channel !== id && !onSwitchChannel)} onClick={() => {
          if (channel === id) return;
          setPreview(null);
          onSwitchChannel?.(id);
        }}>{label}</button>)}
    </div>}
    <Steps labels={isBoss ? ["设置范围", "确认范围", "工作台"] : ["导入公司表", "核对清单", "预览任务"]}
      current={preview ? isBoss ? 1 : 2 : !isBoss && table ? 1 : 0}/>
    {error && <div className="banner error" role="alert">{error}</div>}
    {notice && <div className="banner" role="status">{notice}</div>}
    {preview ? <>
      <div className="task-two-columns task-confirm-columns">
        <div className="task-outline">
          {isBoss ? <>
            <div className="task-confirm-row"><Search size={26}/><div>
              <h2>搜索条件</h2>
              <p>{preview.config.target_roles.join("、")}</p>
              {preview.config.boss_filters
                ? <BossFilterSummary filters={preview.config.boss_filters} kind={preview.config.employment_type} />
                : <><p>沿用已绑定的 BOSS 页面筛选</p>
                  {preview.config.cities?.length ? <p>沟通城市：{preview.config.cities.join("、")}</p> : null}
                  {preview.config.employment_type !== "any" && <p>求职类型：{employmentLabels[preview.config.employment_type as EmploymentType]}</p>}</>}
            </div><button className="task-text-link" onClick={preview.config.boss_filters ? editSaved : () => setPreview(null)} disabled={busy}>修改</button></div>
            <div className="task-confirm-row"><MessageCircle size={26}/><div>
              <h2>投递方式</h2><p>{modes.find((item) => item[0] === mode)?.[1]}</p>
            </div><button className="task-text-link" onClick={() => setPreview(null)} disabled={busy}>修改</button></div>
            <div className="task-confirm-row"><Layers size={26}/><div>
              <h2>沟通上限</h2><p>{preview.config.max_contacts} 个</p>
            </div><button className="task-text-link" onClick={() => setPreview(null)} disabled={busy}>修改</button></div>
            <div className="task-confirm-row"><FileText size={26}/><div><h2>首次沟通</h2><p>沿用 BOSS 招呼语</p></div></div>
          </> : <>
            <div className="task-confirm-row"><FileSpreadsheet size={26}/><div>
              <h2>本轮公司 · {preview.config.company_rows?.length || 0} 家</h2>
              <ul className="task-company-confirm">
                {preview.config.company_rows?.map((row) => <li key={row.source_row}><strong>{row.company}</strong><span>{row.url}</span></li>)}
              </ul>
            </div><button className="task-text-link" onClick={() => setPreview(null)} disabled={busy}>修改</button></div>
            <div className="task-confirm-row"><BriefcaseBusiness size={26}/><div>
              <h2>本轮岗位</h2><p>{employmentLabels[preview.config.employment_type as EmploymentType]} · {preview.config.target_roles.join("、")}</p>
              {preview.config.filters && <p>{preview.config.filters}</p>}
            </div></div>
          </>}
        </div>
        <aside className="task-confirm-checks">
          <div><Check size={18} className="task-ok"/><strong>资料已就绪</strong></div>
          <p>{fileName(preview.config.resume_path)}</p>
          {isBoss && <div>{browserReady ? <Check size={18} className="task-ok"/> : <Info size={18}/>}
            <strong>{browserReady ? "BOSS 标签已绑定" : "启动前需绑定 BOSS 标签"}</strong></div>}
          <div>{preview.config.execution_mode === "script" || codexReady ? <Check size={18} className="task-ok"/> : <Info size={18}/>}
            <strong>{preview.config.execution_mode === "script" ? "脚本无需连接 Codex" : codexReady ? "Codex 已连接" : "启动前需连接 Codex"}</strong></div>
          <div className="task-confirm-next"><Info size={18}/>创建后到工作台启动</div>
        </aside>
      </div>
      <div className="task-setup-footer">
        <span><Info size={18}/>{initialRun ? "保存后返回工作台，由你启动续投" : "创建任务只保存范围，执行工作台启动后开始沟通或提交"}</span>
        <div><Button variant="secondary" disabled={busy} onClick={() => setPreview(null)}>返回修改</Button>
          <Button disabled={busy} onClick={() => void createTask()}>{initialRun ? "确认修改" : "确认并创建任务"}</Button></div>
      </div>
    </> : isBoss ? <>
      <div className="task-two-columns task-boss-columns">
        <div>
          <fieldset className="task-fieldset"><legend>搜索范围</legend>
            <label className="task-scope-option"><input type="radio" name="boss-scope" checked={useSaved} onChange={() => setScope("saved")}/> 使用已保存条件</label>
            <div className="task-saved-strip"><span><FileText size={17}/>{preferences ? sourceName : "尚未保存岗位条件"}</span>
              <button className="task-text-link" onClick={editSaved} disabled={busy}>编辑岗位条件</button></div>
            <label className="task-scope-option"><input type="radio" name="boss-scope" checked={!useSaved} onChange={() => setScope("current")}/> 沿用 BOSS 当前页面</label>
          </fieldset>
          <fieldset className="task-fieldset task-mode-field"><legend>投递方式</legend>
            {modes.map(([value, label, detail, Icon]) => <label key={value} className="task-mode" data-selected={mode === value}>
              <input type="radio" name="boss-mode" checked={mode === value} onChange={() => setMode(value)} aria-label={label}/>
              <Icon size={23}/><span><strong>{label}</strong><small>{detail}</small></span>
            </label>)}
          </fieldset>
        </div>
        <aside className="task-condition-summary">
          <h2>条件摘要</h2>
          <ul>
            <li><MapPin size={19}/>{selectedPreferences?.boss_filters.city || "沿用当前城市"}</li>
            <li><BriefcaseBusiness size={19}/>{selectedPreferences ? employmentLabels[selectedPreferences.employment_type] : useSaved ? "待保存岗位条件" : "沿用页面求职类型"}</li>
            <li><Layers size={19}/>{targetRoles.join("、") || "请填写岗位方向"}</li>
          </ul>
          <label htmlFor="task-communication-limit">本轮沟通上限</label>
          <div className="task-stepper">
            <button type="button" aria-label="减少沟通上限" onClick={() => setLimit((value) => Math.max(1, value - 1))} disabled={limit <= 1}><Minus size={16}/></button>
            <input id="task-communication-limit" type="number" min={1} max={100} value={limit} onChange={(event) => {
              if (event.target.value) setLimit(Math.min(100, Math.max(1, Math.trunc(Number(event.target.value)))));
            }}/>
            <button type="button" aria-label="增加沟通上限" onClick={() => setLimit((value) => Math.min(100, value + 1))} disabled={limit >= 100}><Plus size={16}/></button>
          </div>
          <p>首次沟通沿用 BOSS 招呼语</p>
        </aside>
      </div>
      <div className="task-setup-footer">
        <span>{profile === null ? "请先在设置中保存资料和简历" : profile && !targetRoles.length ? "请先填写岗位方向" : ""}</span>
        <Button disabled={busy || !canPreview} onClick={() => void createPreview()}>预览本轮范围</Button>
      </div>
    </> : <>
      <div className="task-two-columns task-confirm-columns">
        <div className="task-outline task-company-sheet">
          <div className="task-page-heading task-company-heading"><h2><FileSpreadsheet size={22}/>{table?.name || "导入公司表"}</h2>
            <Button variant="secondary" disabled={busy} onClick={() => void perform(async () => {
              const path = await native<string | null>("pick_file", { kind: "companies" });
              if (path) await readTable(path);
            })}>{table ? <RefreshCw size={16}/> : <Plus size={16}/>} {table ? "更换文件" : "选择公司表"}</Button></div>
          {table ? <div className="task-table-wrap"><table className="task-table task-company-table">
            <thead><tr><th>公司</th><th>申请网址</th><th>格式校验</th></tr></thead>
            <tbody>{table.rows.map((row) => <tr key={row.source_row} className={row.status === "invalid" ? "task-company-invalid" : ""}>
              <td><strong>{row.company || "第 " + row.source_row + " 行"}</strong>{row.job && <small>{row.job}</small>}</td>
              <td>{row.status === "valid" ? <button className="task-text-link task-company-url" title="在默认浏览器打开申请网址" disabled={busy} onClick={() => void perform(async () => {
                await request("open_company_site", { path: table.path, sha256: table.sha256, source_row: row.source_row });
                setNotice("已打开申请网址。");
              })}>{row.url} <ExternalLink size={14}/></button> : row.url || "缺少网址"}</td>
              <td><span className={"task-status " + (row.status === "valid" ? "green" : row.status === "duplicate" ? "muted" : "amber")}>
                {row.status === "valid" ? <Check size={14}/> : <Info size={14}/>}
                {row.status === "valid" ? "格式通过" : row.status === "duplicate" ? "重复项" : "需补全"}
              </span>{row.reason && <small>{row.reason}</small>}</td>
            </tr>)}
            {!table.rows.length && <tr><td colSpan={3} className="task-table-empty">没有可核对的公司</td></tr>}</tbody>
          </table></div> : <div className="task-company-empty">
            <p>支持 Excel 和 UTF-8 CSV，包含公司名称和申请网站两列。</p>
            <div className="task-filter-row">
              <input aria-label="公司表路径" placeholder="或粘贴本地公司表路径" value={tablePath} onChange={(event) => setTablePath(event.target.value)}/>
              <Button variant="secondary" disabled={busy || !tablePath.trim()} onClick={() => void perform(() => readTable(tablePath.trim()))}>读取公司表</Button>
            </div>
          </div>}
        </div>
        <aside className="task-outline task-company-materials">
          <h2>本轮材料</h2>
          <ul><li><BriefcaseBusiness size={18}/><span>求职类型</span><strong>{employmentLabels[webKind]}</strong></li>
            <li><Layers size={18}/><span>岗位方向</span><strong>{targetRoles.join("、") || "待填写"}</strong></li>
            <li><FileText size={18}/><span>简历</span><strong>{profile ? fileName(profile.resume_path) || "已选" : "未保存"}</strong></li>
          </ul>
          <details><summary>调整网页填写设置</summary>
            <label>求职类型<select aria-label="网申求职类型" value={webKind} onChange={(event) => setWebEmployment(event.target.value as EmploymentType)}>
              {Object.entries(employmentLabels).map(([kind, label]) => <option key={kind} value={kind}>{label}</option>)}
            </select></label>
            <label>岗位方向<input value={roles} onChange={(event) => setRoles(event.target.value)}/></label>
            <label>目标浏览器标题<input value={title} onChange={(event) => setTitle(event.target.value)}/></label>
            <label>补充要求<input value={webRequirements} onChange={(event) => setWebRequirements(event.target.value)}/></label>
          </details>
          <p><Info size={17}/>逐家公司留存填写截图与结果</p>
          <p><Info size={17}/>网申时需保持浏览器在前台</p>
        </aside>
      </div>
      <div className="task-setup-footer task-company-footer">
        <span>{table ? <>已读取 {table.rows.length} 行{invalidCompanyCount > 0 && <strong> · {invalidCompanyCount} 行需补全</strong>}{duplicateCompanyCount > 0 && <> · {duplicateCompanyCount} 行重复</>}</> : "导入后核对公司和申请网址"}
          {profile === null && <> · 请先保存资料和简历</>}</span>
        <Button disabled={busy || !canPreview} onClick={() => void createPreview()}>
          {table ? "预览可用的 " + selectedCompanyCount + " 家" : "预览任务"} <ChevronRight size={17}/>
        </Button>
      </div>
    </>}
    {editorOpen && <JobPreferencesPage onClose={() => setEditorOpen(false)} onSaved={(value) => {
      setPreferences(value);
      if (initialRun) setScope("saved");
    }}
      initialPreferences={preferences} localOnly={Boolean(initialRun)} />}
  </section>;
}
