'use strict';
// 所有招聘内容通过 textContent 渲染，不把外部文字当作 HTML。
const $ = id => document.getElementById(id);
const labels = {running:'允许执行',watching:'仅值守',paused:'已暂停',stopped:'已停止',in_progress:'处理中',success:'成功',failed:'失败',uncertain:'待核实',needs_user:'需人工',no_match:'无匹配',skipped:'已跳过',pending:'待执行',executing:'执行中',succeeded:'已确认',not_done:'未执行',greet:'首次沟通',submit:'网申提交',reply:'回复消息',resume:'发送简历',applied:'已生效',rejected:'已拒绝',cancelled:'已取消',tick:'检查消息',act:'浏览器操作',capture:'观察截图',begin:'登记申请',prepare:'准备操作',finish:'核实结果',field:'记录填写',evidence:'归档截图'};
const titles = {overview:['运行概览','让每一次投递有记录，让每一步操作看得见。'],records:['投递记录','查看本轮填写内容、执行结果与截图证据。'],attention:['待人工处理','把需要你判断的事，留给你决定。'],setup:['新建任务','复用已有资料，确认这一次求职的范围。']};
let token = new URLSearchParams(location.hash.slice(1)).get('token') || sessionStorage.getItem('job-panel-token') || '';
if (token) sessionStorage.setItem('job-panel-token', token);
history.replaceState(null, '', location.pathname);
let state = null, selected = '', page = 'overview', ticket = null, connected = false, frameId = '', frameUrl = '', attentionSignature = '';
let previewUrls = [];
const drafts = new Map();
function node(tag, text, cls) {const item=document.createElement(tag);if(text!==undefined)item.textContent=text;if(cls)item.className=cls;return item;}
function when(value) {if(!value)return '—';const d=new Date(typeof value==='number'?value*1000:value);return Number.isNaN(d.getTime())?'—':d.toLocaleString('zh-CN',{hour12:false});}
function status(value) {const badge=node('span',labels[value]||value,'badge');if(['uncertain','needs_user','pending','paused'].includes(value))badge.classList.add('warn');if(['failed','rejected','stopped'].includes(value))badge.classList.add('bad');return badge;}
function showError(error) {$('error').textContent=error.message||String(error);$('error').hidden=false;}
function notice(text) {$('notice').textContent=text;$('notice').hidden=false;}
async function api(path, data, binary=false) {
  const controller=new AbortController(), timer=setTimeout(()=>controller.abort(),12000);
  try {
    const response=await fetch(path,{method:data===undefined?'GET':'POST',headers:{Authorization:'Bearer '+token,...(data===undefined?{}:{'Content-Type':'application/json'})},body:data===undefined?undefined:JSON.stringify(data),signal:controller.signal,cache:'no-store'});
    if(!response.ok){const error=await response.json().catch(()=>({error:'请求失败'}));throw new Error(error.error||'请求失败');}
    return binary?response.blob():response.json();
  } finally {clearTimeout(timer);}
}
async function perform(button, work) {button.disabled=true;$('error').hidden=true;try{await work();await refresh();}catch(error){showError(error);}finally{button.disabled=false;renderControls();}}
function run() {return state?.runs.find(r=>r.id===selected);}
function attempts() {return (state?.attempts||[]).filter(a=>a.run_id===selected||state.run_attempts.some(r=>r.run_id===selected&&r.attempt_id===a.id));}
function go(target) {page=target;document.querySelectorAll('.page').forEach(p=>p.hidden=p.id!==page);document.querySelectorAll('.nav').forEach(n=>n.classList.toggle('active',n.dataset.page===page));$('page-title').textContent=titles[page][0];$('page-description').textContent=titles[page][1];render();}
function renderControls() {
  const current=run();
  document.querySelectorAll('[data-control]').forEach(b=>{b.disabled=!connected||!current||current.status==='stopped'||b.dataset.control===current.status;});
  $('export').disabled=!connected;
  $('view-config').disabled=!current;
  $('start-run').disabled=!connected||!ticket||!$('approve-config').checked;
}
function render() {
  if(!state)return;
  if(!state.runs.some(r=>r.id===selected))selected=state.runs[0]?.id||'';
  const options=state.runs.map(r=>{const o=node('option',when(r.created)+' · '+r.id.slice(0,6));o.value=r.id;return o;});
  const emptyOption=node('option','尚无任务');emptyOption.value='';
  $('run-select').replaceChildren(...(options.length?options:[emptyOption]));$('run-select').value=selected;
  const current=run();$('run-status').replaceChildren(current?status(current.status):node('span','未启动'));
  const items=attempts(), unresolved=state.attention.filter(a=>!a.resolved);
  $('stat-total').textContent=items.length;$('stat-success').textContent=items.filter(a=>a.status==='success').length;
  $('stat-issues').textContent=items.filter(a=>['uncertain','failed'].includes(a.status)).length;
  $('stat-attention').textContent=unresolved.length;$('nav-count').textContent=unresolved.length;
  const activity=state.activity.find(a=>a.run_id===selected&&a.updated);
  $('executor-title').textContent=activity?'最近工具活动':'等待 Codex 会话';
  $('executor-description').textContent=activity?`${when(activity.updated)} · ${labels[activity.operation]||activity.operation} · ${{started:'已开始，尚无完成回报',finished:'本次调用已结束',error:'本次调用遇到问题'}[activity.phase]||''}`:'面板没有收到本轮执行活动。请在 Codex 中继续本轮。';
  const recent=items.toSorted((a,b)=>b.updated.localeCompare(a.updated))[0];
  $('current-task').textContent=recent?`最近登记：${recent.company} / ${recent.job} · ${labels[recent.status]||recent.status}`:'暂无正在处理的岗位';
  const latestCommand=state.commands.find(c=>c.run_id===selected);
  $('command-state').textContent=latestCommand?`${when(latestCommand.created)} · ${labels[latestCommand.action]}指令：${latestCommand.status==='pending'?'等待 Codex 下一次操作时接收':labels[latestCommand.status]}${latestCommand.error?' · '+latestCommand.error:''}`:'';
  const config=current?.config;
  $('config-summary').replaceChildren();
  for(const [label,value] of [['目标岗位',config?.target_roles?.join(' / ')||'尚未配置'],['投递渠道',config?.channels?.map(v=>v==='web'?'公司官网':'BOSS').join('、')||'—'],['沟通额度',config?.channels?.includes('boss')?`${state.actions.filter(a=>a.run_id===selected&&a.kind==='greet'&&a.status!=='not_done').length} / ${config.max_contacts}`:'—'],['回复模式',config?.unknown_reply_mode==='ai'?'预设话术 + AI 回复':'预设话术 + 人工处理']]){$('config-summary').append(node('dt',label),node('dd',value));}
  $('data-root').textContent=state.data_root;$('updated').textContent='最近同步 '+when(state.server_time);
  const recentActions=state.actions.filter(a=>a.run_id===selected).toSorted((a,b)=>b.updated.localeCompare(a.updated)).slice(0,6);
  $('activity-list').replaceChildren();
  for(const action of recentActions){const item=state.attempts.find(a=>a.id===action.attempt_id);const row=node('div',undefined,'activity-row');const text=node('div',`${labels[action.kind]} · ${item?.company||action.recipient}`,'activity-name');text.append(node('small',item?.job||action.reason));row.append(text,status(action.status),node('time',when(action.updated)));$('activity-list').append(row);}
  if(!recentActions.length)$('activity-list').append(node('p','本轮尚无行动记录。','empty'));
  renderRecords();renderAttention(unresolved);renderControls();
  updateFrame().catch(showError);
}
async function updateFrame() {
  const f=state.frame;
  if(!f){$('frame').hidden=true;$('frame-empty').hidden=false;return;}
  const age=Math.max(0,Math.round((Date.now()-f.captured_at*1000)/1000));
  $('frame-title').textContent=f.title;$('frame-time').textContent=when(f.captured_at)+(age>30?' · 历史画面':' · 最近截图');
  if(frameId===f.id)return;
  const expected=f.id;frameId=expected;
  try{const blob=await api('/api/image/frame/'+expected,undefined,true);if(frameId!==expected)return;if(frameUrl)URL.revokeObjectURL(frameUrl);frameUrl=URL.createObjectURL(blob);$('frame').src=frameUrl;$('frame').hidden=false;$('frame-empty').hidden=true;}catch(error){frameId='';throw error;}
}
function renderRecords() {
  const q=$('search').value.toLowerCase(), filter=$('record-filter').value;
  const records=attempts().filter(a=>(!filter||a.status===filter)&&`${a.company} ${a.job}`.toLowerCase().includes(q)).toSorted((a,b)=>b.updated.localeCompare(a.updated));
  $('record-rows').replaceChildren();$('records-empty').hidden=records.length>0;
  for(const a of records){const row=node('tr'), company=node('td',a.company);company.append(node('small',a.job));const badge=node('td');badge.append(status(a.status));const action=node('td');const button=node('button','查看详情 ↗','text-button');button.onclick=()=>showAttempt(a).catch(showError);action.append(button);row.append(company,node('td',a.channel==='boss'?'BOSS':'官网'),badge,node('td',when(a.updated)),action);$('record-rows').append(row);}
}
async function showAttempt(attempt) {
  $('detail-title').textContent=attempt.company+' · '+attempt.job;const content=$('detail-content');content.replaceChildren();
  const link=node('a','在新标签页打开岗位');try{const url=new URL(attempt.url);if(['https:','http:'].includes(url.protocol)){link.href=url.href;link.target='_blank';link.rel='noopener noreferrer';content.append(link);}}catch{}
  content.append(node('p',`${labels[attempt.status]}${attempt.reason?' · '+attempt.reason:''}`));
  const table=node('table'), head=node('tr');['填写字段','实际填写值','资料来源'].forEach(t=>head.append(node('th',t)));table.append(head);
  for(const field of state.fields.filter(f=>f.attempt_id===attempt.id)){const row=node('tr');[field.name,field.value,field.source].forEach(t=>row.append(node('td',t)));table.append(row);}content.append(table);
  const grid=node('div',undefined,'evidence-grid');content.append(grid);$('detail-dialog').showModal();
  for(const evidence of state.evidence.filter(e=>e.attempt_id===attempt.id)){const cell=node('div'), button=node('button','查看截图 · '+evidence.kind,'secondary');cell.append(button,node('p',when(evidence.created)));grid.append(cell);button.onclick=()=>perform(button,async()=>{const blob=await api('/api/image/evidence/'+evidence.id,undefined,true), url=URL.createObjectURL(blob);previewUrls.push(url);const image=node('img');image.alt='填写或结果截图';image.src=url;cell.replaceChildren(image,node('p',evidence.kind+' · '+when(evidence.created)));});}
}
function renderAttention(items) {
  const signature=JSON.stringify([items,state.conversations,state.messages,state.notes]);
  if(signature===attentionSignature)return;attentionSignature=signature;$('attention-list').replaceChildren();
  if(!items.length){$('attention-list').append(node('div','目前没有待人工处理事项。','card empty'));return;}
  for(const item of items){const conversation=state.conversations.find(c=>c.id===item.subject), message=state.messages.find(m=>m.conversation_id===item.subject);const card=node('article',undefined,'card');const heading=node('div',undefined,'card-heading');heading.append(node('h2',item.subject),status('needs_user'));card.append(heading,node('p',item.reason),node('p',message?.text||'暂无关联消息正文。','attention-message'));
    const notes=state.notes.filter(n=>n.attention_id===item.id);for(const note of notes)card.append(node('p','已记录（未发送）：'+note.text,'attention-notes'));
    const input=node('textarea');input.rows=3;input.placeholder='记录处理意见或回复草稿，仅保存在本地';input.setAttribute('aria-label','处理意见');input.value=drafts.get(item.id)||'';input.oninput=()=>drafts.set(item.id,input.value);card.append(input);
    const actions=node('div',undefined,'attention-actions'), save=node('button','保存意见，不发送','secondary'), resume=node('button',conversation?'已处理，恢复此会话':'标记已处理','text-button');actions.append(save,resume);card.append(actions);
    save.onclick=()=>perform(save,async()=>{const submitted=input.value;await api('/api/attention',{id:item.id,action:'note',message_key:conversation?.latest_message||'',text:submitted});if(drafts.get(item.id)===submitted)drafts.delete(item.id);notice('意见已保存到本地；没有发送消息，会话仍保持人工状态。');});
    resume.onclick=()=>{if(!confirm(conversation?'请确认已在 BOSS 完成人工沟通，并允许后续新消息按本轮规则自动处理。此操作不会发送草稿。':'确认该事项已人工处理？'))return;perform(resume,async()=>{await api('/api/attention',{id:item.id,action:conversation?'resume':'resolve',message_key:conversation?.latest_message||'',confirmed:true});notice('已记录人工处理结果。');});};
    $('attention-list').append(card);
  }
}
async function refresh() {try{state=await api('/api/state');connected=true;$('connection').textContent='● 面板已连接';$('connection').className='badge';render();}catch(error){connected=false;$('connection').textContent='连接中断';$('connection').className='badge bad';renderControls();showError(error);}}
async function poll(){await refresh();setTimeout(poll,3000);}
document.querySelectorAll('.nav').forEach(button=>button.onclick=()=>go(button.dataset.page));
$('run-select').onchange=()=>{selected=$('run-select').value;render();};
document.querySelectorAll('[data-control]').forEach(button=>button.onclick=()=>{if(button.dataset.control==='stopped'&&!confirm('停止本轮后不能恢复，需要重新授权新轮次。确认停止？'))return;perform(button,async()=>{const result=await api('/api/command',{run_id:selected,action:button.dataset.control});notice(result.status==='pending'?'指令已排队。请让 Codex 会话继续本轮；执行器接收后会重新核对资料并要求新截图。':'控制状态已生效。已经发出的点击无法撤回，尚未完成的结果需要核实。');});});
$('search').oninput=renderRecords;$('record-filter').onchange=renderRecords;
$('export').onclick=()=>perform($('export'),async()=>{const blob=await api('/api/export',{},true),url=URL.createObjectURL(blob),a=node('a');a.href=url;a.download='求职结果.xlsx';a.click();setTimeout(()=>URL.revokeObjectURL(url),30000);notice('已导出所有轮次记录，不会修改原公司表。');});
document.querySelectorAll('.close-dialog').forEach(button=>button.onclick=()=>button.closest('dialog').close());
$('detail-dialog').addEventListener('close',()=>{previewUrls.forEach(URL.revokeObjectURL);previewUrls=[];});
$('view-config').onclick=()=>{ticket=null;$('config-title').textContent='本轮已授权配置';$('config-json').textContent=JSON.stringify(run().config,null,2);$('approval-area').hidden=true;$('config-dialog').showModal();};
function readConfig(){const extra=JSON.parse($('extra-config').value);if(!extra||Array.isArray(extra)||typeof extra!=='object')throw new Error('附加配置必须是 JSON 对象');return {...extra,channels:[$('channel-web').checked?'web':null,$('channel-boss').checked?'boss':null].filter(Boolean),target_roles:$('target-roles').value.split(/[,，\n]/).map(s=>s.trim()).filter(Boolean),resume_path:$('resume-path').value.trim(),profile_path:$('profile-path').value.trim(),company_table:$('company-table').value.trim(),boss_mode:$('boss-mode').value,max_contacts:Number($('max-contacts').value),unknown_reply_mode:$('unknown-mode').value,poll_seconds:Number($('poll-seconds').value)};}
$('setup-form').onsubmit=event=>{event.preventDefault();const button=event.submitter;perform(button,async()=>{const result=await api('/api/preview',{config:readConfig()});ticket=result.ticket;$('config-title').textContent='复核本轮授权';$('config-json').textContent=JSON.stringify(result.config,null,2);$('approve-config').checked=false;$('approval-area').hidden=false;$('config-dialog').showModal();});};
$('approve-config').onchange=renderControls;
$('start-run').onclick=()=>perform($('start-run'),async()=>{const result=await api('/api/start',{ticket,confirmed:$('approve-config').checked});ticket=null;selected=result.run_id;$('config-dialog').close();await refresh();go('overview');notice(`本轮已授权。请在 Codex 中继续：使用 $job-search-assistant，数据目录 ${state.data_root}，接续轮次 ${result.run_id}，先检查状态与材料，再重新观察浏览器。面板不会自行开始投递。`);});
$('import-config').onchange=async event=>{try{const file=event.target.files[0];if(!file)return;if(file.size>262144)throw new Error('配置文件不能超过 256 KB');const c=JSON.parse(await file.text());$('channel-web').checked=c.channels?.includes('web')||false;$('channel-boss').checked=c.channels?.includes('boss')||false;$('target-roles').value=(c.target_roles||[]).join('，');for(const [id,key] of [['resume-path','resume_path'],['profile-path','profile_path'],['company-table','company_table'],['boss-mode','boss_mode'],['max-contacts','max_contacts'],['unknown-mode','unknown_reply_mode'],['poll-seconds','poll_seconds']]){if(c[key]!==undefined)$(id).value=c[key];delete c[key];}delete c.channels;delete c.target_roles;for(const key of ['company_rows','resume_sha256','profile_sha256'])delete c[key];$('extra-config').value=JSON.stringify(c,null,2);notice('配置已导入，请复核后再授权。');}catch(error){showError(error);}};
poll();
