"""真实候选注册、批量精筛、完整缓存键和发送前重新观察的领域验证。"""
from contextlib import ExitStack, nullcontext
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'skills/job-search-assistant/scripts'))
from job_assistant.ai_selection import selection_step, selection_context, accept_selection
from job_assistant.app_decisions import advance_control
from job_assistant.cli import prepare_config
from job_assistant.migrations import migrate_app
from job_assistant.store import Store


class AISelectionTests(unittest.TestCase):
    def setUp(self):
        from PIL import Image
        self.temporary=tempfile.TemporaryDirectory();self.addCleanup(self.temporary.cleanup)
        self.store=Store(self.temporary.name);self.addCleanup(self.store.close)
        self.profile=self.store.root/'profile.json'
        self.profile.write_text(json.dumps({'name':'测试','contact':{'phone':'13800000000','email':'private@example.com'},
            'skills':['MCU','FreeRTOS'],'resume_text':'测试\n13800000000 private@example.com\n项目：RTOS调度与驱动。'},ensure_ascii=False),encoding='utf-8')
        self.config=prepare_config({'channels':['boss'],'execution_mode':'ai','boss_mode':'screened','employment_type':'internship',
            'max_contacts':2,'target_roles':['嵌入式'],'profile_path':str(self.profile),'resume_path':str(self.profile),'simulation':False})
        migrate_app(self.store)
        self.run=self.new_run()
        self.cards=[{'key':'/job_detail/one.html','url':'https://www.zhipin.com/job_detail/one.html','job':'固件研发实习',
                     'company':'本地公司','city':'深圳','salary':'150元/天','tags':'实习','contacted':False}]
        self.details={self.cards[0]['key']:'MCU FreeRTOS，编写底层驱动。'}
        self.registered={}
        self.detail_overrides={}
        self.current=None
        self.frame=self.store.root/'frames'/(uuid.uuid4().hex+'.png');self.frame.parent.mkdir()
        Image.new('RGB',(3,3)).save(self.frame)
        self.stack=ExitStack();self.addCleanup(self.stack.close)
        self.browser=self.stack.enter_context(patch('job_assistant.ai_selection.browser_action',side_effect=self.browser_action))
        self.send=self.stack.enter_context(patch('job_assistant.ai_selection.greet',side_effect=self.sent))
        self.stack.enter_context(patch('job_assistant.ai_selection.desktop_lock',side_effect=lambda _:nullcontext()))

    def new_run(self, config=None):
        run=self.store.create_run(config or self.config,'本地精筛状态机测试，不连接招聘网站')
        with self.store.conn:self.store.conn.execute('INSERT INTO app_sessions(run_id) VALUES (?)',(run,))
        return run

    def browser_action(self, store, run, operation, **params):
        base={'url':'https://www.zhipin.com/web/geek/job-recommend','tab_id':'target-1',
              'frame':{'path':str(self.frame),'window':{'tab_id':'target-1'}},'filters':['深圳','实习'],
              'observation_id':'fresh-'+str(self.current)}
        if operation=='open_job':
            self.assertNotIn('url',params)
            self.current=params['key']
        if operation=='return':self.current=None
        if self.current:
            current=next((row for row in self.cards if row['key']==self.current),None)
            card=current or self.registered[self.current]
            return copy.deepcopy({**base,'url':card['url'],'cards':[],'heading':card['job'],'company':card['company'],
                 'detail':self.details[self.current],'salary':card['salary'],'location':card['city'],'detail_contacted':False,
                 'source_card':card,'content_ready':bool(self.details[self.current]),
                 **self.detail_overrides.get(self.current,{}),
                 **({'source_card_current':current is not None} if operation=='open_job' else {})})
        self.registered.update({card['key']:copy.deepcopy(card) for card in self.cards})
        return copy.deepcopy({**base,'cards':self.cards,'position':{'top':0,'height':500},'text':'原始页面全文不会进入模型'})

    def sent(self, store, run, attempt, action, observation_id=None, expected_target=None):
        evidence=store.evidence(attempt,self.frame,'result')
        store.finish_action(action,'succeeded',evidence,'本地回执验证')
        return {'sent':True}

    def pending(self, run=None, batch_size=2):
        for _ in range(40):
            result=selection_step(self.store,run or self.run,batch_size=batch_size)
            if result.get('ai_required'):return result['ai_required']
            if result['done']:return result
        self.fail('没有产生预期的有界候选判断')

    def decisions(self, pending, decision='accept'):
        return [{'key':row['key'],'decision':decision,'reason':'根据本地提供的真实事实判断'} for row in pending['candidates']]

    def count(self, table):
        return self.store.conn.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]

    def test_script_collects_actual_details_without_keyword_rejection_or_full_profile(self):
        pending=self.pending()
        self.assertEqual(pending['candidates'][0]['key'],self.cards[0]['key'])
        self.assertEqual(pending['candidates'][0]['detail']['detail'],self.details[self.cards[0]['key']])
        self.assertEqual(pending['platform_filters'],['深圳','实习'])
        encoded=json.dumps(pending,ensure_ascii=False)
        self.assertNotIn('private@example.com',encoded)
        self.assertNotIn('13800000000',encoded)
        self.assertNotIn('原始页面全文',encoded)
        self.assertNotIn('material_version',pending)
        self.send.assert_not_called()

    def test_city_scope_excludes_other_city_before_any_detail_navigation(self):
        base=self.cards[0]
        self.cards=[{**base,'key':'/job_detail/other.html','url':'https://www.zhipin.com/job_detail/other.html','city':'广州·番禺区'},
                    {**base,'key':'/job_detail/unknown.html','url':'https://www.zhipin.com/job_detail/unknown.html','city':'全国'},
                    {**base,'key':'/job_detail/local.html','url':'https://www.zhipin.com/job_detail/local.html','city':'深圳·南山区'}]
        self.details={card['key']:'MCU FreeRTOS完整详情' for card in self.cards}
        run=self.new_run({**self.config,'cities':['深圳']})
        pending=self.pending(run)
        self.assertEqual([item['key'] for item in pending['candidates']], [self.cards[1]['key'],self.cards[2]['key']])
        opened=[call.kwargs['key'] for call in self.browser.call_args_list if call.args[2]=='open_job']
        self.assertEqual(opened,[self.cards[1]['key'],self.cards[2]['key']])
        self.assertEqual(pending['requirements']['cities'],['深圳'])
        self.send.assert_not_called()

    def test_hard_city_scope_cannot_be_overridden_by_accepting_another_city(self):
        run=self.new_run({**self.config,'cities':['深圳']})
        self.cards[0]['city']='全国'
        self.detail_overrides[self.cards[0]['key']]={'location':'广州·天河区'}
        pending=self.pending(run)
        with self.assertRaisesRegex(ValueError,'城市'):
            accept_selection(self.store,run,pending['decision_id'],self.decisions(pending))
        self.assertEqual(self.count('actions'),0)
        self.assertEqual(self.count('app_ai_cache'),0)

    def test_reopened_city_change_is_skipped_without_another_ai_or_send(self):
        run=self.new_run({**self.config,'cities':['深圳']})
        pending=self.pending(run)
        accept_selection(self.store,run,pending['decision_id'],self.decisions(pending))
        self.cards[0]['city']='广州·番禺区'
        self.assertEqual(selection_step(self.store,run)['phase'],'execute_return')
        self.assertEqual(self.count('actions'),0)
        self.assertEqual(self.count('attempts'),0)
        self.send.assert_not_called()

    def test_detail_city_change_at_last_gate_never_creates_an_action(self):
        run=self.new_run({**self.config,'cities':['深圳']})
        pending=self.pending(run)
        accept_selection(self.store,run,pending['decision_id'],self.decisions(pending))
        self.assertEqual(selection_step(self.store,run)['phase'],'greet')
        self.detail_overrides[self.cards[0]['key']]={'location':'广州市·天河区'}
        self.assertEqual(selection_step(self.store,run)['phase'],'execute_return')
        self.assertEqual(self.count('actions'),0)
        self.assertEqual(self.count('attempts'),0)
        self.send.assert_not_called()

    def test_filtered_unknown_city_cannot_be_accepted_as_within_hard_scope(self):
        run=self.new_run({**self.config,'boss_mode':'filtered','cities':['深圳']})
        self.cards[0]['city']='全国'
        pending=selection_step(self.store,run)['ai_required']
        with self.assertRaisesRegex(ValueError,'城市'):
            accept_selection(self.store,run,pending['decision_id'],self.decisions(pending))
        self.assertEqual(self.count('actions'),0)
        self.send.assert_not_called()

    def test_only_registered_keys_and_exact_bounded_result_schema_are_accepted(self):
        pending=self.pending()
        malformed=[[],[{'key':'invented','decision':'accept','reason':'伪造'}],
            [{**self.decisions(pending)[0],'url':'https://evil.test'}],
            [{'key':[],'decision':'accept','reason':'bad type'}],
            [{'key':self.cards[0]['key'],'decision':'accept','reason':''}]]
        for result in malformed:
            with self.assertRaises(ValueError):accept_selection(self.store,self.run,pending['decision_id'],result)
        self.assertEqual(self.count('app_ai_cache'),0)
        self.assertEqual(self.count('actions'),0)

    def test_valid_result_is_consumed_once_and_scripts_do_all_browser_actions(self):
        pending=self.pending()
        accepted=accept_selection(self.store,self.run,pending['decision_id'],self.decisions(pending))
        self.assertEqual(accepted['accepted_count'],1)
        with self.assertRaises(ValueError):accept_selection(self.store,self.run,pending['decision_id'],self.decisions(pending))
        selection_step(self.store,self.run)
        self.send.assert_not_called()
        selection_step(self.store,self.run)
        self.assertEqual(self.send.call_count,1)
        self.assertIsNotNone(self.send.call_args.kwargs['observation_id'])
        self.assertEqual(self.store.conn.execute("SELECT count(*) FROM actions WHERE status='succeeded'").fetchone()[0],1)

    def test_more_than_one_batch_on_the_same_page_is_not_skipped(self):
        self.cards=[{**self.cards[0],'key':'/job_detail/'+str(index)+'.html',
                     'url':'https://www.zhipin.com/job_detail/'+str(index)+'.html'} for index in range(10)]
        self.details={card['key']:'MCU FreeRTOS驱动开发' for card in self.cards}
        first=self.pending()
        self.assertEqual(len(first['candidates']),2)
        accept_selection(self.store,self.run,first['decision_id'],self.decisions(first,'reject'))
        self.browser.reset_mock()
        selection_step(self.store,self.run,batch_size=2)
        self.assertEqual(self.browser.call_args.args[2],'observe')
        second=self.pending()
        self.assertEqual([row['key'] for row in second['candidates']],[card['key'] for card in self.cards[2:4]])
        self.assertNotIn('next',[call.args[2] for call in self.browser.call_args_list])

    def test_cap_one_reads_one_complete_detail_then_rejection_continues_same_page(self):
        self.cards=[{**self.cards[0],'key':'/job_detail/'+str(index)+'.html',
                     'url':'https://www.zhipin.com/job_detail/'+str(index)+'.html'} for index in range(10)]
        self.details={card['key']:'完整岗位正文'+('x'*6000) for card in self.cards}
        run=self.new_run({**self.config,'max_contacts':1})
        first=self.pending(run,batch_size=8)
        self.assertEqual(len(first['candidates']),1)
        self.assertEqual(first['candidates'][0]['detail']['detail'],self.details[self.cards[0]['key']])
        opened=[call.kwargs['key'] for call in self.browser.call_args_list if call.args[2]=='open_job']
        self.assertEqual(opened,[self.cards[0]['key']])
        accept_selection(self.store,run,first['decision_id'],self.decisions(first,'reject'))
        state=json.loads(self.store.conn.execute('SELECT state FROM app_ai_selection WHERE run_id=?',(run,)).fetchone()[0])
        self.assertEqual(state['seen'],[self.cards[0]['key']])
        self.browser.reset_mock()
        second=self.pending(run,batch_size=8)
        self.assertEqual([row['key'] for row in second['candidates']],[self.cards[1]['key']])
        self.assertEqual([call.args[2] for call in self.browser.call_args_list],['observe','open_job','return'])
        self.assertEqual(self.count('app_ai_cache'),1)
        self.send.assert_not_called()

    def test_new_batch_is_limited_by_remaining_contacts_after_prior_success(self):
        self.cards=[{**self.cards[0],'key':'/job_detail/'+str(index)+'.html',
                     'url':'https://www.zhipin.com/job_detail/'+str(index)+'.html'} for index in range(4)]
        self.details={card['key']:'MCU FreeRTOS完整详情' for card in self.cards}
        attempt=self.store.begin_attempt(self.run,'先前公司','https://www.zhipin.com/job_detail/prior.html','先前岗位','boss','prior')
        action=self.store.prepare_action(self.run,attempt,'greet','prior',{})
        self.sent(self.store,self.run,attempt,action)
        pending=self.pending(batch_size=8)
        self.assertEqual(len(pending['candidates']),1)
        self.assertEqual(sum(call.args[2]=='open_job' for call in self.browser.call_args_list),1)
        self.send.assert_not_called()

    def test_existing_approved_queue_stops_at_total_contact_cap(self):
        self.cards.append({**self.cards[0],'key':'/job_detail/two.html','url':'https://www.zhipin.com/job_detail/two.html'})
        self.details={card['key']:'MCU FreeRTOS完整详情' for card in self.cards}
        pending=self.pending(batch_size=8)
        accept_selection(self.store,self.run,pending['decision_id'],self.decisions(pending))
        attempt=self.store.begin_attempt(self.run,'先前公司','https://www.zhipin.com/job_detail/prior.html','先前岗位','boss','prior')
        action=self.store.prepare_action(self.run,attempt,'greet','prior',{})
        self.sent(self.store,self.run,attempt,action)
        self.assertEqual(selection_step(self.store,self.run)['phase'],'greet')
        self.assertEqual(selection_step(self.store,self.run)['phase'],'execute_return')
        self.browser.reset_mock()
        self.assertTrue(selection_step(self.store,self.run)['done'])
        self.browser.assert_not_called()
        self.assertEqual(self.send.call_count,1)
        self.assertEqual(self.store.conn.execute("SELECT COUNT(*) FROM actions WHERE status='succeeded'").fetchone()[0],2)

    def test_larger_contact_scope_still_collects_up_to_eight_complete_details(self):
        self.cards=[{**self.cards[0],'key':'/job_detail/'+str(index)+'.html',
                     'url':'https://www.zhipin.com/job_detail/'+str(index)+'.html'} for index in range(10)]
        self.details={card['key']:'MCU FreeRTOS完整详情' for card in self.cards}
        run=self.new_run({**self.config,'max_contacts':20})
        pending=self.pending(run,batch_size=8)
        self.assertEqual(len(pending['candidates']),8)
        self.assertEqual(sum(call.args[2]=='open_job' for call in self.browser.call_args_list),8)

    def test_ten_candidates_survive_return_reordering_and_missing_queued_keys(self):
        self.cards=[{**self.cards[0],'key':'/job_detail/'+str(index)+'.html',
                     'url':'https://www.zhipin.com/job_detail/'+str(index)+'.html'} for index in range(10)]
        original={card['key'] for card in self.cards}
        self.details={key:'MCU FreeRTOS完整详情' for key in original}
        decided=[]
        for _ in range(5):
            self.assertEqual(selection_step(self.store,self.run,batch_size=2)['phase'],'collect_open')
            state=json.loads(self.store.conn.execute('SELECT state FROM app_ai_selection WHERE run_id=?',(self.run,)).fetchone()[0])
            keys=[row['key'] for row in state['queue']]
            self.assertEqual(len(keys),2)
            self.assertEqual(selection_step(self.store,self.run)['phase'],'collect_return')
            # 返回时列表重新排序，下一张原卡离开DOM，但已由真实观察注册。
            self.cards=list(reversed([row for row in self.cards if row['key']!=keys[1]]))
            self.assertEqual(selection_step(self.store,self.run)['phase'],'collect_open')
            self.assertEqual(selection_step(self.store,self.run)['phase'],'collect_return')
            pending=selection_step(self.store,self.run)['ai_required']
            self.assertEqual([row['key'] for row in pending['candidates']],keys)
            self.assertEqual([row['card_current'] for row in pending['candidates']],[True,False])
            decided.extend(keys)
            accept_selection(self.store,self.run,pending['decision_id'],self.decisions(pending,'reject'))
        self.assertEqual(set(decided),original)
        self.assertEqual(len(decided),10)
        self.assertNotIn('next',[call.args[2] for call in self.browser.call_args_list])
        self.assertEqual(self.count('actions'),0)
        self.send.assert_not_called()

    def test_missing_accepted_card_uses_registered_key_and_new_detail_before_send(self):
        pending=self.pending()
        accept_selection(self.store,self.run,pending['decision_id'],self.decisions(pending))
        key=self.cards[0]['key']
        self.cards=[]
        self.details[key]='当前实际岗位详情，MCU FreeRTOS薪资已调整。'
        self.detail_overrides[key]={'salary':'220元/天','heading':'当前固件实习岗位'}
        self.assertEqual(selection_step(self.store,self.run)['phase'],'changed_return')
        self.assertEqual(self.count('attempts'),0)
        refreshed=selection_step(self.store,self.run)['ai_required']
        candidate=refreshed['candidates'][0]
        self.assertFalse(candidate['card_current'])
        self.assertEqual(candidate['card']['salary'],'150元/天')
        self.assertEqual(candidate['detail']['salary'],'220元/天')
        self.assertEqual(candidate['detail']['heading'],'当前固件实习岗位')
        self.assertNotEqual(refreshed['facts_hash'],pending['facts_hash'])
        self.send.assert_not_called()
        accept_selection(self.store,self.run,refreshed['decision_id'],self.decisions(refreshed))
        self.assertEqual(selection_step(self.store,self.run)['phase'],'greet')
        # 详情页observe不重写卡片来源；历史来源在最终发送门仍保持False。
        self.assertEqual(selection_step(self.store,self.run)['phase'],'execute_return')
        self.assertEqual(self.send.call_count,1)
        self.assertEqual(self.count('app_ai_cache'),2)
        opened=[call.kwargs for call in self.browser.call_args_list if call.args[2]=='open_job']
        self.assertTrue(all(row['key']==key and 'url' not in row for row in opened))

    def test_card_provenance_change_alone_requires_a_new_decision(self):
        pending=self.pending()
        accept_selection(self.store,self.run,pending['decision_id'],self.decisions(pending))
        self.cards=[]
        self.assertEqual(selection_step(self.store,self.run)['phase'],'changed_return')
        updated=selection_step(self.store,self.run)['ai_required']
        self.assertNotEqual(updated['facts_hash'],pending['facts_hash'])
        self.assertFalse(updated['candidates'][0]['card_current'])
        self.assertEqual(updated['candidates'][0]['detail'],pending['candidates'][0]['detail'])
        self.assertEqual(self.count('actions'),0)

    def test_filtered_history_is_recorded_instead_of_claimed_as_fresh_or_sent(self):
        run=self.new_run({**self.config,'boss_mode':'filtered'})
        pending=selection_step(self.store,run)['ai_required']
        accept_selection(self.store,run,pending['decision_id'],self.decisions(pending))
        self.cards=[]
        self.assertEqual(selection_step(self.store,run)['phase'],'execute_return')
        self.assertEqual(self.count('attention'),1)
        self.assertEqual(self.count('actions'),0)
        self.assertEqual(self.count('attempts'),0)
        self.send.assert_not_called()

    def test_pause_resume_rejects_old_result_before_cache_write(self):
        pending=self.pending()
        with self.store.conn:advance_control(self.store,self.run)
        self.store.control(self.run,'paused')
        self.store.control(self.run,'running')
        with self.assertRaisesRegex(ValueError,'失效'):
            accept_selection(self.store,self.run,pending['decision_id'],self.decisions(pending))
        self.assertEqual(self.count('app_ai_cache'),0)
        new=self.pending()
        self.assertNotEqual(new['decision_id'],pending['decision_id'])
        with self.assertRaises(ValueError):accept_selection(self.store,self.run,pending['decision_id'],self.decisions(pending))
        self.send.assert_not_called()

    def test_changed_material_or_registered_facts_reject_response(self):
        pending=self.pending()
        state=json.loads(self.store.conn.execute('SELECT state FROM app_ai_selection WHERE run_id=?',(self.run,)).fetchone()[0])
        state['pending']['candidates'][0]['card']['salary']='999元/天'
        with self.store.conn:self.store.conn.execute('UPDATE app_ai_selection SET state=? WHERE run_id=?',(json.dumps(state),self.run))
        with self.assertRaisesRegex(ValueError,'事实包'):
            accept_selection(self.store,self.run,pending['decision_id'],self.decisions(pending))
        self.assertEqual(self.count('app_ai_cache'),0)

    def test_incomplete_detail_cannot_be_accepted(self):
        self.details[self.cards[0]['key']]=''
        pending=self.pending()
        with self.assertRaisesRegex(ValueError,'不完整'):
            accept_selection(self.store,self.run,pending['decision_id'],self.decisions(pending))
        accept_selection(self.store,self.run,pending['decision_id'],self.decisions(pending,'uncertain'))
        self.assertEqual(self.store.conn.execute("SELECT COUNT(*) FROM attention WHERE resolved=0").fetchone()[0],1)
        self.send.assert_not_called()

    def test_same_facts_reuse_cache_but_city_salary_and_detail_changes_do_not(self):
        pending=self.pending()
        accept_selection(self.store,self.run,pending['decision_id'],self.decisions(pending,'reject'))
        second=self.new_run()
        result=self.pending(second)
        self.assertNotIn('ai_required',result)
        state=json.loads(self.store.conn.execute('SELECT state FROM app_ai_selection WHERE run_id=?',(second,)).fetchone()[0])
        self.assertEqual(state['cache_hits'],1)
        for key,value in [('salary','180元/天'),('city','青岛')]:
            self.cards[0][key]=value
            run=self.new_run()
            changed=self.pending(run)
            self.assertIn('decision_id',changed)
            accept_selection(self.store,run,changed['decision_id'],self.decisions(changed,'reject'))
        self.details[self.cards[0]['key']]='增加了此前没有的工作要求。'
        self.assertIn('decision_id',self.pending(self.new_run()))

    def test_salary_change_after_acceptance_forces_new_ai_before_attempt_or_send(self):
        pending=self.pending()
        accept_selection(self.store,self.run,pending['decision_id'],self.decisions(pending))
        self.cards[0]['salary']='200元/天'
        result=selection_step(self.store,self.run)
        self.assertEqual(result['phase'],'changed_return')
        self.assertEqual(self.count('attempts'),0)
        self.assertEqual(self.count('actions'),0)
        new=selection_step(self.store,self.run)['ai_required']
        self.assertEqual(new['candidates'][0]['card']['salary'],'200元/天')
        self.assertNotEqual(new['facts_hash'],pending['facts_hash'])
        self.send.assert_not_called()

    def test_same_url_detail_change_at_last_gate_does_not_create_action(self):
        pending=self.pending()
        accept_selection(self.store,self.run,pending['decision_id'],self.decisions(pending))
        self.assertEqual(selection_step(self.store,self.run)['phase'],'greet')
        self.details[self.cards[0]['key']]='岗位要求已经改变'
        self.assertEqual(selection_step(self.store,self.run)['phase'],'changed_return')
        self.assertEqual(self.count('actions'),0)
        self.assertEqual(self.count('attempts'),0)

    def test_platform_filtered_ai_does_not_read_detail_before_decision(self):
        config={**self.config,'boss_mode':'filtered'}
        run=self.new_run(config)
        pending=selection_step(self.store,run)['ai_required']
        self.assertNotIn('detail',pending['candidates'][0])
        self.assertEqual([call.args[2] for call in self.browser.call_args_list],['observe'])
        self.send.assert_not_called()

    def test_message_only_mode_never_scans_or_accepts_pending_job_decision(self):
        pending=self.pending()
        self.browser.reset_mock()
        self.store.control(self.run,'watching')
        with self.assertRaisesRegex(ValueError,'仅值守'):selection_step(self.store,self.run)
        with self.assertRaisesRegex(ValueError,'仅值守'):
            accept_selection(self.store,self.run,pending['decision_id'],self.decisions(pending))
        self.browser.assert_not_called()
        self.assertEqual(self.count('app_ai_cache'),0)

    def test_filtered_pause_resume_does_not_require_nonexistent_return_checkpoint(self):
        run=self.new_run({**self.config,'boss_mode':'filtered'})
        original=selection_step(self.store,run)['ai_required']
        self.browser.reset_mock()
        with self.store.conn:advance_control(self.store,run)
        new=selection_step(self.store,run)['ai_required']
        self.assertNotEqual(new['decision_id'],original['decision_id'])
        self.assertEqual([call.args[2] for call in self.browser.call_args_list],['observe'])

    def test_resume_rechecks_unclaimed_candidate_without_clearing_other_runs(self):
        from job_assistant.app_runs import control_run
        run=self.new_run({**self.config,'boss_mode':'filtered'})
        original=selection_step(self.store,run)['ai_required']
        card=self.cards[0]
        attempt=self.store.begin_attempt(run,card['company'],card['url'],card['job'],'boss',card['key'])
        action=self.store.prepare_action(run,attempt,'greet','本地招聘者',{})
        other=self.store.begin_attempt(self.run,card['company'],card['url'],card['job'],'boss','other-key')
        control_run(self.store,{'run_id':run,'action':'paused'})
        self.store.control(run,'running')
        new=selection_step(self.store,run)['ai_required']
        self.assertNotEqual(new['decision_id'],original['decision_id'])
        self.assertEqual([row['key'] for row in new['candidates']],[card['key']])
        self.assertEqual(self.store.one('attempts',attempt)['status'],'skipped')
        self.assertEqual(self.store.one('actions',action)['status'],'not_done')
        self.assertEqual(self.store.one('attempts',other)['status'],'in_progress')
        self.send.assert_not_called()

    def test_text_budget_splits_complete_candidates_without_dropping_them(self):
        self.cards.append({**self.cards[0],'key':'/job_detail/two.html','url':'https://www.zhipin.com/job_detail/two.html','job':'第二个实习'})
        for card in self.cards:self.details[card['key']]='完整正文'+('x'*1800)
        with patch('job_assistant.ai_selection.MAX_INPUT_CHARS',3000):
            first=self.pending()
            self.assertEqual(len(first['candidates']),1)
            self.assertEqual(len(first['candidates'][0]['detail']['detail']),1804)
            accept_selection(self.store,self.run,first['decision_id'],self.decisions(first,'reject'))
            second=selection_step(self.store,self.run)['ai_required']
            self.assertEqual(len(second['candidates']),1)
            self.assertNotEqual(first['candidates'][0]['key'],second['candidates'][0]['key'])
            accept_selection(self.store,self.run,second['decision_id'],self.decisions(second,'reject'))
        self.assertEqual(self.count('app_ai_cache'),2)

    def test_oversized_single_job_is_recorded_not_silently_truncated_or_sent(self):
        self.details[self.cards[0]['key']]='x'*5000
        with patch('job_assistant.ai_selection.MAX_INPUT_CHARS',3000):
            result=self.pending()
        self.assertTrue(result['done'])
        self.assertEqual(self.count('app_ai_cache'),0)
        self.assertEqual(self.count('attention'),1)
        self.send.assert_not_called()

    def test_resume_after_exhausting_list_continues_watching_without_opening_new_jobs(self):
        self.cards[0]['contacted']=True
        self.assertTrue(self.pending()['done'])
        with self.store.conn:advance_control(self.store,self.run)
        self.store.control(self.run,'paused')
        self.store.control(self.run,'running')
        self.browser.reset_mock()
        resumed=selection_step(self.store,self.run)
        self.assertTrue(resumed['done'])
        self.assertIn('列表无新增候选',resumed['message'])
        self.browser.assert_not_called()
        self.send.assert_not_called()

    def test_material_version_changes_cache_even_if_public_job_facts_are_same(self):
        pending=self.pending()
        accept_selection(self.store,self.run,pending['decision_id'],self.decisions(pending,'reject'))
        data=json.loads(self.profile.read_text(encoding='utf-8'))
        data['contact']['email']='new-private@example.com'
        other=self.store.root/'profile-v2.json';other.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
        config=prepare_config({**self.config,'profile_path':str(other),'resume_path':str(other),'profile_version':'v2'})
        self.assertIn('decision_id',self.pending(self.new_run(config)))

    def test_worker_selection_entry_requires_native_control_generation(self):
        from job_assistant.app_service import handle_app_request
        pending=self.pending()
        request={'id':'test','method':'accept_selection','params':{'run_id':self.run,'decision_id':pending['decision_id'],
                                                                  'decisions':self.decisions(pending)}}
        self.assertFalse(handle_app_request(self.store,request)['ok'])
        self.assertEqual(self.count('app_ai_cache'),0)
        request['params']['control_generation']=pending['control_generation']
        accepted=handle_app_request(self.store,request)
        self.assertTrue(accepted['ok'])
        self.assertTrue(accepted['result']['saved'])


if __name__=='__main__':unittest.main()
