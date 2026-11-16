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
        job: card.job,
        company: card.company,
      };
    }
    if (command.operation === "next") {
      await lease(command);
      const next = nodes(
        ".options-pages a,.pagination a,.ui-icon-arrow-right",
      ).find((node) => shown(node) && /下一页|›|»/.test(text(node)));
      if (next) {
        next.click();
        await sleep(700);
        return snapshot();
      }
      window.scrollBy({ top: 700, behavior: "instant" });
      await sleep(700);
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
      await lease(command);
      button.click();
      await sleep(1100);
      const body = guard();
      const sent =
        /已向.*发送|打招呼成功|沟通成功|已发送招呼|已发送消息/.test(body) ||
        /继续沟通/.test(
          text(document.querySelector(".job-banner,.job-op,.job-detail")),
        );
      return { sent, uncertain: !sent, ...snapshot() };
    }
    if (command.operation === "messages") {
      const messages = nodes(
        ".message-item,.chat-message,.chat-conversation .message",
      )
        .filter(shown)
        .map((node, index) => ({
          key: node.getAttribute("data-id") || `${index}:${text(node)}`,
          text: text(node),
        }));
      return { messages, ...snapshot() };
    }
    if (command.operation === "reply") {
      const current = nodes(
        ".message-item,.chat-message,.chat-conversation .message",
      )
        .filter(shown)
        .map(text)
        .join("\n");
      if (!command.message_text || !current.endsWith(command.message_text))
        throw new Error("会话消息已更新或无法核对，停止发送");
      if (/面试|过来聊聊|预约时间/.test(current))
        throw new Error("面试事项保持人工接管");
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
      exactButton(/^发送$/).click();
      await sleep(900);
      const sent = nodes(
        ".message-item,.chat-message,.chat-conversation .message",
      ).some((node) => text(node) === command.text);
      return { sent, uncertain: !sent, ...snapshot() };
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
