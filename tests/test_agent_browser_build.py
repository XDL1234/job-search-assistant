"""固定来源、补丁应用与路径边界验证，不下载依赖或操作浏览器。"""
import importlib.util
from datetime import datetime, timezone
import ctypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('agent_browser_build', ROOT / 'tools/build_agent_browser.py')
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class AgentBrowserBuildTests(unittest.TestCase):
    def test_archive_traversal_is_rejected_before_extracting(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / 'bad.zip'
            with zipfile.ZipFile(archive, 'w') as source:
                source.writestr('../outside.txt', 'no')
            with self.assertRaises(ValueError):
                build.unpack(archive, root / 'stage')
            self.assertFalse((root / 'outside.txt').exists())

    def test_patch_requires_all_contexts_before_writing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'a.rs').write_text('expected', encoding='utf-8')
            changes = [{'path': 'a.rs', 'before': 'expected', 'after': 'changed'},
                       {'path': 'a.rs', 'before': 'missing', 'after': 'wrong'}]
            with self.assertRaises(ValueError):
                build.apply_embedding(root, changes, ROOT / 'tools/vendor/agent-browser-embedded.rs')
            self.assertEqual((root / 'a.rs').read_text(), 'expected')

    def test_archive_document_link_is_not_created(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / 'alias.zip'
            item = zipfile.ZipInfo('source/.claude')
            item.create_system = 3
            item.external_attr = (0o120777 << 16)
            with zipfile.ZipFile(archive, 'w') as source:
                source.writestr(item, '.agents')
                source.writestr('source/LICENSE', 'Apache-2.0')
            build.unpack(archive, root / 'stage')
            self.assertFalse((root / 'stage/source/.claude').exists())
            self.assertTrue((root / 'stage/source/LICENSE').is_file())

    def test_patch_path_cannot_escape_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'source').mkdir()
            outside = root / 'outside.rs'
            outside.write_text('original', encoding='utf-8')
            with self.assertRaises(ValueError):
                build.apply_embedding(root / 'source', [{'path': '../outside.rs', 'before': 'original', 'after': 'bad'}],
                                      ROOT / 'tools/vendor/agent-browser-embedded.rs')
            self.assertEqual(outside.read_text(), 'original')

    def test_pinned_source_and_patch_include_required_boundaries(self):
        lock = json.loads((ROOT / 'tools/vendor/agent-browser-lock.json').read_text())
        self.assertEqual(lock['version'], '0.43.0')
        self.assertEqual(len(lock['source_sha256']), 64)
        patch = json.loads((ROOT / 'tools/vendor/agent-browser-patch.json').read_text())
        changed = '\n'.join(item['after'] for item in patch['changes'])
        self.assertIn('job_assistant::authorized', changed)
        self.assertIn('job_assistant::allowed_message', changed)
        self.assertIn('HTTP/1.1 403 Forbidden', changed)
        self.assertIn('job_assistant::validate_env', changed)
        self.assertIn('job_assistant::command_with_auth', changed)
        self.assertIn('job_assistant::take_command_auth', changed)
        self.assertIn('&& !crate::job_assistant::is_embedded()', changed)
        self.assertIn('No eligible existing browser tab found', changed)
        self.assertIn('Select an existing target before enabling a pinned embedded session', changed)


@unittest.skipUnless(os.environ.get('JOB_ASSISTANT_RUNTIME_TEST') == '1', '显式启用隔离 Chrome 集成检查')
class AgentBrowserRuntimeTests(unittest.TestCase):
    def test_auto_connect_preserves_external_tabs_and_tracks_navigation(self):
        executable = Path(os.environ.get('JOB_ASSISTANT_TEST_AGENT_BROWSER', str(ROOT / '.verification/runtime/agent-browser/agent-browser.exe')))
        class Handler(BaseHTTPRequestHandler):
            def handle(self):
                try:
                    super().handle()
                except (ConnectionResetError, BrokenPipeError):
                    pass
            def do_GET(self):
                page = ('<title>External '+self.path+'</title><main>External browser '+self.path+'</main>').encode()
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(page)))
                self.end_headers()
                self.wfile.write(page)
            def log_message(self, *_):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            with tempfile.TemporaryDirectory(prefix='agent-browser-attach-') as temporary:
                folder = Path(temporary)
                # Override only the child discovery root, so auto-connect cannot inspect user profiles.
                local = folder/'local'
                profile = local/'Google'/'Chrome'/'User Data'
                config = folder/'agent-browser.json'
                config.write_text('{}', encoding='utf-8')
                origin = f'http://127.0.0.1:{server.server_port}'
                chrome = subprocess.Popen(['C:/Program Files/Google/Chrome/Application/chrome.exe',
                    '--headless=new', '--remote-debugging-port=0', '--remote-debugging-address=127.0.0.1',
                    '--no-first-run', '--no-default-browser-check', '--user-data-dir='+str(profile), origin+'/one'],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW)
                env = {key: value for key, value in os.environ.items()
                       if not key.startswith(('AGENT_BROWSER_', 'JOB_ASSISTANT_'))}
                env.update({'LOCALAPPDATA': str(local), 'AGENT_BROWSER_CONFIG': str(config),
                    'AGENT_BROWSER_SOCKET_DIR': str(folder/'sessions'), 'JOB_ASSISTANT_EMBEDDED': '1',
                    'JOB_ASSISTANT_STREAM_TOKEN': secrets.token_urlsafe(32)})
                session = 'attach-'+uuid.uuid4().hex[:12]
                connected = False
                def command(*args, bootstrap=False):
                    configured = env if bootstrap else {**env, 'JOB_ASSISTANT_EXISTING_DAEMON': '1'}
                    output = subprocess.run([str(executable), '--session', session, '--json', *args],
                        env=configured, cwd=folder, capture_output=True, text=True, encoding='utf-8', timeout=35)
                    self.assertEqual(output.returncode, 0, output.stderr or output.stdout)
                    response = json.loads(output.stdout)
                    self.assertTrue(response.get('success'), response)
                    return response['data']
                try:
                    active = profile/'DevToolsActivePort'
                    deadline = time.monotonic()+12
                    while not active.is_file() and time.monotonic()<deadline:
                        time.sleep(0.1)
                    port = int(active.read_text().splitlines()[0])
                    def chrome_request(path, method='GET'):
                        with urllib.request.urlopen(urllib.request.Request(f'http://127.0.0.1:{port}'+path,
                                method=method), timeout=5) as response:
                            return json.load(response)
                    def external_tabs():
                        return {item['id']: item['url'] for item in chrome_request('/json/list') if item['type']=='page'}
                    def runtime_tabs():
                        return {item['targetId']: item['url'] for item in command('tab', 'list')['tabs']}
                    # Prove this is the exact isolated browser before invoking auto-discovery.
                    self.assertTrue(chrome_request('/json/version')['webSocketDebuggerUrl'].endswith(active.read_text().splitlines()[1]))
                    chrome_request('/json/new?'+origin+'/two', 'PUT')
                    expected = external_tabs()
                    self.assertEqual(set(expected.values()), {origin+'/one', origin+'/two'})
                    listed = command('--auto-connect', 'tab', 'list', bootstrap=True)
                    connected = True
                    self.assertEqual({item['targetId']: item['url'] for item in listed['tabs']}, expected)
                    self.assertEqual(external_tabs(), expected)
                    command('close')
                    connected = False
                    self.assertEqual(external_tabs(), expected)
                    # Initial pinning must require a selection rather than silently creating/adopting a page.
                    session = 'unpinned-'+uuid.uuid4().hex[:12]
                    refused = subprocess.run([str(executable), '--session', session, '--json', '--auto-connect',
                        '--pin-tab', 'tab', 'list'], env=env, cwd=folder, capture_output=True, text=True,
                        encoding='utf-8', timeout=35)
                    self.assertNotEqual(refused.returncode, 0)
                    self.assertIn('Select an existing target', refused.stdout+refused.stderr)
                    self.assertEqual(external_tabs(), expected)
                    if (folder/'sessions'/f'{session}.pid').exists():
                        command('close')
                    session = 'reattach-'+uuid.uuid4().hex[:12]
                    command('--auto-connect', 'tab', 'list', bootstrap=True)
                    connected = True
                    self.assertEqual(runtime_tabs(), expected)
                    self.assertEqual(external_tabs(), expected)
                    # An explicitly requested navigation changes only its bound tab.
                    command('open', origin+'/changed')
                    expected = external_tabs()
                    deadline = time.monotonic()+3
                    while runtime_tabs()!=expected and time.monotonic()<deadline:
                        time.sleep(0.1)
                    self.assertEqual(runtime_tabs(), expected)
                    self.assertEqual(len(expected), 2)
                    # A tab created outside the agent must enter its cached target list.
                    chrome_request('/json/new?'+origin+'/outside', 'PUT')
                    expected = external_tabs()
                    deadline = time.monotonic()+3
                    while runtime_tabs()!=expected and time.monotonic()<deadline:
                        time.sleep(0.1)
                    self.assertEqual(runtime_tabs(), expected)
                    command('close')
                    connected = False
                    self.assertEqual(external_tabs(), expected)
                    # An empty eligible set is an attach error, never permission to create a page.
                    chrome_request('/json/new?chrome://version', 'PUT')
                    for target in expected:
                        with urllib.request.urlopen(f'http://127.0.0.1:{port}/json/close/'+target, timeout=5) as response:
                            response.read()
                    before_empty = external_tabs()
                    self.assertTrue(before_empty)
                    self.assertTrue(all(url.startswith('chrome://') for url in before_empty.values()))
                    session = 'empty-'+uuid.uuid4().hex[:12]
                    refused = subprocess.run([str(executable), '--session', session, '--json', '--auto-connect', 'tab', 'list'],
                        env=env, cwd=folder, capture_output=True, text=True, encoding='utf-8', timeout=35)
                    self.assertNotEqual(refused.returncode, 0)
                    self.assertIn('No eligible existing browser tab', refused.stdout+refused.stderr)
                    self.assertEqual(external_tabs(), before_empty)
                finally:
                    if connected or (folder/'sessions'/f'{session}.pid').exists():
                        subprocess.run([str(executable), '--session', session, '--json', 'close'],
                            env={**env, 'JOB_ASSISTANT_EXISTING_DAEMON': '1'}, cwd=folder,
                            capture_output=True, timeout=15)
                    chrome.terminate()
                    try:
                        chrome.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        chrome.kill()
                        chrome.wait(timeout=5)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=3)
        report = {'exe_sha256': build.digest(executable), 'verified_at': datetime.now(timezone.utc).isoformat(),
                  'checks': ['auto_connect_preserves_target_ids_and_urls', 'disconnect_preserves_tabs',
                             'reconnect_preserves_tabs', 'explicit_navigation_updates_cache', 'external_tab_discovered',
                             'empty_eligible_set_no_tab_created', 'pinned_attach_requires_selected_target']}
        name = 'attach-smoke.json' if executable.name == 'agent-browser.exe' else executable.stem+'-attach-smoke.json'
        (executable.parent/name).write_text(json.dumps(report,indent=2),encoding='utf-8')

    def test_existing_daemon_mode_never_starts_or_replaces_a_session(self):
        executable = Path(os.environ.get('JOB_ASSISTANT_TEST_AGENT_BROWSER', str(ROOT / '.verification/runtime/agent-browser/agent-browser.exe')))
        probe = json.loads(subprocess.check_output([str(executable), '--job-assistant-probe'], text=True))
        self.assertTrue(probe['capabilities']['existing_daemon_only'])
        with tempfile.TemporaryDirectory(prefix='agent-browser-lifecycle-') as temporary:
            folder = Path(temporary)
            profile = folder/'chrome-profile'
            chrome = subprocess.Popen(['C:/Program Files/Google/Chrome/Application/chrome.exe',
                '--headless=new', '--remote-debugging-port=0', '--remote-debugging-address=127.0.0.1',
                '--no-first-run', '--no-default-browser-check', '--user-data-dir='+str(profile), 'about:blank'],
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW)
            token = secrets.token_urlsafe(32)
            config = folder/'agent-browser.json'
            config.write_text('{}', encoding='utf-8')
            env = {key: value for key, value in os.environ.items()
                   if not key.startswith(('AGENT_BROWSER_', 'JOB_ASSISTANT_'))}
            env.update({'JOB_ASSISTANT_EMBEDDED': '1', 'JOB_ASSISTANT_STREAM_TOKEN': token,
                   'AGENT_BROWSER_CONFIG': str(config),
                   'AGENT_BROWSER_SOCKET_DIR': str(folder/'sessions')})
            session = 'lifecycle-'+uuid.uuid4().hex[:12]
            daemon_killed = False
            def command(*args, required=False):
                configured = {**env, 'JOB_ASSISTANT_EXISTING_DAEMON': '1'} if required else env
                return subprocess.run([str(executable), '--session', session, '--json', *args],
                                      env=configured, capture_output=True, text=True, encoding='utf-8', timeout=30)
            try:
                missing = command('stream', 'status', required=True)
                self.assertNotEqual(missing.returncode, 0)
                self.assertFalse((folder/'sessions'/f'{session}.pid').exists())
                active = profile/'DevToolsActivePort'
                deadline = time.monotonic()+12
                while not active.is_file() and time.monotonic()<deadline:
                    time.sleep(0.1)
                port = int(active.read_text().splitlines()[0])
                connected = command('--cdp', str(port), 'open', 'data:text/html,<title>Lifecycle only</title>')
                self.assertEqual(connected.returncode, 0, connected.stderr)
                self.assertEqual(command('get', 'title', required=True).returncode, 0)
                pid_file = folder/'sessions'/f'{session}.pid'
                pid = int(pid_file.read_text())
                marker_file = folder/'sessions'/f'{session}.version'
                marker = marker_file.read_text()
                marker_file.write_text('unpatched-runtime')
                self.assertNotEqual(command('stream', 'status', required=True).returncode, 0)
                ipc_port = int((folder/'sessions'/f'{session}.port').read_text())
                with socket.create_connection(('127.0.0.1', ipc_port), timeout=5) as connection:
                    connection.sendall(json.dumps({'id':'health','action':'stream_status',
                        '__job_assistant_auth':'Bearer '+token}).encode()+b'\n')
                    self.assertTrue(json.loads(connection.makefile('rb').readline())['success'])
                self.assertEqual(int(pid_file.read_text()), pid)
                marker_file.write_text(marker)
                from ctypes import wintypes
                kernel = ctypes.WinDLL('kernel32', use_last_error=True)
                kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
                kernel.OpenProcess.restype = wintypes.HANDLE
                kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD)]
                kernel.TerminateProcess.argtypes = [wintypes.HANDLE,wintypes.UINT]
                kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE,wintypes.DWORD]
                kernel.CloseHandle.argtypes = [wintypes.HANDLE]
                handle = kernel.OpenProcess(0x1000|0x0001|0x00100000, False, pid)
                self.assertTrue(handle)
                try:
                    image = ctypes.create_unicode_buffer(32768)
                    size = wintypes.DWORD(len(image))
                    self.assertTrue(kernel.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(size)))
                    self.assertEqual(Path(image.value).resolve(), executable.resolve())
                    self.assertTrue(kernel.TerminateProcess(handle, 1))
                    kernel.WaitForSingleObject(handle, 5000)
                    daemon_killed = True
                finally:
                    kernel.CloseHandle(handle)
                lost = command('snapshot', '-i', required=True)
                self.assertNotEqual(lost.returncode, 0)
                self.assertEqual(int(pid_file.read_text()), pid)
                self.assertIsNone(chrome.poll(), '外接Chrome不应随daemon死亡被关闭')
            finally:
                if not daemon_killed and (folder/'sessions'/f'{session}.pid').exists():
                    command('close')
                chrome.terminate()
                try:
                    chrome.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    chrome.kill()
                    chrome.wait(timeout=5)
        report = {'exe_sha256': build.digest(executable), 'verified_at': datetime.now(timezone.utc).isoformat(),
                  'checks': ['missing_daemon_no_spawn', 'live_daemon_reused', 'mismatched_marker_no_stop',
                             'crashed_daemon_no_spawn', 'external_chrome_preserved']}
        name = 'lifecycle-smoke.json' if executable.name == 'agent-browser.exe' else executable.stem+'-lifecycle-smoke.json'
        (executable.parent/name).write_text(json.dumps(report,indent=2),encoding='utf-8')

    def test_native_runtime_rejects_untrusted_control_and_produces_png(self):
        executable = Path(os.environ.get('JOB_ASSISTANT_TEST_AGENT_BROWSER', str(ROOT / '.verification/runtime/agent-browser/agent-browser.exe')))
        probe = json.loads(subprocess.check_output([str(executable), '--job-assistant-probe'], text=True))
        self.assertTrue(probe['capabilities']['command_auth'])
        with tempfile.TemporaryDirectory(prefix='agent-browser-smoke-') as temporary:
            folder = Path(temporary)
            token = secrets.token_urlsafe(32)
            config = folder/'agent-browser.json'
            config.write_text('{}', encoding='utf-8')
            env = {key: value for key, value in os.environ.items()
                   if not key.startswith(('AGENT_BROWSER_', 'JOB_ASSISTANT_'))}
            env.update({'JOB_ASSISTANT_EMBEDDED': '1', 'JOB_ASSISTANT_STREAM_TOKEN': token,
                   'AGENT_BROWSER_CONFIG': str(config),
                   'AGENT_BROWSER_SOCKET_DIR': str(folder / 'session'),
                   'AGENT_BROWSER_EXECUTABLE_PATH': 'C:/Program Files/Google/Chrome/Application/chrome.exe'})
            session = 'security-' + uuid.uuid4().hex[:12]
            def command(*args):
                output = subprocess.run([str(executable), '--session', session, '--json', *args],
                                        env=env, check=True, capture_output=True, text=True, encoding='utf-8', timeout=45)
                result = json.loads(output.stdout)
                self.assertTrue(result.get('success'), result)
                return result['data']
            missing = dict(env)
            missing.pop('JOB_ASSISTANT_STREAM_TOKEN')
            refused = subprocess.run([str(executable), '--version'], env=missing, capture_output=True, timeout=10)
            self.assertEqual(refused.returncode, 2)
            try:
                command('open', 'data:text/html,<title>Isolated runtime test</title><main><h1>Demo job</h1><input aria-label="Name"></main><script>let n=0;setInterval(()=>document.querySelector("h1").textContent="Demo "+(++n),100)</script>')
                for literal in ['dashboard', '--job-assistant-probe']:
                    command('fill', 'input', literal)
                    self.assertEqual(command('get', 'value', 'input')['value'], literal)
                failed_eval = subprocess.run([str(executable), '--session', session, '--json', 'eval', '--stdin'],
                    input="throw new Error('isolated evaluation failure')", env=env,
                    capture_output=True, text=True, encoding='utf-8', timeout=10)
                self.assertNotEqual(failed_eval.returncode, 0)
                self.assertIn('isolated evaluation failure', json.loads(failed_eval.stdout).get('error', ''))
                shot = folder / 'evidence.png'
                command('screenshot', str(shot))
                self.assertEqual(shot.read_bytes()[:8], b'\x89PNG\r\n\x1a\n')
                stream = command('stream', 'status')
                stream_port = stream['port']
                for authorization in [None, 'Bearer ' + token]:
                    request = urllib.request.Request(f'http://127.0.0.1:{stream_port}/api/command', data=b'{}',
                                                     headers={} if authorization is None else {'Authorization': authorization})
                    try:
                        urllib.request.urlopen(request, timeout=5)
                    except urllib.error.HTTPError as denied:
                        self.assertEqual(denied.code, 403)
                    except ConnectionResetError:
                        # Windows 关闭带未读 POST body 的拒绝连接时可能返回 RST。
                        pass
                    except urllib.error.URLError as denied:
                        if not isinstance(denied.reason, ConnectionResetError):
                            raise
                    else:
                        self.fail('嵌入流服务不应接受 HTTP 命令')
                port_file = folder / 'session' / (session + '.port')
                ipc_port = int(port_file.read_text())
                marker = (folder / 'session' / (session + '.version')).read_text()
                self.assertTrue(marker.startswith('0.43.0+job-assistant.1.'))
                self.assertNotIn(token, marker)
                for bad_token in [None, 'Bearer wrong']:
                    wire = {'id': 'untrusted', 'action': 'eval', 'script': 'document.title="Tampered"'}
                    if bad_token is not None:
                        wire['__job_assistant_auth'] = bad_token
                    with socket.create_connection(('127.0.0.1', ipc_port), timeout=5) as connection:
                        connection.sendall(json.dumps(wire).encode() + b'\n')
                        response = json.loads(connection.makefile('rb').readline())
                    self.assertFalse(response['success'])
                    self.assertEqual(response['error'], 'Unauthorized')
                self.assertEqual(command('get', 'title')['title'], 'Isolated runtime test')
                shell = folder / 'stream-check.ps1'
                shell.write_text(r'''
$ErrorActionPreference = 'Stop'
$url = [Uri]$env:JOB_ASSISTANT_TEST_STREAM_URL
$anonymous = [System.Net.WebSockets.ClientWebSocket]::new()
$deadline = [Threading.CancellationTokenSource]::new(20000)
try {
    try {
        $null = $anonymous.ConnectAsync($url, $deadline.Token).GetAwaiter().GetResult()
        throw 'Anonymous stream connection was accepted'
    } catch {
        if ($_.Exception.ToString() -notmatch '401') { throw }
    }
} finally { $anonymous.Dispose() }
$client = [System.Net.WebSockets.ClientWebSocket]::new()
$client.Options.SetRequestHeader('Authorization', 'Bearer ' + $env:JOB_ASSISTANT_STREAM_TOKEN)
function Send-Json([string]$text) {
    $bytes = [Text.Encoding]::UTF8.GetBytes($text)
    $segment = [ArraySegment[byte]]::new($bytes)
    $null = $client.SendAsync($segment, [Net.WebSockets.WebSocketMessageType]::Text, $true, $deadline.Token).GetAwaiter().GetResult()
}
function Receive-Json {
    $buffer = [byte[]]::new(262144)
    $memory = [IO.MemoryStream]::new()
    try {
        do {
            $result = $client.ReceiveAsync([ArraySegment[byte]]::new($buffer), $deadline.Token).GetAwaiter().GetResult()
            if ($result.MessageType -eq [Net.WebSockets.WebSocketMessageType]::Close) { throw 'Stream closed' }
            $memory.Write($buffer, 0, $result.Count)
            if ($memory.Length -gt 2097152) { throw 'Oversize stream message' }
        } while (-not $result.EndOfMessage)
        return [Text.Encoding]::UTF8.GetString($memory.ToArray()) | ConvertFrom-Json
    } finally { $memory.Dispose() }
}
try {
    $null = $client.ConnectAsync($url, $deadline.Token).GetAwaiter().GetResult()
    $frames = 0
    $last = 0
    while ($frames -lt 2) {
        $packet = Receive-Json
        if ($packet.type -ne 'frame') { continue }
        $jpeg = [Convert]::FromBase64String($packet.data)
        if ($jpeg.Length -lt 4 -or $jpeg[0] -ne 255 -or $jpeg[1] -ne 216) { throw 'Not JPEG' }
        if ($packet.seq -le $last) { throw 'Frame sequence did not increase' }
        $last = $packet.seq
        $frames += 1
        if ($frames -eq 1) { [Threading.Thread]::Sleep(1500) }
        if ($frames -eq 2) {
            $age = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() - [long]$packet.metadata.timestamp
            if ($age -gt 1000) { throw 'Stream replayed stale frames after held ACK' }
        }
        Send-Json ('{"type":"ack","seq":' + $last + '}')
    }
    Send-Json '{"type":"input_keyboard","eventType":"keyDown","key":"x","text":"x"}'
    $denied = $false
    try {
        for ($index = 0; $index -lt 8; $index += 1) { $null = Receive-Json }
    } catch {
        if ($deadline.IsCancellationRequested -or $client.State -eq [Net.WebSockets.WebSocketState]::Open) { throw }
        $denied = $true
    }
    if (-not $denied) { throw 'Read-only stream accepted browser input' }
    @{anonymous_rejected=$true; frames=$frames; jpeg=$true; input_rejected=$denied} | ConvertTo-Json -Compress
} finally { $client.Dispose(); $deadline.Dispose() }
''', encoding='utf-8')
                stream_env = {**env, 'JOB_ASSISTANT_TEST_STREAM_URL': f'ws://127.0.0.1:{stream_port}/?pacing=ack&maxFps=5'}
                output = subprocess.run(['pwsh', '-NoProfile', '-File', str(shell)], env=stream_env,
                                        capture_output=True, text=True, encoding='utf-8', check=True, timeout=35)
                checked = json.loads(output.stdout)
                self.assertEqual(checked['frames'], 2)
                self.assertTrue(checked['anonymous_rejected'] and checked['input_rejected'] and checked['jpeg'])
            finally:
                command('close')
        report = {'version': probe['version'], 'exe_sha256': build.digest(executable),
                  'verified_at': datetime.now(timezone.utc).isoformat(),
                  'checks': ['missing_credentials_rejected', 'literal_cli_values', 'direct_eval_exceptions_preserved',
                             'native_png', 'http_disabled',
                             'ipc_authenticated', 'daemon_credentials_fingerprinted',
                             'websocket_authenticated', 'jpeg_frames_acknowledged', 'ack_latest_frame', 'media_input_rejected']}
        name = 'security-smoke.json' if executable.name == 'agent-browser.exe' else executable.stem+'-security-smoke.json'
        (executable.parent/name).write_text(json.dumps(report, indent=2), encoding='utf-8')


if __name__ == '__main__':
    unittest.main()
