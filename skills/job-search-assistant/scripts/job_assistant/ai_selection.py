"""脚本收集真实候选和执行，AI 仅返回有界的结构化精筛结论。"""
import hashlib
import json
from pathlib import Path
import re
import uuid

from .app_decisions import check_generation, current_generation
from .app_runs import app_session, reset_unsent_attempts
from .boss_script import browser_action, greet
from .cli import verify_materials
from .city_filter import city_match
from .desktop import desktop_lock
from .policy import select_job
from .store import now, packed


VERSION = 'job-selection-v1'
MAX_INPUT_CHARS = 24000
CARD_FIELDS = ('key','url','job','company','city','salary','tags','recruiter','contacted')
DETAIL_FIELDS = ('heading','company','salary','location','detail','detail_contacted','content_ready')


def digest(value):
    return hashlib.sha256(packed(value).encode()).hexdigest()


def _load(store, run_id):
    row=store.conn.execute('SELECT state FROM app_ai_selection WHERE run_id=?',(run_id,)).fetchone()
    return json.loads(row[0]) if row else None


def _save(store, run_id, state):
    store.conn.execute('''INSERT INTO app_ai_selection VALUES (?,?)
      ON CONFLICT(run_id) DO UPDATE SET state=excluded.state''',(run_id,packed(state)))


def _config(store, run_id):
    app_session(store,run_id)
    config=verify_materials(store,run_id)
    store.check_run(run_id)
    if store.one('runs',run_id)['status']!='running':
        raise ValueError('当前仅值守消息，不能新增岗位判断或投递')
    if config.get('channels')!=['boss'] or config.get('execution_mode')!='ai' or config.get('simulation') is True:
        raise ValueError('当前轮次不是实际 BOSS AI 筛选任务')
    return config


def _profile(config):
    profile=json.loads(Path(config['profile_path']).read_text(encoding='utf-8'))
    keys=('target_role','education','skills','projects','experience','work_experience','job_intent',
          'graduation_date','availability','salary_expectation','preferred_cities')
    result={key:profile[key] for key in keys if profile.get(key)}
    if profile.get('resume_text'):
        lines=[]
        for line in str(profile['resume_text']).splitlines():
            line=re.sub(r'[A-Za-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|(?<!\d)1[3-9]\d{9}(?!\d)','',line).strip()
            if line and line!=profile.get('name') and line not in lines and not re.match(r'^(?:姓名|联系方式|联系电话|邮箱|现居地址|身份证)',line):
                lines.append(line)
        result['resume_facts']='\n'.join(lines)
    if len(packed(result))>12000:
        raise ValueError('求职事实资料过长，不能静默截断后精筛；请提供精简的求职事实')
    return result


def _context(config):
    keys=('target_roles','employment_type','boss_mode','filters','cities','keywords','exclude_keywords','boss_filters')
    return {'version':VERSION,'requirements':{key:config[key] for key in keys if key in config},'profile':_profile(config),
            'material_version':{key:config[key] for key in ('profile_version','profile_sha256','resume_sha256') if key in config}}


def _facts(card, detail, mode, card_current=True):
    card_current=detail.get('source_card_current',card_current)
    if type(card_current) is not bool:raise ValueError('候选卡片来源状态无效')
    facts={'card':{key:card[key] for key in CARD_FIELDS if key in card},'card_current':card_current}
    if mode=='screened':
        facts['detail']={key:detail[key] for key in DETAIL_FIELDS if key in detail}
        facts['detail']['content_ready']=bool(detail.get('detail') and detail.get('heading'))
    return facts


def _cache_key(context, facts):
    return digest({'context':context,'facts':facts})


def _city_scope_allows(config, facts):
    cities=config.get('cities', [])
    match=city_match(facts['card'].get('city'),cities)
    if match is False or (config['boss_mode']=='filtered' and match is not True):
        return False
    return config['boss_mode']!='screened' or city_match(facts.get('detail',{}).get('location'),cities) is not False


def _review(store, card, reason, title=None):
    review = {field: card[field] for field in ('company', 'job', 'city', 'salary', 'url') if card.get(field)}
    store.attention(card['key'], title or reason, job_review={**review, 'reason': reason})


def _filters(state, observed):
    actual=observed.get('filters',[])
    if not isinstance(actual,list) or any(not isinstance(item,str) for item in actual):
        raise ValueError('无法核对平台城市和筛选条件')
    if 'filters' in state and actual!=state['filters']:
        raise ValueError('平台城市或筛选条件已变化，AI 筛选已停止')
    state.setdefault('filters',actual)


def _bind_target(state, observed):
    target=observed.get('tab_id') or observed.get('frame',{}).get('window',{}).get('tab_id')
    if not target:
        raise ValueError('观察未提供绑定标签身份')
    if state.get('target') and target!=state['target']:
        raise ValueError('任务绑定标签已经变化')
    state['target']=target


def _browser(store, run_id, state, operation, **params):
    return browser_action(store,run_id,operation,expected_target=state.get('target'),**params)


def _candidates(store, observed, state, maximum, cities=()):
    excluded={row[0] for row in store.conn.execute("SELECT job_key FROM attempts WHERE channel='boss' AND status IN ('success','uncertain','in_progress','needs_user')")}
    excluded.update(state.get('seen',[]))
    cards=[card for card in observed.get('cards',[]) if card.get('key') not in excluded
           and card.get('company') and card.get('job') and card.get('url')
           and city_match(card.get('city'), cities) is not False]
    chosen=[]
    while len(chosen)<maximum:
        card=select_job(cards,'filtered')
        if not card:break
        chosen.append(card)
        cards=[row for row in cards if row['key']!=card['key']]
    return chosen


def _next_phase(state):
    return 'execute_open' if state.get('approved') else 'more_decisions' if state.get('deferred') else 'list'


def _freeze(store, run_id, state, context):
    context={**context,'platform_filters':state.get('filters',[])}
    unknown=[]
    deferred=[]
    size=len(packed(context))
    for item in state.pop('collected',[])+state.pop('deferred',[]):
        key=_cache_key(context,item)
        row=store.conn.execute('SELECT value FROM app_ai_cache WHERE cache_key=?',(key,)).fetchone()
        if row:
            decision=json.loads(row[0])
            state.setdefault('seen',[]).append(item['card']['key'])
            state['cache_hits']=state.get('cache_hits',0)+1
            if decision['decision']=='accept':state.setdefault('approved',[]).append(item)
        else:
            length=len(packed(item))
            if len(packed(context))+length>MAX_INPUT_CHARS:
                state.setdefault('seen',[]).append(item['card']['key'])
                state['oversized']=state.get('oversized',0)+1
                _review(store,item['card'],'岗位或资料超过本轮文本预算，未截断判断、未投递，请人工查看')
            elif size+length>MAX_INPUT_CHARS:
                deferred.append(item)
            else:
                unknown.append(item);size+=length
    state['deferred']=deferred
    if unknown:
        pending={'decision_id':uuid.uuid4().hex,'control_generation':state['generation'],
                 'config_hash':state['config_hash'],'context':context,'candidates':unknown}
        pending['facts_hash']=digest({'context':context,'candidates':unknown})
        state['pending']=pending
        state['phase']='await_ai'
    else:
        state['phase']=_next_phase(state)
    with store.conn:
        check_generation(store,run_id,state['generation'])
        _save(store,run_id,state)


def selection_context(store, run_id):
    config=_config(store,run_id)
    state=_load(store,run_id)
    if not state or state.get('phase')!='await_ai':raise ValueError('没有等待判断的真实候选批次')
    pending=state['pending']
    check_generation(store,run_id,pending['control_generation'])
    if digest(config)!=pending['config_hash']:raise ValueError('资料或配置已变化，候选判断失效')
    public={key:pending['context'][key] for key in ('version','requirements','profile','platform_filters')}
    return {'decision_id':pending['decision_id'],'control_generation':pending['control_generation'],
            'facts_hash':pending['facts_hash'],**public,
            'candidates':[{'key':item['card']['key'],**item} for item in pending['candidates']],
            'response_schema':{'decisions':[{'key':'真实候选 key','decision':'accept|reject|uncertain','reason':'不超过300字'}]}}


def accept_selection(store, run_id, decision_id, decisions):
    config=_config(store,run_id)
    with store.conn:
        store.conn.execute('BEGIN IMMEDIATE')
        state=_load(store,run_id)
        if not state or state.get('phase')!='await_ai' or state.get('pending',{}).get('decision_id')!=decision_id:
            raise ValueError('候选判断批次不存在、已消费或已失效')
        pending=state['pending']
        check_generation(store,run_id,pending['control_generation'])
        if digest(config)!=pending['config_hash']:
            raise ValueError('资料或配置已变化，候选判断失效')
        if digest({'context':pending['context'],'candidates':pending['candidates']})!=pending['facts_hash']:
            raise ValueError('本地候选事实包校验失败')
        actual={item['card']['key']:item for item in pending['candidates']}
        if not isinstance(decisions,list) or len(decisions)!=len(actual):
            raise ValueError('必须逐一返回本批真实候选的判断')
        checked={}
        for decision in decisions:
            if (not isinstance(decision,dict) or set(decision)!={'key','decision','reason'}
                    or not isinstance(decision.get('key'),str)
                    or decision['key'] not in actual or decision['key'] in checked
                    or decision['decision'] not in ('accept','reject','uncertain')
                    or not isinstance(decision['reason'],str) or not 0<len(decision['reason'].strip())<=300):
                raise ValueError('模型候选判断格式、身份或理由无效')
            item=actual[decision['key']]
            if decision['decision']=='accept' and not _city_scope_allows(config,item):
                raise ValueError('岗位城市超出或尚未明确符合授权范围，不能接受')
            if decision['decision']=='accept' and config['boss_mode']=='screened' and not item['detail'].get('content_ready'):
                raise ValueError('岗位详情不完整，不能标记已精筛接受')
            if decision['decision']=='accept' and config['boss_mode']=='screened' and not pending['context']['profile']:
                raise ValueError('缺少求职事实，不能标记已精筛接受')
            checked[decision['key']]=decision
        for key,item in actual.items():
            decision=checked[key]
            store.conn.execute('''INSERT INTO app_ai_cache VALUES (?,?,?)
              ON CONFLICT(cache_key) DO UPDATE SET value=excluded.value,updated=excluded.updated''',
                               (_cache_key(pending['context'],item),packed(decision),now()))
            state.setdefault('seen',[]).append(key)
            if decision['decision']=='accept':state.setdefault('approved',[]).append(item)
        state['last_batch']={'accepted_count':sum(item['decision']=='accept' for item in checked.values()),
                             'rejected_count':sum(item['decision']=='reject' for item in checked.values()),
                             'uncertain_count':sum(item['decision']=='uncertain' for item in checked.values())}
        state.pop('pending')
        state['phase']=_next_phase(state)
        _save(store,run_id,state)
    for decision in checked.values():
        if decision['decision']=='uncertain':
            card=actual[decision['key']]['card']
            _review(store,card,decision['reason'],'岗位精筛待人工查看：'+card['company']+' · '+card['job']+'；'+decision['reason'])
    return {'saved':True,**state['last_batch']}


def selection_step(store, run_id, batch_size=8):
    config=_config(store,run_id)
    if type(batch_size) is not int or not 1<=batch_size<=8:raise ValueError('候选批次必须为1至8条')
    generation=current_generation(store,run_id)
    context=_context(config)
    with desktop_lock(store.root):
        state=_load(store,run_id)
        if state and state['phase']=='done' and state['config_hash']==digest(config):
            return {'done':True,'message':state['reason']}
        if not state:
            state={'phase':'list','page_at':'list','generation':generation,'config_hash':digest(config),'seen':[],'approved':[]}
        elif state['generation']!=generation or state['config_hash']!=digest(config):
            # 恢复不重新授权旧响应；回原列表后从真实状态重新收集。
            with store.conn:
                store.conn.execute('BEGIN IMMEDIATE')
                check_generation(store,run_id,generation)
                store.check_run(run_id,'greet')
                reset_unsent_attempts(store,run_id,'恢复后重新核验岗位，原岗位尚未发送')
            previous=state
            state={'phase':'reset_return' if previous.get('page_at')=='detail' else 'list','page_at':previous.get('page_at','list'),
                   'generation':generation,'config_hash':digest(config),'seen':[],'approved':[],
                   **{key:previous[key] for key in ('filters','target') if key in previous}}
        used=store.conn.execute("SELECT COUNT(*) FROM actions WHERE run_id=? AND kind='greet' AND status!='not_done'",(run_id,)).fetchone()[0]
        if used>=config['max_contacts']:return {'done':True,'message':'已达到本轮沟通上限'}
        batch_size=min(batch_size,config['max_contacts']-used)
        if store.conn.execute("SELECT 1 FROM actions WHERE run_id=? AND status IN ('pending','executing','uncertain')",(run_id,)).fetchone():
            raise ValueError('存在未决动作，请先核查，禁止新增沟通')
        phase=state['phase']
        if phase=='done':return {'done':True,'message':state['reason']}
        if phase=='await_ai':return {'done':False,'message':'真实候选已就绪，等待一次结构化判断','ai_required':selection_context(store,run_id)}
        if phase=='more_decisions':
            _freeze(store,run_id,state,context)
            message='继续判断按文本长度拆分的真实候选'
        elif phase in ('list','next','reset_return'):
            operation='return' if phase=='reset_return' else 'next' if phase=='next' else 'observe'
            observed=_browser(store,run_id,state,operation)
            _bind_target(state,observed);_filters(state,observed)
            state['page_at']='list'
            cards=_candidates(store,observed,state,batch_size,config.get('cities', []))
            if not observed.get('cards'):raise ValueError('未识别真实岗位列表，未开始 AI 判断或发送')
            if not cards:
                stamp=digest({'cards':observed['cards'],'position':observed.get('position')})
                visits=state.setdefault('visits',[]);visits.append(stamp)
                if visits.count(stamp)>=3 or len(visits)>=64:
                    state.update(phase='done',reason='列表无新增候选、出现循环或已达扫描上限')
                else:state['phase']='next'
            elif config['boss_mode']=='screened':
                state.update(queue=cards,collected=[],phase='collect_open')
            else:
                state['collected']=[_facts(card,{},'filtered') for card in cards]
                _freeze(store,run_id,state,context)
            message='已读取实际岗位候选'
        elif phase=='collect_open':
            card=state['queue'][0]
            observed=_browser(store,run_id,state,'open_job',key=card['key'])
            _bind_target(state,observed)
            state['page_at']='detail'
            state.setdefault('collected',[]).append(_facts(observed.get('source_card',card),observed,'screened'))
            state['phase']='collect_return'
            message='已读取真实详情：'+card['job']
        elif phase=='collect_return':
            observed=_browser(store,run_id,state,'return')
            _bind_target(state,observed);_filters(state,observed)
            state['page_at']='list'
            state['queue'].pop(0)
            if state['queue']:state['phase']='collect_open'
            else:_freeze(store,run_id,state,context)
            message='已返回列表，继续收集或等待判断'
        elif phase=='execute_open':
            chosen=state['approved'][0]
            listing=_browser(store,run_id,state,'observe')
            _bind_target(state,listing);_filters(state,listing)
            card=next((row for row in listing.get('cards',[]) if row.get('key')==chosen['card']['key']),None)
            if card and card.get('contacted') is True:
                state['approved'].pop(0)
                state['phase']=_next_phase(state)
                message='已接受岗位已沟通，跳过'
            else:
                observed=_browser(store,run_id,state,'open_job',key=chosen['card']['key'])
                _bind_target(state,observed)
                state['page_at']='detail'
                card=observed.get('source_card',card or chosen['card'])
                state['current_card']=card
                fresh=_facts(card,observed,config['boss_mode'])
                state['current_card_current']=fresh['card_current']
                if not _city_scope_allows(config,fresh):
                    _review(store,card,'岗位城市超出或尚未明确符合授权范围，未投递，请人工查看')
                    state['approved'].pop(0);state['phase']='execute_return'
                    message='当前岗位城市不满足授权范围，已跳过，未投递'
                elif config['boss_mode']=='filtered' and not fresh['card_current']:
                    _review(store,card,'岗位列表已变化，历史卡片不能代替当前平台筛选条件，未投递，请人工查看')
                    state['approved'].pop(0);state['phase']='execute_return'
                    message='平台筛选卡片已离开当前列表，已记录待核查，未投递'
                elif observed.get('detail_contacted') is True or card.get('contacted') is True:
                    state['approved'].pop(0);state['phase']='execute_return'
                    message='岗位详情显示已沟通，跳过'
                elif digest(fresh)!=digest(chosen):
                    state['collected']=[fresh];state['approved'].pop(0);state['phase']='changed_return'
                    message='岗位事实已变化，重新判断，未创建发送动作'
                else:state['phase']='greet';message='已核对接受岗位，准备发送前复核'
        elif phase=='greet':
            observed=_browser(store,run_id,state,'observe')
            _bind_target(state,observed)
            fresh=_facts(state['current_card'],observed,config['boss_mode'],state.get('current_card_current',True))
            chosen=state['approved'][0]
            if not _city_scope_allows(config,fresh):
                _review(store,state['current_card'],'发送前岗位城市不满足授权范围，未投递，请人工查看')
                state['approved'].pop(0);state['phase']='execute_return'
                message='发送前岗位城市不满足授权范围，已跳过，未投递'
            elif digest(fresh)!=digest(chosen):
                state['collected']=[fresh];state['approved'].pop(0);state['phase']='changed_return'
                message='发送前事实改变，重新判断，未创建发送动作'
            else:
                card=state['current_card']
                attempt=store.begin_attempt(run_id,card['company'],card['url'],card['job'],'boss',card['key'])
                store.evidence(attempt,observed['frame']['path'],'pre_submit')
                action=store.prepare_action(run_id,attempt,'greet',card.get('recruiter') or card['key'],{})
                greet(store,run_id,attempt,action,observation_id=observed.get('observation_id'),expected_target=state['target'])
                state['approved'].pop(0);state['phase']='execute_return'
                message='已按有效判断沟通：'+card['company']+' · '+card['job']
        elif phase in ('execute_return','changed_return'):
            observed=_browser(store,run_id,state,'return')
            _bind_target(state,observed);_filters(state,observed)
            state['page_at']='list'
            if phase=='changed_return':_freeze(store,run_id,state,context)
            else:state['phase']=_next_phase(state)
            message='已返回原筛选列表'
        else:raise ValueError('AI 筛选状态损坏，已停止')
        with store.conn:
            check_generation(store,run_id,generation)
            _save(store,run_id,state)
        answer={'done':state['phase']=='done','message':message,'phase':state['phase'],'cache_hits':state.get('cache_hits',0)}
        if state['phase']=='await_ai':answer['ai_required']=selection_context(store,run_id)
        return answer
