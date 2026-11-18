"""将当前浏览器核验过的筛选范围绑定给已授权轮次。"""
import json
from pathlib import Path
import re
import uuid
from urllib.parse import parse_qsl, urlencode, urlsplit

from .job_preferences import platform_values


def _list_path(path):
    return path in ('/', '/web/geek/jobs', '/web/geek/recommend', '/web/geek/job-recommend') or bool(
        re.fullmatch(r'/[a-z][a-z0-9-]*/', path))


def _listing(url):
    from .agent_browser import _boss_url
    parts = urlsplit(_boss_url(url))
    path = (parts.path.rstrip('/') if parts.path.startswith('/web/') else parts.path) or '/'
    if not _list_path(path):
        raise ValueError('请从 BOSS 的职位列表开始校验本轮筛选')
    # 页码和追踪标记不改变筛选范围，其他参数都必须保持一致。
    return path, sorted([key, value] for key, value in parse_qsl(parts.query, keep_blank_values=True)
                        if key not in ('ka', 'lid', 'page', 'pageNum') and value)


def list_scope(url):
    path, scope = _listing(url)
    if path != '/web/geek/jobs':
        raise ValueError('请在 BOSS 职位搜索列表核验已保存的筛选')
    return scope


def _checkpoint(root, run_id):
    if not re.fullmatch(r'[a-f0-9]{32}', run_id):
        raise ValueError('投递轮次标识无效')
    return Path(root) / 'agent-browser-state' / (run_id + '.filters.json')


def read_scope(root, run_id, config):
    path = _checkpoint(root, run_id)
    if not path.is_file():
        if 'boss_filters' not in config and not config.get('scope_revision'):
            return None
        raise ValueError('本轮尚未核验平台筛选，不能继续岗位操作')
    proof = json.loads(path.read_text(encoding='utf-8'))
    values = (platform_values(config['boss_filters'], config['employment_type'])
              if 'boss_filters' in config else None)
    if proof.get('values') != values or proof.get('revision') != config.get('scope_revision'):
        raise ValueError('核验过的平台筛选与本轮授权不一致')
    return proof


def check_list_scope(proof, url, filters=None):
    path, scope = _listing(url)
    if (path != proof.get('listing_path', '/web/geek/jobs') or scope != proof['scope']
            or 'filters' in proof and filters is not None and filters != proof['filters']):
        raise ValueError('BOSS 职位列表的筛选已变化，暂停任务以免超出投递范围')


def _apply(store, values, expected_target, check=None):
    from .agent_browser import Browser
    from .boss_filter_dom import apply_filters
    from .desktop import desktop_lock
    with desktop_lock(store.root):
        browser = Browser(store.root, check)
        if not expected_target or browser.config['pinned_target'] != expected_target:
            raise ValueError('用于核验筛选的 BOSS 标签发生变化')
        result = apply_filters(browser, values)
        scope = list_scope(result['url'])
        check_list_scope({'scope': scope}, browser.verify_target()['url'])
        return {**result, 'scope': scope, 'target': expected_target,
                'message': f'已在 BOSS 核验 {len(values)} 项筛选条件'}


def apply_saved_preferences(store, expected_target):
    from .job_preferences import read_preferences
    prefs = read_preferences(store)
    if prefs is None:
        raise ValueError('请先保存投递岗位信息')
    return _apply(store, platform_values(prefs['boss_filters'], prefs['employment_type']), expected_target)


def prepare_run_filters(store, run_id, expected_target):
    from .app_runs import app_session, require_resolved_actions
    from .cli import verify_materials
    app_session(store, run_id)
    config = verify_materials(store, run_id)
    store.check_run(run_id, 'greet')

    def check():
        store.check_run(run_id, 'greet')
        verify_materials(store, run_id)

    path = _checkpoint(store.root, run_id)
    previous = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else None
    changed = bool(config.get('scope_revision') and (not previous or previous.get('revision') != config['scope_revision']))
    if previous and not changed:
        proof = read_scope(store.root, run_id, config)
        if proof['target'] == expected_target:
            _return_interrupted_open(store, run_id, config, proof, check)
            return {'message': '使用本轮已核验的 BOSS 筛选范围'}

    values = (platform_values(config['boss_filters'], config['employment_type'])
              if 'boss_filters' in config else None)
    rebound = bool(previous and not changed)
    old_scope = previous if rebound else None
    if not previous and not changed and values is None:
        old_scope, rebound = _legacy_binding(store, run_id, expected_target)
    if changed or rebound:
        require_resolved_actions(store, run_id)
    if old_scope is not None and rebound and values is not None:
        _observe_listing(store, expected_target, check, old_scope)
    if values is not None:
        result = _apply(store, values, expected_target, check)
        result['listing_path'] = '/web/geek/jobs'
        if old_scope is not None and old_scope['scope'] != result['scope']:
            raise ValueError('新标签未能还原本轮筛选。请编辑任务重新确认范围')
    else:
        result = ({**old_scope, 'target': expected_target, 'message': '使用本轮原岗位列表范围'}
                  if old_scope is not None and not rebound
                  else _observe_listing(store, expected_target, check, old_scope))
    check()
    if changed or rebound:
        _reset_search(store, run_id, expected_target, config)
    proof = {key: result[key] for key in ('target', 'scope', 'listing_path', 'filters') if key in result}
    proof.update(values=values, revision=config.get('scope_revision'))
    if old_scope is not None and not rebound:
        _return_interrupted_open(store, run_id, config, proof, check)
    _replace_json(path, proof)
    if rebound:
        result['message'] = '已在新 Chrome 标签还原并核验本轮范围，继续使用原有沟通记录'
    return result


def _return_interrupted_open(store, run_id, config, proof, check):
    from .agent_browser import Browser, _boss_url, browser_call
    from .desktop import desktop_lock
    source_path = store.root/'agent-browser-state'/(run_id+'.json')
    if not source_path.is_file():
        return
    if config.get('execution_mode') == 'script':
        state_path = store.root/'script-state'/(run_id+'.json')
        if not state_path.is_file():
            return
        state = json.loads(state_path.read_text(encoding='utf-8'))
        card = state.get('card') if state['phase'] == 'open' else None
    else:
        row = store.conn.execute('SELECT state FROM app_ai_selection WHERE run_id=?', (run_id,)).fetchone()
        if not row:
            return
        state = json.loads(row[0])
        if state['phase'] == 'collect_open':
            card = next(iter(state['queue']), None)
        elif state['phase'] == 'execute_open':
            card = next(iter(state['approved']), {}).get('card')
        else:
            card = None
    if not card:
        return
    target = proof['target']
    source = json.loads(source_path.read_text(encoding='utf-8'))
    if source.get('target_id') != target or source.get('source_card', {}).get('key') != card['key']:
        return
    detail = urlsplit(_boss_url(source['navigate'])).path
    if detail != card['key'] or not detail.startswith('/job_detail/'):
        return
    with desktop_lock(store.root):
        browser = Browser(store.root, check)
        if browser.config['pinned_target'] != target:
            raise ValueError('用于核验筛选的 BOSS 标签发生变化')
        if urlsplit(browser.verify_target()['url']).path == detail:
            browser_call(store.root, 'return', run_id, check=check, expected_target=target, filter_scope=proof)


def _replace_json(path, value):
    path.parent.mkdir(exist_ok=True)
    temporary = path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)


def _legacy_binding(store, run_id, target):
    root = store.root/'agent-browser-state'
    source = root/(run_id+'.json')
    registry = root/(run_id+'.candidates.json')
    state_path = store.root/'script-state'/(run_id+'.json')
    source = json.loads(source.read_text(encoding='utf-8')) if source.is_file() else {}
    registry = json.loads(registry.read_text(encoding='utf-8')) if registry.is_file() else {}
    state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.is_file() else {}
    ai = store.conn.execute('SELECT state FROM app_ai_selection WHERE run_id=?', (run_id,)).fetchone()
    ai = json.loads(ai[0]) if ai else {}
    targets = {value for value in (source.get('target_id'), registry.get('target_id'), state.get('target'), ai.get('target')) if value}
    if not targets:
        return None, False
    observations = [(source['return_url'], source.get('return_filters', []))] if source.get('return_url') else [
        (item['list_url'], item.get('filters', [])) for item in registry.get('candidates', {}).values() if item.get('list_url')]
    rebound = targets != {target}
    if not observations:
        if not rebound:
            return None, False
        raise ValueError('原 BOSS 标签已更换，未找到可核验的旧筛选。请编辑任务并重新确认范围')
    url, filters = observations[0]
    path, scope = _listing(url)
    if any(_listing(url) != (path, scope) or labels != filters for url, labels in observations):
        raise ValueError('无法唯一还原旧 BOSS 筛选。请编辑任务并重新确认范围')
    return {'listing_path': path, 'scope': scope, 'filters': filters}, rebound


def _observe_listing(store, expected_target, check, restore):
    from .agent_browser import Browser, ORIGIN
    from .desktop import desktop_lock
    with desktop_lock(store.root):
        browser = Browser(store.root, check)
        if not expected_target or browser.config['pinned_target'] != expected_target:
            raise ValueError('用于核验筛选的 BOSS 标签发生变化')
        target = browser.verify_target()
        if restore is not None:
            path = restore.get('listing_path', '/web/geek/jobs')
            if not _list_path(path):
                raise ValueError('旧岗位列表路径不可核验')
            query = urlencode([tuple(item) for item in restore['scope']])
            browser.command('open', ORIGIN + path + ('?' + query if query else ''))
        else:
            _listing(target['url'])
        observed = browser.wait('observe', lambda row: (row.get('cards') or row.get('list_empty'))
                                and (restore is None or 'filters' not in restore or row.get('filters') == restore['filters']))
        path, scope = _listing(observed['url'])
        if not (observed.get('cards') or observed.get('list_empty')):
            raise ValueError('当前 Chrome 标签未识别到 BOSS 岗位列表，请打开岗位列表后恢复')
        if restore is not None:
            try:
                check_list_scope(restore, observed['url'], observed['filters'])
            except ValueError as error:
                raise ValueError('新标签无法还原本轮筛选。请编辑任务重新确认范围') from error
        return {'target': expected_target, 'url': observed['url'], 'listing_path': path, 'scope': scope,
                'filters': observed['filters'],
                'message': '已核验当前 BOSS 列表范围'}


def _reset_search(store, run_id, target, config):
    from .ai_selection import digest
    from .app_decisions import current_generation
    from .app_runs import require_resolved_actions, reset_unsent_attempts
    require_resolved_actions(store, run_id)
    script = store.root/'script-state'/(run_id+'.json')
    if script.is_file():
        _replace_json(script, {'phase': 'list', 'target': target})
    candidates = store.root/'agent-browser-state'/(run_id+'.candidates.json')
    if candidates.is_file():
        _replace_json(candidates, {'protocol': 1, 'target_id': target, 'candidates': {}})
    with store.conn:
        store.conn.execute('BEGIN IMMEDIATE')
        store.check_run(run_id, 'greet')
        require_resolved_actions(store, run_id)
        empty = {'phase': 'list', 'page_at': 'list', 'target': target, 'generation': current_generation(store, run_id),
                 'config_hash': digest(config), 'seen': [], 'approved': []}
        store.conn.execute('UPDATE app_ai_selection SET state=? WHERE run_id=?', (json.dumps(empty), run_id))
        reset_unsent_attempts(store, run_id, '任务范围或浏览器标签已重新核验，原岗位尚未发送')
