"""原生公司网申执行接口的范围、资料及提交状态回归。"""
from contextlib import closing
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'apps/worker/scripts'))
from job_assistant.app_decisions import current_generation
from job_assistant.app_service import handle_app_request
from job_assistant.cli import prepare_config
from job_assistant.migrations import migrate_app
from job_assistant.store import Store


class CompanyWebTests(unittest.TestCase):
    def setUp(self):
        folder = self.enterContext(TemporaryDirectory())
        self.root = Path(folder).resolve()
        self.store = self.enterContext(closing(Store(self.root)))
        self.companies = self.root / 'companies.csv'
        self.companies.write_text(
            '公司,申请网站,岗位\n甲公司,https://first.example.invalid/careers,嵌入式工程师\n'
            '乙公司,https://second.example.invalid/careers,\n'
            '未选公司,https://outside.example.invalid/careers,后端工程师\n', encoding='utf-8',
        )
        self.profile = self.root / 'profile.json'
        self.profile.write_text(json.dumps({'education': [{'school': '合成大学'}], 'id_number': 'sensitive',
                                           'password': 'private', 'notes': '  已保存的说明  '},
                                           ensure_ascii=False), encoding='utf-8')
        self.resume = self.root / '合成简历.pdf'
        self.resume.write_bytes(b'fake pdf')
        migrate_app(self.store)

    def configure(self, submit=False):
        config = prepare_config({
            'channels': ['web'], 'target_roles': ['测试工程师'], 'companies': ['甲公司', '乙公司'],
            'employment_type': 'any', 'company_table': str(self.companies), 'profile_path': str(self.profile),
            'resume_path': str(self.resume), 'web_submit': submit,
        })
        self.run_id = self.store.create_run(config, '确认合成范围')
        with self.store.conn:
            self.store.conn.execute('INSERT INTO app_sessions (run_id) VALUES (?)', (self.run_id,))
        self.generation = current_generation(self.store, self.run_id)

    def call(self, operation, **arguments):
        return handle_app_request(self.store, {'id': 'company-web-test', 'method': 'company_web', 'params': {
            'run_id': self.run_id, 'control_generation': self.generation,
            'request': {'operation': operation, **arguments},
        }})

    def result(self, operation, **arguments):
        response = self.call(operation, **arguments)
        self.assertTrue(response['ok'], response)
        return response['result']

    def denied(self, operation, text, **arguments):
        response = self.call(operation, **arguments)
        self.assertFalse(response['ok'], response)
        self.assertIn(text, response['error']['message'])

    def first(self):
        return self.result('begin', source_row=2, job='嵌入式工程师',
                           url='https://first.example.invalid/form')['attempt_id']

    def resume_batch(self):
        self.store.control(self.run_id, 'running')
        self.generation = current_generation(self.store, self.run_id)
        self.assertEqual(self.result('prepare_batch')['phase'], 'check')

    @staticmethod
    def check(number, status, target=None):
        target = f'browser:target-{number}' if target is None else target
        return dict(source_row=number, status=status, target_id=target, tab_available=bool(target))

    def prepare_pages(self):
        plan = self.result('prepare_batch')
        self.assertEqual(plan['phase'], 'prepare')
        self.assertEqual([r['state'] for r in plan['rows']], ['pending_open', 'pending_open'])
        prepared = self.result('record_checks', phase='prepare', checks=[self.check(2, 'opened'), self.check(3, 'opened')])
        self.assertEqual((prepared['state'], prepared['eligible_count']), ('paused', 0))

    def queue(self):
        return handle_app_request(self.store, {'id': 'snapshot', 'method': 'snapshot',
            'params': {'run_id': self.run_id, 'view': 'running'}})['result']['company_queue']

    def user_op(self, operation, number):
        return handle_app_request(self.store, {'id': 'user', 'method': operation,
                                               'params': {'run_id': self.run_id, 'source_row': number}})

    def test_login_checkpoint_fills_remaining_in_order_and_manual_submit_has_separate_origin(self):
        self.configure()
        self.prepare_pages()
        self.resume_batch()
        denied = [self.check(2, 'needs_login'), self.check(3, 'needs_login')]
        self.assertEqual(self.result('record_checks', phase='check', checks=denied)['state'], 'paused')
        self.assertFalse(self.store.snapshot()['attempts'])
        self.resume_batch()
        mixed = [self.check(3, 'ready'), self.check(2, 'needs_login')]
        self.assertEqual(self.result('record_checks', phase='check', checks=mixed)['eligible_count'], 1)
        self.assertEqual([r['source_row'] for r in self.result('scope')['rows']], [3])
        self.denied('begin', '尚未通过', source_row=2, job='嵌入式工程师', url='https://first.example.invalid/form')
        second = self.result('begin', source_row=3, job='测试工程师', url='https://second.example.invalid/form')['attempt_id']
        self.assertFalse(self.result('ready', attempt_id=second)['paused'])
        self.assertEqual(self.result('finish_batch')['state'], 'paused')
        self.resume_batch()
        self.result('record_checks', phase='check', checks=[self.check(2, 'ready'), self.check(3, 'needs_review')])
        self.assertEqual([r['source_row'] for r in self.result('scope')['rows']], [2])
        first = self.first()
        self.result('ready', attempt_id=first)
        self.assertEqual(self.result('finish_batch')['state'], 'paused')
        self.assertEqual(self.queue()['phase'], 'waiting_review')
        self.assertEqual([r['state'] for r in self.queue()['rows']], ['ready_for_review'] * 2)
        self.assertFalse(self.user_op('company_reopen_review', 2)['ok'])
        self.assertTrue(self.user_op('company_mark_submitted', 2)['ok'])
        self.assertFalse(self.user_op('company_mark_submitted', 2)['ok'])
        self.assertTrue(self.user_op('company_mark_submitted', 3)['ok'])
        self.assertEqual(self.queue()['phase'], 'completed')
        self.assertEqual([r['state'] for r in self.queue()['rows']], ['manual_submitted'] * 2)
        self.assertFalse(self.store.snapshot()['actions'])
        self.assertEqual(self.store.one('attempts', second)['status'], 'manual_submitted')
        self.configure(submit=True)
        self.assertFalse(self.result('scope')['rows'])
        self.assertEqual(self.result('scope')['historical_count'], 2)

    def test_prepare_retries_persist_tab_ownership_capacity_and_lost_target(self):
        self.configure()
        self.result('prepare_batch')
        self.result('bind_opened', source_row=2, target_id='browser:target-2')
        self.assertIn('browser:target-2', self.result('prepare_batch', prepare_only=True)['occupied_targets'])
        checks = [self.check(2, 'opened'), self.check(3, 'pending', '') | {'reason': '可用内存不足'}]
        self.result('record_checks', phase='prepare', checks=checks)
        self.assertEqual(self.queue()['rows'][1]['reason'], '可用内存不足')
        self.resume_batch()
        self.result('record_checks', phase='check', checks=[self.check(2, 'ready'), self.check(3, 'pending', '')])
        self.result('ready', attempt_id=self.first())
        self.result('finish_batch')
        req = {'id': 'browser', 'method': 'company_lost_target',
               'params': {'run_id': self.run_id, 'source_row': 2, 'target_id': 'browser:other'}}
        self.assertFalse(handle_app_request(self.store, req)['ok'])
        self.assertTrue(self.queue()['rows'][0]['tab_available'])
        req['params']['target_id'] = 'browser:target-2'
        self.assertTrue(handle_app_request(self.store, req)['ok'])
        original = self.queue()['rows'][0]
        self.assertEqual((original['state'], original['target_id'], original['tab_available']),
                         ('ready_for_review', 'browser:target-2', False))
        self.assertIn('草稿状态未知', original['reason'])
        self.assertTrue(self.user_op('company_reopen_review', 2)['ok'])

    def test_subsequent_login_handoff_recovery_closed_draft_and_unobserved_rows(self):
        self.configure()
        self.prepare_pages()
        self.resume_batch()
        self.result('record_checks', phase='check', checks=[self.check(2, 'locate'), self.check(3, 'pending', '')])
        first = self.first()
        self.result('wait_login', source_row=2, reason='岗位表单要求验证码')
        self.denied('field', '尚未通过', attempt_id=first, label='简历', source='resume')
        self.assertEqual(self.result('finish_batch')['state'], 'paused')
        self.resume_batch()
        self.result('record_checks', phase='check', checks=[self.check(2, 'ready')])
        self.assertEqual(self.first(), first)
        self.result('handoff', attempt_id=first, reason='必填项还需本人确认')
        self.assertEqual(self.result('finish_batch')['state'], 'paused')
        self.resume_batch()
        self.result('record_checks', phase='check', checks=[self.check(2, 'ready')])
        self.assertEqual(self.first(), first)
        self.result('ready', attempt_id=first)
        self.result('finish_batch')
        self.resume_batch()
        self.result('record_checks', phase='check', checks=[self.check(2, 'needs_review', '')])
        self.assertTrue(self.user_op('company_reopen_review', 2)['ok'])
        self.assertEqual(self.queue()['rows'][0]['state'], 'pending_open')
        self.resume_batch()
        self.result('record_checks', phase='check', checks=[self.check(2, 'ready', 'browser:new-form')])
        self.assertEqual(self.first(), first)

    def test_browser_destination_requires_bound_target_job_and_tenant_and_survives_only_same_tab(self):
        self.configure(submit=True)
        self.prepare_pages()
        self.resume_batch()
        self.result('record_checks', phase='check', checks=[self.check(2, 'ready'), self.check(3, 'ready')])
        self.denied('select_row', '按公司表顺序', source_row=3)
        self.result('select_row', source_row=2)
        self.result('select_row', source_row=2)
        self.assertEqual(self.queue()['rows'][0]['state'], 'filling')
        target = 'browser:popup-2:target-2'
        source = 'https://first.example.invalid/jobs/1'
        dest = 'https://app.mokahr.com/apply/company-a/forms'
        args = dict(source_row=2, job='嵌入式工程师', entry_url=source, destination_url=dest, target_id=target)
        self.denied('bind_destination', '招聘入口', **(args | {'entry_url': 'https://other.test/jobs'}))
        self.denied('bind_destination', 'HTTPS', **(args | {'destination_url': 'https://a:b@ats.example.invalid/form'}))
        self.result('bind_destination', **args)
        scoped = dict(source_row=2, job=args['job'], target_id=target)
        self.denied('begin', '网站不一致', **scoped, url=dest.replace('company-a', 'company-b'))
        self.denied('begin', '网站不一致', **(scoped | {'target_id': 'browser:other'}), url=dest)
        first = self.result('begin', **scoped, url=dest + '/next')['attempt_id']
        action = self.result('prepare', attempt_id=first, review='a' * 64)['action_id']
        self.result('claim', attempt_id=first, action_id=action, review='a' * 64)
        self.result('settle', attempt_id=first, action_id=action, status='uncertain', reason='未收到回执')
        self.assertEqual([r['source_row'] for r in self.result('scope')['rows']], [3])
        self.assertFalse(self.user_op('company_reopen_review', 2)['ok'])
        self.assertFalse(self.user_op('company_mark_submitted', 2)['ok'])
        self.assertEqual(self.queue()['rows'][0]['state'], 'uncertain')

    def test_imported_shared_ats_link_keeps_tenant_when_opening_and_binding_destination(self):
        text = self.companies.read_text(encoding='utf-8').replace('https://first.example.invalid/careers',
                                                                  'https://app.mokahr.com/campus_apply/company-a/jobs')
        self.companies.write_text(text, encoding='utf-8')
        self.configure()
        self.prepare_pages()
        self.resume_batch()
        self.result('record_checks', phase='check', checks=[self.check(2, 'ready'), self.check(3, 'needs_login')])
        other_tenant = 'https://app.mokahr.com/campus_apply/company-b/form'
        self.denied('begin', '网站不一致', source_row=2, job='嵌入式工程师', url=other_tenant)
        self.denied('bind_destination', '招聘入口', source_row=2, job='嵌入式工程师', entry_url=other_tenant,
                    destination_url='https://jobs.lever.co/company-a/123', target_id='browser:new')
        self.denied('bind_destination', '租户不一致', source_row=2, job='嵌入式工程师',
                    entry_url='https://app.mokahr.com/campus_apply/company-a/jobs',
                    destination_url=other_tenant, target_id='browser:new')
        self.result('begin', source_row=2, job='嵌入式工程师',
                    url='https://app.mokahr.com/campus_apply/company-a/form')

    def test_pausing_after_claim_preserves_uncertainty_and_resumes_other_rows_without_restart(self):
        self.configure(submit=True)
        self.prepare_pages()
        self.resume_batch()
        self.result('record_checks', phase='check', checks=[self.check(2, 'ready'), self.check(3, 'ready')])
        first = self.first()
        args = dict(attempt_id=first, review='a' * 64)
        action = self.result('prepare', **args)['action_id']
        self.result('claim', **args, action_id=action)
        response = handle_app_request(self.store, {'id': 'operator', 'method': 'control_run',
            'params': {'run_id': self.run_id, 'action': 'paused'}})
        self.assertTrue(response['ok'], response)
        self.assertEqual(self.store.one('actions', action)['status'], 'uncertain')
        self.assertEqual(self.queue()['rows'][0]['state'], 'uncertain')
        self.assertEqual(len(self.store.snapshot()['attention']), 1)
        self.resume_batch()
        self.result('record_checks', phase='check', checks=[self.check(3, 'ready')])
        self.assertEqual([r['source_row'] for r in self.result('scope')['rows']], [3])
        self.denied('claim', '人工核查', **args, action_id=action)

    def test_scope_and_sources_are_limited_and_handoff_can_continue(self):
        self.configure()
        scope = self.result('scope')
        self.assertFalse(scope['web_submit'])
        self.assertEqual([row['source_row'] for row in scope['rows']], [2, 3])
        self.denied('begin', '公司不在', source_row=4, job='后端工程师', url='https://outside.example.invalid/form')
        self.denied('begin', '岗位不在', source_row=2, job='测试工程师', url='https://first.example.invalid/form')
        self.denied('begin', '岗位不在', source_row=3, job='其他岗位', url='https://second.example.invalid/form')
        self.denied('begin', '网站不一致', source_row=2, job='嵌入式工程师', url='https://second.example.invalid/form')
        self.denied('begin', '网站不一致', source_row=2, job='嵌入式工程师', url='https://first.example.invalid:8443/form')
        attempt = self.first()
        self.assertEqual(self.first(), attempt)
        self.assertEqual(self.result('value', attempt_id=attempt, source='profile.education.0.school')['value'], '合成大学')
        self.assertEqual(self.result('value', attempt_id=attempt, source='profile.notes')['value'], '  已保存的说明  ')
        for field in ('profile.id_number', 'profile.password', 'profile.sources.name'):
            self.denied('value', '需要人工', attempt_id=attempt, source=field)
        outcome = self.result('handoff', attempt_id=attempt, reason='缺少必填项')
        self.assertTrue(outcome['attention']['new'])
        self.assertEqual(self.store.one('attempts', attempt)['status'], 'needs_user')
        self.assertEqual([row['source_row'] for row in self.result('scope')['rows']], [3])
        self.denied('value', '人工核查', attempt_id=attempt, source='profile.education.0.school')
        blocked = self.result('begin', source_row=3, blocked=True)['attempt_id']
        self.denied('value', '先确认', attempt_id=blocked, source='profile.education.0.school')
        self.result('handoff', attempt_id=blocked, reason='需要登录')
        self.store.control(self.run_id, 'paused')
        self.denied('scope', '控制状态已变化')

    def test_fill_only_pauses_at_ready_and_next_scope_skips_the_ready_company(self):
        self.configure()
        attempt = self.first()
        recorded = self.result('field', attempt_id=attempt, source='resume', label='简历附件')
        self.assertTrue(recorded['recorded'])
        row = self.store.conn.execute('SELECT value, evidence_id FROM fields WHERE attempt_id=?', (attempt,)).fetchone()
        self.assertEqual(tuple(row), ('合成简历.pdf', None))
        self.denied('prepare', '未授权提交', attempt_id=attempt, review='a' * 64)
        outcome = self.result('ready', attempt_id=attempt)
        self.assertTrue(outcome['paused'])
        self.assertTrue(outcome['attention']['new'])
        self.assertEqual(self.store.one('runs', self.run_id)['status'], 'paused')
        self.assertEqual(self.store.one('attempts', attempt)['status'], 'needs_user')
        self.assertIn('当前网页确认并提交', self.store.one('attempts', attempt)['reason'])
        self.denied('scope', '控制状态已变化')
        self.denied('finish_batch', '控制状态已变化')
        self.assertFalse(self.store.snapshot()['actions'])
        self.assertFalse(self.store.snapshot()['evidence'])
        self.store.control(self.run_id, 'running')
        self.generation = current_generation(self.store, self.run_id)
        self.assertEqual([row['source_row'] for row in self.result('scope')['rows']], [3])

    def test_finish_batch_requires_a_terminal_outcome_for_every_authorized_entry(self):
        with self.companies.open('a', encoding='utf-8') as stream:
            stream.write('甲公司,https://first.example.invalid/other,产品经理\n')
        self.configure(submit=True)
        attempt = self.first()
        self.assertEqual(self.result('finish_batch'), {
            'completed': False, 'pending_count': 2, 'pending_companies': ['甲公司', '乙公司'], 'historical_count': 0,
        })
        self.result('no_match', attempt_id=attempt, reason='岗位已关闭')
        self.assertEqual([row['source_row'] for row in self.result('scope')['rows']], [3, 5])
        blocked = self.result('begin', source_row=3, blocked=True)['attempt_id']
        self.result('handoff', attempt_id=blocked, reason='需要登录')
        self.assertEqual(self.result('finish_batch')['pending_companies'], ['甲公司'])
        self.assertEqual(self.store.one('runs', self.run_id)['status'], 'running')
        alternative = self.result('begin', source_row=5, blocked=True)['attempt_id']
        self.result('no_match', attempt_id=alternative, reason='没有开放的申请岗位')
        self.assertFalse(self.result('scope')['rows'])
        self.assertEqual(self.result('finish_batch'), {'completed': True, 'pending_count': 0, 'pending_companies': [],
                                                      'historical_count': 0})
        self.assertEqual(self.store.one('runs', self.run_id)['status'], 'completed')
        session = self.store.conn.execute('SELECT state FROM app_sessions WHERE run_id=?', (self.run_id,)).fetchone()
        self.assertEqual(session['state'], 'completed')

    def test_previous_application_blocks_are_visible_skips_not_new_submissions(self):
        self.configure(submit=True)
        old_run = self.run_id
        first = self.first()
        action = self.result('prepare', attempt_id=first, review='a' * 64)['action_id']
        args = {'attempt_id': first, 'action_id': action}
        self.result('claim', **args, review='a' * 64)
        self.result('settle', **args, status='succeeded', reason='合成回执')
        self.configure(submit=True)
        scope = self.result('scope')
        self.assertEqual([row['source_row'] for row in scope['rows']], [3])
        self.assertEqual(scope['historical_count'], 1)
        partial = self.result('finish_batch')
        self.assertEqual((partial['completed'], partial['pending_count']), (False, 1))
        self.assertEqual(partial['historical_count'], 1)
        self.assertEqual(self.result('finish_batch'), partial)
        self.denied('field', '不属于本轮', attempt_id=first, label='证件', source='resume')
        second = self.result('begin', source_row=3, blocked=True)['attempt_id']
        self.result('no_match', attempt_id=second, reason='没有开放岗位')
        self.assertTrue(self.result('finish_batch')['completed'])
        rows = self.store.conn.execute("SELECT status, reason FROM attempts WHERE run_id=? AND company='甲公司'",
                                       (self.run_id,)).fetchall()
        self.assertEqual([tuple(row) for row in rows], [('skipped', '本轮跳过：此前任务已确认提交')])
        self.assertEqual(self.store.one('actions', action)['run_id'], old_run)
        self.assertEqual(self.store.conn.execute('SELECT count(*) FROM actions').fetchone()[0], 1)

    def test_revision_rechecks_unmatched_entries_for_changed_role_and_changed_table_job(self):
        from job_assistant.app_runs import preview_run, start_run
        self.configure()
        first = self.first()
        second = self.result('begin', source_row=3, blocked=True)['attempt_id']
        for attempt in (first, second):
            self.result('no_match', attempt_id=attempt, reason='原范围未匹配')
        self.assertFalse(self.result('scope')['rows'])
        self.store.control(self.run_id, 'paused')
        self.companies.write_text(self.companies.read_text(encoding='utf-8').replace('嵌入式工程师', '软件工程师'),
                                  encoding='utf-8')
        config = json.loads(self.store.one('runs', self.run_id)['config'])
        config.update(target_roles=['固件工程师'], execution_mode='ai', window_title='Chrome')
        revision = preview_run(self.store, config, self.run_id)
        start_run(self.store, {'ticket': revision['ticket'], 'confirmed': True}, revision=True)
        self.store.control(self.run_id, 'running')
        self.generation = current_generation(self.store, self.run_id)
        scope = self.result('scope')
        self.assertEqual([row['source_row'] for row in scope['rows']], [2, 3])
        self.assertEqual(scope['rows'][0]['job'], '软件工程师')
        self.assertEqual(scope['target_roles'], ['固件工程师'])

    def test_revised_queue_does_not_attach_new_row_to_previous_tab(self):
        from job_assistant.app_runs import preview_run, start_run
        self.configure()
        self.prepare_pages()
        previous = self.queue()
        self.assertEqual(previous['rows'][0]['target_id'], 'browser:target-2')
        config = json.loads(self.store.one('runs', self.run_id)['config'])
        config.update(target_roles=['新岗位'], execution_mode='ai', window_title='Chrome')
        ticket = preview_run(self.store, config, self.run_id)
        start_run(self.store, {'ticket': ticket['ticket'], 'confirmed': True}, revision=True)
        revised = self.queue()
        self.assertNotEqual(previous['scope_revision'], revised['scope_revision'])
        self.assertEqual((revised['phase'], revised['rows'][0]['target_id']), ('prepare', ''))
        self.store.control(self.run_id, 'running')
        self.generation = current_generation(self.store, self.run_id)
        self.assertIn('browser:target-2', self.result('prepare_batch')['occupied_targets'])

    def test_failed_pause_rolls_back_manual_handoff_and_control_generation(self):
        self.configure()
        attempt = self.first()
        self.store.conn.execute("""CREATE TEMP TRIGGER fail_pause BEFORE INSERT ON panel_commands
                                  BEGIN SELECT RAISE(ABORT, '模拟暂停指令写入失败'); END""")
        self.denied('ready', '模拟暂停指令写入失败', attempt_id=attempt)
        self.assertEqual(self.store.one('attempts', attempt)['status'], 'in_progress')
        self.assertEqual(self.store.one('runs', self.run_id)['status'], 'running')
        self.assertEqual(current_generation(self.store, self.run_id), self.generation)
        self.assertFalse(self.store.snapshot()['attention'])
        self.store.conn.execute('DROP TRIGGER fail_pause')
        self.assertTrue(self.result('ready', attempt_id=attempt)['paused'])

    def test_failed_attention_write_leaves_handoff_and_settlement_retryable(self):
        self.configure(submit=True)
        first = self.first()
        other = self.result('begin', source_row=3, blocked=True)['attempt_id']
        review = 'a' * 64
        action = self.result('prepare', attempt_id=first, review=review)['action_id']
        details = {'attempt_id': first, 'action_id': action}
        self.result('claim', **details, review=review)
        self.store.conn.execute("""CREATE TEMP TRIGGER fail_attention BEFORE INSERT ON attention
                                  BEGIN SELECT RAISE(ABORT, '模拟待办写入失败'); END""")
        self.denied('handoff', '模拟待办写入失败', attempt_id=other, reason='需要登录')
        self.denied('settle', '模拟待办写入失败', **details, status='uncertain', reason='无回执')
        self.assertTrue(all(self.store.one('attempts', item)['status'] == 'in_progress' for item in [first, other]))
        self.assertEqual(self.store.one('actions', action)['status'], 'executing')
        self.denied('claim', '已执行', **details, review=review)
        self.assertFalse(self.store.snapshot()['attention'])
        self.store.conn.execute('DROP TRIGGER fail_attention')
        self.assertTrue(self.result('handoff', attempt_id=other, reason='需要登录')['attention']['new'])
        self.assertTrue(self.result('settle', **details, status='uncertain', reason='无回执')['attention']['new'])
        self.assertEqual(len(self.store.snapshot()['attention']), 2)

    def test_reviewed_submission_is_single_use_and_ambiguous_results_block_retry(self):
        self.configure(submit=True)
        attempt = self.first()
        self.denied('ready', '自动提交', attempt_id=attempt)
        # 网页预填也可直接走原生核验。
        prepared = self.result('prepare', attempt_id=attempt, review='a' * 64)
        action = prepared['action_id']
        details = {'attempt_id': attempt, 'action_id': action}
        self.denied('settle', '先实际执行', **details, status='succeeded', reason='无回执')
        self.denied('claim', '核对已变化', **details, review='b' * 64)
        self.assertTrue(self.result('claim', **details, review='a' * 64)['claimed'])
        self.denied('claim', '已执行', **details, review='a' * 64)
        outcome = self.result('settle', **details, status='uncertain', reason='没有明确回执')
        self.assertTrue(outcome['attention']['new'])
        self.assertEqual(self.store.one('actions', action)['status'], 'uncertain')
        self.assertEqual(self.store.one('attempts', attempt)['status'], 'uncertain')
        self.assertEqual([row['source_row'] for row in self.result('scope')['rows']], [3])
        self.denied('begin', '结果待核实', source_row=2, job='嵌入式工程师', url='https://first.example.invalid/form')

    def test_native_success_records_receipt_and_legacy_actions_still_require_screenshots(self):
        self.configure(submit=True)
        attempt = self.first()
        review = 'a' * 64
        with self.assertRaisesRegex(ValueError, '截图证据不完整'):
            self.store.prepare_action(self.run_id, attempt, 'submit', '甲公司', {'native_web_receipt': True})
        action = self.result('prepare', attempt_id=attempt, review=review)['action_id']
        details = {'attempt_id': attempt, 'action_id': action}
        with self.assertRaisesRegex(ValueError, '对应的网申执行入口'):
            self.store.claim_action(self.run_id, action)
        self.result('claim', **details, review=review)
        self.denied('settle', '核验结果', **details, status='succeeded', reason='')
        with self.assertRaisesRegex(ValueError, '记录不存在'):
            self.store.finish_action(action, 'succeeded', reason='已提交')
        self.result('settle', **details, status='succeeded', reason='提交申请成功')
        self.assertEqual(self.store.one('attempts', attempt)['status'], 'success')
        self.assertEqual([row['source_row'] for row in self.result('scope')['rows']], [3])
        self.assertEqual(self.store.one('actions', action)['reason'], '提交申请成功')
        self.assertFalse(self.store.snapshot()['evidence'])


if __name__ == '__main__':
    unittest.main()
