"""应用 IPC 白名单与只读查询，不触碰真实用户数据。"""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/job-search-assistant/scripts'))
from job_assistant.store import Store


class AppServiceTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('job_assistant.app_service'), '应用接口尚未实现')
        from job_assistant.app_service import handle_app_request
        self.handle = handle_app_request
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.addCleanup(self.store.close)

    def call(self, method, **params):
        return self.handle(self.store, {'id': 'test-1', 'method': method, 'params': params})

    def test_unknown_method_rejected(self):
        result = self.call('act', input={'op': 'click', 'x': 0, 'y': 0})
        self.assertFalse(result['ok'])
        self.assertEqual(result['id'], 'test-1')
        self.assertEqual(result['error']['code'], 'invalid_request')

    def test_snapshot_is_read_only(self):
        run = self.store.create_run({'channels': ['web']}, '模拟授权')
        before = self.store.conn.total_changes
        result = self.call('snapshot')
        self.assertTrue(result['ok'])
        self.assertEqual(result['result']['runs'][0]['id'], run)
        self.assertEqual(before, self.store.conn.total_changes)
        self.assertEqual(self.store.conn.execute('SELECT COUNT(*) FROM actions').fetchone()[0], 0)

    def test_envelope_validation(self):
        for request in [None, {}, {'id': 'x', 'method': 'snapshot', 'params': []}]:
            self.assertFalse(self.handle(self.store, request)['ok'])

    def test_export_returns_existing_file(self):
        result = self.call('export_records')
        self.assertTrue(result['ok'])
        self.assertTrue(Path(result['result']['path']).is_file())

    def test_image_requires_record_identity(self):
        result = self.call('read_image', kind='frame', identity='../profile')
        self.assertFalse(result['ok'])

    def test_health_has_no_credentials(self):
        result = self.call('health')
        self.assertTrue(result['ok'])
        self.assertNotIn('token', json.dumps(result))


if __name__ == '__main__':
    unittest.main()
