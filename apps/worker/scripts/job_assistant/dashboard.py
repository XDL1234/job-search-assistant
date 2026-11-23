"""桌面界面需要的记录、历史截图与待办操作。"""
import json
import re
import uuid
import webbrowser
from pathlib import Path
from urllib.parse import urlsplit

from .inputs import path_key
from .panel_state import pending_notes
from .store import now

ID = re.compile(r'^[a-f0-9]{32}$')


class Dashboard:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def attention(self, store, request):
        action = request['action']
        if action == 'open_job':
            row = store.conn.execute('''SELECT a.subject, r.facts FROM attention a
              JOIN attention_job_review r ON r.attention_id=a.id
              WHERE a.id=? AND a.resolved=0''', (request['id'],)).fetchone()
            if not row or not re.fullmatch(r'/job_detail/[A-Za-z0-9_-]+\.html', row['subject']):
                raise ValueError('此事项没有可核对的岗位链接')
            from .agent_browser import _boss_url
            url = _boss_url(json.loads(row['facts']).get('url'))
            if urlsplit(url).path != row['subject']:
                raise ValueError('岗位地址与已记录的岗位标识不一致')
            if not webbrowser.open_new_tab(url):
                raise ValueError('未能打开岗位页面')
            return {'opened': True}
        with store.conn:
            store.conn.execute('BEGIN IMMEDIATE')
            row = store.conn.execute('SELECT * FROM attention WHERE id=? AND resolved=0', (request['id'],)).fetchone()
            if row is None:
                raise ValueError('待处理事项不存在或已处理')
            conversation = store.conn.execute('SELECT * FROM conversations WHERE id=?', (row['subject'],)).fetchone()
            message_key = conversation['latest_message'] if conversation else ''
            if request.get('message_key', '') != message_key:
                raise ValueError('对方消息已更新，请重新查看后处理')
            if action == 'note' or (action == 'resolve' and 'text' in request):
                text = request.get('text', '')
                if not isinstance(text, str) or not text.strip() or len(text) > 4000:
                    raise ValueError('处理意见需为 1 至 4000 字符')
                store.conn.execute('INSERT INTO attention_notes VALUES (?,?,?,?,?)',
                                   (uuid.uuid4().hex, row['id'], message_key, text.strip(), now()))
            if action == 'note':
                pass
            elif action == 'handoff':
                if request.get('confirmed') is not True or not conversation:
                    raise ValueError('需要明确接管当前会话')
                store.conn.execute("UPDATE conversations SET status='human',reason='用户接管',updated=? WHERE id=?", (now(), row['subject']))
            elif action in ('resume', 'resolve'):
                if request.get('confirmed') is not True:
                    raise ValueError('需要明确确认已完成人工处理')
                if conversation and action != 'resume':
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
        reviews = {row['attention_id']: json.loads(row['facts']) for row in store.conn.execute('SELECT * FROM attention_job_review')}
        for item in data['attention']:
            if item['id'] in reviews:
                item['job_review'] = reviews[item['id']]
        data['messages'] = [dict(r) for r in store.conn.execute('''SELECT m.* FROM messages m JOIN conversations c
          ON m.conversation_id=c.id AND m.message_key=c.latest_message''')]
        data['run_attempts'] = [dict(r) for r in store.conn.execute('SELECT * FROM run_attempts')]
        data['frame'] = self.latest_frame()
        data['server_time'] = now()
        data['data_root'] = str(self.root)
        return data

    def latest_frame(self, run_id=None):
        directory = self.root / 'frames'
        for path in sorted(directory.glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True):
            if not ID.fullmatch(path.stem) or path.is_symlink() or not path.with_suffix('.png').is_file():
                continue
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
                if run_id and data.get('run_id') != run_id:
                    continue
                return {'id': path.stem, 'captured_at': data['captured_at'], 'title': data['window']['title'], 'run_id':data.get('run_id')}
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
            path = Path(item['path'])
            directory = self.root / 'evidence'
        else:
            raise ValueError('未知图片类型')
        if not path_key(path.resolve()).is_relative_to(path_key(directory)) or path.is_symlink():
            raise ValueError('图片路径不在记录目录内')
        if kind == 'evidence':
            store.check_evidence(identity, item['attempt_id'])
        return path.read_bytes()
