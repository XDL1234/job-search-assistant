"""本机面板：只提供受限台账和控制接口，不替代 Codex 执行会话。"""
import json
import re
import secrets
import sqlite3
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .bootstrap import SKILL_ROOT
from .panel_state import pending_notes, queue_control
from .store import Store, now

WEB_ROOT = SKILL_ROOT / 'assets/dashboard'
ID = re.compile(r'^[a-f0-9]{32}$')


class Dashboard:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.previews = {}
        self.preview_lock = threading.Lock()

    def command(self, store, request):
        return queue_control(store, request)

    def preview(self, config):
        from .cli import prepare_config
        config = prepare_config(config)
        ticket = uuid.uuid4().hex
        with self.preview_lock:
            self.previews = {k: v for k, v in self.previews.items() if v['expires'] > time.time()}
            if len(self.previews) >= 32:
                raise ValueError('待确认配置过多，请稍后重试')
            self.previews[ticket] = {'config': config, 'expires': time.time() + 600}
        return {'ticket': ticket, 'config': config}

    def start(self, store, request):
        from .cli import prepare_config
        if request.get('confirmed') is not True:
            raise ValueError('请确认本轮配置后启动')
        with self.preview_lock:
            preview = self.previews.pop(request['ticket'], None)
        if not preview or preview['expires'] <= time.time():
            raise ValueError('配置预览已失效，请重新预览')
        config = prepare_config(preview['config'])
        if config != preview['config']:
            raise ValueError('资料或公司表已变更，请重新预览并确认')
        run = store.create_run(config, '用户在本机面板复核配置后点击确认本轮授权')
        return {'run_id': run, 'executor': 'awaiting_session'}

    def attention(self, store, request):
        with store.conn:
            store.conn.execute('BEGIN IMMEDIATE')
            row = store.conn.execute('SELECT * FROM attention WHERE id=? AND resolved=0', (request['id'],)).fetchone()
            if row is None:
                raise ValueError('待处理事项不存在或已处理')
            conversation = store.conn.execute('SELECT * FROM conversations WHERE id=?', (row['subject'],)).fetchone()
            message_key = conversation['latest_message'] if conversation else ''
            if request.get('message_key', '') != message_key:
                raise ValueError('对方消息已更新，请重新查看后处理')
            if request['action'] == 'note':
                text = request.get('text', '')
                if not isinstance(text, str) or not text.strip() or len(text) > 4000:
                    raise ValueError('处理意见需为 1 至 4000 字符')
                store.conn.execute('INSERT INTO attention_notes VALUES (?,?,?,?,?)',
                                   (uuid.uuid4().hex, row['id'], message_key, text.strip(), now()))
            elif request['action'] in ('resume', 'resolve'):
                if request.get('confirmed') is not True:
                    raise ValueError('需要明确确认已完成人工处理')
                if conversation and request['action'] != 'resume':
                    raise ValueError('聊天事项需要明确恢复会话')
                if conversation:
                    store.conn.execute("UPDATE conversations SET status='auto',reason='',updated=? WHERE id=?", (now(), row['subject']))
                    store.conn.execute('UPDATE attention SET resolved=1 WHERE subject=?', (row['subject'],))
                else:
                    store.conn.execute('UPDATE attention SET resolved=1 WHERE id=?', (row['id'],))
            else:
                raise ValueError('未知人工处理操作')
        return {'saved': True, 'sent': False}

    def snapshot(self, store):
        data = store.snapshot()
        for run in data['runs']:
            run['config'] = json.loads(run['config'])
        data['runs'].sort(key=lambda r: r['created'], reverse=True)
        data['activity'] = [dict(r) for r in store.conn.execute('SELECT * FROM executor_activity')]
        data['commands'] = [dict(r) for r in store.conn.execute('SELECT * FROM panel_commands ORDER BY created DESC LIMIT 100')]
        data['notes'] = pending_notes(store)
        data['messages'] = [dict(r) for r in store.conn.execute('''SELECT m.* FROM messages m JOIN conversations c
          ON m.conversation_id=c.id AND m.message_key=c.latest_message''')]
        data['run_attempts'] = [dict(r) for r in store.conn.execute('SELECT * FROM run_attempts')]
        data['frame'] = self.latest_frame()
        data['server_time'] = now()
        data['data_root'] = str(self.root)
        return data

    def latest_frame(self):
        directory = self.root / 'frames'
        for path in sorted(directory.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True):
            if not ID.fullmatch(path.stem) or path.is_symlink() or not path.with_suffix('.png').is_file():
                continue
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
                return {'id': path.stem, 'captured_at': data['captured_at'], 'title': data['window']['title']}
            except (ValueError, KeyError, OSError):
                continue
        return None

    def image(self, store, kind, identity):
        if not ID.fullmatch(identity):
            raise ValueError('无效图片编号')
        if kind == 'frame':
            path = self.root / 'frames' / (identity + '.png')
            directory = self.root / 'frames'
        elif kind == 'evidence':
            item = store.one('evidence', identity)
            store.check_evidence(identity, item['attempt_id'])
            path = Path(item['path'])
            directory = self.root / 'evidence'
        else:
            raise ValueError('未知图片类型')
        if not path.resolve().is_relative_to(directory) or path.is_symlink():
            raise ValueError('图片路径不在记录目录内')
        return path.read_bytes()


def make_server(root, port=0):
    panel = Dashboard(root)
    # 初始化数据库仅一次；每个请求使用独立连接，暂停不等待桌面互斥锁。
    Store(root).close()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # 访问令牌、路径和个人资料不进入控制台访问日志。

        def respond(self, status, payload, content_type='application/json; charset=utf-8', download=False):
            if not isinstance(payload, bytes):
                payload = json.dumps(payload, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            if download:
                self.send_header('Content-Disposition', 'attachment; filename="job-search-results.xlsx"')
            self.end_headers()
            self.wfile.write(payload)

        def allowed(self, api):
            host = f'127.0.0.1:{self.server.server_port}'
            if self.headers.get('Host') != host:
                return False
            if self.headers.get('Origin') not in (None, 'http://' + host):
                return False
            if self.headers.get('Sec-Fetch-Site') == 'cross-site':
                return False
            supplied = self.headers.get('Authorization', '')
            return not api or secrets.compare_digest(supplied, 'Bearer ' + self.server.token)

        def do_GET(self):
            path = urlsplit(self.path).path
            if not self.allowed(path.startswith('/api/')):
                self.respond(403, {'error': '面板访问凭据无效，请使用启动器提供的本机地址'})
                return
            assets = {'/': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                      '/style.css': ('style.css', 'text/css; charset=utf-8')}
            store = None
            try:
                if path in assets:
                    name, mime = assets[path]
                    self.respond(200, (WEB_ROOT / name).read_bytes(), mime)
                elif path == '/api/state':
                    store = Store(root)
                    self.respond(200, panel.snapshot(store))
                elif path.startswith('/api/image/') and len(path.split('/')) == 5:
                    store = Store(root)
                    _, _, _, kind, identity = path.split('/')
                    self.respond(200, panel.image(store, kind, identity), 'image/png')
                else:
                    self.respond(404, {'error': '接口不存在'})
            except (ValueError, KeyError, OSError, sqlite3.Error) as error:
                self.respond(400, {'error': str(error)})
            finally:
                if store: store.close()

        def do_POST(self):
            if not self.allowed(True):
                self.respond(403, {'error': '面板访问凭据或请求来源无效'})
                return
            store = None
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 262144 or self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                    raise ValueError('请求必须为不超过 256 KB 的 JSON')
                self.connection.settimeout(5)
                data = json.loads(self.rfile.read(size))
                if not isinstance(data, dict):
                    raise ValueError('请求需为 JSON 对象')
                store = Store(root)
                path = urlsplit(self.path).path
                if path == '/api/command': result = panel.command(store, data)
                elif path == '/api/preview': result = panel.preview(data['config'])
                elif path == '/api/start': result = panel.start(store, data)
                elif path == '/api/attention': result = panel.attention(store, data)
                elif path == '/api/export':
                    with self.server.export_lock:
                        output = store.export(store.root / 'dashboard-results.xlsx')
                        self.respond(200, Path(output).read_bytes(), 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', True)
                    return
                else:
                    self.respond(404, {'error': '接口不存在'})
                    return
                self.respond(200, result)
            except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as error:
                self.respond(400, {'error': str(error)})
            finally:
                if store: store.close()

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.token = secrets.token_urlsafe(32)
    server.export_lock = threading.Lock()
    return server


def serve(root, port=0, open_browser=False):
    server = make_server(root, port)
    url = f'http://127.0.0.1:{server.server_port}/#token={server.token}'
    session = Path(root) / 'dashboard-session.json'
    session.write_text(json.dumps({'url': url, 'started': now()}, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({'dashboard_url': url, 'note': '仅本机面板；投递仍由当前 Codex 会话执行。Ctrl+C 关闭面板。'}, ensure_ascii=False), flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.3)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
