//! 共享官网网申工具，浏览器目标、资料和提交权限均由本机验证。
use crate::{company_page::CompanyPage, worker::WorkerClient};
use serde_json::{json, Value};
use std::time::Duration;
use tauri::Url;
use tokio::sync::{watch, Mutex};
use uuid::Uuid;

#[derive(Default)]
pub(crate) struct CompanyWeb {
    current: Mutex<Option<Session>>,
    preview: watch::Sender<Option<CompanyPreviewTarget>>,
}

#[derive(Clone)]
pub(crate) struct CompanyPreviewTarget {
    pub(crate) run: String,
    pub(crate) generation: i64,
    pub(crate) page: CompanyPage,
}

struct Session {
    run: String,
    generation: i64,
    row: u64,
    company: String,
    page: CompanyPage,
    job: Option<String>,
    document: Option<Value>,
    attempt: Option<String>,
    snapshot: String,
    observation: Value,
    terminal: bool,
}

struct Ledger<'a> {
    worker: &'a WorkerClient,
    run: &'a str,
    generation: i64,
}

impl Ledger<'_> {
    async fn call(&self, request: Value) -> Result<Value, String> {
        self.worker.call("company_web", json!({
            "run_id": self.run, "control_generation": self.generation, "request": request,
        })).await
    }

}

fn safe_page(value: &str) -> Option<String> {
    let mut url = Url::parse(value).ok()?;
    if !matches!(url.scheme(), "https" | "http") || !url.username().is_empty() || url.password().is_some() {
        return None;
    }
    url.set_query(None);
    url.set_fragment(None);
    Some(url.to_string())
}

fn answer(value: Value, image: Option<String>) -> Value {
    let mut content = vec![json!({"type":"inputText","text":value.to_string()})];
    if let Some(image) = image {
        content.push(json!({"type":"inputImage","imageUrl":format!("data:image/jpeg;base64,{image}")}));
    }
    json!({"success":true,"contentItems":content})
}

fn field<'a>(request: &'a Value, key: &str, max: usize) -> Result<&'a str, String> {
    request[key].as_str().map(str::trim)
        .filter(|value| !value.is_empty() && value.chars().count() <= max && !value.chars().any(char::is_control))
        .ok_or_else(|| format!("缺少有效网申参数 {key}"))
}

impl Session {
    async fn observe(&mut self) {
        self.snapshot.clear();
        let previous_url = self.observation["url"].clone();
        for attempt in 0..5 {
            self.observation = match self.page.observe().await {
                Ok(value) => value,
                Err(error) => json!({"url":previous_url, "controls":[], "blocked":error}),
            };
            if self.blocked() || self.observation["text"].as_str().is_some_and(|text| !text.is_empty())
                || self.observation["controls"].as_array().is_some_and(|controls| !controls.is_empty())
                || attempt == 4 { break; }
            tokio::time::sleep(Duration::from_millis(200)).await;
        }
        self.snapshot = Uuid::new_v4().simple().to_string();
    }

    fn blocked(&self) -> bool {
        self.observation["blocked"].as_str().is_some_and(|reason| !reason.is_empty())
    }

    fn control(&self, reference: &str) -> Result<&Value, String> {
        self.observation["controls"].as_array().into_iter().flatten()
            .find(|control| control["ref"] == reference)
            .ok_or_else(|| "控件不在当前页面观察中，请重新读取".into())
    }

    fn shows_job(&self, job: &str) -> bool {
        let normalize = |text: &str| text.split_whitespace().collect::<String>().to_lowercase();
        let job = normalize(job);
        let exact = |text: &str| normalize(text) == job;
        self.observation["jobLabels"].as_array().into_iter().flatten().filter_map(Value::as_str)
            .filter_map(|line| line.split_once(['：', ':']).map(|(_, label)| label)).any(exact)
            || self.observation["headings"].as_array().into_iter().flatten().filter_map(Value::as_str)
                .flat_map(|line| line.split(['·', '｜', '|', '—'])).any(exact)
    }

    fn shows_company(&self) -> bool {
        self.observation["text"].as_str().is_some_and(|text|
            text.to_lowercase().contains(&self.company.to_lowercase()))
    }

    fn checked_form(&self) -> bool {
        if self.observation["modal"] == true {
            return self.document.as_ref() == self.observation.get("document");
        }
        self.shows_company() && self.job.as_deref().is_some_and(|job| self.shows_job(job))
    }

    fn view(&self, extra: Value, image: Option<String>) -> Value {
        let mut view = self.observation.clone();
        if let Some(url) = view["url"].as_str().and_then(safe_page) {
            view["url"] = json!(url);
        } else {
            view.as_object_mut().map(|object| object.remove("url"));
        }
        view["snapshot"] = json!(self.snapshot);
        view["source_row"] = json!(self.row);
        view["attempt_id"] = json!(self.attempt);
        view["terminal"] = json!(self.terminal);
        if let (Some(object), Some(extra)) = (view.as_object_mut(), extra.as_object()) {
            object.extend(extra.clone());
        }
        answer(view, image)
    }

    async fn view_with_image(&self, mut extra: Value, include_image: bool) -> Value {
        let image = if include_image && !self.blocked() {
            match self.page.capture_image(Some(&self.observation)).await {
                Ok(image) => image,
                Err(error) => {
                    extra["image_error"] = json!(error);
                    None
                }
            }
        } else { None };
        self.view(extra, image)
    }
}

impl CompanyWeb {
    pub(crate) async fn unfinished_company(&self, run: &str) -> Option<String> {
        self.current.lock().await.as_ref().filter(|session| session.run == run && !session.terminal)
            .map(|session| session.company.clone())
    }

    pub(crate) fn preview_updates(&self) -> watch::Receiver<Option<CompanyPreviewTarget>> {
        self.preview.subscribe()
    }

    pub(crate) fn clear_preview(&self, run: &str) {
        self.preview.send_if_modified(|target| {
            if target.as_ref().is_none_or(|target| target.run != run) { return false; }
            *target = None;
            true
        });
    }

    pub(crate) async fn call(&self, worker: &WorkerClient, run: &str, generation: i64, request: Value) -> Result<Value, String> {
        if Uuid::parse_str(run).is_err() {
            return Err("任务编号无效".into());
        }
        let action = field(&request, "action", 24)?;
        let ledger = Ledger { worker, run, generation };
        let mut scope = ledger.call(json!({"operation":"scope"})).await?;
        if action == "scope" {
            for row in scope["rows"].as_array_mut().into_iter().flatten() {
                row["url"] = json!(row["url"].as_str().and_then(safe_page));
            }
            return Ok(answer(scope, None));
        }
        let mut current = self.current.lock().await;
        if action == "open" {
            let source = request["source_row"].as_u64().ok_or("请选择已授权公司清单行")?;
            let row = scope["rows"].as_array().into_iter().flatten()
                .find(|row| row["source_row"].as_u64() == Some(source)).ok_or("公司不在本轮授权范围")?;
            if current.as_ref().is_some_and(|s| s.run == run && s.generation == generation &&
                !s.terminal && s.attempt.is_some() && s.row != source) {
                return Err("请先结束当前申请或将其交给用户，再切换公司".into());
            }
            if !current.as_ref().is_some_and(|s| s.run == run && s.generation == generation && s.row == source && !s.terminal) {
                let url = row["url"].as_str().ok_or("公司申请网址无效")?;
                let company = field(row, "company", 200)?;
                self.clear_preview(run);
                let page = CompanyPage::open(&worker.root, url).await?;
                self.preview.send_replace(Some(CompanyPreviewTarget { run: run.into(), generation, page: page.clone() }));
                *current = Some(Session { run: run.into(), generation, row: source, company: company.into(), page, job: None, document: None,
                    attempt: None, snapshot: String::new(), observation: json!({"url":url}), terminal: false });
            }
        }
        let session = current.as_mut().filter(|s| s.run == run && s.generation == generation)
            .ok_or("请先从本轮清单打开公司")?;
        let image_requested = request["include_image"] == true;
        if matches!(action, "open" | "observe") {
            session.observe().await;
            return Ok(session.view_with_image(Value::Null, image_requested).await);
        }
        if session.snapshot.is_empty() || request["snapshot"] != session.snapshot {
            return Err("页面观察已失效，请重新读取页面".into());
        }
        if session.terminal {
            return Err("本次申请已结束或进入人工核查".into());
        }
        // 清掉旧令牌。操作失败后也要重新观察，避免使用旧点击位置重试。
        session.snapshot.clear();
        if matches!(action, "handoff" | "no_match") {
            let reason = field(&request, "reason", 360)?;
            session.observe().await;
            if session.blocked() && action == "no_match" {
                return Err("页面需要人工处理，请登记待办".into());
            }
            if session.attempt.is_none() {
                let entry = ledger.call(json!({"operation":"begin","source_row":session.row,"blocked":true})).await?;
                session.attempt = Some(field(&entry, "attempt_id", 32)?.into());
            }
            let note = session.observation["blocked"].as_str().map_or_else(String::new, |blocked|
                format!("。当前网页状态（{blocked}）").chars().take(88).collect());
            let url = session.observation["url"].as_str().and_then(safe_page).unwrap_or_else(|| "页面已离开".into());
            let reason = format!("{reason}（{}）{note}", url.chars().take(48).collect::<String>());
            let result = ledger.call(json!({"operation":action,"attempt_id":session.attempt,"reason":reason})).await?;
            session.terminal = true;
            return Ok(session.view(result, None));
        }
        if session.blocked() {
            return Err("页面需要人工处理，请登记待办".into());
        }
        if action == "begin" {
            if session.attempt.is_some() {
                return Err("当前页面已绑定本次申请".into());
            }
            let job = field(&request, "job", 200)?;
            session.observe().await;
            if session.blocked() || !session.shows_company() || !session.shows_job(job) {
                return Err("当前页面无法核对授权岗位名称，请转人工或重新读取岗位详情".into());
            }
            let entry = ledger.call(json!({"operation":"begin","source_row":session.row,
                "job":job,"url":session.observation["url"]})).await?;
            session.attempt = Some(field(&entry, "attempt_id", 32)?.into());
            session.job = Some(job.into());
            session.document = session.observation.get("document").cloned();
            return Ok(session.view(entry, None));
        }
        if action == "scroll" {
            let delta = request["delta"].as_i64().filter(|delta| *delta != 0 && delta.abs_diff(0) <= 1200)
                .ok_or("滚动量必须为 1 到 1200 像素")?;
            session.page.action(&session.observation, json!({"kind":"scroll","delta":delta})).await?;
        } else {
            let reference = field(&request, "ref", 40)?;
            let control = session.control(reference)?.clone();
            if matches!(action, "submit" | "ready") {
                return self.finish_form(&ledger, session, &request, reference, &control, scope["web_submit"] == true).await;
            }
            match action {
                "click" => {
                    if session.attempt.is_some() && control["kind"] == "link" {
                        return Err("申请已绑定岗位，跳转链接须交给用户核对".into());
                    }
                    session.page.action(&session.observation, json!({"kind":"click","ref":reference})).await?;
                }
                "fill" | "upload" => {
                    if control["required"] != true {
                        return Err("选填项保留原样".into());
                    }
                    let attempt = session.attempt.as_deref().ok_or("请先核对并登记本轮岗位")?;
                    if !session.checked_form() {
                        return Err("当前表单无法核对已授权公司和岗位，请转人工".into());
                    }
                    session.document = session.observation.get("document").cloned();
                    let source = field(&request, "source", 160)?;
                    if (action == "upload") != (source == "resume") {
                        return Err("上传控件仅可使用本轮已确认的简历".into());
                    }
                    let result = ledger.call(json!({"operation":"value","attempt_id":attempt,"source":source})).await?;
                    let value = result["value"].as_str().ok_or("本轮资料值无效")?;
                    let args = if action == "upload" { json!({"kind":action,"ref":reference,"path":value}) }
                        else { json!({"kind":action,"ref":reference,"value":value}) };
                    session.page.action(&session.observation, args).await?;
                    session.observe().await;
                    let label = format!("{}（{}）", control["label"].as_str().unwrap_or("字段"), source);
                    let label = label.chars().take(200).collect::<String>();
                    let recorded = ledger.call(json!({"operation":"field","attempt_id":session.attempt,
                        "label":label,"source":source})).await?;
                    return Ok(session.view_with_image(recorded, image_requested).await);
                }
                _ => return Err("未知的网申浏览器操作".into()),
            }
        }
        session.observe().await;
        Ok(session.view_with_image(Value::Null, image_requested).await)
    }

    async fn finish_form(&self, ledger: &Ledger<'_>, session: &mut Session, request: &Value, reference: &str,
        control: &Value, automatic: bool) -> Result<Value, String> {
        let manual = request["action"] == "ready";
        if manual == automatic {
            return Err(if manual { "本轮已授权自动提交，请核对页面后执行提交" }
                else { "本轮仅授权填写，请交给用户手动提交" }.into());
        }
        if request["reviewed"] != true || control["kind"] != "submit" {
            return Err("请先核对必填项、附件及当前提交控件".into());
        }
        let attempt = session.attempt.clone().ok_or("请先核对并登记本轮岗位")?;
        let now = session.page.observe().await?;
        if now != session.observation {
            return Err("提交前页面已变化，请重新读取并复核".into());
        }
        if session.blocked() || !session.shows_company() || !session.job.as_deref().is_some_and(|job| session.shows_job(job)) ||
            now["receipt"].as_str().is_some_and(|receipt| !receipt.is_empty()) {
            return Err("当前页无法确认是本轮岗位的待提交表单，请转人工".into());
        }
        if now["truncated"] == true || now["embedded"] == true {
            return Err("当前页有未能完整核对的控件，请转人工".into());
        }
        let missing = now["controls"].as_array().into_iter().flatten()
            .filter(|item| item["invalid"] == true || (item["required"] == true && item["occupied"] != true))
            .filter_map(|item| item["label"].as_str()).take(8).collect::<Vec<_>>();
        if !missing.is_empty() {
            return Err(format!("有未完成或无效的字段：{}。请补全或转人工", missing.join("、")));
        }
        if manual {
            session.page.action(&now, json!({"kind":"ready","ref":reference})).await?;
            let result = ledger.call(json!({"operation":"ready","attempt_id":attempt})).await?;
            session.terminal = true;
            return Ok(session.view(result, None));
        }
        let review = format!("{}{}", Uuid::new_v4().simple(), Uuid::new_v4().simple());
        let prepared = ledger.call(json!({"operation":"prepare","attempt_id":attempt,"review":review})).await?;
        let id = field(&prepared, "action_id", 32)?;
        ledger.call(json!({"operation":"claim","attempt_id":attempt,"action_id":id,"review":review})).await?;
        session.terminal = true;
        let sent = session.page.action(&now, json!({"kind":"submit","ref":reference})).await;
        // 一旦开始点击，只观察回执，不再次触发提交。
        for _ in 0..4 {
            session.observe().await;
            if session.observation["receipt"].as_str().is_some_and(|r| !r.is_empty()) {
                break;
            }
            tokio::time::sleep(Duration::from_millis(500)).await;
        }
        let receipt = session.observation["receipt"].as_str().filter(|r| !r.is_empty());
        let (status, reason) = if let Some(receipt) = receipt.filter(|_| sent.is_ok()) {
            ("succeeded", receipt)
        } else { ("uncertain", "提交结果不明确，请在招聘网站核对申请状态") };
        let mut result = ledger.call(json!({"operation":"settle","attempt_id":attempt,"action_id":id,
            "status":status,"reason":reason})).await;
        if result.is_err() && status == "succeeded" {
            result = ledger.call(json!({"operation":"settle","attempt_id":attempt,"action_id":id,
                "status":"uncertain","reason":"提交回执未通过核验，请人工核对"})).await;
        }
        Ok(session.view_with_image(result?, request["include_image"] == true).await)
    }
}

#[cfg(test)]
#[path = "../tests/support/company_web_tests.rs"]
mod tests;
