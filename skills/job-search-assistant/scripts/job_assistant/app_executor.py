"""供原生协调器调用的受限执行工具，不接受模型更改轮次授权。"""
import base64
import json
from pathlib import Path

from .app_runs import app_session
from .cli import dispatch, verify_materials
from .panel_state import check_fresh_frame

ALLOWED = {'status', 'bind', 'capture', 'act', 'begin', 'field', 'evidence', 'prepare', 'finish',
           'attempt_status', 'route_message', 'handoff', 'select_job', 'tick', 'verify_materials', 'browser', 'boss_decide'}


def executor_action(store, run_id, request):
    app_session(store, run_id)
    if not isinstance(request, dict) or request.get('operation') not in ALLOWED:
        raise ValueError('模型工具不允许此操作')
    if request.get('run_id', run_id) != run_id:
        raise ValueError('不允许跨轮次操作')
    store.check_run(run_id)
    config = verify_materials(store, run_id)
    if config.get('simulation') is not True and config.get('employment_type') not in ('internship','campus','full_time'):
        raise ValueError('缺少已确认的真实任务配置')
    req = {**request, 'run_id': run_id}
    if req.get('attempt_id') and not store.owns_attempt(run_id, req['attempt_id']):
        raise ValueError('申请记录不属于当前轮次')
    if req.get('action_id') and store.one('actions', req['action_id'])['run_id'] != run_id:
        raise ValueError('动作不属于当前轮次')
    op = req['operation']
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
        return {'success':True,'contentItems':[{'type':'inputText','text':json.dumps(result,ensure_ascii=False)},
            {'type':'inputImage','imageUrl':'data:image/png;base64,'+base64.b64encode(Path(result['frame']['path']).read_bytes()).decode('ascii')}]}
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
    if op in ('capture', 'act'):
        result['run_id'] = run_id
        Path(result['path']).with_suffix('.json').write_text(json.dumps(result, ensure_ascii=False), encoding='utf-8')
    content = [{'type': 'inputText', 'text': json.dumps(result, ensure_ascii=False)}]
    if op in ('capture', 'act'):
        path = Path(result['path'])
        content.append({'type': 'inputImage', 'imageUrl': 'data:image/png;base64,' + base64.b64encode(path.read_bytes()).decode('ascii')})
    return {'success': True, 'contentItems': content}
