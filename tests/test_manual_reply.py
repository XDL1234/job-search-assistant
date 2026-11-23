"""人工确认仅授权一条指定回复，不解除自动回复锁。"""
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'apps/worker/scripts'))
from job_assistant.store import Store


class ManualReplyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.addCleanup(self.store.close)
        self.run = self.store.create_run({'channels': ['boss'], 'max_contacts': 2}, '确认测试')
        self.attempt = self.store.begin_attempt(self.run, '公司', 'https://www.zhipin.com/job_detail/test.html', '岗位', 'boss', 'job')
        self.store.observe_message('chat', 'm1', '周五能来面试吗？')
        self.store.handoff('chat', 'interview')

    def prepare(self, **changes):
        value = dict(run_id=self.run, attempt_id=self.attempt, conversation='chat', message_key='m1', text='请安排周五下午两点，谢谢。', confirmed=True)
        return self.store.prepare_manual_reply(**{**value, **changes})

    def test_confirmed_reply_is_consumed_once_and_lock_stays(self):
        action = self.prepare()
        self.store.claim_action(self.run, action)
        with self.assertRaises(ValueError):
            self.store.claim_action(self.run, action)
        self.assertEqual(self.store.one('conversations', 'chat')['status'], 'human')
        with self.assertRaisesRegex(ValueError, '重复'):
            self.prepare()

    def test_ordinary_prepare_cannot_forge_manual_permission(self):
        with self.assertRaisesRegex(ValueError, '人工'):
            self.store.prepare_action(self.run, self.attempt, 'reply', 'chat', {'message_key': 'm1', 'text': '好', 'manual': True})
        with self.assertRaises(ValueError):
            self.prepare(confirmed=False)

    def test_latest_message_and_expiration_checked_at_claim(self):
        action = self.prepare()
        self.store.observe_message('chat', 'm2', '时间更改了')
        with self.assertRaisesRegex(ValueError, '消息'):
            self.store.claim_action(self.run, action)
        action = self.prepare(message_key='m2')
        with self.store.conn:
            self.store.conn.execute('UPDATE manual_sends SET expires=? WHERE action_id=?', (time.time() - 1, action))
        with self.assertRaises(ValueError):
            self.store.claim_action(self.run, action)

    def test_payload_change_and_pausing_revoke_send(self):
        action = self.prepare()
        with self.store.conn:
            self.store.conn.execute('UPDATE actions SET payload=? WHERE id=?', (json.dumps({'message_key': 'm1', 'text': '被改写'}), action))
        with self.assertRaises(ValueError):
            self.store.claim_action(self.run, action)
        self.store.control(self.run, 'paused')
        with self.assertRaises(ValueError):
            self.prepare()

    def test_boss_model_cannot_use_manual_or_coordinate_send_paths(self):
        from job_assistant.app_executor import executor_action
        config = {'channels': ['boss'], 'employment_type': 'internship'}
        with patch('job_assistant.app_executor.app_session'), patch('job_assistant.app_executor.verify_materials', return_value=config):
            for operation in ('manual_reply', 'attention_update', 'resume_conversation', 'bind', 'capture', 'act'):
                with self.subTest(operation=operation), self.assertRaises(ValueError):
                    executor_action(self.store, self.run, {'operation': operation})

    def test_expired_manual_confirmation_stays_invalid_after_unlock(self):
        action = self.prepare()
        with self.store.conn:
            self.store.conn.execute('UPDATE manual_sends SET expires=0 WHERE action_id=?', (action,))
        self.store.resume_conversation('chat', '用户恢复后续消息自动处理')
        with self.assertRaisesRegex(ValueError, '过期'):
            self.store.claim_action(self.run, action)

    def test_taking_over_ai_attention_neither_sends_nor_resolves(self):
        from job_assistant.dashboard import Dashboard
        self.store.resume_conversation('chat', '测试重置')
        item = self.store.attention('chat', '等待 AI 处理')
        request = {'id': item['id'], 'action': 'handoff', 'message_key': 'm1', 'confirmed': True}
        panel = Dashboard(self.store.root)
        with self.assertRaises(ValueError):
            panel.attention(self.store, {**request, 'confirmed': False})
        panel.attention(self.store, request)
        self.assertEqual(self.store.one('conversations', 'chat')['status'], 'human')
        self.assertEqual(self.store.conn.execute('SELECT count(*) FROM actions').fetchone()[0], 0)
        self.assertEqual(self.store.conn.execute('SELECT resolved FROM attention WHERE id=?', (item['id'],)).fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
