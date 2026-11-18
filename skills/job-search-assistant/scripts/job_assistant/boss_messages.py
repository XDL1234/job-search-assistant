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


def remember_job_context(store, run_id, attempt, evidence):
    """只记录实际打开并已成功沟通岗位的可见卡片；不是模型提供的匹配提示。"""
    source=store.root/'agent-browser-state'/(run_id+'.json')
    if not source.is_file():return
    card=json.loads(source.read_text(encoding='utf-8')).get('source_card',{})
    if (card.get('key')!=attempt['job_key'] or card.get('company')!=attempt['company'] or card.get('job')!=attempt['job']):return
    folder=store.root/'boss-job-context';folder.mkdir(exist_ok=True)
    path=folder/(attempt['id']+'.json')
    path.write_text(json.dumps({'attempt_id':attempt['id'],'job_key':attempt['job_key'],'evidence_id':evidence,
        'card':{key:card.get(key,'') for key in ('company','job','city','salary')}},ensure_ascii=False),encoding='utf-8')


def _proven_job_card(store, attempt):
    successful=list(store.conn.execute("SELECT evidence_id FROM actions WHERE attempt_id=? AND kind='greet' AND status='succeeded'",(attempt['id'],)))
    if not successful:return None
    record_path=store.root/'boss-job-context'/(attempt['id']+'.json')
    if record_path.is_file():
        record=json.loads(record_path.read_text(encoding='utf-8'))
        if record.get('attempt_id')!=attempt['id'] or record.get('job_key')!=attempt['job_key']:
            raise ValueError('岗位可见事实记录不属于当前申请')
        if record.get('evidence_id') not in [row[0] for row in successful]:return None
        store.check_evidence(record['evidence_id'],attempt['id'])
        return record.get('card')
    # 已发布版本的最后一个实际岗位可由持久脚本进度恢复，仍要求成功回执PNG。
    for row in successful:store.check_evidence(row[0],attempt['id'])
    legacy=store.root/'script-state'/(attempt['run_id']+'.json')
    card=json.loads(legacy.read_text(encoding='utf-8')).get('card',{}) if legacy.is_file() else {}
    if not card:
        saved=store.conn.execute('SELECT state FROM app_ai_selection WHERE run_id=?',(attempt['run_id'],)).fetchone()
        card=json.loads(saved[0]).get('current_card',{}) if saved else {}
    if card.get('key')==attempt['job_key'] and card.get('company')==attempt['company'] and card.get('job')==attempt['job']:
        return card
    return None


def _matching_attempts(store, run_id, observation):
    attempts=list(store.conn.execute('''SELECT a.* FROM attempts a WHERE a.channel='boss'
        AND (a.run_id=? OR EXISTS(SELECT 1 FROM run_attempts r WHERE r.run_id=? AND r.attempt_id=a.id))''',(run_id,run_id)))
    keys=observation.get('job_keys',[])
    visible=observation.get('identity_source')=='visible_identity' or str(observation.get('conversation','')).startswith('visible-v1:')
    if keys and not visible:return [row['id'] for row in attempts if row['job_key'] in keys]
    context=observation.get('job_context')
    fields=('company','job','city','salary')
    if not isinstance(context,dict) or any(not isinstance(context.get(key),str) or not context[key].strip() for key in fields):return []
    def normalized(value,key):
        value=re.sub(r'\s+','',value)
        return re.split(r'[·•/]',value)[0] if key=='city' else value
    matched=[]
    for attempt in attempts:
        card=_proven_job_card(store,attempt)
        if card and all(isinstance(card.get(key),str) and card[key].strip() and
            normalized(card[key],key)==normalized(context[key],key) for key in fields):matched.append(attempt['id'])
    return matched


def _identity_drift(store, attempt_id, conversation, latest):
    previous={row[0] for row in store.conn.execute("SELECT DISTINCT recipient FROM actions WHERE attempt_id=? AND kind IN ('reply','resume') AND status!='not_done'",(attempt_id,))}
    for path in (store.root/'boss-watch').glob('*.json'):
        if not re.fullmatch(r'[0-9a-f]{32}\.json',path.name):continue
        state=json.loads(path.read_text(encoding='utf-8'))
        previous.update(key for key,item in state.get('conversations',{}).items() if item.get('bound_attempt_id',item.get('attempt_id'))==attempt_id)
    if latest and latest.get('key'):
        previous.update(row[0] for row in store.conn.execute('SELECT conversation_id FROM messages WHERE message_key=?',(latest['key'],)))
    return sorted(previous-{conversation})


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


def send_action(store, run_id, action_id, expected_target=None):
    from .agent_browser import browser_call, action_check
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
    target=read_watch_state(store,run_id).get('conversations',{}).get(action['recipient'],{})
    if action['recipient'].startswith('visible-v1:') and (target.get('attempt_id')!=action['attempt_id'] or
            _matching_attempts(store,run_id,{'conversation':action['recipient'],
                'job_keys':target.get('job_keys',[]),'job_context':target.get('job_context')})!=[action['attempt_id']]):
        raise ValueError('可见会话身份没有当前唯一且证据完整的岗位绑定')
    store.claim_action(run_id,action_id)
    try:
        params={'action_id':action_id,'conversation':action['recipient'],'message_key':payload['message_key'],
                'message_text':row[0],'manual':manual}
        if expected_target:
            params['expected_target']=expected_target
        if target.get('attempt_id')==action['attempt_id']:
            if action['recipient'].startswith('visible-v1:') and target.get('job_context'):
                params['expected_job_context']=target['job_context']
            elif target.get('job_keys'):
                params['expected_job_key']=attempt['job_key']
            elif target.get('job_context'):
                params['expected_job_context']=target['job_context']
        elif action['recipient'].startswith('visible-v1:'):
            raise ValueError('可见会话身份没有当前已核验岗位绑定')
        if action['kind']=='reply':params['text']=payload['text']
        else:params['resume_path']=config['resume_path']
        result=browser_call(store.root,action['kind'],run_id,
                            check=action_check(store,run_id,action['kind'],params),**params)
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


def watch_step(store, run_id, expected_target=None):
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
            send_action(store,run_id,action,expected_target=expected_target)
            return {'done':False,'message':'已按预设规则处理消息','delay_seconds':2}
        operation='open_conversation' if state['phase']=='open' else 'messages'
        params={'conversation':state['open_conversation']} if operation=='open_conversation' else {}
        if expected_target:
            params['expected_target']=expected_target
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
            interview=[message for message in unseen if message.get('direction')!='outgoing' and INTERVIEW.search(message['text'])]
            for message in incoming:
                store.observe_message(conversation,message['key'],message['text'])
            candidates=_matching_attempts(store,run_id,observation)
            target=candidates[0] if len(candidates)==1 else None
            drift=_identity_drift(store,target,conversation,latest) if target else []
            current={**old,'seen_keys':[message['key'] for message in messages if message.get('key')][-200:]}
            if target:
                current.update(attempt_id=target,bound_attempt_id=old.get('bound_attempt_id',old.get('attempt_id',target)),
                               job_keys=observation.get('job_keys',[]),job_context=observation.get('job_context'))
            else:current.pop('attempt_id',None)
            if target and latest.get('key') and latest.get('direction')=='incoming':
                current.update(attempt_id=target,message_key=latest['key'],text=latest['text'])
            else:
                current.pop('message_key',None);current.pop('text',None)
            state['conversations'][conversation]=current
            _save_state(store,run_id,state)
            changed_job=target and current.get('bound_attempt_id')!=target
            if drift or changed_job:
                reason='岗位联系人可见身份或关联岗位已变化，请人工核对后再恢复'
                for previous in drift:_attention(store,previous,reason)
                _attention(store,conversation,reason)
            elif interview:
                _attention(store,conversation,'interview')
            elif incoming:
                if any(message.get('kind','text')!='text' for message in incoming):
                    _attention(store,conversation,'来信包含尚未支持的非文本内容，请人工核对')
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
