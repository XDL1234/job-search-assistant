"""快速模式的纯规则选择，不依赖模型，也不发送网络请求。"""
import sys
from contextlib import ExitStack, nullcontext
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'skills/job-search-assistant/scripts'))


class BossScriptTests(unittest.TestCase):
    def test_candidates_keep_platform_order_and_skip_contacted(self):
        from job_assistant.boss_script import choose_card
        cards=[{'key':'old','job':'嵌入式实习生','company':'A','contacted':True},
               {'key':'new','job':'嵌入式软件实习','company':'B','contacted':False},
               {'key':'next','job':'MCU实习','company':'C','contacted':False}]
        config={'employment_type':'internship','target_roles':['嵌入式','MCU'],'boss_mode':'filtered'}
        self.assertEqual(choose_card(cards,config)['key'],'new')
        self.assertEqual(choose_card(cards,config,{'new'})['key'],'next')

    def test_wrong_employment_and_unknown_cards_are_not_sent(self):
        from job_assistant.boss_script import choose_card
        cards=[{'key':'full','job':'嵌入式高级工程师','company':'A','contacted':False},
               {'key':'missing','job':'嵌入式实习','company':'','contacted':False}]
        self.assertIsNone(choose_card(cards,{'employment_type':'internship','target_roles':['嵌入式']}))

    def test_interview_and_login_pages_stop_script(self):
        from job_assistant.boss_script import blocking_page
        for text in ['请完成安全验证','扫码登录BOSS直聘','面试邀请，周五有空吗']:
            self.assertIsNotNone(blocking_page(text))
        self.assertIsNone(blocking_page('推荐职位 嵌入式软件实习生 立即沟通'))

    def test_full_time_and_campus_do_not_take_internships(self):
        from job_assistant.boss_script import choose_card
        cards=[{'key':'intern','job':'MCU工程师','tags':'实习','company':'A','contacted':False},
               {'key':'full','job':'MCU工程师','tags':'社招','company':'B','contacted':False},
               {'key':'campus','job':'MCU工程师','tags':'2028届 校招','company':'C','contacted':False}]
        self.assertEqual(choose_card(cards,{'employment_type':'full_time','target_roles':['MCU']})['key'],'full')
        self.assertEqual(choose_card(cards,{'employment_type':'campus','target_roles':['MCU']})['key'],'campus')

class BossPagingTests(unittest.TestCase):
    def setUp(self):
        from job_assistant.store import Store
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store=Store(self.temp.name)
        self.addCleanup(self.store.close)
        self.config={'channels':['boss'],'execution_mode':'script','max_contacts':2,
                     'employment_type':'internship','target_roles':['MCU']}
        self.run_id=self.store.create_run(self.config,'测试翻页，不连接浏览器')
        self.state_path=self.store.root/'script-state'/(self.run_id+'.json')
        self.stack=ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch('job_assistant.app_runs.app_session'))
        self.stack.enter_context(patch('job_assistant.cli.verify_materials',return_value=self.config))
        self.stack.enter_context(patch('job_assistant.desktop.desktop_lock',return_value=nullcontext()))
        self.stack.enter_context(patch.object(self.store,'evidence',return_value='test-evidence'))

    def page(self,key='unmatched',**extra):
        return {'cards':[{'key':key,'url':'https://www.zhipin.com/job_detail/'+key+'.html',
                          'job':'MCU实习' if key=='match' else '销售经理','company':'测试公司','contacted':False}],
                'filters':['青岛','实习'],'frame':{'path':'test.png'},**extra}

    def step(self):
        from job_assistant.boss_script import script_step
        return script_step(self.store,self.run_id)

    def test_next_page_uses_checkpoint_and_selects_match(self):
        with patch('job_assistant.boss_script.browser_action',side_effect=[self.page(),self.page('match')]) as browser:
            self.assertFalse(self.step()['done'])
            self.assertEqual(json.loads(self.state_path.read_text(encoding='utf-8'))['phase'],'next')
            selected=self.step()
        self.assertFalse(selected['done'])
        self.assertIn('attempt_id',selected)
        self.assertEqual([call.args[2] for call in browser.call_args_list],['observe','next'])
        state=json.loads(self.state_path.read_text(encoding='utf-8'))
        self.assertEqual(state['phase'],'open')
        self.assertEqual(state['visited_pages'],[])

    def test_repeated_or_cycling_pages_stop_and_remain_stopped(self):
        pages=[self.page('a'),self.page('b'),self.page('a'),self.page('b'),self.page('a')]
        with patch('job_assistant.boss_script.browser_action',side_effect=pages) as browser:
            for _ in range(4):
                self.assertFalse(self.step()['done'])
            self.assertTrue(self.step()['done'])
            self.assertTrue(self.step()['done'])
        self.assertEqual(browser.call_count,5)
        self.assertEqual(self.store.conn.execute('SELECT COUNT(*) FROM actions').fetchone()[0],0)

    def test_scroll_position_progress_does_not_count_as_stalled_page(self):
        pages=[self.page(position={'top':top,'height':5000}) for top in (0,700,1400)]
        pages.extend([self.page(position={'top':1400,'height':5000})]*2)
        with patch('job_assistant.boss_script.browser_action',side_effect=pages):
            for _ in range(4):
                self.assertFalse(self.step()['done'])
            self.assertTrue(self.step()['done'])

    def test_filter_changes_stop_before_creating_attempt(self):
        with patch('job_assistant.boss_script.browser_action',side_effect=[self.page(),self.page('match',filters=['北京','实习'])]):
            self.step()
            with self.assertRaisesRegex(ValueError,'筛选条件已变化'):
                self.step()
        self.assertEqual(self.store.conn.execute('SELECT COUNT(*) FROM attempts').fetchone()[0],0)

    def test_list_without_recognized_cards_does_not_blindly_scroll(self):
        with patch('job_assistant.boss_script.browser_action',return_value=self.page(cards=[])) as browser:
            with self.assertRaisesRegex(ValueError,'未识别岗位卡片'):
                self.step()
        self.assertEqual(browser.call_count,1)
        self.assertFalse(self.state_path.exists())

    def test_scan_limit_is_persisted(self):
        self.state_path.parent.mkdir()
        self.state_path.write_text(json.dumps({'phase':'next','filters':['青岛','实习'],
                                               'visited_pages':[str(i) for i in range(63)]}),encoding='utf-8')
        with patch('job_assistant.boss_script.browser_action',return_value=self.page()) as browser:
            self.assertTrue(self.step()['done'])
        self.assertEqual(browser.call_args.args[2],'next')
        self.assertEqual(json.loads(self.state_path.read_text(encoding='utf-8'))['phase'],'done')

    def test_return_keeps_filter_scope(self):
        self.state_path.parent.mkdir()
        self.state_path.write_text(json.dumps({'phase':'return','filters':['青岛','实习'],'visited_pages':[]}),encoding='utf-8')
        with patch('job_assistant.boss_script.browser_action',return_value={}):
            self.assertFalse(self.step()['done'])
        state=json.loads(self.state_path.read_text(encoding='utf-8'))
        self.assertEqual(state['phase'],'list')
        self.assertEqual(state['filters'],['青岛','实习'])

    def test_existing_attempt_is_skipped_after_reloading_list(self):
        self.store.begin_attempt(self.run_id,'测试公司','https://www.zhipin.com/job_detail/match.html',
                                 'MCU实习','boss','match')
        with patch('job_assistant.boss_script.browser_action',return_value=self.page('match')):
            self.assertFalse(self.step()['done'])
        self.assertEqual(self.store.conn.execute('SELECT COUNT(*) FROM attempts').fetchone()[0],1)
        self.assertEqual(json.loads(self.state_path.read_text(encoding='utf-8'))['phase'],'next')

    def test_contact_limit_does_not_navigate_or_create_more_actions(self):
        for key in ('one','two'):
            attempt=self.store.begin_attempt(self.run_id,'测试公司','https://www.zhipin.com/job_detail/'+key+'.html',
                                             'MCU实习','boss',key)
            self.store.prepare_action(self.run_id,attempt,'greet',key,{})
        with patch('job_assistant.boss_script.browser_action') as browser:
            self.assertTrue(self.step()['done'])
        browser.assert_not_called()
        self.assertEqual(self.store.conn.execute('SELECT COUNT(*) FROM actions').fetchone()[0],2)

    def test_read_only_browser_entry_cannot_send_messages_or_attachments(self):
        from job_assistant.boss_script import browser_action
        with patch('job_assistant.browser_bridge.bridge_call') as bridge:
            for operation in ('greet','reply','resume'):
                with self.assertRaisesRegex(ValueError,'受控动作入口'):
                    browser_action(self.store,self.run_id,operation)
        bridge.assert_not_called()

if __name__=='__main__':unittest.main()
