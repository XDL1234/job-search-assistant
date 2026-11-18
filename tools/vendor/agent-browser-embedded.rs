//! Job Search Assistant embedding additions to agent-browser v0.43.0.
//! Apache-2.0; upstream streaming and CDP delivery remain unchanged.

const MODE: &str = "JOB_ASSISTANT_EMBEDDED";
const TOKEN: &str = "JOB_ASSISTANT_STREAM_TOKEN";

pub fn is_embedded() -> bool {
    std::env::var(MODE).as_deref() == Ok("1")
}

fn validate_options<'a>(mode: Option<&str>, token: Option<&'a str>) -> Result<Option<&'a str>, String> {
    match (mode, token) {
        (None, None) => Ok(None),
        (Some("1"), Some(value))
            if (32..=256).contains(&value.len())
                && value.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-') =>
        {
            Ok(Some(value))
        }
        _ => Err("Invalid Job Search Assistant embedding credentials".to_string()),
    }
}

pub fn validate_env() -> Result<(), String> {
    let mode = std::env::var(MODE).ok();
    let token = std::env::var(TOKEN).ok();
    validate_options(mode.as_deref(), token.as_deref())?;
    if std::env::var("JOB_ASSISTANT_EXISTING_DAEMON").is_ok_and(|value| value != "1") {
        return Err("Invalid existing-daemon mode".to_string());
    }
    Ok(())
}

pub fn existing_daemon_only() -> bool {
    std::env::var("JOB_ASSISTANT_EXISTING_DAEMON").as_deref() == Ok("1")
}

pub fn daemon_marker() -> String {
    if !is_embedded() {
        return env!("CARGO_PKG_VERSION").to_string();
    }
    use sha2::{Digest, Sha256};
    let token = std::env::var(TOKEN).unwrap_or_default();
    format!("{}+job-assistant.1.{:x}", env!("CARGO_PKG_VERSION"), Sha256::digest(token.as_bytes()))
}

fn token_matches(header: Option<&str>, token: &str) -> bool {
    let Some(received) = header.and_then(|v| v.strip_prefix("Bearer ")) else {
        return false;
    };
    if received.len() != token.len() {
        return false;
    }
    // Fixed-length random credentials; do not return at the first differing byte.
    received.bytes().zip(token.bytes()).fold(0u8, |difference, (a, b)| difference | (a ^ b)) == 0
}

pub fn authorized(header: Option<&str>) -> bool {
    if !is_embedded() {
        return true;
    }
    let Ok(token) = std::env::var(TOKEN) else {
        return false;
    };
    validate_options(Some("1"), Some(&token)).is_ok() && token_matches(header, &token)
}

pub fn allowed_message(embedded: bool, message_type: &str) -> bool {
    !embedded || matches!(message_type, "config" | "ack")
}

pub fn command_with_auth(command: &serde_json::Value) -> Result<serde_json::Value, String> {
    let mut wire = command.clone();
    if is_embedded() {
        validate_env()?;
        let token = std::env::var(TOKEN).map_err(|_| "Missing embedding credentials")?;
        let fields = wire.as_object_mut().ok_or("Invalid command envelope")?;
        fields.insert("__job_assistant_auth".to_string(), serde_json::json!(format!("Bearer {token}")));
    }
    Ok(wire)
}

pub fn take_command_auth(command: &mut serde_json::Value) -> bool {
    // Strip credentials before action dispatch, event broadcasting or logging.
    let header = command.as_object_mut().and_then(|fields| fields.remove("__job_assistant_auth"));
    authorized(header.as_ref().and_then(|value| value.as_str()))
}

pub fn probe() -> serde_json::Value {
    serde_json::json!({
        "name": "agent-browser",
        "version": env!("CARGO_PKG_VERSION"),
        "job_assistant_patch": 1,
        "embedded_auth": true,
        "read_only": true,
        "capabilities": {
            "stream_auth": true,
            "stream_read_only": true,
            "stream_http_disabled": true,
            "command_auth": true,
            "existing_daemon_only": true,
            "jpeg_ack_pacing": true
        }
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn embedding_never_accepts_missing_or_malformed_credentials() {
        assert_eq!(validate_options(None, None), Ok(None));
        let token = "a".repeat(32);
        assert_eq!(validate_options(Some("1"), Some(&token)), Ok(Some(token.as_str())));
        for (mode, value) in [
            (Some("1"), None),
            (Some("0"), Some(token.as_str())),
            (None, Some(token.as_str())),
            (Some("1"), Some("short")),
            (Some("1"), Some("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n")),
        ] {
            assert!(validate_options(mode, value).is_err());
        }
    }

    #[test]
    fn bearer_requires_exact_secret_and_scheme() {
        let token = "x".repeat(64);
        assert!(token_matches(Some(&format!("Bearer {token}")), &token));
        for value in [None, Some("Bearer short"), Some("bearer x"), Some(""), Some("Bearer ")] {
            assert!(!token_matches(value, &token));
        }
        let changed = format!("Bearer {}y", "x".repeat(63));
        assert!(!token_matches(Some(&changed), &token));
    }

    #[test]
    fn media_channel_only_accepts_flow_control_in_embedding() {
        for kind in ["config", "ack"] {
            assert!(allowed_message(true, kind));
        }
        for kind in ["input_mouse", "input_keyboard", "input_touch", "navigate", "command", ""] {
            assert!(!allowed_message(true, kind));
            assert!(allowed_message(false, kind));
        }
    }
}
