"""保存用户原话与匹配条件；保存不发送消息。"""
import json
import uuid

from .policy import decide_reply


def read_settings(store):
    path=store.root/'reply-settings.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'rules':[],'unknown_reply_mode':'human'}


def save_settings(store,value):
    if value.get('unknown_reply_mode') not in ('human','ai') or not isinstance(value.get('rules'),list):
        raise ValueError('请选择未匹配问题的处理方式，并提供规则列表')
    if len(value['rules'])>50:
        raise ValueError('回复规则最多 50 条')
    identities=set()
    for rule in value['rules']:
        if (not isinstance(rule,dict) or not isinstance(rule.get('id'),str) or not rule['id']
                or rule['id'] in identities or rule.get('category') not in ('salary','location','availability','other')
                or not isinstance(rule.get('reply'),str) or not rule['reply'].strip()
                or len(rule['reply'])>2000 or not isinstance(rule.get('when'),dict)):
            raise ValueError('规则需要唯一编号、类别、匹配条件和回复原文')
        identities.add(rule['id'])
        for conditions in rule['when'].values():
            if not isinstance(conditions,dict) or not conditions or not set(conditions)<= {'eq','in','gte','lte'}:
                raise ValueError('匹配条件支持 eq、in、gte、lte')
        if rule.get('category') in ('salary','location') and not rule['when'] and rule.get('enabled',True):
            raise ValueError('薪资和地点规则启用前必须设置匹配条件')
    saved={'rules':value['rules'],'unknown_reply_mode':value['unknown_reply_mode']}
    path=store.root/('reply-settings-'+uuid.uuid4().hex+'.tmp')
    path.write_text(json.dumps(saved,ensure_ascii=False,indent=2),encoding='utf-8')
    path.replace(store.root/'reply-settings.json')
    return saved


def preview_reply(store,params):
    settings=read_settings(store)
    return decide_reply(params['text'],settings['rules'],settings['unknown_reply_mode'],params.get('analysis'))
