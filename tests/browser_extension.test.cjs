const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');

function harness({lease=true,receipt=true,oldReceipt=false}={}) {
  let listener,clicks=0;
  const body={innerText:oldReceipt?'推荐职位 旧会话已发送消息':'推荐职位 嵌入式实习生'};
  const detail={innerText:'立即沟通'};
  const button={innerText:'立即沟通',getClientRects:()=>[{}],getAttribute:()=>null,click:()=>{clicks++;body.innerText='已发送消息';if(receipt)detail.innerText='继续沟通';}};
  const job={innerText:'嵌入式实习生'},company={innerText:'测试科技'},tags={innerText:'实习 本科'};
  const link={href:'https://www.zhipin.com/job_detail/test.html',innerText:'嵌入式实习生'};
  const card={innerText:'嵌入式实习生 测试科技',getClientRects:()=>[{}],getAttribute:()=>null,querySelector:s=>s.includes('job_detail')?link:s.includes('job-name')?job:s.includes('company-name')?company:s.includes('tag-list')?tags:null};
  const context={URL,setTimeout,location:{origin:'https://www.zhipin.com',href:link.href,pathname:'/job_detail/test.html'},
    document:{title:'BOSS直聘',body,scrollingElement:{scrollTop:0,scrollHeight:1000,clientHeight:800},querySelector:s=>s==='.job-banner,.job-op,.job-detail'?detail:null,
      querySelectorAll:s=>s==='.job-card-wrapper,.job-card-box,.card-area'?[card]:s==='button,a,[role="button"]'?[button]:[]},
    chrome:{runtime:{id:'trusted',onMessage:{addListener:fn=>listener=fn},sendMessage:async()=>({valid:lease})}}};
  vm.runInNewContext(fs.readFileSync(path.join(__dirname,'../apps/browser-extension/content.js'),'utf8'),context);
  const call=command=>new Promise(resolve=>listener({id:'request',expires:Date.now()/1000+10,type:'job-command',...command},{id:'trusted'},resolve));
  return {call,listener,clicks:()=>clicks};
}
test('observe uses stable job URLs and returns actual company/type',async()=>{
  const value=await harness().call({operation:'observe'});
  assert.equal(value.ok,true);assert.equal(value.result.cards[0].key,'/job_detail/test.html');
  assert.equal(value.result.cards[0].company,'测试科技');assert.match(value.result.cards[0].tags,/实习/);
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
test('messages from other extensions are ignored',()=>{
  const h=harness();let called=false;h.listener({type:'job-command',operation:'greet'},{id:'foreign'},()=>called=true);
  assert.equal(called,false);assert.equal(h.clicks(),0);
});
test('old page-wide sent text cannot confirm a new greeting',async()=>{
  const h=harness({receipt:false,oldReceipt:true});
  const result=await h.call({operation:'greet',url:'https://www.zhipin.com/job_detail/test.html'});
  assert.equal(result.ok,true);assert.equal(result.result.sent,false);assert.equal(result.result.uncertain,true);
});

function chatHarness({latestText='多久可以到岗呢？',direction='item-friend',lease=true,receipt=true}={}) {
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
      querySelectorAll:s=>s.includes('.user-list')?[session]:s==='.message-item,.chat-message,.chat-conversation .message'?messages:s==='button,a,[role="button"]'?[send]:[]},
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
