import { expect, test } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { Inbox, findReplyTarget } from "./Inbox";
import type { Snapshot } from "../contracts";

const snapshot = (): Snapshot => ({
  runs: [{ id: "run", status: "watching", created: "", config: { channels: ["boss"], target_roles: [] } }],
  attempts: [], attention: [], fields: [], evidence: [], frame: null,
  conversations: [{ id: "chat", status: "human", latest_message: "latest", reason: "面试邀约" }],
  messages: [{ conversation_id: "chat", message_key: "latest", text: "周五可以面试吗？" }],
  reply_targets: [{ run_id: "run", conversation: "chat", attempt_id: "attempt", message_key: "latest", text: "周五可以面试吗？" }],
});

test("只有人工会话的最新消息和活动任务唯一匹配，才提供发送目标", () => {
  expect(findReplyTarget(snapshot(), "chat")?.attempt_id).toBe("attempt");
  const paused = snapshot(); paused.runs[0].status = "paused";
  expect(findReplyTarget(paused,"chat")).toBeUndefined();
  const unlocked = snapshot(); unlocked.conversations![0].status = "auto";
  expect(findReplyTarget(unlocked,"chat")).toBeUndefined();
});

test("新消息、正文不一致或重复映射都不能沿用旧发送目标", () => {
  const changed = snapshot(); changed.conversations![0].latest_message = "new";
  expect(findReplyTarget(changed,"chat")).toBeUndefined();
  const mismatch = snapshot(); mismatch.messages![0].text = "更新后的原文";
  expect(findReplyTarget(mismatch,"chat")).toBeUndefined();
  const duplicated = snapshot(); duplicated.reply_targets!.push({ ...duplicated.reply_targets![0] });
  expect(findReplyTarget(duplicated,"chat")).toBeUndefined();
});

test("旧版本没有消息映射，或值守条目不完整时保留原会话处理", () => {
  const missing = snapshot(); delete missing.reply_targets; delete missing.conversations;
  expect(findReplyTarget(missing,"chat")).toBeUndefined();
  const partial = snapshot(); partial.reply_targets![0].attempt_id = "";
  expect(findReplyTarget(partial,"chat")).toBeUndefined();
});

test("岗位复核展示已观测内容并同时保留独立的面试分类", () => {
  const value = snapshot();
  value.attention = [
    { id: "interview", subject: "chat", reason: "interview", category: "interview", resolved: 0 },
    { id: "review", subject: "/job_detail/test.html", reason: "岗位精筛待人工查看：请查看面试邀约", category: "review", resolved: 0,
      job_review: { company: "测试公司", job: "测试岗位", city: "深圳", reason: "需确认岗位性质" } },
  ];
  const page = renderToStaticMarkup(<Inbox snapshot={value} onChanged={() => {}} initialCategory="review" />);
  expect(page).toContain("测试岗位");
  expect(page).toContain("深圳");
  expect(page).toContain("需确认岗位性质");
  expect(page).toContain("标记已处理");
  expect(page).not.toContain("预览并确认发送");
});
