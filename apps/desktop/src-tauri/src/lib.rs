pub mod codex;
pub mod ai_usage;
pub mod browser_runtime;
mod company_page;
mod company_web;
mod dedicated_chrome;
mod dialog;
pub mod executor;
mod lifecycle;
mod web_autofill;
pub mod worker;
use executor::{Connection, ExecutorCoordinator};
use serde_json::{json, Value};
use std::sync::Arc;
use tauri::{AppHandle, Manager, State};
use tauri::ipc::Channel;
use browser_runtime::PreviewEvent;
use worker::{UiRequest, WorkerClient};

#[tauri::command]
async fn worker_request(
    request: UiRequest,
    state: State<'_, Arc<ExecutorCoordinator>>,
) -> Result<Value, String> {
    if !worker::ui_method_allowed(&request.method) {
        return Err("界面无权调用此操作".into());
    }
    if state.closing.load(std::sync::atomic::Ordering::SeqCst) {
        return Err("应用正在关闭".into());
    }
    state.worker.request(request).await
}
#[tauri::command]
async fn export_records_dialog(state: State<'_, Arc<ExecutorCoordinator>>) -> Result<Option<String>, String> {
    let ensure_open = || {
        if state.closing.load(std::sync::atomic::Ordering::SeqCst) {
            Err("应用正在关闭".to_owned())
        } else {
            Ok(())
        }
    };
    ensure_open()?;
    let Some(destination) = dialog::choose_export_path().await? else { return Ok(None); };
    ensure_open()?;
    let result = state.worker.call("export_records_to", json!({"destination": destination})).await?;
    result["path"].as_str().map(|path| Some(path.to_owned())).ok_or_else(|| "导出结果缺少文件位置".into())
}
#[tauri::command]
fn codex_status(state: State<'_, Arc<ExecutorCoordinator>>) -> Connection {
    state.status()
}
#[tauri::command]
async fn codex_connect(
    app: AppHandle,
    state: State<'_, Arc<ExecutorCoordinator>>,
    reuse_login: Option<bool>,
) -> Result<Connection, String> {
    Ok(state.inner().connect(app, reuse_login.unwrap_or(false)).await)
}
#[tauri::command]
async fn codex_login(
    app: AppHandle,
    state: State<'_, Arc<ExecutorCoordinator>>,
) -> Result<Connection, String> {
    state.inner().login(app).await
}
#[tauri::command]
async fn codex_cancel_login(
    app: AppHandle,
    state: State<'_, Arc<ExecutorCoordinator>>,
) -> Result<Connection, String> {
    Ok(state.cancel_login(&app).await)
}
#[tauri::command]
async fn browser_connect(state: State<'_, Arc<ExecutorCoordinator>>) -> Result<Value, String> {
    state.browser_connect().await
}
#[tauri::command]
async fn browser_refresh_tabs(state: State<'_, Arc<ExecutorCoordinator>>) -> Result<Value, String> {
    state.browser_refresh_tabs().await
}
#[tauri::command]
async fn browser_inspect(state: State<'_, Arc<ExecutorCoordinator>>, scope: Option<String>) -> Result<Value, String> {
    state.browser_inspect(scope.as_deref().unwrap_or("auto")).await
}
#[tauri::command]
async fn browser_apply_job_preferences(state: State<'_, Arc<ExecutorCoordinator>>) -> Result<Value, String> {
    state.browser_apply_job_preferences().await
}
#[tauri::command]
async fn executor_confirm_revision(state: State<'_, Arc<ExecutorCoordinator>>, ticket: String, confirmed: bool) -> Result<Value, String> {
    state.confirm_revision(ticket, confirmed).await
}
#[tauri::command]
fn browser_runtime_status(state: State<'_, Arc<ExecutorCoordinator>>) -> Value {
    state.browser.status()
}
#[tauri::command]
fn ai_usage_status(state: State<'_, Arc<ExecutorCoordinator>>, run_id: String) -> crate::ai_usage::RunUsage {
    state.usage_status(&run_id)
}
#[tauri::command]
fn ai_usage_overview(state: State<'_, Arc<ExecutorCoordinator>>) -> std::collections::HashMap<String, crate::ai_usage::RunUsage> {
    state.usage_overview()
}
#[tauri::command]
async fn browser_select_tab(state: State<'_, Arc<ExecutorCoordinator>>, target_id: String) -> Result<Value, String> {
    state.browser_select_tab(target_id).await
}
#[tauri::command]
async fn browser_preview_start(state: State<'_, Arc<ExecutorCoordinator>>, on_event: Channel<PreviewEvent>, run_id: Option<String>) -> Result<Value, String> {
    if state.closing.load(std::sync::atomic::Ordering::SeqCst) {
        return Err("应用正在关闭".into());
    }
    state.start_browser_preview(run_id, on_event).await
}
#[tauri::command]
async fn browser_preview_ack(state: State<'_, Arc<ExecutorCoordinator>>, epoch: String, seq: u64) -> Result<(), String> {
    state.browser.ack_preview(&epoch, seq).await
}
#[tauri::command]
async fn browser_preview_stop(state: State<'_, Arc<ExecutorCoordinator>>, epoch: String) -> Result<(), String> {
    state.browser.stop_preview_if_epoch(&epoch).await;
    Ok(())
}
#[tauri::command]
async fn inbox_send(
    state: State<'_, Arc<ExecutorCoordinator>>,
    params: Value,
) -> Result<Value, String> {
    state.inbox_send(params).await
}
#[tauri::command]
async fn inbox_open(
    state: State<'_, Arc<ExecutorCoordinator>>,
    params: Value,
) -> Result<Value, String> {
    state.inbox_open(params).await
}
#[tauri::command]
async fn executor_control(
    app: AppHandle,
    state: State<'_, Arc<ExecutorCoordinator>>,
    run_id: String,
    action: String,
) -> Result<(), String> {
    if matches!(action.as_str(), "running" | "watching" | "prepare_company") {
        state.inner().start(app, run_id, &action).await
    } else {
        state.control(&app, &run_id, &action).await
    }
}

#[tauri::command]
async fn company_focus_tab(state: State<'_, Arc<ExecutorCoordinator>>, run_id: String, source_row: i64) -> Result<(), String> {
    state.company_focus_tab(&run_id, source_row).await
}

#[tauri::command]
async fn company_mark_submitted(state: State<'_, Arc<ExecutorCoordinator>>, run_id: String, source_row: i64) -> Result<Value, String> {
    state.worker.call("company_mark_submitted", serde_json::json!({"run_id":run_id,"source_row":source_row})).await
}

#[tauri::command]
async fn company_reopen_review(state: State<'_, Arc<ExecutorCoordinator>>, run_id: String, source_row: i64) -> Result<Value, String> {
    state.worker.call("company_reopen_review", serde_json::json!({"run_id":run_id,"source_row":source_row})).await
}

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _, _| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.set_focus();
            }
        }))
        .setup(|app| {
            let worker = WorkerClient::from_environment(&app.path().resource_dir()?)
                .map_err(std::io::Error::other)?;
            let legacy_home = std::env::var_os("USERPROFILE")
                .map(std::path::PathBuf::from)
                .map_or_else(|| app.path().home_dir(), Ok)?;
            let legacy = legacy_home.join(".job-search-assistant/desktop");
            let legacy_webview = app.path().app_local_data_dir().ok();
            tauri::async_runtime::block_on(async {
                worker.prepare_storage(&legacy, legacy_webview.as_deref()).await?;
                worker.call("recover_app", json!({})).await
            }).map_err(|error| {
                dialog::startup_error(&error);
                std::io::Error::other(error)
            })?;
            let webview = worker.root.join("webview");
            let runtime = Arc::new(ExecutorCoordinator::new(worker));
            app.manage(runtime.clone());
            app.manage(web_autofill::WebAutofill::default());
            let config = app.config().app.windows.iter().find(|window| window.label == "main")
                .ok_or_else(|| std::io::Error::other("缺少求职助手主窗口配置"))?;
            tauri::WebviewWindowBuilder::from_config(app, config)?
                .data_directory(webview)
                .build()?;
            if std::env::var("JOB_ASSISTANT_SKIP_CHROME_SETUP").as_deref() != Ok("1") {
                tauri::async_runtime::spawn(async move { let _ = runtime.browser_startup().await; });
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            worker_request,
            export_records_dialog,
            codex_status,
            codex_connect,
            codex_login,
            codex_cancel_login,
            executor_control,
            company_focus_tab,
            company_mark_submitted,
            company_reopen_review,
            executor_confirm_revision,
            dialog::pick_file,
            browser_connect,
            browser_refresh_tabs,
            browser_inspect,
            browser_apply_job_preferences,
            browser_runtime_status,
            ai_usage_status,
            ai_usage_overview,
            browser_select_tab,
            browser_preview_start,
            browser_preview_ack,
            browser_preview_stop,
            web_autofill::web_autofill_open,
            web_autofill::web_autofill_tabs,
            web_autofill::web_autofill_scan,
            web_autofill::web_autofill_advice,
            web_autofill::web_autofill_fill,
            inbox_send,
            inbox_open
        ])
        .on_window_event(lifecycle::on_window_event)
        .run(tauri::generate_context!())
        .expect("桌面应用启动失败");
}
