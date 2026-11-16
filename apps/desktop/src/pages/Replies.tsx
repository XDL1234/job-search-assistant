import { useEffect, useState } from "react";
import { Button } from "../components/ui/button";
import { request } from "../bridge";
type Rule = {
  id: string;
  category: string;
  reply: string;
  when: Record<string, unknown>;
  enabled: boolean;
};
export function Replies() {
  const [rules, setRules] = useState<Rule[]>([]);
  const [mode, setMode] = useState("human");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    void request<{ rules: Rule[]; unknown_reply_mode: string }>(
      "get_reply_settings",
    )
      .then((value) => {
        setRules(value.rules);
        setMode(value.unknown_reply_mode);
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, []);
  const update = (index: number, change: Partial<Rule>) =>
    setRules((value) =>
      value.map((rule, i) => (i === index ? { ...rule, ...change } : rule)),
    );
  const save = async () => {
    setBusy(true);
    setError("");
    try {
      await request("save_reply_settings", {
        settings: { rules, unknown_reply_mode: mode },
      });
      setNotice("已保存。新建任务会固定本次规则版本，已有任务不受影响。");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="card form-page">
      <div className="section-title">
        <h2>预设回复与人工接管</h2>
        <span className="badge">面试始终转人工</span>
      </div>
      <p>回复保持你填写的原文。只有问题类别和条件完整匹配时才可自动回复。</p>
      {error && (
        <div className="banner error" role="alert">
          {error}
        </div>
      )}
      {notice && (
        <div className="banner" role="status">
          {notice}
        </div>
      )}
      <label>
        未命中预设话术的问题
        <select
          disabled={loading || busy}
          value={mode}
          onChange={(e) => setMode(e.target.value)}
        >
          <option value="human">提醒我，由我回答</option>
          <option value="ai">AI 根据已有资料组织回答；缺失事实仍转人工</option>
        </select>
      </label>
      {rules.map((rule, index) => (
        <section className="source-block" key={rule.id}>
          <div className="form-grid">
            <label>
              问题类别
              <select
                value={rule.category}
                onChange={(e) =>
                  update(index, { category: e.target.value, when: {} })
                }
              >
                <option value="salary">薪资</option>
                <option value="location">工作地点</option>
                <option value="availability">到岗时间</option>
                <option value="other">其他</option>
              </select>
            </label>
            <label>
              启用状态
              <select
                value={String(rule.enabled)}
                onChange={(e) =>
                  update(index, { enabled: e.target.value === "true" })
                }
              >
                <option value="false">关闭</option>
                <option value="true">启用</option>
              </select>
            </label>
          </div>
          <label>
            回复原文
            <textarea
              value={rule.reply}
              onChange={(e) => update(index, { reply: e.target.value })}
            />
          </label>
          {rule.category === "salary" && (
            <label>
              可接受的税前月薪下限（元）
              <input
                type="number"
                min={0}
                value={
                  (rule.when.salary_monthly_gross as { gte?: number })?.gte ??
                  ""
                }
                onChange={(e) =>
                  update(index, {
                    when: e.target.value
                      ? {
                          salary_monthly_gross: { gte: Number(e.target.value) },
                        }
                      : {},
                  })
                }
              />
            </label>
          )}
          {rule.category === "location" && (
            <label>
              可接受的工作城市
              <input
                value={((rule.when.city as { in?: string[] })?.in || []).join(
                  "、",
                )}
                onChange={(e) =>
                  update(index, {
                    when: e.target.value.trim()
                      ? {
                          city: {
                            in: e.target.value
                              .split(/[、,，]/)
                              .map((x) => x.trim()),
                          },
                        }
                      : {},
                  })
                }
                onBlur={(e) => {
                  const cities = e.target.value
                    .split(/[、,，]/)
                    .map((x) => x.trim())
                    .filter(Boolean);
                  update(index, {
                    when: cities.length ? { city: { in: cities } } : {},
                  });
                }}
                placeholder="多个城市用逗号分隔"
              />
            </label>
          )}
          {rule.category === "availability" && (
            <p className="muted">
              只在完整识别为到岗时间问题且没有面试邀约时使用；请按真实情况填写回复。
            </p>
          )}
          <Button
            variant="ghost"
            onClick={() => setRules((v) => v.filter((_, i) => i !== index))}
          >
            移除此规则
          </Button>
        </section>
      ))}
      <div className="actions">
        <Button
          variant="secondary"
          disabled={loading || busy}
          onClick={() =>
            setRules((v) => [
              ...v,
              {
                id: crypto.randomUUID(),
                category: "availability",
                reply: "",
                when: {},
                enabled: false,
              },
            ])
          }
        >
          添加预设话术
        </Button>
        <Button disabled={busy || loading} onClick={() => void save()}>
          保存回复规则
        </Button>
      </div>
      <p className="muted">
        面试邀约、混合问题和新承诺优先转人工。保存规则不发送消息，也不解除会话锁。
      </p>
    </section>
  );
}
