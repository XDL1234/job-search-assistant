//! 公司网申共用的 Chrome 观察与操作。模型只能使用当前观察返回的控件编号。
use crate::{dedicated_chrome, web_autofill::Cdp};
use base64::{engine::general_purpose::STANDARD, Engine};
use image::{codecs::jpeg::JpegEncoder, ImageFormat, ImageReader, Limits};
use serde_json::{json, Value};
use std::{io::Cursor, path::Path, time::Duration};
use tauri::Url;

const PAGE_SCRIPT: &str = include_str!("company_page.js");

#[derive(Clone)]
pub(crate) struct CompanyPage {
    endpoint: String,
    origin: String,
}

impl CompanyPage {
    pub(crate) async fn open(root: &Path, url: &str) -> Result<Self, String> {
        let origin = origin(url).ok_or("公司入口必须是安全的网页地址")?;
        let endpoint = dedicated_chrome::endpoint(&root.join("chrome-profile"), true)
            .await?
            .ok_or("无法连接求职专用 Chrome")?;
        let port = Url::parse(&endpoint)
            .ok()
            .filter(|value| value.scheme() == "ws" && value.host_str() == Some("127.0.0.1"))
            .and_then(|value| value.port())
            .ok_or("求职 Chrome 调试地址无效")?;
        let mut chrome = Cdp::open(&endpoint).await?;
        let target = chrome
            .command(
                "Target.createTarget",
                json!({"url": url, "background": false}),
            )
            .await?;
        let target = target["targetId"]
            .as_str()
            .filter(|value| value.len() == 32 && value.bytes().all(|byte| byte.is_ascii_hexdigit()))
            .ok_or("Chrome 未返回有效的公司标签页")?;
        Ok(Self {
            endpoint: format!("ws://127.0.0.1:{port}/devtools/page/{target}"),
            origin,
        })
    }

    async fn main_frame(&self) -> Result<(Cdp, String, String), String> {
        let mut chrome = Cdp::open(&self.endpoint).await?;
        for _ in 0..20 {
            let tree = chrome.command("Page.getFrameTree", json!({})).await?;
            let frame = &tree["frameTree"]["frame"];
            let id = frame["id"].as_str().ok_or("无法定位当前网申文档")?;
            let url = format!(
                "{}{}",
                frame["url"].as_str().unwrap_or_default(),
                frame["urlFragment"].as_str().unwrap_or_default()
            );
            if !url.is_empty() && url != "about:blank" {
                return Ok((chrome, id.into(), url));
            }
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
        Err("公司的招聘网页尚未加载，请稍后重新观察".into())
    }

    pub(crate) async fn observe(&self) -> Result<Value, String> {
        let (mut chrome, frame, url) = self.main_frame().await?;
        if origin(&url).as_deref() != Some(&self.origin) {
            return Ok(
                json!({"url": page_location(&url), "document":null, "controls":[], "text":"",
                "blocked":"页面已跳转到未授权网站，请人工确认登录或招聘平台",
                "receipt":null}),
            );
        }
        let context = world(&mut chrome, &frame).await?;
        let value = evaluate(&mut chrome, context, json!({"url":url}), true).await?;
        let observation = &value["value"];
        if observation["url"] != url || observation["document"].as_str().is_none() {
            return Err("网申页面已变化，请重新读取".into());
        }
        Ok(observation.clone())
    }

    pub(crate) async fn preview_frame(&self) -> Result<Option<String>, String> {
        self.capture_image(None).await
    }

    pub(crate) async fn capture_image(&self, observation: Option<&Value>) -> Result<Option<String>, String> {
        let (mut chrome, frame, url) = self.main_frame().await?;
        if origin(&url).as_deref() != Some(&self.origin) { return Ok(None); }
        let context = world(&mut chrome, &frame).await?;
        let options = if let Some(observation) = observation {
            self.check_page(observation, &url)?;
            json!({"url":url, "document":observation["document"],
                "revision":observation["revision"], "screenshot":true})
        } else {
            json!({"url":url,"preview":true})
        };
        let before = evaluate(&mut chrome, context, options.clone(), true).await?;
        if before["value"] != true && !before["value"].is_string() { return Ok(None); }
        // 采集原始视口，避免 Chrome 为调整预览图片尺寸而重排网页。
        let picture = chrome.command("Page.captureScreenshot",
            json!({"format":"jpeg","quality":75,"fromSurface":true,"captureBeyondViewport":false})).await?;
        let after = evaluate(&mut chrome, context, options, true).await?;
        if after["value"] != before["value"] { return Ok(None); }
        let bytes = resize_capture(picture, 1100, 750).await?;
        Ok(Some(STANDARD.encode(bytes)))
    }

    pub(crate) async fn action(&self, observation: &Value, args: Value) -> Result<Value, String> {
        let (mut chrome, frame, url) = self.main_frame().await?;
        self.check_page(observation, &url)?;
        let context = world(&mut chrome, &frame).await?;
        let kind = args["kind"].as_str().ok_or("缺少网页操作类型")?;
        let upload = kind == "upload";
        let mut command = args.clone();
        if upload {
            command
                .as_object_mut()
                .ok_or("网页操作格式无效")?
                .remove("path");
        }
        let value = evaluate(&mut chrome, context, json!({
            "url":url, "document":observation["document"], "revision":observation["revision"], "action":command,
            "controls":observation["controls"],
        }), !upload).await?;
        if upload {
            let path = args["path"].as_str().ok_or("缺少本轮已授权的上传文件")?;
            let input = value["objectId"]
                .as_str()
                .filter(|_| value["subtype"] == "node")
                .ok_or("上传位置已变化，请重新读取页面")?;
            let expected = observation["controls"]
                .as_array()
                .into_iter()
                .flatten()
                .find(|control| control["ref"] == args["ref"])
                .ok_or("上传位置不在本次观察中")?;
            let name = upload_name(path, expected["accept"].as_str().unwrap_or_default())?;
            chrome
                .command(
                    "DOM.setFileInputFiles",
                    json!({"objectId":input, "files":[path]}),
                )
                .await?;
            let check = chrome.command("Runtime.callFunctionOn", json!({"objectId":input,
                "functionDeclaration":"function(name) { return this.isConnected && this.files?.length === 1 && this.files[0].name === name; }",
                "arguments":[{"value":name}], "returnByValue":true})).await?;
            if check["result"]["value"] != true {
                return Err("浏览器未确认附件仍在控件中，请在网页核对上传结果".into());
            }
            return Ok(json!({"ok":true}));
        }
        if value["value"]["ok"] != true {
            return Err("浏览器没有确认本次操作".into());
        }
        Ok(value["value"].clone())
    }

    fn check_page(&self, observation: &Value, url: &str) -> Result<(), String> {
        if observation["url"] != url
            || !observation["blocked"].is_null()
            || !observation["document"].is_string()
            || origin(url).as_deref() != Some(&self.origin)
        {
            return Err("网页已变化或需要人工处理，请重新读取".into());
        }
        Ok(())
    }
}

async fn resize_capture(picture: Value, width: u32, height: u32) -> Result<Vec<u8>, String> {
    tokio::task::spawn_blocking(move || {
        let data = picture["data"].as_str().ok_or("Chrome 未返回网页画面")?;
        let bytes = STANDARD.decode(data).map_err(|_| "Chrome 截图内容无效")?;
        let mut reader = ImageReader::with_format(Cursor::new(&bytes), ImageFormat::Jpeg);
        let mut limits = Limits::default();
        limits.max_image_width = Some(8192);
        limits.max_image_height = Some(8192);
        limits.max_alloc = Some(128 * 1024 * 1024);
        reader.limits(limits);
        let image = reader.decode().map_err(|_| "Chrome 截图格式或尺寸无效")?;
        if image.width() <= width && image.height() <= height { return Ok(bytes); }
        let image = image.thumbnail(width, height);
        let mut output = Cursor::new(Vec::new());
        JpegEncoder::new_with_quality(&mut output, 65).encode_image(&image)
            .map_err(|_| "无法调整网页画面尺寸")?;
        Ok(output.into_inner())
    }).await.map_err(|_| "无法处理网页截图")?
}

fn origin(raw: &str) -> Option<String> {
    let url = Url::parse(raw).ok()?;
    if !url.username().is_empty() || url.password().is_some() {
        return None;
    }
    let host = url.host_str()?;
    if url.scheme() == "https"
        || (url.scheme() == "http" && matches!(host, "127.0.0.1" | "localhost" | "[::1]"))
    {
        return Some(url.origin().ascii_serialization());
    }
    None
}

fn page_location(raw: &str) -> String {
    Url::parse(raw)
        .ok()
        .filter(|url| matches!(url.scheme(), "https" | "http"))
        .map_or_else(
            || "about:blank".into(),
            |url| format!("{}{}", url.origin().ascii_serialization(), url.path()),
        )
}

async fn world(chrome: &mut Cdp, frame: &str) -> Result<u64, String> {
    chrome
        .command(
            "Page.createIsolatedWorld",
            json!({
                "frameId":frame, "worldName":"job-assistant-company-web"
            }),
        )
        .await?["executionContextId"]
        .as_u64()
        .ok_or_else(|| "无法打开公司网申执行环境".into())
}

async fn evaluate(
    chrome: &mut Cdp,
    context: u64,
    payload: Value,
    by_value: bool,
) -> Result<Value, String> {
    let script = PAGE_SCRIPT.replacen("__COMPANY_FORM_ARGS__", &payload.to_string(), 1);
    let result = chrome.command("Runtime.evaluate", json!({
        "contextId": context, "expression": script, "returnByValue": by_value, "awaitPromise": true,
    })).await?;
    if let Some(exception) = result.get("exceptionDetails") {
        let description = exception["exception"]["description"]
            .as_str()
            .unwrap_or_default();
        // 只回传本模块自用错误码，避免输出网页脚本或用户资料。
        let message = match description
            .lines()
            .next()
            .unwrap_or_default()
            .strip_prefix("Error: WEB_")
        {
            Some("STALE") => "页面或控件已变化，请重新读取",
            Some("BLOCKED") => "页面需要人工处理",
            Some("EXISTING") => "保留网页已有内容",
            Some("CONTROL") => "控件无法安全识别，请人工核对",
            Some("VALUE") => "已保存的值与网页格式或选项不匹配",
            Some("REQUIRED") => "网页还有未完成或无效的必填项",
            _ => "网申网页操作未能核验，请重新读取",
        };
        return Err(message.into());
    }
    Ok(result["result"].clone())
}

fn upload_name(path: &str, accept: &str) -> Result<String, String> {
    let path = Path::new(path);
    let extension = path
        .extension()
        .and_then(|v| v.to_str())
        .unwrap_or_default()
        .to_ascii_lowercase();
    let mime = match extension.as_str() {
        "pdf" => "application/pdf",
        "doc" => "application/msword",
        "docx" => "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        _ => return Err("附件文件格式无法在此控件中核验".into()),
    };
    let suffix = format!(".{extension}");
    if !accept.trim().is_empty()
        && !accept.split(',').any(|v| {
            matches!(
                v.trim().to_ascii_lowercase().as_str(),
                "*/*" | "application/*"
            ) || v.trim().eq_ignore_ascii_case(mime)
                || v.trim().eq_ignore_ascii_case(&suffix)
        })
    {
        return Err("本轮简历格式与网页附件要求不符".into());
    }
    path.file_name()
        .and_then(|v| v.to_str())
        .map(str::to_owned)
        .ok_or_else(|| "简历文件名无效".into())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn origins_and_attachment_formats_are_bounded() {
        assert_eq!(
            origin("https://example.test/path").as_deref(),
            Some("https://example.test")
        );
        assert_eq!(
            origin("http://127.0.0.1:5555/form").as_deref(),
            Some("http://127.0.0.1:5555")
        );
        assert!(origin("http://example.test/").is_none());
        assert!(origin("https://name@example.test/").is_none());
        assert!(upload_name("application.PDF", ".png,application/pdf").is_ok());
        assert!(upload_name("application.PDF", "image/*").is_err());
    }
}
