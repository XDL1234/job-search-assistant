//! 仅用于桌面集成测试的离线 App Server 替身，不调用模型或桌面输入。
use serde_json::{json, Value};
use std::io::{self, BufRead, Write};

fn send(value: Value) {
    println!("{value}");
    io::stdout().flush().unwrap();
}

fn main() {
    if std::env::args().any(|arg| arg == "--version") {
        println!("codex-cli 0.156.1");
        return;
    }
    if let Ok(path) = std::env::var("JOB_ASSISTANT_FIXTURE_PID_PATH") {
        std::fs::write(path, std::process::id().to_string()).unwrap();
    }
    for line in io::stdin().lock().lines().map_while(Result::ok) {
        let request: Value = serde_json::from_str(&line).unwrap();
        let method = request["method"].as_str().unwrap_or("");
        if method == "initialize" {
            if let Ok(delay) = std::env::var("JOB_ASSISTANT_FIXTURE_DELAY_MS") {
                std::thread::sleep(std::time::Duration::from_millis(delay.parse().unwrap()));
            }
        }
        if request.get("id").is_none() {
            continue;
        }
        let result = match method {
            "account/read" => json!({"account":{"type":"chatgpt"}}),
            "thread/start" | "thread/resume" => json!({"thread":{"id":"offline-test-thread"}}),
            "turn/start" => json!({"turn":{"id":"offline-test-turn","status":"inProgress"}}),
            _ => json!({}),
        };
        send(json!({"id":request["id"],"result":result}));
        if method == "turn/start" {
            send(
                json!({"method":"turn/started","params":{"threadId":"offline-test-thread","turn":{"id":"offline-test-turn","status":"inProgress"}}}),
            );
            send(
                json!({"method":"turn/completed","params":{"threadId":"offline-test-thread","turn":{"id":"offline-test-turn","status":"failed","error":{"message":"Fixture usage limit","codexErrorInfo":"usageLimitExceeded"}}}}),
            );
        }
    }
}
