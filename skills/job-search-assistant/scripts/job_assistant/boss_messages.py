"""后台消息步骤复用规则、会话锁和单次发送账本，不调用网站私有接口。"""
import json
import re
from pathlib import Path

from .cli import verify_materials
from .policy import decide_reply


INTERVIEW = re.compile(r'面试|interview|过来聊聊|预约时间|来(?:我们)?公司.*(?:聊|见)|到(?:公司|现场).*聊', re.I)


def read_watch_state(store, run_id):
    path = store.root/'boss-watch'/(run_id+'.json')
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'phase':'observe','conversations':{}}


def _save_state(store, run_id, state):
    folder=store.root/'boss-watch';folder.mkdir(exist_ok=True)
    path=folder/(run_id+'.json');temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(state,ensure_ascii=False),encoding='utf-8');temporary.replace(path)


def _attention(store, conversation, reason, handoff=True):
    from .desktop import notify
    item=store.handoff(conversation,reason) if handoff else store.attention(conversation,reason)
    first=store.claim_notification(item['id'])
    if first:
        try:notify('求职助手需要你处理',reason)
        except OSError:pass  # 面板待办仍会保留，系统通知失败不解除人工锁。
    return {**item,'notify':first}


def simple_analysis(text):
    """只识别完整的单一常见问句；其余内容交模型或人工，不猜测单位、地点或承诺。"""
    value=text.strip().strip('。！？?！ ')
    if INTERVIEW.search(value):
        return {'complete':True,'categories':['interview']}
    if re.fullmatch(r'(?:请问)?(?:你|您)?(?:录用后)?(?:多久|什么时候|何时)(?:可以|能)?(?:到岗|入职)(?:呢|吗)?',value):
        return {'complete':True,'categories':['availability'],'facts':{}}
    salary=re.fullmatch(r'(?:我们)?(?:提供的)?(?:薪资|工资)(?:是|为)?税前(?:月薪)?\s*(\d+(?:\.\d+)?)\s*([kK千万元]?)(?:元)?(?:/月|每月|月薪)[，, ]*(?:你|您)?(?:可以|能)?接受(?:吗|么)?',value)
    if salary:
        amount=float(salary[1])*{'k':1000,'千':1000,'万':10000,'元':1,'':1}[salary[2].lower()]
        return {'complete':True,'categories':['salary'],'facts':{'salary_monthly_gross':amount}}
    location=re.fullmatch(r'(?:我们)?(?:工作)?地点(?:是|在|为)([\u4e00-\u9fff]{2,12})[，, ]+(?:你|您)?(?:可以|能)?接受(?:吗|么)?',value)
    if location:
        return {'complete':True,'categories':['location'],'facts':{'city':location[1]}}
    if re.fullmatch(r'(?:请|麻烦)?(?:发|发送|给)(?:我|我们)?(?:一份|一下)?(?:你的|您的)?(?:附件)?简历(?:给我|给我们)?(?:可以吗|好吗)?',value):
        return {'complete':True,'categories':['resume'],'facts':{}}
    return {'complete':False,'categories':[]}


def decide_message(store, run_id, conversation, message_key, analysis=None, draft='', sources=None):
    config=verify_materials(store,run_id);store.check_run(run_id)
    state=read_watch_state(store,run_id)
    target=state['conversations'].get(conversation)
    if not target or target['message_key']!=message_key or not store.owns_attempt(run_id,target['attempt_id']):
        raise ValueError('消息没有绑定本轮申请或已变化，请重新观察')
    current=store.one('conversations',conversation)
    if current['latest_message']!=message_key:
        raise ValueError('消息已更新，请重新判断')
    text=store.conn.execute('SELECT text FROM messages WHERE conversation_id=? AND message_key=?',(conversation,message_key)).fetchone()[0]
    local=simple_analysis(text)
    if INTERVIEW.search(text):
        analysis=local
    elif analysis is None:
        analysis=local
        if not local['complete'] and config.get('unknown_reply_mode')=='ai' and current['status']!='human':
            return {'action':'ai_required','conversation':conversation,'message_key':message_key,'text':text,'attempt_id':target['attempt_id']}
    profile=json.loads(Path(config['profile_path']).read_text(encoding='utf-8'))
    decision=decide_reply(text,config.get('rules',[]),config.get('unknown_reply_mode','human'),analysis,
                          profile,draft,sources,current['status']=='human')
    if decision['action']=='human':
        decision['attention']=_attention(store,conversation,decision['reason'])
    else:
        payload={'message_key':message_key}
        if decision['action']=='reply':payload['text']=decision['text']
        else:payload.update(requested=True,resume_sha256=config['resume_sha256'])
        decision['action_id']=store.prepare_action(run_id,target['attempt_id'],decision['action'],conversation,payload)
    store.export()
    return decision


def send_action(store, run_id, action_id):
    from .browser_bridge import bridge_call
    from .boss_script import save_frame
    config=verify_materials(store,run_id)
    action=store.one('actions',action_id)
    if action['kind'] not in ('reply','resume') or action['run_id']!=run_id:
        raise ValueError('不是本轮受控消息动作')
    attempt=store.one('attempts',action['attempt_id'])
    if attempt['channel']!='boss' or not store.owns_attempt(run_id,attempt['id']):
        raise ValueError('消息动作不属于本轮 BOSS 申请')
    payload=json.loads(action['payload'])
    row=store.conn.execute('SELECT text FROM messages WHERE conversation_id=? AND message_key=?',
                           (action['recipient'],payload['message_key'])).fetchone()
    if not row:raise ValueError('对方原始消息不存在')
    manual=store.manual_send_allowed(action)
    if INTERVIEW.search(row[0]) and not manual:
        store.handoff(action['recipient'],'interview');raise ValueError('面试事项需要用户明确回复')
    store.claim_action(run_id,action_id)
    try:
        params={'action_id':action_id,'conversation':action['recipient'],'message_key':payload['message_key'],
                'message_text':row[0],'manual':manual}
        if action['kind']=='reply':params['text']=payload['text']
        else:params['resume_path']=config['resume_path']
        result=bridge_call(store.root,action['kind'],run_id,**params)
        save_frame(store,run_id,result)
        evidence=store.evidence(action['attempt_id'],result['frame']['path'],'result')
        if result.get('not_done') is True:
            store.finish_action(action_id,'not_done',evidence,result.get('reason') or '目标会话未就绪，未输入或点击发送')
            store.export();return result
        if result.get('sent') is not True:
            store.finish_action(action_id,'uncertain',evidence,'未找到本次新增的目标消息/附件回执')
            store.handoff(action['recipient'],'发送结果不明，请核查，禁止重发')
            raise ValueError('发送结果不明，已转人工核查')
        store.finish_action(action_id,'succeeded',evidence,'绑定会话出现本次新增的发送回执')
        store.export();return result
    except Exception as error:
        if store.one('actions',action_id)['status']=='executing':
            store.finish_action(action_id,'uncertain',reason=str(error))
            store.handoff(action['recipient'],'发送未能核实，请人工检查：'+str(error))
        store.export();raise


def finish_ai_watch(store, run_id):
    state=read_watch_state(store,run_id)
    pending=state.pop('pending_ai',None)
    if not pending:return {'resolved':False}
    current=store.one('conversations',pending['conversation'])
    success=store.conn.execute("SELECT 1 FROM actions WHERE recipient=? AND json_extract(payload,'$.message_key')=? AND status='succeeded'",
                               (pending['conversation'],pending['message_key'])).fetchone()
    if current['status']=='auto' and current['latest_message']==pending['message_key'] and not success:
        _attention(store,pending['conversation'],'AI 未完成可核实回复，请人工处理')
    _save_state(store,run_id,state);store.export()
    return {'resolved':True}


def watch_step(store, run_id):
    from .app_runs import app_session
    from .boss_script import browser_action
    from .desktop import desktop_lock
    app_session(store,run_id);verify_materials(store,run_id);store.check_run(run_id)
    state=read_watch_state(store,run_id)
    with desktop_lock(store.root):
        queue=state.setdefault('queued_ai',{})
        pending=state.get('pending_ai')
        if pending:
            current=store.one('conversations',pending['conversation'])
            decided=store.conn.execute("SELECT 1 FROM actions WHERE recipient=? AND json_extract(payload,'$.message_key')=? AND status!='not_done'",
                                       (pending['conversation'],pending['message_key'])).fetchone()
            if current['status']=='human' or current['latest_message']!=pending['message_key'] or decided:
                state.pop('pending_ai');_save_state(store,run_id,state)
                pending=None
                with store.conn:
                    store.conn.execute("UPDATE attention SET resolved=1 WHERE subject=? AND reason=?",(current['id'],'等待 AI 处理，可连接 Codex 或人工接管'))
        if not pending and queue:
            key=next(iter(queue));pending=queue.pop(key);state['pending_ai']=pending
        if state['phase']=='respond':
            action=state.pop('action_id');state['phase']='observe';_save_state(store,run_id,state)
            send_action(store,run_id,action)
            return {'done':False,'message':'已按预设规则处理消息','delay_seconds':2}
        operation='open_conversation' if state['phase']=='open' else 'messages'
        params={'conversation':state['open_conversation']} if operation=='open_conversation' else {}
        observation=browser_action(store,run_id,operation,**params)
        state['phase']='observe'
        conversation=observation.get('conversation')
        messages=observation.get('messages',[])
        if messages and (not conversation or not messages[-1].get('key')):
            raise ValueError('未能可靠识别当前会话与最新消息编号，请核对浏览器页面后继续')
        if conversation and messages:
            latest=messages[-1]
            old=state['conversations'].get(conversation,{})
            unseen=[message for message in messages if message.get('key') and message['key'] not in old.get('seen_keys',[])]
            incoming=[message for message in unseen if message.get('direction')=='incoming']
            for message in incoming:
                store.observe_message(conversation,message['key'],message['text'])
            target=old.get('attempt_id')
            if not target:
                keys=observation.get('job_keys',[])
                candidates=[row['id'] for row in store.conn.execute('SELECT a.* FROM attempts a WHERE a.channel=? AND (a.run_id=? OR EXISTS(SELECT 1 FROM run_attempts r WHERE r.run_id=? AND r.attempt_id=a.id))',('boss',run_id,run_id)) if row['job_key'] in keys]
                if len(candidates)==1:target=candidates[0]
            current={**old,'seen_keys':[message['key'] for message in messages if message.get('key')][-200:]}
            if target and latest.get('key') and latest.get('direction')=='incoming':
                current.update(attempt_id=target,message_key=latest['key'],text=latest['text'])
            state['conversations'][conversation]=current
            _save_state(store,run_id,state)
            if incoming:
                if any(INTERVIEW.search(message['text']) for message in incoming):
                    _attention(store,conversation,'interview')
                elif len(incoming)>1:
                    _attention(store,conversation,'多条未处理消息，需整体核对后回复')
                elif latest.get('direction')=='incoming' and latest.get('key')==incoming[0]['key']:
                    if not target:
                        _attention(store,conversation,'无法唯一绑定本轮岗位，请人工确认会话')
                    else:
                        decision=decide_message(store,run_id,conversation,latest['key'])
                        if decision['action']=='ai_required':
                            if pending:state['queued_ai'][conversation]=decision
                            else:state['pending_ai']=decision;pending=decision
                            _save_state(store,run_id,state)
                            notice=_attention(store,conversation,'等待 AI 处理，可连接 Codex 或人工接管',handoff=False)
                            return {'done':False,'message':'新问题需要 AI 根据资料组织回答','ai_required':pending,'notify':notice['notify'],'delay_seconds':60}
                        if decision.get('action_id'):
                            state.update(phase='respond',action_id=decision['action_id']);_save_state(store,run_id,state)
                            return {'done':False,'message':'已匹配预设规则，待核验会话后发送','delay_seconds':2}
        unread=[row for row in observation.get('conversations',[]) if row.get('unread') and row.get('key')!=conversation]
        if unread:
            state.update(phase='open',open_conversation=unread[0]['key']);_save_state(store,run_id,state)
            return {'done':False,'message':'发现其他会话的新消息','delay_seconds':2}
        _save_state(store,run_id,state);store.export()
        return {'done':False,'message':'后台消息已检查','delay_seconds':60,**({'ai_required':pending,'notify':False} if pending else {})}
