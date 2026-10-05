"""面板控制和本机 HTTP 边界；使用临时资料，不连接招聘网站。"""
import importlib.util
import json
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/job-search-assistant/scripts'))
from job_assistant.cli import dispatch, start
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
        command = self.panel.command(self.db, {'run_id': self.run, 'action': 'paused'})
        self.assertEqual(command['status'], 'applied')
        with self.assertRaises(ValueError):
            self.db.check_run(self.run)
        self.panel.command(self.db, {'run_id': self.run, 'action': 'running'})
        self.assertEqual(self.db.one('runs', self.run)['status'], 'paused')
        dispatch(self.db, {'operation': 'tick', 'run_id': self.run})
        self.assertEqual(self.db.one('runs', self.run)['status'], 'running')
        self.assertIsNotNone(self.panel.snapshot(self.db)['activity'][0]['fresh_after'])

    def test_stop_cancels_pending_resume_and_cannot_be_revived(self):
        self.panel.command(self.db, {'run_id': self.run, 'action': 'paused'})
        self.panel.command(self.db, {'run_id': self.run, 'action': 'running'})
        self.panel.command(self.db, {'run_id': self.run, 'action': 'stopped'})
        with self.assertRaises(ValueError):
            dispatch(self.db, {'operation': 'tick', 'run_id': self.run})
        self.assertEqual(self.db.one('runs', self.run)['status'], 'stopped')
        self.assertFalse(any(c['status'] == 'pending' for c in self.panel.snapshot(self.db)['commands']))

    def test_resume_rechecks_materials_and_rejects_previous_screenshot(self):
        self.panel.command(self.db, {'run_id': self.run, 'action': 'paused'})
        self.panel.command(self.db, {'run_id': self.run, 'action': 'running'})
        (self.root / 'resume.pdf').write_bytes(b'changed')
        with self.assertRaises(ValueError):
            dispatch(self.db, {'operation': 'tick', 'run_id': self.run})
        self.assertEqual(self.db.one('runs', self.run)['status'], 'paused')
        (self.root / 'resume.pdf').write_bytes(b'fixture')
        frame = self.root / 'frames' / ('a' * 32 + '.png')
        frame.parent.mkdir()
        frame.with_suffix('.json').write_text(json.dumps({'captured_at': time.time() - 10}))
        self.panel.command(self.db, {'run_id': self.run, 'action': 'running'})
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
            self.panel.command(self.db, {'run_id': self.run, 'action': 'paused'})
            self.panel.command(self.db, {'run_id': self.run, 'action': 'running'})
            dispatch(self.db, {'operation': 'tick', 'run_id': self.run})
            check()
            return {'should_not_reach': True}
        with patch('job_assistant.desktop.Desktop.act', side_effect=interrupted_action):
            with self.assertRaisesRegex(ValueError, '重新截图'):
                dispatch(self.db, {'operation': 'act', 'run_id': self.run, 'purpose': 'navigate',
                                   'frame': str(frame), 'input': {'op': 'key', 'keys': ['tab']}})

    def test_config_preview_must_be_confirmed_and_material_change_invalidates_it(self):
        preview = self.panel.preview(self.config)
        with self.assertRaises(ValueError):
            self.panel.start(self.db, {'ticket': preview['ticket'], 'confirmed': False})
        (self.root / 'resume.pdf').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, '变更'):
            self.panel.start(self.db, {'ticket': preview['ticket'], 'confirmed': True})
        preview = self.panel.preview(self.config)
        result = self.panel.start(self.db, {'ticket': preview['ticket'], 'confirmed': True})
        self.assertEqual(result['executor'], 'awaiting_session')
        with self.assertRaises(ValueError):
            self.panel.start(self.db, {'ticket': preview['ticket'], 'confirmed': True})

    def test_snapshot_poll_does_not_fabricate_executor_activity(self):
        self.assertEqual(self.panel.snapshot(self.db)['activity'], [])
        dispatch(self.db, {'operation': 'tick', 'run_id': self.run})
        before = self.panel.snapshot(self.db)['activity']
        self.panel.snapshot(self.db)
        self.assertEqual(self.panel.snapshot(self.db)['activity'], before)

    def test_http_requires_token_rejects_foreign_origin_and_serves_no_arbitrary_files(self):
        from job_assistant.dashboard import make_server
        server = make_server(self.root, port=0)
        self.addCleanup(server.server_close)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)
        base = f'http://127.0.0.1:{server.server_port}'
        def fetch(path, token=None, origin=None, data=None):
            headers = {}
            if token: headers['Authorization'] = 'Bearer ' + token
            if origin: headers['Origin'] = origin
            if data is not None: headers['Content-Type'] = 'application/json'
            req = urllib.request.Request(base + path, data=None if data is None else json.dumps(data).encode(), headers=headers)
            return urllib.request.urlopen(req, timeout=3)
        with fetch('/') as response:
            self.assertIn(b'Job Search', response.read())
        for args in [('/api/state',), ('/api/state', server.token, 'https://evil.example'),
                     ('/api/image/frame/../../profile.json', server.token)]:
            with self.assertRaises(urllib.error.HTTPError) as error:
                fetch(*args)
            self.assertIn(error.exception.code, (400, 403, 404))
        with fetch('/api/state', server.token) as response:
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            self.assertEqual(json.load(response)['runs'][0]['id'], self.run)
        with fetch('/api/command', server.token, base, {'run_id': self.run, 'action': 'paused'}) as response:
            self.assertEqual(json.load(response)['status'], 'applied')
        with self.assertRaises(ValueError): self.db.check_run(self.run)
        with fetch('/api/export', server.token, base, {}) as response:
            self.assertTrue(response.read().startswith(b'PK'))


if __name__ == '__main__':
    unittest.main()
