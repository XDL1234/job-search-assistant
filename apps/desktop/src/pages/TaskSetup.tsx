import { useEffect, useState } from "react";
import { Button } from "../components/ui/button";
import { native, request } from "../bridge";
import type { TaskConfig } from "../contracts";

export function TaskSetup({
  channel,
  onCreated,
}: {
  channel: "boss" | "web";
  onCreated: (id: string) => void;
}) {
  const [kind, setKind] = useState("internship");
  const [mode, setMode] = useState("script");
  const [roles, setRoles] = useState("");
  const [filters, setFilters] = useState("沿用当前 BOSS 城市和筛选条件");
  const [title, setTitle] = useState(
    channel === "boss" ? "BOSS直聘" : "Google Chrome",
  );
  const [limit, setLimit] = useState(3);
  const [table, setTable] = useState("");
  const [preview, setPreview] = useState<{
    ticket: string;
    config: TaskConfig;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [ready, setReady] = useState(false);
  useEffect(() => {
    void request<{ profile: { target_role?: string } } | null>("get_profile")
      .then((v) => {
        setReady(Boolean(v));
        if (v?.profile.target_role)
          setRoles((current) => current || v.profile.target_role || "");
      })
      .catch((e) => setError(String(e)));
  }, []);
  const perform = async (fn: () => Promise<void>) => {
    setError("");
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };
  const createPreview = () =>
    perform(async () => {
      const config: TaskConfig = {
        channels: [channel],
        employment_type: kind,
        target_roles: roles
          .split(/[、,，\n]/)
          .map((x) => x.trim())
          .filter(Boolean),
        max_contacts: limit,
        boss_mode: mode === "screened" ? "screened" : "filtered",
        execution_mode:
          channel === "web" || mode !== "script" ? "ai" : "script",
        filters,
        window_title: title,
        ...(channel === "web" ? { company_table: table } : {}),
      };
      setPreview(await request("preview_run", { config }));
    });
  return (
    <section className="card form-page">
      <div className="section-title">
        <h2>{channel === "boss" ? "BOSS 投递配置" : "公司官网网申"}</h2>
        <span className="badge">{ready ? "资料就绪" : "请先保存资料"}</span>
      </div>
      <p>
        {channel === "boss"
          ? "复用 Chrome 当前登录。先预览本轮范围，创建后可在工作台开始或暂停。"
          : "读取用户提供的公司表，保留原表。逐家公司填写并截图，真实网站兼容性需分别核验。"}
      </p>
      {error && (
        <div className="banner error" role="alert">
          {error}
        </div>
      )}
      <div className="form-grid">
        <label>
          求职类型
          <select
            value={kind}
            onChange={(e) => {
              setKind(e.target.value);
              if (e.target.value !== "internship")
                setRoles((value) => value.replace(/实习生?/g, ""));
            }}
          >
            <option value="internship">实习</option>
            <option value="campus">校招 / 应届</option>
            <option value="full_time">正式工作 / 社招</option>
          </select>
        </label>
        <label>
          岗位方向
          <input
            value={roles}
            onChange={(e) => setRoles(e.target.value)}
            placeholder="例如：嵌入式软件、MCU、FreeRTOS"
          />
        </label>
        {channel === "boss" && (
          <>
            <label>
              投递模式
              <select
                value={mode}
                onChange={(e) => {
                  setMode(e.target.value);
                  if (e.target.value === "script")
                    setFilters("沿用当前 BOSS 城市和筛选条件");
                }}
              >
                <option value="script">平台筛选 · 脚本快速沟通</option>
                <option value="filtered">平台筛选 · AI 操作</option>
                <option value="screened">AI 精筛 · 阅读岗位详情</option>
              </select>
            </label>
            <label>
              本轮沟通上限
              <input
                type="number"
                min={1}
                max={100}
                value={limit}
                onChange={(e) => setLimit(Number(e.target.value))}
              />
            </label>
          </>
        )}
        <label>
          目标浏览器标题
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="浏览器标签页中可辨认的标题文字"
          />
        </label>
        <label>
          {channel === "boss" && mode === "script"
            ? "平台筛选（请在 BOSS 页面设置）"
            : "筛选条件 / 补充要求"}
          <input
            value={filters}
            readOnly={channel === "boss" && mode === "script"}
            onChange={(e) => setFilters(e.target.value)}
            placeholder="沿用当前筛选，或填写需要核对的条件"
          />
        </label>
      </div>
      {channel === "web" && (
        <label>
          公司表（Excel / CSV）
          <div className="path-input">
            <input
              value={table}
              onChange={(e) => setTable(e.target.value)}
              placeholder="包含公司名称、申请网站"
            />
            <Button
              variant="secondary"
              disabled={busy}
              onClick={() =>
                void perform(async () => {
                  const p = await native<string | null>("pick_file", {
                    kind: "companies",
                  });
                  if (p) setTable(p);
                })
              }
            >
              选择公司表
            </Button>
          </div>
        </label>
      )}
      <div className="banner">
        首次沟通沿用平台话术。面试邀约转人工；缺少事实的回复不会自动承诺。
        {mode === "script" && channel === "boss"
          ? "脚本完成可确定的页面步骤，不调用模型；页面变化时暂停。"
          : ""}
      </div>
      <div className="actions">
        <Button disabled={busy || !ready} onClick={() => void createPreview()}>
          预览本轮任务
        </Button>
      </div>
      {preview && (
        <div className="modal-backdrop">
          <section
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-label="确认投递范围"
          >
            <h2>确认本轮任务</h2>
            <div className="source-block">
              <p>
                类型：
                {
                  (
                    {
                      internship: "实习",
                      campus: "校招",
                      full_time: "正式工作",
                    } as Record<string, string>
                  )[preview.config.employment_type]
                }
              </p>
              <p>岗位：{preview.config.target_roles.join("、")}</p>
              <p>
                方式：
                {channel === "web"
                  ? "官网网申"
                  : mode === "script"
                    ? "脚本快速沟通"
                    : mode === "screened"
                      ? "AI 精筛"
                      : "AI 推荐投递"}
              </p>
              <p>筛选：{preview.config.filters}</p>
              <p>
                沟通上限：
                {channel === "boss" ? preview.config.max_contacts : "按公司表"}
              </p>
              <p>附件：{preview.config.resume_path}</p>
            </div>
            {preview.config.company_rows && (
              <div>
                {preview.config.company_rows.map((row, i) => (
                  <p key={i}>
                    {row.company} · {row.url}
                  </p>
                ))}
              </div>
            )}
            <p>确认后创建暂停状态任务，开始后可能产生真实沟通或提交。</p>
            <div className="actions end">
              <Button
                variant="secondary"
                disabled={busy}
                onClick={() => setPreview(null)}
              >
                返回修改
              </Button>
              <Button
                disabled={busy}
                onClick={() =>
                  void perform(async () => {
                    const result = await request<{ run_id: string }>(
                      "start_run",
                      { ticket: preview.ticket, confirmed: true },
                    );
                    setPreview(null);
                    onCreated(result.run_id);
                  })
                }
              >
                确认范围，创建任务
              </Button>
            </div>
          </section>
        </div>
      )}
    </section>
  );
}
