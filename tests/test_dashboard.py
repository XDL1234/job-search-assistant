"""桌面台账、控制和待办回归。"""
import importlib.util
import json
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'apps/worker/scripts'))
from job_assistant.cli import dispatch, start
from job_assistant.panel_state import queue_control
from job_assistant.store import Store


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('job_assistant.dashboard'), '本地面板尚未实现')
        from job_assistant.dashboard import Dashboard
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'profile.json').write_text('{}')
        (self.root / 'resume.pdf').write_bytes(b'fixture')
        self.config = {'channels': ['boss'], 'boss_mode': 'filtered', 'max_contacts': 2,
                       'target_roles': ['嵌入式工程师'], 'profile_path': str(self.root / 'profile.json'),
                       'resume_path': str(self.root / 'resume.pdf')}
        self.db = Store(self.root)
        self.addCleanup(self.db.close)
        self.run = start(self.db, {'config': self.config, 'approval': '测试授权'})['run_id']
        self.panel = Dashboard(self.root)

    def test_pause_immediately_blocks_executor_and_resume_waits_for_ack(self):
        command = queue_control(self.db, {'run_id': self.run, 'action': 'paused'})
        self.assertEqual(command['status'], 'applied')
        with self.assertRaises(ValueError):
            self.db.check_run(self.run)
        queue_control(self.db, {'run_id': self.run, 'action': 'running'})
        self.assertEqual(self.db.one('runs', self.run)['status'], 'paused')
        dispatch(self.db, {'operation': 'tick', 'run_id': self.run})
        self.assertEqual(self.db.one('runs', self.run)['status'], 'running')
        self.assertIsNotNone(self.panel.snapshot(self.db)['activity'][0]['fresh_after'])

    def test_stop_cancels_pending_resume_and_cannot_be_revived(self):
        queue_control(self.db, {'run_id': self.run, 'action': 'paused'})
        queue_control(self.db, {'run_id': self.run, 'action': 'running'})
        queue_control(self.db, {'run_id': self.run, 'action': 'stopped'})
        with self.assertRaises(ValueError):
            dispatch(self.db, {'operation': 'tick', 'run_id': self.run})
        self.assertEqual(self.db.one('runs', self.run)['status'], 'stopped')
        self.assertFalse(any(c['status'] == 'pending' for c in self.panel.snapshot(self.db)['commands']))

    def test_resume_rechecks_materials_and_rejects_previous_screenshot(self):
        queue_control(self.db, {'run_id': self.run, 'action': 'paused'})
        queue_control(self.db, {'run_id': self.run, 'action': 'running'})
        (self.root / 'resume.pdf').write_bytes(b'changed')
        with self.assertRaises(ValueError):
            dispatch(self.db, {'operation': 'tick', 'run_id': self.run})
        self.assertEqual(self.db.one('runs', self.run)['status'], 'paused')
        (self.root / 'resume.pdf').write_bytes(b'fixture')
        frame = self.root / 'frames' / ('a' * 32 + '.png')
        frame.parent.mkdir()
        frame.with_suffix('.json').write_text(json.dumps({'captured_at': time.time() - 10}))
        queue_control(self.db, {'run_id': self.run, 'action': 'running'})
        dispatch(self.db, {'operation': 'tick', 'run_id': self.run})
        with self.assertRaisesRegex(ValueError, '重新截图'):
            dispatch(self.db, {'operation': 'act', 'run_id': self.run, 'purpose': 'navigate',
                               'frame': str(frame), 'input': {'op': 'key', 'keys': ['tab']}})

    def test_manual_note_never_unlocks_interview_or_creates_send(self):
        self.db.observe_message('hr-1', 'm1', '周五面试方便吗？')
        item = self.db.handoff('hr-1', '面试邀约')
        self.panel.attention(self.db, {'id': item['id'], 'action': 'note', 'message_key': 'm1', 'text': '我需要查一下时间'})
        self.assertEqual(self.db.one('conversations', 'hr-1')['status'], 'human')
        self.assertEqual(self.db.snapshot()['actions'], [])
        self.assertEqual(self.panel.snapshot(self.db)['notes'][0]['text'], '我需要查一下时间')
        self.db.observe_message('hr-1', 'm2', '时间改周六可以吗？')
        with self.assertRaises(ValueError):
            self.panel.attention(self.db, {'id': item['id'], 'action': 'resume', 'message_key': 'm1', 'confirmed': True})
        self.panel.attention(self.db, {'id': item['id'], 'action': 'resume', 'message_key': 'm2', 'confirmed': True})
        self.assertEqual(self.db.one('conversations', 'hr-1')['status'], 'auto')

    def test_job_review_opens_only_saved_matching_boss_url_and_saves_note_with_resolve(self):
        key = '/job_detail/fixture.html'
        url = 'https://www.zhipin.com' + key + '?securityId=fixture'
        review = {'company': '某公司', 'job': '某岗位', 'city': '深圳', 'reason': '核对经验', 'url': url}
        item = self.db.attention(key, '岗位精筛待人工查看', job_review=review)
        other = self.db.attention(key, '其他原因', job_review={**review, 'url': 'https://example.test' + key})
        self.assertEqual(self.panel.snapshot(self.db)['attention'][0]['job_review'], review)
        with patch('job_assistant.dashboard.webbrowser.open_new_tab', return_value=True) as opened:
            self.assertTrue(self.panel.attention(self.db, {'id': item['id'], 'action': 'open_job'})['opened'])
            opened.assert_called_once_with(url)
            with self.assertRaisesRegex(ValueError, '授权'):
                self.panel.attention(self.db, {'id': other['id'], 'action': 'open_job'})
            opened.assert_called_once()

        finish = {'id': item['id'], 'action': 'resolve', 'text': '先保留该岗位资料'}
        with self.assertRaisesRegex(ValueError, '明确确认'):
            self.panel.attention(self.db, finish)
        self.assertEqual(self.db.conn.execute('SELECT COUNT(*) FROM attention_notes').fetchone()[0], 0)
        self.panel.attention(self.db, {**finish, 'confirmed': True})
        self.assertEqual(self.db.conn.execute('SELECT resolved FROM attention WHERE id=?', (item['id'],)).fetchone()[0], 1)
        self.assertEqual(self.db.conn.execute('SELECT text FROM attention_notes').fetchone()[0], finish['text'])
        self.assertEqual(self.db.snapshot()['actions'], [])

    def test_cli_watching_resume_requires_material_check_and_new_frame(self):
        self.db.control(self.run, 'paused')
        (self.root / 'resume.pdf').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, '变更'):
            dispatch(self.db, {'operation': 'control', 'run_id': self.run, 'status': 'watching'})
        self.assertEqual(self.db.one('runs', self.run)['status'], 'paused')
        (self.root / 'resume.pdf').write_bytes(b'fixture')
        dispatch(self.db, {'operation': 'control', 'run_id': self.run, 'status': 'watching'})
        self.assertIsNotNone(self.panel.snapshot(self.db)['activity'][0]['fresh_after'])

    def test_inflight_action_rechecks_resume_generation_before_input(self):
        frame = self.root / 'frames' / ('b' * 32 + '.png')
        frame.parent.mkdir()
        frame.with_suffix('.json').write_text(json.dumps({'captured_at': time.time()}))
        def interrupted_action(frame_path, operation, check):
            queue_control(self.db, {'run_id': self.run, 'action': 'paused'})
            queue_control(self.db, {'run_id': self.run, 'action': 'running'})
            dispatch(self.db, {'operation': 'tick', 'run_id': self.run})
            check()
            return {'should_not_reach': True}
        with patch('job_assistant.desktop.Desktop.act', side_effect=interrupted_action):
            with self.assertRaisesRegex(ValueError, '重新截图'):
                dispatch(self.db, {'operation': 'act', 'run_id': self.run, 'purpose': 'navigate',
                                   'frame': str(frame), 'input': {'op': 'key', 'keys': ['tab']}})

    def test_snapshot_poll_does_not_fabricate_executor_activity(self):
        self.assertEqual(self.panel.snapshot(self.db)['activity'], [])
        dispatch(self.db, {'operation': 'tick', 'run_id': self.run})
        before = self.panel.snapshot(self.db)['activity']
        self.panel.snapshot(self.db)
        self.assertEqual(self.panel.snapshot(self.db)['activity'], before)


if __name__ == '__main__':
    unittest.main()
