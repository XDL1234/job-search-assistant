"""共享面板指令与执行器活动；读取面板不会制造执行心跳。"""
import json
import time
import uuid
from pathlib import Path

from .store import now


def queue_control(store, request):
    run_id, action = request['run_id'], request['action']
    if action not in ('paused', 'running', 'watching', 'stopped'):
        raise ValueError('不支持的控制指令')
    identity, stamp = uuid.uuid4().hex, now()
    with store.conn:
        store.conn.execute('BEGIN IMMEDIATE')
        if store.one('runs', run_id)['status'] == 'stopped':
            raise ValueError('已停止的运行不能恢复')
        store.conn.execute("UPDATE panel_commands SET status='cancelled',updated=? WHERE run_id=? AND status='pending'", (stamp, run_id))
        immediate = action in ('paused', 'stopped')
        if immediate:
            store.conn.execute('UPDATE runs SET status=? WHERE id=?', (action, run_id))
        store.conn.execute('INSERT INTO panel_commands VALUES (?,?,?,?,?,?,?)',
                           (identity, run_id, action, 'applied' if immediate else 'pending', '', stamp, stamp))
    return {'id': identity, 'status': 'applied' if immediate else 'pending'}


def apply_commands(store, run_id):
    from .cli import verify_materials
    error = None
    with store.conn:
        store.conn.execute('BEGIN IMMEDIATE')
        commands = store.conn.execute("SELECT * FROM panel_commands WHERE run_id=? AND status='pending' ORDER BY created", (run_id,)).fetchall()
        for command in commands:
            try:
                if store.one('runs', run_id)['status'] == 'stopped':
                    raise ValueError('运行已停止')
                verify_materials(store, run_id)
                store.conn.execute('UPDATE runs SET status=? WHERE id=?', (command['action'], run_id))
                store.conn.execute('''INSERT INTO executor_activity VALUES (?, '', '', NULL, ?)
                  ON CONFLICT(run_id) DO UPDATE SET fresh_after=excluded.fresh_after''', (run_id, time.time()))
            except (ValueError, KeyError, OSError) as exc:
                error = str(exc)
            store.conn.execute('UPDATE panel_commands SET status=?,error=?,updated=? WHERE id=?',
                               ('rejected' if error else 'applied', error or '', now(), command['id']))
    if error:
        raise ValueError(error)


def activity(store, run_id, operation, phase):
    with store.conn:
        store.conn.execute('''INSERT INTO executor_activity VALUES (?,?,?,?,NULL)
          ON CONFLICT(run_id) DO UPDATE SET operation=excluded.operation,phase=excluded.phase,updated=excluded.updated''',
                           (run_id, operation, phase, now()))


def check_fresh_frame(store, run_id, frame_path):
    row = store.conn.execute('SELECT fresh_after FROM executor_activity WHERE run_id=?', (run_id,)).fetchone()
    if row and row[0]:
        path = Path(frame_path).resolve()
        if path.parent != store.root / 'frames':
            raise ValueError('恢复后必须重新截图')
        frame = json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))
        if frame.get('captured_at', 0) <= row[0]:
            raise ValueError('恢复后必须重新截图并观察，不能复用恢复前的画面')


def pending_notes(store):
    return [dict(row) for row in store.conn.execute('''SELECT n.*, a.subject, a.reason
      FROM attention_notes n JOIN attention a ON n.attention_id=a.id
      WHERE a.resolved=0 ORDER BY n.created''')]
