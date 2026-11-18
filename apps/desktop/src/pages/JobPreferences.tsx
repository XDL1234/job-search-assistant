import { useEffect, useRef, useState } from "react";
import { ChevronRight, X } from "lucide-react";
import { native, request } from "../bridge";
import { Button } from "../components/ui/button";
import type { BossFilters, EmploymentType, JobPreferences } from "../contracts";
import { rememberDraft, storedDraft } from "./task-draft";
import "./task-pages.css";

export const employmentLabels: Record<EmploymentType, string> = {
  internship: "实习",
  campus: "校招 / 应届",
  full_time: "正式工作 / 社招",
  part_time: "兼职",
  any: "不限",
};

const filterLabels = {
  city: "求职城市",
  province: "省份",
  district: "行政区",
  business_area: "商圈",
  metro_line: "地铁线路",
  metro_station: "站点",
  query: "搜索关键词",
  salary: "薪资待遇",
  experience: "工作经验",
  degree: "学历要求",
  company_size: "公司规模",
  financing: "融资阶段",
};
type LabelField = keyof typeof filterLabels;
const platformTypes: Record<string, string> = { internship: "实习", campus: "全职", full_time: "全职", part_time: "兼职", any: "不限" };

const suggestions: Partial<Record<LabelField, string[]>> = {
  city: ["北京", "上海", "广州", "深圳", "杭州", "天津", "西安", "苏州", "武汉", "厦门", "长沙", "成都", "重庆", "青岛", "南京"],
  salary: ["不限", "3K以下", "3-5K", "5-10K", "10-20K", "20-50K", "50K以上"],
  experience: ["不限", "在校生", "应届生", "经验不限", "1年以内", "1-3年", "3-5年", "5-10年", "10年以上"],
  degree: ["不限", "初中及以下", "中专/中技", "高中", "大专", "本科", "硕士", "博士"],
  company_size: ["不限", "0-20人", "20-99人", "100-499人", "500-999人", "1000-9999人", "10000人以上"],
  financing: ["不限", "未融资", "天使轮", "A轮", "B轮", "C轮", "D轮及以上", "已上市", "不需要融资"],
};

export const splitLabels = (input: string) =>
  input.split(/[、,，;；\n]/).map((value) => value.trim()).filter(Boolean);

export function parsePositionPaths(input: string): string[][] {
  return input.split(/\r?\n/).map((line) => line.trim()).filter(Boolean).map((line) => {
    const path = line.split(/[>＞]/).map((part) => part.trim());
    if (path.length > 3 || path.some((part) => !part)) {
      throw new Error("每行填写一个最多三级的职位分类路径，使用 > 分隔。");
    }
    return path;
  });
}

export function BossFilterSummary({ filters, kind }: { filters: BossFilters; kind: string }) {
  const rows: [string, string][] = [["BOSS 求职类型", platformTypes[kind] ?? kind]];
  const fields = Object.entries(filterLabels) as [LabelField, string][];
  rows.push(...fields.flatMap(([key, label]): [string, string][] =>
    filters[key] ? [[label, filters[key]]] : [],
  ));
  if (filters.positions?.length) {
    rows.push(["职位类型", filters.positions.map((path) => path.join(" ＞ ")).join("；")]);
  }
  if (filters.industries?.length) rows.push(["公司行业", filters.industries.join("、")]);
  if (kind === "campus" && !filters.experience) rows.push(["工作经验", "应届生"]);
  return (
    <div className="flex flex-wrap gap-x-5 gap-y-2 text-sm break-words" aria-label="BOSS 平台筛选">
      {rows.map(([label, value]) => <span key={label}>{label}：{value}</span>)}
    </div>
  );
}

type ConditionsDraft = {
  kind: EmploymentType; roles: string; filters: BossFilters; positions: string;
  industries: string; areaMode: "district" | "metro";
};
const conditionsDraftKey = "job-assistant.job-conditions-draft.v1";

export function JobPreferencesPage({ onClose, onSaved, initialPreferences, localOnly = false }: {
  onClose?: () => void;
  onSaved?: (preferences: JobPreferences) => void;
  initialPreferences?: JobPreferences | null;
  localOnly?: boolean;
} = {}) {
  const drawer = useRef<HTMLDialogElement>(null);
  const [initial] = useState(initialPreferences);
  const [activeTab, setActiveTab] = useState<"basic" | "advanced">("basic");
  const [kind, setKind] = useState<EmploymentType>("internship");
  const [roles, setRoles] = useState("");
  const [filters, setFilters] = useState<BossFilters>({});
  const [positions, setPositions] = useState("");
  const [industries, setIndustries] = useState("");
  const [areaMode, setAreaMode] = useState<"district" | "metro">("district");
  const [loaded, setLoaded] = useState(false);
  const [saved, setSaved] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = (value: JobPreferences) => {
    setKind(value.employment_type);
    setRoles(value.target_roles.join("、"));
    setFilters(value.boss_filters);
    setPositions((value.boss_filters.positions ?? []).map((path) => path.join(" > ")).join("\n"));
    setIndustries((value.boss_filters.industries ?? []).join("、"));
    setAreaMode(value.boss_filters.metro_line ? "metro" : "district");
    setSaved(true);
    setDirty(false);
  };

  useEffect(() => {
    const dialog = drawer.current;
    dialog?.showModal();
    return () => dialog?.close();
  }, []);

  useEffect(() => {
    let active = true;
    const hydrate = (value: JobPreferences | null) => {
      if (!active) return;
      if (value) load(value);
      const draft = localOnly ? undefined : storedDraft<ConditionsDraft>(conditionsDraftKey);
      if (draft && draft.kind in employmentLabels && typeof draft.roles === "string" && draft.filters &&
          typeof draft.positions === "string" && typeof draft.industries === "string") {
        setKind(draft.kind);
        setRoles(draft.roles);
        setFilters(draft.filters);
        setPositions(draft.positions);
        setIndustries(draft.industries);
        setAreaMode(draft.areaMode === "metro" ? "metro" : "district");
        setDirty(true);
      }
      setLoaded(true);
    };
    if (initial || localOnly) hydrate(initial ?? null);
    else void request<JobPreferences | null>("get_job_preferences").then(hydrate)
      .catch((failure) => { if (active) setError(String(failure)); });
    return () => { active = false; };
  }, [initial, localOnly]);

  useEffect(() => {
    if (loaded && !localOnly) rememberDraft(conditionsDraftKey, dirty ? {
      kind, roles, filters, positions, industries, areaMode,
    } satisfies ConditionsDraft : undefined);
  }, [loaded, localOnly, dirty, kind, roles, filters, positions, industries, areaMode]);

  const perform = async (action: () => Promise<void>) => {
    setBusy(true);
    setError("");
    setNotice("");
    try { await action(); }
    catch (failure) { setError(String(failure)); }
    finally { setBusy(false); }
  };

  const edit = (change: () => void) => {
    change();
    setNotice("");
    setDirty(true);
  };

  const field = (key: LabelField, placeholder: string, disabled = false) => {
    const options = suggestions[key];
    return <label key={key}>
      {filterLabels[key]}
      <input
        value={key === "experience" && kind === "campus" ? "应届生" : filters[key] ?? ""}
        placeholder={placeholder}
        list={options && `boss-filter-${key}`}
        disabled={disabled}
        maxLength={key === "query" ? 100 : 60}
        onChange={(event) => {
          const next = event.target.value;
          edit(() => setFilters((current) => ({
            ...current,
            ...(key === "city" ? { province: undefined, district: undefined, business_area: undefined, metro_line: undefined, metro_station: undefined } : {}),
            ...(key === "district" ? { business_area: undefined } : {}),
            ...(key === "metro_line" ? { metro_station: undefined } : {}),
            [key]: next,
          })));
        }}
      />
      {options && <datalist id={`boss-filter-${key}`}>{options.map((value) => <option key={value} value={value} />)}</datalist>}
    </label>;
  };

  const hasCity = Boolean(filters.city?.trim());
  const hasParent = (value?: string) => Boolean(value?.trim() && value.trim() !== "不限");

  const resetArea = { district: undefined, business_area: undefined, metro_line: undefined, metro_station: undefined };
  const selectEmployment = (next: EmploymentType) => edit(() => {
    if (kind === "campus" || next === "campus") {
      setFilters((value) => ({ ...value, experience: undefined }));
    }
    setKind(next);
  });
  const scope = [
    { value: filters.city, clear: () => edit(() => setFilters((value) => ({
      ...value, ...resetArea, city: undefined, province: undefined,
    }))) },
    { value: kind === "any" ? "" : employmentLabels[kind], clear: () => selectEmployment("any") },
    { value: filters.query, clear: () => edit(() => setFilters((value) => ({ ...value, query: undefined }))) },
  ];

  const closeDrawer = () => {
    drawer.current?.close();
    onClose?.();
  };

  const contents = <>
    <div className="task-drawer-heading"><h2 id="boss-preferences-heading">编辑岗位条件</h2>
      {onClose && <button type="button" className="task-icon" aria-label="关闭岗位条件编辑" disabled={busy} onClick={closeDrawer}><X size={20}/></button>}
    </div>
    <div className="task-tabs" role="group" aria-label="岗位条件选项">
      <button type="button" aria-pressed={activeTab === "basic"} onClick={() => setActiveTab("basic")}>基本条件</button>
      <button type="button" aria-pressed={activeTab === "advanced"} onClick={() => setActiveTab("advanced")}>高级筛选</button>
    </div>
    {error && <div className="banner error" role="alert">{error}</div>}
    {notice && <div className="banner" role="status">{notice}</div>}
    <form className="task-preference-form form-page" onSubmit={(event) => {
      event.preventDefault();
      if (!loaded || busy) return;
      void perform(async () => {
        const preferences: JobPreferences = {
          employment_type: kind,
          target_roles: splitLabels(roles),
          boss_filters: {
            ...filters,
            positions: parsePositionPaths(positions),
            industries: splitLabels(industries),
            ...(kind === "campus" ? { experience: "应届生" } : {}),
          },
        };
        const result = localOnly ? preferences : await request<JobPreferences>("save_job_preferences", { preferences });
        load(result);
        onSaved?.(result);
        setNotice(localOnly ? "已保存到任务草稿，请在预览中确认修改。" : "已保存岗位条件。");
      });
    }}>
      <div className="task-preferences-scroll"><fieldset disabled={!loaded || busy} className="task-preferences-body">
        <div className="task-scope-chips"><span>当前范围</span>
          {scope.map(({value, clear}, index) => value && <button key={index} type="button" onClick={clear} aria-label={"移除条件 " + value}>{value}<X size={13}/></button>)}
          {!scope.some((item) => item.value) && <small>沿用 BOSS 当前城市，求职类型不限。</small>}
        </div>
        {activeTab === "basic" ? <>
          <fieldset className="task-fieldset"><legend>求职类型</legend>
            <div className="task-segmented">
              {(Object.keys(employmentLabels) as EmploymentType[]).map((next) =>
                <button key={next} type="button" aria-pressed={kind === next} onClick={() => selectEmployment(next)}>
                  {next === "full_time" ? "全职" : next === "campus" ? "校招" : employmentLabels[next]}
                </button>)}
            </div>
          </fieldset>
          <label>岗位方向<input value={roles} maxLength={400} placeholder="例如：嵌入式软件、MCU；留空使用个人资料中的求职方向" onChange={(event) => edit(() => setRoles(event.target.value))} /></label>
          <div className="form-grid">
            {field("city", "例如：深圳；留空沿用当前城市")}
            {field("province", "例如：广东；用于区分同名城市", !hasCity)}
          </div>
          {field("query", "用于在 BOSS 搜索，可与岗位方向不同")}
          <button type="button" className="task-text-link task-more-filters" onClick={() => setActiveTab("advanced")}>更多条件在高级筛选 <ChevronRight size={15}/></button>
        </> : <>
          <fieldset className="task-fieldset" disabled={!hasCity}><legend>工作区域</legend>
            <div className="task-segmented">
              {([["district", "行政区与商圈"], ["metro", "地铁"]] as const).map(([value, label]) =>
                <button key={value} type="button" aria-pressed={areaMode === value} onClick={() => {
                  if (areaMode === value) return;
                  edit(() => {
                    setAreaMode(value);
                    setFilters((current) => ({ ...current, ...resetArea }));
                  });
                }}>{label}</button>)}
            </div>
            {areaMode === "district" ? <div className="form-grid">
              {field("district", "例如：南山区")}
              {field("business_area", "例如：科技园", !hasParent(filters.district))}
            </div> : <div className="form-grid">
              {field("metro_line", "例如：1号线")}
              {field("metro_station", "例如：高新园", !hasParent(filters.metro_line))}
            </div>}
            {!hasCity && <p className="muted">填写城市后可设置工作区域。</p>}
          </fieldset>
          <label>职位分类路径<textarea value={positions} maxLength={1600} rows={2} placeholder="电子/电气/通信 > 电子/硬件开发 > 嵌入式软件工程师" onChange={(event) => edit(() => setPositions(event.target.value))} /></label>
          <p className="muted">每行填写一个完整的 BOSS 分类路径，用 &gt; 分隔层级。</p>
          <fieldset className="task-fieldset"><legend>其他条件</legend>
            <div className="form-grid task-preference-grid">
              {field("salary", "选择或填写平台薪资选项")}
              {field("experience", "例如：在校生", kind === "campus")}
              {field("degree", "例如：本科")}
              <label>公司行业<input value={industries} maxLength={400} placeholder="例如：电子/硬件开发、半导体/芯片" onChange={(event) => edit(() => setIndustries(event.target.value))} /></label>
              {field("company_size", "例如：100-499人")}
              {field("financing", "例如：已上市")}
            </div>
          </fieldset>
          {kind === "campus" && <p className="muted">校招使用 BOSS 的「全职」和「应届生」筛选。</p>}
        </>}
      </fieldset></div>
      <div className="task-drawer-footer">
        {!localOnly && <Button type="button" variant="secondary" disabled={!saved || dirty || busy} onClick={() => void perform(async () => {
          setNotice((await native<{ message: string }>("browser_apply_job_preferences")).message);
        })}>在 BOSS 核验</Button>}
        <Button type="submit" disabled={!loaded || busy}>{busy ? "处理中…" : "保存条件"}</Button>
      </div>
    </form>
  </>;

  return onClose ? <dialog ref={drawer} className="task-drawer" aria-labelledby="boss-preferences-heading" onCancel={(event) => {
    event.preventDefault();
    if (!busy) closeDrawer();
  }}>{contents}</dialog> : <section className="task-preferences-page">{contents}</section>;
}
