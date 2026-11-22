//! App Server 连接：响应、通知与服务端工具请求分别处理。
use serde_json::{json, Value};
use std::{
    collections::HashMap,
    path::Path,
    sync::{Arc, Mutex},
    time::Duration,
};
use tokio::{
    io::{AsyncBufReadExt, AsyncWriteExt, BufReader},
    process::{Child, ChildStdin, Command},
    sync::{mpsc, oneshot, Mutex as AsyncMutex},
};

pub enum MessageKind {
    Response(u64, Value),
    Notification(Value),
    ServerRequest(Value),
}

#[derive(Default)]
pub struct ConnectionEpoch(std::sync::atomic::AtomicU64);
impl ConnectionEpoch {
    pub fn advance(&self) -> u64 {
        self.0.fetch_add(1, std::sync::atomic::Ordering::SeqCst) + 1
    }
    pub fn accepts(&self, source: u64) -> bool {
        self.0.load(std::sync::atomic::Ordering::SeqCst) == source
    }
}

/// 回合结束与执行成功是两个概念；保留失败原因供界面和重启后读取。
pub fn turn_failure(turn: &Value) -> Option<String> {
    if turn["status"] != "failed" {
        return None;
    }
    if turn["error"]["codexErrorInfo"] == "usageLimitExceeded" {
        return Some(
            "Codex 账号额度已用尽，任务已暂停。请在账号用量页面查看恢复时间，额度恢复后手动继续。"
                .into(),
        );
    }
    let message = turn["error"]["message"].as_str().unwrap_or("Codex 执行失败，任务已暂停");
    if serde_json::from_str::<Value>(message).is_ok_and(|body| body["error"]["code"] == "model_not_found") {
        return Some("ChatGPT 账号暂时无法使用 Codex 返回的模型，任务已暂停。请重新连接 Codex 后继续。".into());
    }
    Some(message.to_owned())
}

fn account_model(models: &[Value], needs_image: bool) -> Option<&Value> {
    let mut visible = models.iter().filter(|model| {
        let modalities = model["inputModalities"].as_array();
        model["hidden"] == false
            && model["model"].as_str().is_some_and(|slug| !slug.is_empty())
            && modalities.is_some_and(|items| {
                items.contains(&json!("text")) && (!needs_image || items.contains(&json!("image")))
            })
    });
    let first = visible.next()?;
    if first["isDefault"] == true { Some(first) }
    else { visible.find(|model| model["isDefault"] == true).or(Some(first)) }
}
pub fn decode_message(line: &str) -> Result<MessageKind, String> {
    let value: Value = serde_json::from_str(line).map_err(|e| e.to_string())?;
    if value.get("method").is_some() {
        return Ok(if value.get("id").is_some() {
            MessageKind::ServerRequest(value)
        } else {
            MessageKind::Notification(value)
        });
    }
    let id = value["id"].as_u64().ok_or("响应编号无效")?;
    Ok(MessageKind::Response(id, value))
}

#[derive(Default, Debug)]
pub struct TurnGate {
    pub turn_id: Option<String>,
    pub busy: bool,
    cancelled: bool,
}
impl TurnGate {
    pub fn begin(&mut self) -> Result<(), String> {
        if self.busy {
            return Err("当前回合尚未结束".into());
        }
        self.busy = true;
        self.cancelled = false;
        self.turn_id = None;
        Ok(())
    }
    pub fn cancel(&mut self) {
        self.cancelled = true;
    }
    pub fn is_cancelled(&self) -> bool {
        self.cancelled
    }
    pub fn started(&mut self, id: String) -> bool {
        self.turn_id = Some(id);
        self.cancelled
    }
    pub fn can_act(&self) -> bool {
        self.busy && !self.cancelled && self.turn_id.is_some()
    }
    pub fn finished(&mut self) {
        self.busy = false;
        self.turn_id = None;
    }
    pub fn finish_for_watch(&mut self) -> bool {
        if !self.busy || self.cancelled {
            self.finished();
            return false;
        }
        self.turn_id = None;
        true
    }
}

type Pending = Arc<Mutex<HashMap<u64, oneshot::Sender<Value>>>>;
pub struct CodexClient {
    writer: AsyncMutex<ChildStdin>,
    child: AsyncMutex<Child>,
    pending: Pending,
    serial: std::sync::atomic::AtomicU64,
}

impl CodexClient {
    pub async fn spawn(
        exe: &Path,
        cwd: &Path,
        events: mpsc::UnboundedSender<Value>,
        proxy_environment: &HashMap<String, String>,
        existing_home: Option<&Path>,
    ) -> Result<Arc<Self>, String> {
        let mut command = Command::new(exe);
        command
            .args(["app-server", "--listen", "stdio://"])
            .current_dir(cwd)
            .envs(proxy_environment)
            .env("CODEX_HOME", cwd.join("codex"))
            .env_remove("CODEX_SQLITE_HOME")
            .env("TEMP", cwd.join("tmp"))
            .env("TMP", cwd.join("tmp"));
        if let Some(home) = existing_home {
            // 用户主动选择后由 Codex 读取自己的登录，运行日志与数据库留在求职助手。
            command.env("CODEX_HOME", home).env("CODEX_SQLITE_HOME", cwd.join("codex"))
                .env_remove("OPENAI_BASE_URL")
                .arg("-c").arg(format!("sqlite_home={}", json!(cwd.join("codex"))))
                .arg("-c").arg(format!("log_dir={}", json!(cwd.join("codex/log"))))
                .args(["-c", "history.persistence=\"none\"", "-c", "model_provider=\"openai\"",
                    "-c", "chatgpt_base_url=\"https://chatgpt.com/backend-api/\"",
                    "-c", "analytics.enabled=false", "-c", "features.hooks=false"]);
        }
        Self::spawn_command(command, events).await
    }

    pub async fn spawn_command(
        mut command: Command,
        events: mpsc::UnboundedSender<Value>,
    ) -> Result<Arc<Self>, String> {
        command
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::null())
            .kill_on_drop(true);
        #[cfg(windows)]
        command.creation_flags(0x08000000);
        let mut child = command
            .spawn()
            .map_err(|e| format!("无法启动 Codex：{e}"))?;
        let writer = child.stdin.take().ok_or("Codex 输入不可用")?;
        let stdout = child.stdout.take().ok_or("Codex 输出不可用")?;
        let pending: Pending = Arc::new(Mutex::new(HashMap::new()));
        let reader_pending = pending.clone();
        tokio::spawn(async move {
            let mut lines = BufReader::new(stdout).lines();
            while let Ok(Some(line)) = lines.next_line().await {
                match decode_message(&line) {
                    Ok(MessageKind::Response(id, value)) => {
                        if let Some(sender) = reader_pending.lock().unwrap().remove(&id) {
                            let _ = sender.send(value);
                        }
                    }
                    Ok(MessageKind::Notification(value) | MessageKind::ServerRequest(value)) => {
                        let _ = events.send(value);
                    }
                    Err(_) => {
                        let _ = events.send(json!({"method":"client/protocolError"}));
                    }
                }
            }
            reader_pending.lock().unwrap().clear();
            let _ = events.send(json!({"method":"client/disconnected"}));
        });
        let client = Arc::new(Self {
            writer: AsyncMutex::new(writer),
            child: AsyncMutex::new(child),
            pending,
            serial: 1.into(),
        });
        client.request("initialize", json!({"clientInfo":{"name":"job_search_desktop","title":"求职助手","version":env!("CARGO_PKG_VERSION")},"capabilities":{"experimentalApi":true}})).await?;
        client.send(json!({"method":"initialized"})).await?;
        Ok(client)
    }
    pub async fn send(&self, value: Value) -> Result<(), String> {
        let mut writer = self.writer.lock().await;
        writer
            .write_all(format!("{value}\n").as_bytes())
            .await
            .map_err(|e| e.to_string())?;
        writer.flush().await.map_err(|e| e.to_string())
    }
    pub async fn request(&self, method: &str, params: Value) -> Result<Value, String> {
        self.request_with_timeout(method, params, Duration::from_secs(60))
            .await
    }

    pub(crate) async fn request_thread(
        &self, method: &str, mut params: Value, effective: &Value, needs_image: bool,
    ) -> Result<Value, String> {
        let account = self.request_with_timeout("account/read", json!({"refreshToken":false}), Duration::from_secs(5)).await?;
        if account["account"]["type"] == "chatgpt" {
            let mut models = Vec::new();
            let mut cursor = Value::Null;
            for _ in 0..5 {
                let page = self.request_with_timeout("model/list", json!({"limit":100,"includeHidden":false,"cursor":cursor}), Duration::from_secs(10))
                    .await.map_err(|error| format!("无法读取 ChatGPT 账号的 Codex 模型目录：{error}"))?;
                models.extend(page["data"].as_array().ok_or("Codex 模型目录格式无效")?.iter().cloned());
                cursor = page["nextCursor"].clone();
                if cursor.is_null() { break; }
            }
            // 复用账号可能继承第三方模型、推理级别和加速配置，以当前 ChatGPT 模型目录为准。
            let model = account_model(&models, needs_image).ok_or("当前 ChatGPT 账号的 Codex 模型目录中没有可用于此任务的模型")?;
            params["model"] = model["model"].clone();
            if !model["defaultReasoningEffort"].is_null() {
                params["config"]["model_reasoning_effort"] = model["defaultReasoningEffort"].clone();
            }
            let tier = &effective["config"]["service_tier"];
            if tier.is_string() && !model["serviceTiers"].as_array().is_some_and(|tiers| tiers.iter().any(|item| &item["id"] == tier)) {
                params["serviceTier"] = json!("default");
            }
        } else if account["account"].is_null() {
            return Err("Codex 授权已失效，请重新连接".into());
        }
        self.request(method, params).await
    }
    pub async fn request_with_timeout(
        &self,
        method: &str,
        params: Value,
        duration: Duration,
    ) -> Result<Value, String> {
        let id = self
            .serial
            .fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        let (sender, receiver) = oneshot::channel();
        self.pending.lock().unwrap().insert(id, sender);
        if let Err(error) = self
            .send(json!({"id":id,"method":method,"params":params}))
            .await
        {
            self.pending.lock().unwrap().remove(&id);
            return Err(error);
        }
        let result = tokio::time::timeout(duration, receiver).await;
        self.pending.lock().unwrap().remove(&id);
        let value = result
            .map_err(|_| format!("Codex 请求超时：{method}"))?
            .map_err(|_| "Codex 连接已关闭")?;
        if let Some(error) = value.get("error") {
            return Err(error["message"].as_str().unwrap_or("Codex 请求失败").into());
        }
        value
            .get("result")
            .cloned()
            .ok_or_else(|| "Codex 响应缺少结果".into())
    }
    pub async fn close(&self) {
        let _ = self.child.lock().await.kill().await;
    }
}

#[cfg(test)]
mod model_tests {
    use super::*;

    #[tokio::test]
    async fn chatgpt_threads_use_the_account_catalog_instead_of_inherited_models() {
        let mut command = Command::new(std::env::var("JOB_ASSISTANT_PYTHON").unwrap_or("python".into()));
        command.arg(concat!(env!("CARGO_MANIFEST_DIR"), "/tests/fake_codex.py"));
        let (sender, _events) = mpsc::unbounded_channel();
        let client = CodexClient::spawn_command(command, sender).await.unwrap();
        let effective = json!({"config":{
            "model":"gpt-6.2-sol","model_reasoning_effort":"ultra","service_tier":"priority"
        }});
        let initial = json!({"config":{"features":{"shell_tool":false}}});
        let text = client.request_thread("thread/start", initial.clone(), &effective, false).await.unwrap();
        assert_eq!(text["model"], "text-only");
        let image = client.request_thread("thread/resume", initial, &effective, true).await.unwrap();
        assert_eq!(image["model"], "account-image");
        assert_eq!(image["config"]["model_reasoning_effort"], "medium");
        assert_eq!(image["config"]["features"]["shell_tool"], false);
        assert_eq!(image["serviceTier"], "default");
        client.close().await;
    }

    #[test]
    fn model_rejection_is_reported_without_raw_provider_json() {
        let message = json!({"type":"error","status":400,"error":{
            "type":"invalid_request_error","code":"model_not_found"
        }});
        assert!(turn_failure(&json!({"status":"failed","error":{"message":message.to_string()}}))
            .unwrap().contains("重新连接 Codex"));
    }
}
