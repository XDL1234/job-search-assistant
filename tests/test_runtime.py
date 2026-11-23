import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'apps/worker/scripts'))


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
        valid = {'schema_version': 1, 'python': '>=3.11', 'packages': [{'name': 'Pillow', 'version': '11.3.0'}]}
        validate_manifest(valid)
        bad = {**valid, 'packages': [{'name': 'Pillow', 'version': 'latest'}]}
        with self.assertRaises(ValueError):
            validate_manifest(bad)
        bad = {**valid, 'sources': ['https://unknown.example/install.ps1']}
        with self.assertRaises(ValueError):
            validate_manifest(bad)

    def test_packaging_includes_worker_sources_and_excludes_user_data_and_old_plugin_resources(self):
        from job_assistant.bootstrap import source_files
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundled = {'dependencies.json', 'assets/simulation.html', 'scripts/run.py'}
            excluded = {'SKILL.md', 'agents/openai.yaml', 'data/resume.pdf', 'scripts/records.sqlite3',
                        'assets/dashboard/index.html'}
            for relative in bundled | excluded:
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'fixture')
            self.assertEqual({path.relative_to(root).as_posix() for path in source_files(root)}, bundled)


if __name__ == '__main__':
    unittest.main()
