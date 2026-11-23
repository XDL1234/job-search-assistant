"""桌面授权持久化、恢复与工具作用域。"""
import importlib.util
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'apps/worker/scripts'))
from job_assistant.app_service import handle_app_request
from job_assistant.store import Store


class AppExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = Store(self.root)
        self.addCleanup(self.db.close)

    def call(self, method, **params):
        return handle_app_request(self.db, {'id': 'x', 'method': method, 'params': params})

    def demo(self):
        preview = self.call('preview_demo')
        self.assertTrue(preview['ok'], preview)
        started = self.call('start_run', ticket=preview['result']['ticket'], confirmed=True)
        self.assertTrue(started['ok'], started)
        return preview['result'], started['result']['run_id']

    def test_ticket_survives_worker_restart_and_consumes_once(self):
        p = self.call('preview_demo')
        self.assertTrue(p['ok'], p)
        other = Store(self.root)
        try:
            result = handle_app_request(other, {'id': 'y', 'method': 'start_run', 'params': {'ticket': p['result']['ticket'], 'confirmed': True}})
            self.assertTrue(result['ok'], result)
        finally:
            other.close()
        self.assertFalse(self.call('start_run', ticket=p['result']['ticket'], confirmed=True)['ok'])

    def test_material_change_invalidates_preview(self):
        p = self.call('preview_demo')
        self.assertTrue(p['ok'], p)
        Path(p['result']['config']['profile_path']).write_text('{}')
        self.assertFalse(self.call('start_run', ticket=p['result']['ticket'], confirmed=True)['ok'])
        self.assertEqual(self.db.conn.execute('SELECT COUNT(*) FROM runs').fetchone()[0], 0)

    def test_start_is_paused_until_executor_ready_and_pause_is_immediate(self):
        _, run = self.demo()
        self.assertEqual(self.db.one('runs', run)['status'], 'paused')
        self.assertTrue(self.call('control_run', run_id=run, action='running')['ok'])
        self.assertEqual(self.db.one('runs', run)['status'], 'paused', 'UI 继续应等待原生执行器接收')
        self.assertTrue(self.call('control_run', run_id=run, action='paused')['ok'])
        with self.assertRaises(ValueError):
            self.db.check_run(run)

    def test_late_pause_cancels_pending_watch_and_preserves_completed_run_on_reopen(self):
        _, run = self.demo()
        with self.db.conn:
            self.db.conn.execute("UPDATE runs SET status='completed' WHERE id=?", (run,))
        watch = self.call('control_run', run_id=run, action='watching')
        self.assertTrue(watch['ok'], watch)
        self.assertTrue(self.call('control_run', run_id=run, action='paused')['ok'])
        self.assertTrue(self.call('recover_app')['ok'])
        self.assertEqual(self.db.one('runs', run)['status'], 'completed')
        command = self.db.conn.execute('SELECT status FROM panel_commands WHERE id=?', (watch['result']['id'],)).fetchone()
        self.assertEqual(command['status'], 'cancelled')

    def test_recover_pauses_only_app_runs_and_marks_inflight_uncertain(self):
        _, run = self.demo()
        other = self.db.create_run({'channels': ['web']}, '其他非桌面轮次')
        self.db.control(run, 'running')
        attempt = self.db.begin_attempt(run, '模拟招聘', 'http://127.0.0.1/simulation', '嵌入式', 'boss', 'test-greet')
        action = self.db.prepare_action(run, attempt, 'greet', '模拟招聘', {})
        self.db.claim_action(run, action)
        result = self.call('recover_app')
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.db.one('runs', run)['status'], 'paused')
        self.assertEqual(self.db.one('runs', other)['status'], 'running')
        self.assertEqual(self.db.one('actions', action)['status'], 'uncertain')
        self.assertEqual(self.db.one('attempts', attempt)['status'], 'uncertain')
        self.db.control(run, 'running')
        with self.assertRaisesRegex(ValueError, '核实|重复'):
            self.db.claim_action(run, action)

    def test_new_demo_has_independent_company_identity(self):
        first, _ = self.demo()
        second, _ = self.demo()
        self.assertNotEqual(first['config']['companies'], second['config']['companies'])

    def test_migration_backup_and_idempotence(self):
        self.assertIsNotNone(importlib.util.find_spec('job_assistant.migrations'))
        from job_assistant.migrations import migrate_app
        migrate_app(self.db)
        backups = list((self.root / 'backups').glob('*.sqlite3'))
        self.assertEqual(len(backups), 1)
        migrate_app(self.db)
        self.assertEqual(len(list((self.root / 'backups').glob('*.sqlite3'))), 1)
        with closing(sqlite3.connect(backups[0])) as conn:
            self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='app_sessions'").fetchone())

    def test_migration_failure_rolls_back(self):
        self.assertIsNotNone(importlib.util.find_spec('job_assistant.migrations'))
        from job_assistant.migrations import migrate_app
        with patch('job_assistant.migrations.STATEMENTS', ('CREATE TABLE should_rollback(id TEXT)', 'INVALID SQL')):
            with self.assertRaises(sqlite3.Error):
                migrate_app(self.db)
        self.assertIsNone(self.db.conn.execute("SELECT name FROM sqlite_master WHERE name='should_rollback'").fetchone())

    def test_executor_rejects_cross_run_and_model_control(self):
        _, run = self.demo()
        for operation in ('start', 'control', 'resume_conversation', 'act'):
            result = self.call('executor_action', run_id=run, request={'operation': operation, 'run_id': 'wrong'})
            self.assertFalse(result['ok'], result)

    def test_internal_watch_transition_cannot_resume_a_paused_run(self):
        _, run = self.demo()
        self.db.control(run, 'running')
        self.assertTrue(self.call('enter_watch', run_id=run)['ok'])
        self.assertEqual(self.db.one('runs', run)['status'], 'watching')
        self.db.control(run, 'paused')
        self.assertFalse(self.call('enter_watch', run_id=run)['ok'])
        self.assertEqual(self.db.one('runs', run)['status'], 'paused')

    def test_evidence_requires_current_run_and_post_resume_frame(self):
        from PIL import Image
        preview, run = self.demo()
        self.db.control(run, 'running')
        company = preview['config']['companies'][0]
        attempt = self.db.begin_attempt(run, company, 'http://127.0.0.1/simulation', '嵌入式软件工程师', 'web', 'demo')
        path = self.root / 'frames' / ('a' * 32 + '.png')
        path.parent.mkdir()
        Image.new('RGB', (20, 20), 'white').save(path)
        request = {'operation': 'evidence', 'attempt_id': attempt, 'path': str(path), 'kind': 'form'}
        for owner, captured in [('another-run', time.time()), (run, 1)]:
            path.with_suffix('.json').write_text(json.dumps({'run_id': owner, 'captured_at': captured}))
            result = self.call('executor_action', run_id=run, request=request)
            self.assertFalse(result['ok'], result)
        self.assertEqual(self.db.conn.execute('SELECT COUNT(*) FROM evidence').fetchone()[0], 0)
        path.with_suffix('.json').write_text(json.dumps({'run_id': run, 'captured_at': time.time()}))
        self.assertTrue(self.call('executor_action', run_id=run, request=request)['ok'])

    def web_application(self, materials):
        from job_assistant.cli import prepare_config
        from job_assistant.migrations import migrate_app
        table = self.root / 'companies.csv'
        url = 'https://example.invalid/careers'
        table.write_text(f'公司,申请网站,岗位\n测试公司,{url},测试岗位\n', encoding='utf-8')
        config = prepare_config({'channels': ['web'], 'target_roles': [], 'employment_type': 'any',
                                 'company_table': str(table), 'window_title': '测试招聘页', **materials})
        run = self.db.create_run(config, '确认测试')
        migrate_app(self.db)
        with self.db.conn:
            self.db.conn.execute('INSERT INTO app_sessions (run_id) VALUES (?)', (run,))
        attempt = self.db.begin_attempt(run, '测试公司', url, '测试岗位', 'web', 'web-test-job')
        return config, run, attempt

    def test_web_materials_and_role_are_optional_without_affecting_boss(self):
        from job_assistant.cli import prepare_config, read_profile, verify_materials
        config, run, _ = self.web_application({})
        self.assertEqual(config, prepare_config(config))
        self.assertEqual(config, verify_materials(self.db, run))
        self.assertEqual({}, read_profile(config))
        self.assertEqual(('', '', '', ''), tuple(config[field] for field in
                         ('resume_path', 'resume_sha256', 'profile_path', 'profile_sha256')))
        with self.assertRaisesRegex(ValueError, '目标岗位'):
            prepare_config({**config, 'channels': ['boss']})
        with self.assertRaisesRegex(ValueError, '缺少 resume'):
            prepare_config({**config, 'channels': ['boss'], 'target_roles': ['测试岗位'],
                            'boss_mode': 'filtered', 'max_contacts': 1})

    def test_web_form_only_inputs_confirmed_values_and_holds_incomplete_application(self):
        from PIL import Image
        personal = self.root / 'profile.json'
        personal.write_text(json.dumps({'contact': {'email': '  '}, 'education': [{'school': '测试大学'}],
                                        'id_number': '000000000000000000'}), encoding='utf-8')
        _, run, attempt = self.web_application({'profile_path': str(personal)})
        path = self.root / 'frames' / ('b' * 32 + '.png')
        path.parent.mkdir()
        Image.new('RGB', (20, 20), 'white').save(path)
        meta = {'run_id': run, 'captured_at': time.time(), 'path': str(path)}
        path.with_suffix('.json').write_text(json.dumps(meta))

        def tool(**request):
            return self.call('executor_action', run_id=run, request={'attempt_id': attempt, **request})

        def fill(source, **extra):
            return tool(operation='act', purpose='fill', frame=str(path), field_source=source,
                        input={'op': 'type', **extra})

        with patch('job_assistant.desktop.foreground', return_value={'window': {'title': '测试招聘页'}}), \
             patch('job_assistant.desktop.Desktop.act', return_value=dict(meta)) as desktop:
            for source in ('profile.contact.email', 'profile.contact.phone', 'profile.education.1.school',
                           'profile.education', 'profile.resume_text', 'resume'):
                self.assertFalse(fill(source)['ok'], source)
            self.assertFalse(fill('profile.education.0.school', text='其他学校')['ok'])
            self.assertFalse(tool(operation='act', purpose='navigate', frame=str(path),
                                  input={'op': 'type', 'text': '伪造号码'})['ok'])
            desktop.assert_not_called()
            self.assertTrue(fill('profile.education.0.school')['ok'])
            self.assertEqual('测试大学', desktop.call_args.args[1]['text'])
            self.assertTrue(tool(operation='act', purpose='navigate', frame=str(path),
                                 input={'op': 'type', 'text': '测试岗位'})['ok'])

        self.assertFalse(tool(operation='field', name='邮箱', source='profile.contact.email')['ok'])
        self.assertTrue(tool(operation='field', name='证件号', source='profile.id_number')['ok'])
        row = self.db.conn.execute('SELECT value FROM fields WHERE attempt_id=?', (attempt,)).fetchone()
        self.assertEqual('已填证件号码', row['value'])
        evidence = self.db.evidence(attempt, path, 'pre_submit', ['证件号'])
        action = self.db.prepare_action(run, attempt, 'submit', '测试公司', {'pre_submit_evidence': evidence})
        result = tool(operation='attempt_status', status='needs_user', reason='缺少必填邮箱')
        self.assertTrue(result['ok'], result)
        self.assertFalse(fill('profile.education.0.school')['ok'])
        self.assertFalse(tool(operation='attempt_status', status='in_progress')['ok'])
        self.assertFalse(tool(operation='act', purpose='commit', action_id=action, frame=str(path),
                              input={'op': 'click', 'x': 2, 'y': 2})['ok'])
        checked = self.db.evidence(attempt, path, 'verification')
        self.assertFalse(tool(operation='finish', action_id=action, status='not_done', evidence_id=checked,
                              reason='未提交')['ok'])
        self.assertEqual('pending', self.db.one('actions', action)['status'])
        self.assertIn('缺少必填邮箱', self.db.conn.execute('SELECT reason FROM attention WHERE subject=?', (attempt,)).fetchone()[0])


if __name__ == '__main__':
    unittest.main()
