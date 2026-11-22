"""原生浏览器适配的目标约束、命令安全、证据与模型输出。"""
from contextlib import nullcontext
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import socket
import uuid
import xml.etree.ElementTree as ET
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/job-search-assistant/scripts'))
from job_assistant.agent_browser import Browser, BrowserOriginError, PAGE_SCRIPT, _boss_url, compact_observation, facts_hash, inspect_page, read_config


class DetailExtractionTests(unittest.TestCase):
    """用脱敏 DOM 运行生产提取函数；不启动浏览器或访问网站。"""
    def extract(self, html):
        node=shutil.which('node')
        if not node:self.skipTest('DOM函数回归需要项目现有Node运行时')
        def serialize(element):
            children=[element.text or '']
            for child in element:children.extend([serialize(child),child.tail or ''])
            return {'tag':element.tag,'attrs':element.attrib,'children':children}
        tree=serialize(ET.fromstring(html))
        helper=PAGE_SCRIPT.split('  function jobDescription() {',1)[1].split('  function snapshot(',1)[0]
        script=r'''
const input=JSON.parse(require('fs').readFileSync(0,'utf8'));
function matches(node,selector) {
  return selector.split(',').some(option=>{
    const parts=option.trim().split(/\s+/);
    const part=(target,value)=>value.startsWith('.') ? (target.attrs.class||'').split(/\s+/).includes(value.slice(1)) : target.tag===value;
    if (!part(node,parts.pop())) return false;
    let ancestor=node.parentElement;
    while(parts.length) {
      const value=parts.pop();
      while(ancestor&&!part(ancestor,value))ancestor=ancestor.parentElement;
      if(!ancestor)return false;
      ancestor=ancestor.parentElement;
    }
    return true;
  });
}
function dom(value,parent=null) {
  if(typeof value==='string')return value;
  const node={...value,parentElement:parent};
  node.children=value.children.map(child=>dom(child,node));
  node.innerText=node.children.map(child=>typeof child==='string'?child:child.innerText).join('\n').trim();
  const descendants=()=>node.children.filter(child=>typeof child!=='string').flatMap(child=>[child,...child.descendants()]);
  node.descendants=descendants;
  node.querySelectorAll=selector=>descendants().filter(child=>matches(child,selector));
  node.querySelector=selector=>node.querySelectorAll(selector)[0]||null;
  node.closest=selector=>matches(node,selector)?node:parent?.closest(selector)||null;
  return node;
}
const document=dom(input.tree),text=node=>(node?.innerText||'').trim();
const all=selector=>document.querySelectorAll(selector),get=(root,selector)=>root?.querySelector(selector);
const shown=node=>!!node&&!('hidden' in node.attrs)&&!node.attrs.style?.includes('display:none');
const jobDescription=eval('(function jobDescription() {'+input.helper+')');
process.stdout.write(JSON.stringify(jobDescription().text));
'''
        completed=subprocess.run([node,'-e',script],input=json.dumps({'tree':tree,'helper':helper}),
            text=True,encoding='utf-8',capture_output=True,timeout=5)
        self.assertEqual(completed.returncode,0,completed.stderr)
        return json.loads(completed.stdout)

    def fixture(self):
        return (Path(__file__).parent/'fixtures/boss-job-detail.html').read_text(encoding='utf-8')

    def test_outer_dynamic_modules_do_not_change_complete_job_description(self):
        html=self.fixture()
        before=self.extract(html)
        after=self.extract(html.replace('甲城 乙城 丙城','丁城 戊城 己城').replace('当前活跃','刚刚活跃')
                           .replace('虚构公司的公司简介','发生变化的公司简介'))
        self.assertEqual(before,after)
        for value in ('职位描述','C++','计算机专业','MCU 驱动','FreeRTOS','职位要求','2027、2028 届优先','每周实习四天'):
            self.assertIn(value,before)
        for value in ('城市招聘','公司简介','HR','竞争力分析'):
            self.assertNotIn(value,before)
        self.assertEqual(facts_hash({'detail':before}),facts_hash({'detail':after}))

    def test_real_job_requirement_or_tag_change_still_invalidates_facts(self):
        html=self.fixture();before=self.extract(html)
        for after in (self.extract(html.replace('每周实习四天','每周实习五天')),
                      self.extract(html.replace('<li>C++</li>','<li>Rust</li>'))):
            self.assertNotEqual(facts_hash({'detail':before}),facts_hash({'detail':after}))

    def test_standalone_inner_node_keeps_all_text_without_outer_fallback(self):
        full='完整正文'+'长'*20001+'末尾真实要求'
        self.assertEqual(self.extract('<main><div class="job-sec-text">'+full+'</div><footer>动态页脚</footer></main>'),full)
        self.assertEqual(self.extract('<main class="job-detail"><footer>只有整页外壳</footer></main>'),'')


class BrowserBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.exe = self.root / 'agent browser.exe'
        self.exe.write_bytes(b'unit test placeholder')
        self.config = {'protocol':1, 'executable':str(self.exe), 'session':'job-test',
                       'connected':True, 'pinned_target':'target-1', 'connection_mode':'external'}
        self.write_config()

    def write_config(self):
        (self.root/'agent-browser.json').write_text(json.dumps(self.config), encoding='utf-8')

    def response(self, data, success=True):
        return subprocess.CompletedProcess([], 0, json.dumps({'success':success, 'data':data}), '')

    def test_configuration_rejects_disconnected_or_flag_like_session(self):
        for change in ({'connected':False}, {'session':'--auto-connect'}, {'pinned_target':''}, {'protocol':2}):
            self.write_config()
            value={**self.config, **change}
            (self.root/'agent-browser.json').write_text(json.dumps(value), encoding='utf-8')
            with self.assertRaises(ValueError):read_config(self.root)
        (self.root/'agent-browser.json').write_text('[]', encoding='utf-8')
        with self.assertRaises(ValueError):read_config(self.root)

    def test_urls_cannot_escape_authorized_origin(self):
        for url in ['http://www.zhipin.com/', 'https://www.zhipin.com.evil.test/',
                    'https://www.zhipin.com@evil.test/', 'https://u:p@www.zhipin.com/',
                    'https://www.zhipin.com:444/', 3]:
            with self.assertRaises(ValueError):_boss_url(url)
        self.assertEqual(_boss_url('https://www.zhipin.com/job_detail/a.html'), 'https://www.zhipin.com/job_detail/a.html')

    def test_fixed_command_array_and_embedded_environment(self):
        browser=Browser(self.root)
        dangerous='D:/resume path/$(echo secret); x.pdf'
        with patch('job_assistant.agent_browser.subprocess.run', return_value=self.response({})) as run:
            browser.command('upload', '#file', dangerous)
        args=run.call_args
        self.assertEqual(args.args[0], [str(self.exe), '--session', 'job-test', '--pin-tab', '--json', 'upload', '#file', dangerous])
        self.assertFalse(args.kwargs['shell'])
        self.assertEqual(args.kwargs['env']['JOB_ASSISTANT_EMBEDDED'], '1')
        self.assertEqual(args.kwargs['env']['JOB_ASSISTANT_EXISTING_DAEMON'], '1')

    def test_rebinding_or_pause_invalidates_already_created_client(self):
        browser=Browser(self.root)
        self.config['pinned_target']='target-2';self.write_config()
        with patch('job_assistant.agent_browser.subprocess.run') as run:
            with self.assertRaisesRegex(ValueError,'绑定已经变化'):browser.command('get','url')
        run.assert_not_called()
        with self.assertRaisesRegex(ValueError,'paused'):
            Browser(self.root, check=lambda: (_ for _ in ()).throw(ValueError('paused'))).command('get','url')

    def test_target_must_be_bound_and_active(self):
        browser=Browser(self.root)
        for target, active in [('target-2',True), ('target-1',False)]:
            with patch.object(browser,'command',return_value={'tabs':[{'targetId':target,'active':active,'url':'https://www.zhipin.com/'}]}):
                with self.assertRaises(ValueError):browser.verify_target()

    def test_old_run_target_cannot_navigate_newly_bound_tab(self):
        from job_assistant.agent_browser import browser_call
        with patch('job_assistant.agent_browser.subprocess.run') as run:
            with self.assertRaisesRegex(ValueError,'绑定标签'):
                browser_call(self.root,'return',uuid.uuid4().hex,expected_target='previous-target')
        run.assert_not_called()

    def test_return_allows_tracking_changes_but_requires_original_list_page(self):
        from job_assistant.agent_browser import browser_call
        from job_assistant.boss_filter_scope import list_scope
        run_id = uuid.uuid4().hex
        listing = 'https://www.zhipin.com/web/geek/jobs?city=101280600&page=2'
        detail = 'https://www.zhipin.com/job_detail/test.html'
        filters = ['深圳', '实习']
        state_folder = self.root / 'agent-browser-state'
        state_folder.mkdir()
        (state_folder / (run_id + '.json')).write_text(json.dumps({
            'target_id': 'target-1', 'navigate': detail, 'return_url': listing + '&ka=original&lid=first',
            'return_filters': filters, 'return_position': {'top': 30},
        }), encoding='utf-8')
        proof = {'target': 'target-1', 'scope': list_scope(listing), 'filters': filters}
        canonical = {'url': listing + '&lid=second', 'list_empty': True, 'filters': filters,
                     'position': {'top': 30, 'height': 800, 'viewport': 400}}
        wrong_page = {**canonical, 'url': listing.replace('page=2', 'page=3')}
        cases = [(detail, canonical, canonical, True), (canonical['url'], canonical, canonical, True),
                 (detail, wrong_page, wrong_page, False), (detail, canonical, wrong_page, False)]
        for current, observed, restored, succeeds in cases:
            with self.subTest(current=current, observed=observed['url'], restored=restored['url']):
                with patch.object(Browser, 'verify_target', return_value={'url': current}), \
                        patch.object(Browser, 'command') as command, \
                        patch.object(Browser, 'wait', side_effect=[observed, restored]), \
                        patch.object(Browser, 'capture', side_effect=lambda result: result) as capture:
                    if succeeds:
                        result = browser_call(self.root, 'return', run_id, filter_scope=proof)
                        self.assertEqual(result['url'], canonical['url'])
                    else:
                        with self.assertRaisesRegex(ValueError, '原筛选列表|原列表滚动位置'):
                            browser_call(self.root, 'return', run_id, filter_scope=proof)
                        capture.assert_not_called()
                if current == detail:
                    command.assert_called_once_with('back')
                else:
                    command.assert_not_called()

    def test_timeout_is_not_retried(self):
        with patch('job_assistant.agent_browser.subprocess.run',side_effect=subprocess.TimeoutExpired('tool',18)) as run:
            with self.assertRaisesRegex(ValueError,'不能重试'):Browser(self.root).command('click','@e1')
        self.assertEqual(run.call_count,1)

    def test_non_object_or_failed_json_is_rejected(self):
        for output in ['[]', '{"success":false,"error":"tab_gone"}', 'not-json']:
            completed=subprocess.CompletedProcess([],0,output,'')
            with patch('job_assistant.agent_browser.subprocess.run',return_value=completed):
                with self.assertRaises(ValueError):Browser(self.root).command('get','url')

    def test_page_values_are_json_encoded_not_executable(self):
        browser=Browser(self.root)
        value='"});fetch("https://evil.test");/*'
        with patch.object(browser,'verify_target'), patch.object(browser,'command',return_value={'result':{'ok':True}}) as run:
            browser.evaluate('open_job', key=value)
        script=run.call_args.kwargs['script']
        self.assertTrue(script.rstrip().endswith(')'))
        self.assertIn(json.dumps(value), script)

    def test_native_actions_only_use_fresh_marked_elements_and_literal_fill(self):
        browser=Browser(self.root)
        selector='[data-job-assistant-action="'+'a'*32+'"]'
        with patch.object(browser,'command') as run:
            with self.assertRaises(ValueError):browser.click({'click_selector':'button'})
            with self.assertRaises(ValueError):browser.fill({'input_selector':'textarea'},'text')
            run.assert_not_called()
            browser.fill({'input_selector':selector},'--json')
            self.assertEqual(run.call_args.args,('fill',selector,'--','--json'))

    def test_capture_rejects_same_url_business_change(self):
        browser=Browser(self.root)
        initial={'url':'https://www.zhipin.com/job_detail/a.html','heading':'MCU实习','detail':'原文'}
        with patch.object(browser,'verify_target'), patch.object(browser,'evaluate',return_value={**initial,'detail':'已变化'}), patch.object(browser,'command') as command:
            with self.assertRaisesRegex(ValueError,'截图前岗位'):browser.capture(initial)
        command.assert_not_called()
        with patch.object(browser,'verify_target'), patch.object(browser,'evaluate',side_effect=[initial,{**initial,'detail':'已变化'}]), patch.object(browser,'command'):
            with self.assertRaisesRegex(ValueError,'截图前后岗位'):browser.capture(initial)

    def test_capture_crops_only_stable_fully_visible_elements_after_viewport_shot(self):
        from PIL import Image
        browser = Browser(self.root)
        observation = {'url': 'https://www.zhipin.com/job_detail/a.html', 'heading': '合成岗位'}
        bounds = [10, 20, 40, 50, 100, 60]

        def screenshot(action, path):
            self.assertEqual(action, 'screenshot')
            canvas = Image.new('RGB', (200, 120), 'white')
            canvas.paste('red', (20, 40, 80, 100))
            canvas.save(path)

        for after, expected in ((bounds, 'element'), ([*bounds[:4], 110, 60], 'viewport'), (None, 'viewport')):
            with self.subTest(expected=expected), patch.object(browser, 'verify_target'), \
                    patch.object(browser, 'evaluate', side_effect=[
                        {**observation, 'capture_bounds': bounds}, {**observation, 'capture_bounds': after},
                    ]), patch.object(browser, 'command', side_effect=screenshot):
                captured = browser.capture(observation)
                self.assertEqual(captured['capture_region'], expected)
                with Image.open(captured['frame_file']) as image:
                    self.assertEqual(image.size, (60, 60) if expected == 'element' else (200, 120))
                    self.assertEqual(image.getpixel((0, 0)), (255, 0, 0) if expected == 'element' else (255, 255, 255))

    def test_inspection_without_run_only_reads_current_page_and_keeps_details_local(self):
        observed={'url':'https://www.zhipin.com/web/geek/chat','cards':[],
            'conversations':[{'key':'private-hr','label':'联系人原文'}],
            'messages':[{'key':'m1','text':'私人消息原文'}],
            'diagnostics':{'page_state':{'status':'ready','message':''}}}
        with patch.object(Browser,'inspect_context',return_value={}), patch.object(Browser,'verify_target'), patch.object(Browser,'evaluate',return_value=observed) as evaluate, patch.object(Browser,'command') as command:
            result=inspect_page(self.root,expected_target='target-1')
        evaluate.assert_called_once_with('observe',chat=True,diagnostic=True)
        command.assert_not_called()
        self.assertEqual(result['status'],'ready')
        self.assertEqual(result['counts'],{'cards':0,'conversations':1,'messages':1})
        self.assertNotIn('私人消息原文',json.dumps(result,ensure_ascii=False))
        self.assertNotIn('联系人原文',json.dumps(result,ensure_ascii=False))
        diagnostic=Path(result['diagnostic_path'])
        self.assertEqual(diagnostic.parent,self.root/'browser-diagnostics')
        self.assertEqual(json.loads(diagnostic.read_text(encoding='utf-8'))['observation'],observed)

    def test_inspection_does_not_navigate_to_requested_scope_or_accept_changed_target(self):
        with patch.object(Browser,'inspect_context',return_value={}), patch.object(Browser,'verify_target'), patch.object(Browser,'evaluate',return_value={
            'url':'https://www.zhipin.com/web/geek/chat','diagnostics':{'page_state':{'status':'ready'}}}) as evaluate:
            result=inspect_page(self.root,scope='jobs',expected_target='target-1')
        self.assertEqual(result['status'],'wrong_page')
        with patch.object(Browser,'command') as command:
            for params in ({'scope':'eval','expected_target':'target-1'}, {'expected_target':'old-target'}):
                with self.assertRaises(ValueError):inspect_page(self.root,**params)
        command.assert_not_called()

    def test_inspection_reports_verification_state_and_worker_needs_no_run(self):
        from job_assistant.app_service import handle_app_request
        from job_assistant.store import Store
        observed={'url':'https://www.zhipin.com/web/geek/job-recommend',
            'diagnostics':{'page_state':{'status':'manual_required','message':'平台要求人工验证'}}}
        with patch.object(Browser,'inspect_context',return_value={}), patch.object(Browser,'verify_target'), patch.object(Browser,'evaluate',return_value=observed):
            store=Store(self.root)
            try:
                response=handle_app_request(store,{'id':'inspect','method':'browser_inspect','params':{'expected_target':'target-1'}})
            finally:store.close()
        self.assertTrue(response['ok'])
        self.assertEqual(response['result']['status'],'manual_required')

    def test_origin_failure_saves_stage_and_host_without_private_url(self):
        private='https://other.example/private-contact?token=must-not-persist'
        context={'target_hostname':'other.example','runtime_hostname':'www.zhipin.com',
                 'document_hostname':'www.zhipin.com','top_hostname':'www.zhipin.com','is_top':True}
        with patch.object(Browser,'inspect_context',return_value=context), patch.object(Browser,'verify_target',
                side_effect=BrowserOriginError(private,'target_metadata')), patch.object(Browser,'evaluate') as evaluate:
            result=inspect_page(self.root,expected_target='target-1')
        evaluate.assert_not_called()
        self.assertEqual(result['status'],'inspection_error')
        self.assertEqual(result['stage'],'target_metadata')
        self.assertEqual(result['actual_hostname'],'other.example')
        saved=Path(result['diagnostic_path']).read_text(encoding='utf-8')
        self.assertIn('other.example',saved)
        self.assertNotIn('private-contact',saved)
        self.assertNotIn('must-not-persist',saved)

    def test_context_probe_only_reports_location_hosts_and_top_frame_identity(self):
        browser=Browser(self.root)
        with patch.object(browser,'_bound_tab',return_value={'url':'https://metadata.example/private?secret=1'}), patch.object(browser,'command',return_value={
                'origin':'https://runtime.example/another-private-path','result':{'hostname':'www.zhipin.com','protocol':'https:',
                'is_top':False,'top_hostname':'(cross-origin)'}}) as command:
            result=browser.inspect_context()
        self.assertEqual(result['target_hostname'],'metadata.example')
        self.assertEqual(result['runtime_hostname'],'runtime.example')
        self.assertFalse(result['is_top'])
        self.assertNotIn('private',json.dumps(result))
        self.assertNotIn('location.href',command.call_args.kwargs['script'])

    def test_registered_candidate_keeps_dom_proof_and_refuses_other_scope_or_tampering(self):
        from PIL import Image
        from job_assistant.agent_browser import _register_candidates, _registered_candidate
        run=uuid.uuid4().hex
        (self.root/'agent-browser-state').mkdir()
        frame=self.root/'frames'/(uuid.uuid4().hex+'.png');frame.parent.mkdir();Image.new('RGB',(2,2)).save(frame)
        card={'key':'/job_detail/one.html','url':'https://www.zhipin.com/job_detail/one.html','job':'MCU实习','company':'虚构公司','salary':'150元/天','city':'深圳','contacted':False}
        listing={'url':'https://www.zhipin.com/shenzhen/','filters':['城市页面路径 /shenzhen/'],
                 'position':{'top':0,'height':900,'viewport':700},'cards':[card],'frame_file':str(frame)}
        _register_candidates(self.root,run,'target-1',listing)
        current={**listing,'cards':[]}
        selected=_registered_candidate(self.root,run,'target-1',card['key'],current)
        self.assertEqual(selected['source_card'],card)
        self.assertFalse(selected['source_card_current'])
        for target,key,page in [('other',card['key'],current),('target-1','/job_detail/invented.html',current),
                ('target-1',card['key'],{**current,'filters':['其他城市']}),
                ('target-1',card['key'],{**current,'url':'https://www.zhipin.com/other/'})]:
            with self.assertRaises(ValueError):_registered_candidate(self.root,run,target,key,page)
        evidence=next((self.root/'candidate-evidence'/run).glob('*.png'));evidence.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'截图证据'):_registered_candidate(self.root,run,'target-1',card['key'],current)

    def test_resume_revalidates_chat_and_original_input_before_upload(self):
        from job_assistant.agent_browser import browser_call
        resume=self.root/'resume.pdf';resume.write_bytes(b'%PDF fixture')
        prepared={'selector':'#verified-input','before_keys':['m1']}
        params={'conversation':'hr1','message_key':'m1','message_text':'请发我一份简历',
                'manual':False,'resume_path':str(resume)}
        for final in [ValueError('会话或最新原文已变化'),{'selector':'#replaced-input'}]:
            with patch.object(Browser,'evaluate',side_effect=[prepared,final]) as evaluate, patch.object(Browser,'command') as command:
                with self.assertRaises(ValueError):browser_call(self.root,'resume',uuid.uuid4().hex,**params)
            self.assertEqual([call.args[0] for call in evaluate.call_args_list],['prepare_resume','verify_resume'])
            command.assert_not_called()


class ProjectionAndEvidenceTests(unittest.TestCase):
    def test_resume_requires_actual_request_not_only_model_claim(self):
        from job_assistant.agent_browser import action_check
        from job_assistant.store import Store
        with tempfile.TemporaryDirectory() as folder:
            store=Store(folder)
            try:
                config={'channels':['boss'],'max_contacts':2,'resume_path':'fixed.pdf','resume_sha256':'version'}
                run=store.create_run(config,'仅本地附件边界')
                attempt=store.begin_attempt(run,'本地公司','https://www.zhipin.com/job_detail/x.html','MCU','boss','x')
                store.observe_message('hr','m1','多久可以到岗呢？')
                action=store.prepare_action(run,attempt,'resume','hr',{'message_key':'m1','requested':True,'resume_sha256':'version'})
                store.claim_action(run,action)
                params={'action_id':action,'conversation':'hr','message_key':'m1','message_text':'多久可以到岗呢？','resume_path':'fixed.pdf','manual':False}
                check=action_check(store,run,'resume',params)
                with patch('job_assistant.cli.verify_materials',return_value=config):
                    with self.assertRaisesRegex(ValueError,'明确索要'):check()
                    params['message_text']='不用发简历'
                    with store.conn:store.conn.execute('UPDATE messages SET text=? WHERE message_key=?',(params['message_text'],'m1'))
                    with self.assertRaisesRegex(ValueError,'明确索要'):check()
                    params['message_text']='请发我一份简历'
                    with store.conn:store.conn.execute('UPDATE messages SET text=? WHERE message_key=?',(params['message_text'],'m1'))
                    check()
            finally:store.close()

    def test_consumed_action_is_rechecked_for_pause_message_change_and_human_lock(self):
        from job_assistant.agent_browser import action_check
        from job_assistant.store import Store
        with tempfile.TemporaryDirectory() as folder:
            store=Store(folder)
            try:
                config={'channels':['boss'],'max_contacts':2}
                run=store.create_run(config,'仅本地动作检查')
                attempt=store.begin_attempt(run,'本地公司','https://www.zhipin.com/job_detail/x.html','MCU','boss','x')
                store.observe_message('hr','m1','多久可以到岗呢？')
                action=store.prepare_action(run,attempt,'reply','hr',{'message_key':'m1','text':'两周后'})
                store.claim_action(run,action)
                params={'action_id':action,'conversation':'hr','message_key':'m1','message_text':'多久可以到岗呢？','text':'两周后','manual':False}
                check=action_check(store,run,'reply',params)
                with patch('job_assistant.cli.verify_materials',return_value=config):
                    check()
                    store.control(run,'paused')
                    with self.assertRaisesRegex(ValueError,'暂停'):check()
                    store.control(run,'running')
                    store.handoff('hr','interview')
                    with self.assertRaisesRegex(ValueError,'人工锁'):check()
                    with store.conn:store.conn.execute("UPDATE conversations SET status='auto' WHERE id='hr'")
                    store.observe_message('hr','m2','新的问题')
                    with self.assertRaisesRegex(ValueError,'消息'):check()
            finally:store.close()

    def test_facts_include_salary_location_and_message_context_not_media(self):
        original={'url':'https://www.zhipin.com/','salary':'100元/天','location':'深圳','messages':[{'text':'问题'}]}
        same={**original,'text':'广告变化','frame_file':'another.png','position':{'top':300}}
        self.assertEqual(facts_hash(original),facts_hash(same))
        for key,value in [('salary','200元/天'),('location','北京'),('messages',[{'text':'新问题'}])]:
            self.assertNotEqual(facts_hash(original),facts_hash({**original,key:value}))

    def test_list_is_bounded_and_body_is_not_sent(self):
        data={'url':'https://www.zhipin.com/','cards':[{'key':str(i)} for i in range(30)],
              'text':'private whole-page body','detail':'','filters':['实习'],'frame':{'path':'local.png'}}
        first=compact_observation(data)
        self.assertEqual(len(first['cards']),8)
        self.assertEqual(first['next_offset'],8)
        self.assertEqual(first['cards_total'],30)
        self.assertNotIn('text',first)
        self.assertEqual(compact_observation(data,offset=8)['cards'][0]['key'],'8')
        for value in [-1,True,'0']:
            with self.assertRaises(ValueError):compact_observation(data,offset=value)

    def test_detail_and_receipt_do_not_repeat_list_or_chat_history(self):
        detail=compact_observation({'heading':'MCU实习','detail':'FreeRTOS','cards':[{'key':'x'}],'text':'repeated'})
        self.assertEqual(detail['detail'],'FreeRTOS')
        self.assertNotIn('cards',detail)
        receipt=compact_observation({'sent':True,'messages':[{'text':'history'}],'frame':{'path':'proof.png'}})
        self.assertEqual(receipt,{'sent':True,'evidence_frame':'proof.png'})

    def test_native_png_file_is_validated_and_reused(self):
        from PIL import Image
        from job_assistant.store import Store
        from job_assistant.boss_script import save_frame
        with tempfile.TemporaryDirectory() as folder:
            store=Store(folder)
            try:
                path=store.root/'frames'/('a'*32+'.png');path.parent.mkdir()
                Image.new('RGB',(3,3)).save(path)
                result={'frame_file':str(path),'url':'https://www.zhipin.com/','tab_id':'target'}
                save_frame(store,'run',result)
                self.assertEqual(result['frame']['path'],str(path))
                self.assertEqual(json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))['run_id'],'run')
                outside=store.root/'outside.png';Image.new('RGB',(3,3)).save(outside)
                with self.assertRaisesRegex(ValueError,'受管'):save_frame(store,'run',{'frame_file':str(outside)})
            finally:store.close()

    def test_browser_tool_omits_image_unless_requested(self):
        from PIL import Image
        from job_assistant.app_executor import executor_action
        class StoreStub:
            def check_run(self, run):return None
        store=StoreStub()
        with tempfile.TemporaryDirectory() as folder:
            store.root=Path(folder)
            path=store.root/'proof.png';Image.new('RGB',(2,2)).save(path)
            result={'cards':[{'key':'a'}],'frame':{'path':str(path)}}
            config={'channels':['boss'],'employment_type':'internship'}
            with patch('job_assistant.app_executor.app_session'), patch('job_assistant.app_executor.verify_materials',return_value=config), \
                 patch('job_assistant.desktop.desktop_lock',return_value=nullcontext()), patch('job_assistant.boss_script.browser_action',return_value=result):
                request={'operation':'browser','action':'observe'}
                ordinary=executor_action(store,'r',request)
                visual=executor_action(store,'r',{**request,'include_image':True})
            self.assertEqual([row['type'] for row in ordinary['contentItems']],['inputText'])
            self.assertEqual([row['type'] for row in visual['contentItems']],['inputText','inputImage'])


class NativeBrowserTests(unittest.TestCase):
    def test_real_local_browser_jobs_reply_upload_and_png(self):
        if os.environ.get('JOB_ASSISTANT_TEST_NATIVE_BROWSER') != '1':
            self.skipTest('仅显式运行隔离的原生浏览器验收')
        from job_assistant import agent_browser as adapter
        from PIL import Image
        executable=Path(os.environ.get('JOB_ASSISTANT_TEST_AGENT_BROWSER_EXE') or
                        Path(__file__).resolve().parents[1]/'.verification/runtime/agent-browser/agent-browser.exe')
        chrome=Path('C:/Program Files/Google/Chrome/Application/chrome.exe')
        if not executable.is_file() or not chrome.is_file():
            self.fail('显式原生验收缺少固定运行时或Chrome')
        listing='''<div class="city-label">测试城市</div><div class="job-card-wrapper"><a href="/job_detail/one.html"><span class="job-name">MCU实习</span></a><div class="company-name">本地测试公司</div><div class="tag-list">实习 FreeRTOS 滑块电机驱动、验证码模块和安全验证流程设计</div></div>'''
        home_listing=(Path(__file__).parent/'fixtures/boss-home-cards.html').read_text(encoding='utf-8')
        modern_chat=(Path(__file__).parent/'fixtures/boss-chat-visible-identity.html').read_text(encoding='utf-8')
        search_listing=(Path(__file__).parent/'fixtures/boss-search-city.html').read_text(encoding='utf-8')
        refreshed_listing=listing.replace('/one.html','/fresh.html').replace('MCU实习','MCU新实习')
        detail='''<main><div class="job-banner"><div class="name"><h1>MCU实习</h1></div><span class="salary">150元/天</span><div class="job-op"><button onclick="this.textContent='继续沟通'; document.querySelector('#other-banner').hidden=false; document.querySelector('#subscription').hidden=false">立即沟通</button><button id="other-banner" hidden>继续沟通</button></div></div><div class="company-info"><a>本地测试公司</a></div><div class="job-detail"><section class="job-detail-section"><h3>职位描述</h3><div class="job-sec-text">MCU FreeRTOS，负责滑块电机驱动、验证码模块和安全验证流程设计。仅本地测试。</div></section><footer>动态城市链接</footer></div><div id="subscription" hidden><p>订阅回复消息</p><p>加载中…</p></div></main>'''
        chat='''<ul class="user-list"><li role="listitem" data-id="hr1" class="active"><span class="name-box">本地HR</span></li></ul><div class="chat-content"><a href="/job_detail/one.html">岗位</a><div id="messages"><div class="message-item item-friend" data-mid="m1">多久可以到岗呢？</div></div><textarea class="chat-input"></textarea><button onclick="const d=document.createElement('div');d.className='message-item item-myself';d.dataset.mid='m2';d.textContent=document.querySelector('textarea').value;document.querySelector('#messages').append(d)">发送</button><input type="file" accept=".pdf" onchange="const d=document.createElement('div');d.className='message-item item-myself';d.dataset.mid='m4';d.textContent=this.files[0].name;document.querySelector('#messages').append(d)"></div>'''
        class Handler(BaseHTTPRequestHandler):
            dynamic_visits=0
            def handle(self):
                try:super().handle()
                except (BrokenPipeError,ConnectionResetError):pass
            def do_GET(self):
                body=home_listing if self.path.startswith(('/qingdao/','/shenzhen/')) else detail if self.path.startswith('/job_detail/') else chat if self.path.startswith('/web/geek/chat') else listing
                if self.path.startswith('/web/geek/chat') and 'modern=1' in self.path:body=modern_chat
                if self.path.startswith('/web/geek/jobs'):body=search_listing
                if self.path.startswith('/dynamic/'):
                    Handler.dynamic_visits+=1
                    body=listing if Handler.dynamic_visits==1 else refreshed_listing
                    body+='<script>addEventListener("pageshow",()=>{const count=Number(sessionStorage.getItem("local-dynamic-visits")||0)+1;sessionStorage.setItem("local-dynamic-visits",String(count));if(count>1)document.body.innerHTML='+json.dumps(refreshed_listing)+';});</script>'
                if 'resume=1' in self.path:body=body.replace('多久可以到岗呢？','请发我一份简历')
                data=('<!doctype html><meta charset="utf-8"><title>本地验收</title>'+body).encode()
                self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        origin='http://127.0.0.1:'+str(server.server_port)
        with socket.socket() as available:
            available.bind(('127.0.0.1',0));debug_port=available.getsockname()[1]
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            process=subprocess.Popen([str(chrome),'--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check',
                                      '--remote-debugging-port='+str(debug_port),'--user-data-dir='+str(root/'chrome'),origin+'/list'],
                                      stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
            session='domain-smoke-'+uuid.uuid4().hex[:10]
            env={**os.environ,'JOB_ASSISTANT_EMBEDDED':'1','JOB_ASSISTANT_STREAM_TOKEN':uuid.uuid4().hex+uuid.uuid4().hex}
            def command(*args):
                result=subprocess.run([str(executable),'--session',session,'--json',*args],capture_output=True,text=True,encoding='utf-8',timeout=40,env=env,creationflags=subprocess.CREATE_NO_WINDOW)
                if result.returncode:
                    self.fail('原生命令失败：'+result.stdout[:500]+result.stderr[:500])
                return json.loads(result.stdout)['data']
            try:
                probe=json.loads(subprocess.check_output([str(executable),'--job-assistant-probe'],text=True,encoding='utf-8'))
                self.assertTrue(probe['capabilities'].get('existing_daemon_only'),'固定运行时必须支持只连接已有daemon')
                missing=subprocess.run([str(executable),'--session',session,'--json','tab','list'],
                    capture_output=True,text=True,encoding='utf-8',timeout=8,
                    env={**env,'JOB_ASSISTANT_EXISTING_DAEMON':'1'},creationflags=subprocess.CREATE_NO_WINDOW)
                self.assertNotEqual(missing.returncode,0,'daemon未创建时不应自动启动浏览器')
                for _ in range(80):
                    try:
                        with urllib.request.urlopen('http://127.0.0.1:'+str(debug_port)+'/json/version',timeout=.5):break
                    except OSError:time.sleep(.1)
                # 仅对本测试的专属 Chrome 开第二页，覆盖非首标签的持久绑定。
                with urllib.request.urlopen(urllib.request.Request(
                    'http://127.0.0.1:'+str(debug_port)+'/json/new?'+origin+'/other',method='PUT'),timeout=3) as response:
                    json.load(response)
                command('connect',str(debug_port))
                tabs=command('tab','list')['tabs']
                target=tabs[-1]
                self.assertGreater(tabs.index(target),0)
                self.assertTrue(target['url'].startswith(origin))
                command('--pin-tab','tab',target['targetId'])
                active=next(row for row in command('tab','list')['tabs'] if row['active'])
                self.assertEqual(active['targetId'],target['targetId'])
                other=next(row for row in tabs if row['targetId']!=target['targetId'])
                with urllib.request.urlopen('http://127.0.0.1:'+str(debug_port)+'/json/activate/'+other['targetId'],timeout=3):pass
                config={'protocol':1,'executable':str(executable),'session':session,'connected':True,'pinned_target':target['targetId']}
                (root/'agent-browser.json').write_text(json.dumps(config),encoding='utf-8')
                def local_url(url,stage=None):
                    if not isinstance(url,str) or not url.startswith(origin+'/'):raise ValueError('本地验收越界')
                    return url
                with patch.dict(os.environ,env), patch.object(adapter,'ORIGIN',origin), patch.object(adapter,'_boss_url',side_effect=local_url):
                    run=uuid.uuid4().hex
                    self.assertEqual(adapter.Browser(root).verify_target()['targetId'],target['targetId'])
                    self.assertEqual(adapter.Browser(root).evaluate('observe')['cards'][0]['job'],'MCU实习')
                    inspected=adapter.inspect_page(root,expected_target=target['targetId'])
                    self.assertEqual(inspected['counts']['cards'],1)
                    diagnostic=json.loads(Path(inspected['diagnostic_path']).read_text(encoding='utf-8'))
                    self.assertGreater(diagnostic['observation']['diagnostics']['selector_counts']['a[href*="/job_detail/"]'],0)
                    self.assertEqual(diagnostic['context']['document_hostname'],'127.0.0.1')
                    self.assertTrue(diagnostic['context']['is_top'])
                    if adapter.Browser(root).command('eval','document.hidden')['result']:
                        command('--pin-tab','tab',target['targetId'])
                        self.assertTrue(adapter.Browser(root).command('eval','document.hidden')['result'],
                                        '重新绑定不得激活原来隐藏的标签')
                    # 隐藏标签的 DOM 已验证。截图验收只在隔离 Chrome 中显式切换活动标签。
                    with urllib.request.urlopen('http://127.0.0.1:'+str(debug_port)+'/json/activate/'+target['targetId'],timeout=3):pass
                    browser=adapter.Browser(root)
                    browser.command('open',origin+'/qingdao/')
                    homepage=adapter.browser_call(root,'observe',run)
                    self.assertEqual(len(homepage['cards']),3)
                    intern=homepage['cards'][0]
                    self.assertEqual({key:intern[key] for key in ('job','company','city','salary','recruiter')},
                        {'job':'嵌入式软件实习生','company':'虚构甲公司','city':'青岛·市南区','salary':'150-200元/天','recruiter':''})
                    self.assertIn('经验不限',intern['tags']);self.assertIn('本科',intern['tags'])
                    self.assertIn('城市页面路径 /qingdao/',homepage['filters'])
                    from job_assistant.boss_script import choose_card
                    self.assertEqual(choose_card(homepage['cards'],{'employment_type':'internship','target_roles':['嵌入式','MCU']})['key'],intern['key'])
                    browser.command('open',origin+'/shenzhen/')
                    another_city=adapter.browser_call(root,'observe',run)
                    self.assertNotEqual(homepage['filters'],another_city['filters'])
                    browser.command('open',origin+'/web/geek/jobs')
                    search=adapter.browser_call(root,'observe',run)
                    self.assertEqual(search['filters'],['深圳'])
                    self.assertEqual([card['city'] for card in search['cards']],['深圳·南山区·科技园','广州·海珠区',''])
                    self.assertEqual([card['company'] for card in search['cards']],['虚构深城公司','虚构广城公司','虚构未知公司'])
                    city_config={'employment_type':'internship','target_roles':['嵌入式'],'cities':['深圳']}
                    self.assertEqual(choose_card(search['cards'],city_config)['key'],'/job_detail/search-shenzhen.html')
                    self.assertIsNone(choose_card(search['cards'][1:],city_config))
                    browser.command('open',origin+'/dynamic/')
                    original_listing=adapter.browser_call(root,'observe',run)
                    self.assertEqual(original_listing['cards'][0]['key'],'/job_detail/one.html')
                    adapter.browser_call(root,'open_job',run,key='/job_detail/one.html')
                    refreshed=adapter.browser_call(root,'return',run)
                    self.assertEqual(refreshed['cards'][0]['key'],'/job_detail/fresh.html')
                    reopened=adapter.browser_call(root,'open_job',run,key='/job_detail/one.html')
                    self.assertFalse(reopened['source_card_current'])
                    self.assertEqual(reopened['source_card'],original_listing['cards'][0])
                    adapter.browser_call(root,'return',run)
                    with self.assertRaises(ValueError):adapter.browser_call(root,'open_job',run,key='/job_detail/invented.html')
                    browser.command('open',origin+'/list')
                    observed=adapter.browser_call(root,'observe',run)
                    self.assertEqual(observed['cards'][0]['job'],'MCU实习')
                    browser.command('eval','--stdin',script="document.body.insertAdjacentHTML('beforeend', '<div role=dialog hidden><h2>安全验证</h2><p>请拖动滑块完成验证</p></div>');true")
                    self.assertEqual(browser.evaluate('observe')['cards'][0]['job'],'MCU实习')
                    browser.command('eval','--stdin',script="document.querySelector('[role=dialog]').hidden=false;true")
                    with self.assertRaisesRegex(ValueError,'人工验证'):
                        browser.evaluate('observe')
                    blocked=adapter.inspect_page(root,scope='jobs',expected_target=target['targetId'])
                    self.assertEqual(blocked['status'],'manual_required')
                    browser.command('open',origin+'/list')
                    browser.command('eval','--stdin',script="document.body.insertAdjacentHTML('beforeend', '<input disabled placeholder=验证码>');true")
                    with self.assertRaisesRegex(ValueError,'人工验证'):
                        browser.evaluate('observe')
                    browser.command('open',origin+'/list')
                    with Image.open(observed['frame_file']) as screenshot:self.assertEqual(screenshot.format,'PNG')
                    opened=adapter.browser_call(root,'open_job',run,key='/job_detail/one.html')
                    self.assertIn('FreeRTOS',opened['detail'])
                    self.assertEqual(opened['salary'],'150元/天')
                    self.assertEqual(opened['capture_region'],'element')
                    self.assertEqual(opened['detail_greet_state'],'ready')
                    browser.command('eval','--stdin',script="document.querySelector('.job-op').insertAdjacentHTML('beforeend','<button id=temporary-greet>立即沟通</button>');true")
                    self.assertEqual(browser.evaluate('observe')['detail_greet_state'],'unknown')
                    with self.assertRaisesRegex(ValueError,'唯一确定'):
                        adapter.browser_call(root,'greet',run,url=origin+'/job_detail/one.html')
                    browser.command('eval','--stdin',script="(() => {document.querySelector('#temporary-greet').remove();const button=document.querySelector('.job-op button');button.disabled=true;button.textContent='职位已下架';return true})()")
                    unavailable=adapter.browser_call(root,'greet',run,url=origin+'/job_detail/one.html',observation_id=opened['observation_id'])
                    self.assertEqual(unavailable['detail_greet_state'],'unavailable')
                    self.assertTrue(unavailable['not_done'])
                    self.assertFalse(unavailable['sent'])
                    browser.command('eval','--stdin',script="(() => {const button=document.querySelector('.job-op button');button.disabled=false;button.textContent='立即沟通';return true})()")
                    marker=browser.evaluate('greet',url=origin+'/job_detail/one.html')['click_selector']
                    center=browser.command('eval','--stdin',script=f'''(() => {{
                        const box=document.querySelector({json.dumps(marker)}).getBoundingClientRect();
                        return [box.x+box.width/2,box.y+box.height/2];
                    }})()''')['result']
                    browser.command('eval','--stdin',script="document.querySelector('h1').textContent='同标签换了岗位';true")
                    self.assertFalse(browser.command('eval','--stdin',script=f'!!document.querySelector({json.dumps(marker)})')['result'])
                    # 原生坐标在同一文档更新前已取得，仍不得送到复用的按钮。
                    browser.command('mouse','move',*map(str,center))
                    browser.command('mouse','down','left')
                    browser.command('mouse','up','left')
                    self.assertFalse(browser.evaluate('observe')['detail_contacted'])
                    browser.command('eval','--stdin',script="document.querySelector('h1').textContent='MCU实习';true")
                    with self.assertRaisesRegex(ValueError,'旧判断'):
                        adapter.browser_call(root,'greet',run,url=origin+'/job_detail/one.html',observation_id='stale')
                    greeted=adapter.browser_call(root,'greet',run,url=origin+'/job_detail/one.html',
                                                  observation_id=opened['observation_id'],expected_target=target['targetId'])
                    self.assertTrue(greeted['sent'])
                    returned=adapter.browser_call(root,'return',run)
                    self.assertEqual(returned['cards'][0]['company'],'本地测试公司')
                    messages=adapter.browser_call(root,'messages',run)
                    self.assertEqual(messages['conversation'],'data-id:hr1')
                    params={'conversation':'data-id:hr1','message_key':'data-mid:m1','message_text':'多久可以到岗呢？','manual':False,'text':'本地测试回复'}
                    replied=adapter.browser_call(root,'reply',run,**params)
                    self.assertTrue(replied['sent'])
                    browser=adapter.Browser(root)
                    browser.command('open',origin+'/web/geek/chat?resume=1')
                    pdf=root/'resume test.pdf';pdf.write_bytes(b'%PDF-1.0\nlocal fixture')
                    uploaded=adapter.browser_call(root,'resume',run,conversation='data-id:hr1',message_key='data-mid:m1',
                        message_text='请发我一份简历',manual=False,resume_path=str(pdf))
                    self.assertTrue(uploaded['sent'])
                    original_evaluate=adapter.Browser.evaluate
                    # 在最终校验之后切HR、增加新消息或克隆上传框，均不能再设置文件。
                    mutations=["document.querySelector('.user-list li').dataset.id='other-hr'; true",
                        "const message=document.createElement('div');message.className='message-item item-friend';message.dataset.mid='new-message';message.textContent='先不用发简历';document.querySelector('#messages').append(message);true",
                        "const input=document.querySelector('input[type=file]');input.replaceWith(input.cloneNode());true"]
                    for mutation in mutations:
                        browser.command('open',origin+'/web/geek/chat?resume=1')
                        def switch_after_verification(client,step,**params):
                            value=original_evaluate(client,step,**params)
                            if step=='verify_resume':client.command('eval','--stdin',script=mutation)
                            return value
                        with patch.object(adapter.Browser,'evaluate',new=switch_after_verification):
                            with self.assertRaises(ValueError):
                                adapter.browser_call(root,'resume',run,conversation='data-id:hr1',message_key='data-mid:m1',
                                    message_text='请发我一份简历',manual=False,resume_path=str(pdf))
                        remaining=browser.command('eval','--stdin',script="({files:document.querySelector('input[type=file]').files.length,outgoing:document.querySelectorAll('.item-myself').length})")['result']
                        self.assertEqual(remaining,{'files':0,'outgoing':0})
                    browser.command('open',origin+'/web/geek/chat?modern=1')
                    modern=adapter.browser_call(root,'messages',run)
                    conversation='visible-v1:'+json.dumps(['测试HR甲','虚构甲公司','招聘经理'],ensure_ascii=False,separators=(',',':'))
                    self.assertEqual(modern['conversation'],conversation)
                    self.assertEqual(modern['identity_source'],'visible_identity')
                    self.assertEqual([item['direction'] for item in modern['messages']],['outgoing','system'])
                    self.assertEqual([item['key'] for item in modern['messages']],['data-mid:local-outgoing-1','data-mid:local-system-1'])
                    self.assertEqual(modern['job_context'],{'company':'虚构甲公司','job':'嵌入式开发实习生','city':'深圳','salary':'150-200元/天'})
                    browser.command('eval','--stdin',script="localMessage('incoming','arrival','多久可以到岗呢？')")
                    modern_reply=adapter.browser_call(root,'reply',run,conversation=conversation,message_key='data-mid:arrival',
                        message_text='多久可以到岗呢？',manual=False,text='两周后可以到岗。',expected_job_context=modern['job_context'])
                    self.assertTrue(modern_reply['sent'])
                    browser.command('eval','--stdin',script="localMessage('incoming','resume-request','请发我一份简历')")
                    modern_upload=adapter.browser_call(root,'resume',run,conversation=conversation,message_key='data-mid:resume-request',
                        message_text='请发我一份简历',manual=False,resume_path=str(pdf),expected_job_context=modern['job_context'])
                    self.assertTrue(modern_upload['sent'])
                    browser.command('open',origin+'/web/geek/chat?modern=1')
                    browser.command('eval','--stdin',script="localMessage('incoming','request-drift','请发我一份简历')")
                    def drift_visible_identity(client,step,**params):
                        value=original_evaluate(client,step,**params)
                        if step=='verify_resume':client.command('eval','--stdin',script="document.querySelector('.friend-content.selected .name-text').textContent='另一位HR';document.querySelector('.base-info .name-text').textContent='另一位HR';true")
                        return value
                    with patch.object(adapter.Browser,'evaluate',new=drift_visible_identity):
                        with self.assertRaises(ValueError):
                            adapter.browser_call(root,'resume',run,conversation=conversation,message_key='data-mid:request-drift',
                                message_text='请发我一份简历',manual=False,resume_path=str(pdf),expected_job_context=modern['job_context'])
                    remaining=browser.command('eval','--stdin',script="({files:document.querySelector('#modern-resume').files.length,outgoing:document.querySelectorAll('.item-myself').length})")['result']
                    self.assertEqual(remaining,{'files':0,'outgoing':1})
                    browser.command('open',origin+'/web/geek/chat?modern=1')
                    browser.command('eval','--stdin',script="localMessage('incoming','arrival-drift','多久可以到岗呢？')")
                    def change_banner_after_fill(client,step,**params):
                        value=original_evaluate(client,step,**params)
                        if step=='fill_reply':
                            client.command('eval','--stdin',script="document.querySelector('.chat-position-content .salary').textContent='999元/天';true")
                        return value
                    with patch.object(adapter.Browser,'evaluate',new=change_banner_after_fill):
                        with self.assertRaisesRegex(ValueError,'岗位'):
                            adapter.browser_call(root,'reply',run,conversation=conversation,message_key='data-mid:arrival-drift',
                                message_text='多久可以到岗呢？',manual=False,text='应被阻止的回复',expected_job_context=modern['job_context'])
                    self.assertEqual(browser.command('eval','--stdin',script="document.querySelectorAll('.item-myself').length")['result'],1)
                    for drift in ("localMessage('incoming','after-approval','请先别回复');true",
                                  "document.querySelector('.friend-content.selected').classList.remove('selected');"
                                  "document.querySelectorAll('.friend-content')[1].classList.add('selected');"
                                  "document.querySelector('.base-info .name-text').textContent='测试HR乙';"
                                  "document.querySelector('.base-info > span:not(.base-title)').textContent='虚构乙公司';"
                                  "document.querySelector('.base-title').textContent='人事';true"):
                        browser.command('open',origin+'/web/geek/chat?modern=1')
                        browser.command('eval','--stdin',script="localMessage('incoming','arrival-race','多久可以到岗呢？')")
                        reply={'conversation':conversation,'message_key':'data-mid:arrival-race','message_text':'多久可以到岗呢？',
                               'manual':False,'text':'应被阻止的回复','expected_job_context':modern['job_context']}
                        browser.evaluate('fill_reply',**reply)
                        button=browser.evaluate('send_reply',**reply)['click_selector']
                        center=browser.command('eval','--stdin',script='''(() => {
                            const box=document.querySelector('.btn-send').getBoundingClientRect();
                            return [box.x+box.width/2,box.y+box.height/2];
                        })()''')['result']
                        browser.command('eval','--stdin',script=drift)
                        self.assertFalse(browser.command('eval','--stdin',script=f'!!document.querySelector({json.dumps(button)})')['result'])
                        # 模拟原生点击已读到坐标，浏览器随后仍在共享按钮上接收到鼠标事件。
                        browser.command('mouse','move',*map(str,center))
                        browser.command('mouse','down','left')
                        browser.command('mouse','up','left')
                        self.assertEqual(browser.command('eval','--stdin',script="document.querySelectorAll('.item-myself').length")['result'],1)
                    browser.command('open',origin+'/web/geek/chat?modern=1')
                    browser.command('eval','--stdin',script="localMessage('incoming','interview','周五来面试可以吗？')")
                    with self.assertRaisesRegex(ValueError,'面试'):
                        adapter.browser_call(root,'reply',run,conversation=conversation,message_key='data-mid:interview',
                            message_text='周五来面试可以吗？',manual=False,text='自动答复不应发送')
                    browser.command('eval','--stdin',script="document.querySelector('.base-title').textContent='另一职务';true")
                    self.assertEqual(browser.evaluate('observe',chat=True)['conversation'],'')
                    browser.command('open',origin+'/web/geek/chat?modern=1')
                    browser.command('eval','--stdin',script="const item=document.querySelector('.user-list li').cloneNode(true);item.querySelector('.friend-content').classList.remove('selected');document.querySelector('.user-list ul').append(item);true")
                    self.assertEqual(browser.evaluate('observe',chat=True)['conversation'],'')
            finally:
                try:command('close')
                finally:
                    # 只结束本测试创建的 Chrome 进程树；不影响用户浏览器。
                    if process.poll() is None:
                        subprocess.run(['taskkill.exe','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
                    try:process.wait(timeout=8)
                    except subprocess.TimeoutExpired:process.kill();process.wait(timeout=8)


if __name__ == '__main__':
    unittest.main()
