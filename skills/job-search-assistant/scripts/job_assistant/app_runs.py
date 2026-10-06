"""桌面轮次与模拟材料；UI 授权与模型控制分开。"""
import json
from pathlib import Path
import time
import uuid

from .bootstrap import SKILL_ROOT
from .cli import prepare_config, verify_materials
from .migrations import migrate_app
from .panel_state import queue_control
from .store import packed


def preview_demo(store):
    migrate_app(store)
    ticket = uuid.uuid4().hex
    folder = store.root / 'simulations' / ticket
    folder.mkdir(parents=True)
    marker = '求职助手本地验证-' + ticket[:8]
    fixture = SKILL_ROOT / 'assets/simulation.html'
    (folder / 'index.html').write_text(fixture.read_text(encoding='utf-8').replace('求职助手本地验证', marker), encoding='utf-8')
    profile = {'name': '演示用户', 'contact': {'email': 'demo@example.com'}, 'projects': ['虚构验证：STM32 传感器数据采集。'], 'sources': {'name': '本地模拟资料'}}
    (folder / 'profile.json').write_text(json.dumps(profile, ensure_ascii=False), encoding='utf-8')
    (folder / 'resume.txt').write_text('虚构验证简历；不上传任何真实招聘网站。', encoding='utf-8')
    (folder / 'companies.csv').write_text(f'公司名称,申请网站\n模拟科技-{ticket[:8]},http://127.0.0.1/simulation\n', encoding='utf-8')
    config = prepare_config({'channels': ['web', 'boss'], 'target_roles': ['嵌入式软件工程师'],
        'boss_mode': 'filtered', 'max_contacts': 1, 'unknown_reply_mode': 'human',
        'profile_path': str(folder / 'profile.json'), 'resume_path': str(folder / 'resume.txt'),
        'company_table': str(folder / 'companies.csv'), 'simulation': True,
        'simulation_url': (folder / 'index.html').as_uri(), 'window_title': marker})
    with store.conn:
        store.conn.execute('DELETE FROM app_previews WHERE expires < ?', (time.time(),))
        store.conn.execute('INSERT INTO app_previews VALUES (?,?,?)', (ticket, packed(config), time.time() + 600))
    return {'ticket': ticket, 'config': config}


def start_run(store, params):
    migrate_app(store)
    if params.get('confirmed') is not True:
        raise ValueError('需要确认本轮范围')
    with store.conn:
        store.conn.execute('BEGIN IMMEDIATE')
        row = store.conn.execute('SELECT * FROM app_previews WHERE ticket=?', (params['ticket'],)).fetchone()
        if not row or row['expires'] <= time.time():
            raise ValueError('预览已失效或已使用，请重新预览')
        config = json.loads(row['config'])
        if prepare_config(config) != config:
            raise ValueError('材料已变化，请重新预览')
        # 与轮次插入共用事务，不允许重复消费。
        store.conn.execute('DELETE FROM app_previews WHERE ticket=?', (params['ticket'],))
        run = uuid.uuid4().hex
        from .store import now
        store.conn.execute('INSERT INTO runs VALUES (?,?,?,?,?)', (run, packed(config), '用户在桌面应用确认本地模拟任务', 'paused', now()))
        store.conn.execute('INSERT INTO app_sessions (run_id) VALUES (?)', (run,))
    return {'run_id': run}


def app_session(store, run_id):
    migrate_app(store)
    row = store.conn.execute('SELECT * FROM app_sessions WHERE run_id=?', (run_id,)).fetchone()
    if not row:
        raise ValueError('不是本应用创建的轮次')
    return dict(row)


def control_run(store, params):
    app_session(store, params['run_id'])
    return queue_control(store, params)


def recover_app(store):
    migrate_app(store)
    rows = store.conn.execute('SELECT run_id FROM app_sessions').fetchall()
    for row in rows:
        if store.one('runs', row[0])['status'] != 'stopped':
            store.control(row[0], 'paused')
        # 系统调用开始后崩溃，不能回到 pending 再次消费。
        actions = store.conn.execute("SELECT id FROM actions WHERE run_id=? AND status='executing'", (row[0],)).fetchall()
        for action in actions:
            store.finish_action(action[0], 'uncertain', reason='执行器中断，请核验页面结果')
        with store.conn:
            store.conn.execute("UPDATE app_sessions SET state='idle' WHERE run_id=?", (row[0],))
    return {'paused': len(rows)}


def bind_session(store, params):
    app_session(store, params['run_id'])
    with store.conn:
        store.conn.execute('UPDATE app_sessions SET thread_id=?,state=?,error=? WHERE run_id=?',
            (params.get('thread_id'), params['state'], params.get('error', ''), params['run_id']))
    return {'saved': True}


def execution_context(store, run_id):
    session = app_session(store, run_id)
    config = verify_materials(store, run_id)
    return {'session': session, 'config': config, 'profile': json.loads(Path(config['profile_path']).read_text(encoding='utf-8')),
            'skill_path': str(SKILL_ROOT / 'SKILL.md')}
