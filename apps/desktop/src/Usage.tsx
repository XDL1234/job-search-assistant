import { useEffect, useState } from "react";
import { native } from "./bridge";

type RunUsage = {
  turns: number;
  images: number;
  tokens: { input: number; cached_input: number; output: number; total: number };
};

export function Usage({ runId }: { runId: string }) {
  const [usage, setUsage] = useState<RunUsage>();
  useEffect(() => {
    let disposed = false;
    setUsage(undefined);
    const update = () => {
      if (!runId) return;
      void native<RunUsage>("ai_usage_status", { runId }).then((value) => {
        if (!disposed) setUsage(value);
      }).catch(() => undefined);
    };
    update();
    const timer = window.setInterval(update, 3000);
    return () => { disposed = true; window.clearInterval(timer); };
  }, [runId]);
  if (!usage) return null;
  return <section className="card" aria-label="本轮 AI 用量">
    <div className="section-title"><h2>本轮 AI 用量</h2><span>已收到的模型用量，JPEG 预览不计入</span></div>
    {usage.turns > 0 && usage.tokens.total === 0 && <p className="text-sm text-slate-500">已发起 AI 回合，尚未收到 token 用量通知。</p>}
    <div className="flex flex-wrap gap-x-8 gap-y-2 text-sm">
      <span>AI 回合 <strong>{usage.turns}</strong></span>
      <span>输入 <strong>{usage.tokens.input.toLocaleString()}</strong></span>
      <span>缓存输入 <strong>{usage.tokens.cached_input.toLocaleString()}</strong></span>
      <span>输出 <strong>{usage.tokens.output.toLocaleString()}</strong></span>
      <span>总 token <strong>{usage.tokens.total.toLocaleString()}</strong></span>
      <span>按需图片 <strong>{usage.images}</strong></span>
    </div>
  </section>;
}
