"""原生公司网申执行接口的范围、资料及提交状态回归。"""
from contextlib import closing
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/job-search-assistant/scripts'))
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
        self.assertFalse(self.store.snapshot()['actions'])
        self.assertFalse(self.store.snapshot()['evidence'])
        self.store.control(self.run_id, 'running')
        self.generation = current_generation(self.store, self.run_id)
        self.assertEqual([row['source_row'] for row in self.result('scope')['rows']], [3])

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
        self.assertEqual(self.store.one('actions', action)['reason'], '提交申请成功')
        self.assertFalse(self.store.snapshot()['evidence'])


if __name__ == '__main__':
    unittest.main()
