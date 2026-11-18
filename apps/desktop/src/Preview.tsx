import { Channel } from "@tauri-apps/api/core";
import { useEffect, useRef, useState } from "react";
import { native } from "./bridge";

export type PreviewEvent =
  | { type: "frame"; epoch: string; seq: number; data: string; metadata: Record<string, unknown> }
  | { type: "status"; epoch: string; state: string; message: string };

const maxFrameCharacters = 2 * 1024 * 1024;

export function previewAgeSeconds(timestamp: number | undefined, now: number): number | undefined {
  if (timestamp === undefined || !Number.isFinite(timestamp) || timestamp <= 0 || timestamp > 8.64e15) return;
  return Math.max(0, Math.floor((now - timestamp) / 1000));
}

export function previewFrameUri(
  frame: Extract<PreviewEvent, { type: "frame" }>,
  epoch: string,
  previous: number,
): string | undefined {
  if (frame.epoch !== epoch || !Number.isSafeInteger(frame.seq) || frame.seq <= previous ||
      frame.data.length > maxFrameCharacters || !/^\/9j\/[A-Za-z0-9+/=]+$/.test(frame.data)) return;
  return `data:image/jpeg;base64,${frame.data}`;
}

export function Preview({ enabled = true }: { enabled?: boolean }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [state, setState] = useState("connecting");
  const [message, setMessage] = useState("正在连接实时画面");
  const [capturedAt, setCapturedAt] = useState<number>();
  const [receivedAt, setReceivedAt] = useState<number>();
  const [now, setNow] = useState(Date.now);

  useEffect(() => {
    if (!enabled) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [enabled]);

  useEffect(() => {
    const previousSurface = canvas.current;
    previousSurface?.getContext("2d")?.clearRect(0, 0, previousSurface.width, previousSurface.height);
    setCapturedAt(undefined);
    setReceivedAt(undefined);
    if (!enabled) {
      setState("stopped");
      setMessage("实时预览未开启");
      return;
    }
    let disposed = false;
    let epoch: string | undefined;
    let previous = -1;
    const channel = new Channel<PreviewEvent>();
    const stop = (identity: string) =>
      native("browser_preview_stop", { epoch: identity }).catch(() => undefined);
    const acknowledge = (identity: string, seq: number) =>
      native("browser_preview_ack", { epoch: identity, seq }).catch((error) => {
        if (!disposed) {
          setState("error");
          setMessage(String(error));
          void stop(identity);
        }
      });
    channel.onmessage = (event) => {
      if (disposed) return;
      if (!epoch && event.type === "status" && event.state === "connected") epoch = event.epoch;
      if (!epoch || event.epoch !== epoch) return;
      if (event.type === "status") {
        setState(event.state);
        setMessage(event.message);
        return;
      }
      const source = previewFrameUri(event, epoch, previous);
      if (!source) {
        // 上游按帧等待确认；错误不能把该订阅永久挂住。
        setState("error");
        setMessage("实时画面数据无效，请重新连接");
        void stop(epoch);
        return;
      }
      previous = event.seq;
      const identity = epoch;
      const image = new Image();
      image.src = source;
      void image.decode().then(() => {
        if (disposed || identity !== epoch) return;
        const surface = canvas.current;
        const context = surface?.getContext("2d");
        if (!surface || !context) {
          void stop(identity);
          return;
        }
        if (surface.width !== image.naturalWidth) surface.width = image.naturalWidth;
        if (surface.height !== image.naturalHeight) surface.height = image.naturalHeight;
        context.drawImage(image, 0, 0);
        setState("connected");
        const received = Date.now();
        setReceivedAt(received);
        setNow(received);
        const timestamp = event.metadata.timestamp;
        if (typeof timestamp === "number" && Number.isFinite(timestamp) && timestamp > 0 && timestamp <= 8.64e15)
          setCapturedAt(timestamp);
        void acknowledge(identity, event.seq);
      }).catch(() => {
        if (!disposed) {
          setState("error");
          setMessage("实时图片解码失败，请重新连接");
          void stop(identity);
        }
      });
    };
    setState("connecting");
    setMessage("正在连接实时画面");
    void native<{ epoch: string }>("browser_preview_start", { onEvent: channel }).then((result) => {
      if (disposed) { void stop(result.epoch); return; }
      if (epoch && epoch !== result.epoch) {
        setState("error");
        setMessage("实时预览代际不一致");
        void stop(result.epoch);
        return;
      }
      epoch = result.epoch;
    }).catch((error) => {
      if (!disposed) { setState("error"); setMessage(String(error)); }
    });
    return () => {
      disposed = true;
      if (epoch) void stop(epoch);
    };
  }, [enabled]);

  const displayedAt = capturedAt ?? receivedAt;
  const age = previewAgeSeconds(displayedAt, now);
  const connectionText = state === "connected"
    ? receivedAt ? "预览连接正常" : "已连接，等待首帧"
    : message;

  return <div className="browser-preview flex flex-col overflow-hidden rounded-xl bg-slate-950">
    <div className="flex min-h-0 flex-1 items-center justify-center overflow-hidden p-2">
      <canvas ref={canvas} aria-label="BOSS 实时画面" role="img"
        className="h-full w-full object-contain" />
    </div>
    <div className="flex flex-wrap items-center justify-between gap-3 px-3 py-2 text-xs text-slate-200">
      <span role="status" className={state === "error" ? "text-amber-300" : undefined}>{connectionText}</span>
      {displayedAt && <time aria-live="off" dateTime={new Date(displayedAt).toISOString()}>
        最近画面{capturedAt ? "采集" : "接收"}：{new Date(displayedAt).toLocaleTimeString("zh-CN", { hour12: false })}
        {age !== undefined && `（${age} 秒前）`}
      </time>}
    </div>
    <p className="browser-preview-hint border-t border-slate-800 px-3 py-2 text-xs leading-relaxed text-slate-400">
      建议将 BOSS 放在独立窗口并保持为当前标签。窗口可在后台；切换标签或最小化可能暂停画面更新。静态页面没有新帧时，连接仍可正常。
    </p>
  </div>;
}
