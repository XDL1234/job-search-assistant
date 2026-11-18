import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { BossFilterSummary, parsePositionPaths } from "./JobPreferences";

describe("投递岗位信息", () => {
  it("保留含斜杠的职位名称，按行形成独立职位路径", () => {
    expect(parsePositionPaths("电子/电气/通信 > 电子/硬件开发 > 驱动开发工程师\r\n\nC/C++")).toEqual([
      ["电子/电气/通信", "电子/硬件开发", "驱动开发工程师"],
      ["C/C++"],
    ]);
    expect(() => parsePositionPaths("电子/电气/通信 >> 驱动开发工程师")).toThrow();
    expect(() => parsePositionPaths("一级 > 二级 > 三级 > 四级")).toThrow();
  });

  it("在确认区保留明确不限和校招对应的应届生条件", () => {
    const page = renderToStaticMarkup(<BossFilterSummary kind="campus" filters={{
      city: "深圳", salary: "不限", positions: [["电子/硬件开发", "驱动开发工程师"]],
    }} />);
    expect(page).toContain("求职城市：深圳");
    expect(page).toContain("BOSS 求职类型：全职");
    expect(page).toContain("薪资待遇：不限");
    expect(page).toContain("工作经验：应届生");
    expect(page).toContain("电子/硬件开发 ＞ 驱动开发工程师");
  });
});
