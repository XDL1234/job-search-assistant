import { useEffect, useState } from "react";
import { ExternalLink, FileText, Upload } from "lucide-react";
import { Button } from "../components/ui/button";
import { native, request } from "../bridge";
import type { Extraction } from "./profile-draft";

type ResumeFile = { name: string; path: string; sha256: string };
type ResumePreview = { text: string; message?: string; ocr_pages?: number[] };

export function Resume({ onSaved, onExtracted }: {
  onSaved: () => void | Promise<void>;
  onExtracted: (result: Extraction) => boolean;
}) {
  const [file, setFile] = useState<ResumeFile | null>();
  const [preview, setPreview] = useState<ResumePreview>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => {
    let active = true;
    void request<ResumeFile | null>("get_resume")
      .then((result) => { if (active) setFile(result); })
      .catch((failure) => { if (active) { setFile(null); setError(String(failure)); } });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!file) return;
    let active = true;
    void request<ResumePreview>("preview_resume")
      .then((result) => { if (active) setPreview(result); })
      .catch((failure) => {
        if (active) setPreview({ text: "", message: String(failure) });
      });
    return () => { active = false; };
  }, [file]);

  const perform = async (action: () => Promise<void>) => {
    setBusy(true);
    setError("");
    setNotice("");
    try { await action(); }
    catch (failure) { setError(String(failure)); }
    finally { setBusy(false); }
  };

  const importFile = () => void perform(async () => {
    const path = await native<string | null>("pick_file", { kind: "resume" });
    if (!path) return;
    const imported = await request<ResumeFile>("import_resume", { path });
    setNotice("简历已导入，正在本机提取个人信息…");
    try {
      await onSaved();
      const result = await request<Extraction>("extract_resume_profile");
      if (!onExtracted(result)) setNotice(result.ocr_pages?.length
        ? "简历已导入。本机 OCR 提取了文本，但未找到可填充的字段。"
        : "简历已导入，未识别到可填充的个人信息。");
    } catch (failure) {
      setNotice("简历已导入。");
      throw failure;
    } finally {
      setPreview(undefined);
      setFile(imported);
    }
  });

  const openFile = () => void perform(async () => { await request("open_resume"); });
  return <section className="settings-resume" aria-label="简历">
    {error && <div className="banner error" role="alert">{error}</div>}
    {notice && <div className="banner" role="status">{notice}</div>}
    <article className="settings-file-card">
      <h2>投递简历</h2>
      <div className="settings-file-row">
        <span className={"settings-file-icon" + (file?.name.toLowerCase().endsWith(".pdf") ? " pdf" : "")}>
          <FileText size={20} aria-hidden="true" />
          {file?.name.split(".").at(-1)?.toUpperCase()}
        </span>
        <div className="settings-file-copy">
          <strong title={file?.name}>{file === undefined ? "正在读取…" : file?.name || "尚未导入简历"}</strong>
          <span>{file ? "投递时使用此附件" : "支持 PDF 和 DOCX，扫描 PDF 会尝试在本机识别"}</span>
        </div>
        {file && <Button type="button" variant="secondary" onClick={openFile} disabled={busy}>
          <ExternalLink size={16} />打开原件
        </Button>}
        <Button type="button" onClick={importFile} disabled={busy || file === undefined}>
          <Upload size={16} />{file ? "更换简历" : "导入简历"}
        </Button>
      </div>
    </article>
    {file && <article className="settings-file-card">
      <h2>简历预览</h2>
      {preview?.text ? <>
        {Boolean(preview.ocr_pages?.length) && <p className="settings-resume-empty">
          第 {preview.ocr_pages?.join("、")} 页使用本机 OCR，请对照原件核对。
        </p>}
        {preview.message && <p className="settings-resume-empty">{preview.message}</p>}
        <pre className="settings-resume-text">{preview.text}</pre>
      </>
        : <p className="settings-resume-empty" role="status">{preview
          ? preview.message || "未提取到文本，可打开原件查看。"
          : "正在本机读取简历内容…"}</p>}
    </article>}
  </section>;
}
