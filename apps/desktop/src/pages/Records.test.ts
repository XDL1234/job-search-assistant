import { describe, expect, it } from "vitest";
import { attemptDay, matchesRecordDay } from "./Records";

describe("投递记录日期", () => {
  it("按本机日期查找投递尝试，旧记录用更新时间补齐", () => {
    const started = new Date(2026, 5, 12, 0, 20).toISOString();
    const updated = new Date(2026, 5, 13, 0, 20).toISOString();
    expect(attemptDay({ started, updated })).toBe("2026-06-12");
    expect(attemptDay({ started: null, updated })).toBe("2026-06-13");
    expect(attemptDay({ started: "失效日期", updated })).toBe("2026-06-13");
  });

  it("最近七天包括今天并能正确跨月", () => {
    const today = new Date(2026, 0, 3, 17);
    const week = { kind: "week" } as const;
    expect(matchesRecordDay("2025-12-27", week, today)).toBe(false);
    expect(matchesRecordDay("2025-12-28", week, today)).toBe(true);
    expect(matchesRecordDay("2026-01-03", week, today)).toBe(true);
    expect(matchesRecordDay("2026-01-04", week, today)).toBe(false);
    expect(matchesRecordDay("", { kind: "all" }, today)).toBe(true);
    expect(matchesRecordDay("", { kind: "today" }, today)).toBe(false);
  });
});
