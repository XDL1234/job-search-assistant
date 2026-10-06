use job_search_desktop::codex::{decode_message, MessageKind, TurnGate};
use serde_json::json;

#[test]
fn responses_notifications_and_requests_are_distinct() {
    assert!(matches!(
        decode_message(r#"{"id":7,"result":{}}"#).unwrap(),
        MessageKind::Response(7, _)
    ));
    assert!(matches!(
        decode_message(r#"{"method":"turn/started","params":{}}"#).unwrap(),
        MessageKind::Notification(_)
    ));
    assert!(matches!(
        decode_message(r#"{"id":"abc","method":"item/tool/call","params":{}}"#).unwrap(),
        MessageKind::ServerRequest(_)
    ));
    assert!(decode_message("not json").is_err());
}

#[test]
fn cancel_during_start_is_preserved() {
    let mut gate = TurnGate::default();
    gate.begin().unwrap();
    gate.cancel();
    assert!(gate.started("t1".into()));
    assert!(!gate.can_act());
    assert_eq!(gate.turn_id.as_deref(), Some("t1"));
    assert!(gate.begin().is_err());
    gate.finished();
    gate.begin().unwrap();
    assert!(!gate.started("t2".into()));
    assert!(gate.can_act());
}

#[test]
fn rpc_error_remains_error() {
    let MessageKind::Response(_, data) =
        decode_message(&json!({"id":1,"error":{"code":-1,"message":"failed"}}).to_string())
            .unwrap()
    else {
        panic!()
    };
    assert!(data.get("error").is_some());
}

#[test]
fn failed_turn_preserves_quota_error() {
    use job_search_desktop::codex::turn_failure;
    let failed = json!({"status":"failed","error":{"message":"Usage exhausted","codexErrorInfo":"usageLimitExceeded"}});
    assert!(turn_failure(&failed).unwrap().contains("额度"));
    assert_eq!(
        turn_failure(&json!({"status":"failed","error":{"message":"Network failed"}})).as_deref(),
        Some("Network failed")
    );
    assert!(turn_failure(&json!({"status":"failed"})).is_some());
    assert!(turn_failure(&json!({"status":"completed","error":null})).is_none());
    assert!(turn_failure(&json!({"status":"interrupted","error":null})).is_none());
}

#[test]
fn reconnect_invalidates_old_event_and_reply_sources() {
    use job_search_desktop::codex::ConnectionEpoch;
    let epoch = ConnectionEpoch::default();
    let old = epoch.advance();
    assert!(epoch.accepts(old));
    let current = epoch.advance();
    assert!(!epoch.accepts(old), "旧连接通知与在途工具回复必须失效");
    assert!(epoch.accepts(current));
    epoch.advance();
    assert!(!epoch.accepts(current), "关闭连接必须拒绝排队事件");
}

#[tokio::test]
async fn transport_handles_partial_lines_timeouts_and_eof() {
    use job_search_desktop::codex::CodexClient;
    use std::time::Duration;
    use tokio::process::Command;
    use tokio::sync::mpsc;
    let mut command =
        Command::new(std::env::var("JOB_ASSISTANT_PYTHON").unwrap_or("python".into()));
    command.arg(concat!(env!("CARGO_MANIFEST_DIR"), "/tests/fake_codex.py"));
    let (sender, mut events) = mpsc::unbounded_channel();
    let client = CodexClient::spawn_command(command, sender).await.unwrap();
    assert_eq!(
        client.request("ping", json!({})).await.unwrap()["pong"],
        true
    );
    assert_eq!(events.recv().await.unwrap()["method"], "test/notification");
    assert_eq!(
        client.request("fail", json!({})).await.unwrap_err(),
        "expected failure"
    );
    assert!(client
        .request_with_timeout("never", json!({}), Duration::from_millis(50))
        .await
        .unwrap_err()
        .contains("超时"));
    assert_eq!(
        client.request("ping", json!({})).await.unwrap()["pong"],
        true
    );
    assert!(client
        .request("die", json!({}))
        .await
        .unwrap_err()
        .contains("关闭"));
    client.close().await;
}
