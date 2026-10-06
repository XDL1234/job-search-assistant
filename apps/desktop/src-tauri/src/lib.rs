pub mod codex;
pub mod executor;
mod lifecycle;
pub mod worker;
use executor::{Connection, ExecutorCoordinator};
use serde_json::{json, Value};
use std::sync::Arc;
use tauri::{AppHandle, Manager, State};
use worker::{UiRequest, WorkerClient};

#[tauri::command]
async fn worker_request(
    request: UiRequest,
    state: State<'_, Arc<ExecutorCoordinator>>,
) -> Result<Value, String> {
    if !matches!(
        request.method.as_str(),
        "health"
            | "snapshot"
            | "export_records"
            | "read_image"
            | "preview_demo"
            | "start_run"
            | "open_simulation"
    ) {
        return Err("界面无权调用此操作".into());
    }
    if state.closing.load(std::sync::atomic::Ordering::SeqCst) {
        return Err("应用正在关闭".into());
    }
    state.worker.request(request).await
}
#[tauri::command]
fn codex_status(state: State<'_, Arc<ExecutorCoordinator>>) -> Connection {
    state.status()
}
#[tauri::command]
async fn codex_connect(
    app: AppHandle,
    state: State<'_, Arc<ExecutorCoordinator>>,
) -> Result<Connection, String> {
    Ok(state.inner().connect(app).await)
}
#[tauri::command]
async fn codex_login(
    app: AppHandle,
    state: State<'_, Arc<ExecutorCoordinator>>,
) -> Result<Connection, String> {
    state.login(&app).await
}
#[tauri::command]
async fn executor_control(
    app: AppHandle,
    state: State<'_, Arc<ExecutorCoordinator>>,
    run_id: String,
    action: String,
) -> Result<(), String> {
    if action == "running" {
        state.inner().start(app, run_id).await
    } else {
        state.control(&app, &run_id, &action).await
    }
}

pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _, _| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.set_focus();
            }
        }))
        .setup(|app| {
            let worker = WorkerClient::from_environment().map_err(std::io::Error::other)?;
            tauri::async_runtime::block_on(worker.call("recover_app", json!({})))
                .map_err(std::io::Error::other)?;
            app.manage(Arc::new(ExecutorCoordinator::new(worker)));
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            worker_request,
            codex_status,
            codex_connect,
            codex_login,
            executor_control
        ])
        .on_window_event(lifecycle::on_window_event)
        .run(tauri::generate_context!())
        .expect("桌面应用启动失败");
}
