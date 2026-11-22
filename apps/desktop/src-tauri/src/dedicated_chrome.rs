//! 启动或重连求职专用的 Chrome 浏览器资料。
use std::path::Path;

#[cfg(windows)]
use std::{
    ffi::OsString,
    io::{ErrorKind, Read},
    os::windows::fs::MetadataExt,
    path::PathBuf,
    process::{Command, Stdio},
};

#[cfg(any(windows, test))]
use std::time::Duration;

#[cfg(any(windows, test))]
fn parse_active_port(contents: &str) -> Option<String> {
    let mut lines = contents.lines();
    let port = lines
        .next()?
        .parse::<u16>()
        .ok()
        .filter(|port| *port != 0)?;
    let path = lines.next()?;
    let id = path.strip_prefix("/devtools/browser/")?;
    if id.len() != 36 || uuid::Uuid::parse_str(id).is_err() {
        return None;
    }
    Some(format!("ws://127.0.0.1:{port}{path}"))
}

#[cfg(any(windows, test))]
async fn endpoint_is_live(endpoint: &str) -> bool {
    matches!(
        tokio::time::timeout(
            Duration::from_millis(750),
            tokio_tungstenite::connect_async(endpoint),
        )
        .await,
        Ok(Ok(_))
    )
}

#[cfg(windows)]
async fn read_endpoint(profile: &Path) -> Option<String> {
    let port_file = std::fs::File::open(profile.join("DevToolsActivePort")).ok()?;
    let mut contents = String::new();
    port_file.take(256).read_to_string(&mut contents).ok()?;
    let endpoint = parse_active_port(&contents)?;
    endpoint_is_live(&endpoint).await.then_some(endpoint)
}

#[cfg(windows)]
fn chrome_path() -> Option<PathBuf> {
    [
        "ProgramW6432",
        "ProgramFiles",
        "ProgramFiles(x86)",
        "LOCALAPPDATA",
    ]
    .iter()
    .filter_map(std::env::var_os)
    .map(|base| PathBuf::from(base).join("Google/Chrome/Application/chrome.exe"))
    .find(|path| path.is_file())
}

#[cfg(windows)]
pub(crate) async fn endpoint(profile: &Path, launch: bool) -> Result<Option<String>, String> {
    const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x400;
    match std::fs::symlink_metadata(profile) {
        Ok(metadata)
            if !metadata.is_dir()
                || metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0 =>
        {
            return Err("求职 Chrome 资料目录无效".into());
        }
        Ok(_) => {}
        Err(error) if error.kind() == ErrorKind::NotFound => {}
        Err(_) => return Err("无法读取求职 Chrome 资料目录".into()),
    }
    if let Some(existing) = read_endpoint(profile).await {
        return Ok(Some(existing));
    }
    if !launch {
        return Ok(None);
    }

    let executable = chrome_path().ok_or("未找到 Google Chrome，请安装 Chrome 后重试")?;
    std::fs::create_dir_all(profile).map_err(|_| "无法创建求职 Chrome 资料目录")?;
    let mut profile_arg = OsString::from("--user-data-dir=");
    profile_arg.push(profile);
    let mut command = Command::new(executable);
    if let Some(root) = profile.parent() {
        command.env("TEMP", root.join("tmp")).env("TMP", root.join("tmp"));
    }
    command
        .arg(profile_arg)
        .args([
            "--remote-debugging-port=0",
            "--remote-debugging-address=127.0.0.1",
            "--no-first-run",
            "--no-default-browser-check",
            "--new-window",
            "https://www.zhipin.com/web/geek/job",
        ])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|_| "无法打开求职 Chrome 窗口")?;

    let endpoint = tokio::time::timeout(Duration::from_secs(15), async {
        loop {
            if let Some(endpoint) = read_endpoint(profile).await {
                break endpoint;
            }
            tokio::time::sleep(Duration::from_millis(120)).await;
        }
    })
    .await
    .map_err(|_| "求职 Chrome 连接超时，请关闭求职 Chrome 窗口后重试")?;
    Ok(Some(endpoint))
}

#[cfg(not(windows))]
pub(crate) async fn endpoint(_profile: &Path, launch: bool) -> Result<Option<String>, String> {
    if launch {
        Err("当前系统不支持打开求职 Chrome 窗口".into())
    } else {
        Ok(None)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn devtools_address_requires_a_loopback_port_and_browser_id() {
        let path = "/devtools/browser/a1c2e3ab-0000-4000-8000-000000000001";
        assert_eq!(
            parse_active_port(&format!("49321\r\n{path}")).as_deref(),
            Some(format!("ws://127.0.0.1:49321{path}").as_str())
        );
        for invalid in [
            format!("0\n{path}"),
            format!("65536\n{path}"),
            format!("127.0.0.1:49321\n{path}"),
            format!("49321\n{path}?additional=value"),
            "49321\nhttps://example.invalid/devtools/browser/a1c2e3ab-0000-4000-8000-000000000001"
                .into(),
            "49321\n/devtools/page/a1c2e3ab-0000-4000-8000-000000000001".into(),
            "49321\n/devtools/browser/not-an-id".into(),
        ] {
            assert!(parse_active_port(&invalid).is_none(), "{invalid}");
        }
    }

    #[tokio::test]
    async fn a_stale_port_without_a_websocket_handshake_is_rejected() {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let endpoint = format!(
            "ws://{}/devtools/browser/a1c2e3ab-0000-4000-8000-000000000001",
            listener.local_addr().unwrap()
        );
        // TCP 可连接但 WebSocket 未启动时，不能复用残留端口。
        let stale = tokio::time::timeout(Duration::from_secs(2), endpoint_is_live(&endpoint))
            .await
            .unwrap();
        assert!(!stale);
    }
}
