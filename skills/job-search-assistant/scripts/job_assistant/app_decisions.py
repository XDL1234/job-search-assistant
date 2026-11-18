"""模型只读取当前轮次；控制代际阻止暂停后迟到的决策继续操作。"""
import json


def current_generation(store, run_id):
    table = store.conn.execute("SELECT 1 FROM sqlite_master WHERE name='app_control'").fetchone()
    if not table:
        return 0
    row = store.conn.execute('SELECT generation FROM app_control WHERE run_id=?', (run_id,)).fetchone()
    return row[0] if row else 0


def advance_control(store, run_id):
    if store.conn.execute("SELECT 1 FROM sqlite_master WHERE name='app_control'").fetchone():
        store.conn.execute('''INSERT INTO app_control VALUES (?,1)
          ON CONFLICT(run_id) DO UPDATE SET generation=generation+1''', (run_id,))


def check_generation(store, run_id, expected):
    if type(expected) is not int or expected < 0 or current_generation(store, run_id) != expected:
        raise ValueError('任务控制状态已变化，旧的 AI 决策已失效，请重新观察')


def compact_status(store, run_id):
    run = store.one('runs', run_id)
    config = json.loads(run['config'])
    pending = [dict(row) for row in store.conn.execute('''SELECT id,attempt_id,kind,status,reason
      FROM actions WHERE run_id=? AND status IN ('pending','executing','uncertain') ORDER BY created LIMIT 50''', (run_id,))]
    used = store.conn.execute("SELECT COUNT(*) FROM actions WHERE run_id=? AND kind='greet' AND status!='not_done'", (run_id,)).fetchone()[0]
    counts = dict(store.conn.execute('SELECT status,COUNT(*) FROM attempts WHERE run_id=? GROUP BY status', (run_id,)))
    return {'run': {'id': run_id, 'status': run['status']},
            'control_generation': current_generation(store, run_id),
            'remaining_contacts': max(0, config.get('max_contacts', 0) - used),
            'attempt_counts': counts, 'pending_actions': pending,
            'pending_actions_truncated': len(pending) == 50}
