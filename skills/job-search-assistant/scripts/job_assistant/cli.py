"""JSON 文件接口避免 shell 插值，并使每次操作可独立重放核查。"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

from .bootstrap import SKILL_ROOT, default_root, install_skill, setup
from .inputs import digest_file, load_json, read_companies, validate_config
from .policy import decide_reply, select_job
from .store import Store, now, packed


def verify_materials(store, run_id):
    config = json.loads(store.one('runs', run_id)['config'])
    for label in ('resume', 'profile'):
        path = Path(config[label + '_path'])
        if not path.is_file() or digest_file(path) != config[label + '_sha256']:
            raise ValueError(f'{label} 资料已变更或丢失；请重新确认本轮材料')
    return config


def prepare_config(value):
    config = validate_config(dict(value))
    for label in ('resume', 'profile'):
        path = Path(config.get(label + '_path', '')).resolve()
        if not path.is_file():
            raise ValueError(f'缺少 {label} 文件')
        config[label + '_path'] = str(path)
        config[label + '_sha256'] = digest_file(path)
    if not isinstance(load_json(config['profile_path']), dict):
        raise ValueError('个人资料需先归一化为 JSON 对象')
    if 'web' in config['channels']:
        companies = read_companies(config['company_table'], config.get('columns'), config.get('sheet'))
        selected = config.get('companies') or [r['company'] for r in companies]
        if not set(selected) <= {r['company'] for r in companies}:
            raise ValueError('本轮公司名单包含表格之外的公司')
        config['companies'] = selected
        config['company_rows'] = [r for r in companies if r['company'] in selected]
    config['rules'] = config.get('rules', [])
    return config


def start(store, request):
    config = prepare_config(request['config'])
    identity = store.create_run(config, request['approval'])
    return {'run_id': identity, 'status': 'running', 'config': config}


def dispatch(store, request):
    from .panel_state import activity, apply_commands
    run_id, op = request.get('run_id'), request.get('operation')
    tracked = run_id and op not in ('control', 'status')
    if tracked:
        apply_commands(store, run_id)
        activity(store, run_id, op, 'started')
    try:
        if op in ('bind', 'capture', 'act'):
            from .desktop import desktop_lock
            with desktop_lock(store.root):
                result = _dispatch(store, request)
        else:
            result = _dispatch(store, request)
    except Exception:
        if tracked:
            activity(store, run_id, op, 'error')
        raise
    if tracked:
        activity(store, run_id, op, 'finished')
    return result


def _dispatch(store, request):
    op = request['operation']
    if op == 'start':
        return start(store, request)
    if op == 'companies':
        return read_companies(request['path'], request.get('columns'), request.get('sheet'))
    if op == 'status':
        from .dashboard import Dashboard
        return Dashboard(store.root).snapshot(store)
    if op == 'export':
        return {'path': store.export()}
    if op == 'verify_materials':
        verify_materials(store, request['run_id'])
        return {'unchanged': True}
    if op == 'control':
        if request['status'] in ('running', 'watching'):
            verify_materials(store, request['run_id'])
        store.control(request['run_id'], request['status'])
        return {'status': request['status']}
    if op == 'begin':
        return {'attempt_id': store.begin_attempt(request['run_id'], request['company'], request['url'],
                                                  request['job'], request['channel'], request['job_key'])}
    if op == 'attach':
        return store.attach_attempt(request['run_id'], request['attempt_id'])
    if op == 'attempt_status':
        store.set_attempt(request['attempt_id'], request['status'], request.get('reason', ''))
        return {'report': store.export()}
    if op == 'field':
        store.record_field(request['attempt_id'], request['name'], request['value'], request['source'], request.get('run_id'))
        return {'recorded': True}
    if op == 'evidence':
        return {'evidence_id': store.evidence(request['attempt_id'], request['path'], request['kind'], request.get('fields'))}
    if op == 'select_job':
        return select_job(request['jobs'], request['mode'])
    if op == 'resume_conversation':
        store.resume_conversation(request['conversation'], request['confirmation'])
        return {'status': 'auto'}
    if op == 'handoff':
        result = store.handoff(request['conversation'], request['reason'])
        store.export()
        return result
    if op == 'route_message':
        config = verify_materials(store, request['run_id'])
        store.check_run(request['run_id'])
        conversation = request['conversation']
        fresh = store.observe_message(conversation, request['message_key'], request['text'])
        state = store.one('conversations', conversation)
        if state['latest_message'] != request['message_key']:
            return {'action': 'skip', 'reason': 'old_message'}
        result = decide_reply(request['text'], config['rules'], config.get('unknown_reply_mode', 'human'),
                              request.get('analysis'), load_json(config['profile_path']), request.get('draft', ''),
                              request.get('sources'), state['status'] == 'human')
        if result['action'] == 'human':
            result['attention'] = store.handoff(conversation, result['reason'])
        result['new_message'] = fresh
        return result
    if op == 'prepare':
        config = verify_materials(store, request['run_id'])
        payload = dict(request.get('payload', {}))
        if request['kind'] in ('reply', 'resume'):
            state = store.one('conversations', request['recipient'])
            message = store.conn.execute('SELECT text FROM messages WHERE conversation_id=? AND message_key=?',
                                         (request['recipient'], payload.get('message_key'))).fetchone()
            if not message:
                raise ValueError('先记录并判断对方消息')
            decision = decide_reply(message[0], config['rules'], config.get('unknown_reply_mode', 'human'),
                                    request.get('analysis'), load_json(config['profile_path']), payload.get('text', ''),
                                    request.get('sources'), state['status'] == 'human')
            if decision['action'] != request['kind']:
                if decision['action'] == 'human':
                    store.handoff(request['recipient'], decision['reason'])
                raise ValueError('消息不满足自动执行规则：' + decision['reason'])
            if request['kind'] == 'reply' and decision['text'] != payload.get('text'):
                raise ValueError('发送文本必须与通过规则检查的回复完全一致')
        return {'action_id': store.prepare_action(request['run_id'], request['attempt_id'], request['kind'], request['recipient'], payload)}
    if op == 'finish':
        store.finish_action(request['action_id'], request['status'], request.get('evidence_id'), request.get('reason', ''))
        return {'report': store.export()}
    if op in ('bind', 'capture', 'act'):
        from .desktop import Desktop
        desktop = Desktop(store.root)
        if op == 'bind':
            return desktop.bind(request['expected_title'])
        if op == 'capture':
            return desktop.capture()
        action_id = request.get('action_id')
        purpose = request.get('purpose')
        if purpose not in ('navigate', 'fill', 'commit'):
            raise ValueError('操作需标明 navigate、fill 或 commit 用途')
        verify_materials(store, request['run_id'])
        store.check_run(request['run_id'])
        from .panel_state import check_fresh_frame
        check_fresh_frame(store, request['run_id'], request['frame'])
        if purpose == 'fill':
            attempt = store.one('attempts', request['attempt_id'])
            if not store.owns_attempt(request['run_id'], request['attempt_id']):
                raise ValueError('填写目标不属于当前轮次')
            if attempt['channel'] == 'web':
                store.check_run(request['run_id'], 'fill')
            else:
                state = store.one('conversations', request.get('recipient', ''))
                if state['status'] == 'human' or state['latest_message'] != request.get('message_key'):
                    raise ValueError('只能对当前未锁定消息输入回复或选择附件')
        if purpose == 'commit':
            # 一经进入真实输入阶段，进程崩溃也不能再次消费同一行动。
            store.claim_action(request['run_id'], action_id or '')
        try:
            def check_input():
                store.check_run(request['run_id'])
                check_fresh_frame(store, request['run_id'], request['frame'])
            result = desktop.act(request['frame'], request['input'], check_input)
        except Exception as error:
            if purpose == 'commit':
                store.finish_action(action_id, 'uncertain', reason=type(error).__name__ + ': ' + str(error))
            raise
        return result
    if op == 'notify':
        from .desktop import notify
        item = store.attention(request['subject'], request['reason'])
        return {**item, 'notification': notify('求职助手需要你处理', request['reason']) if store.claim_notification(item['id']) else None}
    if op == 'tick':
        from .panel_state import pending_notes
        config = store.check_run(request['run_id'])
        return {'run_id': request['run_id'], 'next_check_after_seconds': config.get('poll_seconds', 60),
                'human_notes': pending_notes(store),
                'instructions': '由当前会话重新观察 BOSS 页面，处理新消息；等待需可被用户输入打断。没有变化不通知。'}
    raise ValueError('未知操作：' + op)


def main(argv=None):
    parser = argparse.ArgumentParser(description='求职 Skill：依赖初始化、台账、回复规则与电脑操作')
    parser.add_argument('--root', type=Path, default=default_root(), help='个人数据目录，默认用户主目录/.job-search-assistant')
    commands = parser.add_subparsers(dest='command', required=True)
    bootstrap = commands.add_parser('bootstrap', help='检查依赖；--install 自动安装缺失包到独立环境')
    bootstrap.add_argument('--install', action='store_true')
    request = commands.add_parser('request', help='读取 UTF-8 JSON 请求，输出 JSON 结果')
    request.add_argument('--file', type=Path, required=True)
    install = commands.add_parser('install', help='将自包含 Skill 安装到用户目录，不覆盖已有不同版本')
    install.add_argument('--destination', type=Path, default=Path.home() / '.agents/skills/job-search-assistant')
    commands.add_parser('doctor', help='验证依赖、数据库及 Excel 写入，不移动鼠标')
    dashboard = commands.add_parser('dashboard', help='启动仅本机可访问的可视化面板，不启动投递执行器')
    dashboard.add_argument('--port', type=int, default=0, help='默认自动分配空闲端口')
    dashboard.add_argument('--open', action='store_true', help='打开浏览器面板')
    args = parser.parse_args(argv)
    try:
        if args.root.resolve().is_relative_to(SKILL_ROOT):
            raise ValueError('个人数据目录不能放在可分发的 Skill 内部')
        if args.command == 'dashboard':
            from .dashboard import serve
            serve(args.root, args.port, args.open)
            return 0
        if args.command == 'bootstrap':
            result = setup(args.root, args.install)
        elif args.command == 'install':
            result = install_skill(SKILL_ROOT, args.destination)
        elif args.command == 'doctor':
            result = setup(args.root, False)
            result['desktop_verified'] = False
            result['note'] = '依赖就绪不等于桌面就绪；还需 bind/capture 验证并检查中文输入。'
            store = Store(args.root)
            try:
                result['report'] = store.export()
            finally:
                store.close()
        else:
            store = Store(args.root)
            try:
                result = dispatch(store, load_json(args.file))
            finally:
                store.close()
        print(json.dumps({'ok': True, 'result': result}, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, KeyError, OSError, RuntimeError) as error:
        print(json.dumps({'ok': False, 'error': str(error), 'type': type(error).__name__}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
