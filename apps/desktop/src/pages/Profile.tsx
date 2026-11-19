import { useCallback, useEffect, useRef, useState } from "react";
import { Eye, EyeOff, FileText, Plus, Save, TextSearch, Upload } from "lucide-react";
import { Button } from "../components/ui/button";
import { native, request } from "../bridge";
import { entries, fillFromResume, record, type Extraction, type ProfileData } from "./profile-draft";
import "./WorkspaceSettings.css";

type Saved = { profile: ProfileData; source_file?: string; version_id: string };

type Field = {
  key: string;
  label: string;
  type?: "email" | "tel" | "password" | "textarea";
  options?: readonly string[];
  placeholder?: string;
  autoComplete?: string;
};

const degrees = ["高中及以下", "中专", "大专", "本科", "硕士", "博士", "其他"] as const;
const basicFields: Field[] = [
  { key: "name", label: "姓名", autoComplete: "name" },
  { key: "target_role", label: "求职方向" },
  { key: "contact.email", label: "邮箱", type: "email", autoComplete: "email" },
  { key: "contact.phone", label: "手机号", type: "tel", autoComplete: "tel" },
  { key: "id_number", label: "身份证号", type: "password", autoComplete: "off" },
  { key: "highest_degree", label: "最高学历", options: degrees },
  { key: "country", label: "当前所在国家" },
  { key: "province", label: "当前所在省份" },
  { key: "city", label: "当前所在城市" },
  { key: "graduation_date", label: "毕业时间", placeholder: "例如 2028-06" },
];
const resumeFields = [...basicFields.map((field) => field.key), "notes"];
const dates = [
  { key: "start_date", label: "开始时间", placeholder: "例如 2024-09" },
  { key: "end_date", label: "结束时间", placeholder: "例如 2027-06" },
];
const groups: { key: string; label: string; fields: Field[]; ongoing?: boolean }[] = [
  { key: "education", label: "教育经历", fields: [
    { key: "school", label: "学校名称" }, { key: "degree", label: "学历", options: degrees },
    { key: "college", label: "学院名称" }, { key: "major", label: "专业名称" }, ...dates,
  ] },
  { key: "internships", label: "实习经历", ongoing: true, fields: [
    { key: "company", label: "单位名称" }, { key: "role", label: "职位名称" }, ...dates,
    { key: "description", label: "实习内容", type: "textarea" },
  ] },
  { key: "work_experience", label: "工作经历", ongoing: true, fields: [
    { key: "company", label: "单位名称" }, { key: "role", label: "职位名称" }, ...dates,
    { key: "description", label: "工作内容", type: "textarea" },
  ] },
  { key: "projects", label: "项目经历", ongoing: true, fields: [
    { key: "name", label: "项目名称" }, { key: "role", label: "职务" }, ...dates,
    { key: "description", label: "项目描述", type: "textarea" },
  ] },
  { key: "awards", label: "获奖情况", fields: [
    { key: "name", label: "获奖项" }, { key: "date", label: "获奖时间", placeholder: "例如 2025-10" },
    { key: "level", label: "获奖级别" }, { key: "description", label: "获奖描述", type: "textarea" },
  ] },
  { key: "languages", label: "语言能力", fields: [
    { key: "language", label: "语言类型" }, { key: "proficiency", label: "掌握程度" },
    { key: "listening_speaking", label: "听说" }, { key: "reading_writing", label: "读写" },
    { key: "certificate", label: "语言证书及成绩" },
  ] },
];

export function hasResumeFields(result: Extraction) {
  return Boolean(fillFromResume({}, result, resumeFields, groups, new Set(), {}));
}

function asText(value: unknown) {
  if (typeof value === "string" || typeof value === "number") return String(value);
  return "";
}

function FieldInput({ field, value, onChange, disabled }: {
  field: Field;
  value: unknown;
  onChange: (value: string) => void;
  disabled?: boolean;
}) {
  const text = asText(value);
  return <label className={field.type === "textarea" ? "settings-field-wide" : undefined}>
    {field.label}
    {field.options ? <select aria-label={field.label} value={text} disabled={disabled}
      onChange={(event) => onChange(event.target.value)}>
      <option value="">请选择</option>
      {text && !field.options.includes(text) && <option value={text}>{text}</option>}
      {field.options.map((option) => <option key={option}>{option}</option>)}
    </select> : field.type === "textarea" ?
      <textarea rows={3} value={text} placeholder={field.placeholder} disabled={disabled}
        onChange={(event) => onChange(event.target.value)} /> :
      <input type={field.type || "text"} value={text} placeholder={field.placeholder} autoComplete={field.autoComplete}
        disabled={disabled} onChange={(event) => onChange(event.target.value)} />}
  </label>;
}

export function Profile({ onSaved, resumeExtraction }: {
  onSaved: () => void | Promise<void>;
  resumeExtraction?: Extraction;
}) {
  const [source, setSource] = useState("");
  const [extraction, setExtraction] = useState<Extraction>();
  const [profile, setProfile] = useState<ProfileData>({});
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [dirty, setDirty] = useState(false);
  const [showId, setShowId] = useState(false);
  const editedFields = useRef(new Set<string>());
  const removedRows = useRef<Record<string, unknown[]>>({});
  const filledGroups = useRef(new Map<string, Set<string>>());
  const appliedExtraction = useRef<Extraction | undefined>(undefined);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    let active = true;
    void request<Saved | null>("get_profile").then((value) => {
      if (!active || !value) return;
      setSaved(Boolean(value.version_id));
      setProfile(value.profile);
      setSource(value.source_file || "");
    }).catch((failure) => { if (active) setError(String(failure)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  const applyResume = useCallback((result: Extraction) => {
    const completed = filledGroups.current.get(result.source_sha256) ?? new Set<string>();
    const merged = fillFromResume(profile, result, resumeFields, groups, editedFields.current, removedRows.current, completed);
    setError("");
    if (!merged) {
      setNotice(result.warnings?.join(" ") || "没有可填充的新信息，已保留现有资料。");
      return;
    }
    for (const { key } of groups) {
      if (Object.hasOwn(merged.extraction.profile, key)) completed.add(key);
    }
    if (completed.size) filledGroups.current.set(result.source_sha256, completed);
    setProfile(merged.profile);
    setExtraction(merged.extraction);
    setDirty(true);
    setNotice(result.ocr_pages?.length
      ? "已通过本机 OCR 补齐空白字段，请对照简历原件核对后保存。"
      : "已从简历填充空白字段，请核对后保存。");
  }, [profile]);

  useEffect(() => {
    if (!resumeExtraction || loading || busy || resumeExtraction === appliedExtraction.current) return;
    appliedExtraction.current = resumeExtraction;
    applyResume(resumeExtraction);
  }, [resumeExtraction, loading, busy, applyResume]);

  const perform = async (action: () => Promise<void>) => {
    setBusy(true);
    setError("");
    setNotice("");
    try { await action(); }
    catch (failure) { setError(String(failure)); }
    finally { setBusy(false); }
  };

  const chooseSource = () => void perform(async () => {
    const path = await native<string | null>("pick_file", { kind: "profile" });
    if (!path) return;
    const result = await request<Extraction>("extract_profile", { path });
    setSource(result.source_file);
    if (!/\.json$/i.test(path)) {
      applyResume(result);
      return;
    }
    setExtraction(result);
    setProfile((current) => ({
      ...current,
      ...result.profile,
      contact: { ...record(current.contact), ...record(result.profile.contact) },
    }));
    setDirty(true);
    setNotice("已读取资料，请核对后保存。");
  });

  const save = () => void perform(async () => {
    const result = await request<Saved>("save_personal_info", { profile, ...(extraction ? { extraction } : {}) });
    setProfile(result.profile);
    setSource(result.source_file || "");
    setExtraction(undefined);
    setSaved(true);
    setDirty(false);
    setNotice("个人信息已保存。");
    await onSaved();
  });

  const update = (change: (value: ProfileData) => ProfileData) => {
    setProfile(change);
    setDirty(true);
    setNotice("");
  };
  const setField = (key: string, value: string) => {
    editedFields.current.add(key);
    const [section, field] = key.split(".");
    update((current) => field ? {
      ...current, [section]: { ...record(current[section]), [field]: value },
    } : { ...current, [section]: value });
  };
  const setRows = (key: string, rows: unknown[]) => update((current) => ({ ...current, [key]: rows }));
  const sources = { ...profile.sources, ...extraction?.sources };

  return <section className="settings-profile" aria-label="个人信息">
    {error && <div className="banner error" role="alert">{error}</div>}
    {notice && <div className="banner" role="status">{notice}</div>}
    <fieldset disabled={loading || busy} className="settings-profile-form settings-personal-stack">
      <article className="settings-personal">
        <header>
          <div><h2>基本信息</h2><p>按需填写。网申仅使用你已保存的信息。</p></div>
          <div className="settings-profile-actions">
            <span className={"workspace-tag" + (saved && !dirty ? " success" : "")}>
              <Save size={15} aria-hidden="true" />{loading ? "正在读取" : dirty ? "更改待保存" : saved ? "已保存资料" : "本机保存"}
            </span>
            <Button type="button" variant="secondary" onClick={() => void perform(async () =>
              applyResume(await request<Extraction>("extract_resume_profile")))}>
              <TextSearch size={17} />从已导入简历填充
            </Button>
          </div>
        </header>
        <div className="settings-personal-fields">
          {basicFields.map((field) => {
            const [section, child] = field.key.split(".");
            const value = child ? record(profile[section])[child] : profile[section];
            const input = <FieldInput field={showId && field.type === "password" ? { ...field, type: undefined } : field}
              value={value} onChange={(text) => setField(field.key, text)} />;
            return field.type === "password" ? <div key={field.key} className="settings-sensitive-field">
              {input}
              <Button type="button" variant="ghost" aria-label={showId ? "隐藏身份证号" : "显示身份证号"}
                onClick={() => setShowId((visible) => !visible)}>
                {showId ? <EyeOff size={17} /> : <Eye size={17} />}
              </Button>
            </div> : <div key={field.key}>{input}</div>;
          })}
        </div>
      </article>

      {groups.map((group) => {
        const rows = entries(profile[group.key]);
        return <article className="settings-personal" key={group.key}>
          <header><h2>{group.label}</h2>
            <Button type="button" variant="secondary" onClick={() => setRows(group.key, [...rows, {}])}>
              <Plus size={16} />添加{group.label}
            </Button>
          </header>
          {rows.map((entry, index) => {
            const row = record(entry);
            const ongoing = group.ongoing && row.current === true;
            const replace = (value: unknown) => setRows(group.key, rows.map((old, at) => at === index ? value : old));
            return <div className="settings-profile-entry" role="group" aria-label={group.label + " " + (index + 1)} key={index}>
              <div className="settings-entry-title"><strong>{group.label} {index + 1}</strong>
                <Button type="button" variant="ghost" aria-label={"移除" + group.label + " " + (index + 1)}
                  onClick={() => {
                    removedRows.current[group.key] = [...(removedRows.current[group.key] ?? []), entry];
                    setRows(group.key, rows.filter((_, at) => at !== index));
                  }}>移除</Button>
              </div>
              <div className="settings-personal-fields">
                {typeof entry === "string" ? <FieldInput field={{ key: "original", label: "已有内容", type: "textarea" }}
                  value={entry} onChange={replace} /> : group.fields.map((field) => {
                    const hasOngoing = field.key === "end_date" && group.ongoing;
                    const editedField = <FieldInput key={field.key} field={field} value={row[field.key]}
                      disabled={hasOngoing && ongoing} onChange={(value) => replace({ ...row, [field.key]: value })} />;
                    return hasOngoing ? <div className="settings-end-date" key={field.key}>
                      {editedField}
                      <label className="settings-ongoing-field">
                        <input type="checkbox" checked={ongoing} onChange={(event) => {
                          const current = event.target.checked;
                          replace({ ...row, current, ...(current ? { end_date: "" } : {}) });
                        }} /> 至今
                      </label>
                    </div> : editedField;
                  })}
              </div>
            </div>;
          })}
        </article>;
      })}

      <article className="settings-personal">
        <header><h2>补充说明</h2></header>
        <div className="settings-personal-fields">
          <FieldInput field={{ key: "notes", label: "网申补充说明", type: "textarea" }} value={profile.notes}
            onChange={(text) => setField("notes", text)} />
        </div>
      </article>

      <article className="settings-file-card">
        <h2>导入个人资料</h2>
        <div className="settings-file-row">
          <span className="settings-file-icon"><FileText size={20} aria-hidden="true" /></span>
          <div className="settings-file-copy"><strong title={source}>{source.split(/[\\/]/).at(-1) || "尚未导入资料"}</strong>
            <span>支持 JSON、PDF 和 DOCX</span>
          </div>
          <Button type="button" variant="secondary" onClick={chooseSource}><Upload size={17} />{source ? "重新导入" : "导入资料"}</Button>
        </div>
        {sources && Object.keys(sources).length > 0 && <details className="settings-source">
          <summary>字段出处</summary>
          <dl className="settings-provenance">
            {Object.entries(sources).map(([key, provenance]) => <div key={key}>
              <dt>{basicFields.find((field) => field.key === key)?.label ?? key}</dt>
              <dd>{provenance.locator}{provenance.edited ? " · 已编辑" : ""}</dd>
            </div>)}
          </dl>
        </details>}
      </article>
      <div className="settings-profile-footer">
        <span className="muted">留空字段会跳过。网站的必填项缺失时，需要你补充。</span>
        <Button type="button" disabled={!dirty} onClick={save}>{busy ? "处理中…" : "保存个人信息"}</Button>
      </div>
    </fieldset>
  </section>;
}
