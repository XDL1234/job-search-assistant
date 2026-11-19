import { describe, expect, it } from "vitest";
import { fillFromResume, type Extraction, type ProfileData } from "./profile-draft";

const fields = ["name", "contact.email", "contact.phone"];
const groups = [
  { key: "education", fields: ["school", "degree", "major", "start_date"].map((key) => ({ key })) },
  { key: "projects", fields: ["name", "description", "start_date"].map((key) => ({ key })) },
];

function extract(profile: ProfileData, sources: Extraction["sources"] = {}): Extraction {
  return { source_file: "sample.pdf", source_sha256: "fixture", profile, sources };
}

describe("从简历补齐个人信息草稿", () => {
  it("保留已有姓名和主动清空的手机号，仅引用来自简历的邮箱", () => {
    const current = { name: "已保存姓名", contact: { phone: "", email: "" } };
    const parsed = extract({ name: "简历姓名", contact: { email: "test@example.invalid", phone: "10000000000" } },
      { name: { locator: "第 1 页" }, "contact.email": { locator: "第 2 页" }, "contact.phone": { locator: "第 3 页" } });
    const changed = fillFromResume(current, parsed, fields, groups, new Set(["contact.phone"]), {});
    expect(changed?.profile).toEqual({ name: "已保存姓名", contact: { email: "test@example.invalid", phone: "" } });
    expect(changed?.extraction.sources).toEqual({ "contact.email": { locator: "第 2 页" } });
  });

  it("为旧版单条教育经历补齐专业，保留旧项目文本并对重复提取去重", () => {
    const current = { education: { school_name: "示例大学", degree: "本科", start_date: "2024年6月" },
      projects: ["控制项目：原有描述"] };
    const parsed = extract({ education: [{ school: "示例大学", degree: "本科", major: "电子工程", start_date: "2024-06" }],
      projects: [{ name: "控制项目", description: "简历描述" }, { name: "新的项目", start_date: "2026-01" }] });
    const changed = fillFromResume(current, parsed, fields, groups, new Set(), {});
    expect(changed?.profile).toEqual({
      education: [{ school_name: "示例大学", school: "示例大学", degree: "本科", major: "电子工程", start_date: "2024年6月" }],
      projects: ["控制项目：原有描述", { name: "新的项目", start_date: "2026-01" }],
    });
    expect(fillFromResume(changed!.profile, parsed, fields, groups, new Set(), {})).toBeUndefined();
  });

  it("不恢复用户移除的同一条记录，可以追加同公司不同时间的经历", () => {
    const work = [{ key: "work_experience", fields: ["company", "role", "start_date"].map((key) => ({ key })), ongoing: true }];
    const removed = { company: "示例公司", role: "开发", start_date: "2024-09" };
    const later = { ...removed, start_date: "2026-09" };
    const changed = fillFromResume({}, extract({ work_experience: [removed, later] }), [], work,
      new Set(), { work_experience: [removed] });
    expect(changed?.profile.work_experience).toEqual([later]);
  });

  it("只传递可审核的变更并支持至今标记", () => {
    const internships = [{ key: "internships", fields: ["company", "role", "start_date"].map((key) => ({ key })), ongoing: true }];
    const parsed = extract({ name: {}, resume_text: "原始简历文本", internships: [{ company: "示例组织", current: true, role: [] }] });
    const result = fillFromResume({}, parsed, fields, internships, new Set(), {});
    expect(result?.profile).toEqual({ internships: [{ company: "示例组织", current: true }] });
    expect(result?.extraction.profile).not.toHaveProperty("resume_text");
  });

  it("改名后重填同一份简历时不追加旧项目，另一份简历的新项目正常追加", () => {
    const parsed = extract({ projects: [{ name: "简历原项目" }] },
      { projects: { locator: "段落 1", original: "项目名称：简历原项目" } });
    const draft = { projects: [{ name: "用户改过的项目名" }] };
    expect(fillFromResume(draft, parsed, fields, groups, new Set(), {}, new Set(["projects"]))).toBeUndefined();

    const saved = { ...draft, sources: parsed.sources };
    expect(fillFromResume(saved, parsed, fields, groups, new Set(), {})).toBeUndefined();

    const newer = extract({ projects: [{ name: "另一份简历的新项目" }] },
      { projects: { locator: "段落 1", original: "项目名称：另一份简历的新项目" } });
    expect(fillFromResume(saved, newer, fields, groups, new Set(), {})?.profile.projects)
      .toEqual([...draft.projects, { name: "另一份简历的新项目" }]);
  });
});
