"""平台筛选后的确定性步骤；不调用模型、不调用 BOSS 私有接口。"""
import base64
import hashlib
import io
import json
import re
import time
import uuid
from urllib.parse import urlsplit

from .city_filter import city_match


VERIFICATION_TITLE = re.compile(r'^(?:安全验证|人机验证|滑块验证|验证码|异常访问|访问受限|操作过于频繁)[。！!]?$')
VERIFICATION_PROMPT = re.compile(
    r'^(?:请(?:您)?(?:先)?(?:完成(?:安全|人机|滑块)?验证(?:后(?:继续(?:访问)?|访问|重试))?'
    r'|输入(?:手机|短信|图形)?验证码)|(?:请(?:您)?(?:先)?)?(?:向右)?(?:拖动|拖拽|滑动)滑块(?:完成验证)?'
    r'|(?:系统)?检测到(?:您|您的)?(?:存在)?异常访问|(?:系统检测到)?(?:您的?)?(?:访问受限|操作过于频繁))'
    r'(?:[，,][ \t]*请(?:稍后(?:再试|重试)|完成(?:安全|人机)?验证))?[。！!]?$'
)


def blocking_page(text, *, include_interview=True):
    if VERIFICATION_TITLE.fullmatch(text.strip()) or any(VERIFICATION_PROMPT.fullmatch(line.strip()) for line in text.splitlines()):
        return '平台要求验证或限制访问，请人工处理'
    if re.search(r'扫码登录|登录后查看|请先登录', text):
        return '请先在浏览器完成登录'
    if include_interview and re.search(r'面试邀请|预约面试|约.*面试|过来聊聊', text):
        return '发现面试邀约，需转人工判断'
    return None


def choose_card(cards, config, excluded=()):
    for card in cards:
        if not card.get('key') or card['key'] in excluded or card.get('contacted') is not False:
            continue
        if not card.get('company') or not card.get('job') or card.get('blocked'):
            continue
        if city_match(card.get('city'), config.get('cities', [])) is not True:
            continue
        title = card['job']
        description = title + ' ' + (card.get('tags') or '')
        is_intern = bool(re.search(r'实习|\bintern(?:ship)?\b',description,re.I))
        is_part_time = bool(re.search(r'兼职|\bpart[ -]time\b', description, re.I))
        if config.get('employment_type') == 'internship' and not is_intern:
            continue
        if config.get('employment_type') == 'full_time' and (is_intern or is_part_time):
            continue
        if config.get('employment_type') == 'part_time' and (is_intern or (not is_part_time and 'boss_filters' not in config)):
            continue
        if config.get('employment_type') == 'campus' and (is_intern or is_part_time
                or ('boss_filters' not in config and not re.search(r'校招|应届|毕业生|\d{2,4}届', description))):
            continue
        roles = config.get('keywords') or config.get('target_roles', [])
        terms = re.findall(r'嵌入式|MCU|单片机|FreeRTOS|固件|硬件|FPGA|C\+\+|Java|Python', ' '.join(roles), re.I)
        roles = terms or roles
        # 方向只用于核对卡片标题；快速模式不读取详情或推断适配程度。
        if roles and not any(role.lower() in title.lower() for role in roles):
            continue
        if any(term.lower() in title.lower() for term in config.get('exclude_keywords', [])):
            continue
        return card
    return None


def browser_action(store, run_id, operation, **params):
    from .agent_browser import browser_call
    from .boss_filter_scope import read_scope
    from .cli import verify_materials
    from .inputs import web_url
    from urllib.parse import urlsplit
    config = verify_materials(store, run_id)
    store.check_run(run_id)
    if 'boss' not in config['channels']:
        raise ValueError('本轮未授权 BOSS 渠道')
    if operation in ('greet', 'reply', 'resume'):
        raise ValueError('发送必须使用受控动作入口')
    if operation in ('observe', 'open_job', 'next', 'return'):
        proof = read_scope(store.root, run_id, config)
        if proof is not None:
            params['filter_scope'] = proof
    def check():
        store.check_run(run_id)
        verify_materials(store, run_id)
    result = browser_call(store.root, operation, run_id, check=check, **params)
    url = web_url(result['url'])
    if urlsplit(url).hostname != 'www.zhipin.com':
        raise ValueError('浏览器返回了授权网站之外的页面')
    save_frame(store, run_id, result)
    return result


def save_frame(store, run_id, result):
    from PIL import Image
    from .desktop import prune_frames
    from pathlib import Path
    source = result.pop('frame_file', None)
    if source:
        original = Path(source)
        path = original.resolve()
        if original.is_symlink() or path.parent != (store.root/'frames').resolve() or not re.fullmatch(r'[0-9a-f]{32}\.png', path.name):
            raise ValueError('截图不是本轮受管浏览器文件')
        if not path.is_file() or path.stat().st_size > 8 * 1024 * 1024:
            raise ValueError('浏览器截图不存在或过大')
        data = path.read_bytes()
    else:
        # 桌面模拟和外部协议测试仍可提供原始 PNG，不再依赖扩展运行链路。
        image = result.pop('screenshot', None)
        if not image or len(image) > 8 * 1024 * 1024:
            raise ValueError('浏览器未提供有效实际截图，不能登记发送结果')
        data = base64.b64decode(image, validate=True)
    with Image.open(io.BytesIO(data)) as picture:
        if picture.format != 'PNG':
            raise ValueError('只接受浏览器原始 PNG 截图')
        picture.verify()
    folder = store.root/'frames';folder.mkdir(exist_ok=True)
    if not source:
        path = folder/(uuid.uuid4().hex+'.png');path.write_bytes(data)
    frame = {'run_id':run_id,'path':str(path),'captured_at':time.time(),
             'window':{'title':result.get('title','BOSS直聘'),'url':result.get('url'),'tab_id':result.get('tab_id')}}
    path.with_suffix('.json').write_text(json.dumps(frame,ensure_ascii=False),encoding='utf-8')
    prune_frames(folder)
    result['frame'] = frame


class GreetNotSent(ValueError):
    """当前岗位明确不再需要或允许发起沟通。"""


def greet(store, run_id, attempt_id, action_id, observation_id=None, expected_target=None):
    from .agent_browser import browser_call, action_check, check_current_source
    from .cli import verify_materials
    from .store import now
    config = verify_materials(store, run_id)
    if 'boss' not in config['channels']:
        raise ValueError('本轮未授权 BOSS 沟通')
    attempt = store.one('attempts', attempt_id)
    action = store.one('actions', action_id)
    if attempt['channel'] != 'boss' or action['kind'] != 'greet' or action['attempt_id'] != attempt_id or action['run_id'] != run_id:
        raise ValueError('沟通动作与本轮申请不一致')
    claimed = False

    def claim():
        nonlocal claimed
        store.claim_action(run_id, action_id)
        claimed = True

    try:
        check_current_source(store.root,run_id,config,attempt['job_key'])
        params = {'url':attempt['url'], 'action_id':action_id}
        if observation_id:
            params['observation_id']=observation_id
        if expected_target:
            params['expected_target']=expected_target
        def check():
            action_check(store, run_id, 'greet', params, status='executing' if claimed else 'pending')()

        result = browser_call(store.root, 'greet', run_id, check=check, claim_greet=claim, **params)
        if not claimed:
            if result.get('not_done') is True:
                save_frame(store, run_id, result)
                evidence = store.evidence(attempt_id, result['frame']['path'], 'verification')
                store.finish_action(action_id, 'not_done', evidence, result['reason'])
                raise GreetNotSent(result['reason'])
            raise ValueError('未执行本次沟通，无法登记发送结果')
        save_frame(store, run_id, result)
        evidence = store.evidence(attempt_id,result['frame']['path'],'result')
        if result.get('sent') is True:
            store.finish_action(action_id,'succeeded',evidence,'浏览器在目标岗位显示已发送/继续沟通回执')
            from .boss_messages import remember_job_context
            remember_job_context(store,run_id,attempt,evidence)
        else:
            store.finish_action(action_id,'uncertain',evidence,'页面未明确确认本次沟通成功，请核查后再处理')
            raise ValueError('沟通结果不明，已标记待核查，禁止重发')
        return result
    except Exception as error:
        if claimed and store.one('actions',action_id)['status']=='executing':
            store.finish_action(action_id,'uncertain',reason=str(error))
        elif not claimed:
            with store.conn:
                store.conn.execute("UPDATE actions SET status='not_done',reason=?,updated=? WHERE id=? AND status='pending'",
                                   (str(error), now(), action_id))
        raise


def script_step(store, run_id):
    from .app_runs import app_session, reset_unsent_attempts
    from .cli import verify_materials
    from .desktop import desktop_lock
    app_session(store, run_id)
    config = verify_materials(store, run_id)
    store.check_run(run_id)
    if store.one('runs',run_id)['status']=='watching':
        return {'done':True,'message':'当前仅值守消息，不再新增岗位沟通'}
    if config.get('execution_mode') != 'script' or config.get('channels') != ['boss']:
        raise ValueError('当前轮次不是 BOSS 脚本投递任务')
    state_dir=store.root/'script-state';state_dir.mkdir(exist_ok=True)
    state_path=state_dir/(run_id+'.json')
    state=json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {'phase':'list'}
    def browser(operation, **params):
        observation=browser_action(store,run_id,operation,expected_target=state.get('target'),**params)
        target=observation.get('tab_id') or observation.get('frame',{}).get('window',{}).get('tab_id')
        if not target or (state.get('target') and state['target']!=target):
            raise ValueError('脚本轮次的绑定标签已变化或未能识别，请重新检查页面')
        state['target']=target
        return observation
    def save():
        temporary=state_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(state,ensure_ascii=False),encoding='utf-8');temporary.replace(state_path)
    def return_to_list():
        state['phase']='return';save()
        browser('return')
        state['phase']='list';save()

    with desktop_lock(store.root):
        if state['phase'] in ('open','greet','return') and not state.get('target'):
            raise ValueError('旧脚本进度缺少绑定标签，不能继续操作，请重新创建任务')
        if state['phase']=='done':
            return {'done':True,'message':state['reason']}
        used = store.conn.execute("SELECT COUNT(*) FROM actions WHERE run_id=? AND kind='greet' AND status!='not_done'", (run_id,)).fetchone()[0]
        if used >= config['max_contacts'] and state['phase'] not in ('greet', 'return'):
            return {'done':True,'message':'已达到本轮沟通上限'}
        if store.conn.execute("SELECT 1 FROM actions WHERE run_id=? AND status IN ('pending','executing','uncertain')",(run_id,)).fetchone():
            raise ValueError('存在未决动作，请先核查，禁止重新沟通')
        if state['phase']=='open':
            opened=browser('open_job',key=state['card']['key'])
            store.evidence(state['attempt'],opened['frame']['path'],'pre_submit')
            if opened.get('source_card_current') is False:
                reason='平台推荐列表已更新，快速模式不凭历史卡片继续沟通'
                store.set_attempt(state['attempt'],'skipped',reason)
                store.attention(state['attempt'],reason)
                state.setdefault('stale_keys',[]).append(state['card']['key'])
                state['phase']='return';save()
                return {'done':False,'message':reason}
            source=opened.get('source_card',state['card'])
            if (source.get('key')!=state['card']['key'] or source.get('url')!=state['card']['url']
                    or choose_card([source],config) is None):
                reason='实际岗位卡片已变化或不再符合规则，已跳过'
                store.set_attempt(state['attempt'],'skipped',reason)
                state.setdefault('stale_keys',[]).append(state['card']['key'])
                return_to_list()
                return {'done':False,'message':reason}
            state['card']=source
            state['phase']='greet';save()
            return {'done':False,'message':'已打开目标岗位，准备沿用平台话术沟通'}
        if state['phase']=='greet':
            card=state['card'];attempt=state['attempt']
            sent = store.conn.execute("""SELECT 1 FROM actions
                WHERE run_id=? AND attempt_id=? AND kind='greet' AND status='succeeded'""",
                (run_id, attempt)).fetchone()
            if sent:
                return_to_list()
                return {'done':False,'message':'已恢复到原列表，保留已确认的沟通结果'}
            if used >= config['max_contacts']:
                raise ValueError('沟通上限已用完，请编辑任务后再继续')
            observed=browser('observe')
            if urlsplit(observed.get('url','')).path != urlsplit(card['url']).path:
                raise ValueError('当前页面已离开本轮目标岗位，请回到原岗位后恢复，未发送')
            greeting = observed.get('detail_greet_state')
            if greeting == 'unavailable':
                reason = '岗位已停止招聘，已跳过'
            elif greeting == 'contacted' or observed.get('detail_contacted'):
                reason = '岗位已沟通，已跳过'
            elif not observed.get('heading') or greeting != 'ready':
                raise ValueError('未能核对当前岗位和唯一沟通按钮，任务已暂停，未发送')
            elif choose_card([{**card,'job':observed['heading']}],config) is None:
                reason = '发送前岗位条件已变化，已跳过'
            else:
                store.evidence(attempt,observed['frame']['path'],'pre_submit')
                action=store.prepare_action(run_id,attempt,'greet',card.get('recruiter') or card['key'],{})
                try:
                    greet(store,run_id,attempt,action,observation_id=observed.get('observation_id'),expected_target=state['target'])
                except GreetNotSent as error:
                    reason = str(error)
                else:
                    return_to_list()
                    return {'done':False,'message':f"已沟通：{card['company']} · {card['job']}"}
            store.evidence(attempt,observed['frame']['path'],'verification')
            store.set_attempt(attempt,'skipped',reason)
            state.setdefault('stale_keys',[]).append(card['key'])
            return_to_list()
            return {'done':False,'message':reason}
        if state['phase']=='return':
            return_to_list()
            return {'done':False,'message':'已返回原筛选列表'}
        # 列表阶段没有正在打开的候选，未登记动作的旧记录可以重新筛选。
        with store.conn:
            before = store.conn.total_changes
            reset_unsent_attempts(store, run_id, '候选留档或进度保存中断，尚未发送，恢复后重新核对')
            if state['phase'] == 'next' and store.conn.total_changes != before:
                state['phase'] = 'list'
                save()
        operation = 'next' if state['phase']=='next' else 'observe'
        observation = browser(operation)
        blocked = blocking_page(observation.get('text',''), include_interview=False)
        if blocked:
            raise ValueError(blocked)
        filters = observation.get('filters',[])
        if not isinstance(filters,list) or any(not isinstance(item,str) for item in filters):
            raise ValueError('无法核对平台当前筛选条件，已停止')
        if 'filters' in state and filters != state['filters']:
            raise ValueError('平台城市或筛选条件已变化，已停止；请重新创建任务')
        if 'filters' not in state:
            state['filters']=filters
        excluded = {row[0] for row in store.conn.execute("SELECT job_key FROM attempts WHERE channel='boss' AND status IN ('success','uncertain','in_progress','needs_user')")}
        excluded.update(state.get('stale_keys',[]))
        card = choose_card(observation.get('cards',[]), config, excluded)
        if not card:
            if observation.get('list_empty'):
                state.update(phase='done',reason='原筛选范围没有更多岗位')
                save()
                return {'done':True,'message':state['reason']}
            if not observation.get('cards'):
                raise ValueError('未识别岗位卡片，请确认推荐页面已加载或页面选择器是否变化；未发送')
            fingerprint = hashlib.sha256(json.dumps({
                'keys':sorted(item['key'] for item in observation['cards'] if item.get('key')),
                'position':observation.get('position'),
            },sort_keys=True).encode('utf-8')).hexdigest()
            visited = state.setdefault('visited_pages',[])
            visited.append(fingerprint)
            # shortcut: 每次连续扫描最多 64 步；需要更大范围时再增加用户可见的扫描上限。
            if visited.count(fingerprint)>=3 or len(visited)>=64:
                state.update(phase='done',reason='列表无新增岗位、出现循环或已达连续扫描上限；未扩大筛选范围')
                save()
                return {'done':True,'message':state['reason']}
            state['phase']='next';save()
            return {'done':False,'message':'当前列表没有符合条件的新岗位，继续翻页或加载；保持原筛选条件'}
        attempt = store.begin_attempt(run_id,card['company'],card['url'],card['job'],'boss',card['key'])
        store.evidence(attempt,observation['frame']['path'],'pre_submit')
        state.update(phase='open',card=card,attempt=attempt,visited_pages=[]);save()
        return {'done':False,'message':f"已筛选：{card['company']} · {card['job']}",'attempt_id':attempt}


def complete_script(store, run_id):
    from .app_runs import app_session
    from .cli import verify_materials
    app_session(store, run_id)
    config = verify_materials(store, run_id)
    if config.get('execution_mode') != 'script' or config.get('channels') != ['boss']:
        raise ValueError('只有 BOSS 脚本任务可以完成快速沟通')
    with store.conn:
        updated = store.conn.execute("UPDATE runs SET status='completed' WHERE id=? AND status='running'", (run_id,))
        if updated.rowcount != 1:
            raise ValueError('任务状态已经变化')
        store.conn.execute("UPDATE app_sessions SET state='completed',error='' WHERE run_id=?", (run_id,))
    return {'state':'completed'}
