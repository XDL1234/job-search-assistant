"""模型负责语义提取，本模块对结构化判断实施确定性规则。"""
import re


def value_at(data, path):
    current = data
    for part in path.split('.'):
        if not isinstance(current, dict) or part not in current:
            raise KeyError(path)
        current = current[part]
    return current


def matches(facts, conditions):
    for key, constraints in conditions.items():
        try:
            value = value_at(facts, key)
        except KeyError:
            return False
        if not isinstance(constraints, dict) or not constraints:
            return False
        for operator, expected in constraints.items():
            if operator == 'eq':
                valid = type(value) is type(expected) and value == expected
            elif operator == 'in':
                valid = isinstance(expected, list) and any(type(value) is type(v) and value == v for v in expected)
            elif operator in ('gte', 'lte'):
                valid = type(value) in (int, float) and type(expected) in (int, float)
                valid = valid and (value >= expected if operator == 'gte' else value <= expected)
            else:
                return False
            if not valid:
                return False
    return True


def decide_reply(message, rules, mode='human', analysis=None, profile=None, draft='', sources=None, human_locked=False):
    analysis = analysis or {}
    categories = analysis.get('categories', [])
    def human(reason):
        return {'action': 'human', 'reason': reason, 'text': ''}
    # 词面兜底不能替代模型识别“过来聊聊”等间接面试邀请。
    if re.search(r'面试|interview', message, re.I) or 'interview' in categories or analysis.get('interview'):
        return human('interview')
    if human_locked:
        return human('human_locked')
    if analysis.get('complete') is not True or not categories or analysis.get('ambiguous'):
        return human('incomplete_analysis')
    if analysis.get('new_commitment'):
        return human('new_commitment')
    if 'resume' in categories:
        return {'action': 'resume', 'reason': 'requested', 'text': ''} if set(categories) == {'resume'} else human('mixed_request')
    texts, used = [], []
    for category in dict.fromkeys(categories):
        applicable = [rule for rule in rules if rule.get('enabled', True) and rule.get('category') == category
                      and rule.get('reply', '').strip() and matches(analysis.get('facts', {}), rule.get('when', {}))]
        if category in ('salary', 'location'):
            applicable = [rule for rule in applicable if rule.get('when')]
        if len(applicable) > 1:
            return human('conflicting_rules')
        if len(applicable) == 1:
            texts.append(applicable[0]['reply'])
            used.append(applicable[0]['id'])
        elif category != 'other':
            return human('no_matching_rule')
    if len(texts) == len(set(categories)):
        return {'action': 'reply', 'text': '\n'.join(texts), 'rule_ids': used, 'reason': 'template'}
    if texts:
        return human('partial_answer')
    if mode != 'ai':
        return human('unknown_question')
    if not draft.strip() or not sources or analysis.get('new_commitment') is not False:
        return human('needs_grounded_draft')
    try:
        for source in sources:
            value = value_at(profile or {}, source)
            if value is None or value == '' or value == []:
                return human('missing_source')
    except KeyError:
        return human('missing_source')
    # 此处验证出处存在；回答是否确实由这些事实支持仍由调用 Skill 的模型核验。
    return {'action': 'reply', 'text': draft, 'sources': sources, 'reason': 'grounded_ai'}


def select_job(jobs, mode):
    candidates = [job for job in jobs if job.get('key') and job.get('contacted') is False and not job.get('blocked')]
    if mode == 'filtered':
        return candidates[0] if candidates else None
    if mode != 'screened':
        raise ValueError('未知岗位筛选模式')
    qualified = []
    for job in candidates:
        assessment = job.get('evaluation', {})
        conditions = assessment.get('hard_conditions')
        if not isinstance(conditions, list) or not conditions or any(v != 'yes' for v in conditions):
            continue
        if type(assessment.get('fit')) not in (int, float) or not 0 <= assessment['fit'] <= 100:
            continue
        qualified.append(job)
    return max(qualified, key=lambda item: item['evaluation']['fit']) if qualified else None
