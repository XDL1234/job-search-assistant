"""公司网申的按行浏览器队列；公司/岗位、提交权限与结果仍取自本轮授权和行动台账。"""
import json
import re
from urllib.parse import unquote, urlsplit, urlunsplit

from .app_decisions import current_generation
from .panel_state import queue_control_in_transaction
from .store import now


ELIGIBLE = {'available', 'locate', 'filling'}
RETRYABLE = ELIGIBLE | {'pending_open', 'opened', 'login_required', 'manual_required'}
CHECK_STATES = {'pending': 'pending_open', 'opened': 'opened', 'needs_login': 'login_required',
                'ready': 'available', 'locate': 'locate', 'needs_attention': 'manual_required'}
MANUAL_SUBMITTED = '用户标记已在网页提交'


def key(run_id, config):
    return run_id, config.get('scope_revision') or config.get('company_table_sha256', '')


def batch(store, batch_key):
    return store.conn.execute('SELECT phase,reason FROM company_batches WHERE run_id=? AND revision=?', batch_key).fetchone()


def occupied_targets(store):
    return [row[0] for row in store.conn.execute(
        "SELECT DISTINCT target_id FROM company_queue WHERE target_id!=''")]


def view(store, run_id, config):
    from .company_web import _entry_key, _remaining_rows, _READY_REASON, _BLOCKED_JOB
    batch_key = key(run_id, config)
    progress = batch(store, batch_key)
    saved = {r['source_row']: dict(r) for r in store.conn.execute(
        'SELECT * FROM company_queue WHERE run_id=? AND revision=?', batch_key)}
    allowed, _ = _remaining_rows(store, run_id, config)
    allowed = {row['source_row'] for row in allowed}
    attempts = {r['job_key']: r for r in store.conn.execute(
        "SELECT id,company,status,reason,job_key FROM attempts WHERE run_id=? AND channel='web' ORDER BY started,id", (run_id,))}
    attempted_companies = {a['company'] for a in attempts.values()}
    rows = []
    for item in config['company_rows']:
        number = item['source_row']
        stored = saved.get(number, {})
        state = stored.get('state', 'pending_open')
        reason = stored.get('reason', '')
        attempt_id = stored.get('attempt_id')
        # 旧任务没有队列行，靠原行/岗位稳定标识恢复。公司级防重阻止其他行接管该申请。
        roles = [item['job']] if item.get('job') else config.get('target_roles', [])
        candidates = [a for blocked, jobs in ((False, roles), (True, [*roles, item.get('job') or _BLOCKED_JOB]))
                      for job in jobs if (a := attempts.get(_entry_key(item, job, blocked)))
                      and a['status'] not in ('skipped', 'failed')] if item['company'] in attempted_companies else []
        match = next((a for a in candidates if a['id'] == attempt_id),
                     next((a for a in candidates if a['status'] != 'no_match'), next(iter(candidates), None)))
        if match:
            attempt_id = match['id']
            if match['status'] in ('success', 'manual_submitted', 'uncertain'):
                state = 'submitted' if match['status'] == 'success' else match['status']
                reason = match['reason']
            elif match['status'] == 'needs_user':
                reason = match['reason']
                if reason == _READY_REASON:
                    state = 'ready_for_review'
                    if stored.get('checked_at') and not stored.get('tab_available'):
                        reason = '原标签已关闭，网页草稿状态未知'
                else:
                    state = 'manual_required'
            elif match['status'] == 'no_match' and number not in allowed:
                state, reason = 'no_match', match['reason']
        if number not in allowed and state in RETRYABLE and not (match and match['status'] == 'needs_user'):
            state, reason = 'skipped', '已有申请或待核查记录，本行跳过'
        rows.append(dict(source_row=number, company=item['company'], url=item['url'], job=item.get('job') or '',
                         state=state, status=state, reason=reason, attempt_id=attempt_id,
                         checked_at=stored.get('checked_at'), target_id=stored.get('target_id', ''),
                         tab_available=bool(stored.get('tab_available')), generation=stored.get('generation'),
                         destination_url=stored.get('destination_url', ''), destination_job=stored.get('destination_job', '')))
    return dict(phase=progress['phase'] if progress else 'prepare', reason=progress['reason'] if progress else '',
                scope_revision=batch_key[1], rows=rows)


def row(rows, number):
    if type(number) is not int:
        raise ValueError('请选择本轮公司清单中的行')
    result = next((r for r in rows if r['source_row'] == number), None)
    if result is None:
        raise ValueError('公司不在本轮授权范围内')
    return result


def eligible_rows(rows, generation):
    return [r for r in rows if r['generation'] == generation and r['state'] in ELIGIBLE and r['tab_available']]


def require_eligible(store, run_id, config, number, *, in_order=False):
    details = view(store, run_id, config)
    if details['phase'] != 'filling':
        raise ValueError('请先完成本轮网页检查')
    rows = details['rows']
    entry = row(rows, number)
    eligible = eligible_rows(rows, current_generation(store, run_id))
    if not any(r['source_row'] == entry['source_row'] for r in eligible):
        raise ValueError('该行尚未通过本轮页面检查，或已填写、提交或转交处理')
    if in_order and eligible[0]['source_row'] != entry['source_row']:
        raise ValueError('请按公司表顺序先处理本轮最靠前的可填写行')
    return entry


def update(store, batch_key, number, **fields):
    columns = ','.join(f'{name}=?' for name in fields)
    store.conn.execute(f'UPDATE company_queue SET {columns} WHERE run_id=? AND revision=? AND source_row=?',
                       (*fields.values(), *batch_key, number))


def _phase(store, batch_key, phase, reason):
    store.conn.execute('UPDATE company_batches SET phase=?,reason=? WHERE run_id=? AND revision=?',
                       (phase, reason, *batch_key))


def _release_attempt(store, attempt_id):
    if not attempt_id:
        return
    from .company_web import _KEY_PREFIX, _unresolved
    attempt = store.one('attempts', attempt_id)
    if attempt['status'] != 'needs_user' or _unresolved(store, attempt_id):
        raise ValueError('申请已有提交或状态已变化，请先核查结果')
    # 已确认岗位可继续原申请；未选定岗位的占位记录不能取得后续填写权限。
    status = 'skipped' if attempt['job_key'].startswith(_KEY_PREFIX + 'blocked:') else 'in_progress'
    store._set_attempt(attempt_id, status, '用户继续检查当前网页')
    store.conn.execute('UPDATE attention SET resolved=1 WHERE subject=? AND resolved=0', (attempt_id,))


def _target(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-zA-Z0-9:\-]{1,200}', value):
        raise ValueError('公司网页标签绑定无效')
    return value


def prepare(store, run_id, config, prepare_only=False):
    bk = key(run_id, config)
    store.interrupt_company_submissions(run_id)
    store.conn.execute("INSERT OR IGNORE INTO company_batches(run_id,revision,phase) VALUES (?,?,'prepare')", bk)
    store.conn.executemany('INSERT OR IGNORE INTO company_queue(run_id,revision,source_row) VALUES (?,?,?)',
                           ((*bk, item['source_row']) for item in config['company_rows']))
    result = view(store, run_id, config)
    phase = 'prepare' if prepare_only or result['phase'] == 'prepare' else 'check'
    _phase(store, bk, 'prepare' if phase == 'prepare' else 'checking', '')
    return {**result, 'phase': phase, 'occupied_targets': occupied_targets(store)}


def _rest(store, run_id, config, rows, *, preparing=False):
    states = {r['state'] for r in rows}
    deferred = next((r['reason'] for r in rows if r['state'] == 'pending_open' and r['reason']), '')
    if preparing and any(r['tab_available'] and r['state'] in RETRYABLE for r in rows):
        phase, message = 'waiting_login', '请在本批已打开的网页登录，再点击「我已登录，开始填写」。'
    elif 'login_required' in states:
        phase, message = 'waiting_login', '本批仍有公司需要登录或验证，完成后继续检查。'
    elif 'pending_open' in states or 'opened' in states or states.intersection(ELIGIBLE):
        phase, message = 'waiting_login', deferred or '还有待打开或待检查的网站。请查看对应行的原因，再继续检查。'
    elif 'manual_required' in states:
        phase, message = 'waiting_review', '请处理队列中的待办事项，然后继续检查。'
    elif 'ready_for_review' in states:
        phase, message = 'waiting_review', '填写结束，待你核对并在网页提交。'
    else:
        phase, message = 'completed', '本轮所有公司均已检查。'
    _phase(store, key(run_id, config), phase, message)
    if phase == 'completed':
        store.conn.execute("UPDATE runs SET status='completed' WHERE id=?", (run_id,))
        store.conn.execute("UPDATE app_sessions SET state='completed',error='' WHERE run_id=?", (run_id,))
        return {'state': 'completed', 'message': message}
    queue_control_in_transaction(store, run_id, 'paused', cancel_unclaimed=True)
    store.conn.execute("UPDATE app_sessions SET state='paused',error='' WHERE run_id=?", (run_id,))
    return {'state': 'paused', 'message': message}


def record_checks(store, run_id, config, request):
    from .company_web import _short_text, _remaining_rows, _record_history_skips
    bk = key(run_id, config)
    details = view(store, run_id, config)
    phase = request.get('phase')
    if phase not in ('prepare', 'check') or details['phase'] != ('prepare' if phase == 'prepare' else 'checking'):
        raise ValueError('本轮页面检查阶段已变化，请重新打开任务')
    checks = request.get('checks')
    if not isinstance(checks, list):
        raise ValueError('页面检查结果无效')
    stamp, generation = now(), current_generation(store, run_id)
    by_number = {r['source_row']: r for r in details['rows']}
    seen = set()
    for check in checks:
        if not isinstance(check, dict):
            raise ValueError('页面检查结果无效')
        number, status = check.get('source_row'), check.get('status')
        if type(number) is not int or number not in by_number or number in seen:
            raise ValueError('页面检查包含本轮范围外或重复的行')
        seen.add(number)
        previous = by_number[number]
        target = check.get('target_id') or ''
        available = check.get('tab_available', bool(target))
        if type(available) is not bool or available and not target:
            raise ValueError('公司网页绑定状态无效')
        if target:
            _target(target)
        fields = dict(target_id=target, tab_available=available, checked_at=stamp, generation=generation)
        if target != previous['target_id']:
            fields.update(destination_url='', destination_job='')
        reason = _short_text(check['reason'], 500, '公司网页状态原因无效') if check.get('reason') else ''
        if previous['state'] in RETRYABLE:
            if status not in CHECK_STATES:
                raise ValueError('公司网页状态无效')
            state = CHECK_STATES[status]
            if state in ELIGIBLE and not available:
                raise ValueError('可填写的公司网页缺少绑定')
            if phase == 'prepare' and state in ELIGIBLE:
                state = 'opened'
            if previous['state'] == 'manual_required' and phase == 'check' and state in ELIGIBLE | {'login_required'} and previous['attempt_id']:
                attempt = store.one('attempts', previous['attempt_id'])
                if attempt['status'] == 'needs_user':
                    _release_attempt(store, attempt['id'])
            fields.update(state=state, reason=reason)
        elif target and target != previous['target_id']:
            raise ValueError('已结束的行不能更换其浏览器标签')
        update(store, bk, number, **fields)
    for number, previous in by_number.items():
        if number not in seen and previous['state'] in ELIGIBLE:
            update(store, bk, number, generation=None, state='opened' if previous['tab_available'] else 'pending_open',
                   reason='本轮尚未检查此网页')
    rows = view(store, run_id, config)['rows']
    count = len(eligible_rows(rows, generation))
    _record_history_skips(store, run_id, _remaining_rows(store, run_id, config)[1])
    if phase == 'check' and count:
        message = f'本轮有 {count} 家公司可继续检查并填写。'
        _phase(store, bk, 'filling', message)
        outcome = {'state': 'running', 'message': message}
    else:
        outcome = _rest(store, run_id, config, rows, preparing=phase == 'prepare' and any(r['state'] in RETRYABLE for r in rows))
    return {'eligible_count': count, **outcome}


def finish(store, run_id, config):
    rows = view(store, run_id, config)['rows']
    eligible = eligible_rows(rows, current_generation(store, run_id))
    outstanding = [r for r in rows if r['state'] in RETRYABLE]
    if eligible:
        outcome = {'state': 'incomplete', 'message': '本轮仍有可填写的公司，请继续处理。'}
    else:
        outcome = _rest(store, run_id, config, rows)
    return {**outcome, 'completed': outcome['state'] == 'completed', 'eligible_count': len(eligible), 'pending_count': len(outstanding),
            'pending_companies': [r['company'] for r in outstanding[:3]]}


def control(store, run_id, config, request):
    from .company_web import _short_text, _job_key, _origin
    bk = key(run_id, config)
    operation = request['operation']
    with store.conn:
        store.conn.execute('BEGIN IMMEDIATE')
        store.check_run(run_id, 'fill')
        if operation == 'prepare_batch':
            return prepare(store, run_id, config, prepare_only=request.get('prepare_only') is True)
        if not batch(store, bk):
            raise ValueError('请先打开本批公司网页')
        if operation == 'record_checks':
            return record_checks(store, run_id, config, request)
        if operation == 'bind_opened':
            details = view(store, run_id, config)
            entry = row(details['rows'], request.get('source_row'))
            if details['phase'] not in ('prepare', 'checking') or entry['state'] not in RETRYABLE:
                raise ValueError('仅能在本批准备或检查时绑定待处理公司的标签')
            update(store, bk, entry['source_row'], target_id=_target(request.get('target_id')), tab_available=True,
                   destination_url='', destination_job='')
            return {'bound': True}
        entry = require_eligible(store, run_id, config, request.get('source_row'), in_order=True)
        if operation == 'select_row':
            store.conn.execute("""UPDATE company_queue SET state='available',reason='等待继续核对'
                WHERE run_id=? AND revision=? AND state='filling' AND source_row!=?""", (*bk, entry['source_row']))
            update(store, bk, entry['source_row'], state='filling', reason='正在查看岗位和网申表单')
            return {'selected': True}
        if operation == 'bind_target':
            target = _target(request.get('target_id'))
            if target != entry['target_id']:
                update(store, bk, entry['source_row'], target_id=target, tab_available=True, destination_url='', destination_job='')
            return {'bound': True}
        if operation == 'bind_destination':
            target = _target(request.get('target_id'))
            job = _short_text(request.get('job'), 200, '岗位不在本轮授权范围内')
            roles = [entry['job']] if entry['job'] else config.get('target_roles', [])
            if not any(_job_key(role) == _job_key(job) for role in roles):
                raise ValueError('岗位不在本轮授权范围内')
            source = clean_destination(request.get('entry_url'))
            destination = clean_destination(request.get('destination_url'))
            tenant_prefix(destination)
            if not matches_entry(entry['url'], source) or target.split(':', 1)[0] != entry['target_id'].split(':', 1)[0]:
                raise ValueError('招聘入口与本轮公司网页绑定不一致')
            if _origin(source) == _origin(destination) and not matches_entry(entry['url'], destination):
                raise ValueError('目标招聘平台与原公司的租户不一致')
            update(store, bk, entry['source_row'], target_id=target, tab_available=True,
                   destination_url=destination, destination_job=job)
            return {'bound': True}
        if operation == 'wait_login':
            reason = _short_text(request.get('reason'), 500, '请说明当前页面的登录或验证要求')
            update(store, bk, entry['source_row'], state='login_required', reason=reason)
            return {'status': 'login_required', 'paused': False}
        raise ValueError('未知公司队列操作')


def clean_destination(value):
    from .inputs import web_url
    parts = urlsplit(web_url(value))
    path = parts.path or '/'
    if (parts.scheme != 'https' or parts.username is not None or parts.password is not None or
            any(ch in unquote(path) for ch in ('\\', '?', '#', '%')) or
            re.search(r'%2f|%5c', path, re.I) or any(v in ('.', '..') for v in unquote(path).split('/'))):
        raise ValueError('招聘表单须使用可核对的 HTTPS 地址')
    return urlunsplit((parts.scheme, parts.netloc, path, '', ''))


def matches_destination(entry, job, url, target):
    from .company_web import _origin, _job_key
    approved = entry['destination_url']
    if not approved or target != entry['target_id'] or _job_key(job) != _job_key(entry['destination_job']):
        return False
    actual = clean_destination(url)
    if _origin(actual) != _origin(approved):
        return False
    return tenant_prefix(approved) == tenant_prefix(actual)


MOKA_HOSTS = {'app.mokahr.com', 'app-tc.mokahr.com'}
BOARD_HOSTS = {'jobs.lever.co', 'boards.greenhouse.io', 'job-boards.greenhouse.io'}


def matches_entry(approved, observed):
    from .company_web import _origin
    if _origin(approved) != _origin(observed):
        return False
    # 多家公司共享同一 ATS 域名时，直接导入的表单也须留在原租户。
    if urlsplit(approved).hostname in MOKA_HOSTS | BOARD_HOSTS:
        return tenant_prefix(clean_destination(approved)) == tenant_prefix(clean_destination(observed))
    return True


def tenant_prefix(url):
    parts = urlsplit(url)
    host = parts.hostname
    slug = r'[A-Za-z0-9_-]{1,80}'
    if parts.port in (None, 443):
        if host.endswith(('.zhiye.com', '.myworkdayjobs.com')):
            name = host.split('.')[0]
            if name not in ('www', 'jobs', 'campus', 'login', 'm', 'passport') and re.fullmatch(slug, name):
                return '/'
        if host in MOKA_HOSTS:
            found = re.match(rf'^/(apply|campus_apply|social-recruitment|campus-recruitment)/{slug}(?=/|$)', parts.path)
            if found:
                return found.group()
        if host in BOARD_HOSTS:
            found = re.match(rf'^/{slug}(?=/|$)', parts.path)
            if found:
                return found.group()
    raise ValueError('目标招聘平台的租户无法核对')


def user_operation(store, method, params):
    from .app_runs import app_session
    run_id = params['run_id']
    app_session(store, run_id)
    with store.conn:
        store.conn.execute('BEGIN IMMEDIATE')
        run = store.one('runs', run_id)
        config = json.loads(run['config'])
        if config.get('simulation') or config.get('channels') != ['web'] or not config.get('company_rows'):
            raise ValueError('本轮未授权公司网申')
        bk = key(run_id, config)
        rows = view(store, run_id, config)['rows']
        entry = row(rows, params.get('source_row'))
        if method == 'company_queue_row':
            return entry
        if method == 'company_lost_target':
            if entry['state'] != 'ready_for_review' or entry['target_id'] != _target(params.get('target_id')):
                raise ValueError('该行待核对的标签绑定已变化')
            update(store, bk, entry['source_row'], tab_available=False, checked_at=now(), reason='原标签已关闭，网页草稿状态未知')
            return {'tab_available': False}
        if entry['state'] != 'ready_for_review' or config.get('web_submit') or not entry['attempt_id']:
            raise ValueError('仅可处理本轮已填写待核对的申请')
        from .company_web import _unresolved
        attempt_id = entry['attempt_id']
        if _unresolved(store, attempt_id):
            raise ValueError('请先核查已登记的提交结果')
        if method == 'company_mark_submitted':
            store.conn.execute("UPDATE attempts SET status='manual_submitted',reason=?,updated=? WHERE id=? AND status='needs_user'",
                               (MANUAL_SUBMITTED, now(), attempt_id))
            update(store, bk, entry['source_row'], state='manual_submitted', reason=MANUAL_SUBMITTED)
            store.conn.execute('UPDATE attention SET resolved=1 WHERE subject=? AND resolved=0', (attempt_id,))
            if run['status'] == 'paused' and all(r['state'] not in RETRYABLE | {'ready_for_review'} for r in view(store, run_id, config)['rows']):
                _rest(store, run_id, config, view(store, run_id, config)['rows'])
            return {'status': 'manual_submitted'}
        if run['status'] not in ('paused', 'completed') or entry['tab_available']:
            raise ValueError('请在暂停后确认原核对标签已关闭，再重新打开检查')
        _release_attempt(store, attempt_id)
        update(store, bk, entry['source_row'], state='pending_open', reason='请重新打开并检查网页草稿',
               target_id='', tab_available=False, generation=None)
        _phase(store, bk, 'waiting_login', '请继续检查，重新打开网页并核对其草稿状态。')
        store.conn.execute("UPDATE runs SET status='paused' WHERE id=?", (run_id,))
        return {'status': 'pending_open'}
