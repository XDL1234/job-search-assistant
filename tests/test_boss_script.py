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

    def test_city_scope_keeps_order_and_does_not_guess_unknown_locations(self):
        from job_assistant.boss_script import choose_card
        base={'job':'MCU实习','company':'A','contacted':False}
        cards=[{**base,'key':'other','city':'广州·番禺区'}, {**base,'key':'unknown','city':'全国'},
               {**base,'key':'local','city':'深圳市·南山区'}]
        self.assertEqual(choose_card(cards,{'cities':['深圳']})['key'],'local')
        self.assertIsNone(choose_card(cards[:2],{'cities':['深圳']}))
        self.assertEqual(choose_card(cards,{'cities':[]})['key'],'other')

    def test_interview_and_login_pages_stop_script(self):
        from job_assistant.boss_script import blocking_page
        for text in ['请完成安全验证','扫码登录BOSS直聘','面试邀请，周五有空吗']:
            self.assertIsNotNone(blocking_page(text))
        self.assertIsNone(blocking_page('推荐职位 嵌入式软件实习生 立即沟通'))

    def test_job_copy_is_not_a_platform_verification_prompt(self):
        from job_assistant.boss_script import blocking_page
        listing = 'MCU 实习\n负责滑块电机驱动、验证码模块和安全验证流程设计。\n立即沟通'
        self.assertIsNone(blocking_page(listing, include_interview=False))
        for text in ('请拖动滑块完成验证', '请输入图形验证码', '访问受限，请稍后再试',
                     '检测到异常访问，请完成安全验证', '系统检测到您的操作过于频繁，请稍后重试'):
            self.assertEqual(blocking_page(text), '平台要求验证或限制访问，请人工处理')

    def test_full_time_and_campus_do_not_take_internships(self):
        from job_assistant.boss_script import choose_card
        cards=[{'key':'intern','job':'MCU工程师','tags':'实习','company':'A','contacted':False},
               {'key':'full','job':'MCU工程师','tags':'社招','company':'B','contacted':False},
               {'key':'campus','job':'MCU工程师','tags':'2028届 校招','company':'C','contacted':False}]
        self.assertEqual(choose_card(cards,{'employment_type':'full_time','target_roles':['MCU']})['key'],'full')
        self.assertEqual(choose_card(cards,{'employment_type':'campus','target_roles':['MCU']})['key'],'campus')

    def test_platform_employment_filters_still_skip_explicitly_conflicting_cards(self):
        from job_assistant.boss_script import choose_card
        base={'job':'MCU工程师','company':'A','contacted':False}
        cards=[{**base,'key':'intern','tags':'2028届 实习'},
               {**base,'key':'part','tags':'兼职'}, {**base,'key':'eligible'}]
        config={'target_roles':['MCU'],'boss_filters':{'job_type':'全职','experience':'应届生'}}
        self.assertEqual(choose_card(cards,{**config,'employment_type':'campus'})['key'],'eligible')
        self.assertEqual(choose_card(cards,{**config,'employment_type':'full_time'})['key'],'eligible')
        self.assertEqual(choose_card([cards[0],cards[2]],{
            **config,'employment_type':'part_time','boss_filters':{'job_type':'兼职'}})['key'],'eligible')

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
                'filters':['青岛','实习'],'tab_id':'script-target','frame':{'path':'test.png'},**extra}

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

    def test_resume_reobserves_page_if_candidate_evidence_failed_before_checkpoint(self):
        pages = [self.page(), self.page('match'), ValueError('页面正在加载'), self.page('match')]
        with patch('job_assistant.boss_script.browser_action', side_effect=pages) as browser:
            self.step()
            with patch.object(self.store, 'evidence', side_effect=OSError('截图暂时不可用')):
                with self.assertRaisesRegex(OSError, '截图暂时不可用'):
                    self.step()
            self.assertEqual(json.loads(self.state_path.read_text(encoding='utf-8'))['phase'], 'next')
            with self.assertRaisesRegex(ValueError, '页面正在加载'):
                self.step()
            self.assertEqual(json.loads(self.state_path.read_text(encoding='utf-8'))['phase'], 'list')
            resumed = self.step()
        self.assertEqual([call.args[2] for call in browser.call_args_list], ['observe', 'next', 'observe', 'observe'])
        self.assertEqual(self.store.one('attempts', resumed['attempt_id'])['status'], 'in_progress')
        self.assertEqual(self.store.conn.execute("SELECT COUNT(*) FROM attempts WHERE status='skipped'").fetchone()[0], 1)
        self.assertEqual(self.store.conn.execute('SELECT COUNT(*) FROM actions').fetchone()[0], 0)

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

    def test_unknown_city_with_hard_scope_creates_no_attempt_or_action(self):
        self.config['cities']=['深圳']
        with patch('job_assistant.boss_script.browser_action',return_value=self.page('match')), \
             patch('job_assistant.boss_script.greet') as greet:
            self.assertFalse(self.step()['done'])
        greet.assert_not_called()
        self.assertEqual(self.store.conn.execute('SELECT COUNT(*) FROM attempts').fetchone()[0],0)
        self.assertEqual(self.store.conn.execute('SELECT COUNT(*) FROM actions').fetchone()[0],0)

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
        self.state_path.write_text(json.dumps({'phase':'return','target':'script-target','filters':['青岛','实习'],'visited_pages':[]}),encoding='utf-8')
        with patch('job_assistant.boss_script.browser_action',return_value={'tab_id':'script-target'}):
            self.assertFalse(self.step()['done'])
        state=json.loads(self.state_path.read_text(encoding='utf-8'))
        self.assertEqual(state['phase'],'list')
        self.assertEqual(state['filters'],['青岛','实习'])

    def test_attempt_from_another_run_is_skipped_after_reloading_list(self):
        previous = self.store.create_run(self.config, '测试跨轮去重')
        attempt = self.store.begin_attempt(previous,'测试公司','https://www.zhipin.com/job_detail/match.html',
                                           'MCU实习','boss','match')
        with patch('job_assistant.boss_script.browser_action',return_value=self.page('match')):
            self.assertFalse(self.step()['done'])
        self.assertEqual(self.store.conn.execute('SELECT COUNT(*) FROM attempts').fetchone()[0],1)
        self.assertEqual(self.store.one('attempts', attempt)['status'], 'in_progress')
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

    def test_empty_initial_filters_are_still_a_fixed_baseline(self):
        with patch('job_assistant.boss_script.browser_action',side_effect=[self.page(filters=[]),self.page('match')]):
            self.step()
            with self.assertRaisesRegex(ValueError,'筛选条件已变化'):self.step()
        self.assertEqual(self.store.conn.execute('SELECT COUNT(*) FROM attempts').fetchone()[0],0)

    def test_script_keeps_bound_target_across_next_step_and_rejects_rebind(self):
        with patch('job_assistant.boss_script.browser_action',side_effect=[self.page(),self.page('match',tab_id='other')]) as browser:
            self.step()
            with self.assertRaisesRegex(ValueError,'绑定标签'):self.step()
        self.assertEqual(browser.call_args.kwargs['expected_target'],'script-target')
        self.assertEqual(self.store.conn.execute('SELECT COUNT(*) FROM actions').fetchone()[0],0)

    def test_watching_never_reenters_script_submission(self):
        self.store.control(self.run_id,'watching')
        with patch('job_assistant.boss_script.browser_action') as browser:
            self.assertTrue(self.step()['done'])
        browser.assert_not_called()

    def test_script_refreshes_before_greet_and_passes_actual_observation(self):
        pages=[self.page('match'),{**self.page('match'),'source_card':self.page('match')['cards'][0]},
               self.page('match', url='https://www.zhipin.com/job_detail/match.html', heading='MCU实习',
                         detail_greet_state='ready', observation_id='current-facts'),self.page('match')]
        def sent(store,run,attempt,action,**params):
            with store.conn:store.conn.execute("UPDATE actions SET status='succeeded' WHERE id=?",(action,))
            return {'sent':True}
        with patch('job_assistant.boss_script.browser_action',side_effect=pages) as browser, patch('job_assistant.boss_script.greet',side_effect=sent) as greet:
            self.step();self.step();self.step()
        self.assertEqual([call.args[2] for call in browser.call_args_list],['observe','open_job','observe','return'])
        self.assertEqual(greet.call_args.kwargs,{'observation_id':'current-facts','expected_target':'script-target'})
        self.assertEqual(json.loads(self.state_path.read_text(encoding='utf-8'))['phase'],'list')

    def test_script_skips_known_closed_or_contacted_job_before_preparing(self):
        for state, label in [('unavailable','停止招聘'), ('contacted','已沟通')]:
            url = f'https://www.zhipin.com/job_detail/{state}.html'
            selected = self.page('match')
            selected['cards'][0].update(key=state, url=url)
            original = self.page('match', url=url, detail_greet_state=state)
            with patch('job_assistant.boss_script.browser_action', side_effect=[selected, selected, original, selected]) as browser:
                self.step(); self.step()
                self.assertIn(label, self.step()['message'])
                self.assertEqual(browser.call_args.args[2], 'return')
                script = json.loads(self.state_path.read_text(encoding='utf-8'))
                self.assertEqual(script['phase'],'list')
                self.assertEqual(self.store.one('attempts', script['attempt'])['status'],'skipped')
        self.assertEqual(self.store.conn.execute('SELECT COUNT(*) FROM actions').fetchone()[0], 0)

    def test_script_wrong_detail_or_ambiguous_action_does_not_record_a_send(self):
        selected = self.page('match')
        wrong = self.page('match', url='https://www.zhipin.com/job_detail/other.html', heading='MCU实习', detail_greet_state='ready')
        ambiguous = self.page('match', url='https://www.zhipin.com/job_detail/match.html', heading='MCU实习', detail_greet_state='unknown')
        with patch('job_assistant.boss_script.browser_action', side_effect=[selected, selected, wrong, ambiguous]):
            self.step(); self.step()
            with self.assertRaisesRegex(ValueError, '离开本轮目标岗位'): self.step()
            with self.assertRaisesRegex(ValueError, '唯一沟通按钮'): self.step()
        self.assertEqual(self.store.conn.execute('SELECT COUNT(*) FROM actions').fetchone()[0], 0)

    def test_recover_after_committed_send_before_return_never_resends(self):
        with patch('job_assistant.boss_script.browser_action', return_value=self.page('match')):
            self.step()
            self.step()
        state=json.loads(self.state_path.read_text(encoding='utf-8'))
        action=self.store.prepare_action(self.run_id,state['attempt'],'greet','match',{})
        with self.store.conn:
            self.store.conn.execute("UPDATE actions SET status='succeeded' WHERE id=?",(action,))
        with patch('job_assistant.boss_script.browser_action',return_value=self.page('match')) as browser, patch('job_assistant.boss_script.greet') as greet:
            self.assertFalse(self.step()['done'])
        self.assertEqual(browser.call_args.args[2],'return')
        greet.assert_not_called()

    def test_confirmed_greet_does_not_depend_on_default_workbook(self):
        from job_assistant.boss_script import greet
        from PIL import Image
        attempt = self.store.begin_attempt(self.run_id, '测试公司', 'https://www.zhipin.com/job_detail/match.html', 'MCU实习', 'boss', 'match')
        action = self.store.prepare_action(self.run_id, attempt, 'greet', 'match', {})
        frame = self.store.root / 'frames' / ('a' * 32 + '.png')
        frame.parent.mkdir()
        Image.new('RGB', (8, 8)).save(frame)
        observation = {**self.page('match'), 'sent': True, 'frame_file': str(frame)}
        def sent(*args, claim_greet, **kwargs):
            claim_greet()
            return observation
        with patch('job_assistant.agent_browser.check_current_source'), \
                patch('job_assistant.agent_browser.browser_call', side_effect=sent), \
                patch.object(self.store, 'evidence', wraps=type(self.store).evidence.__get__(self.store)), \
                patch.object(self.store, 'export', side_effect=PermissionError('workbook open')) as export:
            self.assertTrue(greet(self.store, self.run_id, attempt, action)['sent'])
        export.assert_not_called()
        self.assertEqual(self.store.one('actions', action)['status'], 'succeeded')

    def test_greet_distinguishes_no_dispatch_from_possible_delivery(self):
        from job_assistant.boss_script import greet
        attempt = self.store.begin_attempt(self.run_id, '测试公司', 'https://www.zhipin.com/job_detail/match.html', 'MCU实习', 'boss', 'match')
        for should_claim, expected in [(False,'not_done'), (True,'uncertain')]:
            action = self.store.prepare_action(self.run_id, attempt, 'greet', 'match', {})
            def failure(*args, claim_greet, check, **kwargs):
                check()
                if should_claim:
                    claim_greet()
                    check()
                raise ValueError('模拟失败')
            with patch('job_assistant.agent_browser.browser_call', side_effect=failure):
                with self.assertRaisesRegex(ValueError,'模拟失败'):
                    greet(self.store, self.run_id, attempt, action)
            self.assertEqual(self.store.one('actions', action)['status'], expected)

    def test_verified_empty_listing_can_finish(self):
        from job_assistant.app_runs import recover_app
        from job_assistant.boss_script import complete_script
        from job_assistant.migrations import migrate_app
        migrate_app(self.store)
        with self.store.conn:
            self.store.conn.execute('INSERT INTO app_sessions (run_id) VALUES (?)',(self.run_id,))
        with patch('job_assistant.boss_script.browser_action', return_value={**self.page(),'cards':[],'list_empty':True}):
            self.assertTrue(self.step()['done'])
        self.assertEqual(json.loads(self.state_path.read_text(encoding='utf-8'))['phase'],'done')
        self.assertEqual(complete_script(self.store,self.run_id),{'state':'completed'})
        recover_app(self.store)
        self.assertEqual(self.store.one('runs',self.run_id)['status'],'completed')

    def test_stale_registered_card_is_recorded_and_skipped_without_greet(self):
        with patch('job_assistant.boss_script.browser_action',side_effect=[self.page('match'),{
                **self.page('match'),'source_card_current':False}]), patch('job_assistant.boss_script.greet') as greet:
            self.step();result=self.step()
        greet.assert_not_called()
        self.assertIn('历史卡片',result['message'])
        self.assertEqual(self.store.conn.execute('SELECT status FROM attempts').fetchone()[0],'skipped')
        self.assertEqual(self.store.conn.execute('SELECT COUNT(*) FROM actions').fetchone()[0],0)
        state=json.loads(self.state_path.read_text(encoding='utf-8'))
        self.assertEqual(state['stale_keys'],['match'])
        self.assertEqual(state['phase'],'return')

    def test_generic_greet_cannot_bypass_historical_source_with_model_arguments(self):
        from job_assistant.boss_script import greet
        attempt=self.store.begin_attempt(self.run_id,'测试公司','https://www.zhipin.com/job_detail/match.html','MCU实习','boss','match')
        action=self.store.prepare_action(self.run_id,attempt,'greet','match',{})
        folder=self.store.root/'agent-browser-state';folder.mkdir()
        (folder/(self.run_id+'.json')).write_text(json.dumps({'source_card':{'key':'match'},'source_card_current':False}),encoding='utf-8')
        with patch('job_assistant.agent_browser.browser_call') as browser:
            with self.assertRaisesRegex(ValueError,'历史'):greet(self.store,self.run_id,attempt,action)
        browser.assert_not_called()
        self.assertEqual(self.store.one('actions',action)['status'],'not_done')

    def test_read_only_browser_entry_cannot_send_messages_or_attachments(self):
        from job_assistant.boss_script import browser_action
        with patch('job_assistant.agent_browser.browser_call') as bridge:
            for operation in ('greet','reply','resume'):
                with self.assertRaisesRegex(ValueError,'受控动作入口'):
                    browser_action(self.store,self.run_id,operation)
        bridge.assert_not_called()

if __name__=='__main__':unittest.main()
