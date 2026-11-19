//! 使用参数数组启动 Python，不把 UI 文本拼接为命令。
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::{
    collections::HashMap,
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
    time::Duration,
};
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
    pub resources: PathBuf,
    pub python: PathBuf,
    pub script: PathBuf,
    pub executable: Option<PathBuf>,
    pub codex: Option<PathBuf>,
    pub browser_token: Arc<Mutex<Option<String>>>,
}

pub(crate) fn ui_method_allowed(method: &str) -> bool {
    matches!(method,
        "health" | "snapshot" | "export_records" | "read_image" | "preview_demo"
            | "start_run" | "open_simulation" | "extract_profile" | "save_profile"
            | "get_profile" | "save_personal_info" | "get_resume" | "import_resume" | "preview_resume"
            | "extract_resume_profile" | "open_resume"
            | "preview_run" | "get_reply_settings" | "save_reply_settings"
            | "preview_reply" | "browser_status" | "attention_update"
            | "get_job_preferences" | "save_job_preferences" | "inspect_company_table" | "open_company_site")
}

pub fn installed_worker(resources: &Path) -> Option<PathBuf> {
    let executable = resources.join("runtime/worker/worker.exe");
    executable.is_file().then_some(executable)
}

impl WorkerClient {
    pub fn command(&self) -> Command {
        let mut command = if let Some(executable) = &self.executable {
            Command::new(executable)
        } else {
            let mut c = Command::new(&self.python);
            c.arg("-X").arg("utf8").arg(&self.script);
            c
        };
        command.arg("--root").arg(&self.root).kill_on_drop(true);
        command.env("JOB_ASSISTANT_EMBEDDED", "1");
        if let Some(token) = self.browser_token.lock().unwrap().as_ref() {
            command.env("JOB_ASSISTANT_STREAM_TOKEN", token);
        }
        #[cfg(windows)]
        command.creation_flags(0x08000000);
        command
    }
    pub fn from_environment(resources: &Path) -> Result<Self, String> {
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
        let executable = std::env::var_os("JOB_ASSISTANT_WORKER")
            .map(PathBuf::from)
            .or_else(|| (!cfg!(debug_assertions)).then(|| installed_worker(resources)).flatten());
        if !cfg!(debug_assertions) && executable.is_none() {
            return Err("安装文件不完整：缺少内置执行器，请重新安装求职助手".into());
        }
        let codex = std::env::var_os("JOB_ASSISTANT_CODEX")
            .map(PathBuf::from)
            .or_else(|| {
                let path = resources.join("runtime/codex/bin/codex.exe");
                path.is_file().then_some(path)
            });
        Ok(Self {
            root,
            resources: resources.to_path_buf(),
            python: std::env::var_os("JOB_ASSISTANT_PYTHON")
                .map(PathBuf::from)
                .unwrap_or_else(|| "python.exe".into()),
            script: repo.join("skills/job-search-assistant/scripts/run.py"),
            executable,
            codex,
            browser_token: Arc::new(Mutex::new(None)),
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
        let mut command = self.command();
        if request.method == "_native_codex_proxy_environment" {
            command.env("JOB_ASSISTANT_NATIVE_PROXY_REQUEST", "1");
        }
        command
            .arg("app-request")
            .arg("--file")
            .arg(&path)
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::null())
            .kill_on_drop(true);
        #[cfg(windows)]
        command.creation_flags(0x08000000);
        let reads_profile = matches!(
            request.method.as_str(),
            "extract_profile" | "extract_resume_profile" | "preview_resume"
        );
        let timeout = Duration::from_secs(if reads_profile { 120 } else { 45 });
        let output = tokio::time::timeout(timeout, command.output()).await;
        let _ = std::fs::remove_file(&path);
        let output = output
            .map_err(|_| if reads_profile {
                "简历识别超时，请核对 PDF 页数和清晰度后重试"
            } else {
                "执行器响应超时；请核查未决动作"
            })?
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
    // 代理可能含凭据：此结果只给 Codex Command，不进入界面、台账或模型。
    pub(crate) async fn codex_proxy_environment(&self) -> Result<HashMap<String, String>, String> {
        let result = self.call("_native_codex_proxy_environment", json!({})).await
            .map_err(|_| "无法读取当前用户代理配置".to_string())?;
        let env = result["env"].as_object().ok_or("代理配置响应无效")?;
        let mut additions = HashMap::new();
        for (name, value) in env {
            if !matches!(name.as_str(), "HTTP_PROXY" | "HTTPS_PROXY" | "NO_PROXY") {
                return Err("代理配置包含未允许的环境变量".into());
            }
            let value = value.as_str().filter(|value|value.len() <= 8192 && !value.contains(['\r','\n','\0']))
                .ok_or("代理配置响应无效")?;
            // Rust 与 worker 之间再次检查；显式空值也不覆盖。
            if !std::env::vars_os().any(|(key, _)|key.to_string_lossy().eq_ignore_ascii_case(name)) {
                additions.insert(name.clone(), value.to_owned());
            }
        }
        Ok(additions)
    }
    pub async fn pause(&self, run: &str) -> Result<(), String> {
        self.call("control_run", json!({"run_id":run,"action":"paused"}))
            .await
            .map(|_| ())
    }
}

#[cfg(test)]
mod proxy_tests {
    use super::*;

    #[test]
    fn proxy_environment_is_native_only() {
        assert!(!ui_method_allowed("_native_codex_proxy_environment"));
        assert!(!ui_method_allowed("execution_context"));
        assert!(!ui_method_allowed("revise_run"));
        assert!(!ui_method_allowed("export_records_to"));
        assert!(ui_method_allowed("health"));
        assert!(ui_method_allowed("snapshot"));
    }

    #[cfg(windows)]
    #[tokio::test]
    async fn clean_launch_passes_current_user_proxy_only_to_codex_child() {
        const CHILD_MARKER: &str = "JOB_ASSISTANT_PROXY_TEST_CHILD";
        if std::env::var_os(CHILD_MARKER).is_none() {
            let python = std::env::var_os("JOB_ASSISTANT_PYTHON").unwrap_or_else(|| "python.exe".into());
            let mut command = Command::new(std::env::current_exe().unwrap());
            for (key, _) in std::env::vars_os() {
                let name = key.to_string_lossy().to_ascii_uppercase();
                if name.ends_with("_PROXY") || name.starts_with("JOB_ASSISTANT_") {
                    command.env_remove(key);
                }
            }
            command.args(["--exact", "worker::proxy_tests::clean_launch_passes_current_user_proxy_only_to_codex_child"])
                .env(CHILD_MARKER, "1")
                .env("JOB_ASSISTANT_PROXY_TEST_PYTHON", python)
                .kill_on_drop(true)
                .creation_flags(0x08000000);
            let result = tokio::time::timeout(Duration::from_secs(20), command.output()).await.unwrap().unwrap();
            assert!(result.status.success(), "清洁启动的 Native→worker→Codex 子进程测试失败：{}", String::from_utf8_lossy(&result.stdout));
            return;
        }
        assert!(!std::env::vars_os().any(|(key, _)|key.to_string_lossy().to_ascii_uppercase().ends_with("_PROXY")));
        let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        let sandbox = manifest.join("../../../.verification/native-proxy-smoke");
        std::fs::create_dir_all(&sandbox).unwrap();
        let sandbox = sandbox.canonicalize().unwrap();
        let root = sandbox.join(uuid::Uuid::new_v4().to_string());
        std::fs::create_dir(&root).unwrap();
        let python = PathBuf::from(std::env::var_os("JOB_ASSISTANT_PROXY_TEST_PYTHON").unwrap());
        let worker = WorkerClient {
            root: root.clone(), resources: PathBuf::new(), python: python.clone(),
            script: manifest.join("../../../skills/job-search-assistant/scripts/run.py"),
            executable: None, codex: None, browser_token: Arc::new(Mutex::new(None)),
        };
        let proxy_environment = worker.codex_proxy_environment().await.unwrap();
        let mut expected_names: Vec<_> = proxy_environment.keys().cloned().collect();
        expected_names.sort();
        std::fs::write(root.join("app-server"), r#"import json,os,sys
for line in sys.stdin:
    request=json.loads(line)
    if 'id' not in request: continue
    result={} if request['method']=='initialize' else {'names':sorted(k for k in ('HTTP_PROXY','HTTPS_PROXY','NO_PROXY') if k in os.environ),'internal_request_flag_absent':'JOB_ASSISTANT_NATIVE_PROXY_REQUEST' not in os.environ}
    print(json.dumps({'id':request['id'],'result':result}),flush=True)
"#).unwrap();
        let (sender, mut events) = tokio::sync::mpsc::unbounded_channel();
        let client = crate::codex::CodexClient::spawn(&python, &root, sender, &proxy_environment).await.unwrap();
        let observed = client.request("proxy-probe", json!({})).await.unwrap();
        assert_eq!(observed["names"], json!(expected_names));
        assert_eq!(observed["internal_request_flag_absent"], true);
        assert!(!std::env::vars_os().any(|(key, _)|key.to_string_lossy().to_ascii_uppercase().ends_with("_PROXY")));
        client.close().await;
        assert_eq!(tokio::time::timeout(Duration::from_secs(2), events.recv()).await.unwrap().unwrap()["method"], "client/disconnected");
        drop(client);
        assert_eq!(root.parent(), Some(sandbox.as_path()));
        std::fs::remove_dir_all(root).unwrap();
    }
}
