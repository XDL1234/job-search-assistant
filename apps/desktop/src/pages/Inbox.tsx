import { useState } from "react";
import { ShieldCheck } from "lucide-react";
import { Button } from "../components/ui/button";
import { native, request } from "../bridge";
import type { Snapshot } from "../contracts";

type Props = { snapshot: Snapshot; onChanged: () => void | Promise<void> };
const reasons: Record<string,string> = {
  interview: "面试安排需要你确认", human_locked: "会话已由你接管",
  incomplete_analysis: "问题尚未完整识别，需要人工核对", new_commitment: "涉及新的承诺，需要你确认",
  mixed_request: "消息包含多种请求，需要整体处理", conflicting_rules: "预设话术有冲突，需要你选择",
  no_matching_rule: "没有符合条件的预设回复", partial_answer: "预设话术无法回答完整问题",
  unknown_question: "这是尚未配置回复的问题", needs_grounded_draft: "需要根据真实资料组织回复",
  missing_source: "个人资料缺少回答所需信息",
};

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

export function Inbox({ snapshot, onChanged }: Props) {
  const pending = snapshot.attention.filter((item) => !item.resolved);
  return (
    <section className="card form-page">
      <div className="section-title">
        <h2>待我处理 · {pending.length}</h2>
        <span>面试邀约由你回复，发送后仍保持人工接管</span>
      </div>
      <p>查看对方原话后，可保存处理意见，或单独确认发送。恢复自动回复需要再次明确确认。</p>
      {pending.length ? pending.map((item) => {
        const conversation = snapshot.conversations?.find((c) => c.id === item.subject);
        const target = findReplyTarget(snapshot,item.subject);
        return <InboxItem
          key={[item.id,conversation?.latest_message,target?.run_id,target?.attempt_id].join(":")}
          snapshot={snapshot} item={item} onChanged={onChanged}
        />;
      }) : (
        <div className="empty"><ShieldCheck /><strong>暂无新待办</strong></div>
      )}
    </section>
  );
}

function InboxItem({ snapshot, item, onChanged }: Props & { item: Snapshot["attention"][number] }) {
  const conversation = snapshot.conversations?.find((c) => c.id === item.subject);
  const messageKey = conversation?.latest_message || "";
  const message = snapshot.messages?.find((m) => m.conversation_id === item.subject && m.message_key === messageKey);
  const target = findReplyTarget(snapshot,item.subject);
  const original = message?.text || target?.text;
  const attempt = snapshot.attempts.find((a) => a.id === target?.attempt_id);
  const [text, setText] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [focused, setFocused] = useState(false);
  const [resumeConfirmed, setResumeConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const canSend = Boolean(target && focused && original && text.trim() && text.length <= 4000);

  const perform = async (action: () => Promise<unknown>, result: string) => {
    setError(""); setNotice(""); setBusy(true);
    try {
      await action();
      setNotice(result);
      await onChanged();
    } catch (e) { setError(String(e)); }
    finally { setBusy(false); }
  };
  const send = () => {
    if (!canSend || !confirmed || !target) return;
    setConfirmed(false); setFocused(false); setResumeConfirmed(false);
    void perform(async () => {
      await native("inbox_send", { params: {
        run_id: target.run_id, conversation: item.subject, message_key: messageKey,
        text: text.trim(), confirmed: true,
      } });
      setText("");
    }, "回复已交由执行器发送并核验。会话继续保持人工接管，未恢复自动回复。");
  };
  return (
    <section className="source-block">
      <div className="section-title">
        <h3>{attempt ? `${attempt.company} · ${attempt.job}` : item.subject}</h3>
        <span className="badge needs_user">{conversation ? (conversation.status === "human" ? "人工接管" : "等待 AI 处理") : "待处理事项"}</span>
      </div>
      <p>{reasons[item.reason] || item.reason}</p>
      {original ? <><strong>对方最新消息</strong><pre>{original}</pre></> :
        <p className="muted">未取得可核对的最新消息，请在 BOSS 原会话查看。</p>}
      {error && <div className="banner error" role="alert">{error}</div>}
      {notice && <div className="banner" role="status">{notice}</div>}
      <label>
        回复正文 / 处理意见
        <textarea value={text} maxLength={4000} disabled={busy} onChange={(e) => {
          setText(e.target.value); setConfirmed(false); setNotice("");
        }} placeholder="写下要回复的内容，或仅保存给自己的处理意见" />
      </label>
      <div className="actions">
        <Button variant="secondary" disabled={busy || !text.trim()} onClick={() => void perform(
          () => request("attention_update", { id: item.id, action: "note", message_key: messageKey, text }),
          "处理意见已保存，没有发送消息，也没有解除会话锁。",
        )}>保存处理意见（不发送）</Button>
      </div>
      {conversation?.status === "human" && (
        <>
          {!target && <p className="muted">未能唯一绑定正在执行或值守的申请。请在 BOSS 原会话回复；保存意见不会代发。</p>}
          <div className="actions"><Button variant="secondary" disabled={busy || !target} onClick={() => {
            if (!target) return;
            setFocused(false); setConfirmed(false);
            void perform(async () => {
              const result = await native<{ matched: boolean }>("inbox_open", { params: {
                run_id: target.run_id, conversation: item.subject, message_key: messageKey,
              } });
              if (result.matched !== true) throw new Error("会话最新消息未匹配，请刷新后重新核对");
              setFocused(true);
            }, "已打开并核对会话。请复核回复正文，再单独确认发送。");
          }}>打开并核对会话</Button></div>
          <div className="option-row"><label>
            <input type="checkbox" checked={confirmed} disabled={busy || !canSend} onChange={(e) => setConfirmed(e.target.checked)} />
            我已核对最新消息与正文，确认发送这一次回复
          </label></div>
          <div className="actions"><Button disabled={busy || !canSend || !confirmed} onClick={send}>确认发送回复</Button></div>
        </>
      )}
      {conversation && conversation.status !== "human" && <div className="actions">
        <Button variant="secondary" disabled={busy} onClick={() => void perform(
          () => request("attention_update", { id: item.id, action: "handoff", message_key: messageKey, confirmed: true }),
          "已由你接管。请打开并核对会话，再确认发送回复。",
        )}>由我接管</Button>
      </div>}
      {(!conversation || conversation.status === "human") && <details>
        <summary>{conversation ? "人工处理完成后，恢复此会话的自动回复" : "完成此待办"}</summary>
        <p>{conversation ? "发送回复或保存意见都不会解除会话锁。只有确认人工处理完毕后，才恢复后续消息的自动判断。" : "这只会将待办标为已处理，不会发送消息。"}</p>
        <div className="option-row"><label>
          <input type="checkbox" checked={resumeConfirmed} disabled={busy} onChange={(e) => setResumeConfirmed(e.target.checked)} />
          {conversation ? "我已完成人工处理，确认恢复此会话的自动回复" : "我已处理该事项，确认完成"}
        </label></div>
        <div className="actions"><Button variant="secondary" disabled={busy || !resumeConfirmed} onClick={() => {
          setResumeConfirmed(false);
          void perform(() => request("attention_update", {
            id: item.id, action: conversation ? "resume" : "resolve", message_key: messageKey, confirmed: true,
          }), conversation ? "已恢复此会话的自动回复。" : "待办已完成。");
        }}>{conversation ? "确认恢复自动回复" : "确认完成待办"}</Button></div>
      </details>}
    </section>
  );
}
