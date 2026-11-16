//! 应用协调器：先暂停业务输入，再取消模型；模型完成不代表投递成功。
use crate::{
    codex::{turn_failure, CodexClient, ConnectionEpoch, TurnGate},
    worker::WorkerClient,
};
use serde::Serialize;
use serde_json::{json, Value};
use std::{
    sync::{
        atomic::{AtomicBool, AtomicU64, Ordering},
        Arc, Mutex,
    },
    time::Duration,
};
use tauri::{AppHandle, Emitter};
use tokio::{
    process::{Child, Command},
    sync::{mpsc, Mutex as AsyncMutex},
};

#[derive(Clone, Serialize)]
pub struct Connection {
    pub state: String,
    pub message: String,
    pub version: Option<String>,
}
struct Active {
    run: String,
    thread: Option<String>,
    gate: TurnGate,
}
pub struct ExecutorCoordinator {
    pub worker: WorkerClient,
    client: AsyncMutex<Option<Arc<CodexClient>>>,
    connection: Mutex<Connection>,
    active: Mutex<Option<Active>>,
    connect_lock: AsyncMutex<()>,
    start_lock: AsyncMutex<()>,
    tool_lock: AsyncMutex<()>,
    seq: AtomicU64,
    epoch: ConnectionEpoch,
    browser_bridge: AsyncMutex<Option<Child>>,
    pub closing: AtomicBool,
}

impl ExecutorCoordinator {
    pub fn new(worker: WorkerClient) -> Self {
        Self {
            worker,
            client: AsyncMutex::new(None),
            connection: Mutex::new(Connection {
                state: "disconnected".into(),
                message: "连接 Codex 后可运行任务".into(),
                version: None,
            }),
            active: Mutex::new(None),
            connect_lock: AsyncMutex::new(()),
            start_lock: AsyncMutex::new(()),
            tool_lock: AsyncMutex::new(()),
            seq: 1.into(),
            epoch: ConnectionEpoch::default(),
            browser_bridge: AsyncMutex::new(None),
            closing: AtomicBool::new(false),
        }
    }
    pub fn status(&self) -> Connection {
        self.connection.lock().unwrap().clone()
    }
    pub async fn browser_connect(&self) -> Result<Value, String> {
        let mut process = self.browser_bridge.lock().await;
        if self.closing.load(Ordering::SeqCst) {
            return Err("应用正在关闭".into());
        }
        if process
            .as_mut()
            .is_none_or(|child| !matches!(child.try_wait(), Ok(None)))
        {
            let child = self
                .worker
                .command()
                .arg("browser-bridge")
                .stdin(std::process::Stdio::null())
                .stdout(std::process::Stdio::null())
                .stderr(std::process::Stdio::null())
                .spawn()
                .map_err(|e| e.to_string())?;
            *process = Some(child);
        }
        let deadline = tokio::time::Instant::now() + Duration::from_secs(15);
        loop {
            if process
                .as_mut()
                .is_none_or(|child| !matches!(child.try_wait(), Ok(None)))
            {
                return Err("浏览器连接器已退出，请检查端口占用后重试".into());
            }
            if self.worker.call("browser_status", json!({})).await?["ready"] == true {
                break;
            }
            if tokio::time::Instant::now() >= deadline {
                return Err("浏览器连接器启动超时，请重试".into());
            }
            tokio::time::sleep(Duration::from_millis(250)).await;
        }
        let path = self.worker.root.join("browser-bridge.json");
        let info: Value =
            serde_json::from_slice(&std::fs::read(path).map_err(|_| "浏览器连接器尚未就绪")?)
                .map_err(|e| e.to_string())?;
        Ok(
            json!({"code":format!("{}:{}",info["port"],info["token"].as_str().unwrap_or("")),"message":"在 Chrome 加载随安装包提供的 browser-extension 文件夹，打开扩展输入连接码。"}),
        )
    }
    fn set_status(&self, app: &AppHandle, state: &str, message: &str) -> Connection {
        let mut connection = self.connection.lock().unwrap();
        connection.state = state.into();
        connection.message = message.into();
        let _ = app.emit("codex-status", connection.clone());
        connection.clone()
    }
    fn emit(&self, app: &AppHandle, run: &str, kind: &str, payload: Value) {
        let _ = app.emit("executor-event",json!({"runId":run,"seq":self.seq.fetch_add(1,Ordering::Relaxed),"type":kind,"payload":payload}));
    }
    pub async fn connect(self: &Arc<Self>, app: AppHandle) -> Connection {
        let _guard = self.connect_lock.lock().await;
        if self.closing.load(Ordering::SeqCst) {
            return self.status();
        }
        if self.status().state == "ready" {
            return self.status();
        }
        let generation = self.epoch.advance();
        if let Some(old) = self.client.lock().await.take() {
            old.close().await;
        }
        let Some(exe) = self.worker.codex.clone() else {
            return self.set_status(
                &app,
                "not_installed",
                "未找到 Codex 执行器。安装版请重新安装；开发环境可指定 JOB_ASSISTANT_CODEX。",
            );
        };
        self.set_status(&app, "connecting", "正在连接 Codex…");
        let result: Result<Arc<CodexClient>, String> = async {
            let mut command = Command::new(&exe);
            command.arg("--version").kill_on_drop(true);
            #[cfg(windows)]
            command.creation_flags(0x08000000);
            let output = tokio::time::timeout(Duration::from_secs(10), command.output())
                .await
                .map_err(|_| "Codex 版本检测超时")?
                .map_err(|e| e.to_string())?;
            let version = String::from_utf8_lossy(&output.stdout).trim().to_string();
            self.connection.lock().unwrap().version = Some(version.clone());
            if version != "codex-cli 0.156.1" {
                return Err(format!(
                    "此开发版已验证 Codex 0.156.1；当前为 {version}，需先验证协议兼容性"
                ));
            }
            let (sender, mut receiver) = mpsc::unbounded_channel();
            let client = CodexClient::spawn(&exe, &self.worker.root, sender).await?;
            let runtime = self.clone();
            let event_app = app.clone();
            let source = client.clone();
            tokio::spawn(async move {
                while let Some(event) = receiver.recv().await {
                    runtime
                        .process_event(event_app.clone(), event, source.clone(), generation)
                        .await;
                }
            });
            Ok(client)
        }
        .await;
        match result {
            Ok(client) => {
                if self.closing.load(Ordering::SeqCst) {
                    client.close().await;
                    return self.status();
                }
                *self.client.lock().await = Some(client.clone());
                match client
                    .request("account/read", json!({"refreshToken":false}))
                    .await
                {
                    Ok(value) if !value["account"].is_null() => {
                        self.set_status(&app, "ready", "Codex 已连接，可以开始本地模拟任务")
                    }
                    Ok(_) => self.set_status(
                        &app,
                        "login_required",
                        "请登录 ChatGPT，以使用 Codex 执行器",
                    ),
                    Err(error) => self.set_status(&app, "error", &error),
                }
            }
            Err(error) => self.set_status(&app, "error", &error),
        }
    }
    pub async fn login(&self, app: &AppHandle) -> Result<Connection, String> {
        let client = self.get_client().await?;
        let value = client
            .request("account/login/start", json!({"type":"chatgpt"}))
            .await?;
        let url = value["authUrl"].as_str().ok_or("登录响应缺少授权地址")?;
        if !url.starts_with("https://auth.openai.com/") {
            return Err("登录地址未通过校验".into());
        }
        let mut command = Command::new("rundll32.exe");
        command.args(["url.dll,FileProtocolHandler", url]);
        #[cfg(windows)]
        command.creation_flags(0x08000000);
        command.spawn().map_err(|e| e.to_string())?;
        Ok(self.set_status(app, "logging_in", "已打开官方登录页，请在浏览器完成登录"))
    }
    async fn get_client(&self) -> Result<Arc<CodexClient>, String> {
        self.client
            .lock()
            .await
            .clone()
            .ok_or_else(|| "请先连接 Codex".into())
    }
    fn cancelled(&self, run: &str) -> bool {
        self.closing.load(Ordering::SeqCst)
            || self
                .active
                .lock()
                .unwrap()
                .as_ref()
                .is_none_or(|a| a.run != run || !a.gate.busy || a.gate_is_cancelled())
    }

    pub async fn start(self: &Arc<Self>, app: AppHandle, run: String) -> Result<(), String> {
        let _start = self.start_lock.try_lock().map_err(|_| "正在启动另一任务")?;
        let context = self
            .worker
            .call("execution_context", json!({"run_id":run}))
            .await?;
        if context["config"]["execution_mode"] != "script" && self.status().state != "ready" {
            return Err("请先连接 Codex".into());
        }
        {
            let mut active = self.active.lock().unwrap();
            if active.as_ref().is_some_and(|a| a.gate.busy) {
                return Err("已有任务在执行或取消中，请稍后重试".into());
            }
            let mut gate = TurnGate::default();
            gate.begin()?;
            *active = Some(Active {
                run: run.clone(),
                thread: None,
                gate,
            });
        }
        let runtime = self.clone();
        tokio::spawn(async move {
            if let Err(error) = runtime.start_inner(&app, &run).await {
                runtime.fail(&app, &run, &error).await;
            }
        });
        Ok(())
    }
    async fn start_inner(&self, app: &AppHandle, run: &str) -> Result<(), String> {
        let context = self
            .worker
            .call("execution_context", json!({"run_id":run}))
            .await?;
        if context["config"]["simulation"] != true
            && context["config"]["channels"]
                .as_array()
                .is_some_and(|channels| channels.contains(&json!("boss")))
        {
            self.browser_connect().await?;
            if self.worker.call("browser_status", json!({})).await?["connected"] != true {
                return Err("请先在 Chrome 加载并连接浏览器扩展，再开始任务".into());
            }
        }
        self.worker
            .call("control_run", json!({"run_id":run,"action":"running"}))
            .await?;
        self.emit(app, run, "state", json!({"state":"starting"}));
        tokio::time::sleep(Duration::from_secs(5)).await;
        if self.cancelled(run) {
            return Err("任务已暂停".into());
        }
        self.worker
            .call("activate_run", json!({"run_id":run}))
            .await?;
        if context["config"]["execution_mode"] == "script" {
            return self.run_script(app, run).await;
        }
        self.start_ai_turn(run, context).await
    }
    async fn start_ai_turn(&self, run: &str, context: Value) -> Result<(), String> {
        let client = self.get_client().await?;
        let previous = context["session"]["thread_id"].as_str();
        let thread = if let Some(id) = previous {
            client
                .request(
                    "thread/resume",
                    json!({"threadId":id,"sandbox":"read-only","approvalPolicy":"never"}),
                )
                .await?
        } else {
            client.request("thread/start",json!({"cwd":self.worker.root,"sandbox":"read-only","approvalPolicy":"never",
              "developerInstructions":include_str!("runtime-instructions.md"),
              "dynamicTools":[{"type":"function","name":"job_action","description":"求职助手当前轮次的执行工具。request 包含 operation 与参数；浏览器操作使用 browser，桌面操作使用 capture/act。不能修改授权或恢复人工会话。","inputSchema":{"type":"object","properties":{"request":{"type":"string"}},"required":["request"],"additionalProperties":false}}]})).await?
        };
        let thread_id = thread["thread"]["id"]
            .as_str()
            .ok_or("缺少 Codex 会话编号")?
            .to_string();
        {
            if let Some(active) = self.active.lock().unwrap().as_mut() {
                if active.run == run {
                    active.thread = Some(thread_id.clone());
                }
            }
        }
        self.worker
            .call(
                "bind_session",
                json!({"run_id":run,"thread_id":thread_id,"state":"running"}),
            )
            .await?;
        if self.cancelled(run) {
            return Err("任务已暂停".into());
        }
        let text=format!("使用 $job-search-assistant 完成本轮已确认任务。应用已准备依赖与授权，仅使用 job_action。配置与资料：{}。先 status 核查未决动作；simulation=true 按模拟流程。若run.status=watching只处理当前新消息，禁止新增沟通；真实 BOSS 优先 browser action=observe 读取标签页，按 employment_type 和 boss_mode 筛选。官网仅操作 company_rows 授权网站，逐字段留截图。到上限或页面受限就停止，面试整条转人工。不要创造额外要求或话术。",context);
        let result=client.request("turn/start",json!({"threadId":thread_id,"input":[{"type":"skill","name":"job-search-assistant","path":context["skill_path"]},{"type":"text","text":text}]})).await?;
        let turn = result["turn"]["id"]
            .as_str()
            .ok_or("缺少回合编号")?
            .to_string();
        let cancelled = {
            let mut active = self.active.lock().unwrap();
            active
                .as_mut()
                .filter(|a| a.run == run && a.gate.busy)
                .map(|a| a.gate.started(turn.clone()))
                .unwrap_or(true)
        };
        if cancelled {
            let _ = client
                .request(
                    "turn/interrupt",
                    json!({"threadId":thread_id,"turnId":turn}),
                )
                .await;
        }
        Ok(())
    }
    async fn run_script(&self, app: &AppHandle, run: &str) -> Result<(), String> {
        self.worker
            .call(
                "bind_session",
                json!({"run_id":run,"state":"running","thread_id":null}),
            )
            .await?;
        loop {
            if self.cancelled(run) {
                return Err("任务已暂停".into());
            }
            let result = {
                let _tool = self.tool_lock.lock().await;
                if self.cancelled(run) {
                    return Err("任务已暂停".into());
                }
                self.worker
                    .call("script_step", json!({"run_id":run}))
                    .await?
            };
            self.emit(
                app,
                run,
                "message",
                json!({"text":format!("{}\n",result["message"].as_str().unwrap_or("已处理一步"))}),
            );
            if result["done"] == true {
                return self.run_watch(app, run).await;
            }
            tokio::time::sleep(Duration::from_secs(5)).await;
        }
    }
    async fn run_watch(&self, app: &AppHandle, run: &str) -> Result<(), String> {
        if self.cancelled(run) {
            return Err("任务已暂停".into());
        }
        self.worker
            .call("enter_watch", json!({"run_id":run}))
            .await?;
        if self.cancelled(run) {
            return Err("任务已暂停".into());
        }
        self.worker
            .call(
                "bind_session",
                json!({"run_id":run,"state":"watching","thread_id":null}),
            )
            .await?;
        self.emit(app, run, "state", json!({"state":"watching"}));
        loop {
            if self.cancelled(run) {
                return Err("任务已暂停".into());
            }
            let result = {
                let _tool = self.tool_lock.lock().await;
                if self.cancelled(run) {
                    return Err("任务已暂停".into());
                }
                self.worker
                    .call("watch_step", json!({"run_id":run}))
                    .await?
            };
            if result["ai_required"].is_object() {
                if self.status().state == "ready" {
                    let context = self
                        .worker
                        .call("execution_context", json!({"run_id":run}))
                        .await?;
                    return self.start_ai_turn(run, context).await;
                }
            }
            if result["notify"] == true {
                self.emit(app, run, "attention", json!({"message":result["message"]}));
            }
            let delay = result["delay_seconds"].as_u64().unwrap_or(60).clamp(1, 300);
            for _ in 0..delay {
                if self.cancelled(run) {
                    return Err("任务已暂停".into());
                }
                tokio::time::sleep(Duration::from_secs(1)).await;
            }
        }
    }
    pub async fn inbox_send(&self, params: Value) -> Result<Value, String> {
        let _tool = self.tool_lock.lock().await;
        if self.closing.load(Ordering::SeqCst) {
            return Err("应用正在关闭".into());
        }
        self.worker.call("manual_reply", params).await
    }
    pub async fn inbox_open(&self, params: Value) -> Result<Value, String> {
        let _tool = self.tool_lock.lock().await;
        if self.closing.load(Ordering::SeqCst) {
            return Err("应用正在关闭".into());
        }
        self.worker.call("reply_focus", params).await
    }
    pub async fn control(&self, app: &AppHandle, run: &str, action: &str) -> Result<(), String> {
        if !matches!(action, "paused" | "stopped") {
            return Err("不支持的控制指令".into());
        }
        let target = {
            let mut active = self.active.lock().unwrap();
            active.as_mut().filter(|a| a.run == run).map(|a| {
                a.gate.cancel();
                (a.thread.clone(), a.gate.turn_id.clone())
            })
        };
        self.worker
            .call("control_run", json!({"run_id":run,"action":action}))
            .await?;
        self.emit(app, run, "state", json!({"state":action}));
        if let Some((Some(thread), Some(turn))) = target {
            if let Ok(client) = self.get_client().await {
                let _ = client
                    .request("turn/interrupt", json!({"threadId":thread,"turnId":turn}))
                    .await;
            }
        }
        Ok(())
    }
    async fn fail(&self, app: &AppHandle, run: &str, error: &str) {
        let _ = self.worker.pause(run).await;
        let thread = {
            let mut a = self.active.lock().unwrap();
            a.as_mut().filter(|a| a.run == run).and_then(|a| {
                a.gate.cancel();
                a.gate.finished();
                a.thread.clone()
            })
        };
        let _ = self
            .worker
            .call(
                "bind_session",
                json!({"run_id":run,"thread_id":thread,"state":"error","error":error}),
            )
            .await;
        self.emit(app, run, "error", json!({"message":error}));
    }
    async fn process_event(
        self: &Arc<Self>,
        app: AppHandle,
        event: Value,
        source: Arc<CodexClient>,
        generation: u64,
    ) {
        if !self.epoch.accepts(generation) || self.closing.load(Ordering::SeqCst) {
            return;
        }
        let method = event["method"].as_str().unwrap_or("");
        if method == "account/login/completed" || method == "account/updated" {
            if let Ok(v) = source
                .request("account/read", json!({"refreshToken":false}))
                .await
            {
                if !self.epoch.accepts(generation) || self.closing.load(Ordering::SeqCst) {
                    return;
                }
                if !v["account"].is_null() {
                    self.set_status(&app, "ready", "Codex 已连接");
                } else {
                    self.set_status(&app, "login_required", "请登录 Codex");
                }
            }
            return;
        }
        if method == "client/disconnected" || method == "client/protocolError" {
            self.set_status(&app, "error", "Codex 连接已断开，请重新连接");
            let run = self
                .active
                .lock()
                .unwrap()
                .as_ref()
                .filter(|a| a.thread.is_some())
                .map(|a| a.run.clone());
            if let Some(run) = run {
                self.fail(&app, &run, "Codex 中断，已暂停任务；未决操作需核查")
                    .await;
            }
            return;
        }
        let params = &event["params"];
        let scope = self
            .active
            .lock()
            .unwrap()
            .as_ref()
            .filter(|a| a.thread.as_deref() == params["threadId"].as_str())
            .map(|a| a.run.clone());
        if method == "item/tool/call" {
            let runtime = self.clone();
            tokio::spawn(async move {
                runtime.handle_tool(app, event, source, generation).await;
            });
            return;
        }
        if event.get("id").is_some() {
            let _=source.send(json!({"id":event["id"],"error":{"code":-32601,"message":"当前模拟执行器不授权此请求，请转人工"}})).await;
            if let Some(run) = scope {
                let _ = self.control(&app, &run, "paused").await;
                self.emit(
                    &app,
                    &run,
                    "attention",
                    json!({"message":"模型请求额外权限，已暂停"}),
                );
            }
            return;
        }
        let Some(run) = scope else {
            return;
        };
        if method == "turn/started" {
            let turn = params["turn"]["id"].as_str().unwrap_or("").to_string();
            let cancel = {
                let mut a = self.active.lock().unwrap();
                a.as_mut()
                    .map(|a| a.gate.started(turn.clone()))
                    .unwrap_or(true)
            };
            if cancel {
                let _ = source
                    .request(
                        "turn/interrupt",
                        json!({"threadId":params["threadId"],"turnId":turn}),
                    )
                    .await;
            }
        } else if method == "item/agentMessage/delta" {
            self.emit(&app, &run, "message", json!({"text":params["delta"]}));
        } else if method == "turn/completed" {
            let matches = {
                let a = self.active.lock().unwrap();
                a.as_ref()
                    .is_some_and(|a| a.gate.turn_id.as_deref() == params["turn"]["id"].as_str())
            };
            if !matches {
                return;
            }
            if let Some(error) = turn_failure(&params["turn"]) {
                self.fail(&app, &run, &error).await;
                return;
            }
            let context = self
                .worker
                .call("execution_context", json!({"run_id":run}))
                .await;
            if !self.cancelled(&run)
                && context.as_ref().is_ok_and(|c| {
                    c["config"]["simulation"] != true
                        && c["config"]["channels"]
                            .as_array()
                            .is_some_and(|v| v.contains(&json!("boss")))
                })
            {
                if let Err(error) = self
                    .worker
                    .call("finish_ai_watch", json!({"run_id":run}))
                    .await
                {
                    self.fail(&app, &run, &error).await;
                    return;
                }
                let watching = {
                    if let Some(a) = self
                        .active
                        .lock()
                        .unwrap()
                        .as_mut()
                        .filter(|a| a.run == run)
                    {
                        a.thread = None;
                        a.gate.finish_for_watch()
                    } else {
                        false
                    }
                };
                if !watching {
                    return;
                }
                let runtime = self.clone();
                tokio::spawn(async move {
                    if let Err(error) = runtime.run_watch(&app, &run).await {
                        runtime.fail(&app, &run, &error).await;
                    }
                });
                return;
            }
            let _ = self.worker.pause(&run).await;
            {
                if let Some(a) = self.active.lock().unwrap().as_mut() {
                    a.gate.finished();
                }
            }
            let _ = self
                .worker
                .call(
                    "bind_session",
                    json!({"run_id":run,"thread_id":params["threadId"],"state":"idle"}),
                )
                .await;
            self.emit(
                &app,
                &run,
                "state",
                json!({"state":params["turn"]["status"]}),
            );
        }
    }
    async fn handle_tool(
        &self,
        app: AppHandle,
        event: Value,
        source: Arc<CodexClient>,
        generation: u64,
    ) {
        let _lock = self.tool_lock.lock().await;
        if !self.epoch.accepts(generation) || self.closing.load(Ordering::SeqCst) {
            return;
        }
        let p = &event["params"];
        let scope = {
            let active = self.active.lock().unwrap();
            active
                .as_ref()
                .filter(|a| {
                    a.gate.can_act()
                        && a.thread.as_deref() == p["threadId"].as_str()
                        && a.gate.turn_id.as_deref() == p["turnId"].as_str()
                })
                .map(|a| a.run.clone())
        };
        let result: Result<Value, String> = async {
            let run = scope.as_ref().ok_or("任务未运行或已经暂停")?;
            if p["tool"] != "job_action" {
                return Err("未知执行工具".into());
            }
            let request: Value = serde_json::from_str(
                p["arguments"]["request"]
                    .as_str()
                    .ok_or("工具参数格式错误")?,
            )
            .map_err(|e| e.to_string())?;
            self.worker
                .call("executor_action", json!({"run_id":run,"request":request}))
                .await
        }
        .await;
        let response = match result {
            Ok(v) => v,
            Err(e) => json!({"success":false,"contentItems":[{"type":"inputText","text":e}]}),
        };
        if self.epoch.accepts(generation) && !self.closing.load(Ordering::SeqCst) {
            let _ = source
                .send(json!({"id":event["id"],"result":response}))
                .await;
        }
        if let Some(run) = scope {
            self.emit(&app, &run, "state", json!({"state":"observed"}));
        }
    }
    pub async fn shutdown(&self, app: &AppHandle) {
        self.closing.store(true, Ordering::SeqCst);
        // 初始化中的进程尚未登记；等待 connect 收尾再退出原生进程。
        let _connection = self.connect_lock.lock().await;
        self.epoch.advance();
        let run = self.active.lock().unwrap().as_ref().map(|a| a.run.clone());
        if let Some(run) = run {
            let _ = self.control(app, &run, "paused").await;
        }
        if let Some(client) = self.client.lock().await.take() {
            client.close().await;
        }
        let _ = tokio::time::timeout(Duration::from_secs(46), self.tool_lock.lock()).await;
        let _ = self.worker.call("recover_app", json!({})).await;
        if let Some(mut bridge) = self.browser_bridge.lock().await.take() {
            let _ = bridge.kill().await;
        }
    }
}

impl Active {
    fn gate_is_cancelled(&self) -> bool {
        self.gate.is_cancelled()
    }
}
