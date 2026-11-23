//! 公司表行与专用 Chrome 标签的绑定、登录准备和网页导航。
use super::{
    authorized_jobs, field, safe_page, shows_job, CompanyPreviewTarget, CompanyWeb, Ledger, Session,
};
use crate::{company_page::CompanyBrowser, worker::WorkerClient};
use serde_json::{json, Value};
use std::collections::HashSet;
use uuid::Uuid;

const MAX_TABS: usize = 5;
const MEMORY_FLOOR: u64 = 2 * 1024 * 1024 * 1024;

#[cfg(windows)]
fn available_memory() -> Option<u64> {
    #[repr(C)]
    struct MemoryStatus {
        length: u32,
        load: u32,
        total_phys: u64,
        avail_phys: u64,
        total_page_file: u64,
        avail_page_file: u64,
        total_virtual: u64,
        avail_virtual: u64,
        avail_extended_virtual: u64,
    }
    #[link(name = "kernel32")]
    extern "system" {
        fn GlobalMemoryStatusEx(status: *mut MemoryStatus) -> i32;
    }
    let mut status = MemoryStatus {
        length: std::mem::size_of::<MemoryStatus>() as u32,
        load: 0,
        total_phys: 0,
        avail_phys: 0,
        total_page_file: 0,
        avail_page_file: 0,
        total_virtual: 0,
        avail_virtual: 0,
        avail_extended_virtual: 0,
    };
    // Windows 只写入传入的、按其原生布局初始化的结构。
    (unsafe { GlobalMemoryStatusEx(&mut status) } != 0).then_some(status.avail_phys)
}

#[cfg(not(windows))]
fn available_memory() -> Option<u64> {
    None
}

fn capacity_reason(count: usize, memory: Option<u64>, loading: usize) -> Option<&'static str> {
    if count >= MAX_TABS {
        return Some("5 个工作标签已占满，请核对并关闭已处理的页面后继续");
    }
    if memory.is_some_and(|bytes| bytes < MEMORY_FLOOR) {
        return Some("可用内存不足 2 GiB，关闭其他网页后可继续");
    }
    if loading >= 2 {
        return Some("已有两个网站正在加载，请稍后继续检查");
    }
    None
}

fn row_finished(status: &str) -> bool {
    matches!(
        status,
        "ready_for_review"
            | "manual_submitted"
            | "submitted"
            | "uncertain"
            | "no_match"
            | "skipped"
    )
}

fn work_tab_count(browser: &CompanyBrowser, scope: &Value) -> usize {
    scope["occupied_targets"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .chain(
            scope["rows"]
                .as_array()
                .into_iter()
                .flatten()
                .filter_map(|r| r["target_id"].as_str()),
        )
        .flat_map(|binding| browser.live_targets(binding))
        .collect::<HashSet<_>>()
        .len()
}

impl CompanyWeb {
    // 部分入口用脚本打开窗口且没有 href；只认这次点击产生、Chrome 报告原页为 opener 的子页。
    pub(super) async fn click_form_button(
        &self,
        worker: &WorkerClient,
        ledger: &Ledger<'_>,
        session: &mut Session,
        scope: &Value,
        reference: &str,
    ) -> Result<(), String> {
        let _guard = self.prepare_lock.lock().await;
        let browser = CompanyBrowser::connect(&worker.root, false).await?;
        let row = scope["rows"]
            .as_array()
            .into_iter()
            .flatten()
            .find(|r| r["source_row"] == session.row)
            .ok_or("公司不在本轮已核对清单")?;
        let old = field(row, "target_id", 220)?;
        if browser.live_targets(old).count() > 1
            || row["destination_url"]
                .as_str()
                .is_some_and(|s| !s.is_empty())
            || capacity_reason(work_tab_count(&browser, scope), available_memory(), 0).is_some()
        {
            session
                .page
                .action(
                    &session.observation,
                    json!({"kind":"formNode","ref":reference}),
                )
                .await?;
            return Ok(());
        }
        session
            .page
            .action(
                &session.observation,
                json!({"kind":"click","ref":reference}),
            )
            .await?;
        let children = session.page.new_children(&browser.targets).await?;
        if children.is_empty() {
            return Ok(());
        }
        let mut held = session.page.binding();
        for (id, _) in children.iter().take(3) {
            held.push(':');
            held.push_str(id);
        }
        ledger
            .call(json!({"operation":"bind_target","source_row":session.row,"target_id":held}))
            .await?;
        let [(id, url)] = children.as_slice() else {
            return Err("网申入口打开了多个窗口，请人工选择对应表单".into());
        };
        if !session.page.same_origin(url) {
            return Err("新窗口与原招聘入口的归属无法核对，请人工确认".into());
        }
        let browser = CompanyBrowser::connect(&worker.root, false).await?;
        let binding = format!(
            "{}:{}:{}",
            old.split(':').next().unwrap_or(""),
            id,
            old.split(':').nth(1).unwrap_or("")
        );
        let page = browser
            .restore(
                &binding,
                field(row, "url", 8192)?,
                field(row, "company", 200)?,
            )
            .ok_or("新窗口已关闭，请重新观察")?;
        let _ = page.loaded().await;
        let observation = page.observe().await?;
        if observation["loginRequired"] != true
            && (observation["companyMatched"] != true
                || !authorized_jobs(scope, session.row)
                    .iter()
                    .any(|role| shows_job(&observation, role)))
        {
            return Err("新窗口尚未明确显示本行公司及目标岗位，请人工核对".into());
        }
        ledger
            .call(json!({"operation":"bind_target","source_row":session.row,"target_id":binding}))
            .await?;
        session.page = page;
        self.preview.send_replace(Some(CompanyPreviewTarget {
            run: session.run.clone(),
            generation: session.generation,
            page: session.page.clone(),
        }));
        Ok(())
    }

    pub(super) async fn follow_link(
        &self,
        worker: &WorkerClient,
        ledger: &Ledger<'_>,
        session: &mut Session,
        scope: &Value,
        control: &Value,
        reference: &str,
    ) -> Result<(), String> {
        let row = scope["rows"]
            .as_array()
            .into_iter()
            .flatten()
            .find(|r| r["source_row"] == session.row)
            .ok_or("公司不在本轮已核对清单")?;
        let external = control["kind"] == "externalForm";
        let link = session
            .page
            .action(
                &session.observation,
                json!({"kind":"linkTarget","ref":reference}),
            )
            .await?;
        let url = field(&link, "destination", 8192)?;
        if !external && !session.page.same_origin(url) {
            return Err("新标签已离开授权招聘租户，请人工核对".into());
        }
        let roles = authorized_jobs(scope, session.row);
        let job = roles.iter().copied().find(|role| session.shows_job(role));
        if external
            && (!session.shows_company()
                || job.is_none()
                || crate::company_page::tenant_destination(url).is_none())
        {
            return Err("申请入口的公司、岗位或招聘平台租户不明确，请人工核对".into());
        }
        let _preparing = self.prepare_lock.lock().await;
        let mut chrome = CompanyBrowser::connect(&worker.root, false).await?;
        let old_binding = field(row, "target_id", 220)?;
        if chrome
            .restore(
                old_binding,
                field(row, "url", 8192)?,
                field(row, "company", 200)?,
            )
            .is_none()
        {
            return Err("原网申标签已关闭，请重新检查".into());
        }
        // 同域声明式链接可在原位导航，避免为同一岗位无限积累标签。
        if chrome.live_targets(old_binding).count() > 1
            || row["destination_url"]
                .as_str()
                .is_some_and(|s| !s.is_empty())
            || capacity_reason(work_tab_count(&chrome, scope), available_memory(), 0).is_some()
        {
            if external {
                return Err(
                    "核对外部招聘平台需要一个空闲工作标签，请先核对并关闭已处理的网页".into(),
                );
            }
            session
                .page
                .action(
                    &session.observation,
                    json!({"kind":"click","ref":reference}),
                )
                .await?;
            return Ok(());
        }
        let company = field(row, "company", 200)?;
        let page = chrome.reserve(url, company, false).await?;
        let held = format!(
            "{}:{}",
            session.page.binding(),
            page.binding().rsplit(':').next().unwrap_or_default()
        );
        if let Err(error) = ledger
            .call(json!({"operation":"bind_target","source_row":session.row,"target_id":held}))
            .await
        {
            page.discard_blank().await;
            return Err(error);
        }
        page.navigate_blank(url).await?;
        let binding = format!(
            "{}:{}",
            page.binding(),
            old_binding.split(':').nth(1).ok_or("旧标签绑定无效")?
        );
        let page = if external {
            let page = page.with_destination(url)?;
            let checked = async {
                if !page.loaded().await { return Err("外部招聘平台仍在加载".to_string()); }
                let observed = page.observe().await?;
                if observed["blocked"].is_string() || observed["companyMatched"] != true ||
                    !job.is_some_and(|role| shows_job(&observed,role)) {
                    return Err("外部招聘平台未能同时核对公司、目标岗位与租户，请人工确认".into());
                }
                let result = ledger.call(json!({"operation":"bind_destination","source_row":session.row,"job":job,
                    "entry_url":session.observation["url"].as_str().and_then(safe_page),
                    "destination_url":observed["url"].as_str().and_then(safe_page),"target_id":binding})).await;
                result.map(|_| ())
            }.await;
            checked?;
            page
        } else {
            let page = match row["destination_url"].as_str().filter(|s| !s.is_empty()) {
                Some(url) => page.with_destination(url)?,
                None => page,
            };
            ledger
                .call(
                    json!({"operation":"bind_target","source_row":session.row,"target_id":binding}),
                )
                .await?;
            page
        };
        session.page = page;
        self.preview.send_replace(Some(CompanyPreviewTarget {
            run: session.run.clone(),
            generation: session.generation,
            page: session.page.clone(),
        }));
        Ok(())
    }

    pub(crate) async fn prepare_batch(
        &self,
        worker: &WorkerClient,
        run: &str,
        generation: i64,
        rows: &[Value],
        occupied_targets: &[Value],
    ) -> Result<Vec<Value>, String> {
        self.batch(
            &Ledger {
                worker,
                run,
                generation,
            },
            rows,
            occupied_targets,
            false,
        )
        .await
    }

    pub(crate) async fn check_batch(
        &self,
        worker: &WorkerClient,
        run: &str,
        generation: i64,
        rows: &[Value],
        occupied_targets: &[Value],
    ) -> Result<Vec<Value>, String> {
        self.batch(
            &Ledger {
                worker,
                run,
                generation,
            },
            rows,
            occupied_targets,
            true,
        )
        .await
    }

    async fn batch(
        &self,
        ledger: &Ledger<'_>,
        rows: &[Value],
        occupied_targets: &[Value],
        check: bool,
    ) -> Result<Vec<Value>, String> {
        Uuid::parse_str(ledger.run).map_err(|_| "任务编号无效")?;
        if rows.iter().all(|r| r["status"].as_str().is_some_and(|state|
            state != "ready_for_review" && row_finished(state))) {
            return Ok(vec![]);
        }
        let _preparing = self.prepare_lock.lock().await;
        ledger.call(json!({"operation":"scope"})).await?;
        let mut chrome = CompanyBrowser::connect(&ledger.worker.root, true).await?;
        let mut occupied: HashSet<String> = occupied_targets
            .iter()
            .filter_map(Value::as_str)
            .chain(rows.iter().filter_map(|r| r["target_id"].as_str()))
            .flat_map(|binding| chrome.live_targets(binding))
            .map(str::to_owned)
            .collect();
        let mut results = Vec::new();
        let mut loading = chrome.loading_targets(&occupied).await;
        let mut sorted = rows.iter().collect::<Vec<_>>();
        sorted.sort_by_key(|row| row["source_row"].as_u64());
        for row in sorted {
            let source = row["source_row"].as_u64().ok_or("网申队列行无效")?;
            let url = field(row, "url", 8192)?;
            let company = field(row, "company", 200)?;
            let binding = row["target_id"].as_str().unwrap_or("");
            let restored = chrome.restore(binding, url, company);
            let initial_origin =
                restored.is_none() || row["destination_url"].as_str().unwrap_or("").is_empty();
            let status = row["status"].as_str().unwrap_or("pending_open");
            if row_finished(status) {
                if status == "ready_for_review" {
                    results.push(json!({"source_row":source, "status":"needs_review",
                        "reason":if restored.is_some() { "已填写，等待在原标签核对" } else { "原标签已关闭，网页草稿状态未知" },
                        "tab_available":restored.is_some(), "target_id":binding}));
                }
                continue;
            }
            let (page, binding) = if let Some(page) = restored {
                let page = match row["destination_url"].as_str().filter(|s| !s.is_empty()) {
                    Some(url) => page.with_destination(url)?,
                    None => page,
                };
                (page, binding.to_string())
            } else {
                if let Some(reason) = capacity_reason(occupied.len(), available_memory(), loading.len()) {
                    results.push(
                        json!({"source_row":source,"status":"pending","reason":reason,
                        "target_id":binding,"tab_available":false}),
                    );
                    continue;
                }
                ledger.call(json!({"operation":"scope"})).await?;
                let page = match chrome.reserve(url, company, !results.is_empty()).await {
                    Ok(page) => page,
                    Err(reason) => {
                        results.push(json!({"source_row":source,"status":"needs_attention","reason":reason,"tab_available":false,"target_id":""}));
                        continue;
                    }
                };
                let mut new_binding = page.binding();
                for id in chrome.live_targets(binding) {
                    new_binding.push(':');
                    new_binding.push_str(id);
                }
                let binding = new_binding;
                if let Err(error) = ledger
                    .call(
                        json!({"operation":"bind_opened","source_row":source,"target_id":binding}),
                    )
                    .await
                {
                    page.discard_blank().await;
                    return Err(error);
                }
                occupied.extend(chrome.live_targets(&binding).map(str::to_owned));
                (page, binding)
            };
            if initial_origin {
                ledger.call(json!({"operation":"scope"})).await?;
                if let Err(reason) = page.navigate_blank(url).await {
                    results.push(
                        json!({"source_row":source,"status":"needs_attention","reason":reason,
                        "target_id":binding,"tab_available":true}),
                    );
                    continue;
                }
            }
            let loaded = page.loaded().await;
            if let Some(id) = binding.split(':').nth(1) {
                if loaded { loading.remove(id); } else { loading.insert(id.to_owned()); }
            }
            let (state, reason) = if !check {
                ("opened", "已打开，可在求职 Chrome 完成登录")
            } else {
                match page.observe().await {
                    Ok(observed) if observed["loginRequired"] == true => {
                        ("needs_login", "请在原标签完成登录或身份验证")
                    }
                    Ok(observed) if observed["blocked"].is_string() => (
                        "needs_attention",
                        "当前页面已离开授权的招聘入口，请在原标签核对",
                    ),
                    Ok(observed)
                        if observed["companyMatched"] == true
                            && observed["editableForm"] == true =>
                    {
                        ("ready", "已找到公司表单，等待核对岗位")
                    }
                    Ok(observed)
                        if !loaded && observed["text"].as_str().unwrap_or("").is_empty() =>
                    {
                        ("needs_attention", "页面仍在加载，请稍后继续检查")
                    }
                    Ok(_) => ("locate", "从当前网页查找授权岗位和填写入口"),
                    Err(_) => ("needs_attention", "当前网页无法读取，请在原标签核对"),
                }
            };
            results.push(json!({"source_row":source,"status":state,"reason":reason,"target_id":binding,"tab_available":true}));
        }
        Ok(results)
    }

    pub(crate) async fn focus_row(
        &self,
        worker: &WorkerClient,
        run: &str,
        source: i64,
        row: &Value,
    ) -> Result<(), String> {
        Uuid::parse_str(run).map_err(|_| "任务编号无效")?;
        if row["source_row"].as_i64() != Some(source) {
            return Err("标签行与当前授权不符".into());
        }
        let binding = field(row, "target_id", 220)?;
        match CompanyBrowser::connect(&worker.root, false).await {
            Ok(browser) => {
                if let Some(page) = browser.restore(
                    binding,
                    field(row, "url", 8192)?,
                    field(row, "company", 200)?,
                ) {
                    return page.activate().await;
                }
            }
            Err(error) if !CompanyBrowser::confirmed_closed(&worker.root, binding).await => {
                return Err(error)
            }
            Err(_) => (),
        }
        if row["status"] == "ready_for_review" {
            worker
                .call(
                    "company_lost_target",
                    json!({"run_id":run,"source_row":source,"target_id":binding}),
                )
                .await?;
            return Err("原核对标签已关闭，网页草稿状态未知。你可以选择重新打开检查".into());
        }
        Err("原网申标签已关闭，请回到工作台继续检查".into())
    }
}

#[cfg(test)]
mod queue_limits {
    use super::*;

    #[test]
    fn tab_budget_counts_review_and_loading_before_opening() {
        assert!(capacity_reason(4, Some(MEMORY_FLOOR), 1).is_none());
        assert!(capacity_reason(5, Some(MEMORY_FLOOR), 0).is_some());
        assert!(capacity_reason(0, Some(MEMORY_FLOOR - 1), 0).is_some());
        assert!(capacity_reason(0, Some(MEMORY_FLOOR), 2).is_some());
        assert!(row_finished("ready_for_review"));
        assert!(row_finished("uncertain"));
        assert!(!row_finished("login_required"));
        assert!(!row_finished("manual_required"));
    }
}
