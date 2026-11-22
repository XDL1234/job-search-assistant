import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { ChevronRight, X } from "lucide-react";
import { native, request } from "../bridge";
import { Button } from "../components/ui/button";
import type { BossFilters, EmploymentType, JobPreferences } from "../contracts";
import { CityPicker } from "./CityPicker";
import { FilterCatalogPicker } from "./FilterCatalogPicker";
import { industriesByGroup, positionPaths } from "./boss-catalog";
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

const suggestions = {
  salary: ["不限", "3K以下", "3-5K", "5-10K", "10-20K", "20-50K", "50K以上"],
  experience: ["不限", "在校生", "应届生", "经验不限", "1年以内", "1-3年", "3-5年", "5-10年", "10年以上"],
  degree: ["不限", "初中及以下", "中专/中技", "高中", "大专", "本科", "硕士", "博士"],
  company_size: ["不限", "0-20人", "20-99人", "100-499人", "500-999人", "1000-9999人", "10000人以上"],
  financing: ["不限", "未融资", "天使轮", "A轮", "B轮", "C轮", "D轮及以上", "已上市", "不需要融资"],
};
type ChoiceField = keyof typeof suggestions;
const positions = positionPaths.map((value) => ({ value, group: value[0], section: value.length === 3 ? value[1] : undefined }));
const industries = Object.entries(industriesByGroup).flatMap(([group, labels]) => labels.map((label) => ({ group, value: [label] })));

function FilterOptions({ label, options, value, onSelect, children, disabled = false }: {
  label: string;
  options: string[];
  value?: string;
  onSelect: (value: string) => void;
  children: ReactNode;
  disabled?: boolean;
}) {
  const [editing, setEditing] = useState<boolean>();
  const expanded = editing ?? Boolean(value && !options.includes(value));
  return <fieldset className="task-condition-row" disabled={disabled}>
    <legend>{label}</legend>
    <div className="task-filter-choices">
      {options.map((option) => <button key={option} type="button" aria-pressed={value === option} onClick={() => {
        onSelect(option);
        setEditing(false);
      }}>{option}</button>)}
      {!disabled && <button type="button" aria-expanded={expanded} onClick={() => setEditing(!expanded)}>
        自定义
      </button>}
    </div>
    {expanded && children}
  </fieldset>;
}

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
  kind: EmploymentType; roles: string; filters: BossFilters; areaMode: "district" | "metro";
  positions?: string; industries?: string;
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
  const advancedId = useId();
  const [collapsedSections, setCollapsedSections] = useState<Record<string, boolean>>({});
  const [kind, setKind] = useState<EmploymentType>("internship");
  const [roles, setRoles] = useState("");
  const [filters, setFilters] = useState<BossFilters>({});
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
      if (draft && draft.kind in employmentLabels && typeof draft.roles === "string" && draft.filters) {
        const draftFilters = { ...draft.filters };
        if (typeof draft.positions === "string") {
          try { draftFilters.positions = parsePositionPaths(draft.positions); }
          catch { setNotice("旧草稿中的职位分类格式有误，请核对已恢复的条件。"); }
        }
        if (typeof draft.industries === "string") draftFilters.industries = splitLabels(draft.industries);
        setKind(draft.kind);
        setRoles(draft.roles);
        setFilters(draftFilters);
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
      kind, roles, filters, areaMode,
    } satisfies ConditionsDraft : undefined);
  }, [loaded, localOnly, dirty, kind, roles, filters, areaMode]);

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

  const resetArea = { district: undefined, business_area: undefined, metro_line: undefined, metro_station: undefined };
  const hasCity = Boolean(filters.city?.trim());
  const hasParent = (value?: string) => Boolean(value?.trim() && value.trim() !== "不限");
  const selectCity = (city?: string, province?: string) => {
    if (city === filters.city && province === filters.province) return;
    const changedArea = city?.replace(/市$/, "") !== filters.city?.replace(/市$/, "") || Boolean(filters.province &&
      province?.replace(/省$/, "") !== filters.province.replace(/省$/, ""));
    edit(() => setFilters((current) => ({ ...current, ...(changedArea ? resetArea : {}), city, province })));
  };
  const setFilter = (key: LabelField, value: string) => {
    if ((filters[key] ?? "") === value) return;
    edit(() => setFilters((current) => ({
      ...current,
      ...(key === "district" ? { business_area: undefined } : {}),
      ...(key === "metro_line" ? { metro_station: undefined } : {}),
      [key]: value,
    })));
  };
  const field = (key: LabelField, placeholder: string, disabled = false, caption = filterLabels[key]) => <label>
    {caption}
    <input value={filters[key] ?? ""} placeholder={placeholder} disabled={disabled}
      aria-label={caption !== filterLabels[key] ? filterLabels[key] : undefined}
      maxLength={key === "query" ? 100 : 60} onChange={(event) => setFilter(key, event.target.value)} />
  </label>;
  const choices = (key: ChoiceField, disabled = false) => <FilterOptions label={filterLabels[key]} options={suggestions[key]}
    value={disabled ? "应届生" : filters[key]} onSelect={(value) => setFilter(key, value)} disabled={disabled}>
    {field(key, "填写 BOSS 页面上的选项名称", disabled, "")}
  </FilterOptions>;

  const selectEmployment = (next: EmploymentType) => edit(() => {
    if (kind === "campus" || next === "campus") {
      setFilters((value) => ({ ...value, experience: undefined }));
    }
    setKind(next);
  });
  const scope = [
    { value: filters.city, clear: () => selectCity() },
    { value: kind === "any" ? "" : employmentLabels[kind], clear: () => selectEmployment("any") },
    { value: filters.query, clear: () => edit(() => setFilters((value) => ({ ...value, query: undefined }))) },
  ];

  const closeDrawer = () => {
    drawer.current?.close();
    onClose?.();
  };

  const filterSection = (label: string, children: ReactNode) => {
    const expanded = !collapsedSections[label];
    const panel = `${advancedId}-${label}`;
    return <section className="task-filter-section" aria-label={label}>
      <h3><button type="button" className="task-filter-section-toggle" aria-expanded={expanded} aria-controls={panel}
        onClick={() => setCollapsedSections((current) => ({ ...current, [label]: !current[label] }))}>
        <ChevronRight size={17} aria-hidden="true" />{label}
      </button></h3>
      <div className="task-filter-section-body" id={panel} hidden={!expanded}>{children}</div>
    </section>;
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
          <fieldset className="task-condition-row"><legend>求职类型</legend>
            <div className="task-filter-choices">
              {(Object.keys(employmentLabels) as EmploymentType[]).map((next) =>
                <button key={next} type="button" aria-pressed={kind === next} onClick={() => selectEmployment(next)}>
                  {next === "full_time" ? "全职" : next === "campus" ? "校招" : employmentLabels[next]}
                </button>)}
            </div>
          </fieldset>
          <CityPicker city={filters.city} province={filters.province} onSelect={selectCity} />
          <label>岗位方向<input value={roles} maxLength={400} placeholder="例如：嵌入式软件、MCU；留空使用个人资料中的求职方向" onChange={(event) => edit(() => setRoles(event.target.value))} /></label>
          {field("query", "用于在 BOSS 搜索，可与岗位方向不同")}
          <button type="button" className="task-text-link task-more-filters" onClick={() => setActiveTab("advanced")}>更多条件在高级筛选 <ChevronRight size={15}/></button>
        </> : <>
          {filterSection("岗位要求", <>
            {choices("salary")}
            {choices("experience", kind === "campus")}
            {kind === "campus" && <p className="muted">校招使用 BOSS 的「全职」和「应届生」筛选。</p>}
            {choices("degree")}
          </>)}
          {filterSection("工作区域", <fieldset className="task-fieldset" disabled={!hasCity}>
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
            {!hasCity && <p className="muted">选择城市后可设置工作区域。</p>}
          </fieldset>)}
          {filterSection("职位分类", <FilterCatalogPicker label="职位分类" showLabel={false} options={positions} value={filters.positions ?? []}
            onChange={(values) => edit(() => setFilters((current) => ({ ...current, positions: values })))} />)}
          {filterSection("公司条件", <>
            {choices("company_size")}
            {choices("financing")}
            <FilterCatalogPicker label="公司行业" options={industries} value={(filters.industries ?? []).map((value) => [value])}
              onChange={(values) => edit(() => setFilters((current) => ({ ...current, industries: values.map(([value]) => value) })))} />
          </>)}
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
