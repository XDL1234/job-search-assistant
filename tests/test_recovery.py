import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/job-search-assistant/scripts'))

from job_assistant.store import Store
from job_assistant import desktop


class RecoveryTests(unittest.TestCase):
    def test_different_data_roots_cannot_control_same_desktop_concurrently(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            with desktop.desktop_lock(first):
                with self.assertRaises(ValueError):
                    with desktop.desktop_lock(second):
                        self.fail('两个数据目录同时取得桌面输入权')

    def test_new_authorized_run_can_reply_to_existing_attempt(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Store(folder)
            try:
                first = db.create_run({'channels': ['boss'], 'max_contacts': 2}, '第一轮确认')
                attempt = db.begin_attempt(first, '甲', 'https://www.zhipin.com/job/1', '工程师', 'boss', '1')
                db.control(first, 'stopped')
                second = db.create_run({'channels': ['boss'], 'max_contacts': 2}, '第二轮回复确认')
                self.assertTrue(hasattr(db, 'attach_attempt'), '缺少跨轮次继续处理接口')
                db.attach_attempt(second, attempt)
                db.observe_message('hr', 'm1', '你好')
                action = db.prepare_action(second, attempt, 'reply', 'hr', {'message_key': 'm1', 'text': '您好'})
                self.assertEqual(db.one('actions', action)['run_id'], second)
                self.assertEqual(db.one('attempts', attempt)['run_id'], first)
            finally:
                db.close()

    def test_file_dialog_owned_by_bound_browser_is_accepted(self):
        self.assertTrue(hasattr(desktop, 'validate_binding'), '缺少文件对话框绑定检查')
        binding = {'hwnd': 10, 'pid': 100, 'exe': 'chrome.exe'}
        dialog = {'hwnd': 20, 'root_owner': 10, 'pid': 100, 'exe': 'chrome.exe'}
        desktop.validate_binding(binding, dialog)
        for change in ({'root_owner': 30}, {'pid': 101}, {'exe': 'other.exe'}):
            with self.assertRaises(ValueError):
                desktop.validate_binding(binding, {**dialog, **change})

    def test_rolling_frames_never_remove_archived_evidence_or_user_files(self):
        self.assertTrue(hasattr(desktop, 'prune_frames'), '缺少观察截图轮转')
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            frames = root / 'frames'
            frames.mkdir()
            for i in range(5):
                (frames / f'{i:032x}.png').write_bytes(b'frame')
                (frames / f'{i:032x}.json').write_text('{}')
            other = frames / 'my-personal-image.png'
            other.write_bytes(b'keep')
            evidence = root / 'evidence.png'
            evidence.write_bytes(b'keep')
            desktop.prune_frames(frames, keep=2)
            self.assertEqual(len(list(frames.glob('*.json'))), 2)
            self.assertEqual(other.read_bytes(), b'keep')
            self.assertEqual(evidence.read_bytes(), b'keep')


if __name__ == '__main__':
    unittest.main()
