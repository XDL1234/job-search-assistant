import { useEffect, useState } from "react";
import { Button } from "../components/ui/button";
import { native, request } from "../bridge";

type ProfileData = {
  name?: string;
  contact?: { email?: string; phone?: string };
  target_role?: string;
  resume_text?: string;
  [key: string]: unknown;
};
type Extraction = {
  source_file: string;
  source_sha256: string;
  profile: ProfileData;
  sources: Record<string, { locator: string }>;
  blocks: { text: string; locator: string }[];
  warnings: string[];
};
type Saved = {
  profile: ProfileData;
  resume_path: string;
  source_file: string;
  version_id: string;
};

export function Profile({ onSaved }: { onSaved: () => void }) {
  const [source, setSource] = useState("");
  const [resume, setResume] = useState("");
  const [extraction, setExtraction] = useState<Extraction | null>(null);
  const [profile, setProfile] = useState<ProfileData>({});
  const [saved, setSaved] = useState<Saved | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  useEffect(() => {
    void request<Saved | null>("get_profile")
      .then((value) => {
        setSaved(value);
        if (value) {
          setProfile(value.profile);
          setResume(value.resume_path);
          setSource(value.source_file);
        }
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, []);
  const perform = async (action: () => Promise<void>) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await action();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };
  const choose = (kind: string, update: (path: string) => void) =>
    perform(async () => {
      const path = await native<string | null>("pick_file", { kind });
      if (path) update(path);
    });
  const extract = () =>
    perform(async () => {
      const value = await request<Extraction>("extract_profile", {
        path: source,
      });
      setExtraction(value);
      setProfile(value.profile);
      if (!resume) setResume(source);
      setNotice("已在本机提取。请核对姓名和联系方式，原文与出处保留在下方。");
    });
  const save = () =>
    perform(async () => {
      const value =
        extraction ||
        (await request<Extraction>("extract_profile", { path: source }));
      const result = await request<Saved>("save_profile", {
        extraction: value,
        profile,
        resume_path: resume,
        confirmed: true,
      });
      setSaved(result);
      setNotice("资料已保存为独立版本，可前往配置投递任务。");
      onSaved();
    });
  const field = (key: string, value: string) =>
    setProfile((p) => ({ ...p, [key]: value }));
  const contact = (key: string, value: string) =>
    setProfile((p) => ({ ...p, contact: { ...p.contact, [key]: value } }));
  return (
    <section className="card form-page">
      <div className="section-title">
        <h2>简历与个人资料</h2>
        <span className="badge">{saved ? "已保存资料" : "尚未导入"}</span>
      </div>
      <p>
        导入已有资料，在本机提取并核对。投递使用原简历文件，保存资料不会改写原文件。
      </p>
      {error && (
        <div className="banner error" role="alert">
          {error}
        </div>
      )}
      {notice && (
        <div className="banner" role="status">
          {notice}
        </div>
      )}
      <fieldset
        disabled={loading || busy}
        style={{ border: 0, padding: 0, margin: 0 }}
      >
        <label>
          资料文件（PDF / DOCX / JSON）
          <div className="path-input">
            <input
              value={source}
              onChange={(e) => {
                setSource(e.target.value);
                setExtraction(null);
                setProfile({});
              }}
              placeholder="选择已有简历或详细资料"
            />
            <Button
              variant="secondary"
              disabled={busy}
              onClick={() =>
                void choose("profile", (p) => {
                  setSource(p);
                  setExtraction(null);
                  setProfile({});
                })
              }
            >
              选择文件
            </Button>
            <Button disabled={busy || !source} onClick={() => void extract()}>
              提取资料
            </Button>
          </div>
        </label>
        <label>
          投递附件
          <div className="path-input">
            <input
              value={resume}
              onChange={(e) => setResume(e.target.value)}
              placeholder="实际发送的原简历文件"
            />
            <Button
              variant="secondary"
              disabled={busy}
              onClick={() => void choose("resume", setResume)}
            >
              选择简历
            </Button>
          </div>
        </label>
        <div className="form-grid">
          <label>
            姓名
            <input
              value={profile.name || ""}
              onChange={(e) => field("name", e.target.value)}
            />
          </label>
          <label>
            求职方向
            <input
              value={profile.target_role || ""}
              onChange={(e) => field("target_role", e.target.value)}
            />
          </label>
          <label>
            邮箱
            <input
              value={profile.contact?.email || ""}
              onChange={(e) => contact("email", e.target.value)}
            />
          </label>
          <label>
            手机号
            <input
              value={profile.contact?.phone || ""}
              onChange={(e) => contact("phone", e.target.value)}
            />
          </label>
        </div>
        {extraction?.warnings.map((w) => (
          <p className="muted" key={w}>
            {w}
          </p>
        ))}
        <details>
          <summary>原文与字段出处</summary>
          {extraction ? (
            extraction.blocks.map((block, i) => (
              <div className="source-block" key={i}>
                <small>{block.locator}</small>
                <p>{block.text}</p>
              </div>
            ))
          ) : (
            <pre>
              {profile.resume_text ||
                "提取后可核对原文；原文不会因为修改上述字段而被改写。"}
            </pre>
          )}
        </details>
        <div className="actions">
          <Button
            disabled={busy || !source || !resume || !profile.name}
            onClick={() => void save()}
          >
            {busy ? "处理中…" : "核对并保存资料"}
          </Button>
          <span className="muted">
            缺失信息保持为空，后续问题可转人工处理。
          </span>
        </div>
      </fieldset>
    </section>
  );
}
