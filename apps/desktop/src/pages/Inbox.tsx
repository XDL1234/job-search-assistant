import { useEffect, useState } from "react";
import { Building2, CalendarDays, ChevronRight, CircleAlert, CircleCheck, ClipboardList, Info, Send, ShieldCheck, SquarePen } from "lucide-react";
import { Button } from "../components/ui/button";
import { native, request } from "../bridge";
import type { Snapshot } from "../contracts";
import "./WorkspaceSettings.css";

type Category = "interview" | "review" | "other";
type Filter = Category | "all";
type Attention = Snapshot["attention"][number];
type Props = {
  snapshot: Snapshot;
  onChanged: () => void | Promise<void>;
  initialCategory?: Category;
};
const categories: { key: Filter; label: string }[] = [
  { key: "all", label: "全部" },
  { key: "interview", label: "面试邀约" },
  { key: "review", label: "岗位复核" },
  { key: "other", label: "其他" },
];
const reasons: Record<string, string> = {
  interview: "面试安排需要你确认", human_locked: "会话已由你接管",
  incomplete_analysis: "问题尚未完整识别，需要人工核对", new_commitment: "涉及新的承诺，需要你确认",
  mixed_request: "消息包含多种请求，需要整体处理", conflicting_rules: "预设话术有冲突，需要你选择",
  no_matching_rule: "没有符合条件的预设回复", partial_answer: "预设话术无法回答完整问题",
  unknown_question: "这是尚未配置回复的问题", needs_grounded_draft: "需要根据真实资料组织回复",
  missing_source: "个人资料缺少回答所需信息",
};

export function attentionCategory(reason: string): Category {
  if (/^(岗位精筛待人工查看|岗位或资料超过|岗位城市超出|岗位列表已变化|发送前岗位城市)/u.test(reason)) return "review";
  if (/^interview$|^(?:发现)?面试(?:邀约|事项)/u.test(reason)) return "interview";
  return "other";
}

export function findReplyTarget(snapshot: Snapshot, conversationId: string) {
  const conversation = snapshot.conversations?.find((c) => c.id === conversationId);
  if (conversation?.status !== "human" || !conversation.latest_message) return undefined;
  const candidates = snapshot.reply_targets?.filter((target) =>
    target.conversation === conversationId && target.message_key === conversation.latest_message &&
    target.attempt_id && target.text && snapshot.runs.some((run) =>
      run.id === target.run_id && ["running", "watching"].includes(run.status)),
  ) || [];
  if (candidates.length !== 1) return undefined;
  const message = snapshot.messages?.find((m) =>
    m.conversation_id === conversationId && m.message_key === conversation.latest_message);
  return !message || message.text === candidates[0].text ? candidates[0] : undefined;
}

function attentionDetails(snapshot: Snapshot, item: Attention) {
  const conversation = snapshot.conversations?.find((entry) => entry.id === item.subject);
  const message = snapshot.messages?.find((entry) => entry.conversation_id === item.subject && entry.message_key === conversation?.latest_message);
  const related = new Set(snapshot.reply_targets?.filter((entry) => entry.conversation === item.subject).map((entry) => entry.attempt_id));
  const attempt = snapshot.attempts.find((entry) => entry.id === item.subject || (related.size === 1 && related.has(entry.id)));
  const legacyReview = /^岗位精筛待人工查看：(.+?) · (.+?)；(.+)$/s.exec(item.reason);
  return {
    conversation,
    message,
    company: item.job_review?.company || attempt?.company || legacyReview?.[1],
    job: item.job_review?.job || attempt?.job || legacyReview?.[2],
    reason: item.job_review?.reason || legacyReview?.[3] || reasons[item.reason] || item.reason,
  };
}

function timeLabel(value?: string) {
  if (!value) return "";
  const time = new Date(value);
  if (Number.isNaN(time.valueOf())) return "";
  const today = new Date();
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  const day = time.toDateString();
  const prefix = day === today.toDateString() ? "今天" : day === yesterday.toDateString() ? "昨天" :
    time.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" });
  return `${prefix} ${time.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false })}`;
}

export function Inbox({ snapshot, onChanged, initialCategory }: Props) {
  const [filter, setFilter] = useState<Filter>(initialCategory ?? "all");
  const [selected, select] = useState("");
  useEffect(() => {
    setFilter(initialCategory ?? "all");
    select("");
  }, [initialCategory]);
  const pending = snapshot.attention.filter((item) => !item.resolved)
    .sort((a, b) => (b.created ?? "").localeCompare(a.created ?? ""));
  const visible = pending.filter((item) => filter === "all" || attentionCategory(item.reason) === filter);
  const opened = visible.find((item) => item.id === selected) || visible[0];
  const target = opened && findReplyTarget(snapshot, opened.subject);
  const latest = opened && snapshot.conversations?.find((item) => item.id === opened.subject)?.latest_message;
  return (
    <section className="inbox-page">
      <header className="workspace-title inbox-page-title">
        <h1>待办</h1><p>查看需要你决定的岗位、面试邀约和待人工处理的消息。</p>
      </header>
      <nav className="workspace-tabs inbox-tabs" aria-label="待办分类">
        {categories.map(({ key, label }) => (
          <button type="button" key={key} aria-pressed={filter === key} onClick={() => { setFilter(key); select(""); }}>
            {label}<span className="workspace-count">{key === "all" ? pending.length :
              pending.filter((item) => attentionCategory(item.reason) === key).length}</span>
          </button>
        ))}
      </nav>
      {opened ? (
        <div className="inbox-split">
          <div className="inbox-list" aria-label="待办事项">
            {visible.map((item) => {
              const { company, job, message, reason } = attentionDetails(snapshot, item);
              const category = attentionCategory(item.reason);
              const Icon = category === "interview" ? CalendarDays : category === "review" ? Building2 : ClipboardList;
              const created = timeLabel(item.created);
              return <button type="button" key={item.id} className="inbox-entry"
                aria-pressed={opened.id === item.id} data-category={category} onClick={() => select(item.id)}>
                <span className="workspace-icon"><Icon size={19} /></span>
                <span className="inbox-entry-copy">
                  <strong>{company ? (category === "interview" && job ? `${company} · ${job}` : company) :
                    job || categories.find(({ key }) => key === category)?.label}</strong>
                  <span>{message?.text || job || reason}</span>
                  {created && <time dateTime={item.created}>{created}</time>}
                </span>
                <ChevronRight size={16} aria-hidden="true" />
              </button>;
            })}
          </div>
          <InboxItem key={[opened.id, latest, target?.run_id, target?.attempt_id].join(":")}
            snapshot={snapshot} item={opened} onChanged={onChanged} />
        </div>
      ) : <div className="empty inbox-empty"><ShieldCheck /><strong>暂无{filter === "all" ? "新待办" : categories.find(({ key }) => key === filter)?.label}</strong></div>}
    </section>
  );
}

function InboxItem({ snapshot, item, onChanged }: Props & { item: Attention }) {
  const { conversation, message, company, job, reason } = attentionDetails(snapshot, item);
  const messageKey = conversation?.latest_message || "";
  const target = findReplyTarget(snapshot, item.subject);
  const original = message?.text || target?.text;
  const note = snapshot.notes?.filter((row) => row.attention_id === item.id && row.message_key === messageKey).at(-1);
  const category = attentionCategory(item.reason);
  const interview = category === "interview";
  const review = category === "review";
  const limit = interview ? 300 : 4000;
  const [text, setText] = useState(note?.text || "");
  const [confirmed, setConfirmed] = useState(false);
  const [preview, setPreview] = useState(false);
  const [resumeConfirmed, setResumeConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const canSend = Boolean(target && original && text.trim() && text.length <= limit);
  const facts = [item.job_review?.city, item.job_review?.salary].filter(Boolean);

  const perform = async (action: () => Promise<unknown>, result: string, reload = true) => {
    setError(""); setNotice(""); setBusy(true);
    try {
      await action();
      setNotice(result);
      if (reload) await onChanged();
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  };
  const update = (action: string, extra = {}) =>
    request("attention_update", { id: item.id, action, message_key: messageKey, ...extra });
  const openPreview = () => {
    if (!target || !canSend) return;
    setConfirmed(false);
    void perform(async () => {
      const result = await native<{ matched: boolean }>("inbox_open", { params: {
        run_id: target.run_id, conversation: item.subject, message_key: messageKey,
      } });
      if (result.matched !== true) throw new Error("会话最新消息未匹配，请刷新后重新核对");
      setPreview(true);
    }, "", false);
  };
  const send = () => {
    if (!preview || !canSend || !confirmed || !target) return;
    setConfirmed(false);
    setPreview(false);
    void perform(async () => {
      await native("inbox_send", { params: {
        run_id: target.run_id, conversation: item.subject, message_key: messageKey,
        text: text.trim(), confirmed: true,
      } });
      setText("");
    }, "回复已发送并核验。会话继续保持人工接管。");
  };
  const detailTitle = interview ? company || job || "面试邀约" :
    review ? job || company || "岗位复核" : company || job || "待处理事项";
  return (
    <article className="inbox-detail">
      <header className="inbox-detail-heading">
        <div className="inbox-detail-title">
          <h2>{detailTitle}</h2>
          {interview && <span className="workspace-tag warning"><CircleAlert size={16} />面试邀约 · 由你回复</span>}
        </div>
        {interview && company && job && <p>{job}</p>}
        {review && <><p>{[company, item.job_review?.city].filter(Boolean).join(" · ")}</p><span className="workspace-tag accent">来源 · 岗位筛选</span></>}
        {!interview && !review && <p>{reason}</p>}
      </header>
      {review && <div className="inbox-review">
        <p>筛选时有条件需要你确认。这里显示的是筛选记录。</p>
        {facts.length > 0 && <div className="inbox-facts"><CircleCheck size={21} /><strong>已确认</strong>
          <div>{facts.map((fact, index) => <span className="workspace-tag success" key={index}>{fact}</span>)}</div>
        </div>}
        <div className="inbox-uncertainty"><CircleAlert size={21} /><strong>待确认</strong><div>{reason}</div></div>
      </div>}
      {conversation && <section className="inbox-message">
        <h3>对方原话</h3>
        {original ? <div><span className="workspace-icon"><Building2 size={19} /></span>
          <div><p>{original}</p>{timeLabel(message?.created) && <time dateTime={message?.created}>{timeLabel(message?.created)}</time>}</div>
        </div> : <p className="muted">未取得可核对的最新消息，请在 BOSS 原会话查看。</p>}
      </section>}
      {error && <div className="banner error" role="alert">{error}</div>}
      {notice && <div className="banner" role="status">{notice}</div>}
      <section className="inbox-composer">
        <div className="inbox-compose-heading">
          <h3><label htmlFor={`note-${item.id}`}>{conversation ? "回复正文" : "处理备注"}</label></h3>
          {conversation && <span className="muted"><Info size={16} />发送后仍保持人工接管</span>}
        </div>
        <div className="inbox-editor">
          <textarea id={`note-${item.id}`} value={text} maxLength={limit} disabled={busy} onChange={(event) => {
            setText(event.target.value); setConfirmed(false); setNotice("");
          }} placeholder={conversation ? "输入要回复的内容" : "记录你的处理意见"} />
          {interview && <small>{text.length} / {limit}</small>}
        </div>
        {conversation?.status === "human" && !target &&
          <p className="muted">当前会话缺少唯一的活动任务绑定。可在 BOSS 回复，或在这里保存备注。</p>}
        <div className="inbox-actions">
          {!conversation && <span className="muted"><Info size={16} />{review ? "标记已处理只关闭此待办" : "备注只在本机保存"}</span>}
          <div>
            {conversation ? (
              <>
                <Button variant="secondary" disabled={busy || !text.trim()} onClick={() => void perform(
                  () => update("note", { text }), "备注已保存。",
                )}><SquarePen size={17} />保存备注</Button>
                {conversation.status === "human" ? <Button disabled={busy || !canSend} onClick={openPreview}>
                  <Send size={17} />预览并确认发送
                </Button> : <Button disabled={busy} onClick={() => void perform(
                  () => update("handoff", { confirmed: true }), "已由你接管会话。",
                )}>由我接管</Button>}
              </>
            ) : (
              <>
                {review && <Button variant="secondary" disabled={busy || !item.job_review?.url}
                  onClick={() => void perform(() => update("open_job"), "岗位已在浏览器中打开。", false)}>查看岗位</Button>}
                <Button disabled={busy} onClick={() => void perform(() => update("resolve", {
                  confirmed: true, ...(text.trim() && text.trim() !== note?.text ? { text } : {}),
                }), "待办已标记为已处理。")}>标记已处理</Button>
              </>
            )}
          </div>
        </div>
        {conversation?.status === "human" && <details className="inbox-resume">
          <summary>人工处理完成后恢复自动回复</summary>
          <label><input type="checkbox" checked={resumeConfirmed} disabled={busy}
            onChange={(event) => setResumeConfirmed(event.target.checked)} />我确认恢复此会话的自动回复</label>
          <Button variant="secondary" disabled={busy || !resumeConfirmed} onClick={() => {
            setResumeConfirmed(false);
            void perform(() => update("resume", { confirmed: true }), "已恢复此会话的自动回复。");
          }}>确认恢复自动回复</Button>
        </details>}
      </section>
      {preview && (
        <div className="modal-backdrop">
          <section className="modal inbox-confirm" role="dialog" aria-modal="true" aria-label="确认发送回复">
            <h2>确认发送回复</h2>
            <p className="muted"><CircleCheck size={17} />已核对当前会话</p>
            <h3>对方原话</h3><p className="inbox-original">{original}</p>
            <h3>回复正文</h3><p className="inbox-original">{text.trim()}</p>
            {error && <div className="banner error" role="alert">{error}</div>}
            <label><input type="checkbox" checked={confirmed} disabled={busy} onChange={(event) => setConfirmed(event.target.checked)} />
              我已核对最新消息与正文，确认发送这一次回复
            </label>
            <div className="actions end">
              <Button variant="secondary" disabled={busy} onClick={() => { setPreview(false); setConfirmed(false); }}>返回修改</Button>
              <Button disabled={busy || !confirmed} onClick={send}>确认发送回复</Button>
            </div>
          </section>
        </div>
      )}
    </article>
  );
}
