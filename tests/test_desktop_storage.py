"""迁移只使用隔离的合成资料，核对旧数据与新路径。"""
from contextlib import closing, redirect_stdout
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'apps/worker/scripts'))
from job_assistant.ai_selection import digest
from job_assistant.app_runs import preview_demo, preview_run, start_run
from job_assistant.cli import main, verify_materials
from job_assistant.dashboard import Dashboard
from job_assistant.desktop_storage import migrate_desktop_storage
from job_assistant.profiles import extract_resume_profile, get_profile, get_resume, import_resume, save_personal_info
from job_assistant.store import Store, packed


class DesktopStorageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.old = self.root / '旧目录' / 'desktop'
        self.new = self.root / '安装目录' / 'data'

    def test_migrates_history_evidence_and_materials_without_touching_originals(self):
        from openpyxl import load_workbook
        from PIL import Image
        resume = self.root / '用户原件.docx'
        with ZipFile(resume, 'w') as document:
            document.writestr('word/document.xml', '<document/>')
        table = self.root / '用户原始公司表.csv'
        table.write_text('公司名称,申请网站\n合成公司,https://example.invalid/apply\n', encoding='utf-8')
        picture = self.root / '原始合成截图.png'
        Image.new('RGB', (2, 2), 'white').save(picture)
        with closing(Store(self.old)) as store:
            import_resume(store, resume)
            save_personal_info(store, {'name': '测试'})
            config = {'channels': ['web'], 'target_roles': ['工程'], 'execution_mode': 'ai',
                      'employment_type': 'full_time', 'window_title': 'Chrome', 'company_table': str(table)}
            preview = preview_run(store, config)
            run_id = start_run(store, {'ticket': preview['ticket'], 'confirmed': True})['run_id']
            store.control(run_id, 'running')
            attempt = store.begin_attempt(run_id, '合成公司', 'https://example.invalid/apply', '工程', 'web', 'test')
            image_id = store.evidence(attempt, picture, 'pre_submit')
            store.control(run_id, 'paused')
            approval = packed({'original_authorization':'同意', 'revisions':[{'previous_config':preview['config']}]})
            ticket = preview_run(store, config, run_id)['ticket']
            with store.conn:
                store.conn.execute('UPDATE runs SET approval=? WHERE id=?', (approval, run_id))
                store.conn.execute("UPDATE app_sessions SET thread_id='old-thread' WHERE run_id=?", (run_id,))
            simulation = preview_demo(store)
            simulated_run = start_run(store, {'ticket': simulation['ticket'], 'confirmed': True})['run_id']
            store.export()

        chrome_profile = self.old / 'chrome-profile'
        chrome_profile.mkdir()
        (chrome_profile / 'Preferences').write_text('{}', encoding='utf-8')
        (chrome_profile / 'DevToolsActivePort').write_text('stale', encoding='utf-8')
        (self.old / 'chrome-setup-complete').touch()
        diagnostics = self.old / 'browser-diagnostics'
        diagnostics.mkdir()
        (diagnostics / 'report.json').write_text('{}', encoding='utf-8')
        webview = self.root / '旧WebView'
        (webview / 'EBWebView').mkdir(parents=True)
        (webview / 'EBWebView/Preferences').write_text('{}', encoding='utf-8')
        original_db = (self.old / 'records.sqlite3').read_bytes()

        migration = migrate_desktop_storage(self.old, self.new, webview)
        self.assertEqual(migration['data_root'], str(self.new.resolve()))
        self.assertTrue(migration['migrated'])
        self.assertEqual((self.old / 'records.sqlite3').read_bytes(), original_db)
        self.assertEqual((chrome_profile / 'DevToolsActivePort').read_text(), 'stale')
        self.assertTrue((self.new / 'browser-diagnostics/report.json').is_file())
        self.assertTrue((self.new / 'chrome-profile/Preferences').is_file())
        self.assertFalse((self.new / 'chrome-profile/DevToolsActivePort').exists())
        self.assertFalse((self.new / 'chrome-setup-complete').exists())
        self.assertTrue((self.new / 'webview/EBWebView/Preferences').is_file())

        with closing(Store(self.new)) as store:
            self.assertEqual(Dashboard(store.root).image(store, 'evidence', image_id), picture.read_bytes())
            new_config = verify_materials(store, run_id)
            self.assertEqual(Path(new_config['company_table']), table)
            self.assertTrue(Path(new_config['profile_path']).is_relative_to(self.new))
            self.assertEqual(get_profile(store)['profile']['name'], '测试')
            self.assertEqual(Path(get_resume(store)['path']).read_bytes(), resume.read_bytes())
            history = json.loads(store.one('runs', run_id)['approval'])
            self.assertEqual(history['revisions'][0]['previous_config']['resume_path'], new_config['resume_path'])
            saved_preview = store.conn.execute('SELECT config FROM app_previews WHERE ticket=?', (ticket,)).fetchone()[0]
            self.assertEqual(json.loads(saved_preview)['revision']['config_digest'], digest(new_config))
            self.assertIsNone(store.conn.execute('SELECT thread_id FROM app_sessions WHERE run_id=?', (run_id,)).fetchone()[0])
            new_simulation = verify_materials(store, simulated_run)
            self.assertTrue(Path(new_simulation['company_table']).is_relative_to(self.new))
            sim_page = Path(new_simulation['company_table']).with_name('index.html')
            self.assertEqual(new_simulation['simulation_url'], sim_page.as_uri())
        workbook = load_workbook(self.new / 'results.xlsx')
        self.addCleanup(workbook.close)
        self.assertEqual(workbook['网申记录']['L2'].hyperlink.target, (self.new / 'evidence' / attempt).as_uri())

    def test_existing_or_failed_destination_preserves_source(self):
        self.old.mkdir(parents=True)
        self.new.mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, '已有应用资料'):
            migrate_desktop_storage(self.old, self.new)
        untouched = self.new.with_name('失败目标')
        (self.old / 'records.sqlite3').write_bytes(b'corrupt sample')
        with self.assertRaises(sqlite3.Error):
            migrate_desktop_storage(self.old, untouched)
        self.assertFalse(untouched.exists())
        self.assertEqual((self.old / 'records.sqlite3').read_bytes(), b'corrupt sample')

    @unittest.skipUnless(sys.platform == 'win32', 'Windows 长路径格式')
    def test_reads_migrated_paths_with_both_windows_prefixes(self):
        from PIL import Image
        resume = self.root / '示例简历.docx'
        with ZipFile(resume, 'w') as document:
            document.writestr('word/document.xml',
                              '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                              '<w:p><w:r><w:t>姓名：测试</w:t></w:r></w:p></w:document>')
        picture = self.root / '示例截图.png'
        Image.new('RGB', (2, 2), 'white').save(picture)
        with closing(Store(self.old)) as store:
            run = store.create_run({'channels': ['web']}, '同意')
            store.control(run, 'running')
            attempt = store.begin_attempt(run, '合成公司', 'https://example.invalid', '测试', 'web', 'fixture')
            image = store.evidence(attempt, picture, 'pre_submit')
            save_personal_info(store, {'name': '原来保存的资料'})
            original = str(self.old / 'evidence' / attempt / (image + '.png'))
            with store.conn:
                store.conn.execute('UPDATE evidence SET path=? WHERE id=?', ('\\\\?\\' + original, image))

        migrate_desktop_storage(self.old, self.new)
        long_root = Path('\\\\?\\' + str(self.new))
        with closing(Store(long_root)) as store:
            import_resume(store, resume)
        pointer = (self.new / 'profiles/current.json').read_bytes()
        for root in (self.new, long_root):
            with self.subTest(root=root), closing(Store(root)) as store:
                self.assertEqual(get_profile(store)['profile']['name'], '原来保存的资料')
                self.assertEqual(Path(get_resume(store)['path']).read_bytes(), resume.read_bytes())
                self.assertEqual(extract_resume_profile(store)['profile']['name'], '测试')
                self.assertEqual(Dashboard(store.root).image(store, 'evidence', image), picture.read_bytes())
        self.assertEqual((self.new / 'profiles/current.json').read_bytes(), pointer)

        with closing(Store(long_root)) as store:
            with store.conn:
                store.conn.execute('UPDATE evidence SET path=? WHERE id=?', (str(picture), image))
            with self.assertRaisesRegex(ValueError, '图片路径不在记录目录内'):
                Dashboard(store.root).image(store, 'evidence', image)

    def test_native_cli_fresh_install_creates_data_directory(self):
        args = ['migrate-desktop-storage', '--source', str(self.old), '--destination', str(self.new)]
        with patch.dict('os.environ', {'JOB_ASSISTANT_EMBEDDED': '0'}), redirect_stdout(io.StringIO()):
            self.assertEqual(main(args), 1)
        self.assertFalse(self.new.exists())
        with patch.dict('os.environ', {'JOB_ASSISTANT_EMBEDDED': '1'}), redirect_stdout(io.StringIO()) as out:
            self.assertEqual(main(args), 0)
        self.assertTrue(self.new.is_dir())
        self.assertFalse(json.loads(out.getvalue())['result']['migrated'])


if __name__ == '__main__':
    unittest.main()
