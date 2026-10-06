//! 关闭窗口先保存暂停，等待本应用启动的执行器退出。
use crate::executor::ExecutorCoordinator;
use std::sync::{atomic::Ordering, Arc};
use tauri::{Manager, Window, WindowEvent};

pub fn on_window_event(window: &Window, event: &WindowEvent) {
    if let WindowEvent::CloseRequested { api, .. } = event {
        api.prevent_close();
        let runtime = window.state::<Arc<ExecutorCoordinator>>().inner().clone();
        if runtime.closing.swap(true, Ordering::SeqCst) {
            return;
        }
        let app = window.app_handle().clone();
        tauri::async_runtime::spawn(async move {
            runtime.shutdown(&app).await;
            app.exit(0);
        });
    }
}
