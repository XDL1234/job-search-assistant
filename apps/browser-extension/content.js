(() => {
  if (globalThis.__jobAssistantInstalled) return;
  globalThis.__jobAssistantInstalled = true;
  const text = (node) => (node?.innerText || node?.textContent || "").trim();
  const shown = (node) =>
    Boolean(
      node &&
        node.getClientRects().length &&
        !node.disabled &&
        node.getAttribute("aria-disabled") !== "true",
    );
  const nodes = (selector) => Array.from(document.querySelectorAll(selector));
  const one = (root, selector) => root.querySelector(selector);
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
  const messageSelector = '.message-item,.chat-message,.chat-conversation .message';
  function listScroller() {
    const card = cards()[0]?._element;
    for (let parent = card?.parentElement; parent; parent = parent.parentElement) {
      if (parent.scrollHeight > parent.clientHeight + 10 &&
          /auto|scroll/.test(getComputedStyle(parent).overflowY)) return parent;
    }
    return document.scrollingElement || document.documentElement;
  }
  function position() {
    const scroll = listScroller();
    return {top:scroll.scrollTop || 0,height:scroll.scrollHeight,viewport:scroll.clientHeight};
  }
  function stableId(node) {
    for (const key of ['data-mid','data-message-id','data-id','data-uid','data-user-id']) {
      const value = node?.getAttribute(key);
      if (value) return key + ':' + value;
    }
    return '';
  }
  function conversations() {
    return nodes('.user-list li[role="listitem"],.chat-list li[role="listitem"],.conversation-item[data-id],li[role="listitem"][data-id]')
      .filter(shown).map(node => ({
        key:stableId(node),label:text(one(node,'.name-box,.name-text')),
        unread:shown(one(node,'.unread-count,.badge-count,.notice-badge')) && text(one(node,'.unread-count,.badge-count,.notice-badge'))!=='0',
        active:/(^|\s)(active|selected)(\s|$)/.test(String(node.className || '')),
        _element:node,
      })).filter(row=>row.key);
  }
  function chatSnapshot() {
    const active = conversations().filter(row=>row.active);
    const conversation = active.length===1 ? active[0].key : '';
    const messages = nodes(messageSelector).filter(shown).map(node=>{
      const classes=String(node.className || '');
      const incoming=/(^|\s)(item-friend|message-other|incoming)(\s|$)/.test(classes);
      const outgoing=/(^|\s)(item-myself|message-self|outgoing)(\s|$)/.test(classes);
      return {key:stableId(node),text:text(node),direction:incoming && !outgoing?'incoming':outgoing && !incoming?'outgoing':'unknown'};
    });
    const jobs=nodes('.chat-content a[href*="/job_detail/"],.chat-conversation a[href*="/job_detail/"],.chat-job a[href*="/job_detail/"]')
      .map(node=>new URL(node.href,location.href)).filter(url=>url.origin===location.origin).map(url=>url.pathname);
    return {conversation,messages,job_keys:[...new Set(jobs)],
      conversations:conversations().map(({_element,...row})=>row)};
  }
  function verifyChat(command) {
    const chat=chatSnapshot();
    const latest=chat.messages.at(-1);
    if (!command.conversation || chat.conversation!==command.conversation || !latest?.key ||
        latest.key!==command.message_key || latest.direction!=='incoming' || latest.text!==command.message_text)
      throw new Error('会话或最新消息已变化，停止发送');
    if (!command.manual && /面试|interview|过来聊聊|预约时间|来(?:我们)?公司.*(?:聊|见)|到(?:公司|现场).*聊/i.test(latest.text))
      throw new Error('面试事项保持人工接管');
    return chat;
  }
  function guard() {
    if (location.origin !== "https://www.zhipin.com")
      throw new Error("不是授权的 BOSS 网站");
    const body = text(document.body);
    if (/安全验证|访问受限|操作过于频繁|请完成验证/.test(body))
      throw new Error("平台要求人工验证或限制访问，已停止");
    if (/扫码登录|登录后查看|请先登录/.test(body))
      throw new Error("请先完成 BOSS 登录");
    return body;
  }
  function cards() {
    const seen = new Set();
    return nodes(".job-card-wrapper,.job-card-box,.card-area")
      .filter(shown)
      .flatMap((card) => {
        const link = one(card, 'a[href*="/job_detail/"]');
        if (!link) return [];
        const url = new URL(link.href, location.href);
        if (url.origin !== location.origin || seen.has(url.pathname)) return [];
        seen.add(url.pathname);
        return [
          {
            key: url.pathname,
            url: url.href,
            job: text(one(card, ".job-name,.job-title")) || text(link),
            company: text(
              one(card, ".company-name,.company-info a,.company-name a"),
            ),
            recruiter: text(one(card, ".boss-name,.info-public")),
            tags: text(one(card, ".tag-list,.job-info,.job-card-footer")),
            contacted: /继续沟通|已沟通/.test(text(card)),
            _element: card,
          },
        ];
      });
  }
  function snapshot() {
    const body = guard();
    return {
      url: location.href,
      title: document.title,
      cards: cards().map(({ _element, ...card }) => card),
      detail: text(
        document.querySelector(".job-detail,.job-sec-text,.job-detail-section"),
      ),
      heading: text(
        document.querySelector(
          ".job-banner .name h1,.job-name h1,.job-title h1",
        ),
      ),
      company: text(
        document.querySelector(".company-info a,.sider-company .company-name"),
      ),
      filters: nodes(
        ".filter-item.active,.filter-select .label,.city-label,.expect-item.active",
      )
        .map(text)
        .filter(Boolean),
      text: body.slice(-18000),
      position: position(),
    };
  }
  function exactButton(pattern) {
    const candidates = nodes('button,a,[role="button"]').filter(
      (node) => shown(node) && pattern.test(text(node)),
    );
    if (candidates.length !== 1)
      throw new Error("无法唯一确定操作按钮，停止等待核对");
    return candidates[0];
  }
  async function lease(command) {
    guard();
    const result = await chrome.runtime.sendMessage({
      type: "lease",
      id: command.id,
    });
    if (!result?.valid || Date.now() / 1000 > command.expires)
      throw new Error("任务已暂停或命令过期");
  }
  async function execute(command) {
    guard();
    if (command.operation === "observe") return snapshot();
    if (command.operation === "open_job") {
      const card = cards().find((card) => card.key === command.key);
      if (!card) throw new Error("岗位不在当前列表，请重新观察");
      const link = one(card._element, 'a[href*="/job_detail/"]');
      await lease(command);
      // 使用同一标签页的正常导航，扩展始终绑定当前页面，不打开未知新窗口。
      return {
        navigate: link.href,
        return_url: location.href,
        return_position: position(),
        job: card.job,
        company: card.company,
      };
    }
    if (command.operation === "next") {
      await lease(command);
      const next = nodes(
        ".options-pages a,.pagination a,.ui-icon-arrow-right",
      ).find((node) => shown(node) && !/disabled/.test(String(node.className||'')) && /下一页|›|»/.test(text(node)));
      if (next) {
        next.click();
        await sleep(700);
        return snapshot();
      }
      const scroll=listScroller();
      scroll.scrollTop = scroll.scrollHeight;
      await sleep(700);
      return snapshot();
    }
    if (command.operation === 'restore_position') {
      const top=command.position?.top;
      if (!Number.isFinite(top) || top<0 || top>1000000) throw new Error('返回位置无效');
      for(let i=0;i<10;i++) {
        await lease(command);
        const scroll=listScroller(); scroll.scrollTop=top;
        if (Math.abs(scroll.scrollTop-top)<20) break;
        await sleep(400);
      }
      return snapshot();
    }
    if (command.operation === "greet") {
      const expected = new URL(command.url, location.href);
      if (
        expected.origin !== location.origin ||
        expected.pathname !== location.pathname
      )
        throw new Error("当前岗位与待投递岗位不一致");
      if (
        /继续沟通|已沟通/.test(
          text(document.querySelector(".job-banner,.job-op,.job-detail")),
        )
      )
        return { sent: false, already_contacted: true, ...snapshot() };
      const button = exactButton(/^(立即沟通|立即开聊|打招呼)$/);
      const before=text(button);
      await lease(command);
      button.click();
      await sleep(1100);
      guard();
      if (expected.pathname!==location.pathname) throw new Error('沟通后页面已切换，请核查回执');
      const sent = /^(立即沟通|立即开聊|打招呼)$/.test(before) &&
        /继续沟通|已沟通/.test(text(document.querySelector('.job-banner,.job-op,.job-detail')));
      return { sent, uncertain: !sent, ...snapshot() };
    }
    if (command.operation === "messages") {
      return {...snapshot(),...chatSnapshot()};
    }
    if (command.operation==='open_conversation') {
      const matches=conversations().filter(row=>row.key===command.conversation);
      if(matches.length!==1) throw new Error('无法唯一确定目标会话');
      await lease(command);matches[0]._element.click();await sleep(700);
      const chat=chatSnapshot();
      if(chat.conversation!==command.conversation) throw new Error('未能打开指定会话');
      return {...snapshot(),...chat};
    }
    if (command.operation==='resume') {
      if(command.resume_stage==='verify') {
        const chat=chatSnapshot();
        if(chat.conversation!==command.conversation ||
            !chat.messages.some(message=>message.key===command.message_key && message.direction==='incoming' && message.text===command.message_text))
          throw new Error('上传后会话或原始请求已变化，请人工核查');
        const sent=chat.messages.some(message=>message.direction==='outgoing' && message.key &&
          !command.before_keys.includes(message.key) && message.text.includes(command.filename) &&
          !/发送失败|重试|上传中/.test(message.text));
        return {sent,uncertain:!sent,...snapshot(),...chat};
      }
      const chat=verifyChat(command);
      const files=nodes('.chat-content input[type="file"],.chat-container input[type="file"],.chat-conversation input[type="file"]')
        .filter(node=>!node.disabled && /pdf|doc/i.test(node.accept || ''));
      if(files.length!==1) throw new Error('无法唯一核实指定简历上传入口，请人工发送');
      await lease(command);
      files[0].setAttribute('data-job-assistant-upload',command.id);
      return {upload_selector:`input[data-job-assistant-upload="${command.id}"]`,
        before_keys:chat.messages.map(message=>message.key).filter(Boolean)};
    }
    if (command.operation === "reply") {
      let before;
      try {before=verifyChat(command);}
      catch(error) {return {sent:false,not_done:true,reason:error.message,...snapshot(),...chatSnapshot()};}
      const input = document.querySelector(
        'textarea.chat-input,[contenteditable="true"].chat-input,#chat-input',
      );
      if (
        !shown(input) ||
        typeof command.text !== "string" ||
        !command.text.trim()
      )
        throw new Error("未找到可用聊天输入框");
      await lease(command);
      if (input.tagName === "TEXTAREA") {
        const setter = Object.getOwnPropertyDescriptor(
          HTMLTextAreaElement.prototype,
          "value",
        ).set;
        setter.call(input, command.text);
      } else input.textContent = command.text;
      input.dispatchEvent(new Event("input", { bubbles: true }));
      await lease(command);
      verifyChat(command);
      exactButton(/^发送$/).click();
      await sleep(900);
      const after=chatSnapshot();
      const keys=new Set(before.messages.map(message=>message.key));
      const sent=after.conversation===command.conversation && after.messages.some(message=>
        message.direction==='outgoing' && message.key && !keys.has(message.key) && message.text===command.text);
      return { sent, uncertain: !sent, ...snapshot(),...after };
    }
    throw new Error("不支持的页面命令");
  }
  chrome.runtime.onMessage.addListener((message, sender, respond) => {
    if (sender.id !== chrome.runtime.id || message.type !== "job-command")
      return;
    execute(message)
      .then((result) => respond({ ok: true, result }))
      .catch((error) => respond({ ok: false, error: error.message }));
    return true;
  });
})();
