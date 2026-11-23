//! 隔离 Chrome 的真实媒体测试；不会连接用户 Chrome 或招聘网站。
use super::*;
use tauri::ipc::InvokeResponseBody;
use tokio::{io::AsyncWriteExt, net::TcpListener};

#[link(name = "user32")]
unsafe extern "system" {
    fn GetForegroundWindow() -> isize;
    fn SetForegroundWindow(window: isize) -> i32;
}

async fn next_matching(
    events: &mut mpsc::UnboundedReceiver<Value>,
    predicate: impl Fn(&Value) -> bool,
) -> Result<Value, String> {
    tokio::time::timeout(Duration::from_secs(12), async {
        while let Some(event) = events.recv().await {
            if predicate(&event) {
                return Ok(event);
            }
            if event["type"] == "status" && event["state"] == "error" {
                return Err(event["message"].as_str().unwrap_or("预览错误").to_owned());
            }
        }
        Err("预览事件通道提前关闭".into())
    })
    .await
    .map_err(|_| "等待真实预览事件超时".to_string())?
}

fn channel() -> (Channel<PreviewEvent>, mpsc::UnboundedReceiver<Value>) {
    let (sender, receiver) = mpsc::unbounded_channel();
    let events = Channel::new(move |body| {
        if let InvokeResponseBody::Json(text) = body {
            if let Ok(value) = serde_json::from_str(&text) {
                let _ = sender.send(value);
            }
        }
        Ok(())
    });
    (events, receiver)
}

async fn cdp_command(url: &str, method: &str, params: Value) -> Result<Value, String> {
    cdp_page_command(url, None, method, params).await
}

async fn cdp_page_command(
    url: &str,
    target: Option<&str>,
    method: &str,
    params: Value,
) -> Result<Value, String> {
    tokio::time::timeout(Duration::from_secs(5), async {
        let (mut socket, _) = tokio_tungstenite::connect_async(url)
            .await
            .map_err(|_| "隔离CDP连接失败")?;
        let mut session=None;
        if let Some(target)=target {
            socket.send(Message::Text(json!({"id":1,"method":"Target.attachToTarget","params":{"targetId":target,"flatten":true}}).to_string().into())).await.map_err(|_|"隔离页面连接失败")?;
            while let Some(message)=socket.next().await {
                if let Ok(Message::Text(text))=message {
                    let value:Value=serde_json::from_str(&text).map_err(|_|"隔离页面消息无效")?;
                    if value["id"]==1 {
                        session=value["result"]["sessionId"].as_str().map(str::to_owned);
                        if session.is_none(){return Err("隔离页面未返回会话".into());}
                        break;
                    }
                }
            }
        }
        let mut request=json!({"id":2,"method":method,"params":params});
        if let Some(session)=session {request["sessionId"]=json!(session);}
        socket
            .send(Message::Text(
                request.to_string().into(),
            ))
            .await
            .map_err(|_| "隔离CDP命令失败")?;
        while let Some(message) = socket.next().await {
            if let Ok(Message::Text(text)) = message {
                let value: Value = serde_json::from_str(&text).map_err(|_| "隔离CDP消息无效")?;
                if value["id"] == 2 {
                    if !value["error"].is_null() {
                        return Err("隔离CDP拒绝命令".into());
                    }
                    return Ok(value["result"].clone());
                }
            }
        }
        Err("隔离CDP提前关闭".into())
    })
    .await
    .map_err(|_| "隔离CDP命令超时".to_owned())?
}

async fn synthetic_pixel_check(images: &Value) -> Result<Value, String> {
    // 仅测试使用已有 Pillow 校验合成帧；产品媒体链路不经过 Python。
    let python = std::env::var_os("JOB_ASSISTANT_PYTHON").unwrap_or_else(|| "python.exe".into());
    let script="import sys,json,base64,io;from PIL import Image;data=json.load(sys.stdin);print(json.dumps({key:Image.open(io.BytesIO(base64.b64decode(value))).convert('RGB').getpixel((1,1)) for key,value in data.items() if value}))";
    let mut command = Command::new(python);
    command
        .args(["-X", "utf8", "-c", script])
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .kill_on_drop(true)
        .creation_flags(0x08000000);
    let mut child = command.spawn().map_err(|_| "测试像素校验启动失败")?;
    let mut input = child.stdin.take().ok_or("测试像素校验缺少输入")?;
    input
        .write_all(&serde_json::to_vec(images).map_err(|_| "合成图像数据无效")?)
        .await
        .map_err(|_| "写入测试图像失败")?;
    drop(input);
    let result = tokio::time::timeout(Duration::from_secs(5), child.wait_with_output())
        .await
        .map_err(|_| "测试像素校验超时")?
        .map_err(|_| "测试像素校验退出失败")?;
    if !result.status.success() {
        return Err("测试像素校验未成功".into());
    }
    serde_json::from_slice(&result.stdout).map_err(|_| "测试像素结果无效".into())
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "需要本机 Chrome 和固定 agent-browser 运行时；显式运行隔离集成测试"]
async fn real_chrome_preview_obeys_ack_scope_and_external_ownership() {
    run_real_chrome_preview(false, false).await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "单独验证隐藏标签帧流；需要本机 Chrome 和固定原生运行时"]
async fn real_chrome_preview_on_hidden_target() {
    run_real_chrome_preview(true, false).await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
#[ignore = "需要桌面独占时段；独立 headed Chrome 后台窗口验证"]
async fn real_chrome_preview_background_window() {
    run_real_chrome_preview(false, true).await;
}

async fn run_real_chrome_preview(hidden_target: bool, headed: bool) {
    let previous_foreground = unsafe { GetForegroundWindow() };
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../../.verification/native-preview-smoke")
        .join(uuid::Uuid::new_v4().to_string());
    std::fs::create_dir_all(&root).unwrap();
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let origin = format!("http://{}", listener.local_addr().unwrap());
    let server = tokio::spawn(async move {
        loop {
            let Ok((mut stream, _)) = listener.accept().await else {
                break;
            };
            tokio::spawn(async move {
                let mut request = [0u8; 4096];
                if stream.read(&mut request).await.is_err() {
                    return;
                }
                let body = b"<!doctype html><html><title>isolated preview</title><body><h1>Native JPEG proof</h1><p id='counter'>0</p><script>document.body.style.background=location.pathname.includes('unrelated')?'#2244ff':location.pathname.includes('second')?'#ff2211':'#ffffff';window.nativePreviewTimer=setInterval(()=>document.querySelector('#counter').textContent=Date.now(),80)</script></body></html>";
                let header = format!("HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nCache-Control: no-store\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",body.len());
                let _ = stream.write_all(header.as_bytes()).await;
                let _ = stream.write_all(body).await;
            });
        }
    });
    let profile = root.join("chrome-profile");
    let mut command = Command::new("C:/Program Files/Google/Chrome/Application/chrome.exe");
    command
        .args([
            "--remote-debugging-port=0",
            "--remote-debugging-address=127.0.0.1",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
        ])
        .arg(format!("--user-data-dir={}", profile.display()))
        .arg(&origin)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .kill_on_drop(true)
        .creation_flags(0x08000000);
    if !headed {
        command.arg("--headless=new");
    } else {
        command.args(["--window-size=900,650", "--window-position=30,30"]);
    }
    let mut chrome = command.spawn().unwrap();
    let endpoint = tokio::time::timeout(Duration::from_secs(15), async {
        loop {
            if let Ok(contents) = std::fs::read_to_string(profile.join("DevToolsActivePort")) {
                let mut lines = contents.lines();
                if let (Some(port), Some(path)) = (lines.next(), lines.next()) {
                    if let Ok(port) = port.parse::<u16>() {
                        break (port, path.to_owned());
                    }
                }
            }
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
    })
    .await
    .unwrap();
    let mut runtime = BrowserRuntime::new(Path::new("missing-test-resources"), &root.join("app"));
    let mut checks = Vec::<&str>::new();
    let mut bring_to_front_changed_focus = None;
    let result: Result<Value,String> = async {
        let browser_url=format!("ws://127.0.0.1:{}{}",endpoint.0,endpoint.1);
        cdp_command(&browser_url,"Target.createTarget",json!({"url":format!("{origin}/second")})).await?;
        runtime.connect_args(&["--cdp",&browser_url,"tab","list","--json"]).await?;
        let connected = runtime.output(&["tab","list","--json"], true).await?;
        if !connected["tabs"].as_array().is_some_and(|tabs|tabs.len()==2 && tabs.iter().all(|tab|tab["url"].as_str().is_some_and(|url|url.starts_with(&origin)))) {
            return Err("直接CDP枚举制造了空白标签或丢失原有页面".into());
        }
        let target = connected["tabs"].as_array().and_then(|tabs|tabs.get(1))
            .and_then(|tab|tab["targetId"].as_str()).ok_or("未找到隔离页面targetId")?.to_owned();
        if runtime.select_tab(target.clone()).await.is_ok() { return Err("生产域约束错误接受 localhost".into()); }
        runtime.scope.origin=Some(origin.clone());
        runtime.select_tab(target.clone()).await?;
        let other=cdp_command(&browser_url,"Target.createTarget",json!({"url":format!("{origin}/unrelated")})).await?;
        cdp_command(&browser_url,"Target.activateTarget",json!({"targetId":other["targetId"]})).await?;
        let retained=runtime.output(&["tab","list","--json"],true).await?;
        if !retained["tabs"].as_array().is_some_and(|tabs|tabs.iter().any(|tab|tab["targetId"]==target && tab["active"]==true)) {
            return Err("外部新建或激活其他标签夺取了已绑定目标".into());
        }
        checks.push("multi_tab_pin_preserved_after_external_activation");
        if !hidden_target {
            cdp_command(&browser_url,"Target.activateTarget",json!({"targetId":target})).await?;
        }
        if headed {
            unsafe {SetForegroundWindow(previous_foreground);}
            tokio::time::sleep(Duration::from_millis(200)).await;
            if unsafe {GetForegroundWindow()}!=previous_foreground {return Err("无法将隔离Chrome置于原前台窗口后方".into());}
            cdp_page_command(&browser_url,Some(&target),"Page.bringToFront",json!({})).await?;
            bring_to_front_changed_focus=Some(unsafe {GetForegroundWindow()}!=previous_foreground);
            unsafe {SetForegroundWindow(previous_foreground);}
            tokio::time::sleep(Duration::from_millis(200)).await;
            if unsafe {GetForegroundWindow()}!=previous_foreground {return Err("bringToFront后无法恢复原前台窗口".into());}
            runtime.select_tab(target.clone()).await?;
            runtime.output(&["--pin-tab", "eval", "document.title", "--json"], true).await?;
            if unsafe {GetForegroundWindow()}!=previous_foreground {
                return Err("绑定或操作 BOSS 标签时抢占了桌面前台".into());
            }
            checks.push("background_tab_binding_and_dom");
        }
        let (on_event,mut events)=channel();
        let subscription=runtime.start_preview(on_event).await?;
        let epoch=subscription["epoch"].as_str().ok_or("缺少预览代际")?.to_owned();
        if hidden_target {
            let dom=cdp_page_command(&browser_url,Some(&target),"Runtime.evaluate",json!({"expression":"(()=>{clearInterval(window.nativePreviewTimer);const before=getComputedStyle(document.body).backgroundColor;document.body.style.background='#12a654';document.getElementById('counter').textContent='forced-hidden-change';return {hidden:document.hidden,visibility:document.visibilityState,marker:document.getElementById('counter').textContent,focused:document.hasFocus(),before,changed_at:Date.now()};})()","returnByValue":true})).await?;
            let dom=dom["result"]["value"].clone();
            if dom["marker"]!="forced-hidden-change" {return Err("隐藏页DOM强制变化未确认".into());}
            let mut after_change=None;
            let _=tokio::time::timeout(Duration::from_secs(3),async {
                while let Some(event)=events.recv().await {
                    if event["type"]=="frame" {
                        let seq=event["seq"].as_u64().unwrap();
                        runtime.ack_preview(&epoch,seq).await?;
                        after_change=Some(event);
                    } else if event["type"]=="status" && event["state"]=="error" {break;}
                }
                Ok::<(),String>(())
            }).await;
            let captured=cdp_page_command(&browser_url,Some(&target),"Page.captureScreenshot",json!({"format":"jpeg","quality":65,"captureBeyondViewport":false,"fromSurface":true})).await;
            let capture_ok=captured.as_ref().ok().and_then(|value|value["data"].as_str()).is_some_and(|data|data.starts_with("/9j/"));
            let artifacts=json!({"stream":after_change.as_ref().map(|event|event["data"].clone()),"direct_capture":captured.as_ref().ok().map(|value|value["data"].clone())});
            let pixels=synthetic_pixel_check(&artifacts).await?;
            let green=|color:&Value|color.as_array().is_some_and(|channels|channels.len()==3 && channels.iter().zip([18,166,84]).all(|(channel,target)|channel.as_i64().is_some_and(|value|(value-target).abs()<=3)));
            let marker_observed=green(&pixels["stream"]);
            let mut diagnostic=json!({"hidden_target":true,"dom":dom,"stream_frame_received":after_change.is_some(),"stream_marker_observed":marker_observed,"pixels":pixels,"direct_capture_succeeded":capture_ok,"direct_capture_marker_observed":green(&pixels["direct_capture"]),"direct_capture_error":captured.as_ref().err(),"checks":checks});
            std::fs::write(root.join("hidden-diagnostic.json"),serde_json::to_vec_pretty(&diagnostic).unwrap()).map_err(|_|"无法写隐藏页诊断")?;
            std::fs::write(root.join("synthetic-frames.json"),serde_json::to_vec(&artifacts).unwrap()).map_err(|_|"无法写合成帧证据")?;
            if !marker_observed {return Err(format!("隐藏页已确认DOM变化但原生流画面未更新；直接CDP取图成功={capture_ok}；诊断 {}",root.join("hidden-diagnostic.json").display()));}
            if dom["hidden"] != true { return Err("取帧改变了隐藏标签的可见状态".into()); }
            let pending = next_matching(&mut events, |event| event["type"] == "frame").await?;
            if tokio::time::timeout(Duration::from_secs(2), next_matching(&mut events, |event| event["type"] == "frame")).await.is_ok() {
                return Err("隐藏标签没有等待界面确认".into());
            }
            cdp_command(&browser_url,"Target.activateTarget",json!({"targetId":target})).await?;
            cdp_page_command(&browser_url,Some(&target),"Runtime.evaluate",json!({"expression":"document.body.style.background='#b514a0'"})).await?;
            runtime.ack_preview(&epoch, pending["seq"].as_u64().ok_or("缺少隐藏帧序号")?).await?;
            tokio::time::timeout(Duration::from_secs(8), async {
                loop {
                    let frame = next_matching(&mut events, |event| event["type"] == "frame").await?;
                    let bytes = STANDARD.decode(frame["data"].as_str().unwrap_or("")).map_err(|_|"可见帧内容无效")?;
                    let image = image::load_from_memory(&bytes).map_err(|_|"可见帧图片无效")?.into_rgb8();
                    let color = image.get_pixel(1, 1).0;
                    runtime.ack_preview(&epoch, frame["seq"].as_u64().ok_or("缺少可见帧序号")?).await?;
                    if color.into_iter().zip([181,20,160]).all(|(value,wanted)| u8::abs_diff(value,wanted)<=3) {
                        return Ok::<(),String>(());
                    }
                }
            }).await.map_err(|_|"隐藏标签切回前台后的画面未更新")??;
            cdp_command(&browser_url,"Target.activateTarget",json!({"targetId":other["targetId"]})).await?;
            // 给后台取帧一次机会，并让该帧继续等待界面确认。
            next_matching(&mut events, |event| event["type"] == "frame").await?;
            cdp_page_command(&browser_url,Some(&target),"Page.navigate",json!({"url":"data:text/html,<h1>outside-scope</h1>"})).await?;
            let error = next_matching(&mut events, |event| event["type"] == "status" && event["state"] == "error").await?;
            if !error["message"].as_str().is_some_and(|value| value.contains("授权")) {
                return Err("隐藏标签离开授权域时未提示离域".into());
            }
            checks.extend(["hidden_target_remained_hidden", "hidden_frame_requires_ack", "restored_visible_frame", "offsite_stopped_while_unacked"]);
            diagnostic["checks"] = json!(checks);
            return Ok(diagnostic);
        }
        let first=next_matching(&mut events,|event|event["type"]=="frame").await.map_err(|error|format!("首次后台画面：{error}"))?;
        let first_seq=first["seq"].as_u64().ok_or("缺少真实帧序号")?;
        if !first["data"].as_str().is_some_and(|data|data.starts_with("/9j/")) {
            return Err("未收到真实 JPEG 帧".into());
        }
        checks.push("first_jpeg");
        if runtime.ack_preview("old-epoch",first_seq).await.is_ok() { return Err("旧ACK被接受".into()); }
        let before_ack=tokio::time::timeout(Duration::from_secs(10),async {
            while let Some(event)=events.recv().await { if event["type"]=="frame" { return true; } }
            false
        }).await;
        if before_ack==Ok(true) { return Err("渲染确认前提前交付了下一帧".into()); }
        runtime.ack_preview(&epoch,first_seq).await?;
        let second=next_matching(&mut events,|event|event["type"]=="frame").await.map_err(|error|format!("后台停顿恢复：{error}"))?;
        if second["seq"].as_u64().is_none_or(|seq|seq<=first_seq) { return Err("真实帧序号未递增".into()); }
        let now=std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_err(|_|"本机时间无效")?.as_millis() as f64;
        let timestamp=second["metadata"]["timestamp"].as_f64().ok_or("真实帧没有采集时间")?;
        let recovery_age=now-timestamp;
        if !(0.0..2000.0).contains(&recovery_age) { return Err(format!("恢复显示了积压旧帧：{recovery_age}ms")); }
        if headed && unsafe {GetForegroundWindow()}!=previous_foreground {return Err("后台预览期间抢占了操作系统前台".into());}
        checks.push("latest_frame_after_renderer_stall");
        runtime.stop_preview_if_epoch("old-epoch").await;
        runtime.ack_preview(&epoch,second["seq"].as_u64().unwrap()).await?;
        runtime.stop_preview_if_epoch(&epoch).await;
        let (on_event,mut events)=channel();
        let next=runtime.start_preview(on_event).await?;
        if next["epoch"]==epoch { return Err("重新订阅复用了旧代际".into()); }
        let _=next_matching(&mut events,|event|event["type"]=="frame").await.map_err(|error|format!("重新订阅：{error}"))?;
        runtime.output(&["open","data:text/html,<h1>outside-scope</h1>","--json"],true).await?;
        let outside=next_matching(&mut events,|event|event["type"]=="status" && event["state"]=="error").await.map_err(|error|format!("离域断流：{error}"))?;
        if !outside["message"].as_str().is_some_and(|message|message.contains("授权")) { return Err("离域未明确报错".into()); }
        runtime.output(&["open",&origin,"--json"],true).await?;
        runtime.output(&["tab","new","about:blank","--json"],true).await?;
        runtime.select_tab(target.clone()).await?;
        let (on_event,mut events)=channel();
        runtime.start_preview(on_event).await?;
        let _=next_matching(&mut events,|event|event["type"]=="frame").await.map_err(|error|format!("关闭前画面：{error}"))?;
        runtime.output(&["tab","close","--json"],true).await?;
        let closed=next_matching(&mut events,|event|event["type"]=="status" && event["state"]=="error").await.map_err(|error|format!("目标关闭断流：{error}"))?;
        runtime.shutdown().await;
        if runtime.started.load(Ordering::SeqCst) { return Err("关闭后的会话仍被标记为运行中".into()); }
        if chrome.try_wait().map_err(|_|"无法检查隔离Chrome")?.is_some() { return Err("断开session误关了外接Chrome".into()); }
        Ok(json!({"real_jpeg":true,"render_ack":true,"old_epoch_rejected":true,"non_boss_rejected":true,
            "outside_scope":outside["message"],"target_closed":closed["message"],"external_chrome_preserved":true,
            "first_seq":first_seq,"second_seq":second["seq"],"renderer_stall_ms":10000,"recovery_frame_age_ms":recovery_age,"multi_tab_pin_preserved":true}))
    }.await;
    runtime.shutdown().await;
    let browser_url = format!("ws://127.0.0.1:{}{}", endpoint.0, endpoint.1);
    if let Ok((mut browser, _)) = tokio_tungstenite::connect_async(browser_url).await {
        let _ = browser
            .send(Message::Text(
                json!({"id":1,"method":"Browser.close"}).to_string().into(),
            ))
            .await;
    }
    if tokio::time::timeout(Duration::from_secs(5), chrome.wait())
        .await
        .is_err()
    {
        let _ = chrome.kill().await;
        let _ = chrome.wait().await;
    }
    server.abort();
    if headed {
        unsafe {
            SetForegroundWindow(previous_foreground);
        }
    }
    match result {
        Ok(report) => {
            let mut report = report;
            report["hidden_target"] = json!(hidden_target);
            report["headed"] = json!(headed);
            report["bring_to_front_changed_os_focus"] = json!(bring_to_front_changed_focus);
            report["checks"] = json!(checks);
            std::fs::write(
                root.join("report.json"),
                serde_json::to_vec_pretty(&report).unwrap(),
            )
            .unwrap();
            println!(
                "native preview proof: {}",
                root.join("report.json").display()
            );
        }
        Err(error) => {
            let _ = std::fs::write(
                root.join("failure.json"),
                serde_json::to_vec_pretty(
                    &json!({"hidden_target":hidden_target,"checks":checks,"error":error}),
                )
                .unwrap(),
            );
            panic!(
                "隔离原生预览链路失败：{error}；记录 {}",
                root.join("failure.json").display()
            );
        }
    }
}
