"""公司官网简历草稿的本机字段匹配。网页标签不是指令。"""

from collections import Counter
from datetime import date
import math
from pathlib import Path
import re
import unicodedata

from .profiles import MAX_FILE_SIZE, digest_file, get_profile


def _normalize(value):
    if not isinstance(value, str):
        return ''
    return re.sub(r'[\s*＊:：]+', '', unicodedata.normalize('NFKC', value)).casefold()


def _aliases(by_key):
    return {_normalize(label): source for source, labels in by_key.items() for label in labels.split('|')}


BASIC = _aliases({
    'name': '姓名|中文姓名|真实姓名|full name',
    'english_name': '英文姓名|英文名|English name',
    'contact.email': '邮箱|电子邮箱|邮件地址|电子邮件|电子邮件地址|联系邮箱|email',
    'contact.phone': '手机号码|手机号|手机|联系电话|联系手机|移动电话|phone|phone number|mobile',
    'phone_country_code': '区号|手机号区号|电话区号|区号代码|country code',
    'gender': '性别|gender',
    'birth_date': '出生日期|出生年月|birthday|date of birth',
    'nationality_region': '国籍|国家/地区|nationality',
    'ethnicity': '民族',
    'political_status': '政治面貌',
    'country': '当前所在国家|现居国家|现居住国家|所在国家/地区|current country',
    'province': '当前所在省份|现居省份|现居住省份|current province',
    'city': '当前所在城市|现居城市|现居住城市|current city',
    'home_city': '家庭所在城市|家庭所在地',
    'school_city': '学校所在城市|学校所在地',
    'hometown': '籍贯',
    'household_location': '户口所在地|户籍所在地',
    'address': '通讯地址|通信地址|联系地址|详细地址',
    'postal_code': '邮政编码|邮编|postal code',
    'highest_degree': '最高学历|最高教育程度|highest education|highest degree',
    'graduation_date': '毕业时间|毕业日期|预计毕业时间|graduation date',
    'target_role': '求职方向|求职意向|意向岗位',
    'preferred_city': '期望工作城市|意向工作城市|意向城市',
    'availability_date': '可到岗时间|可到岗日期|最早到岗时间|到岗时间',
    'self_evaluation': '自我评价|自我介绍|个人评价|个人简介',
    'notes': '附加说明|补充说明|网申补充说明|其它|其他|自我评价或其它补充信息|自我评价或其他补充信息',
    'competition_summary': '大赛经历|竞赛经历',
    'awards_summary': '奖励与荣誉|奖励和荣誉',
    'recruitment_source': '招聘信息来源|招聘渠道来源|获知招聘信息渠道',
})

SECTION_ALIASES = _aliases({
    'education': '教育经历|教育背景|教育信息|教育经验|教育情况|education',
    'internships': '实习经历|实习经验|实习实践|internship experience|internships',
    'work_experience': '工作经历|工作经验|工作信息|work experience|employment history',
    'experience': '工作/实习经历|实习/工作经历|工作及实习经历|实习及工作经历|工作经历/实习经历',
    'mixed_experience': '实习/项目经历|实习及项目经历|实习、项目经历',
    'projects': '项目经历|项目经验|项目实践|project experience|projects',
    'awards': '获奖情况|荣誉奖项|获奖经历|奖项荣誉|荣誉奖励|awards|honors',
    'languages': '语言能力|语言水平|语言技能|languages|language skills',
    'certificates': '证书|专业证书|证书资质|技能证书|资格证书|certificates|certifications',
    'activities': '校园经历|校内实践|社会实践|校园实践|学生工作|社会活动|activities',
})

PERIOD = {
    'start_date': '开始时间|起始时间|起始日期|开始日期|入学时间|入学日期|入职时间|入职日期|start date',
    'end_date': '结束时间|结束日期|毕业时间|毕业日期|离职时间|离职日期|end date',
}
EMPLOYMENT = {
    **PERIOD,
    'company': '单位名称|企业名称|公司|公司名称|公司或组织名称|用人单位|实习单位|工作单位|company|employer',
    'role': '职位名称|岗位名称|岗位|职位|职位或职责|实习岗位|实习职位|工作岗位|职务|role|position|job title',
    'department': '部门|所在部门|工作部门|实习部门|department',
    'description': '工作职责|岗位职责|工作内容|实习内容|实习职责|经历描述|工作描述|实习描述|description',
}
GROUPS = {group: _aliases(mapping) for group, mapping in {
    'education': {**PERIOD, 'school': '学校名称|学校全称|学校|院校名称|院校|就读院校|大学|school|university',
                  'college': '学院名称|学院|院系|学院/院系|所在院系|department',
                  'major': '专业名称|所学专业|就读专业|专业|major', 'degree': '学历|学历层次|教育程度|education level',
                  'major_category': '专业类别|专业分类', 'laboratory': '实验室|实验室名称',
                  'academic_degree': '学位|学位名称|degree', 'study_type': '培养方式|学习方式|教育形式|学历类型|是否全日制',
                  'gpa': 'GPA|GPA成绩|平均学分绩点|绩点', 'ranking': '专业排名|成绩排名|班级排名',
                  'national_scholarship': '是否获得国家奖学金|国家奖学金', 'exchange_student': '该学历是否为交换生|是否为交换生|是否交换生',
                  'research_direction': '研究方向', 'advisor': '导师|导师姓名', 'courses': '主修课程|主要课程',
                  'country': '学校所在国家|留学国家|留学国家/地区', 'city': '学校所在城市|就读城市'},
    'internships': EMPLOYMENT, 'work_experience': EMPLOYMENT,
    'projects': {**PERIOD, 'name': '项目名称|项目|project name', 'role': '项目角色|角色|担任角色|职务|职位|职位或职责',
                 'organization': '公司或组织名称|组织名称|项目单位',
                 'description': '项目描述|项目介绍|项目内容|项目简介|项目经历|工作描述|description',
                 'responsibilities': '项目职责|职责', 'url': '项目链接|项目地址|project link'},
    'awards': {'name': '获奖项|奖项|奖项名称|获奖名称|获奖项目|award name', 'date': '获奖时间|获奖日期|授予时间',
               'level': '获奖级别|获奖等级|奖项级别|奖项等级|奖项类别|级别|等级',
               'description': '获奖描述|奖项描述|奖项说明|描述', 'issuer': '颁发机构|颁奖单位|奖励机构'},
    'languages': {'language': '语言类型|语种|语言|language', 'proficiency': '掌握程度|熟练程度|proficiency',
                  'listening_speaking': '听说|听说能力', 'reading_writing': '读写|读写能力',
                  'certificate': '语言证书及成绩|语言证书|证书及成绩|证书', 'score': '语言成绩|考试成绩|成绩|score'},
    'certificates': {'name': '证书名称|证书|资格证书名称|certificate name', 'date': '获得时间|获得日期|颁发日期',
                     'issuer': '颁发机构|发证机构|发证单位', 'level': '证书级别|证书等级', 'description': '证书描述|证书说明'},
    'activities': {**PERIOD, 'name': '活动名称|实践名称|组织名称|社团名称|校园经历名称',
                   'role': '职务|担任职务|角色', 'description': '活动描述|实践内容|经历描述|工作内容'},
}.items()}

ANCHORS = {'education': ('school',), 'internships': ('company',), 'work_experience': ('company',),
           'projects': ('name', 'organization'), 'awards': ('name',), 'languages': ('language',),
           'certificates': ('name',), 'activities': ('name',)}
DISTINCT_CHOICES = [
    ('本科', '大学本科', 'bachelor', 'bachelor’s degree'),
    ('专科', '大专', '大学专科'),
    ('硕士', '硕士研究生', 'master'),
    ('博士', '博士研究生', 'doctorate', 'phd'),
    ('男', 'male'), ('女', 'female'),
    ('中国大陆', '中国内地'),
]
CHOICES = {_normalize(label): _normalize(group[0]) for group in DISTINCT_CHOICES for label in group}
FORBIDDEN = re.compile(r'验证(?:码|用)|确认邮箱|确认手机|登录|账号|密码|银行卡|账户|身份证|证件|护照|token|captcha|otp|password|passport', re.I)
FILLABLE = {'text', 'tel', 'email', 'url', 'number', 'date', 'month', 'textarea', 'select-one'}
ATTACHMENT_LABELS = _aliases({
    'resume': '简历|简历附件|个人简历|上传简历|resume|cv',
    'photo_path': '证件照|证件照片|个人照片|个人头像|photo|portrait',
    'portfolio_path': '作品集|作品集附件|portfolio',
    'other_attachment_path': '附件|其他附件|其它附件|补充附件|other attachment',
})
ATTACHMENT_TYPES = {
    '.pdf': 'application/pdf',
    '.doc': 'application/msword',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
    '.zip': 'application/zip', '.rar': 'application/vnd.rar',
}
ATTACHMENT_EXTENSIONS = {
    'resume': {'.pdf', '.docx'},
    'photo_path': {'.jpg', '.jpeg', '.png'},
    'portfolio_path': ATTACHMENT_TYPES.keys(),
    'other_attachment_path': ATTACHMENT_TYPES.keys(),
}


def _profile_rows(profile, key):
    value = profile.get(key)
    if isinstance(value, dict):
        return [value]
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _source_parts(field, profile):
    label, section = _normalize(field['label']), _normalize(field.get('section'))
    if field['type'] == 'file':
        if label == '附件' and section not in ('', '附件', '网申附件', '其他附件', '其它附件'):
            return None, None
        return None, ATTACHMENT_LABELS.get(label)
    group = SECTION_ALIASES.get(section)
    basic = BASIC.get(label)
    if (group == 'mixed_experience' and field['type'] == 'textarea'
            and basic in ('competition_summary', 'awards_summary')):
        return None, basic
    if group in ('experience', 'mixed_experience'):
        candidates = ('internships', 'work_experience' if group == 'experience' else 'projects')
        groups = [key for key in candidates if _profile_rows(profile, key)]
        if len(groups) == 1:
            group = groups[0]
        else:
            return (group if any(label in GROUPS[key] for key in candidates) else None), None
    if group:
        return group, GROUPS.get(group, {}).get(label)
    return None, basic


def _value_at(profile, source):
    value = profile
    for part in source.split('.'):
        if isinstance(value, dict):
            value = value.get(part)
        else:
            return None
    return value


def _date_value(value, kind):
    match = re.fullmatch(r'(\d{4})[./年-](\d{1,2})(?:(?:[./月-])(\d{1,2}))?[月日]?', value)
    if not match or kind == 'date' and match[3] is None:
        return None
    try:
        iso = date(int(match[1]), int(match[2]), int(match[3] or 1)).isoformat()
    except ValueError:
        return None
    return iso if kind == 'date' else iso[:7]


def _match_choice(a):
    key = _normalize(a)
    return CHOICES.get(key, key)


def _input_value(value, field):
    if isinstance(value, str):
        value = value.strip()
    elif type(value) is int or type(value) is float and math.isfinite(value):
        value = str(value)
    else:
        return None
    if not value or len(value) > 8192:
        return None
    kind = field['type']
    if kind == 'select-one':
        candidates = [row['index'] for row in field['options'] if _match_choice(row['label']) == _match_choice(value)]
        return candidates[0] if len(candidates) == 1 else None
    if kind in ('date', 'month'):
        return _date_value(value, kind)
    if kind == 'number' and not re.fullmatch(r'-?[0-9]+(?:\.[0-9]+)?', value):
        return None
    return value if kind == 'textarea' or all(char not in value for char in '\r\n\t') else None


def _local_attachment_path(file):
    if not file.is_absolute():
        return False
    if file.drive:
        return re.fullmatch(r'[A-Za-z]:', file.drive.removeprefix('\\\\?\\')) is not None
    return not str(file).startswith(('\\\\', '//'))


def _attachment_value(value, source, accept):
    if not isinstance(value, str):
        return None
    try:
        file = Path(value)
        if not _local_attachment_path(file):
            return None
        file = file.resolve(strict=True)
        extension = file.suffix.lower()
        if (not _local_attachment_path(file) or extension not in ATTACHMENT_EXTENSIONS[source] or
                not file.is_file() or not 0 < file.stat().st_size <= MAX_FILE_SIZE):
            return None
        mime = ATTACHMENT_TYPES[extension]
        accepted = {item.strip().lower() for item in accept.split(',') if item.strip()}
        if accepted and not accepted.intersection({extension, mime, mime.split('/')[0] + '/*', '*/*'}):
            return None
        return {'file': str(file), 'sha256': digest_file(file)}
    except (OSError, RuntimeError, ValueError):
        return None


def _validate_fields(fields):
    if not isinstance(fields, list) or len(fields) > 240:
        raise ValueError('网页表单字段列表无效')
    ids = set()
    for field in fields:
        if (not isinstance(field, dict) or not isinstance(field.get('id'), str) or
                not re.fullmatch(r'field-[0-9]{1,3}', field['id']) or field['id'] in ids or
                not all(isinstance(field.get(key), str) and len(field[key]) <= 200
                        for key in ('label', 'section', 'row', 'type')) or
                type(field.get('occupied')) is not bool or type(field.get('required')) is not bool):
            raise ValueError('网页表单字段结构无效')
        options = field.get('options')
        if not isinstance(options, list) or len(options) > 200 or any(
                not isinstance(row, dict) or type(row.get('index')) is not int or not 0 <= row['index'] < 2000 or
                not isinstance(row.get('label'), str) or len(row['label']) > 200 for row in options):
            raise ValueError('网页表单选项结构无效')
        if not isinstance(field.get('accept', ''), str) or len(field.get('accept', '')) > 500:
            raise ValueError('网页附件格式无效')
        ids.add(field['id'])


def plan_autofill(profile, fields, resume_path='', resume_sha256=''):
    _validate_fields(fields)
    bindings = [_source_parts(field, profile) for field in fields]
    repeated = Counter((group, key, field['row'] if group else '')
                       for field, (group, key) in zip(fields, bindings) if key)
    anchors = {}
    for field, (group, key) in zip(fields, bindings):
        if key in ANCHORS.get(group, ()):
            anchors.setdefault(group, []).append((field, key))
    eligible_rows = {}
    for group, entries in anchors.items():
        rows = {field['row'] for field, _ in entries}
        if (len(rows) == 1 and len({key for _, key in entries}) == len(entries)
                and not any(field['occupied'] for field, _ in entries)
                and len(_profile_rows(profile, group)) == 1):
            eligible_rows[group] = rows.pop()
    planned, previews = [], []
    for field, (group, key) in zip(fields, bindings):
        source = f'{group}.0.{key}' if group and key else key
        preview = {name: field[name] for name in ('id', 'label', 'section', 'required')}
        value = None
        attachment = None
        if field['occupied']:
            status, reason = 'existing', '保留网页已有内容'
        elif field['type'] == 'file':
            raw = resume_path if key == 'resume' else profile.get(key) if key else None
            if not key or repeated[(group, key, '')] != 1:
                status, reason = 'manual', '无法唯一对应已保存的附件'
            elif not raw:
                status, reason = 'missing', '尚未保存此附件'
            elif attachment := _attachment_value(raw, key, field.get('accept', '')):
                if key == 'resume' and resume_sha256 and resume_sha256 != attachment['sha256']:
                    status, reason = 'manual', '本机简历已变更，请重新导入'
                else:
                    status, reason = 'ready', '将选用 ' + Path(attachment['file']).name
                    if key in (profile.get('sources') or {}):
                        preview['requires_selection'] = True
            else:
                status, reason = 'manual', '本机文件不可用或格式不符合网页要求'
        elif field['type'] not in FILLABLE or FORBIDDEN.search(field['label']):
            status, reason = 'manual', '需要在网页上操作此控件'
        elif group in ('experience', 'mixed_experience'):
            status, reason = 'manual', '需要核对混合经历的类别'
        elif not key:
            status, reason = 'unmapped', '没有可唯一对应的资料字段'
        elif repeated[(group, key, field['row'] if group else '')] != 1:
            status, reason = 'manual', '页面有多个同类字段，须核对位置'
        elif group and eligible_rows.get(group) != field['row']:
            status, reason = 'manual', '需要核对履历所属条目'
        else:
            raw = _profile_rows(profile, group)[0].get(key) if group else _value_at(profile, key)
            if raw is None or isinstance(raw, str) and not raw.strip():
                status, reason = 'missing', '个人资料尚未填写'
            else:
                value = _input_value(raw, field)
                status, reason = ('ready', '') if value is not None else ('manual', '网页格式或选项需要核对')
        previews.append({**preview, 'source': source, 'status': status, 'reason': reason})
        if status == 'ready':
            planned.append({'id': field['id'], 'source': source, **(attachment or {'value': value})})
    return {'fields': previews, 'operations': planned}


def prepare_web_autofill(store, fields):
    saved = get_profile(store) or {}
    return {'profile_version': saved.get('version_id', ''),
            **plan_autofill(saved.get('profile') or {}, fields, saved.get('resume_path', ''), saved.get('resume_sha256', ''))}
