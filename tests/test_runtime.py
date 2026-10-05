import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/job-search-assistant/scripts'))


class DesktopGuardTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('job_assistant.desktop'), '电脑操作守卫尚未实现')

    def test_stale_frame_and_changed_window_are_rejected(self):
        from job_assistant.desktop import validate_frame
        frame = {'captured_at': 100, 'window': {'hwnd': 1, 'pid': 2, 'rect': [0, 0, 800, 600], 'title': '浏览器'}, 'screen': [1920, 1080]}
        state = {'window': frame['window'], 'screen': [1920, 1080]}
        validate_frame(frame, state, 110)
        with self.assertRaises(ValueError):
            validate_frame(frame, state, 131)
        state['window'] = {**frame['window'], 'hwnd': 4}
        with self.assertRaises(ValueError):
            validate_frame(frame, state, 110)

    def test_coordinates_are_relative_to_window_and_bounded(self):
        from job_assistant.desktop import screen_point
        self.assertEqual(screen_point([100, 80, 900, 680], 20, 30), (120, 110))
        for x, y in [(-1, 20), (800, 20), (2, 600)]:
            with self.assertRaises(ValueError):
                screen_point([100, 80, 900, 680], x, y)


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('job_assistant.bootstrap'), '初始化模块尚未实现')

    def test_dependency_manifest_rejects_floating_or_unverified_sources(self):
        from job_assistant.bootstrap import validate_manifest
        valid = {'schema_version': 1, 'python': '>=3.11', 'packages': [{'name': 'Pillow', 'version': '11.3.0'}], 'skills': []}
        validate_manifest(valid)
        bad = {**valid, 'packages': [{'name': 'Pillow', 'version': 'latest'}]}
        with self.assertRaises(ValueError):
            validate_manifest(bad)
        bad = {**valid, 'skills': [{'name': 'computer-use', 'url': 'https://unknown.example/install.ps1'}]}
        with self.assertRaises(ValueError):
            validate_manifest(bad)

    def test_local_skill_install_does_not_overwrite_different_existing_skill(self):
        from job_assistant.bootstrap import install_skill
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, destination = root / 'source', root / 'installed'
            source.mkdir()
            (source / 'SKILL.md').write_text('original', encoding='utf-8')
            first = install_skill(source, destination)
            self.assertEqual(first['status'], 'installed')
            self.assertEqual(install_skill(source, destination)['status'], 'already_installed')
            (source / 'SKILL.md').write_text('changed', encoding='utf-8')
            with self.assertRaises(ValueError):
                install_skill(source, destination)
            self.assertEqual((destination / 'SKILL.md').read_text(), 'original')

    def test_install_excludes_runtime_data_even_if_source_contains_it(self):
        from job_assistant.bootstrap import install_skill
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source'
            source.mkdir()
            (source / 'SKILL.md').write_text('skill')
            (source / 'data').mkdir()
            (source / 'data/resume.pdf').write_bytes(b'private')
            (source / 'scripts').mkdir()
            (source / 'scripts/records.sqlite3').write_bytes(b'private')
            destination = root / 'installed'
            install_skill(source, destination)
            self.assertFalse((destination / 'data').exists())
            self.assertFalse((destination / 'scripts/records.sqlite3').exists())


if __name__ == '__main__':
    unittest.main()
