//! agent-browser 的本机生命周期与只读 JPEG 订阅；媒体不经过 Python worker。
use futures_util::{SinkExt, StreamExt};
use serde::Serialize;
use serde_json::{json, Value};
use std::{
    path::{Path, PathBuf},
    process::Stdio,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc, Mutex as StdMutex,
    },
    time::Duration,
};
use tauri::ipc::Channel;
use tokio::{
    io::AsyncReadExt,
    process::Command,
    sync::{mpsc, oneshot, Mutex},
    task::JoinHandle,
};
use tokio_tungstenite::{
    connect_async_with_config,
    tungstenite::{
        client::IntoClientRequest, http::HeaderValue, protocol::WebSocketConfig, Message,
    },
};

const MAX_MEDIA_BYTES: usize = 2 * 1024 * 1024;
const MAX_CLI_BYTES: u64 = 4 * 1024 * 1024;
const MAX_DIAGNOSTIC_BYTES: u64 = 8 * 1024;

#[derive(Clone, Debug, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum PreviewEvent {
    Frame {
        epoch: String,
        seq: u64,
        data: String,
        metadata: Value,
    },
    Status {
        epoch: String,
        state: String,
        message: String,
    },
}

struct PreviewSession {
    epoch: String,
    ack: mpsc::Sender<u64>,
    stop: oneshot::Sender<()>,
    task: JoinHandle<()>,
}

#[derive(Clone, Default)]
struct PageScope {
    #[cfg(test)]
    origin: Option<String>,
}

impl PageScope {
    fn tabs(&self, tabs: &[Value]) -> Value {
        json!(tabs.iter().filter(|tab| tab["url"].as_str().is_some_and(|url| self.permits(url)))
            .cloned().collect::<Vec<_>>())
    }

    fn permits(&self, url: &str) -> bool {
        if allowed_boss_url(url) {
            return true;
        }
        #[cfg(test)]
        if let Some(origin) = &self.origin {
            return url == origin || url.starts_with(&format!("{origin}/"));
        }
        false
    }
}

pub struct BrowserRuntime {
    pub executable: PathBuf,
    root: PathBuf,
    session: String,
    token: String,
    state: Arc<StdMutex<Value>>,
    started: AtomicBool,
    command_lock: Mutex<()>,
    preview_control: Mutex<()>,
    preview: Mutex<Option<PreviewSession>>,
    scope: PageScope,
}

impl BrowserRuntime {
    pub fn new(resources: &Path, root: &Path) -> Self {
        let repo = std::env::var_os("JOB_ASSISTANT_REPO")
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../.."));
        let installed = resources.join("runtime/agent-browser/agent-browser.exe");
        let executable = std::env::var_os("JOB_ASSISTANT_AGENT_BROWSER")
            .map(PathBuf::from)
            .unwrap_or_else(|| {
                if installed.is_file() || !cfg!(debug_assertions) {
                    installed
                } else {
                    repo.join(".verification/runtime/agent-browser/agent-browser.exe")
                }
            });
        let session = format!("job-assistant-{}", uuid::Uuid::new_v4().simple());
        let token = format!(
            "{}{}",
            uuid::Uuid::new_v4().simple(),
            uuid::Uuid::new_v4().simple()
        );
        let state = json!({"protocol":1,"executable":executable,"session":session,
            "connected":false,"pinned_target":null,"url":null,"connection_mode":"external",
            "preview_state":"stopped"});
        Self {
            executable,
            root: root.to_owned(),
            session,
            token,
            state: Arc::new(StdMutex::new(state)),
            started: AtomicBool::new(false),
            command_lock: Mutex::new(()),
            preview_control: Mutex::new(()),
            preview: Mutex::new(None),
            scope: PageScope::default(),
        }
    }

    // 只供本机子进程环境使用，禁止放入界面状态或模型上下文。
    pub fn command_token(&self) -> String {
        self.token.clone()
    }

    pub fn status(&self) -> Value {
        let mut state = self.state.lock().unwrap_or_else(|e| e.into_inner()).clone();
        state["installed"] = json!(self.executable.is_file());
        state
    }

    fn save_state(&self) -> Result<Value, String> {
        let state = self.status();
        std::fs::create_dir_all(&self.root).map_err(|_| "无法创建浏览器状态目录")?;
        let path = self.root.join("agent-browser.json");
        let temporary = self
            .root
            .join(format!("agent-browser-{}.tmp", uuid::Uuid::new_v4()));
        std::fs::write(
            &temporary,
            serde_json::to_vec(&state).map_err(|_| "浏览器状态无效")?,
        )
        .map_err(|_| "无法保存浏览器状态")?;
        std::fs::rename(&temporary, path).map_err(|_| "无法更新浏览器状态")?;
        Ok(state)
    }

    fn command(&self) -> Command {
        let mut command = Command::new(&self.executable);
        command
            .env_remove("JOB_ASSISTANT_EXISTING_DAEMON")
            .env("JOB_ASSISTANT_EMBEDDED", "1")
            .env("JOB_ASSISTANT_STREAM_TOKEN", &self.token)
            .env("AGENT_BROWSER_STREAM_QUALITY", "65")
            .env("AGENT_BROWSER_STREAM_MAX_WIDTH", "1280")
            .env("AGENT_BROWSER_STREAM_MAX_HEIGHT", "900")
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true);
        #[cfg(windows)]
        command.creation_flags(0x08000000);
        #[cfg(test)]
        command.env("AGENT_BROWSER_SOCKET_DIR", self.root.join("agent-sockets"));
        command
    }

    async fn output(&self, args: &[&str], session: bool) -> Result<Value, String> {
        if !self.executable.is_file() {
            return Err("安装文件缺少 agent-browser 原生运行时".into());
        }
        let mut command = self.command();
        if session {
            command.args(["--session", &self.session]);
            if !args.contains(&"--auto-connect") && !args.contains(&"--cdp") {
                command.env("JOB_ASSISTANT_EXISTING_DAEMON", "1");
            }
            if self.status()["pinned_target"].as_str().is_some() && !args.contains(&"--pin-tab") {
                command.arg("--pin-tab");
            }
        }
        command.args(args);
        let mut child = command.spawn().map_err(|_| "agent-browser 启动失败")?;
        if session {
            self.started.store(true, Ordering::SeqCst);
        }
        let mut stdout = child.stdout.take().ok_or("浏览器运行时没有输出通道")?;
        let mut stderr = child.stderr.take().ok_or("浏览器运行时没有诊断通道")?;
        let mut bytes = Vec::new();
        let mut diagnostic = Vec::new();
        let mut stdout_eof = false;
        let mut stderr_eof = false;
        let operation = async {
            let mut limited_stdout = (&mut stdout).take(MAX_CLI_BYTES + 1);
            let mut limited_stderr = (&mut stderr).take(MAX_DIAGNOSTIC_BYTES + 1);
            let (out, err) = tokio::join!(
                async {
                    let result = limited_stdout.read_to_end(&mut bytes).await;
                    stdout_eof = result.is_ok();
                    result
                },
                async {
                    let result = limited_stderr.read_to_end(&mut diagnostic).await;
                    stderr_eof = result.is_ok();
                    result
                }
            );
            out.map_err(|_| "读取浏览器运行时失败")?;
            err.map_err(|_| "读取浏览器诊断失败")?;
            if bytes.len() as u64 > MAX_CLI_BYTES {
                return Err("浏览器运行时输出超过上限".to_string());
            }
            if diagnostic.len() as u64 > MAX_DIAGNOSTIC_BYTES {
                return Err(self.failure(
                    args,
                    "Runtime diagnostic output exceeded its limit",
                    None,
                ));
            }
            let status = child.wait().await.map_err(|_| "浏览器运行时退出失败")?;
            let value: Value = match serde_json::from_slice(&bytes) {
                Ok(value) => value,
                Err(_) => {
                    let detail = String::from_utf8_lossy(&diagnostic);
                    return Err(self.failure(
                        args,
                        if detail.trim().is_empty() {
                            "Runtime returned invalid JSON"
                        } else {
                            detail.trim()
                        },
                        status.code(),
                    ));
                }
            };
            if !status.success() || value.get("success") == Some(&Value::Bool(false)) {
                let detail = value["error"]
                    .as_str()
                    .or_else(|| value["error"]["message"].as_str())
                    .unwrap_or("Runtime command failed without an error message");
                return Err(self.failure(args, detail, status.code()));
            }
            Ok(value.get("data").cloned().unwrap_or(value))
        };
        match tokio::time::timeout(Duration::from_secs(30), operation).await {
            Ok(result) => result,
            Err(_) => {
                let exit = child.try_wait().ok().flatten();
                let phase = if exit.is_some() {
                    "cli_exited_waiting_for_stdio_eof"
                } else if stdout_eof && stderr_eof {
                    "waiting_for_cli_exit"
                } else {
                    "cli_running_waiting_for_response"
                };
                let detail = format!(
                    "Browser runtime operation timed out at {phase} (stdout_bytes={}, stderr_bytes={}, stdout_eof={stdout_eof}, stderr_eof={stderr_eof}); any pending send requires verification",
                    bytes.len(), diagnostic.len()
                );
                Err(self.failure(args, &detail, exit.and_then(|status| status.code())))
            }
        }
    }

    fn failure(&self, args: &[&str], detail: &str, exit_code: Option<i32>) -> String {
        let operation = if args.contains(&"--auto-connect") || args.contains(&"--cdp") {
            "chrome_connect"
        } else if args.contains(&"--job-assistant-probe") {
            "runtime_probe"
        } else if args.contains(&"--pin-tab") {
            "tab_bind"
        } else if args.first() == Some(&"tab") {
            "tab_list"
        } else if args.first() == Some(&"stream") {
            "preview_status"
        } else if args.first() == Some(&"close") {
            "session_close"
        } else {
            "browser_command"
        };
        let message = redact_diagnostic(detail, &self.token);
        let category = error_category(detail);
        let record = json!({"session":self.session,"operation":operation,"category":category,"message":message,"exit_code":exit_code,
            "recorded_at_ms":std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|time|time.as_millis()).unwrap_or(0)});
        self.state.lock().unwrap_or_else(|e| e.into_inner())["last_error"] = record.clone();
        let path = self.root.join("browser-diagnostic.json");
        if let Ok(bytes) = serde_json::to_vec(&record) {
            let _ = std::fs::write(path, bytes);
        }
        format!("浏览器操作失败 [{operation}/{category}]：{message}")
    }

    async fn verify_runtime(&self) -> Result<(), String> {
        let value = self.output(&["--job-assistant-probe"], false).await?;
        verify_probe(&value)
    }

    pub async fn connect(&self) -> Result<Value, String> {
        self.connect_args(&["--auto-connect", "tab", "list", "--json"])
            .await
    }

    async fn connect_args(&self, args: &[&str]) -> Result<Value, String> {
        self.stop_preview().await;
        let _guard = self.command_lock.lock().await;
        {
            let mut state = self.state.lock().unwrap_or_else(|e| e.into_inner());
            state["connected"] = json!(false);
            state["pinned_target"] = Value::Null;
            state["url"] = Value::Null;
            state["tabs"] = json!([]);
            state["connection_strategy"] = json!(if args.contains(&"--cdp") {
                "active_port"
            } else {
                "auto_connect"
            });
        }
        self.save_state()?;
        self.verify_runtime().await?;
        let result = self.output(args, true).await?;
        let tabs = result
            .get("tabs")
            .and_then(Value::as_array)
            .ok_or("浏览器未返回可核对的标签列表")?;
        {
            let mut state = self.state.lock().unwrap_or_else(|e| e.into_inner());
            state["connected"] = json!(true);
            state["last_error"] = Value::Null;
            state["tabs"] = self.scope.tabs(tabs);
            state["pinned_target"] = Value::Null;
            state["url"] = Value::Null;
        }
        self.save_state()
    }

    pub async fn select_tab(&self, target_id: String) -> Result<Value, String> {
        self.stop_preview().await;
        let _guard = self.command_lock.lock().await;
        if self.status()["connected"] != true {
            return Err("请先连接浏览器".into());
        }
        let result = self.output(&["tab", "list", "--json"], true).await?;
        let selected = result
            .get("tabs")
            .and_then(Value::as_array)
            .and_then(|tabs| tabs.iter().find(|tab| tab["targetId"] == target_id))
            .cloned()
            .ok_or("目标标签已关闭或不属于当前浏览器")?;
        let url = selected["url"].as_str().ok_or("标签缺少网址")?;
        if !self.scope.permits(url) {
            return Err("请选中已登录的 BOSS 标签页".into());
        }
        {
            let mut state = self.state.lock().unwrap_or_else(|e| e.into_inner());
            state["pinned_target"] = Value::Null;
            state["url"] = Value::Null;
        }
        self.save_state()?;
        self.output(&["--pin-tab", "tab", &target_id, "--json"], true)
            .await?;
        let actual = self
            .output(&["--pin-tab", "tab", "list", "--json"], true)
            .await?;
        let actual_tabs = actual["tabs"].as_array().ok_or("浏览器未返回标签列表")?;
        if !actual_tabs.iter().any(|tab| tab["targetId"] == target_id && tab["active"] == true) {
            return Err("浏览器没有实际绑定到所选标签，已停止绑定".into());
        }
        {
            let mut state = self.state.lock().unwrap_or_else(|e| e.into_inner());
            state["pinned_target"] = json!(target_id);
            state["url"] = json!(url);
            state["tabs"] = self.scope.tabs(actual_tabs);
        }
        self.save_state()
    }

    pub async fn start_preview(&self, on_event: Channel<PreviewEvent>) -> Result<Value, String> {
        let _control = self.preview_control.lock().await;
        self.stop_preview_locked().await;
        let target_id = self.status()["pinned_target"]
            .as_str()
            .ok_or("请先选择 BOSS 标签页")?
            .to_owned();
        let stream = {
            let _guard = self.command_lock.lock().await;
            let current = self.output(&["tab", "list", "--json"], true).await?;
            let selected = current["tabs"]
                .as_array()
                .and_then(|tabs| {
                    tabs.iter()
                        .find(|tab| tab["targetId"] == target_id && tab["active"] == true)
                })
                .ok_or("已绑定标签不可用，请重新选择")?;
            if !selected["url"]
                .as_str()
                .is_some_and(|url| self.scope.permits(url))
            {
                return Err("绑定标签已离开授权 BOSS 网站".into());
            }
            self.output(&["stream", "status", "--json"], true).await?
        };
        let port = stream_port(&stream)?;
        let epoch = uuid::Uuid::new_v4().to_string();
        let mut request = format!("ws://127.0.0.1:{port}/?pacing=ack&maxFps=5")
            .into_client_request()
            .map_err(|_| "预览地址无效")?;
        request.headers_mut().insert(
            "Authorization",
            HeaderValue::from_str(&format!("Bearer {}", self.token)).map_err(|_| "预览凭证无效")?,
        );
        let config = WebSocketConfig::default()
            .max_message_size(Some(MAX_MEDIA_BYTES))
            .max_frame_size(Some(MAX_MEDIA_BYTES));
        let (socket, _) = tokio::time::timeout(
            Duration::from_secs(8),
            connect_async_with_config(request, Some(config), false),
        )
        .await
        .map_err(|_| "浏览器预览连接超时")?
        .map_err(|_| "浏览器预览认证或连接失败")?;
        let (mut writer, mut reader) = socket.split();
        let (ack, mut ack_rx) = mpsc::channel::<u64>(1);
        let (stop, mut stop_rx) = oneshot::channel();
        let event_epoch = epoch.clone();
        let state = Arc::clone(&self.state);
        let scope = self.scope.clone();
        let task = tokio::spawn(async move {
            let _ = on_event.send(PreviewEvent::Status {
                epoch: event_epoch.clone(),
                state: "connected".into(),
                message: "实时预览已连接".into(),
            });
            let mut pending = None;
            let mut last_seq = None;
            let mut heartbeat = tokio::time::interval(Duration::from_secs(10));
            let mut last_received = tokio::time::Instant::now();
            let outcome: Result<(), String> = async {
                loop {
                    tokio::select! {
                        _ = &mut stop_rx => break,
                        _ = heartbeat.tick() => {
                            if last_received.elapsed() > Duration::from_secs(30) {
                                return Err("预览心跳超时，请重新连接".into());
                            }
                            writer.send(Message::Ping(Vec::new().into())).await
                                .map_err(|_| "预览心跳发送失败")?;
                        }
                        next_ack = ack_rx.recv() => {
                            let Some(seq) = next_ack else { break; };
                            if pending == Some(seq) {
                                writer.send(Message::Text(json!({"type":"ack","seq":seq}).to_string().into())).await
                                    .map_err(|_| "预览确认发送失败")?;
                                pending = None;
                            }
                        }
                        next = reader.next() => {
                            last_received = tokio::time::Instant::now();
                            match next {
                                Some(Ok(Message::Text(text))) => {
                                    let value: Value = serde_json::from_str(&text).map_err(|_| "预览消息格式无效")?;
                                    if value["type"] == "frame" {
                                        if pending.is_some() { return Err("预览端未遵守单帧确认协议".into()); }
                                        let (seq, data) = validate_frame(&value, last_seq)?;
                                        pending = Some(seq);
                                        last_seq = Some(seq);
                                        on_event.send(PreviewEvent::Frame { epoch: event_epoch.clone(), seq,
                                            data, metadata: value["metadata"].clone() })
                                            .map_err(|_| "预览界面已关闭")?;
                                    } else if value["type"] == "url" {
                                        let url = value["url"].as_str().unwrap_or("");
                                        if !scope.permits(url) { return Err("预览标签已离开授权 BOSS 网站".into()); }
                                    } else if value["type"] == "tabs" {
                                        let tabs = value["tabs"].as_array().ok_or("预览标签列表无效")?;
                                        let selected = tabs.iter().find(|tab| tab["targetId"]==target_id && tab["active"]==true)
                                            .ok_or("绑定的 BOSS 标签已关闭或变化")?;
                                        if !selected["url"].as_str().is_some_and(|url| scope.permits(url)) {
                                            return Err("预览标签已离开授权 BOSS 网站".into());
                                        }
                                    } else if value["type"] == "status" && value["connected"] == false {
                                        return Err("浏览器已断开".into());
                                    }
                                }
                                Some(Ok(Message::Ping(bytes))) => {
                                    writer.send(Message::Pong(bytes)).await.map_err(|_| "预览心跳失败")?;
                                }
                                Some(Ok(Message::Close(_))) | None => break,
                                Some(Err(_)) => return Err("预览连接中断或消息超过上限".into()),
                                _ => {}
                            }
                        }
                    }
                }
                Ok(())
            }.await;
            let _ = writer.close().await;
            state.lock().unwrap_or_else(|e| e.into_inner())["preview_state"] = json!("stopped");
            let _ = on_event.send(PreviewEvent::Status {
                epoch: event_epoch,
                state: if outcome.is_err() {
                    "error".into()
                } else {
                    "stopped".into()
                },
                message: outcome.err().unwrap_or_else(|| "实时预览已停止".into()),
            });
        });
        *self.preview.lock().await = Some(PreviewSession {
            epoch: epoch.clone(),
            ack,
            stop,
            task,
        });
        self.state.lock().unwrap_or_else(|e| e.into_inner())["preview_state"] = json!("connected");
        Ok(json!({"epoch":epoch}))
    }

    pub async fn ack_preview(&self, epoch: &str, seq: u64) -> Result<(), String> {
        let preview = self.preview.lock().await;
        let current = preview.as_ref().ok_or("预览未订阅")?;
        if current.epoch != epoch {
            return Err("预览代际已失效".into());
        }
        current
            .ack
            .try_send(seq)
            .map_err(|_| "预览确认已过期或正在处理".into())
    }

    pub async fn stop_preview(&self) {
        let _control = self.preview_control.lock().await;
        self.stop_preview_locked().await;
    }

    pub async fn stop_preview_if_epoch(&self, epoch: &str) {
        let _control = self.preview_control.lock().await;
        let matches = self
            .preview
            .lock()
            .await
            .as_ref()
            .is_some_and(|preview| preview.epoch == epoch);
        if matches {
            self.stop_preview_locked().await;
        }
    }

    async fn stop_preview_locked(&self) {
        if let Some(previous) = self.preview.lock().await.take() {
            let _ = previous.stop.send(());
            let mut task = previous.task;
            if tokio::time::timeout(Duration::from_secs(2), &mut task)
                .await
                .is_err()
            {
                task.abort();
            }
        }
        self.state.lock().unwrap_or_else(|e| e.into_inner())["preview_state"] = json!("stopped");
    }

    pub async fn shutdown(&self) {
        self.stop_preview().await;
        let _guard = self.command_lock.lock().await;
        if self.started.swap(false, Ordering::SeqCst) {
            let _ = self.output(&["close", "--json"], true).await;
            self.started.store(false, Ordering::SeqCst);
        }
        self.state.lock().unwrap_or_else(|e| e.into_inner())["connected"] = json!(false);
        let _ = self.save_state();
    }
}

#[cfg(all(test, windows))]
#[path = "browser_runtime_live_tests.rs"]
mod live_tests;

fn allowed_boss_url(url: &str) -> bool {
    url == "https://www.zhipin.com" || url.starts_with("https://www.zhipin.com/")
}

fn error_category(detail: &str) -> &'static str {
    let detail = detail.to_ascii_lowercase();
    if detail.contains("waiting for remote-debugging approval")
        || (detail.contains("approve") && detail.contains("chrome"))
    {
        "chrome_approval_pending"
    } else if detail.contains("no running chrome") {
        "chrome_not_found"
    } else if detail.contains("unauthorized")
        || detail.contains("authentication")
        || detail.contains("credential")
    {
        "authentication"
    } else if detail.contains("tab_gone") || detail.contains("target closed") {
        "target_gone"
    } else if detail.contains("timed out") || detail.contains("timeout") {
        "timeout"
    } else if detail.contains("protocol error")
        || detail.contains("network.enable")
        || detail.contains("page.enable")
    {
        "cdp_protocol"
    } else if detail.contains("cannot connect")
        || detail.contains("connection refused")
        || detail.contains("connection reset")
    {
        "connection"
    } else {
        "runtime_error"
    }
}

fn redact_diagnostic(detail: &str, token: &str) -> String {
    let replaced = detail.replace(token, "[凭证已隐藏]");
    let mut words = Vec::new();
    let mut secret_next = false;
    for word in replaced.split_whitespace() {
        let lower = word.to_ascii_lowercase();
        if lower == "bearer" {
            words.push("Bearer".to_owned());
            secret_next = true;
            continue;
        }
        if secret_next {
            words.push("[凭证已隐藏]".into());
            secret_next = false;
            continue;
        }
        if lower == "authorization:"
            || lower == "cookie:"
            || lower == "password:"
            || lower == "token:"
        {
            words.push(word.to_owned());
            secret_next = true;
            continue;
        }
        if word.contains("://")
            || word.contains("/devtools/")
            || word.contains(":\\")
            || word.contains(":/")
        {
            words.push("[地址已隐藏]".into());
            continue;
        }
        if let Some((key, _)) = word.split_once('=') {
            let key_lower = key.to_ascii_lowercase();
            if ["token", "password", "secret", "cookie", "authorization"]
                .iter()
                .any(|part| key_lower.contains(part))
            {
                words.push(format!("{key}=[凭证已隐藏]"));
                continue;
            }
        }
        words.push(word.to_owned());
    }
    words.join(" ").chars().take(512).collect()
}

fn verify_probe(value: &Value) -> Result<(), String> {
    let capabilities = &value["capabilities"];
    if value["version"] != "0.43.0"
        || value["job_assistant_patch"] != 1
        || value["embedded_auth"] != true
        || value["read_only"] != true
        || capabilities["stream_auth"] != true
        || capabilities["stream_read_only"] != true
        || capabilities["stream_http_disabled"] != true
        || capabilities["jpeg_ack_pacing"] != true
        || capabilities["command_auth"] != true
        || capabilities["existing_daemon_only"] != true
    {
        return Err("浏览器运行时版本或认证、只读能力不符合要求".into());
    }
    Ok(())
}

fn stream_port(value: &Value) -> Result<u16, String> {
    if value["enabled"] != true {
        return Err("浏览器未提供实时流服务".into());
    }
    value["port"]
        .as_u64()
        .filter(|port| *port > 0 && *port <= u16::MAX as u64)
        .map(|port| port as u16)
        .ok_or_else(|| "浏览器预览端口无效".into())
}

fn validate_frame(value: &Value, previous: Option<u64>) -> Result<(u64, String), String> {
    let seq = value["seq"].as_u64().ok_or("预览帧缺少序号")?;
    if previous.is_some_and(|last| seq <= last) {
        return Err("预览帧顺序无效".into());
    }
    let data = value["data"].as_str().ok_or("预览帧缺少图片")?;
    if data.len() > MAX_MEDIA_BYTES
        || !data.starts_with("/9j/")
        || !data
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"+/=".contains(&b))
    {
        return Err("预览图片不是有效的受限 JPEG 数据".into());
    }
    Ok((seq, data.to_owned()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn saved_tabs_exclude_unrelated_browsing_metadata() {
        let job = json!({"targetId":"job","url":"https://www.zhipin.com/web/geek/job"});
        let private = json!({"targetId":"other","url":"https://example.com/private"});
        assert_eq!(PageScope::default().tabs(&[job.clone(), private]), json!([job]));
    }

    #[test]
    fn diagnostic_preserves_cause_without_credentials_or_debug_endpoints() {
        let detail="Chrome failed ws://127.0.0.1:9222/devtools/browser/private-id Authorization: Bearer other-secret token=third-secret C:\\Users\\private\\profile own-token";
        let redacted = redact_diagnostic(detail, "own-token");
        assert!(redacted.contains("Chrome failed"));
        for private in [
            "private-id",
            "other-secret",
            "third-secret",
            "private\\profile",
            "own-token",
        ] {
            assert!(!redacted.contains(private));
        }
        assert_eq!(
            error_category("Chrome is waiting for remote-debugging approval on port 9222"),
            "chrome_approval_pending"
        );
        assert_eq!(
            error_category("Protocol error (Network.enable)"),
            "cdp_protocol"
        );
        assert!(
            redact_diagnostic(&"很长的诊断".repeat(1000), "secret")
                .chars()
                .count()
                <= 512
        );
    }

    #[test]
    fn requires_pinned_version_and_all_embedded_capabilities() {
        let mut probe = json!({"version":"0.43.0","job_assistant_patch":1,"embedded_auth":true,"read_only":true,
            "capabilities":{"stream_auth":true,"stream_read_only":true,"stream_http_disabled":true,"jpeg_ack_pacing":true,"command_auth":true,"existing_daemon_only":true}});
        assert!(verify_probe(&probe).is_ok());
        probe["capabilities"]["command_auth"] = Value::Null;
        assert!(verify_probe(&probe).is_err());
        probe["capabilities"]["command_auth"] = json!(true);
        probe["capabilities"]["stream_http_disabled"] = json!(false);
        assert!(verify_probe(&probe).is_err());
        probe["capabilities"]["stream_http_disabled"] = json!(true);
        probe["version"] = json!("0.44.0");
        assert!(verify_probe(&probe).is_err());
    }

    #[test]
    fn validates_stream_port_and_scope() {
        assert_eq!(
            stream_port(&json!({"enabled":true,"port":1234})).unwrap(),
            1234
        );
        assert!(stream_port(&json!({"enabled":true,"port":65536})).is_err());
        assert!(stream_port(&json!({"enabled":false,"port":1234})).is_err());
        assert!(allowed_boss_url("https://www.zhipin.com/web/geek/job"));
        assert!(!allowed_boss_url(
            "https://www.zhipin.com.attacker.invalid/"
        ));
        assert!(!allowed_boss_url(
            "https://www.zhipin.com@attacker.invalid/"
        ));
    }

    #[test]
    fn rejects_old_invalid_or_unbounded_frames() {
        assert!(validate_frame(&json!({"seq":2,"data":"/9j/AA=="}), Some(1)).is_ok());
        assert!(validate_frame(&json!({"seq":2,"data":"/9j/AA=="}), Some(2)).is_err());
        assert!(validate_frame(&json!({"seq":3,"data":"iVBORw0KGgo="}), None).is_err());
        assert!(validate_frame(&json!({"seq":3,"data":"/9j/<script>"}), None).is_err());
        assert!(validate_frame(
            &json!({"seq":3,"data":format!("/9j/{}","A".repeat(MAX_MEDIA_BYTES))}),
            None
        )
        .is_err());
    }

    #[tokio::test]
    async fn stale_unsubscribe_cannot_stop_a_new_subscription() {
        let runtime = BrowserRuntime::new(
            Path::new("missing-resources"),
            Path::new("unused-test-root"),
        );
        let (ack, mut ack_rx) = mpsc::channel(1);
        let (stop, stop_rx) = oneshot::channel();
        let task = tokio::spawn(async {
            let _ = stop_rx.await;
        });
        *runtime.preview.lock().await = Some(PreviewSession {
            epoch: "new".into(),
            ack,
            stop,
            task,
        });
        runtime.stop_preview_if_epoch("old").await;
        assert!(runtime.preview.lock().await.is_some());
        assert!(runtime.ack_preview("old", 42).await.is_err());
        runtime.ack_preview("new", 42).await.unwrap();
        assert_eq!(ack_rx.recv().await, Some(42));
        runtime.stop_preview_if_epoch("new").await;
        assert!(runtime.preview.lock().await.is_none());
    }
}
