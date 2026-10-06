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

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/job-search-assistant/scripts'))
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

    def test_recover_pauses_only_app_runs_and_marks_inflight_uncertain(self):
        _, run = self.demo()
        other = self.db.create_run({'channels': ['web']}, '其他 Skill 轮次')
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


if __name__ == '__main__':
    unittest.main()
