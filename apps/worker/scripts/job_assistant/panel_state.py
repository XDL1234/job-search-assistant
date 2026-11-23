"""共享面板指令与执行器活动；读取面板不会制造执行心跳。"""
import json
import time
import uuid
from pathlib import Path

from .store import now


def cancel_pending_actions(store, run_id):
    # claim_action 同样取得写锁，状态仍为 pending 的动作尚未取得执行权。
    store.conn.execute("""UPDATE actions SET status='not_done',reason=?,updated=?
        WHERE run_id=? AND status='pending'""", ('任务暂停或停止，操作尚未领取', now(), run_id))


def queue_control(store, request, *, cancel_unclaimed=False):
    with store.conn:
        store.conn.execute('BEGIN IMMEDIATE')
        return queue_control_in_transaction(store, request['run_id'], request['action'], cancel_unclaimed=cancel_unclaimed)


def queue_control_in_transaction(store, run_id, action, *, cancel_unclaimed):
    if action not in ('paused', 'running', 'watching', 'stopped'):
        raise ValueError('不支持的控制指令')
    identity, stamp = uuid.uuid4().hex, now()
    status = store.one('runs', run_id)['status']
    if status == 'stopped':
        raise ValueError('已停止的运行不能恢复')
    from .app_decisions import advance_control
    advance_control(store, run_id)
    store.conn.execute("UPDATE panel_commands SET status='cancelled',updated=? WHERE run_id=? AND status='pending'", (stamp, run_id))
    immediate = action in ('paused', 'stopped')
    if immediate:
        store.interrupt_company_submissions(run_id)
        # 退出时的暂停可能晚于脚本完成；仍须撤销尚未领取的值守指令。
        if (status, action) != ('completed', 'paused'):
            store.conn.execute('UPDATE runs SET status=? WHERE id=?', (action, run_id))
        if cancel_unclaimed:
            cancel_pending_actions(store, run_id)
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
