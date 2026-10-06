//! 使用参数数组启动 Python，不把 UI 文本拼接为命令。
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{path::PathBuf, time::Duration};
use tokio::process::Command;

#[derive(Clone, Deserialize, Serialize)]
pub struct UiRequest {
    pub id: String,
    pub method: String,
    #[serde(default)]
    pub params: Value,
}
#[derive(Clone)]
pub struct WorkerClient {
    pub root: PathBuf,
    pub python: PathBuf,
    pub script: PathBuf,
    pub executable: Option<PathBuf>,
}

impl WorkerClient {
    pub fn from_environment() -> Result<Self, String> {
        let repo = std::env::var_os("JOB_ASSISTANT_REPO")
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../.."));
        let root = std::env::var_os("JOB_ASSISTANT_DATA_DIR")
            .map(PathBuf::from)
            .unwrap_or_else(|| {
                PathBuf::from(std::env::var_os("USERPROFILE").unwrap_or_default())
                    .join(".job-search-assistant/desktop")
            });
        std::fs::create_dir_all(&root).map_err(|e| e.to_string())?;
        Ok(Self {
            root,
            python: std::env::var_os("JOB_ASSISTANT_PYTHON")
                .map(PathBuf::from)
                .unwrap_or_else(|| "python.exe".into()),
            script: repo.join("skills/job-search-assistant/scripts/run.py"),
            executable: std::env::var_os("JOB_ASSISTANT_WORKER").map(PathBuf::from),
        })
    }
    pub async fn request(&self, request: UiRequest) -> Result<Value, String> {
        let directory = self.root.join("requests");
        std::fs::create_dir_all(&directory).map_err(|e| e.to_string())?;
        let path = directory.join(format!("{}.json", uuid::Uuid::new_v4()));
        std::fs::write(
            &path,
            serde_json::to_vec(&request).map_err(|e| e.to_string())?,
        )
        .map_err(|e| e.to_string())?;
        let mut command = if let Some(executable) = &self.executable {
            Command::new(executable)
        } else {
            let mut c = Command::new(&self.python);
            c.arg("-X").arg("utf8").arg(&self.script);
            c
        };
        command
            .arg("--root")
            .arg(&self.root)
            .arg("app-request")
            .arg("--file")
            .arg(&path)
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::null())
            .kill_on_drop(true);
        #[cfg(windows)]
        command.creation_flags(0x08000000);
        let output = tokio::time::timeout(Duration::from_secs(45), command.output()).await;
        let _ = std::fs::remove_file(&path);
        let output = output
            .map_err(|_| "执行器响应超时；请核查未决动作")?
            .map_err(|e| format!("执行器未启动：{e}"))?;
        let value: Value =
            serde_json::from_slice(&output.stdout).map_err(|_| "执行器响应格式错误")?;
        if value["id"] != request.id {
            return Err("执行器响应编号不一致".into());
        }
        Ok(value)
    }
    pub async fn call(&self, method: &str, params: Value) -> Result<Value, String> {
        let value = self
            .request(UiRequest {
                id: uuid::Uuid::new_v4().to_string(),
                method: method.into(),
                params,
            })
            .await?;
        if value["ok"] != true {
            return Err(value["error"]["message"]
                .as_str()
                .unwrap_or("执行器失败")
                .into());
        }
        Ok(value["result"].clone())
    }
    pub async fn pause(&self, run: &str) -> Result<(), String> {
        self.call("control_run", json!({"run_id":run,"action":"paused"}))
            .await
            .map(|_| ())
    }
}
