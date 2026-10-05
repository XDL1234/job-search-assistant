import json
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'skills/job-search-assistant/scripts/run.py'
sys.path.insert(0, str(SCRIPT.parent))


class CliTests(unittest.TestCase):
    def test_watching_allows_boss_reply_input_but_rejects_web_filling(self):
        from job_assistant.cli import dispatch
        from job_assistant.store import Store
        from job_assistant.inputs import digest_file
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            profile = root / 'profile.json'
            profile.write_text('{}')
            resume = root / 'resume.pdf'
            resume.write_bytes(b'example')
            db = Store(root)
            try:
                config = {'channels': ['boss','web'], 'max_contacts': 1, 'profile_path': str(profile),
                          'profile_sha256': digest_file(profile), 'resume_path': str(resume), 'resume_sha256': digest_file(resume)}
                run = db.create_run(config, '确认')
                boss = db.begin_attempt(run, '甲', 'https://www.zhipin.com', '工程师', 'boss', '1')
                web = db.begin_attempt(run, '乙', 'https://example.com', '工程师', 'web', '2')
                db.observe_message('hr', 'm1', '多久到岗？')
                db.control(run, 'watching')
                frame = root / 'frames' / ('a' * 32 + '.png')
                frame.parent.mkdir()
                frame.with_suffix('.json').write_text(json.dumps({'captured_at': time.time()}))
                request = {'operation': 'act', 'run_id': run, 'purpose': 'fill', 'attempt_id': boss,
                           'recipient': 'hr', 'message_key': 'm1', 'frame': str(frame), 'input': {'op': 'type', 'text': '两周'}}
                with patch('job_assistant.desktop.Desktop.act', return_value={'observed': True}):
                    self.assertEqual(dispatch(db, request), {'observed': True})
                    request['attempt_id'] = web
                    with self.assertRaises(ValueError):
                        dispatch(db, request)
            finally:
                db.close()

    def test_start_requires_actual_materials_and_snapshot_is_saved(self):
        self.assertTrue(SCRIPT.is_file(), '统一命令入口尚未实现')
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            profile = directory / 'profile.json'
            profile.write_text('{"name":"测试用户"}', encoding='utf-8')
            resume = directory / 'resume.pdf'
            resume.write_bytes(b'%PDF-test')
            request = {'operation': 'start', 'approval': '本轮同意', 'config': {
                'channels': ['boss'], 'target_roles': ['嵌入式软件工程师'], 'boss_mode': 'filtered',
                'max_contacts': 2, 'resume_path': str(resume), 'profile_path': str(profile),
                'unknown_reply_mode': 'ai'}}
            path = directory / 'request.json'
            path.write_text(json.dumps(request), encoding='utf-8')
            output = subprocess.run([sys.executable, '-X', 'utf8', str(SCRIPT), '--root', str(directory / 'data'),
                                     'request', '--file', str(path)], capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(output.returncode, 0, output.stderr + output.stdout)
            result = json.loads(output.stdout)
            self.assertTrue(result['ok'])
            self.assertTrue(result['result']['run_id'])
            self.assertTrue((directory / 'data/records.sqlite3').exists())
            resume.write_bytes(b'changed')
            request = {'operation': 'verify_materials', 'run_id': result['result']['run_id']}
            path.write_text(json.dumps(request), encoding='utf-8')
            output = subprocess.run([sys.executable, '-X', 'utf8', str(SCRIPT), '--root', str(directory / 'data'),
                                     'request', '--file', str(path)], capture_output=True, text=True, encoding='utf-8')
            self.assertNotEqual(output.returncode, 0)


if __name__ == '__main__':
    unittest.main()
