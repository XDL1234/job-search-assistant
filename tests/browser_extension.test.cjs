const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');

function harness({lease=true}={}) {
  let listener,clicks=0;
  const body={innerText:'推荐职位 嵌入式实习生'};
  const button={innerText:'立即沟通',getClientRects:()=>[{}],getAttribute:()=>null,click:()=>{clicks++;body.innerText='已发送消息';}};
  const job={innerText:'嵌入式实习生'},company={innerText:'测试科技'},tags={innerText:'实习 本科'};
  const link={href:'https://www.zhipin.com/job_detail/test.html',innerText:'嵌入式实习生'};
  const card={innerText:'嵌入式实习生 测试科技',getClientRects:()=>[{}],getAttribute:()=>null,querySelector:s=>s.includes('job_detail')?link:s.includes('job-name')?job:s.includes('company-name')?company:s.includes('tag-list')?tags:null};
  const context={URL,setTimeout,location:{origin:'https://www.zhipin.com',href:link.href,pathname:'/job_detail/test.html'},
    document:{title:'BOSS直聘',body,querySelector:s=>s==='.job-banner,.job-op,.job-detail'?{innerText:body.innerText}:null,
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
