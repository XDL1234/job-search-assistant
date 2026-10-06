"""供原生协调器调用的受限执行工具，不接受模型更改轮次授权。"""
import base64
import json
from pathlib import Path

from .app_runs import app_session
from .cli import dispatch, verify_materials
from .panel_state import check_fresh_frame

ALLOWED = {'status', 'bind', 'capture', 'act', 'begin', 'field', 'evidence', 'prepare', 'finish',
           'attempt_status', 'route_message', 'handoff', 'select_job', 'tick', 'verify_materials'}


def executor_action(store, run_id, request):
    app_session(store, run_id)
    if not isinstance(request, dict) or request.get('operation') not in ALLOWED:
        raise ValueError('模型工具不允许此操作')
    if request.get('run_id', run_id) != run_id:
        raise ValueError('不允许跨轮次操作')
    store.check_run(run_id)
    config = verify_materials(store, run_id)
    if config.get('simulation') is not True:
        raise ValueError('当前应用执行入口仅开放模拟验收')
    req = {**request, 'run_id': run_id}
    if req.get('attempt_id') and not store.owns_attempt(run_id, req['attempt_id']):
        raise ValueError('申请记录不属于当前轮次')
    if req.get('action_id') and store.one('actions', req['action_id'])['run_id'] != run_id:
        raise ValueError('动作不属于当前轮次')
    op = req['operation']
    if op == 'bind':
        req['expected_title'] = config['window_title']
    if op in ('bind', 'capture', 'act'):
        from .desktop import foreground
        if config['window_title'] not in foreground()['window']['title']:
            raise ValueError('请将本轮模拟浏览器放到前台，再恢复')
    if op == 'act':
        keys = req.get('input', {}).get('keys')
        if keys and keys not in (['tab'], ['shift', 'tab'], ['enter'], ['ctrl', 'a'], ['backspace'], ['esc'], ['down'], ['up']):
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
