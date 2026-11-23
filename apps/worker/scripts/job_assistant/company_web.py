"""公司网申的原生执行接口，校验本轮授权、填写资料与提交结果。"""
import hashlib
import json
from pathlib import Path
import re
import unicodedata
from urllib.parse import urlsplit
import uuid

from .app_executor import _web_field_value
from . import company_queue
from .app_runs import app_session
from .cli import verify_materials
from .inputs import web_url
from .store import now
from .panel_state import queue_control_in_transaction
from .web_autofill import BASIC, GROUPS


_PROFILE_FIELDS = frozenset(BASIC.values())
_RECORD_FIELDS = {group: frozenset(fields.values()) | {'current'} for group, fields in GROUPS.items()}
_BLOCKED_JOB = '待确认岗位'
_KEY_PREFIX = 'company-web:'
_READY_REASON = '已填写，等待你在当前网页确认并提交。'


def _job_key(value):
    return ' '.join(unicodedata.normalize('NFKC', value).split()).casefold()


def _origin(value):
    url = urlsplit(web_url(value))
    return url.scheme, url.hostname, url.port or (443 if url.scheme == 'https' else 80)


def _short_text(value, maximum, error):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(error)
    if any(unicodedata.category(char) in ('Cc', 'Cf') for char in value):
        raise ValueError(error)
    return value.strip()


def _value(config, source):
    if source == 'resume':
        return _web_field_value(config, source)
    if not isinstance(source, str) or not source.startswith('profile.') or len(source) > 160:
        raise ValueError('请选择本轮已保存的资料字段')
    key = source.removeprefix('profile.')
    row = key.split('.')
    record = (len(row) == 3 and row[1].isascii() and row[1].isdecimal()
              and len(row[1]) <= 3 and row[2] in _RECORD_FIELDS.get(row[0], ()))
    if key not in _PROFILE_FIELDS and not record:
        raise ValueError('该资料字段需要人工处理')
    return _web_field_value(config, source)


def _review(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{64}', value):
        raise ValueError('缺少有效的提交前核对结果')
    return value


def _entry_key(row, job, blocked):
    digest = hashlib.sha256(json.dumps(
        [row['source_row'], row['company'], row['url'], _job_key(job)], ensure_ascii=False,
    ).encode()).hexdigest()
    return _KEY_PREFIX + ('blocked:' if blocked else 'job:') + digest


def _remaining_rows(store, run_id, config):
    held = set()
    no_match = {}
    previous = {}
    companies = list(dict.fromkeys(row['company'] for row in config['company_rows']))
    revised = store.conn.execute("SELECT max(created) FROM panel_commands WHERE run_id=? AND action='revised'",
                                 (run_id,)).fetchone()[0]
    for item in store.conn.execute(
        """SELECT id, run_id, company, job, job_key, status, started FROM attempts
           WHERE channel='web' AND company IN (SELECT value FROM json_each(?))
           AND (status IN ('success','manual_submitted','uncertain','needs_user') OR (run_id!=? AND status='in_progress')
                OR (run_id=? AND status='no_match'))
           ORDER BY started DESC, id""", (json.dumps(companies), run_id, run_id),
    ):
        if item['run_id'] != run_id:
            previous.setdefault(item['company'], item)
        elif item['status'] == 'no_match':
            if not revised or item['started'] >= revised:
                no_match.setdefault(item['company'], []).append((item['job_key'], item['job']))
        else:
            held.add(item['company'])
    rows, skipped = [], {}
    for row in config['company_rows']:
        company = row['company']
        if company in held:
            continue
        if company in previous:
            skipped.setdefault(company, (row, previous[company]))
            continue
        if any(key == _entry_key(row, job, key.startswith(_KEY_PREFIX + 'blocked:'))
               for key, job in no_match.get(company, ())):
            continue
        rows.append({**{key: row[key] for key in ('source_row', 'company', 'url')}, 'job': row.get('job', '')})
    return rows, skipped


def _record_history_skips(store, run_id, skipped):
    status_text = {'in_progress': '有未完成的申请', 'success': '已确认提交', 'manual_submitted': '已由用户标记提交', 'uncertain': '提交结果待核查',
                   'needs_user': '有待人工处理的申请'}
    stamp = now()
    for row, previous in skipped.values():
        key = _KEY_PREFIX + 'history:' + previous['id']
        reason = '本轮跳过：此前任务' + status_text[previous['status']]
        store.conn.execute("""INSERT INTO attempts
            SELECT ?,?,?,?,?,?,?,?,?,?,? WHERE NOT EXISTS
            (SELECT 1 FROM attempts WHERE run_id=? AND job_key=?)""",
            (uuid.uuid4().hex, run_id, row['company'], row['url'], row.get('job') or _BLOCKED_JOB,
             'web', key, 'skipped', reason, stamp, stamp, run_id, key))


def _begin(store, run_id, config, request):
    source_row = request.get('source_row')
    if type(source_row) is not int or source_row < 2:
        raise ValueError('请选择本轮公司清单中的行')
    row = next((item for item in config['company_rows'] if item['source_row'] == source_row), None)
    if not row:
        raise ValueError('公司不在本轮授权范围内')
    job = request.get('job', '')
    blocked = request.get('blocked') is True
    roles = [row['job']] if row.get('job') else config.get('target_roles', [])
    if not blocked or job:
        job = _short_text(job, 200, '必须确认本轮授权的岗位')
        approved_job = next((value for value in roles if _job_key(value) == _job_key(job)), None)
        if not approved_job:
            raise ValueError('岗位不在本轮授权范围内')
        job = approved_job
    else:
        job = row.get('job') or _BLOCKED_JOB
    url = row['url'] if blocked else web_url(request.get('url'))
    key = _entry_key(row, job, blocked)
    with store.conn:
        store.conn.execute('BEGIN IMMEDIATE')
        store.check_run(run_id, 'fill')
        bk = company_queue.key(run_id, config)
        entry = company_queue.require_eligible(store, run_id, config, source_row, in_order=True) if company_queue.batch(store, bk) else None
        if not blocked and not company_queue.matches_entry(row['url'], url) and not (
                entry and company_queue.matches_destination(entry, job, url, request.get('target_id'))):
            raise ValueError('页面与本轮授权的网站不一致')
        existing = store.conn.execute('''SELECT id, url FROM attempts WHERE run_id=? AND channel='web'
            AND job_key=? AND status='in_progress' ''', (run_id, key)).fetchone()
        if existing:
            if _unresolved(store, existing['id']) or (
                    existing['url'] != url and not (entry and entry['attempt_id'] == existing['id'])):
                raise ValueError('本次申请已有不同页面或存在未决提交，请核查投递记录')
            identity = existing['id']
            if existing['url'] != url:
                store.conn.execute('UPDATE attempts SET url=?,updated=? WHERE id=?', (url, now(), identity))
        else:
            identity = store._begin_attempt(run_id, row['company'], url, job, 'web', key)
        if entry:
            company_queue.update(store, bk, source_row, state='filling', attempt_id=identity, reason='正在处理本行网页')
    return {'attempt_id': identity}


def _unresolved(store, attempt_id):
    return store.conn.execute("""SELECT 1 FROM actions WHERE attempt_id=?
        AND status IN ('pending','executing','uncertain','succeeded')""", (attempt_id,)).fetchone()


def company_web(store, run_id, request):
    app_session(store, run_id)
    store.check_run(run_id, 'fill')
    config = verify_materials(store, run_id)
    if config.get('simulation') is True or config.get('channels') != ['web'] or not config.get('company_rows'):
        raise ValueError('本轮未授权公司网申')
    if not isinstance(request, dict):
        raise ValueError('网申操作参数无效')
    operation = request.get('operation')
    bk = company_queue.key(run_id, config)
    if operation in ('prepare_batch', 'record_checks', 'select_row', 'wait_login', 'bind_target', 'bind_opened', 'bind_destination'):
        return company_queue.control(store, run_id, config, request)
    queued = company_queue.batch(store, bk)
    if operation == 'scope':
        rows, skipped = _remaining_rows(store, run_id, config)
        result = {'historical_count': len(skipped), 'target_roles': config.get('target_roles', []),
                  'web_submit': config.get('web_submit') is True}
        if queued:
            from .app_decisions import current_generation
            rows = (company_queue.eligible_rows(company_queue.view(store, run_id, config)['rows'], current_generation(store, run_id))
                    if queued['phase'] == 'filling' else [])
            result['occupied_targets'] = company_queue.occupied_targets(store)
        return {'rows': rows, **result}
    if operation == 'finish_batch':
        with store.conn:
            store.conn.execute('BEGIN IMMEDIATE')
            config = store.check_run(run_id, 'fill')
            remaining, skipped = _remaining_rows(store, run_id, config)
            _record_history_skips(store, run_id, skipped)
            if queued:
                return {**company_queue.finish(store, run_id, config), 'historical_count': len(skipped)}
            if not remaining:
                store.conn.execute("UPDATE runs SET status='completed' WHERE id=?", (run_id,))
                store.conn.execute("UPDATE app_sessions SET state='completed',error='' WHERE run_id=?", (run_id,))
        names = list(dict.fromkeys(row['company'] for row in remaining))
        return {'completed': not remaining, 'pending_count': len(names), 'pending_companies': names[:3],
                'historical_count': len(skipped)}
    if operation == 'begin':
        return _begin(store, run_id, config, request)

    attempt = store.one('attempts', request.get('attempt_id'))
    attempt_id = attempt['id']
    if attempt['run_id'] != run_id or attempt['channel'] != 'web' or not attempt['job_key'].startswith(_KEY_PREFIX):
        raise ValueError('申请不属于本轮官网操作')
    if attempt['status'] != 'in_progress':
        raise ValueError('申请已结束或需要人工核查')
    if queued:
        linked = store.conn.execute('SELECT source_row FROM company_queue WHERE run_id=? AND revision=? AND attempt_id=?',
                                    (*bk, attempt_id)).fetchone()
        if not linked:
            raise ValueError('申请不属于本次检查的公司网页')
        company_queue.require_eligible(store, run_id, config, linked[0])
    if operation not in ('handoff', 'no_match') and attempt['job_key'].startswith(_KEY_PREFIX + 'blocked:'):
        raise ValueError('须先确认本轮授权的岗位，才能填写或提交')
    if operation not in ('claim', 'settle') and _unresolved(store, attempt_id):
        raise ValueError('申请存在未决提交，请先核查结果')

    if operation == 'value':
        return {'value': _value(config, request.get('source'))}
    if operation == 'field':
        label = _short_text(request.get('label'), 200, '网页字段名称无效')
        source = request.get('source')
        value = _value(config, source)
        store.record_field(attempt_id, label, Path(value).name if source == 'resume' else value, source, run_id)
        return {'recorded': True}
    if operation in ('ready', 'handoff', 'no_match'):
        if operation == 'ready' and config.get('web_submit') is True:
            raise ValueError('请使用本轮已授权的自动提交方式')
        reason = _READY_REASON if operation == 'ready' else _short_text(request.get('reason'), 500, '须说明当前页面需要核查的事项')
        with store.conn:
            store.conn.execute('BEGIN IMMEDIATE')
            store.check_run(run_id, 'fill')
            if store.one('attempts', attempt_id)['status'] != 'in_progress' or _unresolved(store, attempt_id):
                raise ValueError('本次申请状态已变化，请先核查结果')
            if operation == 'no_match':
                store._set_attempt(attempt_id, 'no_match', reason)
                return {}
            store._set_attempt(attempt_id, 'needs_user', reason)
            if operation == 'handoff':
                return {'attention': store._attention(attempt_id, '公司网申需要处理：' + reason)}
            attention = store._attention(attempt_id, reason)
            if queued:
                company_queue.update(store, bk, linked[0], state='ready_for_review', reason=reason)
                return {'status': 'needs_user', 'attention': attention, 'paused': False}
            queue_control_in_transaction(store, run_id, 'paused', cancel_unclaimed=True)
            return {'status': 'needs_user', 'attention': attention, 'paused': True}
    if operation in ('prepare', 'claim', 'settle'):
        if config.get('web_submit') is not True:
            raise ValueError('本轮仅授权填写，未授权提交')
        if operation == 'prepare':
            review = _review(request.get('review'))
            action = store.prepare_action(run_id, attempt_id, 'submit', attempt['company'],
                                          {'web_review': review}, native_web=True)
            return {'action_id': action}

        action = store.one('actions', request.get('action_id'))
        action_id = action['id']
        payload = json.loads(action['payload'])
        if (action['run_id'] != run_id or action['attempt_id'] != attempt_id or action['kind'] != 'submit'
                or not payload.get('web_review')):
            raise ValueError('提交动作不属于本次申请')
        native_web = payload.get('native_web_receipt') is True
        if operation == 'claim':
            if payload['web_review'] != _review(request.get('review')):
                raise ValueError('提交前核对已变化，请重新核查')
            store.claim_action(run_id, action_id, native_web=native_web)
            return {'claimed': True}

        status = request.get('status')
        if action['status'] != 'executing' or status not in ('succeeded', 'uncertain'):
            raise ValueError('须先实际执行提交，且只能记录已核验或待核查的结果')
        reason = _short_text(request.get('reason'), 500, '须说明页面核验结果')
        attention = store.finish_action(action_id, status, reason=reason, native_web=native_web)
        result = {'status': status}
        if attention:
            result['attention'] = attention
        return result
    raise ValueError('未知的公司网申操作')
