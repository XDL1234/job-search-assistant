"""代理只填补 Codex 子进程环境；不从界面或台账暴露凭据。"""
import json
import os
from contextlib import closing
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'skills/job-search-assistant/scripts'
sys.path.insert(0, str(SCRIPTS))
from job_assistant.native_proxy import _proxy_additions, codex_proxy_environment
from job_assistant.app_service import handle_app_request
from job_assistant.store import Store


class NativeProxyTests(unittest.TestCase):
    def test_fills_missing_protocol_without_overwriting_explicit_or_empty_values(self):
        system = {'http': 'http://system-proxy:3128', 'https': 'http://system-proxy:3128', 'no': 'localhost'}
        self.assertEqual(_proxy_additions({'http_proxy': 'http://chosen-proxy:8123'}, system),
                         {'HTTPS_PROXY': system['https'], 'NO_PROXY': 'localhost'})
        self.assertEqual(_proxy_additions({'HTTP_PROXY': '', 'https_proxy': '', 'no_proxy': ''}, system), {})
        self.assertEqual(_proxy_additions({'ALL_PROXY': ''}, system), {'NO_PROXY': 'localhost'})
        self.assertEqual(_proxy_additions({'all_proxy': 'socks5://chosen:8123'}, system), {'NO_PROXY': 'localhost'})

    def test_no_proxy_and_non_proxy_registry_values_cannot_add_environment(self):
        self.assertEqual(_proxy_additions({}, {}), {})
        self.assertEqual(_proxy_additions({}, {'ftp': 'http://unused:3128', 'PATH': 'untrusted'}), {})
        for proxy in ['http://proxy:0', 'http://proxy:99999', 'file:///private', 'http://host\nsecret', 123]:
            self.assertEqual(_proxy_additions({}, {'https': proxy}), {})

    def test_no_proxy_env_does_not_hide_windows_registry_proxy(self):
        with patch.dict(os.environ, {'NO_PROXY': 'chosen.local'}, clear=True), \
                patch('job_assistant.native_proxy.urllib.request.getproxies_registry',
                      return_value={'https': 'http://system:3128', 'no': 'system.local'}, create=True):
            before = os.environ.copy()
            self.assertEqual(codex_proxy_environment(), {'HTTPS_PROXY': 'http://system:3128'})
            self.assertEqual(os.environ, before)

    def test_internal_request_requires_native_process_and_snapshot_does_not_leak(self):
        secret = 'http://private-user:private-password@proxy.local:3128'
        request = {'id': 'native', 'method': '_native_codex_proxy_environment', 'params': {}}
        with tempfile.TemporaryDirectory() as folder, closing(Store(folder)) as store, \
                patch.dict(os.environ, {}, clear=True), \
                patch('job_assistant.native_proxy.codex_proxy_environment', return_value={'HTTPS_PROXY': secret}):
            denied = handle_app_request(store, request)
            self.assertFalse(denied['ok'])
            self.assertNotIn('private-password', json.dumps(denied))
            with patch.dict(os.environ, {'JOB_ASSISTANT_NATIVE_PROXY_REQUEST': '1'}):
                result = handle_app_request(store, request)
                self.assertEqual(result['result']['env'], {'HTTPS_PROXY': secret})
            snapshot = handle_app_request(store, {'id': 'ui', 'method': 'snapshot', 'params': {}})
            self.assertTrue(snapshot['ok'])
            self.assertNotIn('private-password', json.dumps(snapshot))
            for path in Path(folder).rglob('*'):
                if path.is_file():
                    self.assertNotIn(b'private-password', path.read_bytes())

    @unittest.skipUnless(os.name == 'nt', '安装目标为 Windows')
    def test_clean_launch_reads_current_user_proxy_without_dev_helper(self):
        env = {key: value for key, value in os.environ.items()
               if not key.upper().endswith('_PROXY') and not key.upper().startswith('JOB_ASSISTANT_')}
        script = '''import os,sys,json,urllib.request
sys.path.insert(0,sys.argv[1])
from job_assistant.native_proxy import codex_proxy_environment
assert not any(k.upper().endswith('_PROXY') for k in os.environ)
raw=urllib.request.getproxies_registry()
result=codex_proxy_environment()
for kind in ('http','https'):
    value=raw.get(kind)
    if isinstance(value,str) and value.startswith(('http://','https://')):
        assert result.get(kind.upper()+'_PROXY')==value
print(json.dumps({'only_allowed_keys':set(result)<=set(('HTTP_PROXY','HTTPS_PROXY','NO_PROXY')),'configured_protocols':sorted(result)}))
'''
        output = subprocess.run([sys.executable, '-X', 'utf8', '-c', script, str(SCRIPTS)],
                                env=env, capture_output=True, text=True, timeout=15)
        # 不把可能包含代理的 stderr 写入测试失败输出。
        self.assertEqual(output.returncode, 0, '清洁启动的代理检测失败')
        self.assertTrue(json.loads(output.stdout)['only_allowed_keys'])


if __name__ == '__main__':
    unittest.main()
