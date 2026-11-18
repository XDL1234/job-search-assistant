"""消息策略、绑定、人工锁与回执边界；不连接真实 BOSS。"""
import base64
from contextlib import nullcontext
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'skills/job-search-assistant/scripts'))
from job_assistant.store import Store
from job_assistant.cli import prepare_config
from job_assistant.migrations import migrate_app
from job_assistant.boss_messages import simple_analysis, read_watch_state, watch_step, decide_message, send_action, finish_ai_watch


class BossMessagesTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=Store(self.temp.name);self.addCleanup(self.store.close)
        path=Path(self.temp.name)/'profile.json';path.write_text('{"name":"测试"}',encoding='utf-8')
        self.config=prepare_config({'channels':['boss'],'max_contacts':5,'boss_mode':'filtered','target_roles':['嵌入式'],
            'profile_path':str(path),'resume_path':str(path),'unknown_reply_mode':'human',
            'rules':[{'id':'arrival','category':'availability','reply':'可以在两周后到岗。','when':{}}]})
        self.run=self.store.create_run(self.config,'协议测试不发送');migrate_app(self.store)
        with self.store.conn:self.store.conn.execute('INSERT INTO app_sessions(run_id) VALUES (?)',(self.run,))
        self.attempt=self.store.begin_attempt(self.run,'测试公司','https://www.zhipin.com/job_detail/test.html','嵌入式实习','boss','/job_detail/test.html')
        self.addCleanup(patch.stopall)
        patch('job_assistant.desktop.notify',return_value={'requested':True}).start()
        patch('job_assistant.desktop.desktop_lock',side_effect=lambda _:nullcontext()).start()

    def observation(self,text='多久可以到岗呢？',key='data-mid:1'):
        return {'conversation':'data-id:hr1','messages':[{'key':key,'text':text,'direction':'incoming'}],
                'job_keys':['/job_detail/test.html'],'conversations':[],'frame':{'path':'unused'}}

    def prove_visible_job(self, attempt=None):
        from PIL import Image
        from job_assistant.boss_messages import remember_job_context
        attempt=attempt or self.attempt
        row=self.store.one('attempts',attempt)
        picture=self.store.root/'visible-proof.png';Image.new('RGB',(2,2)).save(picture)
        evidence=self.store.evidence(attempt,picture,'result')
        action=self.store.prepare_action(self.run,attempt,'greet',row['job_key'],{})
        self.store.claim_action(self.run,action);self.store.finish_action(action,'succeeded',evidence,'脱敏本地回执')
        folder=self.store.root/'agent-browser-state';folder.mkdir(exist_ok=True)
        card={'key':row['job_key'],'company':row['company'],'job':row['job'],'city':'深圳·南山区','salary':'150元/天'}
        (folder/(self.run+'.json')).write_text(json.dumps({'source_card':card}),encoding='utf-8')
        remember_job_context(self.store,self.run,row,evidence)

    def visible_observation(self,text=None,key='data-mid:visible-incoming',person='测试HR甲',system=False,**context_changes):
        conversation='visible-v1:'+json.dumps([person,'测试公司','招聘经理'],ensure_ascii=False,separators=(',',':'))
        context={'company':'测试公司','job':'嵌入式实习','city':'深圳','salary':'150元/天',**context_changes}
        messages=[{'key':'data-mid:opening','text':'用户预设开场白','direction':'outgoing','kind':'text'},
                  {'key':'data-mid:competition','text':'平台竞争者提示','direction':'system','kind':'system_card'}]
        if text is not None:messages.append({'key':key,'text':text,'direction':'system' if system else 'incoming','kind':'system_card' if system else 'text'})
        return {'conversation':conversation,'identity_source':'visible_identity','messages':messages,
                'job_keys':[],'job_context':context,'conversations':[],'frame':{'path':'unused'}}

    def test_parser_does_not_guess_salary_units_or_partial_questions(self):
        self.assertEqual(simple_analysis('薪资是税前5000元/月，你能接受吗？')['facts']['salary_monthly_gross'],5000)
        for text in ['薪资是5000，你能接受吗？','多久到岗，周五来面试吗？','地点在青岛，能接受吗？另外每周几天？']:
            result=simple_analysis(text)
            self.assertTrue(not result['complete'] or result['categories']==['interview'])

    def test_new_attempt_is_bound_and_template_is_prepared_once(self):
        self.assertEqual(self.store.conn.execute('SELECT count(*) FROM run_attempts').fetchone()[0],0)
        with patch('job_assistant.boss_script.browser_action',return_value=self.observation()) as browser:
            watch_step(self.store,self.run)
        self.assertEqual(browser.call_count,1)
        state=read_watch_state(self.store,self.run)
        self.assertEqual(state['conversations']['data-id:hr1']['attempt_id'],self.attempt)
        self.assertEqual(state['phase'],'respond')
        self.assertEqual(self.store.one('actions',state['action_id'])['status'],'pending')
        with self.assertRaisesRegex(ValueError,'重复'):decide_message(self.store,self.run,'data-id:hr1','data-mid:1')

    def test_interview_in_any_new_message_locks_whole_conversation(self):
        observation=self.observation('薪资是税前5000元/月，你能接受吗？',key='data-mid:2')
        observation['messages'].insert(0,{'key':'data-mid:1','text':'周五过来聊聊？','direction':'incoming'})
        with patch('job_assistant.boss_script.browser_action',return_value=observation):watch_step(self.store,self.run)
        self.assertEqual(self.store.one('conversations','data-id:hr1')['status'],'human')
        self.assertEqual(self.store.conn.execute('SELECT count(*) FROM actions').fetchone()[0],0)

    def test_unknown_ai_is_persisted_and_does_not_block_observation(self):
        self.config['unknown_reply_mode']='ai'
        with self.store.conn:self.store.conn.execute('UPDATE runs SET config=? WHERE id=?',(json.dumps(self.config),self.run))
        with patch('job_assistant.boss_script.browser_action',return_value=self.observation('请介绍你做的项目。')) as browser:
            first=watch_step(self.store,self.run);second=watch_step(self.store,self.run)
        self.assertTrue(first['notify']);self.assertFalse(second['notify']);self.assertEqual(browser.call_count,2)
        self.assertEqual(second['ai_required']['message_key'],'data-mid:1')
        self.assertEqual(self.store.one('conversations','data-id:hr1')['status'],'auto')
        self.assertEqual(self.store.conn.execute('SELECT count(*) FROM attention WHERE resolved=0').fetchone()[0],1)

    def test_unbound_conversation_never_sends(self):
        observation=self.observation();observation['job_keys']=['/job_detail/other.html']
        with patch('job_assistant.boss_script.browser_action',return_value=observation):watch_step(self.store,self.run)
        self.assertEqual(self.store.one('conversations','data-id:hr1')['status'],'human')
        self.assertEqual(self.store.conn.execute('SELECT count(*) FROM actions').fetchone()[0],0)

    def test_unfinished_ai_turn_hands_off_instead_of_retrying_model(self):
        self.config['unknown_reply_mode']='ai'
        with self.store.conn:self.store.conn.execute('UPDATE runs SET config=? WHERE id=?',(json.dumps(self.config),self.run))
        with patch('job_assistant.boss_script.browser_action',return_value=self.observation('请介绍项目。')):
            watch_step(self.store,self.run)
            finish_ai_watch(self.store,self.run)
            result=watch_step(self.store,self.run)
        self.assertNotIn('ai_required',result)
        self.assertEqual(self.store.one('conversations','data-id:hr1')['status'],'human')

    def test_send_requires_new_receipt_and_wrong_target_is_not_done(self):
        from PIL import Image
        data=io.BytesIO();Image.new('RGB',(2,2)).save(data,format='PNG')
        self.store.observe_message('hr','m1','多久可以到岗呢？')
        def prepare():return self.store.prepare_action(self.run,self.attempt,'reply','hr',{'message_key':'m1','text':'两周后'})
        action=prepare()
        response={'url':'https://www.zhipin.com/web/geek/chat','screenshot':base64.b64encode(data.getvalue()).decode(),'sent':False,'not_done':True,'reason':'会话未匹配，未输入发送'}
        with patch('job_assistant.agent_browser.browser_call',return_value=response):send_action(self.store,self.run,action)
        self.assertEqual(self.store.one('actions',action)['status'],'not_done')
        next_action=prepare()
        response={**response,'screenshot':base64.b64encode(data.getvalue()).decode(),'not_done':False}
        with patch('job_assistant.agent_browser.browser_call',return_value=response):
            with self.assertRaisesRegex(ValueError,'不明'):send_action(self.store,self.run,next_action)
        self.assertEqual(self.store.one('actions',next_action)['status'],'uncertain')
        with self.assertRaisesRegex(ValueError,'人工|重复'):prepare()

    def test_visible_identity_requires_proven_success_and_ignores_competition_card(self):
        self.prove_visible_job()
        observation=self.visible_observation()
        with patch('job_assistant.boss_script.browser_action',return_value=observation):watch_step(self.store,self.run)
        target=read_watch_state(self.store,self.run)['conversations'][observation['conversation']]
        self.assertEqual(target['attempt_id'],self.attempt)
        self.assertEqual(target['bound_attempt_id'],self.attempt)
        self.assertNotIn('message_key',target)
        self.assertEqual(self.store.conn.execute('SELECT COUNT(*) FROM messages').fetchone()[0],0)
        self.assertEqual(self.store.conn.execute("SELECT COUNT(*) FROM actions WHERE kind='reply'").fetchone()[0],0)

    def test_visible_identity_without_greet_proof_is_not_auto_associated(self):
        observation=self.visible_observation('多久可以到岗呢？')
        with patch('job_assistant.boss_script.browser_action',return_value=observation):watch_step(self.store,self.run)
        self.assertEqual(self.store.one('conversations',observation['conversation'])['status'],'human')
        self.assertEqual(self.store.conn.execute("SELECT COUNT(*) FROM actions WHERE kind='reply'").fetchone()[0],0)

    def test_visible_fixed_reply_passes_proven_job_context_to_last_browser_guard(self):
        from PIL import Image
        self.prove_visible_job();observation=self.visible_observation('多久可以到岗呢？')
        observation['job_keys']=['/job_detail/test.html']
        with patch('job_assistant.boss_script.browser_action',return_value=observation):watch_step(self.store,self.run)
        action=read_watch_state(self.store,self.run)['action_id']
        data=io.BytesIO();Image.new('RGB',(2,2)).save(data,format='PNG')
        response={'url':'https://www.zhipin.com/web/geek/chat','screenshot':base64.b64encode(data.getvalue()).decode(),'sent':True}
        with patch('job_assistant.agent_browser.browser_call',return_value=response) as browser:send_action(self.store,self.run,action)
        self.assertEqual(browser.call_args.kwargs['expected_job_context'],observation['job_context'])
        self.assertNotIn('expected_job_key',browser.call_args.kwargs)
        self.assertEqual(self.store.one('actions',action)['status'],'succeeded')

    def test_system_interview_card_locks_conversation_without_a_reply(self):
        self.prove_visible_job();observation=self.visible_observation('约你周五来面试',system=True)
        with patch('job_assistant.boss_script.browser_action',return_value=observation):watch_step(self.store,self.run)
        self.assertEqual(self.store.one('conversations',observation['conversation'])['status'],'human')
        self.assertEqual(self.store.conn.execute("SELECT COUNT(*) FROM actions WHERE kind='reply'").fetchone()[0],0)

    def test_job_banner_change_does_not_reuse_previous_attempt_binding(self):
        self.prove_visible_job();initial=self.visible_observation()
        with patch('job_assistant.boss_script.browser_action',return_value=initial):watch_step(self.store,self.run)
        changed=self.visible_observation('多久可以到岗呢？',salary='500元/天')
        with patch('job_assistant.boss_script.browser_action',return_value=changed):watch_step(self.store,self.run)
        target=read_watch_state(self.store,self.run)['conversations'][initial['conversation']]
        self.assertNotIn('attempt_id',target)
        self.assertEqual(target['bound_attempt_id'],self.attempt)
        self.assertEqual(self.store.one('conversations',initial['conversation'])['status'],'human')

    def test_multiple_proven_jobs_with_same_banner_are_not_guessed(self):
        self.prove_visible_job()
        other=self.store.begin_attempt(self.run,'测试公司','https://www.zhipin.com/job_detail/another.html','嵌入式实习','boss','/job_detail/another.html')
        self.prove_visible_job(other)
        observation=self.visible_observation('多久可以到岗呢？')
        with patch('job_assistant.boss_script.browser_action',return_value=observation):watch_step(self.store,self.run)
        self.assertEqual(self.store.one('conversations',observation['conversation'])['status'],'human')
        self.assertEqual(self.store.conn.execute("SELECT COUNT(*) FROM actions WHERE kind='reply'").fetchone()[0],0)

    def test_message_job_link_cannot_override_mismatched_visible_banner(self):
        self.prove_visible_job();observation=self.visible_observation('多久可以到岗呢？',salary='999元/天')
        observation['job_keys']=['/job_detail/test.html']
        with patch('job_assistant.boss_script.browser_action',return_value=observation):watch_step(self.store,self.run)
        self.assertEqual(self.store.one('conversations',observation['conversation'])['status'],'human')
        self.assertEqual(self.store.conn.execute("SELECT COUNT(*) FROM actions WHERE kind='reply'").fetchone()[0],0)

    def test_identity_drift_keeps_human_lock_across_run_and_historical_attach(self):
        self.prove_visible_job();original=self.visible_observation()
        with patch('job_assistant.boss_script.browser_action',return_value=original):watch_step(self.store,self.run)
        self.store.handoff(original['conversation'],'用户接管')
        next_run=self.store.create_run(self.config,'本地历史跟进授权')
        with self.store.conn:self.store.conn.execute('INSERT INTO app_sessions(run_id) VALUES (?)',(next_run,))
        self.store.attach_attempt(next_run,self.attempt)
        changed=self.visible_observation('多久可以到岗呢？',person='姓名已变更')
        with patch('job_assistant.boss_script.browser_action',return_value=changed):watch_step(self.store,next_run)
        self.assertEqual(self.store.one('conversations',original['conversation'])['status'],'human')
        self.assertEqual(self.store.one('conversations',changed['conversation'])['status'],'human')
        self.assertEqual(self.store.conn.execute("SELECT COUNT(*) FROM actions WHERE kind='reply'").fetchone()[0],0)

    def test_missing_proof_is_rechecked_before_sending_an_already_prepared_reply(self):
        self.prove_visible_job();observation=self.visible_observation('多久可以到岗呢？')
        with patch('job_assistant.boss_script.browser_action',return_value=observation):watch_step(self.store,self.run)
        action=read_watch_state(self.store,self.run)['action_id']
        proof=self.store.conn.execute("SELECT e.path FROM evidence e JOIN actions a ON a.evidence_id=e.id WHERE a.kind='greet'").fetchone()[0]
        Path(proof).write_bytes(b'corrupted local fixture')
        with patch('job_assistant.agent_browser.browser_call') as browser:
            with self.assertRaisesRegex(ValueError,'证据'):send_action(self.store,self.run,action)
        browser.assert_not_called()
        self.assertEqual(self.store.one('actions',action)['status'],'pending')

    def test_visible_interview_only_sends_after_explicit_manual_confirmation(self):
        from PIL import Image
        self.prove_visible_job();observation=self.visible_observation('周五来面试可以吗？')
        with patch('job_assistant.boss_script.browser_action',return_value=observation):watch_step(self.store,self.run)
        self.assertEqual(self.store.one('conversations',observation['conversation'])['status'],'human')
        with self.assertRaises(ValueError):
            self.store.prepare_manual_reply(self.run,self.attempt,observation['conversation'],'data-mid:visible-incoming','我确认后回复。',False)
        action=self.store.prepare_manual_reply(self.run,self.attempt,observation['conversation'],'data-mid:visible-incoming','我确认后回复。',True)
        image=io.BytesIO();Image.new('RGB',(2,2)).save(image,format='PNG')
        with patch('job_assistant.agent_browser.browser_call',return_value={'url':'https://www.zhipin.com/web/geek/chat',
                'screenshot':base64.b64encode(image.getvalue()).decode(),'sent':True}) as browser:
            send_action(self.store,self.run,action)
        self.assertTrue(browser.call_args.kwargs['manual'])
        self.assertEqual(self.store.one('conversations',observation['conversation'])['status'],'human')


if __name__=='__main__':unittest.main()
