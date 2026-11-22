"""桌面轮次与模拟材料；UI 授权与模型控制分开。"""
import json
from pathlib import Path
import time
import uuid

from .bootstrap import SKILL_ROOT
from .cli import prepare_config, read_profile, verify_materials
from .migrations import migrate_app
from .panel_state import cancel_pending_actions, queue_control
from .store import packed


def _revisable_run(store, run_id):
    app_session(store, run_id)
    row = store.one('runs', run_id)
    config = json.loads(row['config'])
    if config.get('simulation') or row['status'] not in ('paused', 'stopped', 'completed'):
        raise ValueError('请先暂停或停止任务，再编辑投递范围')
    require_resolved_actions(store, run_id)
    return row, config


def require_resolved_actions(store, run_id):
    if store.conn.execute("""SELECT 1 FROM actions WHERE status IN ('pending','executing','uncertain')
          AND (run_id=? OR attempt_id IN (SELECT attempt_id FROM run_attempts WHERE run_id=?)) LIMIT 1""",
                          (run_id, run_id)).fetchone():
        raise ValueError('任务有待核查的发送或提交，请先核查结果，再编辑或继续')


def reset_unsent_attempts(store, run_id, reason):
    from .store import now
    store.conn.execute("""UPDATE attempts SET status='skipped',reason=?,updated=?
          WHERE run_id=? AND status='in_progress' AND NOT EXISTS
          (SELECT 1 FROM actions WHERE actions.attempt_id=attempts.id AND actions.status!='not_done')""",
                       (reason, now(), run_id))


def _used_contacts(store, run_id):
    return store.conn.execute("""SELECT count(*) FROM actions
        WHERE run_id=? AND kind='greet' AND status!='not_done'""", (run_id,)).fetchone()[0]


def preview_run(store, value, run_id=None):
    if not isinstance(value,dict):
        raise ValueError('任务配置必须为对象')
    from .profiles import get_profile
    from .inputs import digest_file
    allowed = {'channels', 'target_roles', 'employment_type', 'boss_mode', 'boss_filters', 'execution_mode', 'max_contacts',
               'filters', 'cities', 'keywords', 'exclude_keywords', 'company_table', 'companies', 'columns', 'sheet',
               'reviewed_company_table_sha256', 'web_submit',
               'unknown_reply_mode', 'rules', 'window_title', 'poll_seconds'}
    config = {key: val for key, val in value.items() if key in allowed}
    if config.get('channels') == ['web']:
        config['web_submit'] = config.get('web_submit') is True
    else:
        config.pop('web_submit', None)
    profile = get_profile(store) or {}
    profile_path = profile.get('profile_path', '')
    resume_path = profile.get('resume_path', '')
    if config.get('channels') != ['web'] and (not profile_path or not resume_path):
        raise ValueError('请先保存个人信息和简历')
    if resume_path and (not Path(resume_path).is_file() or digest_file(resume_path) != profile['resume_sha256']):
        raise ValueError('简历原件已变化或丢失，请在简历页重新导入')
    from .job_preferences import JOB_TYPES
    employment = config.get('employment_type')
    if not isinstance(employment, str) or employment not in JOB_TYPES:
        raise ValueError('请选择实习、校招、正式工作或兼职')
    if config.get('execution_mode') not in ('script', 'ai'):
        raise ValueError('请选择脚本执行或 AI 执行')
    if config['execution_mode'] == 'script' and (config.get('channels') != ['boss'] or config.get('boss_mode') != 'filtered'):
        raise ValueError('脚本模式用于 BOSS 平台筛选结果；AI 精筛与官网填写请选择 AI 执行')
    if config['execution_mode']=='script' and config.get('filters','') not in ('','沿用当前 BOSS 筛选','沿用当前 BOSS 筛选条件','沿用当前 BOSS 城市和筛选条件'):
        raise ValueError('脚本模式沿用平台当前筛选；自然语言补充条件请改用 AI 模式或先在 BOSS 设置')
    if not isinstance(config.get('window_title'),str) or not config['window_title'].strip():
        raise ValueError('请填写目标浏览器窗口标题')
    config.update({'profile_path': profile_path, 'resume_path': resume_path,
                   'profile_version': profile.get('version_id', ''), 'simulation': False})
    from .reply_settings import read_settings
    settings = read_settings(store)
    config['rules'] = settings['rules']
    config['unknown_reply_mode'] = settings['unknown_reply_mode']
    config = prepare_config(config)
    migrate_app(store)
    ticket_value = config
    if run_id is not None:
        from .ai_selection import digest
        from .app_decisions import current_generation
        row, original = _revisable_run(store, run_id)
        if original['channels'] != config['channels']:
            raise ValueError('编辑任务需保持原投递渠道')
        used = _used_contacts(store, run_id)
        if 'boss' in config['channels'] and config['max_contacts'] < used:
            raise ValueError(f'沟通上限需要至少保留已占用的 {used} 次')
        ticket_value = {'config': config, 'revision': {'run_id': run_id, 'config_digest': digest(original),
                                                       'generation': current_generation(store, run_id),
                                                       'status': row['status']}}
    ticket = uuid.uuid4().hex
    with store.conn:
        store.conn.execute('INSERT INTO app_previews VALUES (?,?,?)', (ticket, packed(ticket_value), time.time()+600))
    return {'ticket': ticket, 'config': config, **({'run_id': run_id, 'used_contacts': used} if run_id is not None else {})}


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


def start_run(store, params, revision=False):
    migrate_app(store)
    if params.get('confirmed') is not True:
        raise ValueError('需要确认本轮范围')
    with store.conn:
        store.conn.execute('BEGIN IMMEDIATE')
        row = store.conn.execute('SELECT * FROM app_previews WHERE ticket=?', (params['ticket'],)).fetchone()
        if not row or row['expires'] <= time.time():
            raise ValueError('预览已失效或已使用，请重新预览')
        ticket = json.loads(row['config'])
        draft = ticket.get('revision')
        if revision != bool(draft):
            raise ValueError('请按预览中的操作类型确认任务')
        config = ticket['config'] if revision else ticket
        if prepare_config(config) != config:
            raise ValueError('材料已变化，请重新预览')
        # 与轮次插入共用事务，不允许重复消费。
        store.conn.execute('DELETE FROM app_previews WHERE ticket=?', (params['ticket'],))
        if revision:
            return _confirm_revision(store, config, draft)
        run = uuid.uuid4().hex
        from .store import now
        store.conn.execute('INSERT INTO runs VALUES (?,?,?,?,?)', (run, packed(config), '用户在桌面应用确认本轮任务范围和材料', 'paused', now()))
        store.conn.execute('INSERT INTO app_sessions (run_id) VALUES (?)', (run,))
    return {'run_id': run}


def _confirm_revision(store, config, draft):
    from .ai_selection import digest
    from .app_decisions import advance_control, current_generation
    from .store import now
    run_id = draft['run_id']
    row, original = _revisable_run(store, run_id)
    if (digest(original) != draft['config_digest'] or row['status'] != draft['status']
            or current_generation(store, run_id) != draft['generation']):
        raise ValueError('任务在预览后已变化，请重新预览并确认')
    if 'boss' in config['channels'] and config['max_contacts'] < _used_contacts(store, run_id):
        raise ValueError('沟通上限低于已占用次数，请重新预览')
    stamp = now()
    try:
        history = json.loads(row['approval'])
        if not isinstance(history, dict) or not isinstance(history.get('revisions'), list):
            raise ValueError('没有修订历史')
    except (TypeError, ValueError):
        history = {'original_authorization': row['approval'], 'revisions': []}
    history['revisions'].append({'confirmed_at': stamp, 'previous_config': original})
    config['scope_revision'] = uuid.uuid4().hex
    store.conn.execute("UPDATE runs SET config=?,approval=?,status='paused' WHERE id=?",
                       (packed(config), packed(history), run_id))
    reset_unsent_attempts(store, run_id, '任务条件已修改，原岗位未执行发送或提交')
    advance_control(store, run_id)
    store.conn.execute("UPDATE panel_commands SET status='cancelled',updated=? WHERE run_id=? AND status='pending'", (stamp, run_id))
    store.conn.execute("INSERT INTO panel_commands VALUES (?,?,?,'applied','',?,?)",
                       (uuid.uuid4().hex, run_id, 'revised', stamp, stamp))
    store.conn.execute("UPDATE app_sessions SET thread_id=NULL,state='idle',error='' WHERE run_id=?", (run_id,))
    return {'run_id': run_id}


def app_session(store, run_id):
    migrate_app(store)
    row = store.conn.execute('SELECT * FROM app_sessions WHERE run_id=?', (run_id,)).fetchone()
    if not row:
        raise ValueError('不是本应用创建的轮次')
    return dict(row)


def control_run(store, params):
    app_session(store, params['run_id'])
    return queue_control(store, params, cancel_unclaimed=True)


def recover_app(store):
    migrate_app(store)
    rows = store.conn.execute('SELECT run_id FROM app_sessions').fetchall()
    for row in rows:
        if store.one('runs', row[0])['status'] not in ('stopped', 'completed'):
            store.control(row[0], 'paused')
        # 系统调用开始后崩溃，不能回到 pending 再次消费。
        actions = store.conn.execute("SELECT id FROM actions WHERE run_id=? AND status='executing'", (row[0],)).fetchall()
        for action in actions:
            store.finish_action(action[0], 'uncertain', reason='执行器中断，请核验页面结果')
        with store.conn:
            store.conn.execute('BEGIN IMMEDIATE')
            if store.one('runs', row[0])['status'] in ('paused', 'stopped', 'completed'):
                cancel_pending_actions(store, row[0])
            store.conn.execute("UPDATE app_sessions SET state='idle' WHERE run_id=?", (row[0],))
    return {'paused': len(rows)}


def bind_session(store, params):
    app_session(store, params['run_id'])
    with store.conn:
        store.conn.execute('UPDATE app_sessions SET thread_id=?,state=?,error=? WHERE run_id=?',
            (params.get('thread_id'), params['state'], params.get('error', ''), params['run_id']))
    return {'saved': True}


MANUAL_WEB_PROFILE_FIELDS = ('document_type', 'document_number', 'photo_path', 'portfolio_path', 'other_attachment_path')


def execution_context(store, run_id):
    session = app_session(store, run_id)
    config = verify_materials(store, run_id)
    from .boss_messages import read_watch_state
    from .app_decisions import current_generation
    profile = read_profile(config)
    private_fields = []
    if config.get('simulation') is not True:
        identifier = profile.pop('id_number', '')
        sources = profile.get('sources')
        for key in MANUAL_WEB_PROFILE_FIELDS:
            profile.pop(key, None)
        if isinstance(sources, dict):
            for key in ('id_number', *MANUAL_WEB_PROFILE_FIELDS):
                sources.pop(key, None)
        if config['channels'] == ['web']:
            profile.pop('resume_text', None)
            profile.pop('sources', None)
            if identifier:
                private_fields.append('profile.id_number')
    status = store.one('runs', run_id)['status']
    pending = read_watch_state(store, run_id).get('pending_ai')
    context = {'session': session, 'run': {'id': run_id, 'status': status},
            'control_generation': current_generation(store, run_id),
            'pending_messages': pending,
            'config': config, 'profile': profile, 'private_profile_fields': private_fields,
            'skill_path': str(SKILL_ROOT / 'SKILL.md')}
    if status == 'watching' and pending and config.get('unknown_reply_mode') == 'ai' and not config.get('simulation'):
        from .reply_settings import read_settings
        context['ai_reply_instructions'] = read_settings(store).get('ai_instructions', '')
    return context
