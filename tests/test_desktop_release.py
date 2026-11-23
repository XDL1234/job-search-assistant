"""安装资源必须自包含，版本一致且不包含用户数据。"""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'apps/worker/scripts'))


class DesktopReleaseTests(unittest.TestCase):
    def test_frozen_worker_resources_are_inside_bundle(self):
        from job_assistant import bootstrap
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(sys, 'frozen', True, create=True), patch.object(sys, '_MEIPASS', temp, create=True):
                self.assertEqual(bootstrap.worker_root(), Path(temp) / 'worker-data')
        self.assertEqual(bootstrap.worker_root(), ROOT / 'apps/worker')

    def test_desktop_versions_and_installer_are_consistent(self):
        import tomllib
        config = json.loads((ROOT/'apps/desktop/src-tauri/tauri.conf.json').read_text(encoding='utf-8'))
        package = json.loads((ROOT/'apps/desktop/package.json').read_text())
        cargo = tomllib.loads((ROOT/'apps/desktop/src-tauri/Cargo.toml').read_text())
        self.assertRegex(config['version'], r'^\d+\.\d+\.\d+$')
        self.assertEqual(package['version'], config['version'])
        self.assertEqual(cargo['package']['version'], config['version'])
        self.assertEqual(config['bundle']['targets'], ['msi', 'nsis'])
        self.assertEqual(config['productName'], '求职助手')
        self.assertEqual(config['bundle']['windows']['wix']['language'], 'zh-CN')
        self.assertFalse(config['bundle']['windows']['allowDowngrades'])
        self.assertEqual(config['bundle']['windows']['nsis']['installMode'], 'currentUser')
        self.assertEqual(config['bundle']['windows']['nsis']['languages'], ['SimpChinese'])

    def test_resource_manifest_rejects_personal_data(self):
        spec = importlib.util.spec_from_file_location('desktop_build', ROOT/'tools/build_desktop.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder/'auth.json').write_text('{}')
            with self.assertRaises(ValueError):
                module.resource_manifest(folder)

    def test_build_only_clears_known_generated_directories(self):
        from unittest.mock import patch
        spec=importlib.util.spec_from_file_location('desktop_build',ROOT/'tools/build_desktop.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp);(folder/'worker').mkdir();(folder/'worker/stale').write_text('old')
            (folder/'keep.txt').write_text('keep')
            with patch.object(module,'RESOURCES',folder):module.clear_generated_resources()
            self.assertFalse((folder/'worker').exists())
            self.assertTrue((folder/'keep.txt').exists())


if __name__ == '__main__':
    unittest.main()
