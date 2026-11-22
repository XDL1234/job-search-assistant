//! 扫描指定的网申标签页，确认后只填仍然空白且能明确匹配的字段。
use crate::{dedicated_chrome, ExecutorCoordinator};
use futures_util::{SinkExt, StreamExt};
use serde_json::{json, Value};
use std::{
    path::Path,
    sync::Arc,
    time::{Duration, Instant},
};
use tauri::{State, Url};
use tokio::{
    io::{AsyncReadExt, AsyncWriteExt},
    net::TcpStream,
    sync::Mutex,
};
use tokio_tungstenite::{
    connect_async_with_config, tungstenite::{protocol::WebSocketConfig, Message}, MaybeTlsStream,
    WebSocketStream,
};

const FORM_SCRIPT: &str = include_str!("web_form.js");
const STEP_TIMEOUT: Duration = Duration::from_secs(4);
const REVIEW_LIFETIME: Duration = Duration::from_secs(300);
const MAX_RESPONSE: usize = 1024 * 1024;
const MAX_SCREENSHOT_RESPONSE: usize = 24 * 1024 * 1024;
const ALIBABA_RESUME: &str = "https://campus-talent.alibaba.com/personal/resume";
const BAIC_RESUME: &str = "https://baicgroup.zhiye.com/form?fromPage=editMyResume";

#[derive(Default)]
pub(crate) struct WebAutofill(Mutex<Option<Review>>);

struct Review {
    id: String,
    tab: Tab,
    document: f64,
    fields: Vec<Value>,
    profile_version: Value,
    operations: Value,
    questions: Value,
    created: Instant,
}

#[derive(Clone, PartialEq)]
struct Tab {
    port: u16,
    target: String,
    url: String,
    title: String,
}

impl Tab {
    fn id(&self) -> String {
        format!("{}:{}", self.port, self.target)
    }

    fn same_page(&self, other: &Self) -> bool {
        self.port == other.port && self.target == other.target && self.url == other.url
    }
}

fn applicant_url(value: &str) -> bool {
    let Ok(url) = Url::parse(value) else {
        return false;
    };
    url.scheme() == "https"
        && url.username().is_empty()
        && url.password().is_none()
        && url.port_or_known_default() == Some(443)
        && matches!(
            (url.host_str(), url.path()),
            (
                Some("campus-talent.alibaba.com"),
                "/personal/resume" | "/personal/resume/"
            ) | (Some("baicgroup.zhiye.com"), "/form" | "/form/")
        )
}

fn browser_port(endpoint: &str) -> Option<u16> {
    let url = Url::parse(endpoint).ok()?;
    if url.scheme() != "ws"
        || url.host_str() != Some("127.0.0.1")
        || !url.username().is_empty()
        || url.password().is_some()
        || url.query().is_some()
        || url.fragment().is_some()
        || uuid::Uuid::parse_str(url.path().strip_prefix("/devtools/browser/")?).is_err()
    {
        return None;
    }
    url.port().filter(|port| *port != 0)
}

async fn manual_endpoint() -> Option<String> {
    tokio::time::timeout(Duration::from_millis(750), async {
        let mut stream = TcpStream::connect(("127.0.0.1", 9222)).await.ok()?;
        stream
            .write_all(
                b"GET /json/version HTTP/1.0\r\nHost: 127.0.0.1:9222\r\nConnection: close\r\n\r\n",
            )
            .await
            .ok()?;
        let mut bytes = Vec::new();
        stream.take(16 * 1024).read_to_end(&mut bytes).await.ok()?;
        let response = std::str::from_utf8(&bytes).ok()?;
        let (headers, body) = response.split_once("\r\n\r\n")?;
        if !headers
            .lines()
            .next()?
            .split(' ')
            .nth(1)
            .is_some_and(|status| status == "200")
        {
            return None;
        }
        let response: Value = serde_json::from_str(body).ok()?;
        let endpoint = response["webSocketDebuggerUrl"].as_str()?;
        (browser_port(endpoint) == Some(9222)).then(|| endpoint.to_owned())
    })
    .await
    .ok()
    .flatten()
}

pub(crate) struct Cdp {
    socket: WebSocketStream<MaybeTlsStream<TcpStream>>,
    sequence: u64,
}

impl Cdp {
    pub(crate) async fn open(endpoint: &str) -> Result<Self, String> {
        let config = WebSocketConfig::default()
            .max_message_size(Some(MAX_SCREENSHOT_RESPONSE))
            .max_frame_size(Some(MAX_SCREENSHOT_RESPONSE));
        let (socket, _) = tokio::time::timeout(STEP_TIMEOUT, connect_async_with_config(endpoint, Some(config), false))
            .await
            .map_err(|_| "Chrome 连接超时")?
            .map_err(|_| "无法连接指定的 Chrome 标签页")?;
        Ok(Self {
            socket,
            sequence: 0,
        })
    }

    pub(crate) async fn command(&mut self, method: &str, params: Value) -> Result<Value, String> {
        let maximum = if method == "Page.captureScreenshot" { MAX_SCREENSHOT_RESPONSE } else { MAX_RESPONSE };
        self.sequence += 1;
        let id = self.sequence;
        let payload = json!({"id": id, "method": method, "params": params}).to_string();
        tokio::time::timeout(STEP_TIMEOUT, async {
            self.socket
                .send(Message::Text(payload.into()))
                .await
                .map_err(|_| "Chrome 连接已断开")?;
            while let Some(message) = self.socket.next().await {
                let message = message.map_err(|_| "Chrome 连接已断开")?;
                if !message.is_text() {
                    continue;
                }
                let text = message.to_text().map_err(|_| "Chrome 响应无效")?;
                if text.len() > maximum {
                    return Err("网申页面响应超过大小限制");
                }
                let response: Value = serde_json::from_str(text).map_err(|_| "Chrome 响应无效")?;
                if response["id"].as_u64() == Some(id) {
                    return response.get("result").cloned().ok_or("Chrome 未执行该操作");
                }
            }
            Err("Chrome 标签页已关闭")
        })
        .await
        .map_err(|_| "Chrome 操作超时")?
        .map_err(String::from)
    }
}

async fn discover(root: &Path) -> Result<Vec<Tab>, String> {
    let profile = root.join("chrome-profile");
    let (managed, manual) = tokio::join!(
        dedicated_chrome::endpoint(&profile, false),
        manual_endpoint()
    );
    let mut tabs = Vec::new();
    let mut connected = false;
    let managed = managed.ok().flatten();
    for endpoint in [
        managed.as_ref(),
        manual
            .as_ref()
            .filter(|value| Some(*value) != managed.as_ref()),
    ]
    .into_iter()
    .flatten()
    {
        let Some(port) = browser_port(endpoint) else {
            continue;
        };
        let Ok(mut chrome) = Cdp::open(endpoint).await else {
            continue;
        };
        let Ok(targets) = chrome.command("Target.getTargets", json!({})).await else {
            continue;
        };
        let Some(targets) = targets["targetInfos"].as_array() else {
            continue;
        };
        connected = true;
        tabs.extend(targets.iter().filter_map(|target| {
            let id = target["targetId"].as_str()?;
            let url = target["url"].as_str()?;
            if target["type"] != "page"
                || id.len() != 32
                || !id.bytes().all(|b| b.is_ascii_hexdigit())
                || url.len() > 4096
                || !applicant_url(url)
            {
                return None;
            }
            Some(Tab {
                port,
                target: id.into(),
                url: url.into(),
                title: target["title"]
                    .as_str()
                    .unwrap_or("")
                    .chars()
                    .take(120)
                    .collect(),
            })
        }));
    }
    if !connected {
        return Err("请先连接求职专用 Chrome，再打开招聘简历页".into());
    }
    Ok(tabs)
}

async fn selected_tab(root: &Path, tab_id: &str) -> Result<Tab, String> {
    discover(root)
        .await?
        .into_iter()
        .find(|tab| tab.id() == tab_id)
        .ok_or_else(|| "选定的网申标签页已关闭或离开简历页，请刷新后重新选择".into())
}

#[tauri::command]
pub(crate) async fn web_autofill_open(
    runtime: State<'_, Arc<ExecutorCoordinator>>,
    site: String,
) -> Result<Value, String> {
    runtime.check_browser_change()?;
    let url = match site.as_str() {
        "alibaba" => ALIBABA_RESUME,
        "baic" => BAIC_RESUME,
        _ => return Err("请选择受支持的网申网站".into()),
    };
    let profile = runtime.worker.root.join("chrome-profile");
    let endpoint = dedicated_chrome::endpoint(&profile, true)
        .await?
        .ok_or("无法打开求职专用 Chrome")?;
    let mut chrome = Cdp::open(&endpoint).await?;
    runtime.check_browser_change()?;
    chrome
        .command("Target.createTarget", json!({"url":url,"background":false}))
        .await?;
    Ok(json!({"url":url}))
}

async fn form_context(tab: &Tab) -> Result<(Cdp, u64), String> {
    let endpoint = format!("ws://127.0.0.1:{}/devtools/page/{}", tab.port, tab.target);
    let mut chrome = Cdp::open(&endpoint).await?;
    let tree = chrome.command("Page.getFrameTree", json!({})).await?;
    let frame = &tree["frameTree"]["frame"];
    let Some(frame_id) = frame["id"].as_str() else {
        return Err("无法定位网申页主文档".into());
    };
    let actual = format!(
        "{}{}",
        frame["url"].as_str().unwrap_or_default(),
        frame["urlFragment"].as_str().unwrap_or_default()
    );
    if actual != tab.url {
        return Err("网申页已跳转，请重新扫描".into());
    }
    let world = chrome
        .command(
            "Page.createIsolatedWorld",
            json!({"frameId": frame_id, "worldName":"job-assistant-web-autofill"}),
        )
        .await?;
    let context = world["executionContextId"]
        .as_u64()
        .ok_or("无法打开网申页执行环境")?;
    Ok((chrome, context))
}

fn form_expression(tab: &Tab, document: Option<f64>, payload: Value, result: &str) -> String {
    let script = FORM_SCRIPT.replacen("__JOB_FORM_ARGS__", &payload.to_string(), 1);
    format!(
        "(async()=>{{const documentIdentity=performance.timeOrigin; const expected={}; const url={};\n\
         if (window!==window.top || location.href!==url || (expected!==null && documentIdentity!==expected)) return {{stale:true}};\n\
         const result=await ({}); return {result};}})()",
        json!(document), json!(tab.url), script.trim_end().trim_end_matches(';')
    )
}

async fn run_script(
    tab: &Tab,
    document: Option<f64>,
    payload: Value,
) -> Result<(f64, Value), String> {
    let (mut chrome, context) = form_context(tab).await?;
    let expression = form_expression(tab, document, payload, "{document:documentIdentity,result}");
    let result = chrome.command("Runtime.evaluate",
        json!({"contextId":context,"expression":expression,"awaitPromise":true,"returnByValue":true})).await?;
    if result.get("exceptionDetails").is_some() {
        return Err("网申控件可能已变化，请检查网页并重新扫描".into());
    }
    let value = &result["result"]["value"];
    if value["stale"] == true {
        return Err("网申页已跳转或刷新，请重新扫描".into());
    }
    let document = value["document"].as_f64().ok_or("未收到网申页有效响应")?;
    Ok((document, value["result"].clone()))
}

async fn fill_attachment(
    tab: &Tab,
    document: f64,
    fields: &[Value],
    id: &str,
    file: &str,
) -> Result<(), String> {
    let (mut chrome, context) = form_context(tab).await?;
    let expression = form_expression(
        tab,
        Some(document),
        json!({"url":tab.url,"fields":fields,"fileId":id}),
        "result",
    );
    let control = chrome
        .command(
            "Runtime.evaluate",
            json!({
                "contextId":context,"expression":expression,"awaitPromise":true
            }),
        )
        .await?;
    let object = &control["result"];
    let object_id = object["objectId"]
        .as_str()
        .filter(|_| object["subtype"] == "node")
        .ok_or("附件控件已变化，请重新扫描")?;
    chrome
        .command(
            "DOM.setFileInputFiles",
            json!({"objectId":object_id,"files":[file]}),
        )
        .await?;
    let name = Path::new(file)
        .file_name()
        .and_then(|name| name.to_str())
        .ok_or("附件文件名无效")?;
    let check = chrome.command("Runtime.callFunctionOn", json!({
        "objectId": object_id,
        "functionDeclaration": "function(expected) { return this.files?.length === 1 && this.files[0].name === expected; }",
        "arguments": [{"value": name}], "returnByValue": true,
    })).await?;
    if check["result"]["value"] != true {
        return Err("请在网页核对附件状态".into());
    }
    Ok(())
}

async fn scan_fields(tab: &Tab, document: Option<f64>) -> Result<(f64, Vec<Value>), String> {
    let (document, result) = run_script(tab, document, json!({"url":tab.url})).await?;
    let fields = result["fields"].as_array().ok_or("网申页未返回字段")?;
    if fields.is_empty() || fields.len() > 250 {
        return Err("当前页未找到可识别的网申字段。请打开简历填写区域后重试".into());
    }
    Ok((document, fields.clone()))
}

#[tauri::command]
pub(crate) async fn web_autofill_tabs(
    runtime: State<'_, Arc<ExecutorCoordinator>>,
) -> Result<Value, String> {
    runtime.check_browser_change()?;
    Ok(Value::Array(
        discover(&runtime.worker.root)
            .await?
            .into_iter()
            .map(|tab| json!({"id":tab.id(),"title":tab.title,"url":tab.url}))
            .collect(),
    ))
}

#[tauri::command]
pub(crate) async fn web_autofill_scan(
    runtime: State<'_, Arc<ExecutorCoordinator>>,
    plans: State<'_, WebAutofill>,
    tab_id: String,
) -> Result<Value, String> {
    let mut plan = plans.0.lock().await;
    runtime.check_browser_change()?;
    *plan = None;
    let tab = selected_tab(&runtime.worker.root, &tab_id).await?;
    let (document, fields) = scan_fields(&tab, None).await?;
    let prepared = runtime
        .worker
        .call("prepare_web_autofill", json!({"fields":fields}))
        .await?;
    let id = uuid::Uuid::new_v4().to_string();
    let view = json!({"scanId":id,"url":tab.url,"fields":prepared["fields"]});
    *plan = Some(Review {
        id,
        tab,
        document,
        fields,
        profile_version: prepared["profile_version"].clone(),
        operations: prepared["operations"].clone(),
        questions: advice_questions(&prepared["fields"]),
        created: Instant::now(),
    });
    Ok(view)
}

#[tauri::command]
pub(crate) async fn web_autofill_advice(
    runtime: State<'_, Arc<ExecutorCoordinator>>,
    plans: State<'_, WebAutofill>,
    tab_id: String,
    scan_id: String,
) -> Result<Value, String> {
    runtime.check_browser_change()?;
    let questions = {
        let plan = plans.0.lock().await;
        plan.as_ref()
            .filter(|review| valid_review(review, &tab_id, &scan_id))
            .ok_or("填充预览已失效，请重新扫描")?
            .questions
            .clone()
    };
    if questions.as_array().is_some_and(Vec::is_empty) {
        return Ok(json!({"suggestions":[]}));
    }
    runtime.web_form_advice(questions).await
}

fn advice_questions(fields: &Value) -> Value {
    fn safe_text(value: &Value, limit: usize) -> Option<String> {
        let text = value.as_str()?.trim();
        if text.chars().count() > limit
            || text.chars().any(char::is_control)
            || text.contains('@')
            || text.contains("://")
            || text
                .split(|c: char| !c.is_ascii_digit())
                .any(|part| part.len() >= 6)
        {
            return None;
        }
        Some(text.to_owned())
    }
    Value::Array(fields.as_array().into_iter().flatten().filter_map(|field| {
        if !matches!(field["status"].as_str(), Some("manual" | "unmapped")) {
            return None;
        }
        let id = field["id"].as_str()?;
        id.strip_prefix("field-")?.parse::<u16>().ok().filter(|number| *number < 250)?;
        let label = safe_text(&field["label"], 60).filter(|label| !label.is_empty())?;
        let section = safe_text(&field["section"], 40).unwrap_or_default();
        Some(json!({"id":id,"label":label,"section":section,
            "reason": if field["status"] == "manual" {"控件或经历条目需要核对"} else {"未匹配已保存字段"}}))
    }).take(15).collect())
}

fn valid_review(review: &Review, tab_id: &str, scan_id: &str) -> bool {
    review.id == scan_id && review.tab.id() == tab_id && review.created.elapsed() <= REVIEW_LIFETIME
}

#[tauri::command]
pub(crate) async fn web_autofill_fill(
    runtime: State<'_, Arc<ExecutorCoordinator>>,
    plans: State<'_, WebAutofill>,
    tab_id: String,
    scan_id: String,
    field_ids: Vec<String>,
) -> Result<Value, String> {
    let mut plan = plans.0.lock().await;
    runtime.check_browser_change()?;
    if !plan
        .as_ref()
        .is_some_and(|review| valid_review(review, &tab_id, &scan_id))
    {
        return Err("填充预览已失效，请重新扫描".into());
    }
    let review = plan.take().expect("review checked above");
    let tab = selected_tab(&runtime.worker.root, &tab_id).await?;
    if !review.tab.same_page(&tab) {
        return Err("网申页已跳转，请重新扫描".into());
    }
    let (_, fields) = scan_fields(&tab, Some(review.document)).await?;
    if fields != review.fields {
        return Err("网页字段或已有内容的状态已变化，请重新扫描".into());
    }
    let prepared = runtime
        .worker
        .call("prepare_web_autofill", json!({"fields":fields}))
        .await?;
    if prepared["profile_version"] != review.profile_version {
        return Err("个人资料已变化，请重新扫描".into());
    }
    if prepared["operations"] != review.operations {
        return Err("网申附件已变化，请重新扫描".into());
    }
    let allowed = prepared["operations"]
        .as_array()
        .ok_or("无法生成安全的填充计划")?;
    let operations = select_operations(allowed, &field_ids)?;
    if operations.is_empty() {
        return Ok(json!({"filled":[],"skipped":[]}));
    }
    runtime.check_browser_change()?;
    let (attachments, inputs): (Vec<_>, Vec<_>) = operations
        .into_iter()
        .partition(|operation| operation["file"].is_string());
    let (_, result) = run_script(
        &tab,
        Some(review.document),
        json!({"url":tab.url,"fields":fields,"operations":inputs}),
    )
    .await?;
    let (Some(mut filled), Some(mut skipped)) = (
        result["filled"].as_array().cloned(),
        result["skipped"].as_array().cloned(),
    ) else {
        return Err("未收到准确的填写结果，请检查当前网页".into());
    };
    for operation in attachments {
        let id = operation["id"].as_str().ok_or("附件字段无效")?;
        let file = operation["file"].as_str().ok_or("附件文件无效")?;
        if runtime.check_browser_change().is_ok()
            && fill_attachment(&tab, review.document, &fields, id, file)
                .await
                .is_ok()
        {
            filled.push(json!(id));
        } else {
            skipped.push(json!(id));
        }
    }
    Ok(json!({"filled":filled,"skipped":skipped}))
}

fn select_operations<'a>(
    operations: &'a [Value],
    ids: &[String],
) -> Result<Vec<&'a Value>, String> {
    if ids.len() > operations.len()
        || ids
            .iter()
            .any(|id| !operations.iter().any(|op| op["id"] == *id))
    {
        return Err("所选字段不在可填充的预览中，请重新扫描".into());
    }
    Ok(operations
        .iter()
        .filter(|op| ids.iter().any(|id| op["id"] == *id))
        .collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn applicant_urls_exclude_other_origins_and_pages() {
        assert!(applicant_url(
            "https://baicgroup.zhiye.com/form?fromPage=editMyResume"
        ));
        assert!(applicant_url(
            "https://campus-talent.alibaba.com/personal/resume"
        ));
        for url in [
            "https://baicgroup.zhiye.com.evil.invalid/form",
            "http://baicgroup.zhiye.com/form",
            "https://baicgroup.zhiye.com:8000/form",
            "https://p@baicgroup.zhiye.com/form",
            "https://campus-talent.alibaba.com/personal/resume/submission",
            "https://www.zhipin.com/form",
            "file:///form",
        ] {
            assert!(!applicant_url(url));
        }
    }

    #[test]
    fn browser_endpoint_is_loopback_and_a_browser_not_a_page() {
        let browser = "/devtools/browser/00000000-0000-4000-8000-000000000001";
        assert_eq!(
            browser_port(&format!("ws://127.0.0.1:9222{browser}")),
            Some(9222)
        );
        assert_eq!(
            browser_port(&format!("ws://evil.invalid:9222{browser}")),
            None
        );
        assert_eq!(browser_port("ws://127.0.0.1:9222/devtools/page/xyz"), None);
    }

    #[test]
    fn field_selection_only_accepts_planned_ids() {
        let plan = vec![
            json!({"id":"name","value":"合成姓名"}),
            json!({"id":"school","value":"合成学校"}),
        ];
        assert_eq!(
            select_operations(&plan, &["school".into()]).unwrap(),
            vec![&plan[1]]
        );
        assert!(select_operations(&plan, &["submit".into()]).is_err());
        assert!(select_operations(&plan, &[]).unwrap().is_empty());
    }

    #[test]
    fn advice_only_uses_bounded_non_value_metadata() {
        let fields = json!([
            {"id":"field-0","label":"联系电话","status":"ready","value":"synthetic"},
            {"id":"field-1","label":"专业方向","section":"教育经历","status":"manual","value":"synthetic"},
            {"id":"field-2","label":"private@example.invalid","status":"unmapped"},
            {"id":"field-3","label":"手机号13800000000","status":"unmapped"},
            {"id":"field-900","label":"未知","status":"unmapped"}
        ]);
        assert_eq!(
            advice_questions(&fields),
            json!([
                {"id":"field-1","label":"专业方向","section":"教育经历","reason":"控件或经历条目需要核对"}
            ])
        );
    }
}
