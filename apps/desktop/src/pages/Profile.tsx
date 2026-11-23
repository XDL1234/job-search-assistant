import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronRight, Eye, EyeOff, FileText, Pencil, Plus, Save, TextSearch, Upload } from "lucide-react";
import { Button } from "../components/ui/button";
import { native, request, WorkerRequestError } from "../bridge";
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
const documentTypes = ["居民身份证", "护照", "港澳居民来往内地通行证", "台湾居民来往大陆通行证", "其他"] as const;
const yesNo = ["是", "否"] as const;
const basicFields: Field[] = [
  { key: "name", label: "姓名", autoComplete: "name" },
  { key: "gender", label: "性别", options: ["男", "女"] },
  { key: "birth_date", label: "出生日期", placeholder: "例如 2002-08-15", autoComplete: "bday" },
  { key: "nationality_region", label: "国籍或地区" },
  { key: "document_type", label: "证件类型", options: documentTypes },
  { key: "id_number", label: "身份证号", type: "password", autoComplete: "off" },
  { key: "document_number", label: "证件号码", type: "password", autoComplete: "off" },
];
const contactFields: Field[] = [
  { key: "contact.email", label: "邮箱", type: "email", autoComplete: "email" },
  { key: "phone_country_code", label: "手机国际区号", type: "tel", placeholder: "例如 +86", autoComplete: "tel-country-code" },
  { key: "contact.phone", label: "手机号", type: "tel", autoComplete: "tel" },
  { key: "country", label: "当前所在国家" },
  { key: "province", label: "当前所在省份" },
  { key: "city", label: "当前所在城市" },
  { key: "home_city", label: "家庭所在城市" },
  { key: "school_city", label: "学校所在城市" },
];
const careerFields: Field[] = [
  { key: "target_role", label: "求职方向" },
  { key: "highest_degree", label: "最高学历", options: degrees },
  { key: "graduation_date", label: "毕业时间", placeholder: "例如 2028-06" },
  { key: "recruitment_source", label: "招聘信息来源" },
];
const noteFields: Field[] = [
  { key: "competition_summary", label: "竞赛经历概述", type: "textarea" },
  { key: "awards_summary", label: "获奖情况概述", type: "textarea" },
  { key: "notes", label: "网申补充说明", type: "textarea" },
];
const profileSections = [
  { key: "basic", label: "基本信息", fields: basicFields },
  { key: "contact", label: "联系方式与地区", fields: contactFields },
  { key: "career", label: "求职信息", fields: careerFields },
];
const attachments = [
  { key: "photo_path", label: "证件照", kind: "photo" },
  { key: "portfolio_path", label: "作品集", kind: "portfolio" },
  { key: "other_attachment_path", label: "其他附件", kind: "portfolio" },
] as const;
const resumeFields = [...basicFields, ...contactFields, ...careerFields, ...noteFields].map((field) => field.key);
const dates = [
  { key: "start_date", label: "开始时间", placeholder: "例如 2024-09" },
  { key: "end_date", label: "结束时间", placeholder: "例如 2027-06" },
];
const groups: { key: string; label: string; fields: Field[]; ongoing?: boolean }[] = [
  { key: "education", label: "教育经历", fields: [
    { key: "school", label: "学校名称" }, { key: "degree", label: "学历", options: degrees },
    { key: "college", label: "学院名称" }, { key: "major", label: "专业名称" },
    { key: "major_category", label: "专业类别" }, { key: "gpa", label: "绩点", placeholder: "例如 3.7 / 4.0" }, ...dates,
    { key: "laboratory", label: "实验室" }, { key: "advisor", label: "导师" },
    { key: "national_scholarship", label: "获得过国家奖学金", options: yesNo },
    { key: "exchange_student", label: "交换生经历", options: yesNo },
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
    { key: "name", label: "项目名称" }, { key: "organization", label: "公司或组织名称" },
    { key: "role", label: "职务" }, ...dates,
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

function FieldInput({ field, value, onChange, disabled, autoFocus }: {
  field: Field;
  value: unknown;
  onChange: (value: string) => void;
  disabled?: boolean;
  autoFocus?: boolean;
}) {
  const text = asText(value);
  return <label className={field.type === "textarea" ? "settings-field-wide" : undefined}>
    {field.label}
    {field.options ? <select aria-label={field.label} value={text} disabled={disabled} autoFocus={autoFocus}
      onChange={(event) => onChange(event.target.value)}>
      <option value="">请选择</option>
      {text && !field.options.includes(text) && <option value={text}>{text}</option>}
      {field.options.map((option) => <option key={option}>{option}</option>)}
    </select> : field.type === "textarea" ?
      <textarea rows={3} value={text} placeholder={field.placeholder} disabled={disabled} autoFocus={autoFocus}
        onChange={(event) => onChange(event.target.value)} /> :
      <input type={field.type || "text"} value={text} placeholder={field.placeholder} autoComplete={field.autoComplete}
        disabled={disabled} autoFocus={autoFocus} onChange={(event) => onChange(event.target.value)} />}
  </label>;
}

export function Profile({ onSaved, resumeExtraction }: {
  onSaved: () => void | Promise<void>;
  resumeExtraction?: Extraction;
}) {
  const [source, setSource] = useState("");
  const [extraction, setExtraction] = useState<Extraction>();
  const [profile, setProfile] = useState<ProfileData>({});
  const [version, setVersion] = useState("");
  const [versionConflict, setVersionConflict] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loadState, setLoadState] = useState<"loading" | "ready" | "failed">("loading");
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [dirty, setDirty] = useState(false);
  const [shownDocument, setShownDocument] = useState<string>();
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({});
  const [editingRows, setEditingRows] = useState<Record<string, number | undefined>>({});
  const editedFields = useRef(new Set<string>());
  const removedRows = useRef<Record<string, unknown[]>>({});
  const filledGroups = useRef(new Map<string, Set<string>>());
  const appliedExtraction = useRef<Extraction | undefined>(undefined);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const loadSaved = useCallback((value: Saved | null) => {
    setVersion(value?.version_id ?? "");
    setProfile(value?.profile ?? {});
    setSource(value?.source_file ?? "");
    setExtraction(undefined);
    setDirty(false);
    setEditingRows({});
    setVersionConflict(false);
  }, []);

  useEffect(() => {
    let active = true;
    void request<Saved | null>("get_profile").then((value) => {
      if (!active) return;
      loadSaved(value);
      setLoadState("ready");
    }).catch((failure) => {
      if (!active) return;
      setLoadState("failed");
      setError(String(failure));
    });
    return () => { active = false; };
  }, [loadAttempt, loadSaved]);

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
    if (!resumeExtraction || loadState !== "ready" || busy || resumeExtraction === appliedExtraction.current) return;
    appliedExtraction.current = resumeExtraction;
    applyResume(resumeExtraction);
  }, [resumeExtraction, loadState, busy, applyResume]);

  const perform = async (action: () => Promise<void>) => {
    setBusy(true);
    setError("");
    setNotice("");
    try { await action(); }
    catch (failure) {
      if (failure instanceof WorkerRequestError && failure.code === "profile_version_conflict") setVersionConflict(true);
      else setError(String(failure));
    }
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
    setEditingRows({});
    setDirty(true);
    setNotice("已读取资料，请核对后保存。");
  });

  const save = () => void perform(async () => {
    const result = await request<Saved>("save_personal_info", { profile, expected_version: version, ...(extraction ? { extraction } : {}) });
    loadSaved(result);
    setNotice("个人信息已保存。");
    await onSaved();
  });

  const discardAndReload = () => void perform(async () => {
    const latest = await request<Saved | null>("get_profile");
    loadSaved(latest);
    editedFields.current.clear();
    filledGroups.current.clear();
    removedRows.current = {};
    setShownDocument(undefined);
    setNotice("已读取最新个人信息。");
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
  const setGroupOpen = (key: string, open: boolean) => setOpenGroups((current) => ({ ...current, [key]: open }));
  const setEditingRow = (key: string, index?: number) => setEditingRows((current) => ({ ...current, [key]: index }));
  const jumpTo = (key: string, group?: boolean) => {
    if (group) setGroupOpen(key, true);
    const target = document.getElementById(`profile-section-${key}`);
    target?.scrollIntoView({ block: "start" });
    target?.focus({ preventScroll: true });
  };
  const documentType = asText(profile.document_type).trim();
  const usesIdNumber = !documentType || /^(?:中国)?(?:居民)?身份证$/.test(documentType);
  const displayedField = (field: Field) => field.key !== (usesIdNumber ? "document_number" : "id_number");
  const renderField = (field: Field) => {
    const [section, child] = field.key.split(".");
    const value = child ? record(profile[section])[child] : profile[section];
    const shown = shownDocument === field.key;
    const input = <FieldInput key={field.key} field={shown ? { ...field, type: undefined } : field}
      value={value} onChange={(text) => {
        if (field.key === "document_type") setShownDocument(undefined);
        setField(field.key, text);
      }} />;
    return field.type === "password" ? <div key={field.key} className="settings-sensitive-field">
      {input}
      <Button type="button" variant="ghost" aria-label={`${shown ? "隐藏" : "显示"}${field.label}`}
        onClick={() => setShownDocument(shown ? undefined : field.key)}>
        {shown ? <EyeOff size={17} /> : <Eye size={17} />}
      </Button>
    </div> : input;
  };

  return <section className="settings-profile" aria-label="个人信息">
    {error && <div className="banner error" role="alert">{error}
      {loadState === "failed" && <Button type="button" variant="secondary" onClick={() => {
        setError("");
        setLoadState("loading");
        setLoadAttempt((attempt) => attempt + 1);
      }}>重新读取个人信息</Button>}
    </div>}
    {notice && <div className="banner" role="status">{notice}</div>}
    <fieldset disabled={loadState !== "ready" || busy} className="settings-profile-form settings-profile-layout">
      <nav className="settings-profile-directory" aria-label="个人信息目录">
        {profileSections.map(({ key, label }) => <button type="button" key={key} onClick={() => jumpTo(key)}>{label}</button>)}
        {groups.map(({ key, label }) => {
          const count = entries(profile[key]).length;
          return <button type="button" key={key} onClick={() => jumpTo(key, true)}>
            {label}<span aria-label={`${count} 条`}>{count}</span>
          </button>;
        })}
        <button type="button" onClick={() => jumpTo("notes")}>补充说明</button>
        <button type="button" onClick={() => jumpTo("attachments")}>网申附件</button>
        <button type="button" onClick={() => jumpTo("import")}>导入个人资料</button>
      </nav>
      <div className="settings-personal-stack settings-profile-sections">
        {profileSections.map(({ key, label, fields }) => <article key={key} className="settings-personal"
          id={`profile-section-${key}`} tabIndex={-1}>
          <header>
            <div><h2>{label}</h2>{key === "basic" && <p>按需填写。网申仅使用你已保存的信息。</p>}</div>
            {key === "basic" && <div className="settings-profile-actions">
              <span className={"workspace-tag" + (version && !dirty ? " success" : "")}>
                <Save size={15} aria-hidden="true" />{loadState === "loading" ? "正在读取" : loadState === "failed" ? "读取失败" : dirty ? "更改待保存" : version ? "已保存资料" : "本机保存"}
              </span>
              <Button type="button" variant="secondary" onClick={() => void perform(async () =>
                applyResume(await request<Extraction>("extract_resume_profile")))}>
                <TextSearch size={17} />从已导入简历填充
              </Button>
            </div>}
          </header>
          <div className="settings-personal-fields">{fields.filter(displayedField).map(renderField)}</div>
        </article>)}

        {groups.map((group) => {
          const rows = entries(profile[group.key]);
          const expanded = openGroups[group.key] ?? (rows.length > 0);
          const panel = `personal-${group.key}`;
          return <article className="settings-personal settings-profile-group" key={group.key}
            id={`profile-section-${group.key}`} tabIndex={-1}>
            <header>
              <h2><button type="button" className="settings-group-toggle" aria-expanded={expanded} aria-controls={panel}
                onClick={() => setGroupOpen(group.key, !expanded)}>
                <ChevronRight size={18} aria-hidden="true" />{group.label}
              </button></h2>
              <Button type="button" variant="secondary" onClick={() => {
                setGroupOpen(group.key, true);
                setEditingRow(group.key, rows.length);
                setRows(group.key, [...rows, {}]);
              }}>
                <Plus size={16} />添加{group.label}
              </Button>
            </header>
            <div id={panel} className="settings-group-fields" hidden={!expanded}>
              {!rows.length && <p className="settings-entry-empty">尚未添加{group.label}</p>}
              {rows.map((entry, index) => {
                const row = record(entry);
                const ongoing = group.ongoing && row.current === true;
                const editing = editingRows[group.key] === index;
                const label = group.label + " " + (index + 1);
                const title = (typeof entry === "string" ? entry : asText(row[group.fields[0].key])).trim() || label;
                const period = [asText(row.start_date), ongoing ? "至今" : asText(row.end_date)].filter(Boolean).join(" — ");
                const summary = [asText(row.degree || row.role || row.level || row.proficiency), period || asText(row.date)]
                  .filter(Boolean).join(" · ");
                const replace = (value: unknown) => setRows(group.key, rows.map((old, at) => at === index ? value : old));
                return <div className="settings-profile-entry" role="group" aria-label={label} key={index}>
                  <div className="settings-entry-title">
                    <div className="settings-entry-summary"><strong title={title}>{title}</strong>{summary && <span>{summary}</span>}</div>
                    <div className="settings-entry-actions">
                      <Button type="button" variant="ghost" aria-label={(editing ? "完成编辑" : "编辑") + label}
                        aria-expanded={editing} onClick={(event) => {
                          setEditingRow(group.key, editing ? undefined : index);
                          if (editing) event.currentTarget.focus();
                        }}>{!editing && <Pencil size={15} />}{editing ? "完成" : "编辑"}</Button>
                      <Button type="button" variant="ghost" className="settings-entry-remove" aria-label={"移除" + label}
                        onClick={() => {
                          removedRows.current[group.key] = [...(removedRows.current[group.key] ?? []), entry];
                          const editingIndex = editingRows[group.key];
                          if (editingIndex !== undefined && editingIndex >= index) {
                            setEditingRow(group.key, editingIndex === index ? undefined : editingIndex - 1);
                          }
                          setRows(group.key, rows.filter((_, at) => at !== index));
                        }}>移除</Button>
                    </div>
                  </div>
                  {editing && <div className="settings-personal-fields">
                    {typeof entry === "string" ? <FieldInput field={{ key: "original", label: "已有内容", type: "textarea" }}
                      value={entry} onChange={replace} autoFocus /> : group.fields.map((field, fieldIndex) => {
                        const hasOngoing = field.key === "end_date" && group.ongoing;
                        const editedField = <FieldInput key={field.key} field={field} value={row[field.key]}
                          autoFocus={fieldIndex === 0} disabled={hasOngoing && ongoing}
                          onChange={(value) => replace({ ...row, [field.key]: value })} />;
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
                  </div>}
                </div>;
              })}
            </div>
          </article>;
        })}

        <article className="settings-personal" id="profile-section-notes" tabIndex={-1}>
          <header><h2>补充说明</h2></header>
          <div className="settings-personal-fields">{noteFields.map(renderField)}</div>
        </article>

        <article className="settings-personal settings-profile-attachments" id="profile-section-attachments" tabIndex={-1}>
          <header><div><h2>网申附件</h2><p>选好文件并保存后，可在「网页简历填写」时自动匹配并添加附件。</p></div></header>
          {attachments.map(({ key, label, kind }) => {
            const path = asText(profile[key]);
            return <div className="settings-file-row" key={key}>
              <span className="settings-file-icon"><FileText size={20} aria-hidden="true" /></span>
              <div className="settings-file-copy"><strong>{label}</strong>
                <span className="settings-profile-file-name" title={path}>{path ? path.split(/[\\/]/).at(-1) : "未选择文件"}</span>
              </div>
              {path && <Button type="button" variant="ghost" aria-label={`移除${label}引用`} onClick={() => setField(key, "")}>移除</Button>}
              <Button type="button" variant="secondary" aria-label={`选择${label}`} onClick={() => void perform(async () => {
                const selected = await native<string | null>("pick_file", { kind });
                if (selected) setField(key, selected);
              })}><Upload size={16} />{path ? "更换" : "选择文件"}</Button>
            </div>;
          })}
        </article>

        <article className="settings-file-card" id="profile-section-import" tabIndex={-1}>
          <h2>导入个人资料</h2>
          <div className="settings-file-row">
            <span className="settings-file-icon"><FileText size={20} aria-hidden="true" /></span>
            <div className="settings-file-copy"><strong title={source}>{source.split(/[\\/]/).at(-1) || "尚未导入资料"}</strong>
              <span>支持 JSON、PDF 和 DOCX</span>
            </div>
            <Button type="button" variant="secondary" onClick={chooseSource}><Upload size={17} />{source ? "重新导入" : "导入资料"}</Button>
          </div>
        </article>
      </div>
      <div className="settings-profile-footer">
        {versionConflict ? <>
          <span className="settings-profile-hint" role="alert">其他窗口已更新个人信息。本页草稿已保留，请先复制需要保留的改动。</span>
          <Button type="button" variant="secondary" onClick={discardAndReload}>放弃草稿并读取最新版</Button>
        </> : <>
          <span className="settings-profile-hint">留空字段会跳过。网站的必填项缺失时，需要你补充。</span>
          <Button type="button" disabled={!dirty} onClick={save}>{busy ? "处理中…" : "保存个人信息"}</Button>
        </>}
      </div>
    </fieldset>
  </section>;
}
