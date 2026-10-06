"""桌面应用白名单接口；沿用台账与证据校验，不暴露任意命令。"""
import base64
import os
import sqlite3
from .dashboard import Dashboard


def handle_app_request(store, request):
    identity = request.get('id', '') if isinstance(request, dict) else ''
    try:
        if not isinstance(request, dict) or not isinstance(identity, str) or not identity:
            raise ValueError('请求必须包含非空编号')
        params = request.get('params', {})
        if not isinstance(params, dict):
            raise ValueError('请求参数必须为对象')
        method = request.get('method')
        if method == 'health':
            result = {'platform_supported': os.name == 'nt', 'data_root': str(store.root), 'version': '0.2.0'}
        elif method == 'snapshot':
            result = Dashboard(store.root).snapshot(store)
            if store.conn.execute("SELECT name FROM sqlite_master WHERE name='app_sessions'").fetchone():
                result['sessions'] = [dict(row) for row in store.conn.execute('SELECT * FROM app_sessions')]
        elif method == 'export_records':
            result = {'path': store.export()}
        elif method == 'read_image':
            data = Dashboard(store.root).image(store, params['kind'], params['identity'])
            result = {'url': 'data:image/png;base64,' + base64.b64encode(data).decode('ascii')}
        elif method in ('preview_demo', 'start_run', 'control_run', 'recover_app', 'bind_session', 'execution_context', 'activate_run', 'open_simulation'):
            from . import app_runs
            if method == 'preview_demo':
                result = app_runs.preview_demo(store)
            elif method == 'start_run':
                result = app_runs.start_run(store, params)
            elif method == 'control_run':
                result = app_runs.control_run(store, params)
            elif method == 'recover_app':
                result = app_runs.recover_app(store)
            elif method == 'bind_session':
                result = app_runs.bind_session(store, params)
            elif method == 'execution_context':
                result = app_runs.execution_context(store, params['run_id'])
            elif method == 'activate_run':
                from .panel_state import apply_commands
                app_runs.app_session(store, params['run_id'])
                apply_commands(store, params['run_id'])
                store.check_run(params['run_id'])
                result = {'active': True}
            else:
                import webbrowser
                context = app_runs.execution_context(store, params['run_id'])
                if context['config'].get('simulation') is not True:
                    raise ValueError('不是模拟任务')
                result = {'opened': webbrowser.open(context['config']['simulation_url'])}
        elif method == 'executor_action':
            from .app_executor import executor_action
            result = executor_action(store, params['run_id'], params['request'])
        else:
            raise ValueError('不支持的应用操作')
        return {'id': identity, 'ok': True, 'result': result}
    except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as exc:
        return {'id': identity, 'ok': False, 'error': {'code': 'invalid_request', 'message': str(exc)}}
