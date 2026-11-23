"""供原生协调器调用的受限执行工具，不接受模型更改轮次授权。"""
import base64
import json
from pathlib import Path

from .app_runs import app_session, MANUAL_WEB_PROFILE_FIELDS
from .cli import dispatch, read_profile, verify_materials
from .panel_state import check_fresh_frame

ALLOWED = {'status', 'bind', 'capture', 'act', 'begin', 'field', 'evidence', 'prepare', 'finish',
           'attempt_status', 'route_message', 'handoff', 'select_job', 'tick', 'verify_materials', 'browser', 'boss_decide'}


def _web_field_value(config, source):
    if source == 'resume' and config.get('resume_path'):
        return config['resume_path']
    if not isinstance(source, str) or len(source) > 160 or not source.startswith('profile.'):
        raise ValueError('请指定有值的个人信息字段或本轮已确认的简历')
    parts = source.split('.')[1:]
    if parts[0] in ('sources', 'resume_text'):
        raise ValueError('请使用已保存的具体个人信息字段')
    if parts[0] in MANUAL_WEB_PROFILE_FIELDS:
        raise ValueError('证件类型、其他证件号码和证明附件需要手动处理')
    value = read_profile(config)
    for part in parts:
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, list) and part.isascii() and part.isdecimal() and int(part) < len(value):
            value = value[int(part)]
        else:
            value = None
            break
    if isinstance(value, str) and value.strip():
        return value
    if type(value) in (bool, int, float):
        return json.dumps(value, allow_nan=False)
    raise ValueError('该个人信息尚未填写，已阻止输入')


def _check_web_request(config, request, attempt, action):
    op, purpose = request['operation'], request.get('purpose')
    if attempt and attempt['status'] == 'needs_user' and (
            op in ('field', 'prepare', 'finish') or op == 'act' and purpose in ('fill', 'commit')):
        raise ValueError('这份申请需要人工处理，已阻止继续填写或提交')
    if op == 'attempt_status' and attempt and attempt['status'] == 'needs_user' and request.get('status') != 'needs_user':
        raise ValueError('自动化不能恢复人工处理的申请')
    if op == 'attempt_status' and request.get('status') == 'needs_user' and not isinstance(request.get('reason', ''), str):
        raise ValueError('请说明需要人工处理的原因')
    if op == 'finish' and request.get('status') == 'succeeded' and (
            not action or action['status'] not in ('executing', 'uncertain')):
        raise ValueError('必须实际执行本次提交后才能核验成功')
    if op == 'field':
        value = _web_field_value(config, request.get('source'))
        if 'value' in request and request['value'] != value:
            raise ValueError('登记值必须与本轮个人信息一致')
        request['value'] = '已填证件号码' if request['source'] == 'profile.id_number' else value
        return
    if op != 'act':
        return
    operation = request['input']
    if not isinstance(operation, dict):
        raise ValueError('填写操作无效')
    if purpose == 'fill':
        if not attempt:
            raise ValueError('填写官网表单前请登记本轮申请')
        value = _web_field_value(config, request.get('field_source'))
        if operation.get('op') == 'type':
            if 'text' in operation and operation['text'] != value:
                raise ValueError('输入值必须与本轮个人信息一致')
            request['input'] = {**operation, 'text': value}
    elif purpose == 'navigate' and operation.get('op') == 'type':
        entries = (row.get(key) for row in config.get('company_rows', []) for key in ('url', 'job'))
        text = operation.get('text')
        if not isinstance(text, str) or not text.strip() or (
                text not in config.get('target_roles', []) and text not in entries):
            raise ValueError('导航文字只能是本轮授权的网址或岗位搜索词')
    elif purpose == 'commit' and (not action or action['kind'] != 'submit' or operation.get('op') not in ('click', 'key')):
        raise ValueError('官网提交只接受已确认的点击或按键')


def executor_action(store, run_id, request):
    app_session(store, run_id)
    if not isinstance(request, dict) or request.get('operation') not in ALLOWED:
        raise ValueError('模型工具不允许此操作')
    if request.get('run_id', run_id) != run_id:
        raise ValueError('不允许跨轮次操作')
    store.check_run(run_id)
    config = verify_materials(store, run_id)
    from .job_preferences import JOB_TYPES
    if config.get('simulation') is not True and config.get('employment_type') not in JOB_TYPES:
        raise ValueError('缺少已确认的真实任务配置')
    req = {**request, 'run_id': run_id}
    if req.get('attempt_id') and not store.owns_attempt(run_id, req['attempt_id']):
        raise ValueError('申请记录不属于当前轮次')
    action = store.one('actions', req['action_id']) if req.get('action_id') else None
    if action and action['run_id'] != run_id:
        raise ValueError('动作不属于当前轮次')
    op = req['operation']
    web = config.get('simulation') is not True and 'web' in config.get('channels', [])
    web_attempt = None
    if web and op in ('act', 'field', 'prepare', 'finish', 'attempt_status'):
        action_attempt = op == 'finish' or op == 'act' and req.get('purpose') == 'commit'
        attempt_id = action['attempt_id'] if action and action_attempt else req.get('attempt_id')
        attempt = store.one('attempts', attempt_id) if attempt_id else None
        if attempt is None or attempt['channel'] == 'web':
            web_attempt = attempt
            _check_web_request(config, req, attempt, action)
    if op == 'status':
        from .app_decisions import compact_status
        return {'success':True,'contentItems':[{'type':'inputText','text':json.dumps(compact_status(store,run_id),ensure_ascii=False)}]}
    if config.get('simulation') is not True and config.get('channels') == ['boss'] and op in ('bind', 'capture', 'act'):
        raise ValueError('BOSS 后台任务请使用受控 browser 操作，不使用桌面坐标发送')
    if op == 'boss_decide':
        from .boss_messages import decide_message
        result = decide_message(store, run_id, req['conversation'], req['message_key'],
            req.get('analysis'), req.get('draft', ''), req.get('sources'))
        return {'success': True, 'contentItems': [{'type': 'inputText', 'text': json.dumps(result, ensure_ascii=False)}]}
    if op == 'browser':
        from .boss_script import browser_action, greet
        from .desktop import desktop_lock
        action = req.get('action')
        with desktop_lock(store.root):
            if action == 'greet':
                result = greet(store,run_id,req['attempt_id'],req['action_id'])
            elif action == 'send_message':
                from .boss_messages import send_action
                result = send_action(store, run_id, req['action_id'])
            elif action in ('observe','open_job','next','return','messages','open_conversation'):
                params = {'key': req['key']} if action=='open_job' else ({'conversation':req['conversation']} if action=='open_conversation' else {})
                result = browser_action(store,run_id,action,**params)
            else:
                raise ValueError('不允许此浏览器操作')
        from .agent_browser import compact_observation
        content=[{'type':'inputText','text':json.dumps(compact_observation(result,req.get('scope','auto'),req.get('offset',0)),ensure_ascii=False)}]
        if req.get('include_image') is True:
            content.append({'type':'inputImage','imageUrl':'data:image/png;base64,'+base64.b64encode(Path(result['frame']['path']).read_bytes()).decode('ascii')})
        return {'success':True,'contentItems':content}
    if op == 'bind':
        req['expected_title'] = config['window_title']
    if op in ('bind', 'capture', 'act'):
        from .desktop import foreground
        if config['window_title'] not in foreground()['window']['title']:
            raise ValueError('请将本轮目标浏览器放到前台，再恢复')
    if op == 'act':
        keys = req.get('input', {}).get('keys')
        allowed_keys = (['tab'], ['shift', 'tab'], ['enter'], ['ctrl', 'a'], ['backspace'], ['esc'], ['down'], ['up'])
        if config.get('simulation') is not True:
            allowed_keys += (['ctrl', 'l'], ['alt', 'left'], ['f5'])
        if keys and keys not in allowed_keys:
            raise ValueError('模拟模式不允许此浏览器快捷键')
    if op in ('evidence', 'act'):
        source = Path(req['path'] if op == 'evidence' else req['frame'])
        path = source.resolve()
        metadata = path.with_suffix('.json')
        if path.parent != store.root / 'frames' or source.is_symlink() or metadata.is_symlink():
            raise ValueError('只能归档本轮执行目录的截图')
        if json.loads(metadata.read_text(encoding='utf-8')).get('run_id') != run_id:
            raise ValueError('截图不属于当前轮次，请重新观察')
        check_fresh_frame(store, run_id, path)
    result = dispatch(store, req)
    if web_attempt and op == 'attempt_status' and req['status'] == 'needs_user':
        reason = (req.get('reason') or '请核对缺少的必填信息或网页操作').strip()
        result['attention'] = store.attention(web_attempt['id'], '公司网申需要处理：' + reason)
    if op in ('capture', 'act'):
        result['run_id'] = run_id
        Path(result['path']).with_suffix('.json').write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    content = [{'type': 'inputText', 'text': json.dumps(result, ensure_ascii=False)}]
    if op in ('capture', 'act'):
        path = Path(result['path'])
        content.append({'type': 'inputImage', 'imageUrl': 'data:image/png;base64,' + base64.b64encode(path.read_bytes()).decode('ascii')})
    return {'success': True, 'contentItems': content}
