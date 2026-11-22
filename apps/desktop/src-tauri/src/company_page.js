((args) => {
  const refuse = (reason) => { throw new Error('WEB_' + reason); };
  if (window !== window.top || location.href !== args.url) refuse('STALE');
  const state = globalThis.__jobAssistantCompanyPage ??= {
    document: crypto.randomUUID(), refs: new WeakMap(), next: 0,
  };
  if (args.document && args.document !== state.document) refuse('STALE');

  const controlsSelector = 'input,textarea,select,button,a[href],summary,[role="button"],[role="option"],[role="combobox"],[contenteditable="true"]';
  const sectionSelector = 'fieldset,section,article,[data-section],[role="group"],[role="dialog"],dialog';
  const authentication = /(?:密码|验证码|短信验证|扫码登录|请先登录|登录后继续|人机验证|安全验证|滑块验证|password|captcha|one[- ]time code|verify you are human)/i;
  const sensitive = /(?:身份证|证件(?:号码|类型)?|护照|银行卡|纳税|密码|验证码|验证身份|passport|identity.?number|social security|credit.?card|password|otp|captcha)/i;
  const submit = /^(?:提交(?:简历|申请|投递)?|确认(?:提交|投递|申请)|立即投递|投递(?:简历)?|submit(?: application| résumé| resume)?|apply now|send application)$/i;
  const receipt = /^(?:(?:简历|申请)?(?:提交|投递)成功|(?:申请|简历)已(?:成功)?(?:提交|投递)|申请已收到|application (?:submitted|received)(?: successfully)?|(?:you have )?successfully (?:submitted|applied))(?:[\s,，·:：]*(?:回执(?:编号)?|申请编号|confirmation(?: id| number)?)[:：\s]+[a-z0-9-]{3,80})?[\s.!！。]*$/i;
  const safeClick = /^(?:(?:新增|添加|增加)(?:一条)?(?:教育(?:经历|背景)?|工作(?:经历|经验)?|实习(?:经历|经验)?|项目(?:经历|经验)?|语言|证书|获奖|经历|记录)|新增|添加|编辑(?:教育经历|工作经历|实习经历|项目经历|此条)?|展开(?:全部|详情|更多)?|显示更多|下一步(?:[:：]\s*返回申请表)?|继续填写|返回上一步|add(?: another)?(?: education| job| experience| employment| project)?|edit|show more|expand|next(?: step)?|previous(?: step)?)$/i;
  const dialogClick = /^(?:确定|完成|取消|关闭|保存(?:本条|此条|教育经历|工作经历|实习经历|项目经历)?|done|cancel|close|save(?: entry)?)$/i;
  const text = (value, limit = 160) => String(value || '').replace(/\s+/g, ' ').trim().slice(0, limit);
  const contents = (node, limit) => text(node?.innerText || node?.textContent, limit);
  const normal = (value) => text(value, 8192).normalize('NFKC').toLowerCase();
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
  const blocked = /(?:^|\/)(?:login|sign[-_]?in|sso|captcha|verify)(?:[.\/_-]|$)/i.test(location.pathname) ||
    Array.from(document.querySelectorAll('input[type=password],input[autocomplete="one-time-code"],input[name*="captcha" i],iframe[src*="captcha" i]')).some(active) ||
    Array.from(document.querySelectorAll('h1,h2,h3,[role="dialog"],[role="alert"],label')).some((node) =>
      active(node) && authentication.test(contents(node))
    );
  if (blocked) {
    if (args.screenshot) return false;
    if (args.action) refuse('BLOCKED');
    return { url: location.href, document: state.document, controls: [], text: '', receipt: null,
      blocked: '页面需要登录、验证码或人工身份核验' };
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
  const describe = (node) => {
    const wrapper = node.closest('.form-item,.form-group,.ant-form-item,.el-form-item,.next-form-item,[data-form-field]');
    const labelNodes = Array.from(node.labels || []);
    const associated = labelNodes.map((item) => contents(item)).filter(Boolean).join(' ');
    const formLabel = contents(wrapper?.querySelector('label,.form-item__text,[class*="form-label"],.next-form-item-label'));
    const button = node.matches('button,summary,[role="button"],a[href],[type="button"],[type="submit"],[type="reset"]');
    const label = text(associated || labelRefs(node) || node.getAttribute('aria-label') || formLabel ||
      ((button || node.matches('[role="option"]')) ? (contents(node) || node.value) : '') ||
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
    const check = node.matches('input[type="checkbox"],input[type="radio"]');
    const groupSelected = node.type === 'radio' && node.name && Array.from(
      (node.form || document).querySelectorAll('input[type=radio]:checked')
    ).some((item) => item.name === node.name && item.form === node.form);
    const occupied = isFile ? node.files.length > 0 || Boolean(wrapper?.querySelector('[data-uploaded],.ant-upload-list-item,.el-upload-list__item,.next-upload-list-item,.file-uploader__list-view > *'))
      : check ? node.checked || Boolean(groupSelected)
      : listbox ? Boolean(listbox.querySelector('[role=option][aria-selected=true]') || text(owner?.value || owner?.getAttribute('aria-valuetext')))
      : Boolean(text(node.value) || node.getAttribute('aria-valuetext'));
    const required = !disabled && (node.required === true || node.getAttribute('aria-required') === 'true' ||
      owner?.getAttribute('aria-required') === 'true' ||
      Boolean(wrapper?.querySelector('.form-item__required,.ant-form-item-required,.el-form-item__label.is-required,.next-form-item-required')) ||
      /(?:^\*|\*$|^＊|＊$)/.test(label));
    let kind = 'manual';
    if (!forbidden && !readonly && !disabled) {
      if (node.matches('a[href]')) {
        try {
          const url = new URL(node.href);
          if (url.origin === location.origin && !url.username && !url.password && !node.hasAttribute('download')) kind = 'link';
        } catch {}
      } else if (node.matches('input[type=submit],button,[role=button]') && submit.test(label) && !node.matches('[type=reset],[type=button][aria-haspopup]')) {
        kind = 'submit';
      } else if (isSelect && !node.multiple) {
        kind = 'select';
      } else if (!nativeSubmit && (node.matches('summary,[role=combobox]') ||
          (button && (!node.form || node.type === 'button') && !node.matches('[type=reset]') &&
           (safeClick.test(label) || (dialog && dialogClick.test(label)) || node.matches('[aria-haspopup="listbox"]'))))) {
        kind = 'button';
      } else if (!nativeSubmit && owner) kind = 'choice';
      else if (isFile) kind = 'file';
      else if (node.matches('textarea')) kind = 'textarea';
      else if (node.matches('input') && ['text','number','email','tel','date','month','url','search','time'].includes(node.type)) kind = 'input';
    }
    if (kind === 'manual' && (button || disabled) && !required && !nativeSubmit && !submit.test(label)) return null;
    const options = isSelect ? Array.from(node.options, (item,index) => ({item,index}))
      .filter(({item}) => !item.disabled && !item.closest('optgroup[disabled]') && item.value && text(item.label))
      .slice(0,200).map(({item,index}) => ({index,label:redact(text(item.label))})) : [];
    return { node, fields: { ref:refs(node), kind, label:redact(label), section:redact(section), required, occupied,
      invalid:Boolean(node.validity && !node.validity.valid && (required || occupied)), options,
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
  const lines = [...new Set(Array.from(document.querySelectorAll(
    'h1,h2,h3,h4,legend,p,label,a[href],button,li,[role="heading"],[role="status"],[role="alert"]'
  )).filter(active).map((node) => redact(contents(node,260))).filter(Boolean))];
  const pageReceipt = Array.from(document.querySelectorAll('h1,h2,h3,p,[role="status"],[role="alert"],[aria-live="polite"]'))
    .filter(active).map((node) => contents(node,200)).find((label) => receipt.test(label)) || null;
  const embedded = Array.from(document.querySelectorAll('iframe')).some((node) => active(node) &&
    node.getBoundingClientRect().width >= 200 && node.getBoundingClientRect().height >= 100);
  // 原始值只留在本标签的隔离执行环境，用于判断本次观察是否已失效。
  const signature = JSON.stringify([location.href, pageReceipt, truncated, embedded, collected.map(({node,fields}) =>
    [fields, String(node.value || '').slice(0,8192), node.checked ?? null,
      Array.from(node.files || [], (file) => [file.name, file.size])])]);
  if (state.signature !== signature) {
    state.signature = signature;
    state.revision = crypto.randomUUID();
  }
  if (args.screenshot) return args.revision === state.revision;
  if (!args.action) {
    const headings = Array.from(document.querySelectorAll('h1,h2,h3,[role="heading"]'))
      .filter(active).slice(0,50).map((node) => redact(contents(node,200))).filter(Boolean);
    const jobLabels = lines.filter((line) => /^(?:应聘岗位|应聘职位|招聘岗位|职位名称|job title|position)\s*[:：]/i.test(line)).slice(0,20);
    return { url:location.href, document:state.document, controls, truncated, embedded, modal:Boolean(dialog),
      headings, jobLabels, text:lines.join('\n').slice(0,7200), blocked:null, receipt:pageReceipt,
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
  if (action.kind === 'click') {
    if (fields.kind === 'link') {
      const url = new URL(node.href);
      if (url.origin !== location.origin || url.username || url.password) refuse('CONTROL');
      location.assign(url.href);
    } else if (fields.kind === 'button') {
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
      if (destination.origin !== location.origin || destination.username || destination.password || target !== '_self') refuse('CONTROL');
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
  if (fields.kind === 'choice') {
    if (normal(value) !== normal(fields.label)) refuse('VALUE');
    const listbox = node.closest('[role=listbox]');
    const owner = listboxOwner(listbox);
    if (!owner || text(owner.value || owner.getAttribute('aria-valuetext')) ||
        listbox.querySelector('[role=option][aria-selected=true]')) refuse('CONTROL');
    node.click();
    return {ok:true};
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
