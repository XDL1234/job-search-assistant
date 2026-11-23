"""只读取桌面当前页面使用的数据，旧版面板继续使用完整快照。"""
import json
from datetime import datetime, timedelta

from .boss_messages import read_watch_state
from .dashboard import Dashboard
from .panel_state import pending_notes


_ACTIVE = "status IN ('running','watching')"
_REAL = "coalesce(json_extract(config,'$.simulation'),0)=0"
_RUN_CONFIG = ('channels', 'target_roles', 'web_submit', 'simulation', 'execution_mode', 'employment_type',
               'boss_mode', 'boss_filters', 'cities', 'filters', 'max_contacts', 'poll_seconds',
               'window_title', 'company_table')
_ID_LIST = '(SELECT value FROM json_each(?))'
_ATTEMPT_COLUMNS = 'id,run_id,company,job,channel,status,reason,started,updated'
# 总览计数与待办列表共用同一分类，避免自由文本逐条进入总览 IPC。
_CATEGORY = """CASE
    WHEN reason LIKE '岗位精筛待人工查看%' OR reason LIKE '岗位或资料超过%' OR reason LIKE '岗位城市超出%'
        OR reason LIKE '岗位列表已变化%' OR reason LIKE '发送前岗位城市%' THEN 'review'
    WHEN reason='interview' OR reason LIKE '面试邀约%' OR reason LIKE '面试事项%'
        OR reason LIKE '发现面试邀约%' OR reason LIKE '发现面试事项%' THEN 'interview'
    ELSE 'other' END"""


def _rows(store, sql, params=()):
    return [dict(row) for row in store.conn.execute(sql, params)]


def _runs(store, condition='', params=()):
    rows = _rows(store, "SELECT id,status,created,json_remove(config,'$.company_rows') AS config FROM runs "
                 + condition, params)
    for row in rows:
        config = json.loads(row['config'])
        row['config'] = {key: config[key] for key in _RUN_CONFIG if key in config}
        row['config'].setdefault('target_roles', [])
        row['confirmed_counts'] = {'greet': 0, 'submit': 0}
    return rows


def _run_metrics(store, runs):
    if not runs:
        return
    by_id = {run['id']: run for run in runs}
    ids = json.dumps(list(by_id))
    scope = f'run_id IN {_ID_LIST}'
    # 每个表先按本轮聚合，再合并时间范围。动作计数和更新时间共用一次读取。
    timings = store.conn.execute(f'''
        SELECT run_id,MIN(started) AS first,MAX(updated) AS last,SUM(greet) AS greet,SUM(submit) AS submit
        FROM (
            SELECT run_id,MIN(CASE WHEN action='running' THEN updated END) AS started,MAX(updated) AS updated,
                   0 AS greet,0 AS submit FROM panel_commands WHERE status='applied' AND {scope} GROUP BY run_id
            UNION ALL SELECT run_id,MIN(started),MAX(updated),0,0 FROM attempts WHERE {scope} GROUP BY run_id
            UNION ALL SELECT run_id,NULL,MAX(updated),SUM(kind='greet' AND status='succeeded'),
                SUM(kind='submit' AND status='succeeded') FROM actions WHERE {scope} GROUP BY run_id
            UNION ALL SELECT run_id,NULL,updated,0,0 FROM executor_activity WHERE {scope}
        ) GROUP BY run_id''', (ids,) * 4)
    for row in timings:
        by_id[row['run_id']].update(started_at=row['first'], updated_at=row['last'],
                                    confirmed_counts={'greet': row['greet'], 'submit': row['submit']})


def _home(store, data, params):
    data['active_real_count'] = store.conn.execute(f'SELECT count(*) FROM runs WHERE {_ACTIVE} AND {_REAL}').fetchone()[0]
    data['runs'] = _runs(store, f"WHERE {_REAL} AND status IN ('running','watching','paused') "
                         "ORDER BY (status='paused'),created DESC,id DESC LIMIT 3")
    data['activity'] = _rows(store, f'SELECT * FROM executor_activity WHERE run_id IN {_ID_LIST}',
                             (json.dumps([run['id'] for run in data['runs']]),))
    data['attempts'] = _rows(store, f"""SELECT {_ATTEMPT_COLUMNS} FROM attempts
        WHERE status IN ('success','manual_submitted','failed','uncertain','needs_user') AND EXISTS (
            SELECT 1 FROM runs WHERE id=attempts.run_id AND {_REAL})
        ORDER BY julianday(updated) DESC,id DESC LIMIT 3""")
    counts = {'interview': 0, 'review': 0, 'other': 0}
    counts.update(store.conn.execute(f'SELECT {_CATEGORY} AS category,count(*) FROM attention '
                                     'WHERE resolved=0 GROUP BY category'))
    data['attention_category_counts'] = counts
    week_start = params.get('week_start')
    if week_start is None:
        today = datetime.now().date()
        week_start = datetime.combine(today - timedelta(days=today.weekday()), datetime.min.time()).astimezone().isoformat()
    if datetime.fromisoformat(week_start).tzinfo is None:
        raise ValueError('周起点必须包含时区')
    data['weekly_greet_count'] = store.conn.execute(f"""SELECT count(*) FROM actions
        WHERE kind='greet' AND status='succeeded' AND julianday(updated) >= julianday(?)
        AND EXISTS (SELECT 1 FROM runs WHERE id=actions.run_id AND {_REAL})""", (week_start,)).fetchone()[0]


def _workbench(store, data, run_id):
    data['runs'] = _runs(store, 'WHERE id=?', (run_id,))
    if not data['runs']:
        return
    config = data['runs'][0]['config']
    if config.get('channels') == ['web'] and not config.get('simulation'):
        from .company_queue import view
        original = json.loads(store.one('runs', run_id)['config'])
        if original.get('company_rows'):
            data['company_queue'] = view(store, run_id, original)
    data['activity'] = _rows(store, 'SELECT * FROM executor_activity WHERE run_id=?', (run_id,))
    if store.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='app_sessions'").fetchone():
        data['sessions'] = _rows(store, 'SELECT * FROM app_sessions WHERE run_id=?', (run_id,))
    # 同一申请可能在消息值守中被新任务接管，保留明确登记过的关联。
    data['run_attempts'] = _rows(store, 'SELECT run_id,attempt_id FROM run_attempts WHERE run_id=?', (run_id,))
    data['attempts'] = _rows(store, f'''SELECT {_ATTEMPT_COLUMNS} FROM attempts WHERE run_id=? OR
        id IN (SELECT attempt_id FROM run_attempts WHERE run_id=?) ORDER BY started,id''', (run_id, run_id))
    data['attention'] = _rows(store, '''SELECT id,subject,reason,resolved FROM attention WHERE resolved=1
        AND subject IN (SELECT id FROM attempts WHERE run_id=? AND status='needs_user')''', (run_id,))
    data['frame'] = Dashboard(store.root).latest_frame(run_id)


def _inbox(store, data):
    data['attention'] = _rows(store, f'''SELECT a.*,r.facts,{_CATEGORY} AS category FROM attention a
        LEFT JOIN attention_job_review r ON r.attention_id=a.id WHERE a.resolved=0''')
    for item in data['attention']:
        facts = item.pop('facts')
        if facts:
            item['job_review'] = json.loads(facts)
    data['notes'] = pending_notes(store)
    subjects = json.dumps(list({item['subject'] for item in data['attention']}))
    data['conversations'] = _rows(store, f'SELECT * FROM conversations WHERE id IN {_ID_LIST}', (subjects,))
    data['messages'] = _rows(store, f'''SELECT m.* FROM messages m JOIN conversations c ON
        m.conversation_id=c.id AND m.message_key=c.latest_message WHERE c.id IN {_ID_LIST}''', (subjects,))
    conversations = {row['id'] for row in data['conversations']}
    targets = []
    if conversations:
        # 已暂停的值守记录也可用于显示公司和岗位，仅活动任务可从待办发送回复。
        watch_ids = [path.stem for path in (store.root/'boss-watch').glob('*.json')]
        runs = _runs(store, f'WHERE id IN {_ID_LIST}', (json.dumps(watch_ids),))
        for run in runs:
            items = read_watch_state(store, run['id']).get('conversations', {})
            related = [{'run_id': run['id'], 'conversation': key, 'attempt_id': item['attempt_id'],
                        'message_key': item['message_key'], 'text': item['text']}
                       for key in conversations.intersection(items) if (item := items[key]).get('attempt_id')
                       and item.get('message_key') and item.get('text')]
            if related:
                data['runs'].append(run)
                targets.extend(related)
    data['reply_targets'] = targets
    data['attempts'] = _rows(store, f'SELECT {_ATTEMPT_COLUMNS} FROM attempts '
                             f'WHERE id IN {_ID_LIST} OR id IN {_ID_LIST}',
                             (subjects, json.dumps([target['attempt_id'] for target in targets])))


def desktop_snapshot(store, params):
    view = params['view']
    data = {'runs': [], 'attempts': [], 'attention': [], 'fields': [], 'evidence': [], 'actions': [], 'frame': None,
            'active_run_count': store.conn.execute(f'SELECT count(*) FROM runs WHERE {_ACTIVE}').fetchone()[0],
            'pending_count': store.conn.execute('SELECT count(*) FROM attention WHERE resolved=0').fetchone()[0]}
    if view == 'home':
        _home(store, data, params)
    elif view == 'running':
        _workbench(store, data, params['run_id'])
    elif view == 'tasks':
        data['runs'] = _runs(store, 'ORDER BY created DESC,id DESC')
    elif view in ('boss', 'companies'):
        data['runs'] = _runs(store, f"WHERE {_REAL} AND EXISTS (SELECT 1 FROM json_each(config,'$.channels') "
                             "WHERE value=?) ORDER BY created DESC,id DESC LIMIT 1",
                             ('boss' if view == 'boss' else 'web',))
    elif view == 'inbox':
        _inbox(store, data)
    elif view != 'shell':
        raise ValueError('未知桌面页面')
    if view in ('home', 'running', 'tasks'):
        _run_metrics(store, data['runs'])
    return data
