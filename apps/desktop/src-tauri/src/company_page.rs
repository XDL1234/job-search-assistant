//! 公司网申共用的 Chrome 观察与操作。模型只能使用当前观察返回的控件编号。
use crate::{dedicated_chrome, web_autofill::Cdp};
use base64::{engine::general_purpose::STANDARD, Engine};
use image::{codecs::jpeg::JpegEncoder, ImageFormat, ImageReader, Limits};
use serde_json::{json, Value};
use std::{collections::HashSet, io::{Cursor, Read}, path::Path, time::Duration};
use tauri::Url;

const PAGE_SCRIPT: &str = include_str!("company_page.js");

#[derive(Clone)]
pub(crate) struct CompanyPage {
    endpoint: String,
    browser: String,
    target: String,
    origin: String,
    tenant_path: Option<String>,
    company: String,
}

pub(crate) struct CompanyBrowser {
    endpoint: String,
    pub(crate) targets: HashSet<String>,
}

fn valid_target(id: &str) -> bool {
    id.len() == 32 && id.bytes().all(|byte| byte.is_ascii_hexdigit())
}

impl CompanyBrowser {
    // 预览或网络握手暂时超时不会清掉草稿绑定。只读专用本机端口，连接被拒绝时再复核一次。
    pub(crate) async fn confirmed_closed(root: &Path, binding: &str) -> bool {
        let file = root.join("chrome-profile/DevToolsActivePort");
        for retry in 0..2 {
            match std::fs::File::open(&file) {
                Ok(file) => {
                    let mut content = String::new();
                    if file.take(256).read_to_string(&mut content).is_err() { return false; }
                    let mut lines = content.lines();
                    let Some(port) = lines.next().and_then(|s| s.parse::<u16>().ok()).filter(|v| *v != 0) else {return false};
                    if lines.next().and_then(|s| s.strip_prefix("/devtools/browser/")) != binding.split(':').next() {
                        return false;
                    }
                    let attempt = tokio::time::timeout(Duration::from_millis(500),
                        tokio::net::TcpStream::connect((std::net::Ipv4Addr::LOCALHOST,port))).await;
                    if !matches!(attempt, Ok(Err(ref e)) if e.kind() == std::io::ErrorKind::ConnectionRefused) {return false;}
                }
                Err(e) if e.kind() == std::io::ErrorKind::NotFound => (),
                Err(_) => return false,
            }
            if retry == 0 { tokio::time::sleep(Duration::from_millis(250)).await; }
        }
        true
    }

    pub(crate) async fn connect(root: &Path, launch: bool) -> Result<Self, String> {
        let endpoint = dedicated_chrome::company_endpoint(&root.join("chrome-profile"), launch).await?
            .ok_or("求职 Chrome 已关闭，请重新打开本批网页")?;
        let mut chrome = Cdp::open(&endpoint).await?;
        let list = chrome.command("Target.getTargets", json!({})).await?;
        let targets = list["targetInfos"].as_array().ok_or("无法读取 Chrome 标签列表")?
            .iter().filter(|info| info["type"] == "page").filter_map(|info| info["targetId"].as_str())
            .filter(|id| valid_target(id)).map(str::to_owned).collect();
        Ok(Self { endpoint, targets })
    }

    fn id(&self) -> &str {
        self.endpoint.rsplit('/').next().unwrap_or_default()
    }

    pub(crate) fn live_targets<'a>(&'a self, binding: &'a str) -> impl Iterator<Item=&'a str> + 'a {
        let mut parts = binding.split(':');
        let matches_browser = parts.next() == Some(self.id());
        parts.filter(move |id| matches_browser && self.targets.contains(*id))
    }

    pub(crate) fn restore(&self, binding: &str, url: &str, company: &str) -> Option<CompanyPage> {
        let mut parts = binding.split(':');
        if parts.next()? != self.id() { return None; }
        let target = parts.next()?;
        if !self.targets.contains(target) { return None; }
        self.page(target, url, company).ok()
    }

    pub(crate) async fn loading_targets(&self, targets: &HashSet<String>) -> HashSet<String> {
        let mut loading = HashSet::new();
        for id in targets {
            let Ok(endpoint) = self.page_endpoint(id) else {continue};
            // 这里只检查已绑定标签的浏览器加载状态，不读取其他任务的页面内容。
            let complete = tokio::time::timeout(Duration::from_millis(600), async {
                let mut cdp = Cdp::open(&endpoint).await?;
                cdp.command("Runtime.evaluate",json!({"expression":"document.readyState === 'complete'","returnByValue":true})).await
            }).await;
            if !matches!(complete, Ok(Ok(ref value)) if value["result"]["value"] == true) { loading.insert(id.clone()); }
        }
        loading
    }

    fn page_endpoint(&self, target: &str) -> Result<String,String> {
        let port = Url::parse(&self.endpoint).ok()
            .filter(|value| value.scheme() == "ws" && value.host_str() == Some("127.0.0.1"))
            .and_then(|value| value.port()).ok_or("求职 Chrome 调试地址无效")?;
        if !valid_target(target) { return Err("Chrome 未返回有效的公司标签页".into()); }
        Ok(format!("ws://127.0.0.1:{port}/devtools/page/{target}"))
    }

    fn page(&self, target: &str, url: &str, company: &str) -> Result<CompanyPage, String> {
        let origin = origin(url).ok_or("公司入口必须是安全的网页地址")?;
        let tenant_path = tenant_destination(url).map(|(_,path)| path);
        Ok(CompanyPage { endpoint:self.page_endpoint(target)?,
            browser:self.endpoint.clone(), target:target.into(), origin, tenant_path, company:company.into() })
    }

    pub(crate) async fn open(&mut self, url: &str, company: &str, background: bool) -> Result<CompanyPage, String> {
        self.create(url, company, background, false).await
    }

    pub(crate) async fn reserve(&mut self, url: &str, company: &str, background: bool) -> Result<CompanyPage, String> {
        self.create(url, company, background, true).await
    }

    async fn create(&mut self, url: &str, company: &str, background: bool, blank: bool) -> Result<CompanyPage, String> {
        origin(url).ok_or("公司入口必须是安全的网页地址")?;
        let mut chrome = Cdp::open(&self.endpoint).await?;
        let result = chrome.command("Target.createTarget",json!({"url":if blank {"about:blank"} else {url},"background":background})).await?;
        let target = result["targetId"].as_str().ok_or("Chrome 未返回公司标签页")?;
        let page = self.page(target, url, company)?;
        self.targets.insert(target.into());
        Ok(page)
    }
}

impl CompanyPage {
    pub(crate) fn with_destination(mut self, url: &str) -> Result<Self,String> {
        let (trusted_origin, tenant_path) = tenant_destination(url).ok_or("目标招聘平台的租户无法核对")?;
        self.origin = trusted_origin;
        self.tenant_path = Some(tenant_path);
        Ok(self)
    }

    fn permits(&self, url: &str) -> bool {
        if origin(url).as_deref() != Some(&self.origin) { return false; }
        self.tenant_path.as_deref().is_none_or(|prefix| {
            Url::parse(url).is_ok_and(|url| {
                let path = url.path().to_ascii_lowercase();
                !["%2f","%5c","%2e","%25"].iter().any(|unsafe_part| path.contains(unsafe_part)) &&
                    (url.path() == prefix.trim_end_matches('/') || url.path().starts_with(prefix))
            })
        })
    }

    pub(crate) async fn open(root: &Path, url: &str, company: &str) -> Result<Self, String> {
        CompanyBrowser::connect(root, true).await?.open(url, company, false).await
    }

    pub(crate) fn binding(&self) -> String {
        format!("{}:{}", self.browser.rsplit('/').next().unwrap_or_default(), self.target)
    }

    pub(crate) async fn activate(&self) -> Result<(), String> {
        Cdp::open(&self.browser).await?.command("Target.activateTarget", json!({"targetId":self.target})).await?;
        Ok(())
    }

    pub(crate) async fn new_children(&self, previous: &HashSet<String>) -> Result<Vec<(String,String)>,String> {
        let mut browser = Cdp::open(&self.browser).await?;
        for _ in 0..5 {
            let result = browser.command("Target.getTargets",json!({})).await?;
            let children = result["targetInfos"].as_array().ok_or("无法读取网申弹窗")?.iter()
                .filter(|info| info["type"] == "page" && info["openerId"] == self.target)
                .filter_map(|info| Some((info["targetId"].as_str()?,info["url"].as_str()?)))
                .filter(|(id,_)| valid_target(id) && !previous.contains(*id))
                .map(|(id,url)| (id.to_owned(),url.to_owned())).collect::<Vec<_>>();
            if !children.is_empty() { return Ok(children); }
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
        Ok(vec![])
    }

    pub(crate) fn same_origin(&self, url: &str) -> bool {
        self.permits(url)
    }

    pub(crate) async fn navigate_blank(&self, url: &str) -> Result<(),String> {
        if !self.permits(url) { return Err("公司网页地址不在授权范围".into()); }
        let mut cdp = Cdp::open(&self.endpoint).await?;
        let tree = cdp.command("Page.getFrameTree",json!({})).await?;
        if tree["frameTree"]["frame"]["url"] == "about:blank" {
            let response = cdp.command("Page.navigate",json!({"url":url})).await?;
            if response["errorText"].is_string() { return Err("招聘入口暂时无法打开，请在该标签检查".into()); }
        }
        Ok(())
    }

    pub(crate) async fn discard_blank(&self) {
        if let Ok(mut cdp) = Cdp::open(&self.endpoint).await {
            if let Ok(tree) = cdp.command("Page.getFrameTree",json!({})).await {
                if tree["frameTree"]["frame"]["url"] == "about:blank" {
                    let _ = cdp.command("Page.close",json!({})).await;
                }
            }
        }
    }

    pub(crate) async fn loaded(&self) -> bool {
        tokio::time::timeout(Duration::from_secs(4), async {
            let Ok((mut cdp, frame, _)) = self.main_frame().await else { return false };
            let Ok(id) = world(&mut cdp, &frame).await else { return false };
            for _ in 0..16 {
                let read = cdp.command("Runtime.evaluate",json!({"contextId":id,"expression":"document.readyState","returnByValue":true})).await;
                if read.is_ok_and(|r| r["result"]["value"] == "complete") { return true; }
                tokio::time::sleep(Duration::from_millis(200)).await;
            }
            false
        }).await.unwrap_or(false)
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
        if !self.permits(&url) {
            let login = Url::parse(&url).is_ok_and(|u| u.path_segments().into_iter().flatten()
                .any(|segment| matches!(segment.to_ascii_lowercase().as_str(), "login"|"signin"|"sso"|"captcha"|"passport")));
            return Ok(
                json!({"url": page_location(&url), "document":null, "controls":[], "text":"",
                "blocked":"页面已跳转到未授权网站，请人工确认登录或招聘平台",
                "loginRequired":login,
                "receipt":null}),
            );
        }
        let context = world(&mut chrome, &frame).await?;
        let value = evaluate(&mut chrome, context, json!({"url":url,"company":self.company,"tenantPath":self.tenant_path}), true).await?;
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
        if !self.permits(&url) { return Ok(None); }
        let context = world(&mut chrome, &frame).await?;
        let options = if let Some(observation) = observation {
            self.check_page(observation, &url)?;
            json!({"url":url, "company":self.company,"tenantPath":self.tenant_path, "document":observation["document"],
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
        let in_place = kind == "formNode";
        let mut command = args.clone();
        if upload {
            command
                .as_object_mut()
                .ok_or("网页操作格式无效")?
                .remove("path");
        }
        let value = evaluate(&mut chrome, context, json!({
            "url":url, "company":self.company,"tenantPath":self.tenant_path, "document":observation["document"], "revision":observation["revision"], "action":command,
            "controls":observation["controls"],
        }), !(upload || in_place)).await?;
        if in_place {
            let element = value["objectId"].as_str().filter(|_| value["subtype"] == "node")
                .ok_or("当前网申入口已变化")?;
            let node = chrome.command("DOM.describeNode",json!({"objectId":element})).await?;
            let backend = node["node"]["backendNodeId"].as_u64().ok_or("无法定位当前入口")?;
            // 点击同一个已核验的实际按钮。主文档短暂接管弹窗请求，让已打开的五家公司仍可原位进入表单。
            let main = chrome.command("DOM.resolveNode",json!({"backendNodeId":backend})).await?;
            let main = main["object"]["objectId"].as_str().ok_or("当前网申入口已失效")?;
            let result = chrome.command("Runtime.callFunctionOn",json!({"objectId":main,
                "functionDeclaration":include_str!("company_in_place.js"),
                "arguments":[{"value":url}],"awaitPromise":true,"returnByValue":true})).await;
            // 本页导航销毁执行环境也可能结束此次调用，后续须重新观察，禁止重放点击。
            let result = match result {
                Ok(v) if v["exceptionDetails"].is_null() && v["result"]["value"]["ok"] == true => v,
                _ => {
                    let changed = self.observe().await.is_ok_and(|view|
                        view["url"] != observation["url"] || view["document"] != observation["document"]);
                    return if changed { Ok(json!({"ok":true})) }
                        else { Err("网申入口已触发，请重新读取当前网页核对结果".into()) };
                }
            };
            let destination = &result["result"]["value"]["popup"];
            if destination.is_null() { return Ok(json!({"ok":true})); }
            let destination = destination.as_str().ok_or("入口需要新增窗口，请在 Chrome 中核对")?;
            if !self.permits(destination) || !safe_form_navigation(destination) {
                return Err("入口需要新增窗口且目的地址无法核对，请在 Chrome 中处理".into());
            }
            self.check_page(observation,&self.main_frame().await?.2)?;
            let navigation = chrome.command("Page.navigate",json!({"url":destination})).await?;
            if navigation["errorText"].is_string() { return Err("表单地址无法打开，请重新检查当前网页".into()); }
            return Ok(json!({"ok":true}));
        }
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
        if let Some(reference) = value["value"]["completeChoice"].as_str() {
            let mut follow = json!({"url":url,"document":observation["document"],"company":self.company,
                "tenantPath":self.tenant_path,"completeChoice":reference,"parentHeadings":observation["headings"],
                "parentJobLabels":observation["jobLabels"]});
            let mut error = String::from("未确认联想选项已选中，请在网页核对");
            for _ in 0..32 {
                let step = evaluate(&mut chrome,context,follow.clone(),true).await;
                match step {
                    Ok(value) if value["value"]["selected"] == true => return Ok(json!({"ok":true})),
                    Ok(value) if value["value"]["waiting"] == true => {
                        tokio::time::sleep(Duration::from_millis(125)).await;
                    }
                    Err(reason) => { error = reason; break; }
                    _ => break,
                }
            }
            follow["abandon"] = json!(true);
            let _ = evaluate(&mut chrome,context,follow,true).await;
            return Err(error);
        }
        Ok(value["value"].clone())
    }

    fn check_page(&self, observation: &Value, url: &str) -> Result<(), String> {
        if observation["url"] != url
            || !observation["blocked"].is_null()
            || !observation["document"].is_string()
            || !self.permits(url)
        {
            return Err("网页已变化或需要人工处理，请重新读取".into());
        }
        Ok(())
    }
}

fn safe_form_navigation(raw: &str) -> bool {
    let Ok(url) = Url::parse(raw) else { return false; };
    let path = url.path().to_ascii_lowercase();
    // 只跟随可辨认的表单/编辑/职位详情页面；有副作用或语义不明的地址交给用户。
    let segments = path.split('/').collect::<Vec<_>>();
    !path.contains('%') && !["submit","delete","withdraw","oneclick","quickapply","confirm"].iter().any(|s| path.contains(s)) &&
        url.query_pairs().all(|(key,value)| match key.to_ascii_lowercase().as_str() {
            "stage" => matches!(value.as_ref(),"edit"|"form"|"details"|"review"),
            "id"|"job"|"jobid"|"job_id"|"positionid"|"position_id"|"requisitionid"|"lang"|"locale" =>
                !value.is_empty() && value.len() <= 120 && value.bytes().all(|c| c.is_ascii_alphanumeric() || matches!(c,b'-'|b'_')),
            _ => false,
        }) &&
        segments.iter().any(|s| matches!(*s,"form"|"application-form"|"edit"|"resume"|"job"|"jobs"|"position"|"positions"))
}

// 已知招聘平台只有独立租户子域或路径才可承接企业官网中的表单入口。
pub(crate) fn tenant_destination(raw: &str) -> Option<(String,String)> {
    let url = Url::parse(raw).ok()?;
    let host = url.host_str()?;
    if url.scheme() != "https" || url.port().is_some() || !url.username().is_empty() || url.password().is_some() {
        return None;
    }
    let safe_part = |s: &str| !s.is_empty() && s.len() <= 80 &&
        s.bytes().all(|c| c.is_ascii_alphanumeric() || matches!(c, b'-' | b'_'));
    let tenant_path = if host.ends_with(".zhiye.com") || host.ends_with(".myworkdayjobs.com") {
        let first = host.split('.').next()?;
        if !safe_part(first) || matches!(first,"www"|"jobs"|"campus"|"login"|"m"|"passport") { return None; }
        "/".into()
    } else {
        let mut segments = url.path_segments()?;
        let first = segments.next()?;
        match host {
            "app.mokahr.com" | "app-tc.mokahr.com" if matches!(first,"apply"|"campus_apply"|"social-recruitment"|"campus-recruitment") => {
                let tenant = segments.next()?;
                if !safe_part(tenant) { return None; }
                format!("/{first}/{tenant}/")
            }
            "jobs.lever.co"|"boards.greenhouse.io"|"job-boards.greenhouse.io" if safe_part(first) => format!("/{first}/"),
            _ => return None,
        }
    };
    Some((url.origin().ascii_serialization(),tenant_path))
}

pub(crate) async fn resize_capture(picture: Value, width: u32, height: u32) -> Result<Vec<u8>, String> {
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
    fn tab_binding_requires_the_same_browser_and_active_target() {
        let first = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA";
        let second = "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB";
        let browser = CompanyBrowser {endpoint:"ws://127.0.0.1:9222/devtools/browser/11".into(),
            targets:HashSet::from([first.to_string(),second.to_string()])};
        let binding = format!("11:{first}:{second}");
        assert_eq!(browser.live_targets(&binding).count(),2);
        assert!(browser.restore(&binding,"https://a.example/","甲").is_some());
        assert!(browser.restore(&format!("22:{first}"),"https://a.example/","甲").is_none());
        let gone = format!("11:CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC:{second}");
        assert_eq!(browser.live_targets(&gone).count(),1);
        assert!(browser.restore(&gone,"https://a.example/","甲").is_none());
    }

    #[test]
    fn corporate_ats_tenants_cannot_cross_organizations() {
        let browser = CompanyBrowser {endpoint:"ws://127.0.0.1:9222/devtools/browser/11".into(),targets:HashSet::new()};
        let page = browser.page("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA","https://example.com","甲").unwrap()
            .with_destination("https://app.mokahr.com/campus_apply/acme/job/123").unwrap();
        assert!(page.permits("https://app.mokahr.com/campus_apply/acme/123/form"));
        assert!(!page.permits("https://app.mokahr.com/campus_apply/acme-other/123/form"));
        let direct = browser.page("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA","https://jobs.lever.co/acme/123","甲").unwrap();
        assert!(!direct.permits("https://jobs.lever.co/other/123"));
        assert!(direct.permits("https://jobs.lever.co/acme/123/apply"));
        assert!(safe_form_navigation("https://jobs.lever.co/acme/jobs/123?jobId=12"));
        assert!(!safe_form_navigation("https://jobs.lever.co/acme/jobs/submit"));
        assert!(!safe_form_navigation("https://jobs.lever.co/acme/jobs/123?action=submit"));
        for url in ["https://www.zhiye.com/form","https://company.zhiye.com.fake/job/1","https://app.mokahr.com/apply/",
            "https://x@company.zhiye.com/form","http://company.zhiye.com/form","https://app.mokahr.com/apply/a%2fb/1"] {
            assert!(tenant_destination(url).is_none(),"{url}");
        }
    }

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
