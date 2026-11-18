import { useEffect, useState } from "react";
import { CalendarDays, ChevronRight, Database, LockKeyhole, MapPin, MessageSquare, Plus } from "lucide-react";
import { Button } from "../components/ui/button";
import { request } from "../bridge";
import "./WorkspaceSettings.css";

type Rule = {
  id: string;
  category: string;
  reply: string;
  when: Record<string, unknown>;
  enabled: boolean;
};
const categories = {
  availability: { label: "到岗时间", icon: CalendarDays },
  location: { label: "工作地点", icon: MapPin },
  salary: { label: "薪资", icon: Database },
  other: { label: "其他", icon: MessageSquare },
};
type Category = keyof typeof categories;

export function Replies() {
  const [rules, setRules] = useState<Rule[]>([]);
  const [selected, select] = useState("");
  const [mode, setMode] = useState("human");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    let active = true;
    void request<{ rules: Rule[]; unknown_reply_mode: string }>("get_reply_settings")
      .then((value) => {
        if (!active) return;
        setRules(value.rules);
        setMode(value.unknown_reply_mode);
        setLoaded(true);
      })
      .catch((failure) => { if (active) setError(String(failure)); });
    return () => { active = false; };
  }, []);
  const rule = rules.find((entry) => entry.id === selected) || rules[0];
  const update = (change: Partial<Rule>) => {
    setNotice("");
    setRules((value) => value.map((entry) => entry.id === rule?.id ? { ...entry, ...change } : entry));
  };
  const add = () => {
    const id = crypto.randomUUID();
    setRules((value) => [...value, { id, category: "availability", reply: "", when: {}, enabled: false }]);
    select(id);
    setNotice("");
  };
  const save = async () => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await request("save_reply_settings", { settings: { rules, unknown_reply_mode: mode } });
      setNotice("已保存。新建任务会采用这份规则。");
    } catch (failure) { setError(String(failure)); }
    finally { setBusy(false); }
  };

  return (
    <section className="settings-rules" aria-label="回复规则">
      {error && <div className="banner error" role="alert">{error}</div>}
      {notice && <div className="banner" role="status">{notice}</div>}
      <fieldset className="settings-unknown" disabled={!loaded || busy}>
        <strong>未命中规则时</strong>
        {[["human", "提醒我回答"], ["ai", "AI 根据资料拟答"]].map(([key, text]) =>
          <label key={key}><input type="radio" name="unknown-reply-mode" value={key} checked={mode === key}
            onChange={() => { setMode(key); setNotice(""); }} />{text}</label>)}
        {mode === "ai" && <span className="muted">符合发送条件时可使用已核实的资料回复</span>}
      </fieldset>
      <div className="settings-rules-split">
        <section className="settings-rule-list">
          <header><h2>预设话术</h2><Button variant="secondary" disabled={!loaded || busy} onClick={add}><Plus size={17} />添加预设话术</Button></header>
          <div>
            {rules.map((entry) => {
              const category = categories[entry.category as Category] ?? categories.other;
              return <button type="button" className="settings-rule-entry" key={entry.id}
                aria-pressed={rule?.id === entry.id} onClick={() => select(entry.id)}>
                <category.icon size={20} /><strong>{category.label}</strong>
                <span className={`workspace-tag ${entry.enabled ? "success" : "warning"}`}>
                  {entry.enabled ? "已配置" : "草稿"}
                </span>
                <ChevronRight size={16} />
              </button>;
            })}
            {!rules.length && <div className="empty"><MessageSquare /><strong>{loaded ? "暂无预设话术" : error ? "暂时无法读取预设话术" : "正在读取规则"}</strong></div>}
          </div>
        </section>
        <section className="settings-rule-editor">
          <h2>编辑话术</h2>
          {rule ? (
            <fieldset disabled={!loaded || busy}>
              <label>匹配条件
                <select aria-label="问题类别" value={rule.category}
                  onChange={(event) => update({ category: event.target.value, when: {} })}>
                  {Object.entries(categories).map(([key, category]) => <option key={key} value={key}>{category.label}</option>)}
                </select>
              </label>
              {rule.category === "salary" && <label>可接受的税前月薪下限（元）
                <input type="number" min={0} value={(rule.when.salary_monthly_gross as { gte?: number })?.gte ?? ""}
                  onChange={(event) => update({ when: event.target.value ? { salary_monthly_gross: { gte: Number(event.target.value) } } : {} })} />
              </label>}
              {rule.category === "location" && <label>可接受的工作城市
                <input value={(rule.when.city as { in?: string[] })?.in?.join("、") || ""}
                  onChange={(event) => update({ when: { city: { in: event.target.value.split(/[、,，]/) } } })}
                  onBlur={(event) => {
                    const cities = event.target.value.split(/[、,，]/).map((value) => value.trim()).filter(Boolean);
                    update({ when: cities.length ? { city: { in: cities } } : {} });
                  }} placeholder="多个城市用逗号分隔" />
              </label>}
              <label>回复原文<textarea rows={5} value={rule.reply} onChange={(event) => update({ reply: event.target.value })} /></label>
              <div className="settings-rule-controls">
                <label>启用状态
                  <select value={String(rule.enabled)} onChange={(event) => update({ enabled: event.target.value === "true" })}>
                    <option value="false">关闭</option><option value="true">启用</option>
                  </select>
                </label>
                <Button variant="ghost" onClick={() => {
                  setRules((value) => value.filter((entry) => entry.id !== rule.id));
                  select("");
                  setNotice("");
                }}>移除此规则</Button>
              </div>
            </fieldset>
          ) : <p className="muted">添加话术后可编辑匹配条件和回复原文。</p>}
          <div className="settings-interview-lock"><LockKeyhole size={19} />面试邀约始终转人工</div>
          <Button disabled={!loaded || busy} onClick={() => void save()}>保存回复规则</Button>
        </section>
      </div>
    </section>
  );
}
