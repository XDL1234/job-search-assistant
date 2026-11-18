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
        if method == '_native_codex_proxy_environment':
            if os.environ.get('JOB_ASSISTANT_NATIVE_PROXY_REQUEST') != '1':
                raise ValueError('不支持的应用操作')
            from .native_proxy import codex_proxy_environment
            result = {'env': codex_proxy_environment()}
        elif method == 'health':
            result = {'platform_supported': os.name == 'nt', 'data_root': str(store.root), 'version': '0.2.0'}
        elif method == 'snapshot':
            result = Dashboard(store.root).snapshot(store)
            if params.get('run_id'):
                result['frame'] = Dashboard(store.root).latest_frame(params['run_id'])
            timing = {row['run_id']: row for row in store.conn.execute('''
                SELECT run_id, MIN(started) AS started_at, MAX(updated) AS updated_at FROM (
                  SELECT run_id, CASE WHEN action='running' THEN updated END AS started, updated
                  FROM panel_commands WHERE status='applied'
                  UNION ALL SELECT run_id, started, updated FROM attempts
                  UNION ALL SELECT run_id, NULL, updated FROM actions
                  UNION ALL SELECT run_id, NULL, updated FROM executor_activity
                ) GROUP BY run_id''')}
            for run in result['runs']:
                if row := timing.get(run['id']):
                    run.update(started_at=row['started_at'], updated_at=row['updated_at'])
            if store.conn.execute("SELECT name FROM sqlite_master WHERE name='app_sessions'").fetchone():
                result['sessions'] = [dict(row) for row in store.conn.execute('SELECT * FROM app_sessions')]
            from .boss_messages import read_watch_state
            result['reply_targets'] = [{'run_id': run['id'], 'conversation': conversation, **item}
                for run in result['runs'] for conversation, item in read_watch_state(store, run['id']).get('conversations', {}).items()
                if item.get('attempt_id') and item.get('message_key') and item.get('text')]
        elif method == 'export_records':
            result = {'path': store.export()}
        elif method == 'export_records_to':
            from pathlib import Path
            destination = Path(params['destination'])
            if (not destination.is_absolute() or destination.suffix.casefold() != '.xlsx'
                    or not destination.parent.is_dir() or destination.is_symlink()
                    or destination.exists() and not destination.is_file()):
                raise ValueError('请选择有效的 Excel (.xlsx) 文件位置')
            result = {'path': store.export(destination)}
        elif method in ('inspect_company_table', 'open_company_site'):
            from .inputs import inspect_company_table
            result = inspect_company_table(params['path'])
            if method == 'open_company_site':
                number = params.get('source_row')
                if type(number) is not int or number < 2 or result['sha256'] != params.get('sha256'):
                    raise ValueError('公司表已变更，请重新导入并核对')
                row = next((item for item in result['rows'] if item['source_row'] == number
                            and item['status'] == 'valid'), None)
                if not row:
                    raise ValueError('该行没有可打开的申请网址')
                import webbrowser
                if not webbrowser.open(row['url']):
                    raise ValueError('无法启动默认浏览器')
                result = {'opened': True}
        elif method in ('get_job_preferences', 'save_job_preferences'):
            from .job_preferences import read_preferences, save_preferences
            result = (read_preferences(store) if method == 'get_job_preferences'
                      else save_preferences(store, params['preferences']))
        elif method == 'browser_apply_job_preferences':
            from .boss_filter_scope import apply_saved_preferences
            result = apply_saved_preferences(store, params['expected_target'])
        elif method == 'prepare_run_filters':
            from .boss_filter_scope import prepare_run_filters
            result = prepare_run_filters(store, params['run_id'], params['expected_target'])
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
        elif method in ('preview_demo', 'preview_run', 'start_run', 'revise_run', 'control_run', 'recover_app', 'bind_session', 'execution_context', 'activate_run', 'open_simulation'):
            from . import app_runs
            if method == 'preview_demo':
                result = app_runs.preview_demo(store)
            elif method == 'preview_run':
                result = app_runs.preview_run(store, params['config'], params.get('run_id'))
            elif method in ('start_run', 'revise_run'):
                result = app_runs.start_run(store, params, revision=method == 'revise_run')
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
            from .app_decisions import check_generation
            generation = params.get('control_generation')
            if generation is not None:
                check_generation(store, params['run_id'], generation)
            store.request_generation = (params['run_id'], generation) if generation is not None else None
            try:
                result = executor_action(store, params['run_id'], params['request'])
            finally:
                store.request_generation = None
        elif method == 'script_step':
            from .boss_script import script_step
            result = script_step(store, params['run_id'])
        elif method == 'complete_script':
            from .boss_script import complete_script
            result = complete_script(store, params['run_id'])
        elif method in ('selection_step','selection_context','accept_selection'):
            from .ai_selection import selection_step, selection_context, accept_selection
            if method=='selection_step':
                result=selection_step(store,params['run_id'],params.get('batch_size',8))
            elif method=='selection_context':
                result=selection_context(store,params['run_id'])
            else:
                from .app_decisions import check_generation
                check_generation(store,params['run_id'],params.get('control_generation'))
                result=accept_selection(store,params['run_id'],params['decision_id'],params['decisions'])
        elif method == 'watch_step':
            from .boss_messages import watch_step
            result = watch_step(store, params['run_id'], params.get('expected_target'))
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
            from .agent_browser import connection_status
            result=connection_status(store.root)
        elif method == 'browser_inspect':
            from .agent_browser import inspect_page
            result=inspect_page(store.root,params.get('scope','auto'),params.get('expected_target'))
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
