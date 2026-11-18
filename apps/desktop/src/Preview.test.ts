import { expect, test } from "vitest";
import { previewAgeSeconds, previewFrameUri, type PreviewEvent } from "./Preview";

const frame = (changes: Partial<Extract<PreviewEvent, { type: "frame" }>> = {}) => ({
  type: "frame" as const, epoch: "current", seq: 42, data: "/9j/AA==", metadata: {}, ...changes,
});

test("只显示当前预览代际中尚未显示的帧", () => {
  expect(previewFrameUri(frame(), "current", 41)).toBe("data:image/jpeg;base64,/9j/AA==");
  expect(previewFrameUri(frame({ epoch: "old" }), "current", 41)).toBeUndefined();
  expect(previewFrameUri(frame(), "current", 42)).toBeUndefined();
  expect(previewFrameUri(frame({ seq: 1.5 }), "current", 1)).toBeUndefined();
});

test("拒绝错误图片类型、异常编码及过大的媒体消息", () => {
  expect(previewFrameUri(frame({ data: "iVBORw0KGgo=" }), "current", 0)).toBeUndefined();
  expect(previewFrameUri(frame({ data: "/9j/<script>" }), "current", 0)).toBeUndefined();
  expect(previewFrameUri(frame({ data: `/9j/${"A".repeat(2 * 1024 * 1024)}` }), "current", 0)).toBeUndefined();
});

test("最近画面时间随本地时钟变老，不把静止画面伪装成新帧", () => {
  expect(previewAgeSeconds(10_000, 25_900)).toBe(15);
  expect(previewAgeSeconds(10_000, 45_900)).toBe(35);
  expect(previewAgeSeconds(11_000, 10_000)).toBe(0);
  expect(previewAgeSeconds(undefined, 10_000)).toBeUndefined();
  expect(previewAgeSeconds(Number.POSITIVE_INFINITY, 10_000)).toBeUndefined();
  expect(previewAgeSeconds(8.65e15, 10_000)).toBeUndefined();
});
