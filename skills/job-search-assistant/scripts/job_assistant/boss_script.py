"""平台筛选后的确定性步骤；不调用模型、不调用 BOSS 私有接口。"""
import base64
import hashlib
import io
import json
import re
import time
import uuid


def blocking_page(text):
    if re.search(r'安全验证|滑块|验证码|异常访问|操作过于频繁|访问受限', text):
        return '平台要求验证或限制访问，请人工处理'
    if re.search(r'扫码登录|登录后查看|请先登录', text):
        return '请先在浏览器完成登录'
    if re.search(r'面试邀请|预约面试|约.*面试|过来聊聊', text):
        return '发现面试邀约，需转人工判断'
    return None


def choose_card(cards, config, excluded=()):
    for card in cards:
        if not card.get('key') or card['key'] in excluded or card.get('contacted') is not False:
            continue
        if not card.get('company') or not card.get('job') or card.get('blocked'):
            continue
        title = card['job']
        description = title + ' ' + card.get('tags', '')
        is_intern = bool(re.search(r'实习|\bintern(?:ship)?\b',description,re.I))
        if config.get('employment_type') == 'internship' and not is_intern:
            continue
        if config.get('employment_type') == 'full_time' and is_intern:
            continue
        if config.get('employment_type') == 'campus' and not re.search(r'校招|应届|毕业生|\d{2,4}届',description):
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
    from .browser_bridge import bridge_call
    from .cli import verify_materials
    from .inputs import web_url
    from urllib.parse import urlsplit
    config = verify_materials(store, run_id)
    store.check_run(run_id)
    if 'boss' not in config['channels']:
        raise ValueError('本轮未授权 BOSS 渠道')
    if operation in ('greet', 'reply', 'resume'):
        raise ValueError('发送必须使用受控动作入口')
    result = bridge_call(store.root, operation, run_id, **params)
    url = web_url(result['url'])
    if urlsplit(url).hostname != 'www.zhipin.com':
        raise ValueError('扩展返回了授权网站之外的页面')
    save_frame(store, run_id, result)
    return result


def save_frame(store, run_id, result):
    from PIL import Image
    from .desktop import prune_frames
    image = result.pop('screenshot', None)
    if not image:
        raise ValueError('浏览器未提供实际截图，不能据此登记发送结果')
    if len(image) > 8 * 1024 * 1024:
        raise ValueError('浏览器截图过大')
    data = base64.b64decode(image, validate=True)
    with Image.open(io.BytesIO(data)) as picture:
        if picture.format != 'PNG':
            raise ValueError('只接受浏览器原始 PNG 截图')
        picture.verify()
    folder = store.root/'frames';folder.mkdir(exist_ok=True)
    path = folder/(uuid.uuid4().hex+'.png');path.write_bytes(data)
    frame = {'run_id':run_id,'path':str(path),'captured_at':time.time(),
             'window':{'title':result.get('title','BOSS直聘'),'url':result.get('url'),'tab_id':result.get('tab_id')}}
    path.with_suffix('.json').write_text(json.dumps(frame,ensure_ascii=False),encoding='utf-8')
    prune_frames(folder)
    result['frame'] = frame


def greet(store, run_id, attempt_id, action_id):
    from .browser_bridge import bridge_call
    from .cli import verify_materials
    config = verify_materials(store, run_id)
    if 'boss' not in config['channels']:
        raise ValueError('本轮未授权 BOSS 沟通')
    attempt = store.one('attempts', attempt_id)
    action = store.one('actions', action_id)
    if attempt['channel'] != 'boss' or action['kind'] != 'greet' or action['attempt_id'] != attempt_id:
        raise ValueError('沟通动作与本轮申请不一致')
    store.claim_action(run_id, action_id)
    try:
        result = bridge_call(store.root, 'greet', run_id, url=attempt['url'],action_id=action_id)
        save_frame(store, run_id, result)
        evidence = store.evidence(attempt_id,result['frame']['path'],'result')
        if result.get('sent') is True:
            store.finish_action(action_id,'succeeded',evidence,'浏览器在目标岗位显示已发送/继续沟通回执')
        else:
            store.finish_action(action_id,'uncertain',evidence,'页面未明确确认本次沟通成功，请核查后再处理')
            raise ValueError('沟通结果不明，已标记待核查，禁止重发')
        store.export()
        return result
    except Exception as error:
        if store.one('actions',action_id)['status']=='executing':
            store.finish_action(action_id,'uncertain',reason=str(error))
        raise


def script_step(store, run_id):
    from .app_runs import app_session
    from .cli import verify_materials
    from .desktop import desktop_lock
    app_session(store, run_id)
    config = verify_materials(store, run_id)
    store.check_run(run_id)
    if config.get('execution_mode') != 'script' or config.get('channels') != ['boss']:
        raise ValueError('当前轮次不是 BOSS 脚本投递任务')
    state_dir=store.root/'script-state';state_dir.mkdir(exist_ok=True)
    state_path=state_dir/(run_id+'.json')
    state=json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {'phase':'list'}
    def save():
        temporary=state_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(state,ensure_ascii=False),encoding='utf-8');temporary.replace(state_path)
    with desktop_lock(store.root):
        if state['phase']=='open':
            browser_action(store,run_id,'open_job',key=state['card']['key'])
            state['phase']='greet';save()
            return {'done':False,'message':'已打开目标岗位，准备沿用平台话术沟通'}
        if state['phase']=='greet':
            card=state['card'];attempt=state['attempt']
            action=store.prepare_action(run_id,attempt,'greet',card.get('recruiter') or card['key'],{})
            greet(store,run_id,attempt,action)
            state['phase']='return';save()
            return {'done':False,'message':f"已沟通：{card['company']} · {card['job']}"}
        if state['phase']=='return':
            browser_action(store,run_id,'return')
            state['phase']='list';save()
            return {'done':False,'message':'已返回原筛选列表'}
        if state['phase']=='done':
            return {'done':True,'message':state['reason']}
        used = store.conn.execute("SELECT COUNT(*) FROM actions WHERE run_id=? AND kind='greet' AND status!='not_done'", (run_id,)).fetchone()[0]
        if used >= config['max_contacts']:
            return {'done':True,'message':'已达到本轮沟通上限'}
        if store.conn.execute("SELECT 1 FROM actions WHERE run_id=? AND status IN ('pending','executing','uncertain')",(run_id,)).fetchone():
            raise ValueError('存在未决动作，请先核查，禁止重新沟通')
        operation = 'next' if state['phase']=='next' else 'observe'
        observation = browser_action(store,run_id,operation)
        blocked = blocking_page(observation.get('text',''))
        if blocked:
            raise ValueError(blocked)
        filters = observation.get('filters',[])
        if not isinstance(filters,list) or any(not isinstance(item,str) for item in filters):
            raise ValueError('无法核对平台当前筛选条件，已停止')
        if state.get('filters') and filters != state['filters']:
            raise ValueError('平台城市或筛选条件已变化，已停止；请重新创建任务')
        if not state.get('filters'):
            state['filters']=filters
        excluded = {row[0] for row in store.conn.execute("SELECT job_key FROM attempts WHERE channel='boss' AND status IN ('success','uncertain','in_progress','needs_user')")}
        card = choose_card(observation.get('cards',[]), config, excluded)
        if not card:
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
