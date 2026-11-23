import { useState } from "react";
import { Button } from "../components/ui/button";
import type { CompanyQueue, CompanyQueueRow, CompanyQueueState } from "../contracts";

const stateLabels: Record<CompanyQueueState, string> = {
  pending_open: "尚未打开", opened: "待检查", login_required: "待登录",
  locate: "待定位岗位", available: "可填写", filling: "正在填写", manual_required: "需人工处理",
  ready_for_review: "已填写待核对", submitted: "已核验提交", manual_submitted: "用户标记已提交",
  uncertain: "提交待核查", no_match: "未匹配岗位", skipped: "已跳过",
};

const phaseLabels: Record<CompanyQueue["phase"], string> = {
  prepare: "待打开网页", waiting_login: "等待登录或继续", checking: "检查网页",
  filling: "AI 填写中", waiting_review: "等待人工处理", completed: "本轮已完成",
};

export function companyBatchView(queue: CompanyQueue, targetRoles: string[]) {
  const target = targetRoles.join("、");
  const rows = queue.rows.map((row) => ({ ...row, job: row.destination_job || row.job || target }))
    .sort((a, b) => a.source_row - b.source_row);
  const count = (...states: CompanyQueueState[]) => rows.filter((row) => states.includes(row.state)).length;
  const pending = count("pending_open");
  const review = count("ready_for_review");
  const checkable = count("opened", "login_required", "locate", "available", "filling", "manual_required");
  const firstLogin = rows.some((row) => row.state === "opened") &&
    !rows.some((row) => row.state !== "opened" && row.state !== "pending_open");
  const recheckDraft = rows.some((row) => row.state === "pending_open" && row.attempt_id);
  const prepare = queue.phase === "prepare" || (pending > 0 && !checkable && !recheckDraft);
  const finishedFilling = review > 0 && !pending && !checkable;
  return {
    rows, pending, review, checkable, prepare, finishedFilling,
    canContinue: pending > 0 || checkable > 0,
    primary: prepare ? queue.phase === "prepare" ? "打开本批网页" : "打开下一批网页"
      : firstLogin ? "我已登录，开始填写" : "继续检查",
    status: finishedFilling ? "填写结束，待你核对" : phaseLabels[queue.phase],
    available: count("available", "filling"), login: count("login_required"),
    verified: count("submitted"), manual: count("manual_submitted"), uncertain: count("uncertain"),
    fillingRow: queue.phase === "filling" ? rows.find((row) => row.state === "filling") : undefined,
  };
}

export type CompanyRowAction = "company_focus_tab" | "company_mark_submitted" | "company_reopen_review";
export function CompanyQueueList({ rows, busy, canReopen, onAction, onProfile }: {
  rows: CompanyQueueRow[]; busy: boolean; canReopen: boolean;
  onAction?: (action: CompanyRowAction, sourceRow: number) => void;
  onProfile?: () => void;
}) {
  const [confirming, setConfirming] = useState<{ row: number; action: "company_mark_submitted" | "company_reopen_review" }>();
  return <ol className="company-queue" aria-label="公司网申队列">
    {rows.map((row) => {
      const review = row.state === "ready_for_review";
      const manual = row.state === "manual_required";
      const confirmingRow = confirming?.row === row.source_row ? confirming : undefined;
      const focusLabel = row.state === "login_required" || row.state === "opened" ? "前往登录"
        : manual ? "前往处理" : "打开核对";
      return <li key={row.source_row}>
        <div className="company-queue-heading"><strong>{row.company}</strong>
          <span className="badge" data-state={row.state}>{stateLabels[row.state]}</span></div>
        <span className="muted">表格第 {row.source_row} 行{row.job && <> · {row.job}</>}</span>
        {row.reason && <p>{row.reason}</p>}
        {review && !row.tab_available && <p>原标签已关闭或无法连接，网站草稿需要重新核对。</p>}
        {onAction && <div className="company-queue-actions">
          {row.tab_available && ["opened", "login_required", "manual_required", "ready_for_review", "uncertain"].includes(row.state) &&
            <Button variant="secondary" disabled={busy} onClick={() => onAction("company_focus_tab", row.source_row)}>{focusLabel}</Button>}
          {manual && onProfile && <Button variant="ghost" disabled={busy} onClick={onProfile}>补充个人资料</Button>}
          {review && !confirmingRow && <>
            {!row.tab_available && canReopen && <Button variant="secondary" disabled={busy}
              onClick={() => setConfirming({ row: row.source_row, action: "company_reopen_review" })}>重新打开检查</Button>}
            <Button variant="ghost" disabled={busy}
              onClick={() => setConfirming({ row: row.source_row, action: "company_mark_submitted" })}>标记已手动提交</Button>
          </>}
          {review && confirmingRow && <div className="company-queue-confirm" role="group" aria-label={row.company}>
            <p>{confirmingRow.action === "company_mark_submitted" ? "你已经在网站手动提交了这份申请吗？"
              : "将此行放回待检查队列。继续检查时会重新打开网站并核对草稿。"}</p>
            <Button variant="secondary" disabled={busy} onClick={() => setConfirming(undefined)}>取消</Button>
            <Button disabled={busy} onClick={() => { onAction(confirmingRow.action, row.source_row); setConfirming(undefined); }}>
              {confirmingRow.action === "company_mark_submitted" ? "确认已手动提交" : "确认重新检查"}
            </Button>
          </div>}
        </div>}
      </li>;
    })}
  </ol>;
}
