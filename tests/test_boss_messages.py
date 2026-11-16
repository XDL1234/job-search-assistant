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
        with patch('job_assistant.browser_bridge.bridge_call',return_value=response):send_action(self.store,self.run,action)
        self.assertEqual(self.store.one('actions',action)['status'],'not_done')
        next_action=prepare()
        response={**response,'screenshot':base64.b64encode(data.getvalue()).decode(),'not_done':False}
        with patch('job_assistant.browser_bridge.bridge_call',return_value=response):
            with self.assertRaisesRegex(ValueError,'不明'):send_action(self.store,self.run,next_action)
        self.assertEqual(self.store.one('actions',next_action)['status'],'uncertain')
        with self.assertRaisesRegex(ValueError,'人工|重复'):prepare()


if __name__=='__main__':unittest.main()
