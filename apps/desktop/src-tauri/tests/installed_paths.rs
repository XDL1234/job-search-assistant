use job_search_desktop::worker::installed_worker;

#[test]
fn installed_worker_is_resolved_without_source_checkout() {
    let root = std::env::temp_dir().join(format!("求职助手 安装测试-{}", uuid::Uuid::new_v4()));
    let executable = root.join("runtime/worker/worker.exe");
    std::fs::create_dir_all(executable.parent().unwrap()).unwrap();
    assert!(installed_worker(&root).is_none());
    std::fs::write(&executable, b"fixture").unwrap();
    assert_eq!(installed_worker(&root), Some(executable.clone()));
    std::fs::remove_file(executable).unwrap();
    std::fs::remove_dir_all(root).unwrap();
}
