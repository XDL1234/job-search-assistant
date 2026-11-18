import { useEffect, useState } from "react";
import { FileText, RefreshCw, Save } from "lucide-react";
import { Button } from "../components/ui/button";
import { native, request } from "../bridge";
import "./WorkspaceSettings.css";

type Source = { locator: string; edited?: boolean };
type ProfileData = {
  name?: string;
  contact?: { email?: string; phone?: string };
  target_role?: string;
  resume_text?: string;
  sources?: Record<string, Source>;
  [key: string]: unknown;
};
type Extraction = {
  source_file: string;
  source_sha256: string;
  profile: ProfileData;
  sources: Record<string, Source>;
  blocks: { text: string; locator: string }[];
  warnings: string[];
};
type Saved = {
  profile: ProfileData;
  resume_path: string;
  source_file: string;
  version_id: string;
};
const fieldLabels: Record<string, string> = {
  name: "姓名", target_role: "求职方向", "contact.email": "邮箱", "contact.phone": "手机号",
};

function FileBadge({ path }: { path: string }) {
  const ext = /\.([a-z0-9]+)$/i.exec(path)?.[1]?.toLowerCase();
  return <div className={`settings-file-icon ${ext === "pdf" ? "pdf" : "document"}`}>
    <FileText size={25} /><span>{ext?.toUpperCase() || "文件"}</span>
  </div>;
}

const filename = (path: string) => path.split(/[\\/]/).at(-1) || "";
export function Profile({ onSaved }: { onSaved: () => void | Promise<void> }) {
  const [source, setSource] = useState("");
  const [resume, setResume] = useState("");
  const [extraction, setExtraction] = useState<Extraction | null>(null);
  const [profile, setProfile] = useState<ProfileData>({});
  const [saved, setSaved] = useState<Saved | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [dirty, setDirty] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  useEffect(() => {
    let active = true;
    void request<Saved | null>("get_profile").then((value) => {
      if (!active || !value) return;
      setSaved(value);
      setProfile(value.profile);
      setResume(value.resume_path);
      setSource(value.source_file);
    }).catch((failure) => { if (active) setError(String(failure)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);
  const perform = async (action: () => Promise<void>) => {
    setBusy(true);
    setError("");
    setNotice("");
    try { await action(); }
    catch (failure) { setError(String(failure)); }
    finally { setBusy(false); }
  };
  const adoptSource = async (path: string) => {
    const result = await request<Extraction>("extract_profile", { path });
    setSource(result.source_file);
    setExtraction(result);
    setProfile(result.profile);
    if (!resume && /\.(?:pdf|docx)$/i.test(path)) setResume(path);
    setDirty(true);
    setNotice("已在本机提取，请核对姓名和联系方式。");
  };
  const chooseSource = () => void perform(async () => {
    const path = await native<string | null>("pick_file", { kind: "profile" });
    if (path) await adoptSource(path);
  });
  const chooseResume = () => void perform(async () => {
    const path = await native<string | null>("pick_file", { kind: "resume" });
    if (path) { setResume(path); setDirty(true); }
  });
  const save = () => void perform(async () => {
    const value = extraction || await request<Extraction>("extract_profile", { path: source });
    const result = await request<Saved>("save_profile", {
      extraction: value, profile, resume_path: resume, confirmed: true,
    });
    setSaved(result);
    setProfile(result.profile);
    setDirty(false);
    setNotice("资料已保存，可用于创建投递任务。");
    await onSaved();
  });
  const setField = (key: string, value: string) => {
    setProfile((current) => ({ ...current, [key]: value }));
    setDirty(true);
    setNotice("");
  };
  const setContact = (key: string, value: string) => {
    setProfile((current) => ({ ...current, contact: { ...current.contact, [key]: value } }));
    setDirty(true);
    setNotice("");
  };
  const sources = profile.sources ?? extraction?.sources;
  return (
    <section className="settings-profile" aria-label="资料与简历">
      {error && <div className="banner error" role="alert">{error}</div>}
      {notice && <div className="banner" role="status">{notice}</div>}
      <fieldset disabled={loading || busy} className="settings-profile-form">
        <div className="settings-profile-grid">
          <div className="settings-file-stack">
            <article className="settings-file-card">
              <h2>投递附件</h2>
              <div className="settings-file-row">
                <FileBadge path={resume} />
                <div className="settings-file-copy">
                  <strong title={resume}>{filename(resume) || "尚未选择简历"}</strong>
                  <span>实际发送的文件</span>
                </div>
                <Button variant="secondary" onClick={chooseResume}><RefreshCw size={17} />{resume ? "更换简历" : "选择简历"}</Button>
              </div>
            </article>
            <article className="settings-file-card">
              <h2>资料来源</h2>
              <div className="settings-file-row">
                <FileBadge path={source} />
                <div className="settings-file-copy"><strong title={source}>{filename(source) || "尚未导入资料"}</strong></div>
                <Button variant="secondary" onClick={chooseSource}><RefreshCw size={17} />{source ? "重新导入" : "导入资料"}</Button>
              </div>
              <details className="settings-source">
                <summary>原文与字段出处</summary>
                {sources && Object.keys(sources).length > 0 && <dl className="settings-provenance">
                  {Object.entries(sources).map(([key, provenance]) =>
                    <div key={key}><dt>{fieldLabels[key] || key}</dt><dd>{provenance.locator}{provenance.edited ? " · 已编辑" : ""}</dd></div>)}
                </dl>}
                {extraction?.blocks.length ? extraction.blocks.map((block, index) =>
                  <div className="settings-text-block" key={index}><small>{block.locator}</small><p>{block.text}</p></div>) :
                  <pre>{profile.resume_text || "导入后可核对原文或字段出处。"}</pre>}
                {extraction?.warnings.map((warning) => <p className="muted" key={warning}>{warning}</p>)}
              </details>
            </article>
          </div>
          <article className="settings-personal">
            <header>
              <h2>个人信息</h2>
              <span className={`workspace-tag ${saved && !dirty ? "success" : ""}`}>
                <Save size={15} aria-hidden="true" />{loading ? "正在读取" : dirty ? "更改待保存" : saved ? "已保存资料" : "本机保存"}
              </span>
            </header>
            <div className="settings-personal-fields">
              <label>姓名<input value={profile.name || ""} autoComplete="name" onChange={(event) => setField("name", event.target.value)} /></label>
              <label>求职方向<input value={profile.target_role || ""} onChange={(event) => setField("target_role", event.target.value)} /></label>
              <label>邮箱<input type="email" autoComplete="email" value={profile.contact?.email || ""} onChange={(event) => setContact("email", event.target.value)} /></label>
              <label>手机号<input type="tel" autoComplete="tel" value={profile.contact?.phone || ""} onChange={(event) => setContact("phone", event.target.value)} /></label>
            </div>
          </article>
        </div>
        <div className="settings-profile-footer">
          <span className="muted">保存资料会创建本机资料版本</span>
          <Button disabled={busy || !source || !resume || !profile.name?.trim()} onClick={save}>
            {busy ? "处理中…" : "保存资料"}
          </Button>
        </div>
      </fieldset>
    </section>
  );
}
