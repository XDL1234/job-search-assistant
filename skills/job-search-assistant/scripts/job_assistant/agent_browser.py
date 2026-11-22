"""固定原生浏览器工具的求职适配；媒体帧不经过此模块。"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import uuid
from urllib.parse import urlsplit

from .boss_filter_dom import FILTER_DOM
from .boss_script import VERIFICATION_PROMPT, VERIFICATION_TITLE


ORIGIN = 'https://www.zhipin.com'
OPERATIONS = {'observe', 'open_job', 'next', 'return', 'messages', 'open_conversation', 'greet', 'reply', 'resume'}
LOCATION_DIAGNOSTIC = r'''(() => ({hostname:window.location.hostname,protocol:window.location.protocol,
  is_top:window===window.top,top_hostname:(() => {try{return window.top.location.hostname;}catch{return '(cross-origin)';}})()}))()'''

# 页面字符串仅通过 JSON 数据传入；模型和网站不能提交可执行脚本。
PAGE_SCRIPT = r'''
(async (args) => {
  const text = node => (node?.innerText || node?.textContent || '').trim();
  const all = selector => Array.from(document.querySelectorAll(selector));
  const visible = node => {
    if (!node || !node.getClientRects().length) return false;
    const style=getComputedStyle(node);
    return !['hidden','collapse'].includes(style.visibility) && style.display!=='none' && style.opacity!=='0';
  };
  const shown = node => visible(node) && !node.disabled && node.getAttribute('aria-disabled')!=='true';
  const get = (root, selector) => root?.querySelector(selector);
  function pageState(body) {
    const prompt=new RegExp(__VERIFY_PROMPT__), title=new RegExp(__VERIFY_TITLE__);
    const verification=title.test(body.trim()) || body.split(/\r?\n/).some(line=>prompt.test(line.trim())) ||
      all('input[placeholder*="验证码"],input[aria-label*="验证码"],.geetest_panel,.yidun_popup,.nc_wrapper,iframe[src*="captcha" i]').some(visible) ||
      all('h1,h2,[role="alert"],[role="alertdialog"],[role="dialog"]').some(node =>
        visible(node) && !node.closest('.job-banner,.job-sec-text,.job-card-wrapper,.job-card-box,.card-area,.sub-li,.message-item,.chat-message') &&
        (node.innerText || '').split(/\r?\n/).some(line=>title.test(line.trim())));
    if (verification) return {status:'manual_required',message:'平台要求人工验证'};
    if (/扫码登录|登录后查看|请先登录/.test(body)) return {status:'login_required',message:'请先完成浏览器登录'};
    return {status:'ready',message:''};
  }
  function guard() {
    if (location.origin !== args.origin) throw Error('页面不在授权网站');
    const body=(document.body?.innerText || '').trim();
    const state=pageState(body);
    if (!args.diagnostic && state.status!=='ready') throw Error(state.message+'，已停止');
    if (Date.now() > args.expires_at_ms) throw Error('浏览器动作已过期');
    return body;
  }
  function diagnostics(body) {
    const selectors=['.job-card-wrapper','.job-card-box','.card-area','.sub-li','a[href*="/job_detail/"]',
      '.job-name','.company-name','.company-location','.job-detail','.job-sec-text','.job-banner .name h1',
      '.user-list li','.chat-list li','.conversation-item','.message-item','.chat-content','.chat-conversation','[class*="city"]',
      '.friend-content','.chat-no-data','[contenteditable="true"]','textarea','input[type="file"]'];
    let remaining=2500;
    function tree(node,depth=0) {
      if (!node || remaining--<=0 || depth>7) return null;
      if (node.nodeType===Node.TEXT_NODE) return (node.textContent||'').trim().slice(0,300)||null;
      if (node.nodeType!==Node.ELEMENT_NODE || /^(SCRIPT|STYLE|NOSCRIPT|IFRAME|SVG|PATH|LINK|META)$/.test(node.tagName)) return null;
      const row={tag:node.tagName.toLowerCase()};
      for (const attr of ['id','class','role','aria-label','aria-selected','aria-current','contenteditable','type','accept'])
        if (node.hasAttribute(attr)) row[attr]=node.getAttribute(attr).slice(0,500);
      for (const attr of node.attributes)
        if (attr.name.startsWith('data-') && !attr.name.startsWith('data-v-') && !/token|secret|password|cookie|auth|session/i.test(attr.name))
          row[attr.name]=attr.value.slice(0,500);
      if (node.tagName==='A' && node.getAttribute('href')) {
        try {
          const link=new URL(node.href,location.href);
          if (link.origin===args.origin) {row.link_path=link.pathname;row.link_query_keys=[...new Set(link.searchParams.keys())];}
        } catch {}
      }
      if (node.tagName==='A' && node.getAttribute('href')?.includes('/job_detail/')) {
        try {row.job_path=new URL(node.href,location.href).pathname;} catch {}
      }
      row.children=Array.from(node.childNodes).slice(0,100).map(child=>tree(child,depth+1)).filter(Boolean);
      return row;
    }
    const roots=new Set();
    for (const node of all('.job-sec-text').filter(shown).slice(0,8))
      roots.add(node.closest('.job-sec') || node.parentElement || node);
    for (const node of all('a[href*="/job_detail/"],.user-list,.chat-list,.chat-content,.chat-conversation').filter(shown).slice(0,25)) {
      const root=node.tagName==='A' ? node.closest('[class*="card"]') || node.parentElement?.parentElement || node : node;
      if (![...roots].some(existing=>existing.contains(root))) roots.add(root);
    }
    for (const node of all('[class*="city"]').filter(shown).slice(0,8))
      if (![...roots].some(existing=>existing.contains(node))) roots.add(node);
    if (!roots.size && document.body) roots.add(document.body);
    const samples=all('.user-list li[role="listitem"],.chat-list li[role="listitem"]')
      .filter(shown).slice(0,3).map(node=>tree(node));
    return {page_state:pageState(body),ready_state:document.readyState,
      selector_counts:Object.fromEntries(selectors.map(selector=>[selector,all(selector).length])),
      dom:[...roots].map(node=>tree(node)).filter(Boolean),chat_item_samples:samples.filter(Boolean),
      dom_node_limit:2500,dom_truncated:remaining<=0};
  }
  function stableId(node) {
    for (const key of ['data-mid','data-message-id','data-id','data-uid','data-user-id']) {
      const value = node?.getAttribute(key);
      if (value) return key + ':' + value;
    }
    return '';
  }
  function cards() {
    const seen = new Set();
    return all('.job-card-wrapper,.job-card-box,.card-area,.sub-li').filter(shown).flatMap(node => {
      const link = get(node, 'a[href*="/job_detail/"]');
      if (!link) return [];
      const url = new URL(link.href, location.href);
      if (url.origin !== args.origin || seen.has(url.pathname)) return [];
      seen.add(url.pathname);
      const footer = get(node, '.job-card-footer .boss-info .boss-logo');
      return [{key:url.pathname,url:url.href,job:(text(get(node,'.job-name,.sub-li-top .name')) || text(get(node,'.job-title')).split(/\r?\n/)[0] || text(link)).slice(0,300),
        company:(text(get(node,'.company-name,.company-info a,.sub-li-bottom .user-info > p .name')) || (footer ? text(get(node,'.job-card-footer .boss-name')) : '')).slice(0,300),
        recruiter:footer ? '' : text(get(node,'.boss-name,.info-public')).slice(0,200),
        salary:text(get(node,'.salary,.job-salary,.salary-text')).slice(0,200),
        city:text(get(node,'.job-area,.area-district,.job-location,.job-card-footer .company-location,.sub-li-bottom-commany-place .name')).slice(0,300),
        tags:(text(get(node,'.tag-list,.job-text')) || text(get(node,'.job-info,.job-card-footer'))).slice(0,1000),
        contacted:/继续沟通|已沟通/.test(text(node)),_element:node}];
    });
  }
  function scroller() {
    for (let p=cards()[0]?._element?.parentElement;p;p=p.parentElement)
      if (p.scrollHeight > p.clientHeight+10 && /auto|scroll/.test(getComputedStyle(p).overflowY)) return p;
    return document.scrollingElement || document.documentElement;
  }
  function conversations() {
    const rows=all('.user-list li[role="listitem"],.chat-list li[role="listitem"],.conversation-item[data-id],li[role="listitem"][data-id]')
      .filter(shown).map(node=>{
        const box=get(node,'.name-box'), spans=box?Array.from(box.children).filter(child=>child.tagName==='SPAN'):[];
        const identity=spans.length===3 && spans[0].matches('.name-text') ? spans.map(child=>text(child).replace(/\s+/g,' ')) : [];
        const platformKey=stableId(node);
        return {key:platformKey || (identity.length===3&&identity.every(Boolean)?'visible-v1:'+JSON.stringify(identity):''),
        identity:identity.length===3&&identity.every(Boolean)?identity:null,identity_source:platformKey?'platform_dom':'visible_identity',
        label:text(get(node,'.name-box,.name-text')).slice(0,300),
        unread:shown(get(node,'.unread-count,.badge-count,.notice-badge')) && text(get(node,'.unread-count,.badge-count,.notice-badge'))!=='0',
        active:/(^|\s)(active|selected)(\s|$)/.test(String(node.className || '')) || node.getAttribute('aria-selected')==='true' || !!get(node,'.friend-content.selected'),
        _element:node};});
    return rows.filter(row=>row.key && rows.filter(other=>other.key===row.key).length===1);
  }
  function chat() {
    const active=conversations().filter(row=>row.active);
    const messages=all('.message-item,.chat-message,.chat-conversation .message').filter(shown).filter(node=>
      node.matches('.message-item') || (stableId(node)&&/(^|\s)(item-friend|item-myself|message-other|message-self|incoming|outgoing)(\s|$)/.test(String(node.className||'')))).map(node=>{
      const cls=String(node.className || '');
      const incoming=/(^|\s)(item-friend|message-other|incoming)(\s|$)/.test(cls);
      const outgoing=/(^|\s)(item-myself|message-self|outgoing)(\s|$)/.test(cls);
      const system=get(node,'.articles-center,.system-card,.system-message');
      const bubble=get(node,'.message-content > .text');
      return {key:stableId(node),text:text(system||bubble||node),kind:system?'system_card':bubble||!get(node,'.message-content')?'text':'unsupported',
        direction:system?'system':incoming&&!outgoing?'incoming':outgoing&&!incoming?'outgoing':'unknown'};
    });
    const jobs=all('.chat-content a[href*="/job_detail/"],.chat-conversation a[href*="/job_detail/"],.chat-job a[href*="/job_detail/"]')
      .map(node=>new URL(node.href,location.href)).filter(url=>url.origin===args.origin).map(url=>url.pathname);
    const header=get(document,'.chat-conversation .base-info');
    const identity=header?[text(get(header,'.name-text')),text(get(header,':scope > span:not(.base-title)')),text(get(header,':scope > .base-title'))].map(value=>value.replace(/\s+/g,' ')):[];
    const current=active.length===1 && (active[0].identity_source!=='visible_identity' ||
      (identity.every(Boolean) && JSON.stringify(active[0].identity)===JSON.stringify(identity)))?active[0]:null;
    const position=get(document,'.chat-conversation .chat-position-content');
    const job_context=current&&position?{company:current.identity?.[1]||'',job:text(get(position,'.position-name')),
      city:text(get(position,'.city')),salary:text(get(position,'.salary'))}:null;
    return {conversation:current?.key||'',identity_source:current?.identity_source||'',messages,
      job_keys:[...new Set(jobs)],job_context,conversations:conversations().map(({_element,...row})=>row)};
  }
  const greetControls = '.job-op button,.job-op a,.job-op [role="button"],.job-banner button,.job-banner a,.job-banner [role="button"]';
  const greetLabel = /^(立即沟通|立即开聊|打招呼)$/;
  const jobHeading = () => text(get(document,'.job-banner .name h1,.job-name h1,.job-title h1'));
  function greetState() {
    if (!location.pathname.startsWith('/job_detail/')) return {state:'unknown'};
    const buttons=all(greetControls).filter(shown);
    const ready=buttons.filter(node=>greetLabel.test(text(node)));
    const contacted=buttons.some(node=>/^(继续沟通|已沟通)$/.test(text(node)));
    const closed=all(greetControls+',.job-status,.job-status-tip,.job-detail-empty,.job-invalid-tip').filter(visible).some(node=>
      /^(?:(?:该)?(?:职位|岗位)(?:已(?:下架|下线|关闭|失效)|不存在)|(?:已)?停止招聘|招聘已结束)[。！!]?$/.test(text(node)));
    if (ready.length) return ready.length===1 && !contacted && !closed ? {state:'ready',button:ready[0]} : {state:'unknown'};
    return {state:contacted?'contacted':closed?'unavailable':'unknown'};
  }
  function jobDescription() {
    const bodies=all('.job-sec-text').filter(shown).map(node=>{
      const section=node.closest('.job-detail-section,.job-sec,.detail-section-item');
      return {node,section,heading:text(get(section,'.detail-content-header h3,h3'))};
    });
    const described=bodies.filter(row=>/^职位描述[：:]?$/.test(row.heading));
    const standalone=bodies.filter(row=>!row.heading && !row.node.closest('.company-info-box'));
    const selected=described.length ? described : standalone.length===1 ? standalone : [];
    return {nodes:selected.map(row=>row.node),text:selected.map(({node,section,heading})=>
      [heading,...Array.from(section?.querySelectorAll('.job-keyword-list')||[]).filter(shown).map(text),text(node)]
        .filter(Boolean).join('\n')).join('\n\n')};
  }
  function snapshot(includeChat=false) {
    const body=guard(), scroll=scroller(), description=jobDescription(), greeting=greetState().state;
    let capture_bounds=null;
    if (args.capture) {
      const heading=get(document,'.job-banner .name h1,.job-name h1,.job-title h1');
      const detail=description.nodes.at(-1);
      let region=includeChat ? get(document,'.chat-conversation') || get(document,'.chat-content') : heading;
      if (!includeChat && heading && detail) while(region && !region.contains(detail)) region=region.parentElement;
      if (region && ![document.body,document.documentElement].includes(region) && shown(region)) {
        const box=region.getBoundingClientRect();
        if (box.left>=0 && box.top>=0 && box.right<=innerWidth && box.bottom<=innerHeight &&
            box.height>0 && box.width>0 && window.visualViewport?.scale===1)
          capture_bounds=[box.left,box.top,box.right,box.bottom,innerWidth,innerHeight];
      }
    }
    return {url:location.href,title:document.title,cards:cards().map(({_element,...row})=>row),
      detail:description.text,
      heading:jobHeading(),
      company:text(get(document,'.company-info a,.sider-company .company-name')),
      salary:text(get(document,'.job-banner .salary,.job-salary')),
      location:text(get(document,'.job-location,.location-address,.job-address')),
      detail_contacted:greeting==='contacted',detail_greet_state:greeting,
      list_empty: ['/web/geek/jobs','/web/geek/recommend'].includes(location.pathname.replace(/\/$/,'')) &&
        all('.job-list-empty,.job-list-container .empty-box,.search-job-result .empty-box')
          .some(node=>shown(node)&&/暂无相关职位|暂无符合条件|没有找到相关职位|未找到相关职位/.test(text(node))),
      filters:[...all('.filter-item.active,.filter-select .label,.city-label,.expect-item.active').map(text).filter(Boolean),
        ...(all('.sub-li a.job-info[href*="/job_detail/"]').some(shown)?['城市页面路径 '+location.pathname]:[])],
      text:body.slice(-18000),position:{top:scroll.scrollTop||0,height:scroll.scrollHeight,viewport:scroll.clientHeight},
      capture_bounds,
      ...(args.diagnostic?{diagnostics:diagnostics(body)}:{}),
      ...(includeChat?chat():{})};
  }
  function targetChat() {
    guard();
    const value=chat(), latest=value.messages.at(-1);
    if (value.conversation!==args.conversation || !latest?.key || latest.key!==args.message_key || latest.direction!=='incoming' || latest.text!==args.message_text)
      throw Error('会话或最新原文已变化');
    if (args.expected_job_key && !value.job_keys.includes(args.expected_job_key)) throw Error('当前会话岗位链接已变化');
    if (args.expected_job_context && (!value.job_context || ['company','job','city','salary'].some(key=>value.job_context[key]!==args.expected_job_context[key]))) throw Error('当前会话岗位内容已变化');
    if (!args.manual && /面试|interview|过来聊聊|预约时间|来(?:我们)?公司.*(?:聊|见)|到(?:公司|现场).*聊/i.test(latest.text)) throw Error('面试事项保持人工接管');
    return value;
  }
  function observeActionTarget(check, invalidate) {
    const observer=new MutationObserver(()=>{ try {check();} catch {invalidate();} });
    observer.observe(document.body,{childList:true,subtree:true,characterData:true,attributes:true,
      attributeFilter:['class','role','style','hidden','aria-hidden','aria-selected','data-id','data-mid','data-message-id','data-uid','data-user-id','disabled','type']});
    return observer;
  }
  const replyInput = () => get(document,'textarea.chat-input,[contenteditable="true"].chat-input,#chat-input');
  const replyDraft = input => input?.tagName==='TEXTAREA'?input.value:input?.innerText || '';
  function markCheckedButton(button, actionLabel, validate) {
    globalThis.__jobAssistantActionCleanup?.();
    const selector=mark(button);
    const revoke=()=>{ if (button.matches(selector)) button.removeAttribute('data-job-assistant-action'); };
    const check=()=>{
      validate();
      if (!button.matches(selector)) throw Error('已核对的按钮标记已失效');
    };
    const observer=observeActionTarget(check,revoke);
    const timer=setTimeout(dispose,Math.max(0,args.expires_at_ms-Date.now()));
    function dispose() {
      observer.disconnect();revoke();clearTimeout(timer);
      window.removeEventListener('click',verifyClick,true);
      if (globalThis.__jobAssistantActionCleanup===dispose) delete globalThis.__jobAssistantActionCleanup;
    }
    function verifyClick(event) {
      const path=event.composedPath();
      if (!path.includes(button) && !path.some(node=>node instanceof Element && node.matches('button,a,[role="button"]') && actionLabel.test(text(node)))) return;
      try {
        if (!path.includes(button)) throw Error('当前点击的不是核验过的按钮');
        check();
      } catch {
        event.preventDefault();event.stopImmediatePropagation();
      } finally {dispose();}
    }
    // 原生命令算坐标后页面仍可能变化；在实际点击前再核验。
    globalThis.__jobAssistantActionCleanup=dispose;
    window.addEventListener('click',verifyClick,true);
    return selector;
  }
  function clearResumeCheck(record) {
    if (!record) return;
    record.observer?.disconnect();clearTimeout(record.timer);
    all('[data-job-assistant-upload="'+record.key+'"]').forEach(node=>node.removeAttribute('data-job-assistant-upload'));
    all('[data-job-assistant-resume-conversation="'+record.key+'"]').forEach(node=>node.removeAttribute('data-job-assistant-resume-conversation'));
    if (globalThis.__jobAssistantResumeTarget===record) delete globalThis.__jobAssistantResumeTarget;
  }
  function checkResumeTarget(record) {
    targetChat();
    if (!record || record.key!==args.upload_key || !record.input.isConnected || record.input.disabled ||
        record.input.type!=='file' || record.input.ownerDocument!==document ||
        document.querySelectorAll(record.selector).length!==1 || document.querySelector(record.selector)!==record.input)
      throw Error('简历上传框或会话绑定已变化');
  }
  function exactButton(pattern) {
    const candidates=all('button,a,[role="button"]').filter(node=>shown(node)&&pattern.test(text(node)));
    if (candidates.length!==1) throw Error('无法唯一确定操作按钮');
    return candidates[0];
  }
  function mark(node) {
    node.setAttribute('data-job-assistant-action',args.capture_key);
    return '[data-job-assistant-action="'+args.capture_key+'"]';
  }
  guard();
__FILTER_DOM__
  if (args.step==='observe') return snapshot(args.chat);
  if (args.step==='open_job') {
    const card=cards().find(row=>row.key===args.key);
    if (!card) return {missing:true,...snapshot()};
    const {_element,...source_card}=card;
    const listing=snapshot();
    return {navigate:card.url,return_url:location.href,return_position:listing.position,return_filters:listing.filters,
      job:card.job,company:card.company,source_card,source_card_current:true};
  }
  if (args.step==='next') {
    const next=all('.options-pages a,.pagination a,.ui-icon-arrow-right').find(node=>shown(node)&&!/disabled/.test(String(node.className||''))&&/下一页|›|»/.test(text(node)));
    if (next) return {click_selector:mark(next)};
    const scroll=scroller();scroll.scrollTop=scroll.scrollHeight;
    return {changed:true};
  }
  if (args.step==='restore') {const scroll=scroller();scroll.scrollTop=args.top;return snapshot();}
  if (args.step==='open_conversation') {
    const found=conversations().filter(row=>row.key===args.conversation);
    if (found.length!==1) throw Error('无法唯一确定目标会话');
    return {click_selector:mark(found[0]._element)};
  }
  if (args.step==='greet') {
    const expected=new URL(args.url);
    if (location.origin!==expected.origin || location.pathname!==expected.pathname) throw Error('当前岗位与发送目标不一致');
    const {state,button}=greetState();
    if (state==='contacted'||state==='unavailable') return {sent:false,not_done:true,already_contacted:state==='contacted',
      reason:state==='contacted'?'岗位已沟通，未重复发送':'岗位已停止招聘，未发送',...snapshot()};
    if (state!=='ready') throw Error('无法唯一确定目标岗位的立即沟通按钮，未发送');
    const heading=jobHeading();
    return {click_selector:markCheckedButton(button,greetLabel,()=>{
      guard();
      if (location.origin!==expected.origin || location.pathname!==expected.pathname || jobHeading()!==heading || greetState().button!==button)
        throw Error('岗位或沟通按钮已变化');
    })};
  }
  if (args.step==='greet_receipt') {
    const expected=new URL(args.url);
    if (location.origin!==expected.origin || location.pathname!==expected.pathname) throw Error('沟通后目标页面已变化');
    const sent=greetState().state==='contacted';
    return {sent,uncertain:!sent,...snapshot()};
  }
  if (args.step==='fill_reply') {
    const before=targetChat();
    const input=replyInput();
    if (!shown(input) || !(input.tagName==='TEXTAREA'||input.isContentEditable) || !args.text?.trim()) throw Error('聊天输入框或回复正文无效');
    const draft=replyDraft(input);
    if (draft.trim() && draft!==args.text) throw Error('输入框存在其他草稿，未覆盖');
    if (draft!==args.text) {
      if (input.tagName==='TEXTAREA') input.value=args.text;
      else input.innerText=args.text;
      input.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'insertText',data:args.text}));
    }
    return {before_keys:before.messages.map(row=>row.key).filter(Boolean)};
  }
  if (args.step==='send_reply') {
    targetChat();
    if (replyDraft(replyInput())!==args.text) throw Error('回复正文已变化');
    const label=/^发送$/, button=exactButton(label);
    return {click_selector:markCheckedButton(button,label,()=>{
      targetChat();
      if (replyDraft(replyInput())!==args.text || exactButton(label)!==button) throw Error('回复草稿或发送按钮已变化');
    })};
  }
  if (args.step==='prepare_resume') {
    const before=targetChat();
    const inputs=all('.chat-content input[type="file"],.chat-container input[type="file"],.chat-conversation input[type="file"]').filter(node=>!node.disabled&&/pdf|doc/i.test(node.accept||''));
    if (inputs.length!==1) throw Error('无法唯一确定简历上传入口');
    clearResumeCheck(globalThis.__jobAssistantResumeTarget);
    const active=conversations().filter(row=>row.active&&row.key===args.conversation);
    if (active.length!==1) throw Error('无法唯一确定简历接收会话');
    const conversation=active[0]._element;
    const attribute=['data-mid','data-message-id','data-id','data-uid','data-user-id'].find(name=>conversation.getAttribute(name));
    if (!attribute && active[0].identity_source!=='visible_identity') throw Error('简历接收会话缺少稳定编号');
    inputs[0].setAttribute('data-job-assistant-upload',args.upload_key);
    conversation.setAttribute('data-job-assistant-resume-conversation',args.upload_key);
    const identitySelector=attribute?'['+attribute+'="'+CSS.escape(conversation.getAttribute(attribute))+'"]':'';
    const selectedSelector=conversation.matches('.active,.selected')?':is(.active,.selected)':
      conversation.getAttribute('aria-selected')==='true'?'[aria-selected="true"]':' .friend-content.selected';
    const selector=':root:has([data-job-assistant-resume-conversation="'+args.upload_key+'"]'+identitySelector+selectedSelector+') input[data-job-assistant-upload="'+args.upload_key+'"]';
    const record={key:args.upload_key,input:inputs[0],selector};
    record.observer=observeActionTarget(()=>checkResumeTarget(record),()=>clearResumeCheck(record));
    record.timer=setTimeout(()=>clearResumeCheck(record),Math.max(0,args.expires_at_ms-Date.now()));
    globalThis.__jobAssistantResumeTarget=record;
    return {selector,before_keys:before.messages.map(row=>row.key).filter(Boolean)};
  }
  if (args.step==='verify_resume') {
    const record=globalThis.__jobAssistantResumeTarget;
    try {
      checkResumeTarget(record);
      if (record.selector!==args.selector) throw Error('简历上传框不是原核对节点');
      return {selector:record.selector};
    } catch(error) {clearResumeCheck(record);throw error;}
  }
  if (args.step==='message_receipt') {
    globalThis.__jobAssistantReplyCleanup?.();
    if (args.upload_key) clearResumeCheck(globalThis.__jobAssistantResumeTarget);
    const after=chat();
    const original=after.messages.some(row=>row.key===args.message_key&&row.direction==='incoming'&&row.text===args.message_text);
    if (after.conversation!==args.conversation || !original) throw Error('发送后会话或原始消息已变化');
    const sent=after.messages.some(row=>row.key&&!args.before_keys.includes(row.key)&&row.direction==='outgoing'&&
      (args.filename ? row.text.includes(args.filename)&&!/发送失败|重试|上传中/.test(row.text) : row.text===args.text));
    return {sent,uncertain:!sent,...snapshot(true)};
  }
  throw Error('不支持的浏览器步骤');
})(__ARGS__)
'''.replace('__FILTER_DOM__', FILTER_DOM).replace('__VERIFY_PROMPT__', json.dumps(VERIFICATION_PROMPT.pattern)).replace('__VERIFY_TITLE__', json.dumps(VERIFICATION_TITLE.pattern))


def read_config(root):
    path = Path(root) / 'agent-browser.json'
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise ValueError('请先连接并绑定浏览器运行时') from error
    if (not isinstance(value, dict) or value.get('protocol') != 1 or value.get('connected') is not True
            or not isinstance(value.get('session'), str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,99}', value['session'])
            or not isinstance(value.get('pinned_target'), str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', value['pinned_target'])
            or not isinstance(value.get('executable'), str)
            or not Path(value.get('executable', '')).is_file()):
        raise ValueError('浏览器运行时未连接或未绑定目标标签')
    return value


def _hostname(value):
    try:
        hostname=urlsplit(value).hostname if isinstance(value,str) else None
    except ValueError:
        hostname=None
    return hostname[:253] if hostname and re.fullmatch(r'[A-Za-z0-9.:-]+',hostname) else '(unknown)'


class BrowserOriginError(ValueError):
    def __init__(self,value,stage):
        self.stage=stage
        self.hostname=_hostname(value)
        self.url_type=type(value).__name__
        super().__init__('页面不在授权的 BOSS 网站 ['+stage+'; hostname='+self.hostname+']')


def _boss_url(value, stage='requested_url'):
    try:
        parsed = urlsplit(value) if isinstance(value,str) else None
        valid=bool(parsed and parsed.scheme=='https' and parsed.hostname=='www.zhipin.com'
                   and not parsed.username and not parsed.password and parsed.port in (None,443))
    except ValueError:
        valid=False
    if not valid:
        raise BrowserOriginError(value,stage)
    return value


class Browser:
    def __init__(self, root, check=None):
        self.root = Path(root).resolve()
        self.config = read_config(root)
        self.check = check or (lambda: None)

    def command(self, *args, script=None, before_dispatch=None):
        self.check()
        current = read_config(self.root)
        if any(current[key] != self.config[key] for key in ('session', 'executable', 'pinned_target')):
            raise ValueError('浏览器连接或绑定已经变化')
        env = {**os.environ, 'JOB_ASSISTANT_EMBEDDED': '1', 'JOB_ASSISTANT_EXISTING_DAEMON': '1'}
        options = {'input': script, 'text': True, 'encoding': 'utf-8', 'capture_output': True,
                   'timeout': 18, 'env': env, 'shell': False}
        if os.name == 'nt':
            options['creationflags'] = subprocess.CREATE_NO_WINDOW
        if before_dispatch is not None:
            before_dispatch()
        try:
            completed = subprocess.run([self.config['executable'], '--session', self.config['session'], '--pin-tab', '--json', *args], **options)
            if len(completed.stdout) > 4 * 1024 * 1024:
                raise ValueError('浏览器结果超出允许大小')
            value = json.loads(completed.stdout)
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as error:
            raise ValueError('浏览器命令失败或超时；已经开始的发送必须核查，不能重试') from error
        if completed.returncode or not isinstance(value, dict) or value.get('success') is not True:
            reason = value.get('error', '请检查原生运行时') if isinstance(value, dict) else '结果不是对象'
            raise ValueError('浏览器未完成命令：' + str(reason)[:500])
        return value.get('data', {})

    def _bound_tab(self):
        tabs = self.command('tab', 'list').get('tabs', [])
        found = [row for row in tabs if row.get('targetId') == self.config['pinned_target']]
        if len(found) != 1 or found[0].get('active') is not True:
            raise ValueError('绑定标签已关闭或当前目标已变化')
        return found[0]

    def verify_target(self):
        tab=self._bound_tab()
        _boss_url(tab.get('url',''),stage='target_metadata')
        return tab

    def inspect_context(self):
        # 元信息探针不读取页面正文；即使工具元数据异常也只记录主机名。
        tab=self._bound_tab()
        result=self.command('eval','--stdin',script=LOCATION_DIAGNOSTIC)
        context=result.get('result')
        if not isinstance(context,dict):raise ValueError('执行上下文未返回位置元信息')
        protocol=context.get('protocol')
        return {'target_id':tab.get('targetId',self.config['pinned_target']),
                'target_hostname':_hostname(tab.get('url')),'runtime_hostname':_hostname(result.get('origin')),
                'target_url_type':type(tab.get('url')).__name__,'runtime_origin_type':type(result.get('origin')).__name__,
                'document_hostname_type':type(context.get('hostname')).__name__,
                'document_hostname':_hostname('https://'+str(context.get('hostname',''))),
                'top_hostname':'(cross-origin)' if context.get('top_hostname')=='(cross-origin)' else _hostname('https://'+str(context.get('top_hostname',''))),
                'document_protocol':protocol if protocol in ('https:','http:','about:','chrome:','blob:','file:') else '(unknown)',
                'is_top':context.get('is_top') is True}

    def evaluate(self, step, **args):
        self.verify_target()
        payload = {'origin': ORIGIN, 'expires_at_ms': int(time.time() * 1000) + 15000,
                   'capture_key':uuid.uuid4().hex, **args, 'step': step}
        result = self.command('eval', '--stdin', script=PAGE_SCRIPT.replace('__ARGS__', json.dumps(payload, ensure_ascii=True)))
        value = result.get('result')
        if not isinstance(value, dict):
            raise ValueError('浏览器未返回可核验的页面观察')
        if value.get('url'):
            _boss_url(value['url'],stage='evaluation_document')
        return value

    def wait(self, step, ready, **args):
        deadline = time.monotonic() + 8
        while True:
            result = self.evaluate(step, **args)
            if ready(result) or time.monotonic() >= deadline:
                return result
            time.sleep(.35)

    def click(self, prepared, before_dispatch=None):
        selector=prepared.get('click_selector','')
        if not re.fullmatch(r'\[data-job-assistant-action="[0-9a-f]{32}"\]',selector):
            raise ValueError('操作目标不是本轮核对的元素')
        if before_dispatch is None:
            self.command('click', selector)
        else:
            self.command('click', selector, before_dispatch=before_dispatch)

    def fill(self, prepared, value):
        selector=prepared.get('input_selector','')
        if not re.fullmatch(r'\[data-job-assistant-action="[0-9a-f]{32}"\]',selector):
            raise ValueError('输入框不是本轮核对的元素')
        self.command('fill',selector,'--',value)

    def filter_input(self, prepared, value=None):
        kinds = [(name, prepared[name + '_selector']) for name in ('click', 'hover', 'input')
                 if prepared.get(name + '_selector')]
        if len(kinds) != 1 or not re.fullmatch(r'\[data-job-assistant-action="[0-9a-f]{32}"\]', kinds[0][1]):
            raise ValueError('筛选目标不是本轮核对的控件')
        kind, selector = kinds[0]
        if kind == 'input':
            if not isinstance(value, str):
                raise ValueError('筛选输入框缺少文字')
            self.fill(prepared, value)
        elif kind == 'hover':
            point = prepared.get('point')
            if (not isinstance(point, list) or len(point) != 2
                    or not all(type(axis) in (int, float) and 0 <= axis < 100_000 for axis in point)):
                raise ValueError('筛选目标缺少已核验的 Chrome 标签内位置')
            self.command('mouse', 'move', str(point[0]), str(point[1]))
        else:
            self.command(kind, selector)

    def capture(self, result):
        self.verify_target()
        before = self.evaluate('observe', chat='messages' in result, capture=True)
        if facts_hash(before) != facts_hash(result):
            raise ValueError('截图前岗位或消息内容已变化，请重新观察')
        folder = self.root / 'frames'
        folder.mkdir(exist_ok=True)
        path = folder / (uuid.uuid4().hex + '.png')
        # Chrome 的元素截图会临时调整浏览器视口。只采集当前视口，再裁剪完整可见的区域。
        self.command('screenshot', str(path))
        self.verify_target()
        after = self.evaluate('observe', chat='messages' in result, capture=True)
        if facts_hash(before) != facts_hash(after):
            raise ValueError('截图前后岗位或消息内容已变化，不能归档旧观察')
        region = 'viewport'
        bounds = before.get('capture_bounds')
        if (isinstance(bounds, list) and len(bounds) == 6 and bounds == after.get('capture_bounds')
                and all(type(value) in (int, float) for value in bounds)):
            left, top, right, bottom, width, height = bounds
            if 0 <= left < right <= width and 0 <= top < bottom <= height:
                from PIL import Image
                with Image.open(path) as original:
                    x1, x2 = (round(x * original.width / width) for x in (left, right))
                    y1, y2 = (round(y * original.height / height) for y in (top, bottom))
                    cropped = original.crop((x1, y1, x2, y2)) if x2 > x1 and y2 > y1 else None
                if cropped is not None:
                    cropped.save(path, format='PNG')
                    region = 'element'
        return {**result, 'frame_file': str(path), 'tab_id': self.config['pinned_target'],
                'capture_region': region}


def facts_hash(result):
    keys=('url','cards','heading','company','salary','location','detail','detail_contacted','detail_greet_state','list_empty','filters','conversation','identity_source','messages','job_keys','job_context')
    facts={key:result[key] for key in keys if key in result}
    return hashlib.sha256(json.dumps(facts,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def _candidate_digest(record):
    return hashlib.sha256(json.dumps({key:record[key] for key in ('card','list_url','filters','target_id')},
        sort_keys=True,ensure_ascii=False).encode()).hexdigest()


def _register_candidates(root, run_id, target, observed):
    if not observed.get('cards'):return
    path=Path(root)/'agent-browser-state'/(run_id+'.candidates.json')
    registry=json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {'protocol':1,'target_id':target,'candidates':{}}
    if registry.get('protocol')!=1 or registry.get('target_id')!=target:
        raise ValueError('本轮候选记录的绑定标签已变化')
    source=Path(observed['frame_file'])
    evidence=Path(root)/'candidate-evidence'/run_id/source.name
    evidence.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(source,evidence)
    proof={'frame':str(evidence),'frame_sha256':hashlib.sha256(evidence.read_bytes()).hexdigest()}
    for card in observed['cards']:
        record={'card':card,'list_url':observed['url'],'filters':observed.get('filters',[]),'target_id':target,**proof}
        record['facts_sha256']=_candidate_digest(record)
        registry['candidates'][card['key']]=record
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(registry,ensure_ascii=False),encoding='utf-8');temporary.replace(path)


def _registered_candidate(root, run_id, target, key, listing):
    path=Path(root)/'agent-browser-state'/(run_id+'.candidates.json')
    registry=json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
    record=registry.get('candidates',{}).get(key)
    if registry.get('protocol')!=1 or not record or registry.get('target_id')!=target or record.get('target_id')!=target:
        raise ValueError('岗位已离开当前列表，且没有本轮同标签的真实候选记录')
    if record.get('facts_sha256')!=_candidate_digest(record):raise ValueError('真实候选记录校验失败')
    if record['list_url']!=listing.get('url') or record['filters']!=listing.get('filters',[]):
        raise ValueError('候选所在的城市、筛选或列表页面已变化，不能沿用旧记录')
    evidence=Path(record['frame'])
    if (evidence.resolve().parent!=(Path(root)/'candidate-evidence'/run_id).resolve() or evidence.is_symlink()
            or not evidence.is_file() or hashlib.sha256(evidence.read_bytes()).hexdigest()!=record['frame_sha256']):
        raise ValueError('真实候选的原始截图证据已丢失或变化')
    card=record['card']
    if card.get('key')!=key or urlsplit(_boss_url(card.get('url'))).path!=key:
        raise ValueError('真实候选的岗位地址与已记录标识不一致')
    return {'navigate':card['url'],'return_url':listing['url'],'return_position':listing['position'],
            'return_filters':listing.get('filters',[]),'source_card':card,'source_card_current':False}


def _return_page_location(url):
    parts = urlsplit(url or '')
    query = '&'.join(pair for pair in parts.query.split('&') if pair.partition('=')[0] not in ('ka', 'lid'))
    return parts._replace(query=query)


def browser_call(root, operation, run_id, check=None, claim_greet=None, **params):
    if operation not in OPERATIONS or not re.fullmatch(r'[0-9a-f]{32}', run_id):
        raise ValueError('浏览器操作或轮次无效')
    browser = Browser(root, check)
    if params.get('expected_target') and params['expected_target']!=browser.config['pinned_target']:
        raise ValueError('Chrome 绑定标签已变化，请选择 BOSS 标签后恢复任务，应用将重新核验投递范围')
    proof = params.pop('filter_scope', None)
    if proof is not None:
        from .boss_filter_scope import check_list_scope
        if proof['target'] != browser.config['pinned_target']:
            raise ValueError('本轮筛选需在新 Chrome 标签重新核验，请恢复任务重试')
        if operation in ('open_job', 'next'):
            check_list_scope(proof, browser.verify_target()['url'])
    folder = Path(root) / 'agent-browser-state'
    folder.mkdir(exist_ok=True)
    path = folder / (run_id + '.json')
    if operation == 'open_job':
        if not isinstance(params.get('key'),str):raise ValueError('岗位标识必须为已观察的真实key')
        selected = browser.evaluate('open_job', key=params['key'])
        if selected.get('missing'):
            selected=_registered_candidate(root,run_id,browser.config['pinned_target'],params['key'],selected)
        if proof is not None:
            check_list_scope(proof, selected['return_url'], selected.get('return_filters'))
        destination = _boss_url(selected['navigate'])
        if not urlsplit(destination).path.startswith('/job_detail/'):
            raise ValueError('不是实际岗位详情链接')
        selected['target_id']=browser.config['pinned_target']
        path.write_text(json.dumps(selected, ensure_ascii=False), encoding='utf-8')
        browser.command('open', destination)
        result = browser.wait('observe', lambda row: row.get('detail_greet_state')=='unavailable' or bool(row.get('heading') and row.get('detail')))
        if urlsplit(result['url']).path != urlsplit(destination).path or not (result.get('heading') or result.get('detail_greet_state')=='unavailable'):
            raise ValueError('目标岗位详情未就绪')
        result['source_card']=selected['source_card']
        result['source_card_current']=selected.get('source_card_current',True)
    elif operation == 'return':
        if not path.is_file():
            raise ValueError('没有本轮推荐列表返回位置')
        state = json.loads(path.read_text(encoding='utf-8'))
        if state.get('target_id')!=browser.config['pinned_target']:raise ValueError('返回位置的绑定标签已变化或缺少原始身份')
        destination=_boss_url(state['return_url'])
        if proof is not None:
            check_list_scope(proof, destination, state.get('return_filters'))
        location = _return_page_location(destination)
        current=browser.verify_target()
        if _return_page_location(current['url']) != location:
            if urlsplit(current['url']).path!=urlsplit(state['navigate']).path:
                raise ValueError('当前页不是本轮打开的岗位，不能盲目返回其他历史页面')
            browser.command('back')

        def original_list(row):
            return (_return_page_location(row.get('url')) == location
                    and row.get('filters', []) == state.get('return_filters', [])
                    and bool(row.get('cards') or row.get('list_empty')))

        if not original_list(browser.wait('observe', original_list)):
            raise ValueError('原筛选列表尚未恢复或筛选条件已变化')
        top=state['return_position']['top']
        def restored(row):
            position=row.get('position',{})
            requested=min(top,max(0,position.get('height',0)-position.get('viewport',0)))
            return original_list(row) and abs(position.get('top',-100)-requested)<20
        result = browser.wait('restore',restored,top=top)
        if not restored(result):
            raise ValueError('原列表滚动位置尚未恢复，请核查后继续')
    elif operation == 'next':
        before = browser.evaluate('observe')
        if proof is not None:
            check_list_scope(proof, before['url'], before.get('filters'))
        prepared=browser.evaluate('next')
        if prepared.get('click_selector'):
            browser.click(prepared)
        result = browser.wait('observe', lambda row: (row.get('cards'), row.get('position')) != (before.get('cards'), before.get('position')))
    elif operation in ('messages', 'open_conversation'):
        if operation == 'messages':
            tab = browser.verify_target()
            if not urlsplit(tab['url']).path.startswith('/web/geek/chat'):
                browser.command('open', ORIGIN + '/web/geek/chat')
        else:
            browser.click(browser.evaluate('open_conversation', conversation=params['conversation']))
        result = browser.wait('observe', lambda row: bool(row.get('conversations')) and
                              (operation != 'open_conversation' or row.get('conversation') == params['conversation']), chat=True)
        if not result.get('conversations') or (operation == 'open_conversation' and result.get('conversation') != params['conversation']):
            raise ValueError('未能识别目标聊天列表或会话，自动发送已暂停')
    elif operation == 'greet':
        same_observation = True
        if params.get('observation_id'):
            observed=browser.evaluate('observe')
            same_observation = facts_hash(observed)==params['observation_id']
            if not same_observation and observed.get('detail_greet_state') not in ('contacted','unavailable'):
                raise ValueError('岗位观察已变化，旧判断不能继续沟通')
        result = browser.evaluate('greet', url=_boss_url(params['url']))
        if result.get('click_selector'):
            if not same_observation:
                raise ValueError('岗位观察已变化，旧判断不能继续沟通')
            browser.click(result, before_dispatch=claim_greet)
            result = browser.wait('greet_receipt', lambda row: row.get('sent') is True, url=params['url'])
    elif operation in ('reply', 'resume'):
        upload_key=uuid.uuid4().hex
        before = browser.evaluate('prepare_resume' if operation == 'resume' else 'fill_reply', upload_key=upload_key, **params)
        if operation == 'reply':
            browser.click(browser.evaluate('send_reply', **params))
        else:
            resume = Path(params['resume_path'])
            if not resume.is_file():
                raise ValueError('本轮原始简历文件不存在')
            verified=browser.evaluate('verify_resume',upload_key=upload_key,selector=before['selector'],**params)
            if verified.get('selector')!=before['selector']:
                raise ValueError('简历上传框在发送前已经变化')
            browser.command('upload', verified['selector'], str(resume))
        result = browser.wait('message_receipt', lambda row: row.get('sent') is True,
                              before_keys=before['before_keys'],upload_key=upload_key if operation=='resume' else '',
                              filename=Path(params['resume_path']).name if operation == 'resume' else '', **params)
    else:
        result = browser.evaluate('observe')
    if proof is not None and (operation in ('next', 'return') or
            operation == 'observe' and not urlsplit(result['url']).path.startswith('/job_detail/')):
        check_list_scope(proof, result['url'], result.get('filters'))
    result = browser.capture(result)
    if operation in ('observe','next','return'):
        _register_candidates(root,run_id,browser.config['pinned_target'],result)
    result['observation_id'] = facts_hash(result)
    result['content_ready'] = not result.get('heading') or bool(result.get('detail'))
    return result


def connection_status(root):
    try:
        browser = Browser(root)
        tab = browser.verify_target()
        return {'connected': True, 'ready': True, 'target_id': tab['targetId'], 'url': tab['url']}
    except ValueError as error:
        return {'connected': False, 'ready': False, 'message': str(error)}


def inspect_page(root, scope='auto', expected_target=None):
    """只读当前绑定页；完整页面诊断仅写入本机，不进入模型结果。"""
    if scope not in ('auto','jobs','messages'):
        raise ValueError('页面检查仅支持 auto、jobs 或 messages')
    browser=Browser(root)
    if not expected_target or browser.config['pinned_target']!=expected_target:
        raise ValueError('页面检查的绑定标签已经变化')
    record={'schema':2,'captured_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
            'target_id':expected_target,'scope':scope,'configured_hostname':_hostname(browser.config.get('url'))}
    stage='context_metadata'
    try:
        record['context']=browser.inspect_context()
        stage='target_metadata'
        browser.verify_target()
        stage='page_read'
        observed=browser.evaluate('observe',chat=True,diagnostic=True)
        record['observation']=observed
    except ValueError as error:
        stage=getattr(error,'stage',stage)
        hostname=getattr(error,'hostname',record.get('context',{}).get('document_hostname','(unknown)'))
        record['error']={'stage':stage,'hostname':hostname,
                         'message':'页面检查未完成 ['+stage+'; hostname='+hostname+']'}
        if isinstance(error,BrowserOriginError):
            record['error']['reason']='origin_not_allowed'
            record['error']['url_type']=error.url_type
        elif '页面不在授权网站' in str(error):record['error']['reason']='page_guard_origin'
        else:record['error']['reason']='context_or_read_failed'
    folder=Path(root).resolve()/'browser-diagnostics'
    folder.mkdir(exist_ok=True)
    if folder.resolve().parent!=Path(root).resolve():
        raise ValueError('页面诊断目录超出应用数据目录')
    diagnostic=folder/(uuid.uuid4().hex+'.json')
    with diagnostic.open('x',encoding='utf-8') as output:
        json.dump(record,output,ensure_ascii=False)
    if 'error' in record:
        return {'status':'inspection_error','message':record['error']['message'],'stage':stage,
                'actual_hostname':record['error']['hostname'],'counts':{'cards':0,'conversations':0,'messages':0},
                'diagnostic_path':str(diagnostic)}
    state=observed.get('diagnostics',{}).get('page_state',{})
    path=urlsplit(observed.get('url','')).path
    kind='messages' if path.startswith('/web/geek/chat') else 'detail' if path.startswith('/job_detail/') else 'jobs'
    counts={key:len(observed.get(key,[])) for key in ('cards','conversations','messages')}
    detail_ready=bool(observed.get('heading') and observed.get('detail'))
    status=state.get('status','unsupported_layout')
    message=state.get('message','')
    if status=='ready':
        if scope=='messages' and kind!='messages':
            status='wrong_page';message='当前不是消息页；检查不会自动切换页面'
        elif scope=='jobs' and kind=='messages':
            status='wrong_page';message='当前是消息页，请手动打开岗位页后检查'
        elif (kind=='messages' and not counts['conversations']) or (kind=='jobs' and not counts['cards']) or (kind=='detail' and not detail_ready):
            status='unsupported_layout';message='当前布局尚未识别到完整岗位或会话，已保存诊断'
        else:
            message='当前页面已识别，检查没有切换页面或发送消息'
    return {'status':status,'message':message,'page_kind':kind,'url_path':path,'counts':counts,
            'detail_ready':detail_ready,'diagnostic_path':str(diagnostic)}


def check_current_source(root, run_id, config, job_key):
    from .boss_filter_scope import check_list_scope, read_scope
    proof = read_scope(root, run_id, config)
    if config.get('boss_mode','filtered')!='filtered' and proof is None:
        return
    path=Path(root)/'agent-browser-state'/(run_id+'.json')
    if not path.is_file():
        if proof is not None:
            raise ValueError('未找到来自已核验筛选范围的岗位来源')
        return
    source=json.loads(path.read_text(encoding='utf-8'))
    if (config.get('boss_mode', 'filtered') == 'filtered' and source.get('source_card_current') is False
            or source.get('source_card', {}).get('key') != job_key):
        raise ValueError('当前平台筛选模式不能凭历史或其他岗位卡片继续沟通')
    if proof is not None:
        if source.get('target_id') != proof['target']:
            raise ValueError('岗位来源与本轮筛选核验标签不一致')
        check_list_scope(proof, source.get('return_url'), source.get('return_filters'))


def action_check(store, run_id, operation, params, *, status='executing'):
    """每次原生命令前重新检查动作；不在等待浏览器时持有写事务。"""
    def check():
        from .cli import verify_materials
        config = verify_materials(store, run_id)
        store.check_run(run_id, operation)
        action = store.one('actions', params.get('action_id', ''))
        if ('boss' not in config['channels'] or action['run_id'] != run_id
                or action['status'] != status or action['kind'] != operation):
            raise ValueError('发送授权已变化或动作已失效')
        attempt = store.one('attempts', action['attempt_id'])
        if not store.owns_attempt(run_id, attempt['id']) or attempt['channel'] != 'boss':
            raise ValueError('动作不属于本轮 BOSS 申请')
        if operation == 'greet':
            if params.get('url') != attempt['url']:
                raise ValueError('沟通目标已变化')
            check_current_source(store.root,run_id,config,attempt['job_key'])
            return
        payload = json.loads(action['payload'])
        conversation = store.one('conversations', action['recipient'])
        manual = store.manual_send_allowed(action)
        if (params.get('conversation') != action['recipient'] or conversation['latest_message'] != payload.get('message_key')
                or params.get('message_key') != payload.get('message_key') or params.get('manual', False) != manual
                or (conversation['status'] == 'human' and not manual)):
            raise ValueError('会话、最新消息或人工锁已变化')
        original = store.conn.execute('SELECT text FROM messages WHERE conversation_id=? AND message_key=?',
                                      (action['recipient'], payload['message_key'])).fetchone()
        if not original or original[0] != params.get('message_text'):
            raise ValueError('原始消息已变化')
        if operation == 'reply' and params.get('text') != payload.get('text'):
            raise ValueError('回复正文不属于已确认动作')
        if operation == 'resume' and (params.get('resume_path') != config['resume_path'] or payload.get('resume_sha256') != config['resume_sha256']):
            raise ValueError('附件不是本轮原始简历')
        if operation == 'resume':
            from .boss_messages import simple_analysis
            if simple_analysis(original[0]).get('categories') != ['resume']:
                raise ValueError('原始消息没有明确索要简历，请人工核对')
    return check


def compact_observation(result, scope='auto', offset=0):
    """证据保存在本机；模型只接收当前步骤需要的事实。"""
    common = {key: result[key] for key in ('url', 'title', 'observation_id', 'sent', 'uncertain', 'already_contacted', 'not_done', 'reason') if key in result}
    if result.get('frame'):
        common['evidence_frame'] = result['frame']['path']
    if 'sent' in result:
        return common
    if scope == 'chat' or 'messages' in result:
        for key in ('conversation','identity_source','job_keys','job_context'):
            if key in result:
                common[key] = result[key]
        conversations=result.get('conversations',[])
        common['conversations']=[row for row in conversations if row.get('active') or row.get('unread')][:8]
        common['conversations_total']=len(conversations)
        common['messages'] = result.get('messages', [])[-8:]
    elif scope == 'detail' or result.get('heading'):
        common.update({key: result[key] for key in ('heading', 'company', 'salary', 'location', 'detail', 'detail_contacted') if key in result})
        common['content_ready'] = bool(result.get('detail'))
    else:
        if type(offset) is not int or offset < 0:
            raise ValueError('候选偏移无效')
        cards = result.get('cards', [])
        common.update({key: result[key] for key in ('filters', 'position') if key in result})
        common.update(cards=cards[offset:offset+8], cards_total=len(cards), offset=offset)
        if offset+8 < len(cards):
            common['next_offset'] = offset+8
    return common
