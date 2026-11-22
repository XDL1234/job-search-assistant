import { useCallback, useEffect, useId, useRef, useState, type CSSProperties } from "react";
import { isTauri } from "@tauri-apps/api/core";
import { ArrowDown, ArrowUp, CalendarDays, ChevronDown, ChevronLeft, ChevronRight, Download, FileImage, Info, Layers, Maximize2, Minus, Plus, Search, X } from "lucide-react";
import { request } from "../bridge";
import { Button } from "../components/ui/button";
import type { Attempt, Snapshot } from "../contracts";
import "./records.css";

type DateFilter = { kind: "all" | "today" | "week" } | { kind: "day"; day: string };
type Evidence = Snapshot["evidence"][number];
type Photo = { url: string; name: string };
type RecordTab = "application" | "evidence";

const resultLabels: Record<string, string> = {
  success: "成功", uncertain: "待核查", needs_user: "待处理", failed: "失败",
  in_progress: "处理中", no_match: "不匹配", skipped: "已跳过",
};
const evidenceLabels: Record<string, string> = {
  pre_submit: "操作前截图", result: "结果截图", failure: "失败截图", verification: "核查截图",
};
const presets = [["all", "全部日期"], ["today", "今天"], ["week", "最近 7 天"]] as const;
const dateLabel = new Intl.DateTimeFormat("zh-CN", { year: "numeric", month: "long", day: "numeric" });
const timeLabel = new Intl.DateTimeFormat("zh-CN", { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const tabNames: Record<RecordTab, string> = { application: "申请信息", evidence: "截图留档" };
const minListWidth = 38;
const maxListWidth = 70;
const clampListWidth = (width: number) => Math.max(minListWidth, Math.min(maxListWidth, width));

function localDay(date: Date) {
  if (Number.isNaN(date.getTime())) return "";
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

function parseDay(value: string) {
  return new Date(`${value}T00:00:00`);
}

export function attemptDay(attempt: Pick<Attempt, "started" | "updated">) {
  return localDay(new Date(attempt.started || attempt.updated)) || localDay(new Date(attempt.updated));
}

export function matchesRecordDay(day: string, filter: DateFilter, today: Date) {
  if (filter.kind === "all") return true;
  if (!day) return false;
  if (filter.kind === "day") return day === filter.day;
  const end = localDay(today);
  if (filter.kind === "today") return day === end;
  const start = localDay(new Date(today.getFullYear(), today.getMonth(), today.getDate() - 6));
  return start <= day && day <= end;
}

const channelLabel = (channel: string) => channel === "web" ? "官网" : channel === "boss" ? "BOSS" : channel;
const resultLabel = (attempt: Attempt) => attempt.status === "success"
  ? (attempt.channel === "boss" ? "已沟通" : "已提交")
  : resultLabels[attempt.status] ?? attempt.status;
const evidenceLabel = (kind: string) => evidenceLabels[kind] ?? kind;
const timestamp = (value?: string | null) => (value ? new Date(value).getTime() : 0) || 0;
const fullTime = (value?: string | null) => value && localDay(new Date(value)) ? new Date(value).toLocaleString("zh-CN") : "—";

function RecordDatePicker({ filter, onChange, dates }: {
  filter: DateFilter;
  onChange: (value: DateFilter) => void;
  dates: Set<string>;
}) {
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [month, setMonth] = useState(() => new Date());
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useId();
  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      setOpen(false);
      trigger.current?.focus();
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);

  const label = filter.kind === "day"
    ? dateLabel.format(parseDay(filter.day))
    : presets.find(([kind]) => kind === filter.kind)?.[1] ?? "全部日期";
  const first = new Date(month.getFullYear(), month.getMonth(), 1);
  const daysInMonth = new Date(month.getFullYear(), month.getMonth() + 1, 0).getDate();
  const leftPad = (first.getDay() + 6) % 7;
  const cellCount = Math.ceil((leftPad + daysInMonth) / 7) * 7;
  const today = localDay(new Date());
  const apply = (value: DateFilter) => { onChange(value); setOpen(false); trigger.current?.focus(); };
  const changeMonth = (delta: number) => setMonth(new Date(first.getFullYear(), first.getMonth() + delta, 1));

  return <div className="records-date-tools">
    <div className="records-date" ref={root}>
      <Button ref={trigger} variant="secondary" className="records-date-toggle" aria-expanded={open} aria-controls={panel}
        onClick={() => {
          if (!open) {
            setDraft(filter.kind === "day" ? filter.day : "");
            setMonth(filter.kind === "day" ? parseDay(filter.day) : new Date());
          }
          setOpen(!open);
        }}><CalendarDays size={17} />{label}<ChevronDown size={15} /></Button>
      {open && <section id={panel} className="records-calendar" role="dialog" aria-label="按投递日期筛选">
        <div className="records-calendar-head"><strong>按投递日期筛选</strong>
          <button className="records-accent-link" onClick={() => apply({ kind: "all" })}>清除</button>
        </div>
        <div className="records-presets">{presets.map(([kind, name]) => (
          <button key={kind} className="records-preset" aria-pressed={filter.kind === kind && !draft}
            onClick={() => apply({ kind })}>{name}</button>
        ))}</div>
        <div className="records-calendar-head records-calendar-month">
          <button aria-label="上个月" onClick={() => changeMonth(-1)}><ChevronLeft size={17} /></button>
          <strong aria-live="polite">{first.getFullYear()}年{first.getMonth() + 1}月</strong>
          <button aria-label="下个月" onClick={() => changeMonth(1)}><ChevronRight size={17} /></button>
        </div>
        <div className="records-calendar-grid">
          {["一", "二", "三", "四", "五", "六", "日"].map((day) => <span key={day}>周{day}</span>)}
          {Array.from({ length: cellCount }, (_, index) => {
            const cell = new Date(first.getFullYear(), first.getMonth(), index - leftPad + 1);
            const value = localDay(cell);
            return <button key={value} className="records-calendar-day" aria-label={dateLabel.format(cell)}
              aria-current={value === today ? "date" : undefined} aria-pressed={draft === value}
              data-adjacent={cell.getMonth() !== first.getMonth()} data-has-record={dates.has(value)}
              onClick={() => { setDraft(value); setMonth(cell); }}>{cell.getDate()}</button>;
          })}
        </div>
        <div className="records-calendar-footer">
          <label>记录日期<input aria-label="记录日期" type="date" value={draft} onChange={(event) => {
            const value = event.target.value;
            setDraft(value);
            if (value && localDay(parseDay(value)) === value) setMonth(parseDay(value));
          }} /></label>
          <Button disabled={!draft || localDay(parseDay(draft)) !== draft} onClick={() => apply({ kind: "day", day: draft })}>应用日期</Button>
        </div>
        <small>按沟通或提交尝试的日期</small>
      </section>}
    </div>
    {filter.kind !== "all" && <button className="records-accent-link" onClick={() => apply({ kind: "all" })}>清除日期</button>}
  </div>;
}

function EvidenceGallery({ items, title, onEnlarge }: { items: Evidence[]; title: string; onEnlarge: (photo: Photo) => void }) {
  const [chosen, setChosen] = useState("");
  const item = items.find((entry) => entry.id === chosen) ?? items[0];
  const [loaded, setLoaded] = useState<{ id: string; url?: string; error?: string }>();
  const evidenceId = item?.id;
  useEffect(() => {
    if (!evidenceId) return;
    let active = true;
    void request<{ url: string }>("read_image", { kind: "evidence", identity: evidenceId })
      .then(({ url }) => { if (active) setLoaded({ id: evidenceId, url }); })
      .catch((error) => { if (active) setLoaded({ id: evidenceId, error: String(error) }); });
    return () => { active = false; };
  }, [evidenceId]);
  const current = loaded?.id === evidenceId ? loaded : undefined;
  const url = current?.url;

  return <section className="records-evidence">
    <div className="records-section-heading"><h3>{title}</h3>
      {items.length > 1 && <select aria-label={`${title}截图`} value={item.id} onChange={(event) => setChosen(event.target.value)}>
        {items.map((entry, index) => <option key={entry.id} value={entry.id}>{index + 1}. {evidenceLabel(entry.kind)}</option>)}
      </select>}
    </div>
    {!item ? <p className="records-note"><Info size={16} />暂无已保存的截图</p>
      : <div className="records-photo">
        {url ? <>
          <img src={url} alt={evidenceLabel(item.kind)} />
          <button aria-label={`放大${evidenceLabel(item.kind)}`} title="放大截图"
            onClick={() => onEnlarge({ url, name: evidenceLabel(item.kind) })}><Maximize2 size={17} /></button>
        </> : current?.error ? <p role="alert">{current.error}</p> : <p role="status">正在读取留档截图…</p>}
      </div>}
  </section>;
}

function PhotoPreview({ photo, onClose }: { photo: Photo; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const viewport = useRef<HTMLDivElement>(null);
  const image = useRef<HTMLImageElement>(null);
  const [zoom, setZoom] = useState(1);
  const changeZoom = useCallback((factor: number | "fit", x?: number, y?: number) => {
    const view = viewport.current;
    const img = image.current;
    if (!view || !img?.naturalWidth) return;
    const current = Number(img.style.zoom || 1);
    const next = factor === "fit" ? 1 : Math.max(0.5, Math.min(5, current * factor));
    if (next === current) return;
    const frame = view.getBoundingClientRect();
    const before = img.getBoundingClientRect();
    const anchorX = x ?? frame.left + view.clientWidth / 2;
    const anchorY = y ?? frame.top + view.clientHeight / 2;
    const imageX = Math.max(0, Math.min(1, (anchorX - before.left) / before.width));
    const imageY = Math.max(0, Math.min(1, (anchorY - before.top) / before.height));
    img.style.zoom = String(next);
    // 使用参与布局的缩放，以便通过滚动条查看超出视口的部分。
    const after = img.getBoundingClientRect();
    view.scrollLeft += after.left + after.width * imageX - anchorX;
    view.scrollTop += after.top + after.height * imageY - anchorY;
    setZoom(next);
  }, []);
  useEffect(() => {
    if (dialog.current && !dialog.current.open) dialog.current.showModal();
    const view = viewport.current;
    if (!view) return;
    const wheel = (event: WheelEvent) => {
      if (!event.deltaY || event.shiftKey) return;
      event.preventDefault();
      const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? view.clientHeight : 1;
      changeZoom(Math.exp(-event.deltaY * unit / 550), event.clientX, event.clientY);
    };
    view.addEventListener("wheel", wheel, { passive: false });
    return () => view.removeEventListener("wheel", wheel);
  }, [changeZoom]);
  return <dialog ref={dialog} className="records-photo-dialog" aria-label="截图预览" onClose={onClose}
    onClick={(event) => {
      if (event.target === event.currentTarget || event.target === viewport.current) dialog.current?.close();
    }}>
    <div ref={viewport} className="records-photo-viewport" tabIndex={0} role="region" aria-label="截图查看区域">
      <img ref={image} src={photo.url} alt={photo.name} draggable={false} />
    </div>
    <div className="records-photo-tools" role="group" aria-label="截图操作">
      <button aria-label="缩小截图" title="缩小截图" disabled={zoom <= 0.5} onClick={() => changeZoom(0.8)}><Minus size={17} /></button>
      <button aria-label="适应窗口" title="适应窗口" onClick={() => changeZoom("fit")}>{Math.round(zoom * 100)}%</button>
      <button aria-label="放大截图" title="放大截图" disabled={zoom >= 5} onClick={() => changeZoom(1.25)}><Plus size={17} /></button>
      <button aria-label="关闭截图预览" onClick={() => dialog.current?.close()}><X size={20} /></button>
    </div>
  </dialog>;
}

function ApplicationAddress({ url }: { url: string }) {
  const [expanded, setExpanded] = useState(false);
  const address = useId();
  const action = expanded ? "收起" : "展开";
  return <div className="records-address" data-expanded={expanded}>
    <span id={address}>{url}</span>
    <button type="button" className="records-accent-link" aria-label={`${action}申请地址`}
      aria-controls={address} aria-expanded={expanded} onClick={() => setExpanded(!expanded)}>{action}</button>
  </div>;
}

function RecordDetails({ attempt, snapshot, tab, onTab, onClose, onEnlarge }: {
  attempt: Attempt; snapshot: Snapshot; tab: RecordTab;
  onTab: (tab: RecordTab) => void; onClose: () => void; onEnlarge: (photo: Photo) => void;
}) {
  const tabs = useId();
  const fields = snapshot.fields.filter((field) => field.attempt_id === attempt.id);
  const files = snapshot.evidence.filter((file) => file.attempt_id === attempt.id)
    .sort((a, b) => timestamp(b.created) - timestamp(a.created));
  const actions = snapshot.actions?.filter((action) => action.attempt_id === attempt.id) ?? [];
  const submissionActions = actions.filter((action) => action.kind === "greet" || action.kind === "submit");
  const receipt = submissionActions.sort((a, b) => timestamp(b.updated) - timestamp(a.updated))[0];
  const receiptIds = new Set(submissionActions.map((action) => action.evidence_id));
  const actionIds = new Set(actions.map((action) => action.evidence_id));
  const receiptFiles = files.filter((file) => receiptIds.has(file.id)
    || (!actionIds.has(file.id) && ["result", "failure", "verification"].includes(file.kind)));
  const filing = files.filter((file) => !receiptFiles.includes(file));
  const receiptLabel = receipt?.status === "succeeded" && receipt.evidence_id
    ? (receipt.kind === "greet" ? "已沟通" : "已提交") : "暂无明确回执";

  return <aside className="records-details" aria-label="投递详情">
    <div className="records-details-heading">
      <div><h2>{attempt.company}</h2><p>{attempt.job} · {channelLabel(attempt.channel)}</p></div>
      <div className="records-details-actions">
        <span className={`records-result ${attempt.status}`}>{resultLabel(attempt)}</span>
        <button type="button" aria-label="关闭投递详情" title="关闭详情" onClick={onClose}><X size={17} /></button>
      </div>
    </div>
    <div className="records-tabs" role="tablist" aria-label="投递详情内容" onKeyDown={(event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === "Home" ? "application" : event.key === "End" ? "evidence" : tab === "application" ? "evidence" : "application";
      onTab(next);
      document.getElementById(`${tabs}-${next}`)?.focus();
    }}>
      {(["application", "evidence"] as const).map((name) => <button id={`${tabs}-${name}`} key={name}
        role="tab" type="button" aria-selected={tab === name} aria-controls={`${tabs}-panel-${name}`}
        tabIndex={tab === name ? 0 : -1} onClick={() => onTab(name)}>{tabNames[name]}</button>)}
    </div>
    <div role="tabpanel" id={`${tabs}-panel-${tab}`} aria-labelledby={`${tabs}-${tab}`} tabIndex={0}>
      {tab === "application" ? <>
        <dl className="records-details-meta">
          <dt>投递时间</dt><dd>{fullTime(localDay(new Date(attempt.started || "")) ? attempt.started : attempt.updated)}</dd>
          <dt>最近更新</dt><dd>{fullTime(attempt.updated)}</dd>
          {attempt.url && <><dt>申请地址</dt><dd><ApplicationAddress key={attempt.id} url={attempt.url} /></dd></>}
          {attempt.reason && <><dt>处理说明</dt><dd>{attempt.reason}</dd></>}
        </dl>
        <h3>填写记录</h3>
        {fields.length ? <dl className="records-field-list">{fields.map((field) => <div key={field.name}>
          <dt>{field.name}</dt><dd>{field.value}<small>{field.source}</small></dd>
        </div>)}</dl> : <p className="records-note"><Info size={16} />暂无填写记录</p>}
      </> : <>
        <EvidenceGallery key={`${attempt.id}-filing`} items={filing} title="填写留档" onEnlarge={onEnlarge} />
        <section className="records-receipt">
          <h3>回执情况</h3>
          <p className="records-note"><Info size={17} /><span>{receiptLabel}{receipt?.reason && <small>{receipt.reason}</small>}</span></p>
          {receiptFiles.length > 0 && <EvidenceGallery key={`${attempt.id}-receipt`} items={receiptFiles} title="回执截图" onEnlarge={onEnlarge} />}
        </section>
      </>}
    </div>
  </aside>;
}

export function Records({ snapshot, onExport, exporting = false }: { snapshot: Snapshot; onExport: () => void; exporting?: boolean }) {
  const [query, setQuery] = useState("");
  const [channel, setChannel] = useState("");
  const [result, setResult] = useState("");
  const [sort, setSort] = useState("newest");
  const [filter, setFilter] = useState<DateFilter>({ kind: "all" });
  const [selection, setSelection] = useState<{ id: string; tab: RecordTab }>();
  const [listWidth, setListWidth] = useState(58);
  const [photo, setPhoto] = useState<Photo>();
  const content = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const drag = useRef<{ id: number; x: number; start: number; width: number } | null>(null);
  const listId = useId();
  const now = new Date();
  const search = query.trim().toLowerCase();
  const availableDays = new Set(snapshot.attempts.map(attemptDay));
  const records = snapshot.attempts.filter((record) => (
    (!channel || record.channel === channel) && (!result || record.status === result) &&
    `${record.company} ${record.job}`.toLowerCase().includes(search) && matchesRecordDay(attemptDay(record), filter, now)
  )).sort((a, b) => sort === "newest" ? timestamp(b.updated) - timestamp(a.updated) : timestamp(a.updated) - timestamp(b.updated));
  const selected = records.find((record) => record.id === selection?.id);
  useEffect(() => {
    if (selection && !selected) {
      setSelection(undefined);
      setPhoto(undefined);
    }
  }, [selection, selected]);
  const closeDetails = () => {
    list.current?.querySelector<HTMLButtonElement>('.records-select[aria-pressed="true"]')?.focus({ preventScroll: true });
    setSelection(undefined);
  };
  const scope = filter.kind === "day"
    ? parseDay(filter.day).toLocaleDateString("zh-CN", { month: "long", day: "numeric" })
    : presets.find(([kind]) => kind === filter.kind)?.[1];

  return <section className="records-page" aria-label="投递记录">
    <div className="records-heading">
      <div><h1>投递记录</h1><RecordDatePicker filter={filter} onChange={setFilter} dates={availableDays} /></div>
      <Button className="records-export" title="导出全部投递记录"
        disabled={exporting || !isTauri()} onClick={onExport}><Download size={18} />导出 Excel</Button>
    </div>
    <div className="records-filters" role="search" aria-label="筛选投递记录">
      <label className="records-search"><Search size={19} />
        <input aria-label="搜索记录" placeholder="搜索公司或岗位" value={query} onChange={(event) => setQuery(event.target.value)} />
      </label>
      <div className="records-filter-options">
        <select aria-label="投递渠道" value={channel} onChange={(event) => setChannel(event.target.value)}>
          <option value="">全部渠道</option><option value="boss">BOSS</option><option value="web">官网</option>
        </select>
        <select aria-label="投递结果" value={result} onChange={(event) => setResult(event.target.value)}>
          <option value="">全部结果</option>
          {Object.entries(resultLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
        </select>
        <select aria-label="更新时间排序" value={sort} onChange={(event) => setSort(event.target.value)}>
          <option value="newest">最近更新</option><option value="oldest">最早更新</option>
        </select>
      </div>
    </div>
    <div ref={content} className="records-content" data-split={Boolean(selected)}
      style={{ "--records-list-width": `${listWidth}%` } as CSSProperties}>
      <div ref={list} id={listId} className="records-list">
        {filter.kind !== "all" && <p className="records-count" role="status">{scope} · {records.length} 条记录</p>}
        <div className="records-table-scroll"><table>
          <thead><tr><th>公司和岗位</th><th>渠道</th><th>结果</th><th aria-sort={sort === "newest" ? "descending" : "ascending"}>
            <button onClick={() => setSort(sort === "newest" ? "oldest" : "newest")}>更新 {sort === "newest" ? <ArrowDown size={15} /> : <ArrowUp size={15} />}</button>
          </th><th><span className="sr-only">操作</span></th></tr></thead>
          <tbody>{records.map((record) => {
            const updated = new Date(record.updated);
            const day = localDay(updated);
            return <tr key={record.id} data-selected={selected?.id === record.id}
              onClick={() => setSelection({ id: record.id, tab: "application" })}>
              <td><button className="records-select" aria-pressed={selected?.id === record.id}
                title={`${record.company} · ${record.job}`}>
                <strong>{record.company}</strong>
                <span>{record.job}</span>
              </button></td>
              <td><span className="records-channel">{channelLabel(record.channel)}</span></td>
              <td><span className={`records-result ${record.status}`}>{resultLabel(record)}</span></td>
              <td>{day ? <time dateTime={record.updated} title={fullTime(record.updated)}>{day.slice(5)} {timeLabel.format(updated)}</time> : "—"}</td>
              <td><button className="records-more" aria-label="查看证据" title="查看证据" onClick={(event) => {
                event.stopPropagation();
                setSelection({ id: record.id, tab: "evidence" });
              }}>
                <FileImage size={17} />
              </button></td>
            </tr>;
          })}</tbody>
        </table></div>
        {records.length === 0 && <div className="records-empty"><Layers size={25} />
          <p>{snapshot.attempts.length ? "没有符合条件的投递记录" : "暂无投递记录"}</p>
        </div>}
      </div>
      {selected && selection && <>
        <div className="records-divider" role="separator" aria-label="调整投递记录两栏宽度"
          aria-orientation="vertical" aria-controls={listId} aria-valuemin={minListWidth} aria-valuemax={maxListWidth}
          aria-valuenow={Math.round(listWidth)} aria-valuetext={`列表宽度 ${Math.round(listWidth)}%`} tabIndex={0}
          onPointerDown={(event) => {
            const width = content.current?.clientWidth;
            if (event.button !== 0 || !width || drag.current) return;
            event.preventDefault();
            event.currentTarget.focus({ preventScroll: true });
            event.currentTarget.setPointerCapture(event.pointerId);
            drag.current = { id: event.pointerId, x: event.clientX, start: listWidth, width };
          }}
          onPointerMove={(event) => {
            const start = drag.current;
            if (start?.id !== event.pointerId) return;
            setListWidth(clampListWidth(start.start + 100 * (event.clientX - start.x) / start.width));
          }}
          onLostPointerCapture={() => { drag.current = null; }}
          onKeyDown={(event) => {
            const widths: Record<string, number> = { ArrowLeft: listWidth - 3, ArrowRight: listWidth + 3, Home: minListWidth, End: maxListWidth };
            const next = widths[event.key];
            if (next === undefined) return;
            event.preventDefault();
            setListWidth(clampListWidth(next));
          }}
        />
        <RecordDetails key={selected.id} attempt={selected} snapshot={snapshot} tab={selection.tab}
          onTab={(next) => setSelection({ id: selected.id, tab: next })} onClose={closeDetails} onEnlarge={setPhoto} />
      </>}
    </div>
    {photo && <PhotoPreview photo={photo} onClose={() => setPhoto(undefined)} />}
  </section>;
}
