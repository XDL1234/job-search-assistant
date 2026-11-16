let polling = false;
const origins = new Set(["https://www.zhipin.com"]);
const allowed = new Set([
  "observe",
  "open_job",
  "greet",
  "next",
  "return",
  "messages",
  "reply",
  "resume",
  "open_conversation",
]);

async function connection() {
  const { bridge } = await chrome.storage.local.get("bridge");
  if (
    !bridge ||
    !Number.isInteger(bridge.port) ||
    bridge.port < 1 ||
    bridge.port > 65535 ||
    !/^[A-Za-z0-9_-]{32,}$/.test(bridge.token)
  )
    throw new Error("请先输入应用提供的连接码");
  return bridge;
}
async function http(path, value) {
  const bridge = await connection();
  const result = await fetch(`http://127.0.0.1:${bridge.port}${path}`, {
    method: value === undefined ? "GET" : "POST",
    headers: {
      Authorization: `Bearer ${bridge.token}`,
      "Content-Type": "application/json",
    },
    ...(value === undefined ? {} : { body: JSON.stringify(value) }),
    signal: AbortSignal.timeout(28000),
  });
  if (!result.ok) throw new Error(`本机连接失败（${result.status}）`);
  return result.json();
}
async function screenshot(tabId) {
  const target = { tabId };
  await chrome.debugger.attach(target, "1.3");
  try {
    const result = await chrome.debugger.sendCommand(
      target,
      "Page.captureScreenshot",
      { format: "png", captureBeyondViewport: false },
    );
    return result.data;
  } finally {
    await chrome.debugger.detach(target);
  }
}
async function pageCommand(tabId, command) {
  return chrome.tabs.sendMessage(tabId,{...command,type:'job-command'});
}
async function navigate(tabId,url) {
  await chrome.tabs.update(tabId,{url});
  for(let i=0;i<20;i++) {
    await new Promise(resolve=>setTimeout(resolve,300));
    const tab=await chrome.tabs.get(tabId);
    if(tab.status==='complete' && tab.url===url) return;
  }
  throw new Error('页面未完成加载，请稍后核查');
}
async function uploadResume(tabId, command, prepared) {
  const target={tabId};
  if(!/^[A-Za-z]:[\\/]/.test(command.resume_path || '') ||
      !prepared.upload_selector || !Array.isArray(prepared.before_keys))
    throw new Error('附件材料或上传入口无效');
  await chrome.debugger.attach(target,'1.3');
  try {
    if(!(await http(`/lease/${command.id}`)).valid) throw new Error('任务已暂停');
    const {root}=await chrome.debugger.sendCommand(target,'DOM.getDocument');
    const {nodeId}=await chrome.debugger.sendCommand(target,'DOM.querySelector',{nodeId:root.nodeId,selector:prepared.upload_selector});
    if(!nodeId) throw new Error('上传入口已变化');
    const checked=await pageCommand(tabId,command);
    if(!checked?.ok || checked.result.upload_selector!==prepared.upload_selector)
      throw new Error('会话或上传入口已变化');
    if(!(await http(`/lease/${command.id}`)).valid) throw new Error('任务已暂停');
    await chrome.debugger.sendCommand(target,'DOM.setFileInputFiles',{nodeId,files:[command.resume_path]});
  } finally {await chrome.debugger.detach(target);}
  await new Promise(resolve=>setTimeout(resolve,1200));
  return pageCommand(tabId,{...command,resume_stage:'verify',
    filename:command.resume_path.split(/[\\/]/).pop(),before_keys:prepared.before_keys});
}
async function tick() {
  if (polling) return;
  polling = true;
  try {
    const command = await http("/next");
    if (!command) return;
    let response;
    try {
      if (!allowed.has(command.operation))
        throw new Error("不允许的浏览器命令");
      if (!(await http(`/lease/${command.id}`)).valid)
        throw new Error("任务已暂停或过期");
      const tabs = await chrome.tabs.query({ url: "https://www.zhipin.com/*" });
      const { bindings = {} } = await chrome.storage.session.get("bindings");
      const tab =
        tabs.find((t) => t.id === bindings[command.run_id]) ||
        (tabs.length === 1 ? tabs[0] : tabs.find((t) => t.active));
      if (!tab || !origins.has(new URL(tab.url).origin))
        throw new Error("请打开已登录的 BOSS 标签页；有多个页面时先选中目标页");
      if (bindings[command.run_id] && tab.id !== bindings[command.run_id])
        throw new Error("绑定标签页已关闭，请停止任务后重新创建");
      bindings[command.run_id] = tab.id;
      await chrome.storage.session.set({ bindings });
      let data;
      if (command.operation === "return") {
        const key = "return-" + command.run_id;
        const saved = await chrome.storage.session.get(key);
        const returned=typeof saved[key]==='string'?{url:saved[key]}:saved[key];
        if (!returned?.url || !origins.has(new URL(returned.url).origin))
          throw new Error("没有可核验的推荐页返回位置");
        if (!(await http(`/lease/${command.id}`)).valid)
          throw new Error("任务已暂停");
        await navigate(tab.id,returned.url);
        data=await pageCommand(tab.id,returned.position?{...command,operation:'restore_position',position:returned.position}:{...command,operation:'observe'});
      } else {
        if(command.operation==='messages' && !new URL(tab.url).pathname.startsWith('/web/geek/chat'))
          await navigate(tab.id,'https://www.zhipin.com/web/geek/chat');
        data=await pageCommand(tab.id,command);
        if(command.operation==='resume' && data?.ok)
          data=await uploadResume(tab.id,command,data.result);
      }
      if (!data?.ok) throw new Error(data?.error || "页面未能执行命令");
      if (data.result.navigate) {
        const destination = new URL(data.result.navigate);
        if (
          !origins.has(destination.origin) ||
          !destination.pathname.startsWith("/job_detail/")
        )
          throw new Error("岗位链接不在允许范围");
        await chrome.storage.session.set({
          ["return-" + command.run_id]: {url:data.result.return_url,position:data.result.return_position},
        });
        await navigate(tab.id,destination.href);
      }
      // 截图始终针对绑定标签页，即使该页处于后台；不截取用户的其他标签。
      const image = await screenshot(tab.id);
      const current = await chrome.tabs.get(tab.id);
      if (!origins.has(new URL(current.url).origin))
        throw new Error("截图页面已离开授权网站");
      if (data.result.url && current.url!==data.result.url && !data.result.navigate)
        throw new Error('截图前页面已变化，请核查本次操作');
      response = {
        ok: true,
        result: {
          ...data.result,
          url: current.url,
          title: current.title,
          screenshot: image,
          tab_id: tab.id,
        },
      };
    } catch (error) {
      response = { ok: false, error: String(error.message || error) };
    }
    await http("/result", { id: command.id, response });
    await chrome.storage.local.set({
      status: response.ok ? "已连接，任务正常" : response.error,
    });
  } catch (error) {
    await chrome.storage.local.set({ status: String(error.message || error) });
  } finally {
    polling = false;
  }
}
chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (sender.id !== chrome.runtime.id) return;
  if (
    message.type === "lease" &&
    sender.tab &&
    origins.has(new URL(sender.tab.url).origin)
  ) {
    http(`/lease/${message.id}`)
      .then(respond)
      .catch(() => respond({ valid: false }));
    return true;
  }
  if (sender.tab || message.type !== "connect") return;
  (async () => {
    const match = /^(\d{1,5}):([A-Za-z0-9_-]{32,})$/.exec(message.code.trim());
    if (!match || +match[1] > 65535 || +match[1] < 1)
      throw new Error("连接码格式不正确");
    await chrome.storage.local.set({
      bridge: { port: +match[1], token: match[2] },
    });
    const value = await http("/status");
    await chrome.alarms.create("job-bridge", { periodInMinutes: 0.5 });
    void tick();
    return value;
  })()
    .then(() => respond({ ok: true }))
    .catch((e) => respond({ ok: false, error: e.message }));
  return true;
});
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "job-bridge") void tick();
});
chrome.runtime.onStartup.addListener(() => void tick());
setInterval(() => void tick(), 3000);
void tick();
