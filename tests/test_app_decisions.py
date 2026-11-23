"""暂停恢复后的旧请求不能借新状态发送；模型状态不暴露其他轮次历史。"""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'apps/worker/scripts'))
from job_assistant.app_decisions import check_generation, compact_status, current_generation
from job_assistant.app_runs import app_session
from job_assistant.app_service import handle_app_request
from job_assistant.store import Store


class AppDecisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Store(self.temp.name)
        self.addCleanup(self.db.close)
        self.run = self.db.create_run({'channels': ['boss'], 'max_contacts': 3}, '本地验证')
        from job_assistant.migrations import migrate_app
        migrate_app(self.db)

    def test_pause_resume_invalidates_request_even_when_running_again(self):
        old = current_generation(self.db, self.run)
        self.db.request_generation = (self.run, old)
        self.db.check_run(self.run)
        self.db.control(self.run, 'paused')
        self.db.control(self.run, 'running')
        with self.assertRaisesRegex(ValueError, '旧的 AI 决策'):
            self.db.check_run(self.run)
        with self.assertRaises(ValueError):
            check_generation(self.db, self.run, True)

    def test_panel_control_advances_generation_before_resume_is_applied(self):
        from job_assistant.panel_state import queue_control
        old = current_generation(self.db, self.run)
        queue_control(self.db, {'run_id': self.run, 'action': 'paused'})
        queue_control(self.db, {'run_id': self.run, 'action': 'running'})
        self.assertGreater(current_generation(self.db, self.run), old)

    def test_model_status_contains_no_other_runs_or_personal_history(self):
        other = self.db.create_run({'channels': ['web'], 'private': 'other-personal-data'}, '无关轮次')
        self.db.begin_attempt(other, 'private-company', 'https://example.com/apply', 'private-job', 'web', 'private')
        result = compact_status(self.db, self.run)
        serialized = json.dumps(result)
        self.assertNotIn(other, serialized)
        self.assertNotIn('private-company', serialized)
        self.assertNotIn('other-personal-data', serialized)
        self.assertEqual(result['remaining_contacts'], 3)

    def test_stale_generation_rejected_before_tool_dispatch(self):
        from unittest.mock import patch
        with self.db.conn:
            self.db.conn.execute('INSERT INTO app_sessions(run_id) VALUES (?)', (self.run,))
        old = current_generation(self.db, self.run)
        self.db.control(self.run, 'paused')
        self.db.control(self.run, 'running')
        with patch('job_assistant.app_executor.executor_action') as execute:
            result = handle_app_request(self.db, {'id': 'x', 'method': 'executor_action', 'params': {
                'run_id': self.run, 'control_generation': old, 'request': {'operation': 'status'}}})
        self.assertFalse(result['ok'])
        execute.assert_not_called()


if __name__ == '__main__':
    unittest.main()
