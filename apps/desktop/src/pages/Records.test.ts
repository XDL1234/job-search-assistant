import { describe, expect, it } from "vitest";
import { recordDateRange, receiptLabel } from "./Records";

describe("投递记录回执", () => {
  it("按已核验的行动结果显示回执，包括无截图的公司网申", () => {
    expect(receiptLabel({ kind: "submit", status: "succeeded" })).toBe("已提交");
    expect(receiptLabel({ kind: "greet", status: "succeeded" })).toBe("已沟通");
    expect(receiptLabel({ kind: "submit", status: "uncertain" })).toBe("待核查");
    expect(receiptLabel({ kind: "submit", status: "executing" })).toBe("暂无明确回执");
    expect(receiptLabel()).toBe("暂无明确回执");
  });
});

describe("投递记录日期", () => {
  it("向分页查询传递本地日期闭区间，最近七天可跨月和跨年", () => {
    const today = new Date(2026, 0, 3, 0, 20);
    expect(recordDateRange({ kind: "week" }, today)).toEqual({ date_from: "2025-12-28", date_to: "2026-01-03" });
    expect(recordDateRange({ kind: "today" }, today)).toEqual({ date_from: "2026-01-03", date_to: "2026-01-03" });
    expect(recordDateRange({ kind: "day", day: "2020-06-12" }, today)).toEqual({ date_from: "2020-06-12", date_to: "2020-06-12" });
    expect(recordDateRange({ kind: "all" }, today)).toEqual({});
  });
});
