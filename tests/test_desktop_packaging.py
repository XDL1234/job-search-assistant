"""新浏览器运行时按白名单打包，复用资源必须与当前源码及哈希一致。"""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('desktop_packaging', ROOT/'tools/build_desktop.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class DesktopPackagingTests(unittest.TestCase):
    def agent_fixture(self, directory):
        directory.mkdir(parents=True)
        (directory/'agent-browser.exe').write_bytes(b'controlled-runtime-fixture')
        (directory/'LICENSE-APACHE-2.0.txt').write_text('Apache License', encoding='utf-8')
        (directory/'NOTICE.txt').write_bytes((ROOT/'tools/vendor/agent-browser-NOTICE.txt').read_bytes())
        lock = json.loads((ROOT/'tools/vendor/agent-browser-lock.json').read_text())
        capabilities = {key: True for key in ['command_auth', 'stream_auth', 'stream_read_only',
                                              'stream_http_disabled', 'jpeg_ack_pacing', 'existing_daemon_only']}
        manifest = {**lock, 'patch_sha256': hashlib.sha256((ROOT/'tools/vendor/agent-browser-patch.json').read_bytes()
                    + (ROOT/'tools/vendor/agent-browser-embedded.rs').read_bytes()).hexdigest(),
                    'exe_sha256': build.file_hash(directory/'agent-browser.exe'),
                    'executable': 'agent-browser.exe', 'capabilities': capabilities}
        (directory/'runtime-manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        probe = {'version': lock['version'], 'job_assistant_patch': lock['patch_level'],
                 'embedded_auth': True, 'read_only': True, 'capabilities': capabilities}
        return manifest, probe

    def test_copy_uses_only_runtime_allowlist(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root/'source'
            manifest, probe = self.agent_fixture(source)
            (source/'agent-browser.json').write_text('{"token":"private"}')
            (source/'security-smoke.json').write_text('{}')
            (source/'frames').mkdir()
            (source/'frames/private.png').write_bytes(b'private')
            resources = root/'resources'
            resources.mkdir()
            with patch.object(build, 'RESOURCES', resources), patch.object(build.subprocess, 'check_output', return_value=json.dumps(probe)):
                self.assertEqual(build.copy_agent_browser(source), manifest)
            self.assertEqual({p.name for p in (resources/'agent-browser').iterdir()}, set(build.AGENT_FILES))

    def test_modified_executable_is_rejected_before_probe(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)/'runtime'
            self.agent_fixture(root)
            (root/'agent-browser.exe').write_bytes(b'tampered')
            with patch.object(build.subprocess, 'check_output') as probe:
                with self.assertRaisesRegex(ValueError, 'SHA-256'):
                    build.verify_agent_browser(root)
                probe.assert_not_called()

    def test_private_extra_manifest_field_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)/'runtime'
            manifest, _ = self.agent_fixture(root)
            manifest['token'] = 'must-never-ship'
            (root/'runtime-manifest.json').write_text(json.dumps(manifest))
            with self.assertRaises(ValueError):
                build.verify_agent_browser(root)

    def test_missing_actual_auth_capability_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)/'runtime'
            _, probe = self.agent_fixture(root)
            probe['embedded_auth'] = False
            with patch.object(build.subprocess, 'check_output', return_value=json.dumps(probe)):
                with self.assertRaisesRegex(ValueError, '能力'):
                    build.verify_agent_browser(root)

    def test_published_runtime_rejects_unknown_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)/'runtime'
            self.agent_fixture(root)
            (root/'unexpected.txt').write_text('not part of the runtime')
            with self.assertRaisesRegex(ValueError, '白名单'):
                build.verify_agent_browser(root, published=True)

    def test_reuse_rejects_old_extension(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'browser-extension').mkdir()
            with patch.object(build, 'RESOURCES', root):
                with self.assertRaisesRegex(ValueError, '旧浏览器扩展'):
                    build.verify_reused_resources()

    def test_reuse_rejects_changed_resource_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'worker').mkdir()
            exe = root/'worker/worker.exe'
            exe.write_bytes(b'original')
            original = {'codex': build.CODEX_VERSION, 'files': build.resource_manifest(root)}
            (root/'resource-manifest.json').write_text(json.dumps(original))
            exe.write_bytes(b'changed')
            with patch.object(build, 'RESOURCES', root):
                with self.assertRaisesRegex(ValueError, '校验清单'):
                    build.verify_reused_resources()

    def test_resource_manifest_rejects_connection_credentials_and_test_capture(self):
        for filename in ['agent-browser.json', 'browser-bridge.json', 'security-smoke.json', 'records.db',
                         'records.sqlite3-wal', 'records.sqlite3-shm', 'records.db-journal', 'capture.jpg']:
            with self.subTest(filename=filename), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                (root/'agent-browser').mkdir()
                (root/'agent-browser'/filename).write_text('{}')
                with self.assertRaises(ValueError):
                    build.resource_manifest(root)

    def test_nested_resource_manifest_is_covered_by_checksums(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'worker').mkdir()
            nested = root/'worker/resource-manifest.json'
            nested.write_bytes(b'{}')
            (root/'resource-manifest.json').write_bytes(b'{}')
            self.assertEqual(build.resource_manifest(root), [
                {'path': 'worker/resource-manifest.json', 'size': 2, 'sha256': build.file_hash(nested)}
            ])

    def test_reuse_checks_current_skill_fingerprint(self):
        import sys
        sys.path.insert(0, str(ROOT/'apps/worker/scripts'))
        from job_assistant.bootstrap import tree_hash
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            runtime, probe = self.agent_fixture(root/'agent-browser')
            (root/'worker').mkdir()
            worker = root/'worker/worker.exe'
            worker.write_bytes(b'worker')
            metadata = {'source_sha256': tree_hash(ROOT/'apps/worker'),
                        'worker_sha256': build.file_hash(worker), 'pyinstaller': build.PYINSTALLER_VERSION}
            (root/'worker/build-manifest.json').write_text(json.dumps(metadata))
            previous = {'codex': build.CODEX_VERSION, 'agent_browser': runtime, 'files': build.resource_manifest(root)}
            (root/'resource-manifest.json').write_text(json.dumps(previous))
            def output(args, **kwargs):
                return 'codex-cli ' + build.CODEX_VERSION if args[-1] == '--version' else json.dumps(probe)
            with patch.object(build, 'RESOURCES', root), patch.object(build.subprocess, 'check_output', side_effect=output):
                self.assertEqual(build.verify_reused_resources(), runtime)
                with patch('job_assistant.bootstrap.tree_hash', return_value='changed-after-freeze'):
                    with self.assertRaisesRegex(ValueError, '工作进程源码'):
                        build.verify_reused_resources()

    def test_bundle_configuration_replaces_extension_with_native_runtime(self):
        config = json.loads((ROOT/'apps/desktop/src-tauri/tauri.bundle.conf.json').read_text())['bundle']['resources']
        self.assertEqual(config['resources/agent-browser/'], 'runtime/agent-browser/')
        self.assertFalse(any('browser-extension' in name for name in config))


if __name__ == '__main__':
    unittest.main()
