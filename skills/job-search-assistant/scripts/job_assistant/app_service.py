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
            if params.get('run_id'):
                result['frame'] = Dashboard(store.root).latest_frame(params['run_id'])
            if store.conn.execute("SELECT name FROM sqlite_master WHERE name='app_sessions'").fetchone():
                result['sessions'] = [dict(row) for row in store.conn.execute('SELECT * FROM app_sessions')]
            from .boss_messages import read_watch_state
            result['reply_targets'] = [{'run_id': run['id'], 'conversation': conversation, **item}
                for run in result['runs'] for conversation, item in read_watch_state(store, run['id']).get('conversations', {}).items()
                if item.get('attempt_id') and item.get('message_key') and item.get('text')]
        elif method == 'export_records':
            result = {'path': store.export()}
        elif method == 'read_image':
            data = Dashboard(store.root).image(store, params['kind'], params['identity'])
            result = {'url': 'data:image/png;base64,' + base64.b64encode(data).decode('ascii')}
        elif method in ('extract_profile', 'save_profile', 'get_profile'):
            from . import profiles
            if method == 'extract_profile':
                result = profiles.extract_profile(params['path'])
            elif method == 'save_profile':
                result = profiles.save_profile(store, **params)
            else:
                result = profiles.get_profile(store)
        elif method in ('preview_demo', 'preview_run', 'start_run', 'control_run', 'recover_app', 'bind_session', 'execution_context', 'activate_run', 'open_simulation'):
            from . import app_runs
            if method == 'preview_demo':
                result = app_runs.preview_demo(store)
            elif method == 'preview_run':
                result = app_runs.preview_run(store, params['config'])
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
        elif method == 'script_step':
            from .boss_script import script_step
            result = script_step(store, params['run_id'])
        elif method == 'watch_step':
            from .boss_messages import watch_step
            result = watch_step(store, params['run_id'])
        elif method == 'finish_ai_watch':
            from .boss_messages import finish_ai_watch
            result = finish_ai_watch(store, params['run_id'])
        elif method == 'enter_watch':
            from .app_runs import app_session
            from .cli import verify_materials
            app_session(store, params['run_id'])
            verify_materials(store, params['run_id'])
            with store.conn:
                store.conn.execute('BEGIN IMMEDIATE')
                store.check_run(params['run_id'])
                store.control(params['run_id'], 'watching')
            result = {'state': 'watching'}
        elif method == 'attention_update':
            result = Dashboard(store.root).attention(store, params)
        elif method == 'reply_focus':
            from .app_runs import app_session
            from .boss_messages import read_watch_state
            from .boss_script import browser_action
            from .desktop import desktop_lock
            run_id = params['run_id']
            app_session(store, run_id)
            target = read_watch_state(store, run_id).get('conversations', {}).get(params['conversation'])
            if not target or target.get('message_key') != params['message_key']:
                raise ValueError('会话已更新，请重新查看')
            with desktop_lock(store.root):
                observation = browser_action(store, run_id, 'open_conversation', conversation=params['conversation'])
            latest = observation.get('messages', [])[-1:]
            if (observation.get('conversation') != params['conversation'] or not latest
                    or latest[0].get('direction') != 'incoming' or latest[0].get('key') != params['message_key']
                    or latest[0].get('text') != target['text']):
                raise ValueError('页面会话或最新消息已变化，请等待重新读取后再确认')
            result = {'matched': True}
        elif method == 'manual_reply':
            from .app_runs import app_session
            from .boss_messages import read_watch_state, send_action
            from .cli import verify_materials
            from .desktop import desktop_lock
            run_id = params['run_id']
            app_session(store, run_id)
            verify_materials(store, run_id)
            target = read_watch_state(store, run_id).get('conversations', {}).get(params['conversation'])
            if not target or target['message_key'] != params['message_key']:
                raise ValueError('会话目标已更新或未能绑定本轮申请，请重新查看')
            with desktop_lock(store.root):
                action = store.prepare_manual_reply(run_id, target['attempt_id'], params['conversation'],
                    params['message_key'], params['text'], params.get('confirmed'))
                result = send_action(store, run_id, action)
                if result.get('sent') is not True:
                    raise ValueError('尚未发送；请先打开并核对目标会话后再确认')
        elif method == 'browser_status':
            from .browser_bridge import connection_status
            result=connection_status(store.root)
        elif method in ('get_reply_settings','save_reply_settings','preview_reply'):
            from . import reply_settings
            if method=='get_reply_settings':result=reply_settings.read_settings(store)
            elif method=='save_reply_settings':result=reply_settings.save_settings(store,params['settings'])
            else:result=reply_settings.preview_reply(store,params)
        else:
            raise ValueError('不支持的应用操作')
        return {'id': identity, 'ok': True, 'result': result}
    except (ValueError, KeyError, TypeError, OSError, sqlite3.Error) as exc:
        return {'id': identity, 'ok': False, 'error': {'code': 'invalid_request', 'message': str(exc)}}
