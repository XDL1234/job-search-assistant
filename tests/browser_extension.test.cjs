const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');

function harness({lease=true,receipt=true,oldReceipt=false,cardOverride=null,receiptAfter=0,replaceButton=false,onTimer=()=>{}}={}) {
  let listener,clicks=0,elapsed=0;
  const started=Date.now();
  const body={innerText:oldReceipt?'推荐职位 旧会话已发送消息':'推荐职位 嵌入式实习生'};
  const detail={innerText:'立即沟通',querySelectorAll:()=>[currentButton]};
  const confirmReceipt=()=>{
    if(!receipt || !clicks || elapsed<receiptAfter) return;
    detail.innerText='继续沟通';
    if(replaceButton) {
      currentButton={...button,innerText:'继续沟通',getClientRects:()=>[{}]};
      button.getClientRects=()=>[];
    } else button.innerText='继续沟通';
  };
  const button={innerText:'立即沟通',getClientRects:()=>[{}],getAttribute:()=>null,closest:()=>detail,
    click:()=>{clicks++;body.innerText='已发送消息';confirmReceipt();}};
  let currentButton=button;
  const job={innerText:'嵌入式实习生'},company={innerText:'测试科技'},tags={innerText:'实习 本科'};
  const link={href:'https://www.zhipin.com/job_detail/test.html',innerText:'嵌入式实习生'};
  const card=cardOverride || {innerText:'嵌入式实习生 测试科技',getClientRects:()=>[{}],getAttribute:()=>null,querySelector:s=>s.includes('job_detail')?link:s.includes('job-name')?job:s.includes('company-name')?company:s.includes('tag-list')?tags:null};
  const context={URL,Date:class extends Date{static now(){return started+elapsed;}},
    setTimeout:(fn,ms)=>{elapsed+=ms;onTimer({elapsed,body,location:context.location});confirmReceipt();fn();return 0;},
    location:{origin:'https://www.zhipin.com',href:link.href,pathname:'/job_detail/test.html'},
    document:{title:'BOSS直聘',body,scrollingElement:{scrollTop:0,scrollHeight:1000,clientHeight:800},querySelector:s=>s==='.job-banner,.job-op,.job-detail'?detail:null,
      querySelectorAll:s=>s==='.job-card-wrapper,.job-card-box,.card-area'?[card]:s==='button,a,[role="button"]'?[button]:[]},
    chrome:{runtime:{id:'trusted',onMessage:{addListener:fn=>listener=fn},sendMessage:async()=>({valid:typeof lease==='function'?lease(elapsed):lease})}}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../apps/browser-extension/content.js'),'utf8'),context);
  const call=command=>new Promise(resolve=>listener({id:'request',expires:Date.now()/1000+10,type:'job-command',...command},{id:'trusted'},resolve));
  return {call,listener,clicks:()=>clicks,elapsed:()=>elapsed};
}
test('observe uses stable job URLs and returns actual company/type',async()=>{
  const value=await harness().call({operation:'observe'});
  assert.equal(value.ok,true);assert.equal(value.result.cards[0].key,'/job_detail/test.html');
  assert.equal(value.result.cards[0].company,'测试科技');assert.match(value.result.cards[0].tags,/实习/);
});
function currentCard({jobName=true,logo=true}={}) {
  const link={href:'https://www.zhipin.com/job_detail/current.html',innerText:'嵌入式软件实习生'};
  // 保留真实 DOM 顺序：父级 job-info/job-title 位于岗位名和标签前。
  const elements=[
    ['.job-info',{innerText:'嵌入式软件实习生\n200-300元/天\n经验不限 本科'}],
    ['.job-title',{innerText:'嵌入式软件实习生\n200-300元/天'}],
    ...(jobName?[['.job-name',link]]:[]),
    ['.tag-list',{innerText:'经验不限 本科'}],
    ['.job-card-footer',{innerText:'测试嵌入式科技 青岛'}],
    ...(logo?[['.job-card-footer .boss-info .boss-logo',{}]]:[]),
    ['.job-card-footer .boss-info .boss-name',{innerText:'测试嵌入式科技'}],
    ['.boss-name',{innerText:'测试嵌入式科技'}],
  ];
  return {innerText:'嵌入式软件实习生\n200-300元/天\n经验不限 本科\n测试嵌入式科技 青岛',
    getClientRects:()=>[{}],getAttribute:()=>null,
    querySelector(selector) {
      if(selector==='a[href*="/job_detail/"]') return link;
      const selectors=selector.split(',');
      return elements.find(([candidate])=>selectors.includes(candidate))?.[1] || null;
    }};
}
test('current cards keep salary out of job title and company out of recruiter',async()=>{
  const value=await harness({cardOverride:currentCard()}).call({operation:'observe'});
  assert.equal(value.ok,true);
  const card=value.result.cards[0];
  assert.equal(card.job,'嵌入式软件实习生');
  assert.equal(card.tags,'经验不限 本科');
  assert.equal(card.company,'测试嵌入式科技');
  assert.equal(card.recruiter,'');
});
test('title fallback uses its first line and company fallback requires the logo layout',async()=>{
  const fallback=await harness({cardOverride:currentCard({jobName:false})}).call({operation:'observe'});
  assert.equal(fallback.result.cards[0].job,'嵌入式软件实习生');
  const unverified=await harness({cardOverride:currentCard({logo:false})}).call({operation:'observe'});
  assert.equal(unverified.result.cards[0].company,'');
});
test('pause lease rejects greeting before click',async()=>{
  const h=harness({lease:false});const result=await h.call({operation:'greet',url:'https://www.zhipin.com/job_detail/test.html'});
  assert.equal(result.ok,false);assert.match(result.error,/暂停/);assert.equal(h.clicks(),0);
});
test('different job cannot reuse an action',async()=>{
  const h=harness();const result=await h.call({operation:'greet',url:'https://www.zhipin.com/job_detail/other.html'});
  assert.equal(result.ok,false);assert.equal(h.clicks(),0);
});
test('single greeting requires visible receipt',async()=>{
  const h=harness();const result=await h.call({operation:'greet',url:'https://www.zhipin.com/job_detail/test.html'});
  assert.equal(result.ok,true);assert.equal(result.result.sent,true);assert.equal(h.clicks(),1);
});
test('delayed greeting receipt is polled without another click',async()=>{
  const h=harness({receiptAfter:2400,replaceButton:true});
  const result=await h.call({operation:'greet',url:'https://www.zhipin.com/job_detail/test.html'});
  assert.equal(result.result.sent,true);assert.equal(h.clicks(),1);assert.equal(h.elapsed(),2400);
});
test('greeting polling ends after eight seconds without accepting old receipt text',async()=>{
  const h=harness({receipt:false,oldReceipt:true});
  const result=await h.call({operation:'greet',url:'https://www.zhipin.com/job_detail/test.html'});
  assert.equal(result.result.sent,false);assert.equal(result.result.uncertain,true);
  assert.equal(h.clicks(),1);assert.equal(h.elapsed(),8000);
});
test('pause and expiration stop greeting receipt polling after the single click',async()=>{
  const paused=harness({receiptAfter:1600,lease:elapsed=>elapsed<800});
  const stopped=await paused.call({operation:'greet',url:'https://www.zhipin.com/job_detail/test.html'});
  assert.equal(stopped.ok,false);assert.match(stopped.error,/暂停/);assert.equal(paused.clicks(),1);
  const expired=harness({receiptAfter:1600});
  const stale=await expired.call({operation:'greet',url:'https://www.zhipin.com/job_detail/test.html',expires:Date.now()/1000+0.5});
  assert.equal(stale.ok,false);assert.match(stale.error,/过期/);assert.equal(expired.clicks(),1);
});
test('page verification or a different job stops receipt polling',async()=>{
  const moved=harness({receiptAfter:1600,onTimer:({location})=>{location.pathname='/job_detail/other.html';}});
  const changed=await moved.call({operation:'greet',url:'https://www.zhipin.com/job_detail/test.html'});
  assert.equal(changed.ok,false);assert.match(changed.error,/页面已切换/);assert.equal(moved.clicks(),1);
  const restricted=harness({receiptAfter:1600,onTimer:({body})=>{body.innerText='请完成安全验证';}});
  const blocked=await restricted.call({operation:'greet',url:'https://www.zhipin.com/job_detail/test.html'});
  assert.equal(blocked.ok,false);assert.match(blocked.error,/人工验证/);assert.equal(restricted.clicks(),1);
});
test('messages from other extensions are ignored',()=>{
  const h=harness();let called=false;h.listener({type:'job-command',operation:'greet'},{id:'foreign'},()=>called=true);
  assert.equal(called,false);assert.equal(h.clicks(),0);
});
test('old page-wide sent text cannot confirm a new greeting',async()=>{
  const h=harness({receipt:false,oldReceipt:true});
  const result=await h.call({operation:'greet',url:'https://www.zhipin.com/job_detail/test.html'});
  assert.equal(result.ok,true);assert.equal(result.result.sent,false);assert.equal(result.result.uncertain,true);
});

function chatHarness({latestText='多久可以到岗呢？',direction='item-friend',lease=true,receipt=true,emptyList=false}={}) {
  let listener,clicks=0;
  const node=(innerText,attrs={},className='')=>({innerText,className,getClientRects:()=>[{}],getAttribute:key=>attrs[key] || null,
    querySelector:()=>null});
  const session=node('测试HR',{'data-id':'hr1'},'active');
  session.querySelector=selector=>selector==='.name-box,.name-text'?node('测试HR'):null;
  const messages=[node(latestText,{'data-mid':'m1'},direction)];
  const input={...node(''),tagName:'DIV',textContent:'',dispatchEvent:()=>{}};
  const send={...node('发送'),click:()=>{clicks++;if(receipt)messages.push(node(input.textContent,{'data-mid':'m2'},'item-myself'));}};
  const context={URL,Event:class{},setTimeout:fn=>{fn();return 0;},
    location:{origin:'https://www.zhipin.com',href:'https://www.zhipin.com/web/geek/chat',pathname:'/web/geek/chat'},
    document:{title:'BOSS聊天',body:node('沟通'),scrollingElement:{scrollTop:0,scrollHeight:1000,clientHeight:800},
      querySelector:s=>s.includes('chat-input')?input:null,
      querySelectorAll:s=>s.includes('.user-list')?(emptyList?[]:[session]):s==='.message-item,.chat-message,.chat-conversation .message'?messages:s==='button,a,[role="button"]'?[send]:[]},
    chrome:{runtime:{id:'trusted',onMessage:{addListener:fn=>listener=fn},sendMessage:async()=>({valid:lease})}}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../apps/browser-extension/content.js'),'utf8'),context);
  const call=command=>new Promise(resolve=>listener({id:'request',expires:Date.now()/1000+10,type:'job-command',conversation:'data-id:hr1',message_key:'data-mid:m1',message_text:latestText,text:'两周后可以到岗。',...command},{id:'trusted'},resolve));
  return {call,clicks:()=>clicks,messages,node};
}
test('messages require stable ids and explicit incoming direction',async()=>{
  const h=chatHarness();const result=await h.call({operation:'messages'});
  assert.equal(result.result.conversation,'data-id:hr1');
  assert.equal(result.result.messages[0].key,'data-mid:m1');
  assert.equal(result.result.messages[0].direction,'incoming');
});
test('unrecognized chat list fails closed instead of reporting idle',async()=>{
  const h=chatHarness({emptyList:true});const result=await h.call({operation:'messages'});
  assert.equal(result.ok,false);
  assert.equal(result.error,'未能识别可核对的 BOSS 聊天列表，自动回复已暂停，请人工查看消息');
  assert.equal(h.clicks(),0);
});
test('reply requires a new outgoing bubble, not an old matching text',async()=>{
  const h=chatHarness({receipt:false});
  h.messages.unshift(h.node('两周后可以到岗。',{'data-mid':'old'},'item-myself'));
  const result=await h.call({operation:'reply'});
  assert.equal(result.ok,true);assert.equal(result.result.sent,false);assert.equal(h.clicks(),1);
});
test('successful reply is bound to latest incoming message and new receipt',async()=>{
  const h=chatHarness();const result=await h.call({operation:'reply'});
  assert.equal(result.ok,true);assert.equal(result.result.sent,true);assert.equal(h.clicks(),1);
});
test('wrong conversation and interview auto replies are definitely not sent',async()=>{
  const wrong=chatHarness();const mismatch=await wrong.call({operation:'reply',conversation:'data-id:other'});
  assert.equal(mismatch.result.not_done,true);assert.equal(wrong.clicks(),0);
  const interview=chatHarness({latestText:'周五过来聊聊，薪资能接受吗？'});
  const blocked=await interview.call({operation:'reply'});
  assert.equal(blocked.result.not_done,true);assert.equal(interview.clicks(),0);
});
test('unknown message direction and pause cannot send',async()=>{
  const unknown=chatHarness({direction:''});const response=await unknown.call({operation:'reply'});
  assert.equal(response.result.not_done,true);assert.equal(unknown.clicks(),0);
  const paused=chatHarness({lease:false});const blocked=await paused.call({operation:'reply'});
  assert.equal(blocked.ok,false);assert.equal(paused.clicks(),0);
});
test('resume receipt may be latest outgoing after requested upload',async()=>{
  const h=chatHarness({latestText:'请发一份附件简历'});
  h.messages.push(h.node('实习简历.pdf',{'data-mid':'file1'},'item-myself'));
  const result=await h.call({operation:'resume',resume_stage:'verify',filename:'实习简历.pdf',before_keys:['data-mid:m1']});
  assert.equal(result.ok,true);assert.equal(result.result.sent,true);
  const old=await h.call({operation:'resume',resume_stage:'verify',filename:'实习简历.pdf',before_keys:['data-mid:m1','data-mid:file1']});
  assert.equal(old.result.sent,false);
});

function navigationHarness(getTab) {
  let elapsed=0;
  const updates=[];
  const context={URL,setInterval:()=>0,setTimeout:(fn,ms)=>{elapsed+=ms;fn();return 0;},
    chrome:{storage:{local:{get:async()=>({}),set:async()=>{}}},
      tabs:{update:async(tabId,value)=>{updates.push({tabId,...value});},get:async()=>getTab(elapsed)},
      runtime:{onMessage:{addListener:()=>{}},onStartup:{addListener:()=>{}}},
      alarms:{onAlarm:{addListener:()=>{}}}}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../apps/browser-extension/background.js'),'utf8'),context);
  return {navigate:url=>context.navigate(1,url),updates,elapsed:()=>elapsed};
}
test('navigation waits beyond six seconds and accepts changed job tracking parameters',async()=>{
  const target='https://www.zhipin.com/job_detail/slow.html?source=list';
  const h=navigationHarness(elapsed=>({status:elapsed<8100?'loading':'complete',
    url:'https://www.zhipin.com/job_detail/slow.html?source=detail'}));
  await h.navigate(target);
  assert.equal(h.elapsed(),8100);assert.equal(h.updates.length,1);assert.equal(h.updates[0].url,target);
});
test('recommendation navigation preserves filters while normalizing parameter order',async()=>{
  const target='https://www.zhipin.com/web/geek/job?city=101120200&jobType=1901';
  const same=navigationHarness(()=>({status:'complete',url:'https://www.zhipin.com/web/geek/job?jobType=1901&city=101120200'}));
  await same.navigate(target);assert.equal(same.updates.length,1);
  for(const query of ['city=101010100&jobType=1901','city=101120200&jobType=1902']) {
    const changed=navigationHarness(()=>({status:'complete',url:'https://www.zhipin.com/web/geek/job?'+query}));
    await assert.rejects(changed.navigate(target),/页面未完成加载/);
    assert.equal(changed.elapsed(),12000);assert.equal(changed.updates.length,1);
  }
});
test('navigation never accepts a different origin or job path and never repeats navigation',async()=>{
  for(const url of ['https://other.example/job_detail/test.html','https://www.zhipin.com/job_detail/other.html']) {
    const h=navigationHarness(()=>({status:'complete',url}));
    await assert.rejects(h.navigate('https://www.zhipin.com/job_detail/test.html'),/页面未完成加载/);
    assert.equal(h.elapsed(),12000);assert.equal(h.updates.length,1);
  }
  const untrusted=navigationHarness(()=>({}));
  await assert.rejects(untrusted.navigate('https://other.example/job_detail/test.html'),/不在允许范围/);
  assert.equal(untrusted.updates.length,0);
});
