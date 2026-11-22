((args) => {
  const target = () => window === window.top && location.protocol === 'https:' && location.href === args.url;
  if (!target()) throw Error('网申页面已变化');
  const alibaba = location.hostname === 'campus-talent.alibaba.com';
  const baic = location.hostname === 'baicgroup.zhiye.com';

  const controlSelector = 'input,textarea,select,[role="combobox"],[role="radiogroup"],[role="checkbox"],[role="textbox"],[contenteditable="true"]';
  const groupSelector = '.ant-form-item,.el-form-item,.next-form-item,.tea-form-item,.semi-form-field,.form-item,.formItem,.form-group,[data-form-field]';
  const headingSelector = 'h1,h2,h3,h4,h5,h6,legend,header,[role="heading"],[class*="title" i],[class*="heading" i],[class*="caption" i]';
  const sectionLabel = /^(?:(?:个人|基本|联系)(?:信息|资料|方式)|教育(?:经历|背景|信息|经验|情况)|(?:实习|工作)(?:经历|经验|实践|信息)|(?:实习|工作)\s*[\/、及与和]\s*(?:工作|实习|项目)(?:经历|经验)|项目(?:经历|经验|实践)|大赛经历|获奖(?:情况|经历)|奖项荣誉|荣誉(?:奖项|奖励)|奖励与荣誉|语言(?:能力|水平|技能)|专业证书|技能证书|资格证书|证书(?:资质)?|校园(?:经历|实践)|校内实践|社会(?:实践|活动)|学生工作|自我评价|补充说明|其[它他](?:信息)?|(?:简历)?附件|education|internships?|internship experience|work experience|employment history|projects?|project experience|awards|honors|languages|language skills|certificates|certifications|activities)$/i;
  const customControl = '[role="combobox"],.ant-select,.ant-picker,.ant-cascader,.el-select,.el-date-editor,.el-cascader,.next-select,.next-date-picker,.next-range-picker,.tea-select,.tea-date-picker,.semi-select,.semi-datepicker,.phoenix-select,.phoenix-auto-complete-container';
  const neverFill = /验证(?:码|用)|确认邮箱|确认手机|登录|账号|密码|银行卡|账户|身份证|证件|护照|token|captcha|otp|password|passport/i;
  const privateInput = /one-time-code|(?:^|\s)cc-|password|otp|captcha|card[-_]?number|certificate[-_]?number|identity[-_]?number|id[-_]?number|passport/i;
  const text = (node) => (node?.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 200);
  const labelText = (value) => String(value || '').replace(/^[\s*＊]+|[\s:：*＊]+$/g, '').trim().slice(0, 200);
  const sectionText = (node) => text(node).replace(/^[\d.、]+\s*/, '').replace(/\s*[:：]?\s*$/, '');

  function visible(node) {
    if (!node.getClientRects().length || node.closest('[hidden],[inert],[aria-hidden="true"]')) return false;
    for (let element = node; element; element = element.parentElement) {
      const style = getComputedStyle(element);
      if (style.visibility === 'hidden' || style.visibility === 'collapse' || style.display === 'none' || style.opacity === '0') return false;
    }
    return true;
  }

  function adjacentLabel(node) {
    for (let item = node, depth = 0; item?.parentElement && depth < 3; item = item.parentElement, depth++) {
      const before = item.previousElementSibling;
      if (!before || !visible(before) || before.querySelector(controlSelector)) continue;
      if (before.matches('label,[class*="label" i],[class*="caption" i],[class*="Title" i]') ||
          (!before.children.length && before.parentElement.children.length <= 3)) {
        const value = labelText(text(before));
        if (value && value.length <= 28) return value;
      }
    }
    return '';
  }

  function baicHeading(form) {
    for (let node = form, depth = 0; node && depth < 7; node = node.parentElement, depth++) {
      const heading = node.previousElementSibling;
      const label = sectionText(heading);
      if (sectionLabel.test(label) && visible(heading) && !heading.querySelector(controlSelector)) return label;
    }
    return '';
  }

  function metadata() {
    const headings = Array.from(document.querySelectorAll(headingSelector))
      .filter((heading) => sectionLabel.test(sectionText(heading)) && visible(heading));
    const controlGroups = new Map();
    const controls = [];
    for (const node of document.querySelectorAll(controlSelector)) {
      const file = node.matches('input[type="file"]');
      const baicForm = baic ? node.closest('.ux-standard-form') : null;
      const baicSection = baicHeading(baicForm);
      const baicUpload = file && baicSection === '简历附件' && node.closest('.file-uploader__wrapper');
      const aliSelect = alibaba ? node.closest('.next-select') : null;
      const aliPhone = alibaba && node.matches('.cpo-phone-select-number input');
      if (aliSelect?.classList.contains('cpo-phone-select-inner')) continue;
      const visibleLabel = file && !node.closest('[inert]') && Array.from(node.labels || []).some(visible);
      if (node.matches(':disabled,input[type="hidden"],input[type="submit"],input[type="button"],input[type="reset"],input[type="image"]') ||
          node.closest('[aria-disabled="true"],[inert]') ||
          (!visible(node) && !visibleLabel && !(aliSelect && visible(aliSelect)) && !(baicUpload && visible(baicUpload)))) continue;
      if (node.matches('[role="combobox"]:not(input):not(select),[role="radiogroup"]') && node.querySelector('input,select')) continue;
      const wrapper = node.closest(groupSelector);
      const ariaLabel = node.getAttribute('aria-labelledby')?.split(/\s+/).map((id) => text(document.getElementById(id))).filter(Boolean).join(' ');
      const labels = Array.from(node.labels || [], text).filter(Boolean).join(' ');
      const formLabel = text(wrapper?.querySelector('.ant-form-item-label,.el-form-item__label,.next-form-item-label,.tea-form-item__label,.semi-form-field-label,.form-item__text,[class*="form-label" i],label:not([for])'));
      const placeholder = node.getAttribute('placeholder')?.replace(/^(?:请输入|请填写|请选择|Enter |Select )/i, '') || '';
      const explicitLabel = labelText(node.getAttribute('aria-label') || ariaLabel || labels || formLabel || adjacentLabel(node));
      const label = labelText(
        aliPhone ? formLabel :
        alibaba && node.id === 'otherInformation' ? placeholder :
        aliSelect && formLabel ? formLabel :
        !explicitLabel || /^(?:时间|日期|时间范围|日期范围)$/.test(explicitLabel) ? placeholder || explicitLabel : explicitLabel
      );
      if (!label || /^(?:请输入|请填写|请选择|输入|选择|select|enter)$/i.test(label)) continue;

      const preceding = headings.filter((heading) => heading.compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING);
      const containingSection = preceding.findLast((heading) => heading.parentElement?.contains(node));
      const aliCard = alibaba ? node.closest('[class*="FormCard--formCard--"]') : null;
      const section = baicSection || sectionText(aliCard?.querySelector('[class*="FormCard--formCardTitle--"]') || containingSection || preceding.at(-1));
      const rowNode = baicForm || (alibaba && node.closest('.table-field-tiled-item')) ||
        node.closest('[data-form-row],.resume-item,.experience-item,.experience-edit,[class*="experienceCard" i],fieldset');
      if (rowNode && !controlGroups.has(rowNode)) controlGroups.set(rowNode, controlGroups.size);
      const row = rowNode ? String(controlGroups.get(rowNode)) : '';
      const custom = node.closest('[aria-readonly="true"]') ||
        (!node.matches('select') && (node.closest(customControl) || node.matches('[readonly],[contenteditable="true"]')));
      const privateControl = ['name', 'id', 'autocomplete'].some((attr) => privateInput.test(node.getAttribute(attr) || ''));
      const type = privateControl ? 'private' : custom ? 'custom' : file ? 'file' : String(node.type || node.getAttribute('role') || '').slice(0, 200);
      const options = type === 'select-one' ? Array.from(node.options, (option, index) => ({ option, index }))
        .filter(({ option, index }) => index < 2000 && !option.disabled && !option.closest('optgroup[disabled]') && option.value && labelText(option.label))
        .slice(0, 200).map(({ option, index }) => ({ index, label: labelText(option.label) })) : [];
      const aliSelected = aliSelect?.querySelector('.next-select-values');
      const aliHasSelection = aliSelected && Array.from(aliSelected.childNodes).some((child) =>
        child.nodeType === Node.ELEMENT_NODE ? !child.matches('.next-select-trigger-search') :
          child.nodeType === Node.TEXT_NODE && Boolean(child.textContent.trim())
      );
      const occupied = file ? node.files.length > 0 ||
        Boolean(baicUpload && baicUpload.querySelector('.file-uploader__list-view > *')) ||
        Boolean(node.closest('.ant-upload-wrapper,.next-upload,.el-upload,.tea-upload')?.querySelector(
          '.ant-upload-list-item,.next-upload-list-item,.el-upload-list__item,.tea-upload-list-item'
        )) : node.matches('input[type="radio"],input[type="checkbox"]') ? node.checked :
          Boolean(aliHasSelection || String(node.value || '').trim());
      const required = node.required === true || node.getAttribute('aria-required') === 'true' ||
        Boolean(wrapper?.querySelector('.ant-form-item-required,.el-form-item__label.is-required,.next-form-item-required,.form-item__required,label[required]'));
      const field = { id: `field-${controls.length}`, label, section, row, type, options, occupied, required };
      if (type === 'file') field.accept = node.accept.length <= 500 ? node.accept : 'unsupported';
      controls.push({ node, field });
      if (controls.length >= 240) break;
    }
    return controls;
  }

  const fields = metadata();
  const schema = ({ id, label, section, row, type, options, accept, required }) =>
    JSON.stringify([id, label, section, row, type, options.map(({ index, label }) => [index, label]), accept, required]);
  if (args.fileId !== undefined) {
    if (fields.length !== args.fields.length || fields.some(({ field }, index) => schema(field) !== schema(args.fields[index]))) return null;
    const match = fields.find(({ field }) => field.id === args.fileId);
    return match?.field.type === 'file' && !match.field.occupied ? match.node : null;
  }
  if (args.operations === undefined) return { fields: fields.map(({ field }) => field) };
  const sameField = (a, b) => schema(a) === schema(b) && a.occupied === b.occupied;
  if (!Array.isArray(args.fields) || args.fields.length !== fields.length ||
      fields.some(({ field }, index) => !sameField(field, args.fields[index])) || !Array.isArray(args.operations)) {
    throw Error('网申表单已变化');
  }

  const checked = new Map();
  for (const { id, value } of args.operations) {
    const match = fields.find(({ field }) => field.id === id);
    if (!match || checked.has(id)) throw Error('填充字段未核对');
    const { field } = match;
    const valid = field.type === 'select-one' ? Number.isInteger(value) && field.options.some((item) => item.index === value) :
      ['text', 'tel', 'email', 'url', 'number', 'date', 'month', 'textarea'].includes(field.type) &&
      typeof value === 'string' && value.trim() && value.length <= 8192;
    if (!valid || field.occupied || neverFill.test(field.label)) throw Error('填充目标无效');
    checked.set(id, { ...match, value });
  }

  const filled = [], skipped = [];
  for (const [id, { node, field, value }] of checked) {
    if (!target()) throw Error('网申页面已变化');
    const latest = metadata().find((item) => item.field.id === id);
    if (!latest || latest.node !== node || !sameField(latest.field, field) || node.readOnly ||
        (field.type !== 'select-one' && node.maxLength >= 0 && value.length > node.maxLength)) {
      skipped.push(id);
      continue;
    }
    const property = field.type === 'select-one' ? 'selectedIndex' : 'value';
    const prototype = node.matches('select') ? HTMLSelectElement.prototype :
      node.matches('textarea') ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(prototype, property).set;
    const previous = node[property];
    setter.call(node, value);
    if (node[property] !== value || node.validity?.valid === false) {
      setter.call(node, previous);
      skipped.push(id);
      continue;
    }
    node.dispatchEvent(new Event('input', { bubbles: true }));
    if (node.isConnected && target()) node.dispatchEvent(new Event('change', { bubbles: true }));
    (node.isConnected && node[property] === value ? filled : skipped).push(id);
  }
  return { filled, skipped };
})(__JOB_FORM_ARGS__)
