//! 由 tests/desktop_company_application_smoke.py 启动的真实工具链验收。
use super::CompanyWeb;
use crate::{browser_runtime::BrowserRuntime, worker::WorkerClient};
use base64::{engine::general_purpose::STANDARD, Engine};
use serde_json::{json, Value};
use std::{
    path::Path,
    sync::{
        atomic::{AtomicBool, Ordering},
        Arc,
    },
    time::Duration,
};
use tauri::ipc::{Channel, InvokeResponseBody};
use tokio::sync::mpsc;

struct Batch {
    browser: CompanyWeb,
    worker: WorkerClient,
    run: String,
    generation: i64,
}

impl Batch {
    async fn request(&self, mut request: Value) -> Result<Value, String> {
        let include_image = request["include_image"] == true;
        request["operation"] = json!("web");
        let envelope = self
            .browser
            .call(&self.worker, &self.run, self.generation, request)
            .await?;
        if envelope["success"] != true {
            return Err(format!("工具返回失败: {envelope}"));
        }
        if include_image {
            let image = &envelope["contentItems"][1];
            assert_eq!(image["type"], "inputImage");
            assert!(image["imageUrl"].as_str().unwrap().starts_with("data:image/"));
        }
        serde_json::from_str(
            envelope["contentItems"][0]["text"]
                .as_str()
                .expect("工具无文本结果"),
        )
        .map_err(|error| error.to_string())
    }

    async fn must(&self, request: Value) -> Value {
        let action = request["action"].clone();
        self.request(request)
            .await
            .unwrap_or_else(|error| panic!("{action}: {error}"))
    }

    async fn open(&self, source_row: u64) -> Value {
        self.must(json!({"action":"open","source_row":source_row}))
            .await
    }

    async fn wait_for_control(&self, mut page: Value, label: &str) -> Value {
        for _ in 0..25 {
            if has_control(&page, label) {
                return page;
            }
            tokio::time::sleep(Duration::from_millis(120)).await;
            page = self.must(json!({"action":"observe"})).await;
        }
        panic!("页面没有加载预期控件 {label}: {page}");
    }

    async fn begin(&self, page: &Value, job: &str) -> Value {
        assert!(page["text"].as_str().unwrap_or_default().contains(job));
        let page = self
            .must(json!({"action":"begin","snapshot":page["snapshot"],"job":job}))
            .await;
        assert!(page["attempt_id"].is_string());
        page
    }

    async fn click(&self, page: &Value, label: &str) -> Value {
        self.must(control_request(page, "click", label)).await
    }

    async fn fill(&self, page: &Value, labels: &[&str], source: &str) -> Value {
        let target = page["controls"]
            .as_array()
            .unwrap()
            .iter()
            .find(|control| {
                control["label"]
                    .as_str()
                    .is_some_and(|label| labels.contains(&label))
            })
            .unwrap_or_else(|| panic!("在当前页面找不到 {labels:?}"));
        assert_eq!(target["occupied"], false);
        let mut request = json!({"action":"fill","snapshot":page["snapshot"],"ref":target["ref"],"source":source});
        let label = target["label"].as_str().unwrap();
        request["label"] = json!(label);
        let page = self.must(request).await;
        assert_eq!(control(&page, label)["occupied"], true);
        page
    }

    async fn basics(&self, page: &Value) -> Value {
        let existing = control(
            page,
            if has_control(page, "电子邮件") {
                "电子邮件"
            } else {
                "联系邮箱"
            },
        );
        assert_eq!(existing["occupied"], true);
        assert!(self.request(json!({"action":"fill","snapshot":page["snapshot"],"ref":existing["ref"],"source":"profile.contact.email"})).await.is_err());
        let page = self.must(json!({"action":"observe"})).await;
        self.fill(&page, &["申请人姓名", "姓名"], "profile.name")
            .await
    }
}

fn has_control(page: &Value, label: &str) -> bool {
    page["controls"]
        .as_array()
        .unwrap()
        .iter()
        .any(|control| control["label"] == label)
}

fn control<'a>(page: &'a Value, label: &str) -> &'a Value {
    page["controls"]
        .as_array()
        .unwrap()
        .iter()
        .find(|control| control["label"] == label)
        .unwrap_or_else(|| panic!("找不到 {label}: {}", page["controls"]))
}

fn control_request(page: &Value, action: &str, label: &str) -> Value {
    json!({"action":action,"snapshot":page["snapshot"],"ref":control(page,label)["ref"]})
}

fn submit_request(page: &Value, label: &str) -> Value {
    let mut request = control_request(page, "submit", label);
    request["reviewed"] = json!(true);
    request
}

fn ready_request(page: &Value, label: &str) -> Value {
    let mut request = submit_request(page, label);
    request["action"] = json!("ready");
    request
}

async fn preview_event(
    events: &mut mpsc::UnboundedReceiver<Value>,
    matches: impl Fn(&Value) -> bool,
) -> Value {
    tokio::time::timeout(Duration::from_secs(8), async {
        loop {
            let event = events.recv().await.expect("实时画面通道提前关闭");
            assert_ne!(event["state"], "error", "{event}");
            if matches(&event) {
                return event;
            }
        }
    })
    .await
    .expect("等待实时画面事件超时")
}

async fn preview_jpeg(events: &mut mpsc::UnboundedReceiver<Value>, epoch: &str) -> (u64, Vec<u8>) {
    let frame = preview_event(events, |event| event["type"] == "frame").await;
    assert_eq!(frame["epoch"], epoch);
    let jpeg = STANDARD
        .decode(frame["data"].as_str().unwrap())
        .expect("实时画面应为 Base64 JPEG");
    assert!(jpeg.starts_with(&[0xff, 0xd8]) && jpeg.ends_with(&[0xff, 0xd9]));
    assert!(frame["metadata"]["timestamp"].as_u64().unwrap() > 0);
    (frame["seq"].as_u64().unwrap(), jpeg)
}

#[tokio::test]
#[ignore = "需由 Python 启动隔离 Chrome 与合成公司网站"]
async fn synthetic_company_batch() {
    let batch = Batch {
        browser: CompanyWeb::default(),
        worker: WorkerClient::from_environment(Path::new(env!("CARGO_MANIFEST_DIR"))).unwrap(),
        run: std::env::var("JOB_ASSISTANT_TEST_WEB_RUN").unwrap(),
        generation: std::env::var("JOB_ASSISTANT_TEST_WEB_GENERATION")
            .unwrap()
            .parse()
            .unwrap(),
    };
    let mut preview = batch.browser.preview_updates();
    assert!(preview.borrow_and_update().is_none());
    let unavailable = batch
        .request(json!({"action":"open","source_row":99}))
        .await;
    assert!(unavailable.is_err(), "不能打开清单外网站");
    assert!(
        preview.borrow_and_update().is_none(),
        "清单外页面不能接入预览"
    );

    let foreign = batch
        .wait_for_control(batch.open(6).await, "进入申请表")
        .await;
    let page = batch.click(&foreign, "进入申请表").await;
    let page = batch.wait_for_control(page, "学校名称").await;
    let wrong_company = batch
        .request(json!({"action":"begin","snapshot":page["snapshot"],
        "job":"软件测试工程师"}))
        .await;
    assert!(wrong_company.is_err(), "同源同岗位也不能给未授权公司填写");

    let page = batch.must(json!({"action":"observe"})).await;
    let handoff = batch.must(json!({"action":"handoff","snapshot":page["snapshot"],"reason":"页面公司不匹配"})).await;
    assert_eq!(handoff["terminal"], true);
    assert!(handoff["attention"]["id"].is_string() && handoff["evidence_id"].is_null());

    let mut verification = batch.open(5).await;
    for _ in 0..25 {
        if verification["blocked"].is_string() && verification["document"].is_string() {
            break;
        }
        tokio::time::sleep(Duration::from_millis(120)).await;
        verification = batch.must(json!({"action":"observe"})).await;
    }
    assert!(verification["blocked"].as_str().unwrap().contains("验证码"));
    assert!(verification["controls"].as_array().unwrap().is_empty());
    let verification_target = preview.borrow_and_update().clone();
    if let Some(target) = verification_target {
        assert!(
            target.page.preview_frame().await.unwrap().is_none(),
            "安全验证画面不能进入预览"
        );
    }
    batch.must(json!({"action":"handoff","snapshot":verification["snapshot"],"reason":"等待用户完成安全验证"})).await;

    let runtime = BrowserRuntime::new(&batch.worker.resources, &batch.worker.root);
    let (send, mut events) = mpsc::unbounded_channel();
    let channel = Channel::new(move |body| {
        if let InvokeResponseBody::Json(text) = body {
            let _ = send.send(serde_json::from_str(&text).unwrap());
        }
        Ok(())
    });
    let running = Arc::new(AtomicBool::new(false));
    let running_for_preview = running.clone();
    let generation = batch.generation;
    let stream = runtime
        .start_company_preview(
            batch.run.clone(),
            batch.browser.preview_updates(),
            move || {
                running_for_preview
                    .load(Ordering::SeqCst)
                    .then_some(generation)
            },
            channel,
        )
        .await
        .unwrap();
    let epoch = stream["epoch"].as_str().unwrap();
    preview_event(&mut events, |event| event["state"] == "waiting").await;
    running.store(true, Ordering::SeqCst);

    // 步骤式布局的首家公司缺少必填资料，进入待办后继续下一家。
    let visited = batch
        .wait_for_control(batch.open(2).await, "进入申请表")
        .await;
    let river = preview
        .borrow_and_update()
        .clone()
        .expect("打开公司后应绑定标签页");
    assert_eq!(river.run, batch.run);
    assert_eq!(river.generation, batch.generation);
    let (river_first_seq, river_landing) = preview_jpeg(&mut events, epoch).await;
    let jpeg = image::load_from_memory_with_format(&river_landing, image::ImageFormat::Jpeg).unwrap().to_rgb8();
    assert!(jpeg.width() <= 1100 && jpeg.height() <= 750);
    let pixel = jpeg.get_pixel(jpeg.width() - 5, jpeg.height() - 5).0;
    assert!(pixel[0] > 180 && pixel[1] < 100 && pixel[2] < 100, "预览保留页面右下角标记");
    let changed = batch.must(json!({"action":"observe"})).await;
    assert!(visited["viewport"]["width"].as_u64().unwrap() > 1100, "测试应使用宽视口");
    assert_eq!(changed["viewport"], visited["viewport"], "采集不得改变浏览器视口");
    let visited = changed;
    let page = batch.click(&visited, "进入申请表").await;
    let page = batch.wait_for_control(page, "职业资格编号").await;
    assert_ne!(page["document"], visited["document"]);
    runtime.ack_preview(epoch, river_first_seq).await.unwrap();
    let (river_form_seq, river_form) = preview_jpeg(&mut events, epoch).await;
    assert!(river_form_seq > river_first_seq);
    assert!(river_form != river_landing, "同一标签页跳转后应采集新画面");
    let page = batch.begin(&page, "软件测试工程师").await;
    assert_eq!(control(&page, "职业资格编号")["required"], true);
    let page = batch.basics(&page).await;
    let page = batch
        .fill(
            &page,
            &["毕业院校", "学校名称"],
            "profile.education.0.school",
        )
        .await;
    assert_eq!(control(&page, "补充说明（选填）")["occupied"], false);
    assert!(
        batch
            .request(submit_request(&page, "确认提交"))
            .await
            .is_err(),
        "空白必填项不能提交"
    );
    let page = batch.must(json!({"action":"observe"})).await;
    batch
        .must(
            json!({"action":"handoff","snapshot":page["snapshot"],"reason":"缺少必填职业资格编号"}),
        )
        .await;

    // 长页布局新增教育弹窗，两站名称不同的学校字段都从相同资料来源填写。
    batch
        .wait_for_control(batch.open(3).await, "申请人姓名")
        .await;
    assert!(
        preview.has_changed().unwrap(),
        "切换公司后应通知预览更新标签页"
    );
    let north = preview
        .borrow_and_update()
        .clone()
        .expect("下一家公司应重新绑定标签页");
    assert_eq!(north.run, batch.run);
    assert_eq!(north.generation, batch.generation);
    let (north_seq, north_frame) = preview_jpeg(&mut events, epoch).await;
    assert!(north_seq > river_form_seq, "换页无需等待上一家公司的确认");
    assert!(north_frame != river_form, "切换后应显示当前公司的表单画面");
    let page = batch.must(json!({"action":"observe","include_image":true})).await;
    let page = batch.begin(&page, "嵌入式开发工程师").await;
    let page = batch.basics(&page).await;
    assert_eq!(control(&page, "个人主页（选填）")["occupied"], false);
    let before_scroll = page["viewport"]["y"].as_f64().unwrap();
    let page = batch
        .must(json!({"action":"scroll","snapshot":page["snapshot"],"delta":400}))
        .await;
    assert!(page["viewport"]["y"].as_f64().unwrap() > before_scroll);
    assert!(!has_control(&page, "毕业院校"));
    let dialog = batch.click(&page, "新增教育经历").await;
    assert_ne!(dialog["snapshot"], page["snapshot"]);
    assert!(
        batch
            .request(control_request(&page, "click", "新增教育经历"))
            .await
            .is_err(),
        "旧观察不能重放"
    );
    let page = batch
        .fill(
            &dialog,
            &["毕业院校", "学校名称"],
            "profile.education.0.school",
        )
        .await;
    let page = batch
        .fill(&page, &["所学专业"], "profile.education.0.major")
        .await;
    let page = batch.click(&page, "下一步：返回申请表").await;
    let mut upload = control_request(&page, "upload", "简历附件");
    upload["source"] = json!("resume");
    let page = batch.must(upload).await;
    assert_eq!(control(&page, "简历附件")["occupied"], true);
    assert!(batch.request(ready_request(&page, "提交申请")).await.is_err(), "自动模式与手动交接分开");
    let page = batch.must(json!({"action":"observe"})).await;
    let success = batch.must(submit_request(&page, "提交申请")).await;
    assert_eq!(success["status"], "succeeded");
    assert!(success["receipt"]
        .as_str()
        .unwrap()
        .contains("BL-LOCAL-001"));

    let page = batch
        .wait_for_control(batch.open(4).await, "进入申请表")
        .await;
    let (harbor_seq, _) = preview_jpeg(&mut events, epoch).await;
    assert!(harbor_seq > north_seq);
    let page = batch.click(&page, "进入申请表").await;
    let page = batch.wait_for_control(page, "学校名称").await;
    let page = batch.begin(&page, "软件测试工程师").await;
    let page = batch.basics(&page).await;
    let page = batch
        .fill(
            &page,
            &["毕业院校", "学校名称"],
            "profile.education.0.school",
        )
        .await;
    let submission = submit_request(&page, "确认提交");
    let waiting = batch.must(submission.clone()).await;
    assert_eq!(waiting["status"], "uncertain");
    assert!(waiting["text"]
        .as_str()
        .unwrap_or_default()
        .contains("系统正在处理"));
    assert!(
        batch.request(submission).await.is_err(),
        "待核查申请不能重复提交"
    );
    running.store(false, Ordering::SeqCst);
    batch.browser.clear_preview(&batch.run);
    assert!(
        preview.borrow_and_update().is_none(),
        "暂停任务后预览应解除标签页绑定"
    );
    preview_event(&mut events, |event| event["state"] == "waiting").await;
    let frame_after_pause = tokio::time::timeout(Duration::from_millis(650), async {
        while let Some(event) = events.recv().await {
            if event["type"] == "frame" {
                return true;
            }
        }
        false
    })
    .await;
    assert_ne!(
        frame_after_pause.ok(),
        Some(true),
        "暂停后禁止继续推送旧画面"
    );
    runtime.stop_preview_if_epoch(epoch).await;
    preview_event(&mut events, |event| event["state"] == "stopped").await;

    let manual = Batch {
        browser: CompanyWeb::default(),
        worker: batch.worker,
        run: std::env::var("JOB_ASSISTANT_TEST_WEB_MANUAL_RUN").unwrap(),
        generation: std::env::var("JOB_ASSISTANT_TEST_WEB_MANUAL_GENERATION")
            .unwrap()
            .parse()
            .unwrap(),
    };
    let page = manual.wait_for_control(manual.open(2).await, "进入申请表").await;
    let page = manual.click(&page, "进入申请表").await;
    let page = manual.wait_for_control(page, "学校名称").await;
    let page = manual.begin(&page, "软件测试工程师").await;
    assert!(manual.request(ready_request(&page, "确认提交")).await.is_err(), "手动交接前也要核对必填项");
    let page = manual.must(json!({"action":"observe"})).await;
    let page = manual.basics(&page).await;
    let page = manual.fill(&page, &["学校名称"], "profile.education.0.school").await;
    assert!(manual.request(submit_request(&page, "确认提交")).await.is_err(), "仅填写没有提交授权");
    let page = manual.must(json!({"action":"observe"})).await;
    assert!(manual.request(control_request(&page, "click", "确认提交")).await.is_err(), "普通点击也不能提交");
    let page = manual.must(json!({"action":"observe"})).await;
    let ready = manual.must(ready_request(&page, "确认提交")).await;
    assert_eq!(ready["status"], "needs_user");
    assert_eq!(ready["paused"], true);
    assert_eq!(ready["terminal"], true);
    assert!(has_control(&ready, "确认提交"), "交接应保留当前已填表单");
    assert!(manual.request(json!({"action":"open","source_row":3})).await.is_err(), "交接后不切到下一家公司");
}
