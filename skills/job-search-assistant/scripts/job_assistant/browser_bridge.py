"""应用与用户安装的浏览器扩展通信，仅监听回环地址并校验随机密钥。"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import threading
import time
import urllib.request
import uuid


class BridgeServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.secret = secrets.token_urlsafe(32)
        port = 0
        pointer = self.root / 'browser-bridge.json'
        if pointer.is_file():
            try:
                previous = json.loads(pointer.read_text(encoding='utf-8'))
                if isinstance(previous.get('port'), int) and 1024 <= previous['port'] <= 65535 and len(previous.get('token', '')) >= 32:
                    port, self.secret = previous['port'], previous['token']
            except (ValueError, OSError, TypeError):
                pass
        self.commands = {}
        self.lock = threading.Lock()
        self.last_seen = 0
        super().__init__(('127.0.0.1', port), BridgeHandler)
        self.root.mkdir(parents=True, exist_ok=True)
        self.info = {'port': self.server_port, 'token': self.secret}
        temporary = self.root / ('bridge-' + uuid.uuid4().hex + '.tmp')
        temporary.write_text(json.dumps(self.info), encoding='utf-8')
        temporary.replace(self.root / 'browser-bridge.json')

    def valid_command(self, command):
        if time.time() > command['expires']:
            return False
        from .store import Store
        with_db = Store(self.root)
        try:
            operation = command.get('operation')
            with_db.check_run(command['run_id'], operation)
            from .cli import verify_materials
            config = verify_materials(with_db,command['run_id'])
            if 'boss' not in config['channels']:
                return False
            if operation in ('greet','reply','resume'):
                action = with_db.one('actions',command.get('action_id',''))
                if action['run_id'] != command['run_id'] or action['status'] != 'executing' or action['kind'] != operation:
                    return False
                if operation=='greet' and with_db.one('attempts',action['attempt_id'])['url'] != command.get('url'):
                    return False
                if operation in ('reply','resume'):
                    payload = json.loads(action['payload'])
                    state = with_db.one('conversations',action['recipient'])
                    manual = with_db.manual_send_allowed(action)
                    if (state['latest_message'] != payload.get('message_key')
                            or (state['status']=='human' and not manual)
                            or command.get('conversation') != action['recipient']
                            or command.get('message_key') != payload.get('message_key')
                            or command.get('manual',False) != manual):
                        return False
                    if operation=='reply' and command.get('text') != payload.get('text'):
                        return False
                    message=with_db.conn.execute('SELECT text FROM messages WHERE conversation_id=? AND message_key=?',
                                                 (action['recipient'],payload.get('message_key'))).fetchone()
                    if not message or message[0]!=command.get('message_text'):
                        return False
                    if operation=='resume' and (command.get('resume_path') != config['resume_path']
                            or payload.get('resume_sha256') != config['resume_sha256']):
                        return False
            return True
        except (ValueError,KeyError,OSError):
            return False
        finally:
            with_db.close()


class BridgeHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, status, value):
        data = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(data)))
        origin = self.headers.get('Origin', '')
        if origin.startswith('chrome-extension://'):
            self.send_header('Access-Control-Allow-Origin', origin)
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def authorized(self):
        origin = self.headers.get('Origin', '')
        host = self.headers.get('Host', '')
        return (host == f'127.0.0.1:{self.server.server_port}'
                and (not origin or origin.startswith('chrome-extension://'))
                and secrets.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + self.server.secret))

    def do_OPTIONS(self):
        origin = self.headers.get('Origin', '')
        if not origin.startswith('chrome-extension://'):
            return self.send(403, {'error': 'origin'})
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', origin)
        self.send_header('Access-Control-Allow-Headers', 'Authorization, Content-Type')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST')
        self.end_headers()

    def do_GET(self):
        if not self.authorized():
            return self.send(403, {'error': 'unauthorized'})
        if self.path == '/status':
            return self.send(200, {'connected': time.time() - self.server.last_seen < 40})
        if self.path == '/next':
            self.server.last_seen = time.time()
            with self.server.lock:
                for command in self.server.commands.values():
                    if not command['claimed'] and self.server.valid_command(command):
                        command['claimed'] = True
                        return self.send(200, {key: value for key, value in command.items() if key not in ('event', 'response')})
            return self.send(200, None)
        if self.path.startswith('/lease/'):
            with self.server.lock:
                command = self.server.commands.get(self.path.split('/')[-1])
                valid = bool(command and self.server.valid_command(command))
            return self.send(200, {'valid': valid})
        self.send(404, {'error': 'not_found'})

    def do_POST(self):
        if not self.authorized():
            return self.send(403, {'error': 'unauthorized'})
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 8 * 1024 * 1024:
                raise ValueError('请求大小不合法')
            value = json.loads(self.rfile.read(length))
            if self.path == '/command':
                if not isinstance(value, dict) or value.get('operation') not in {'observe', 'open_job', 'greet', 'next', 'return', 'messages', 'open_conversation', 'reply', 'resume'}:
                    raise ValueError('浏览器操作不支持')
                command = {**value, 'id': uuid.uuid4().hex, 'expires': time.time()+35,
                           'claimed': False, 'event': threading.Event(), 'response': None}
                if not self.server.valid_command(command):
                    raise ValueError('任务已暂停或失效')
                with self.server.lock:
                    if self.server.commands:
                        raise ValueError('已有浏览器请求正在执行')
                    self.server.commands[command['id']] = command
                try:
                    if not command['event'].wait(35):
                        raise ValueError('浏览器扩展未响应，请确认已连接；未决发送需核查，不能重试')
                    return self.send(200, command['response'])
                finally:
                    with self.server.lock:
                        self.server.commands.pop(command['id'], None)
            if self.path == '/result':
                with self.server.lock:
                    command = self.server.commands.get(value['id'])
                    if command and command['response'] is None:
                        command['response'] = value['response']
                        command['event'].set()
                return self.send(200, {'saved': True})
            self.send(404, {'error': 'not_found'})
        except (ValueError, KeyError, TypeError) as error:
            self.send(400, {'error': str(error)})


def bridge_call(root, operation, run_id, **params):
    info_path = Path(root) / 'browser-bridge.json'
    if not info_path.is_file():
        raise ValueError('请先在设置中启动并连接浏览器扩展')
    info = json.loads(info_path.read_text(encoding='utf-8'))
    body = json.dumps({'operation': operation, 'run_id': run_id, **params}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{int(info['port'])}/command", body,
        {'Authorization': 'Bearer '+info['token'], 'Content-Type': 'application/json'})
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=39) as response:
            value = json.loads(response.read())
    except OSError as error:
        raise ValueError('浏览器桥接不可用或响应超时，请检查连接并核查未决动作') from error
    if not isinstance(value, dict) or not value.get('ok'):
        raise ValueError(value.get('error', '浏览器操作失败') if isinstance(value, dict) else '浏览器响应无效')
    return value['result']


def serve(root):
    server = BridgeServer(root)
    try:
        server.serve_forever()
    finally:
        server.server_close()


def connection_status(root):
    path=Path(root)/'browser-bridge.json'
    if not path.is_file():
        return {'connected':False,'ready':False}
    try:
        info=json.loads(path.read_text(encoding='utf-8'))
        req=urllib.request.Request(f"http://127.0.0.1:{int(info['port'])}/status",headers={'Authorization':'Bearer '+info['token']})
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=1) as response:
            return {'connected':json.loads(response.read()).get('connected') is True,'ready':True}
    except (ValueError,KeyError,OSError,TypeError):
        return {'connected':False,'ready':False}
