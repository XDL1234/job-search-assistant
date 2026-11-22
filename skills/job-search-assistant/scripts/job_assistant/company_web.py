"""公司网申的原生执行接口，校验本轮授权、填写资料与提交结果。"""
import hashlib
import json
from pathlib import Path
import re
import unicodedata
from urllib.parse import urlsplit

from .app_executor import _web_field_value
from .app_runs import app_session, control_run
from .cli import verify_materials
from .inputs import web_url
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
    if _origin(url) != _origin(row['url']):
        raise ValueError('页面与本轮授权的网站不一致')
    key = _KEY_PREFIX + ('blocked:' if blocked else 'job:') + hashlib.sha256(
        json.dumps([source_row, row['company'], row['url'], _job_key(job)], ensure_ascii=False).encode()
    ).hexdigest()
    with store.conn:
        store.conn.execute('BEGIN IMMEDIATE')
        store.check_run(run_id, 'fill')
        existing = store.conn.execute('''SELECT id, url FROM attempts WHERE run_id=? AND channel='web'
            AND job_key=? AND status='in_progress' ''', (run_id, key)).fetchone()
        if existing:
            if existing['url'] != url or _unresolved(store, existing['id']):
                raise ValueError('本次申请已有不同页面或存在未决提交，请核查投递记录')
            return {'attempt_id': existing['id']}
        identity = store._begin_attempt(run_id, row['company'], url, job, 'web', key)
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
    if operation == 'scope':
        ready_companies = {item[0] for item in store.conn.execute(
            "SELECT company FROM attempts WHERE run_id=? AND channel='web' AND status='needs_user' AND reason=?",
            (run_id, _READY_REASON),
        )}
        rows = [{**{key: row[key] for key in ('source_row', 'company', 'url')}, 'job': row.get('job', '')}
                for row in config['company_rows'] if row['company'] not in ready_companies]
        return {'rows': rows, 'target_roles': config.get('target_roles', []), 'web_submit': config.get('web_submit') is True}
    if operation == 'begin':
        return _begin(store, run_id, config, request)

    attempt = store.one('attempts', request.get('attempt_id'))
    attempt_id = attempt['id']
    if attempt['run_id'] != run_id or attempt['channel'] != 'web' or not attempt['job_key'].startswith(_KEY_PREFIX):
        raise ValueError('申请不属于本轮官网操作')
    if attempt['status'] != 'in_progress':
        raise ValueError('申请已结束或需要人工核查')
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
    if operation == 'ready':
        if config.get('web_submit') is True:
            raise ValueError('请使用本轮已授权的自动提交方式')
        store.set_attempt(attempt_id, 'needs_user', _READY_REASON)
        attention = store.attention(attempt_id, _READY_REASON)
        control_run(store, {'run_id': run_id, 'action': 'paused'})
        return {'status': 'needs_user', 'attention': attention, 'paused': True}
    if operation in ('handoff', 'no_match'):
        reason = _short_text(request.get('reason'), 500, '须说明当前页面需要核查的事项')
        result = {}
        store.set_attempt(attempt_id, 'needs_user' if operation == 'handoff' else 'no_match', reason)
        if operation == 'handoff':
            result['attention'] = store.attention(attempt_id, '公司网申需要处理：' + reason)
        return result
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
        store.finish_action(action_id, status, reason=reason, native_web=native_web)
        result = {'status': status}
        if status == 'uncertain':
            result['attention'] = store.attention(attempt_id, '公司网申提交待核查：' + reason)
        return result
    raise ValueError('未知的公司网申操作')
