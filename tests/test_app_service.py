"""应用 IPC 白名单与只读查询，不触碰真实用户数据。"""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'apps/worker/scripts'))
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

    def test_task_timing_uses_first_applied_start_and_latest_confirmed_activity(self):
        run = self.store.create_run({'channels': ['web']}, '测试授权')
        with self.store.conn:
            self.store.conn.executemany('INSERT INTO panel_commands VALUES (?,?,?,?,?,?,?)', [
                ('cancelled', run, 'running', 'cancelled', '', '2026-01-01', '2026-01-01'),
                ('started', run, 'running', 'applied', '', '2026-01-02', '2026-01-02'),
                ('paused', run, 'paused', 'applied', '', '2026-01-03', '2026-01-03'),
            ])
        row = self.call('snapshot')['result']['runs'][0]
        self.assertEqual((row['started_at'], row['updated_at']), ('2026-01-02', '2026-01-03'))

    def test_envelope_validation(self):
        for request in [None, {}, {'id': 'x', 'method': 'snapshot', 'params': []}]:
            self.assertFalse(self.handle(self.store, request)['ok'])

    def test_export_returns_existing_file(self):
        result = self.call('export_records')
        self.assertTrue(result['ok'])
        self.assertTrue(Path(result['result']['path']).is_file())

    def test_export_to_selected_absolute_excel_path(self):
        from openpyxl import load_workbook
        target = Path(self.temp.name).resolve() / '指定导出位置.xlsx'
        target.write_bytes(b'previous workbook')
        result = self.call('export_records_to', destination=str(target))
        self.assertTrue(result['ok'], result)
        self.assertEqual(Path(result['result']['path']), target)
        with target.open('rb') as source:
            self.assertIn('BOSS记录', load_workbook(source).sheetnames)
        self.assertFalse((self.store.root / 'results.xlsx').exists())
        self.assertFalse(self.call('export_records_to', destination='relative.xlsx')['ok'])
        invalid = target.with_suffix('.txt')
        self.assertFalse(self.call('export_records_to', destination=str(invalid))['ok'])
        self.assertFalse(invalid.exists())

    def test_image_requires_record_identity(self):
        result = self.call('read_image', kind='frame', identity='../profile')
        self.assertFalse(result['ok'])

    def test_export_preserves_source_company_workbooks(self):
        from job_assistant.migrations import migrate_app
        from openpyxl import Workbook
        paths = [self.store.root / (name + '.xlsx') for name in ('本轮公司表', '上轮公司表', '预览公司表')]
        book = Workbook()
        book.active.append(['公司名称', '申请网站'])
        for path in paths:
            book.save(path)
        book.close()
        run = self.store.create_run({'channels': ['web'], 'company_table': str(paths[0])}, '确认')
        migrate_app(self.store)
        with self.store.conn:
            approval = {'revisions': [{'previous_config': {'company_table': str(paths[1])}}]}
            self.store.conn.execute('UPDATE runs SET approval=? WHERE id=?', (json.dumps(approval), run))
            preview = {'config': {'company_table': str(paths[2])}, 'revision': {'run_id': run}}
            self.store.conn.execute('INSERT INTO app_previews VALUES (?,?,?)', ('test', json.dumps(preview), 9999999999))
        for path in paths:
            with self.subTest(path=path):
                original = path.read_bytes()
                result = self.call('export_records_to', destination=str(path))
                self.assertFalse(result['ok'])
                self.assertIn('公司表', result['error']['message'])
                self.assertEqual(original, path.read_bytes())

    def test_health_has_no_credentials(self):
        result = self.call('health')
        self.assertTrue(result['ok'])
        self.assertNotIn('token', json.dumps(result))


if __name__ == '__main__':
    unittest.main()
