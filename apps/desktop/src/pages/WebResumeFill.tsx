import { useEffect, useRef, useState } from "react";
import { ClipboardList, ExternalLink, RefreshCw, X } from "lucide-react";
import { native } from "../bridge";
import { Button } from "../components/ui/button";

type Site = "alibaba" | "baic";
type Tab = { id: string; url: string };
const statuses = {
  ready: "可填写", existing: "已有内容", missing: "资料缺失", manual: "需核对", unmapped: "未匹配",
};
type Field = { id: string; label: string; section: string; status: keyof typeof statuses; required: boolean; reason?: string; requires_selection?: boolean };
type Scan = { scanId: string; fields: Field[] };
type FillResult = { filled: string[]; skipped: string[] };
const sites = [["alibaba", "阿里校招"], ["baic", "北汽招聘"]] as const;

function siteName(tab: Tab) {
  return new URL(tab.url).hostname === "campus-talent.alibaba.com" ? "阿里校招" : "北汽招聘";
}

export function WebResumeFill({ onClose }: { onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [tabs, setTabs] = useState<Tab[]>([]);
  const [tabId, setTabId] = useState("");
  const [scan, setScan] = useState<Scan>();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [suggestions, setSuggestions] = useState<Record<string, string>>({});
  const [result, setResult] = useState<FillResult>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  useEffect(() => { if (dialog.current && !dialog.current.open) dialog.current.showModal(); }, []);

  const perform = async (action: () => Promise<void>) => {
    setBusy(true);
    setError("");
    setNotice("");
    try { await action(); }
    catch (failure) { setError(String(failure)); }
    finally { setBusy(false); }
  };

  const reset = () => {
    setScan(undefined);
    setSelected(new Set());
    setSuggestions({});
    setResult(undefined);
  };

  const loadTabs = () => perform(async () => {
    reset();
    const pages = await native<Tab[]>("web_autofill_tabs");
    setTabs(pages);
    setTabId((current) => pages.some((page) => page.id === current) ? current : pages.length === 1 ? pages[0].id : "");
    if (!pages.length) setNotice("请先在求职 Chrome 打开简历页并登录，然后刷新标签页。");
  });

  const openSite = (site: Site) => perform(async () => {
    reset();
    await native("web_autofill_open", { site });
    setNotice("已在求职 Chrome 打开页面。登录后回来刷新标签页。");
  });

  const scanTab = () => perform(async () => {
    reset();
    const preview = await native<Scan>("web_autofill_scan", { tabId });
    setScan(preview);
    setSelected(new Set(preview.fields.filter((field) => field.status === "ready" && !field.requires_selection).map((field) => field.id)));
    if (!preview.fields.length) setNotice("当前页未识别到简历字段。请确认已登录，页面已加载。");
  });

  const askAdvice = () => perform(async () => {
    if (!scan) return;
    const { suggestions: replies } = await native<{ suggestions: { id: string; note: string }[] }>("web_autofill_advice", { tabId, scanId: scan.scanId });
    setSuggestions(Object.fromEntries(replies.map(({ id, note }) => [id, note])));
    if (!replies.length) setNotice("没有生成可用的填写建议。");
  });

  const fillSelected = () => perform(async () => {
    if (!scan) return;
    try {
      const outcome = await native<FillResult>("web_autofill_fill", { tabId, scanId: scan.scanId, fieldIds: [...selected] });
      setResult(outcome);
      setNotice(`已填写 ${outcome.filled.length} 项${outcome.skipped.length ? `，${outcome.skipped.length} 项需要核对` : ""}。请回网页检查并保存。`);
    } finally {
      setScan({ ...scan, scanId: "" });
    }
  });

  const canAdvise = scan?.fields.some(({ status }) => status === "manual" || status === "unmapped");
  return <dialog ref={dialog} className="web-resume-fill" aria-labelledby="web-resume-title" onClose={onClose} onCancel={(event) => {
    event.preventDefault();
    if (!busy) onClose();
  }}>
    <header><h2 id="web-resume-title"><ClipboardList size={21} aria-hidden="true"/> 网页简历填写</h2>
      <Button variant="ghost" aria-label="关闭网页简历填写" disabled={busy} onClick={onClose}><X size={20} aria-hidden="true"/></Button>
    </header>
    <div className="web-resume-fill-body" aria-busy={busy}>
      <p>使用本机已保存的资料填写空白字段并添加匹配的附件。请核对预览中的文件，再填写并回网页检查和提交。</p>
      <div className="web-resume-fill-actions">
        {sites.map(([site, label]) => <Button key={site} variant="secondary" disabled={busy} onClick={() => void openSite(site)}>{label}<ExternalLink size={15} aria-hidden="true"/></Button>)}
        <Button variant="secondary" disabled={busy} onClick={() => void loadTabs()}><RefreshCw size={15} aria-hidden="true"/>刷新标签页</Button>
      </div>
      <div className="web-resume-fill-actions">
        <select aria-label="选择网申标签页" disabled={busy || !tabs.length} value={tabId} onChange={(event) => { setTabId(event.target.value); reset(); }}>
          <option value="">选择已登录的简历标签页</option>
          {tabs.map((tab, index) => <option value={tab.id} key={tab.id}>{siteName(tab)} · 标签 {index + 1}</option>)}
        </select>
        <Button variant="secondary" disabled={busy || !tabId} onClick={() => void scanTab()}>{scan ? "重新扫描" : "扫描字段"}</Button>
      </div>
      {error && <div className="banner error" role="alert">{error}</div>}
      {notice && <div className="banner" role="status">{notice}</div>}
      {!!scan?.fields.length && <div className="task-table-wrap"><table className="task-table web-resume-fill-table">
        <thead><tr><th>字段</th><th>状态</th><th>处理说明</th></tr></thead>
        <tbody>{scan.fields.map((field) => <tr key={field.id}>
          <td><label>
            {field.status === "ready" && <input type="checkbox" checked={selected.has(field.id)} disabled={busy || !scan.scanId}
              onChange={(event) => {
                const next = new Set(selected);
                if (event.target.checked) next.add(field.id);
                else next.delete(field.id);
                setSelected(next);
              }}/>}
            {field.label}{field.required && <span className="web-resume-fill-required"> · 必填</span>}
          </label>{field.section && <small>{field.section}</small>}</td>
          <td>{result?.filled.includes(field.id) ? "已填写" : result?.skipped.includes(field.id) ? "需核对" : statuses[field.status]}</td>
          <td>{suggestions[field.id] || field.reason || "—"}</td>
        </tr>)}</tbody>
      </table></div>}
    </div>
    <footer className="web-resume-fill-actions">
      {canAdvise && <Button variant="secondary" disabled={busy || !scan?.scanId} onClick={() => void askAdvice()}>获取 AI 填写建议</Button>}
      <Button disabled={busy || !scan?.scanId || !selected.size} onClick={() => void fillSelected()}>填写所选空白字段{selected.size > 0 ? ` (${selected.size})` : ""}</Button>
    </footer>
  </dialog>;
}
