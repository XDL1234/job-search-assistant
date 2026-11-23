((args) => {
  const refuse = (reason) => { throw new Error('WEB_' + reason); };
  if (window !== window.top || location.href !== args.url) refuse('STALE');
  const state = globalThis.__jobAssistantCompanyPage ??= {
    document: crypto.randomUUID(), refs: new WeakMap(), next: 0, selections: new WeakMap(),
  };
  state.selections ??= new WeakMap();
  if (args.document && args.document !== state.document) refuse('STALE');

  const controlsSelector = 'input,textarea,select,button,a[href],summary,[role="button"],[role="option"],[role="menuitem"],[role="tab"],[role="combobox"],[contenteditable="true"],nav li,header li';
  const sectionSelector = 'fieldset,section,article,[data-section],[role="group"],[role="dialog"],dialog';
  const authentication = /(?:密码|验证码|短信验证|扫码登录|请先登录|登录后继续|人机验证|安全验证|滑块验证|password|captcha|one[- ]time code|verify you are human)/i;
  const sensitive = /(?:身份证|证件(?:号码|类型)?|护照|银行卡|纳税|密码|验证码|验证身份|passport|identity.?number|social security|credit.?card|password|otp|captcha)/i;
  const submit = /^(?:提交(?:简历|申请|投递)?|确认(?:提交|投递|申请)|立即投递|投递(?:简历)?|submit(?: application| résumé| resume)?|apply now|send application)$/i;
  const ambiguous = /一键|立即申请|立即应聘|申请职位|申请岗位|logout|sign out|注销|退出|删除|撤回|直接投递|确认投递|quick apply|easy apply/i;
  const formEntry = /^(?:进入(?:申请表|网申|填写页面)|(?:开始|继续)填写(?:申请表|简历)?|填写(?:申请表|简历)|完善(?:在线)?简历|open application form|complete application form)$/i;
  const searchButton = /^(?:搜索(?:职位|岗位)?|查找职位|search(?: jobs)?)$/i;
  const careerNavLabel = /^(?:职位(?:搜索|列表|查询|机会)?|岗位(?:列表|搜索|查询)?|查看(?:所有|全部|在招)?(?:职位|岗位)|(?:全部|所有|在招)(?:职位|岗位)|(?:校园|校招|实习|社会|社招)招聘(?:职位)?|校招|社招|实习生|实习职位|日常实习(?:生)?|研究型[\/／]日常实习生|jobs|job openings|open positions|internships|careers)$/i;
  const jobSearchLabel = /(?:搜索|查找).{0,8}(?:职位|岗位)|(?:职位|岗位).{0,6}(?:搜索|关键词)|search (?:jobs|positions)|job (?:search|keywords?)/i;
  const receipt = /^(?:(?:简历|申请)?(?:提交|投递)成功|(?:申请|简历)已(?:成功)?(?:提交|投递)|申请已收到|application (?:submitted|received)(?: successfully)?|(?:you have )?successfully (?:submitted|applied))(?:[\s,，·:：]*(?:回执(?:编号)?|申请编号|confirmation(?: id| number)?)[:：\s]+[a-z0-9-]{3,80})?[\s.!！。]*$/i;
  const safeClick = /^(?:(?:新增|添加|增加)(?:一条)?(?:教育(?:经历|背景)?|工作(?:经历|经验)?|实习(?:经历|经验)?|项目(?:经历|经验)?|语言|证书|获奖|经历|记录)|新增|添加|编辑(?:教育经历|工作经历|实习经历|项目经历|此条)?|展开(?:全部|详情|更多)?|显示更多|下一步(?:[:：]\s*返回申请表)?|继续填写|返回上一步|add(?: another)?(?: education| job| experience| employment| project)?|edit|show more|expand|next(?: step)?|previous(?: step)?)$/i;
  const dialogClick = /^(?:确定|完成|取消|关闭|保存(?:本条|此条|教育经历|工作经历|实习经历|项目经历)?|done|cancel|close|save(?: entry)?)$/i;
  const text = (value, limit = 160) => String(value || '').replace(/\s+/g, ' ').trim().slice(0, limit);
  const contents = (node, limit) => text(node?.innerText || node?.textContent, limit);
  const normal = (value) => text(value, 8192).normalize('NFKC').toLowerCase();
  const authorizedLocation = url => url.origin === location.origin &&
    (!args.tenantPath || (!/%(?:2f|5c|2e|25)/i.test(url.pathname) &&
      (url.pathname === args.tenantPath.replace(/\/$/,'') || url.pathname.startsWith(args.tenantPath))));
  const redact = (value) => value.replace(/[\w.+%-]+@[\w.-]+\.[a-z]{2,}/gi, '[邮箱]')
    .replace(/\d{7,}[0-9Xx]?/g, '[号码]');

  const visible = (node) => Boolean(node?.getClientRects().length) &&
    !node.closest('[hidden],[inert],[aria-hidden="true"]') &&
    node.checkVisibility({checkOpacity:true,checkVisibilityCSS:true});
  const dialog = Array.from(document.querySelectorAll('dialog[open],[aria-modal="true"],[role="dialog"]')).filter(visible).at(-1);
  const active = (node) => visible(node) && (!dialog || dialog.contains(node));
  const uploadTarget = (node) => {
    if (!node.matches('input[type=file]') || node.closest('[inert],[aria-disabled="true"]')) return null;
    if (active(node)) return node;
    const known = node.closest('.file-uploader__wrapper,.ant-upload,.el-upload,.next-upload');
    return Array.from(node.labels || []).find(active) ||
      ((known && active(known) && known.querySelectorAll('input[type=file]').length === 1) ? known : null);
  };
  const loginPath = /(?:^|\/)(?:login|sign[-_]?in|sso|captcha|verify|passport)(?:[.\/_?#-]|$)/i;
  const blocked = loginPath.test(location.pathname) || loginPath.test(location.hash.replace(/^#\/?/, '/')) ||
    Array.from(document.querySelectorAll('input[type=password],input[autocomplete="one-time-code"],input[name*="captcha" i],iframe[src*="captcha" i]')).some(active) ||
    Array.from(document.querySelectorAll('iframe[src],img[alt],[role=dialog],form,[class*="login" i],[class*="qrcode" i]')).some(node => {
      if (!active(node)) return false;
      if (node.tagName === 'IFRAME') {
        try { return loginPath.test(new URL(node.src).pathname); } catch { return false; }
      }
      return /扫码登[录陆]|扫描二维码登[录陆]|二维码登[录陆]|登[录陆](?:用|的)?二维码|短信验证码|身份验证|scan (?:the )?qr code to (?:log|sign) in/i
        .test(contents(node,360) + ' ' + (node.alt || ''));
    }) || Array.from(document.querySelectorAll('img,canvas')).filter(active).some(node =>
      /(?:请(?:使用)?(?:手机|微信|支付宝|APP)?(?:扫码|扫描二维码)(?:完成)?登[录陆]|扫描二维码(?:完成)?身份验证)/i
        .test(contents(node.parentElement,280))) ||
    Array.from(document.querySelectorAll('h1,h2,h3,[role="dialog"],[role="alert"],label')).some((node) =>
      active(node) && authentication.test(contents(node))
    );
  if (blocked) {
    if (args.screenshot || args.preview) return false;
    if (args.action || args.completeChoice) refuse('BLOCKED');
    return { url: location.href, document: state.document, controls: [], text: '', receipt: null,
      blocked: '页面需要登录、验证码或人工身份核验', loginRequired:true };
  }
  if (args.preview) return state.document;

  const labelRefs = (node) => (node.getAttribute('aria-labelledby') || '').split(/\s+/)
    .map((id) => contents(document.getElementById(id))).filter(Boolean).join(' ');
  const refs = (node) => {
    if (!state.refs.has(node)) state.refs.set(node, 'field-' + ++state.next);
    return state.refs.get(node);
  };
  const listboxOwner = (listbox) => {
    if (!listbox.id) return null;
    const matching = Array.from(document.querySelectorAll('[aria-controls],[aria-owns]')).filter((node) =>
      active(node) && (node.getAttribute('aria-controls') || node.getAttribute('aria-owns')).split(/\s+/).includes(listbox.id));
    return matching.length === 1 ? matching[0] : null;
  };
  const pickOption = (owner, pending, option) => {
    pending.picked = true;
    pending.expanded = owner.getAttribute('aria-expanded');
    pending.priorDisplay = normal(owner.getAttribute('aria-valuetext'));
    pending.preselected = option.getAttribute('aria-selected') === 'true';
    option.scrollIntoView({block:'nearest',behavior:'instant'});
    option.click();
  };
  const describe = (node) => {
    const wrapper = node.closest('.form-item,.form-group,.ant-form-item,.el-form-item,.next-form-item,[data-form-field]');
    const labelNodes = Array.from(node.labels || []);
    const associated = labelNodes.map((item) => contents(item)).filter(Boolean).join(' ');
    const formLabel = contents(wrapper?.querySelector('label,.form-item__text,[class*="form-label"],.next-form-item-label'));
    const button = node.matches('button,summary,[role="button"],a[href],[type="button"],[type="submit"],[type="reset"]');
    const menuItem = node.matches('li,[role="option"],[role="menuitem"],[role="tab"]');
    const label = text(associated || labelRefs(node) || node.getAttribute('aria-label') || formLabel ||
      ((button || menuItem) ? (contents(node) || node.value) : '') ||
      node.getAttribute('placeholder') || node.getAttribute('title') || node.getAttribute('name') ||
      ((node.required || node.getAttribute('aria-required') === 'true') ? '未命名的必填控件' : ''));
    if (!label) return null;
    const parent = node.closest(sectionSelector);
    const section = text(parent?.getAttribute('aria-label') || (parent && labelRefs(parent)) ||
      contents(parent?.querySelector(':scope > legend,:scope > h1,:scope > h2,:scope > h3,:scope > header')) ||
      parent?.getAttribute('data-section'));
    const forbidden = label === '未命名的必填控件' || sensitive.test(label + ' ' + (node.name || '') + ' ' + (node.autocomplete || ''));
    const readonly = node.readOnly || node.closest('[aria-readonly="true"]');
    const disabled = node.matches(':disabled') || node.closest('[aria-disabled="true"]');
    const isFile = node.matches('input[type="file"]');
    const isSelect = node.matches('select');
    const nativeSubmit = node.matches('button,input') && node.type === 'submit' && Boolean(node.form);
    const listbox = node.matches('[role="option"]') ? node.closest('[role="listbox"]') : null;
    const owner = listbox ? listboxOwner(listbox) : null;
    const navigation = !dialog && !node.closest('form,[contenteditable="true"]') && !owner &&
      !node.querySelector('a[href],button,input,select') && careerNavLabel.test(label) &&
      (node.matches('button,[role="button"]') ||
        (menuItem && node.closest('nav,header,[role="navigation"],[role="menu"],[role="menubar"],[role="tablist"],[role="listbox"]') &&
          getComputedStyle(node).cursor === 'pointer'));
    const check = node.matches('input[type="checkbox"],input[type="radio"]');
    const groupSelected = node.type === 'radio' && node.name && Array.from(
      (node.form || document).querySelectorAll('input[type=radio]:checked')
    ).some((item) => item.name === node.name && item.form === node.form);
    const combo = node.matches('[role=combobox]') && Boolean(node.getAttribute('aria-controls') || node.getAttribute('aria-owns'));
    const pending = state.selections.get(node);
    if (pending && pending.typed && node.value !== pending.value) state.selections.delete(node);
    const occupied = state.selections.has(node) ? false
      : isFile ? node.files.length > 0 || Boolean(wrapper?.querySelector('[data-uploaded],.ant-upload-list-item,.el-upload-list__item,.next-upload-list-item,.file-uploader__list-view > *'))
      : check ? node.checked || Boolean(groupSelected)
      : listbox ? Boolean(listbox.querySelector('[role=option][aria-selected=true]') || text(owner?.value || owner?.getAttribute('aria-valuetext')))
      : Boolean(text(node.isContentEditable ? node.innerText : node.value) || node.getAttribute('aria-valuetext') ||
          (combo && (node.getAttribute('aria-controls') || node.getAttribute('aria-owns')).split(/\s+/)
            .some(id => document.getElementById(id)?.querySelector('[role=option][aria-selected=true]'))));
    const required = !disabled && (node.required === true || node.getAttribute('aria-required') === 'true' ||
      owner?.getAttribute('aria-required') === 'true' ||
      Boolean(wrapper?.querySelector('.form-item__required,.ant-form-item-required,.el-form-item__label.is-required,.next-form-item-required')) ||
      /(?:^\*|\*$|^＊|＊$)/.test(label));
    const searchForm = nativeSubmit && searchButton.test(label) && (node.form.getAttribute('method') || 'get').toLowerCase() === 'get' &&
      (!node.form.target || node.form.target === '_self') && !node.hasAttribute('formaction') && !node.hasAttribute('formtarget') &&
      !node.hasAttribute('formmethod') && URL.canParse(node.form.getAttribute('action') || location.href, location.href) &&
      authorizedLocation(new URL(node.form.getAttribute('action') || location.href,location.href)) &&
      Array.from(node.form.elements).some(el => el.matches('input[type=search]') ||
        (el.matches('input[type=text],input:not([type])') && jobSearchLabel.test(el.placeholder))) &&
      !Array.from(node.form.elements).some(el => (el.required && !el.matches('input[type=search],[role=searchbox]')) || sensitive.test(el.name || '') ||
        el.matches('input[type=email],input[type=tel],input[type=password],input[type=file]'));
    let kind = 'manual', newTab = false;
    if (!forbidden && (!readonly || combo) && !disabled) {
      if (node.matches('a[href]')) {
        try {
          const url = new URL(node.href);
          const trigger = ['','#'].includes(node.getAttribute('href')?.trim());
          if (!url.username && !url.password && !node.hasAttribute('download') && !submit.test(label) &&
              !ambiguous.test(label) && !/(?:^|\/)(?:submit|delete|logout|withdraw|quick[-_]?apply)(?:[./_-]|$)/i.test(url.pathname)) {
            if (trigger) kind = formEntry.test(label) ? 'formButton' :
              (navigation || (!dialog && !node.closest('form') && careerNavLabel.test(label))) ? 'careerNavigation' :
                safeClick.test(label) ? 'button' : 'manual';
            else if (authorizedLocation(url)) kind = 'link';
            else if (url.origin !== location.origin && url.protocol === 'https:' && formEntry.test(label)) kind = 'externalForm';
            newTab = node.target === '_blank' || kind === 'externalForm';
          }
        } catch {}
      } else if (node.matches('input[type=submit],button,[role=button]') && submit.test(label) && !node.matches('[type=reset],[type=button][aria-haspopup]')) {
        kind = 'submit';
      } else if (searchForm) {
        kind = 'button';
      } else if (navigation) {
        kind = 'careerNavigation';
      } else if (combo && !readonly && node.matches('input') && ['text','search'].includes(node.type)) {
        kind = 'combobox';
      } else if (isSelect && !node.multiple) {
        kind = 'select';
      } else if (!nativeSubmit && (node.matches('summary,[role=combobox]') ||
          (button && (!node.form || node.type === 'button') && !node.matches('[type=reset]') &&
           (safeClick.test(label) || formEntry.test(label) || searchButton.test(label) ||
            (dialog && dialogClick.test(label)) || node.matches('[aria-haspopup="listbox"]'))))) {
        kind = formEntry.test(label) ? 'formButton' : 'button';
      } else if (!nativeSubmit && owner) kind = 'choice';
      else if (isFile) kind = 'file';
      else if (node.matches('textarea')) kind = 'textarea';
      else if (node.matches('input') && ['text','search'].includes(node.type) &&
          (node.type === 'search' || node.getAttribute('role') === 'searchbox' || (!required && jobSearchLabel.test(label)))) kind = 'search';
      else if (node.matches('input') && ['text','number','email','tel','date','month','url','search','time'].includes(node.type)) kind = 'input';
    }
    if (kind === 'manual' && (button || disabled) && !required && !nativeSubmit && !submit.test(label)) return null;
    const options = isSelect ? Array.from(node.options, (item,index) => ({item,index}))
      .filter(({item}) => !item.disabled && !item.closest('optgroup[disabled]') && item.value && text(item.label))
      .slice(0,200).map(({item,index}) => ({index,label:redact(text(item.label))})) : [];
    return { node, fields: { ref:refs(node), kind, newTab, label:redact(label), section:redact(section), required, occupied,
      invalid:!disabled && (node.getAttribute('aria-invalid') === 'true' ||
        Boolean(node.validity && !node.validity.valid && (required || occupied))), options,
      accept:isFile ? text(node.accept,500) : '', inputType: node.matches('input') ? node.type : '' } };
  };

  const collected = [];
  for (const node of document.querySelectorAll(controlsSelector)) {
    if ((!active(node) && !uploadTarget(node)) || node.matches('input[type=hidden],input[type=image]') ||
        (node.matches('[role=button]:not(button),[role=combobox]:not(input)') && node.querySelector('input,select,button'))) continue;
    const item = describe(node);
    if (item) collected.push(item);
    if (collected.length >= 181) break;
  }
  const truncated = collected.length > 180;
  const controls = collected.slice(0,180).map(({fields}) => fields);
  const nodes = Array.from(document.querySelectorAll(
    'h1,h2,h3,h4,legend,p,label,a[href],button,li,[role="heading"],[role="status"],[role="alert"]'
  )).filter(visible);
  const lines = [...new Set(nodes.filter((node) => !dialog || dialog.contains(node))
    .map((node) => redact(contents(node,260))).filter(Boolean))];
  // 弹窗只限定可操作的控件；公司和岗位始终核对当前主表单。
  const scopeNodes = dialog ? nodes.filter((node) => !node.closest('dialog,[role="dialog"],[aria-modal="true"]')) : nodes;
  const scopeLines = dialog ? scopeNodes.map((node) => redact(contents(node,260))) : lines;
  const headings = scopeNodes.filter((node) => node.matches('h1,h2,h3,[role="heading"]'))
    .slice(0,50).map((node) => redact(contents(node,200))).filter(Boolean);
  const jobLabels = scopeLines.filter((line) => /^(?:应聘岗位|应聘职位|招聘岗位|职位名称|job title|position)\s*[:：]/i.test(line)).slice(0,20);
  const company = normal(args.company).replace(/\s+/g,'');
  const employerLabels = scopeLines.filter(line => /^(?:公司(?:名称)?|招聘单位|所属公司|用人单位|company|employer)\s*[:：]/i.test(line));
  const companyNames = employerLabels.length ? employerLabels : [
    ...headings,
    ...Array.from(document.querySelectorAll('header img[alt],header [aria-label]')).filter(visible)
      .map(node => node.alt || node.getAttribute('aria-label')),
  ];
  const matchesEmployer = name => String(name).split(/[·｜|—]/).some(part => {
    const identity = normal(part).replace(/^(?:公司(?:名称)?|招聘单位|所属公司|用人单位|company|employer)\s*[:：]\s*/i,'').replace(/\s+/g,'');
    return identity.startsWith(company) && /^(?:(?:集团|股份有限公司|有限公司)?(?:校园招聘|社会招聘|人才招聘|招聘官网|招聘|校招|招聘平台|careers)?)?$/.test(identity.slice(company.length));
  });
  const companyMatched = Boolean(company) && (employerLabels.length
    ? employerLabels.every(matchesEmployer) : companyNames.some(matchesEmployer));
  const pageReceipt = Array.from(document.querySelectorAll('h1,h2,h3,p,[role="status"],[role="alert"],[aria-live="polite"]'))
    .filter(active).map((node) => contents(node,200)).find((label) => receipt.test(label)) || null;
  const embedded = Array.from(document.querySelectorAll('iframe')).some((node) => active(node) &&
    node.getBoundingClientRect().width >= 200 && node.getBoundingClientRect().height >= 100);
  // 原始值只留在本标签的隔离执行环境，用于判断本次观察是否已失效。
  const signature = JSON.stringify([location.href, headings, jobLabels, companyMatched, Boolean(dialog), pageReceipt, truncated, embedded, collected.map(({node,fields}) =>
    [fields, node.href || '', String(node.value || '').slice(0,8192), node.checked ?? null,
      Array.from(node.files || [], (file) => [file.name, file.size])])]);
  if (state.signature !== signature) {
    state.signature = signature;
    state.revision = crypto.randomUUID();
  }
  if (args.screenshot) return args.revision === state.revision;
  if (args.completeChoice) {
    if (!companyMatched || args.parentJobLabels.some(label => !jobLabels.includes(label)) ||
        args.parentHeadings.some(label => !headings.includes(label))) refuse('STALE');
    const owner = collected.find(item => item.fields.ref === args.completeChoice)?.node;
    const pending = state.selections.get(owner);
    if (!pending || !active(owner)) refuse('STALE');
    if (args.abandon) {
      if (pending.typed && !pending.picked && owner.value === pending.value) {
        state.selections.delete(owner);
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(owner,'');
        owner.dispatchEvent(new Event('input',{bubbles:true}));
      }
      return {ok:true};
    }
    const boxes = (owner.getAttribute('aria-controls') || owner.getAttribute('aria-owns') || '').split(/\s+/)
      .map(id => document.getElementById(id)).filter(box => box?.matches('[role=listbox]') && listboxOwner(box) === owner);
    const options = boxes.flatMap(box => Array.from(box.querySelectorAll('[role=option]'))).filter(option =>
      normal(contents(option) || option.getAttribute('aria-label')) === normal(pending.value));
    if (!pending.picked) {
      const visibleOptions = options.filter(option => active(option) && !option.closest('[aria-disabled=true],:disabled'));
      if (visibleOptions.length > 1) refuse('VALUE');
      if (!visibleOptions.length) return {ok:true,waiting:true};
      pickOption(owner,pending,visibleOptions[0]);
      return {ok:true,waiting:true};
    }
    const selected = !pending.preselected && options.some(option => option.getAttribute('aria-selected') === 'true');
    const displayed = normal(owner.getAttribute('aria-valuetext'));
    const explicit = displayed !== pending.priorDisplay && displayed === normal(pending.value);
    const echo = normal(owner.value || contents(owner)) === normal(pending.value);
    const closed = !boxes.some(active) || (pending.expanded === 'true' && owner.getAttribute('aria-expanded') === 'false');
    if (!selected && !explicit && !(echo && closed)) return {ok:true,waiting:true};
    state.selections.delete(owner);
    return {ok:true,selected:true};
  }
  const editableForm = controls.some(item => item.required && ['input','select','textarea','file','choice','combobox'].includes(item.kind));
  const finalReview = !dialog && !truncated && !embedded && headings.some(label =>
    /^(?:最终核对|申请信息确认|网申信息核对|申请表预览|review your application|final review)$/i.test(label)) &&
    lines.some(line => /^(?:申请资料已完整|申请表已填写完整|岗位申请草稿已完成|application (?:draft )?complete)$/i.test(line));
  if (!args.action) {
    return { url:location.href, document:state.document, controls, truncated, embedded, modal:Boolean(dialog),
      headings, jobLabels, companyMatched, editableForm, finalReview, text:lines.join('\n').slice(0,7200), blocked:null, receipt:pageReceipt,
      revision:state.revision, viewport:{x:scrollX,y:scrollY,width:innerWidth,height:innerHeight} };
  }

  if (args.revision !== state.revision) refuse('STALE');
  const action = args.action;
  if (action.kind === 'scroll') {
    if (!Number.isInteger(action.delta) || action.delta === 0 || Math.abs(action.delta) > 2000) refuse('CONTROL');
    (dialog || window).scrollBy({top:action.delta,behavior:'instant'});
    return {ok:true};
  }
  const before = args.controls?.find((item) => item.ref === action.ref);
  const current = collected.find((item) => item.fields.ref === action.ref);
  if (!before || !current) refuse('STALE');
  // Rust encodes object keys in sorted order, so compare by value on their stable top-level keys.
  const same = Object.keys(current.fields).every((key) =>
    key === 'options' ? current.fields.options.every((o,i) => before.options?.[i]?.index === o.index &&
      before.options?.[i]?.label === o.label) && current.fields.options.length === before.options?.length
      : current.fields[key] === before[key]);
  if (!same) refuse('STALE');
  const { node, fields } = current;
  if (action.kind === 'formNode') {
    if (!['formButton','careerNavigation'].includes(fields.kind)) refuse('CONTROL');
    return node;
  }
  if (action.kind === 'linkTarget') {
    if (!['link','externalForm'].includes(fields.kind)) refuse('CONTROL');
    return {ok:true, destination:node.href};
  }
  if (action.kind === 'search') {
    if (fields.kind !== 'search' || typeof action.value !== 'string' || !action.value || action.value.length > 200) refuse('CONTROL');
    if (action.runSearch && (node.form || dialog || !jobSearchLabel.test(fields.label))) refuse('CONTROL');
    if (action.runSearch) node.focus();
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(node,action.value);
    node.dispatchEvent(new Event('input',{bubbles:true}));
    if (node.isConnected) node.dispatchEvent(new Event('change',{bubbles:true}));
    if (action.runSearch && node.isConnected && location.href === args.url) {
      node.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',code:'Enter',keyCode:13,which:13,bubbles:true,cancelable:true}));
    }
    return {ok:true};
  }
  if (action.kind === 'click') {
    if (fields.kind === 'link') {
      const url = new URL(node.href);
      if (!authorizedLocation(url) || url.username || url.password) refuse('CONTROL');
      location.assign(url.href);
    } else if (fields.kind === 'button' || fields.kind === 'formButton') {
      node.scrollIntoView({block:'center',inline:'nearest',behavior:'instant'});
      node.click();
    } else refuse('CONTROL');
    return {ok:true};
  }
  if (action.kind === 'submit' || action.kind === 'ready') {
    if (fields.kind !== 'submit') refuse('CONTROL');
    const form = node.form || node.closest('form');
    if (action.kind === 'submit') {
      const destination = new URL(node.getAttribute('formaction') || form?.getAttribute('action') || location.href, location.href);
      const target = node.getAttribute('formtarget') || form?.target || '_self';
      if (!authorizedLocation(destination) || destination.username || destination.password || target !== '_self') refuse('CONTROL');
    }
    const invalidNative = Array.from(form?.elements || []).some((item) =>
      item.willValidate && item.validity.valid === false);
    if (truncated || embedded || invalidNative || controls.some((item) => item.invalid || (item.required && !item.occupied))) refuse('REQUIRED');
    if (action.kind === 'submit') node.click();
    return {ok:true};
  }
  if (fields.occupied) refuse('EXISTING');
  if (action.kind === 'upload' && fields.kind === 'file') {
    const target = uploadTarget(node);
    if (!target) refuse('CONTROL');
    target.scrollIntoView({block:'center',inline:'nearest',behavior:'instant'});
    return node;
  }
  if (action.kind !== 'fill') refuse('CONTROL');
  const value = action.value;
  if (typeof value !== 'string' || !value.trim() || value.length > 8192) refuse('VALUE');
  if (fields.kind === 'combobox') {
    if (state.selections.has(node) || node.value || node.maxLength >= 0 && value.length > node.maxLength) refuse('EXISTING');
    state.selections.set(node,{value,typed:true,picked:false});
    node.focus();
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(node,value);
    node.dispatchEvent(new Event('input',{bubbles:true}));
    return {ok:true,completeChoice:fields.ref};
  }
  if (fields.kind === 'choice') {
    if (normal(value) !== normal(fields.label)) refuse('VALUE');
    const listbox = node.closest('[role=listbox]');
    const owner = listboxOwner(listbox);
    if (!owner || text(owner.value || owner.getAttribute('aria-valuetext')) ||
        listbox.querySelector('[role=option][aria-selected=true]')) refuse('CONTROL');
    if (state.selections.has(owner)) refuse('EXISTING');
    const pending = {value,typed:false};
    state.selections.set(owner,pending);
    pickOption(owner,pending,node);
    return {ok:true,completeChoice:refs(owner)};
  }
  if (!['input','textarea','select'].includes(fields.kind)) refuse('CONTROL');
  let next = value;
  let property = 'value';
  let prototype = fields.kind === 'textarea' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  if (fields.kind === 'select') {
    const match = Array.from(node.options, (item,index) => ({item,index}))
      .filter(({item}) => !item.disabled && !item.closest('optgroup[disabled]') && item.value && normal(item.label) === normal(value));
    if (match.length !== 1) refuse('VALUE');
    next = match[0].index;
    property = 'selectedIndex';
    prototype = HTMLSelectElement.prototype;
  } else if (node.maxLength >= 0 && value.length > node.maxLength) refuse('VALUE');
  node.scrollIntoView({block:'center',inline:'nearest',behavior:'instant'});
  const setter = Object.getOwnPropertyDescriptor(prototype, property).set;
  const original = node[property];
  setter.call(node, next);
  if (node[property] !== next || node.validity?.valid === false) {
    setter.call(node, original);
    refuse('VALUE');
  }
  node.dispatchEvent(new Event('input', {bubbles:true}));
  if (node.isConnected && location.href === args.url) node.dispatchEvent(new Event('change', {bubbles:true}));
  if (!node.isConnected || location.href !== args.url || node[property] !== next) refuse('STALE');
  return {ok:true};
})(__COMPANY_FORM_ARGS__)
