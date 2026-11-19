"""按可识别的简历章节与字段提取本机文本，保留原文定位。"""

import re


SECTIONS = {
    'education': ('教育经历', '教育背景', '教育信息', '学习经历', '学历信息', 'education'),
    'internships': ('实习经历', '实习经验', '实习实践', 'internship', 'internships', 'internship experience'),
    'work_experience': ('工作经历', '工作经验', '职业经历', '工作与实习经历', '工作/实习经历', '实习/工作经历',
                        'work experience', 'employment history', 'professional experience', 'experience'),
    'projects': ('项目经历', '项目经验', '项目实践', '代表项目', '科研项目', 'projects', 'project experience', 'selected projects'),
    'awards': ('获奖情况', '获奖经历', '获奖荣誉', '获奖证书', '荣誉奖励', '校内荣誉', '所获荣誉', '荣誉证书', 'awards', 'honors'),
    'languages': ('语言能力', '语言水平', '语言技能', '外语水平', 'languages', 'language skills'),
    '': ('个人简历', '个人信息', '基本信息', '联系方式', '求职意向', '个人简介', '自我评价', '个人评价',
         '自我介绍', '专业技能', '技能特长', '技能证书', '专业证书', '核心技能', '个人技能', '技能', '校园经历',
         '社会实践', '志愿经历', '社团经历', '兴趣爱好', '个人优势', '其他信息', 'skills', 'profile', 'summary', 'certifications'),
}
SECTION_KEYS = {name.replace(' ', '').lower(): section for section, names in SECTIONS.items() for name in names}
IDENTITY_FIELDS = {'education': 'school', 'internships': 'company', 'work_experience': 'company',
                   'projects': 'name', 'awards': 'name', 'languages': 'language'}
EMPLOYMENT_FIELDS = {
    'company': ('公司名称', '单位名称', '用人单位', '公司', '单位', 'company', 'employer'),
    'role': ('职位名称', '岗位名称', '职位', '岗位', '职务', 'role', 'title'),
    'description': ('工作内容', '工作职责', '实习内容', '实习职责', '主要工作', '职责', 'description', 'responsibilities'),
}
SECTION_FIELDS = {
    'education': {'school': ('学校名称', '学校', '院校', 'university', 'school'),
                  'degree': ('学历', '学位', 'degree'), 'college': ('学院名称', '学院', 'college'),
                  'major': ('专业名称', '专业', 'major')},
    'internships': EMPLOYMENT_FIELDS,
    'work_experience': EMPLOYMENT_FIELDS,
    'projects': {'name': ('项目名称', '项目', 'project', 'project name'),
                 'role': ('项目角色', '角色', '职务', '职位', '担任角色', 'role'),
                 'description': ('项目描述', '项目概述', '项目介绍', '项目简介', '项目职责', '工作内容', '职责', 'description')},
    'awards': {'name': ('获奖项目', '获奖名称', '获奖项', '奖项', 'award'),
               'date': ('获奖时间', '时间', 'date'), 'level': ('获奖级别', '级别', 'level'),
               'description': ('获奖描述', '说明', 'description')},
    'languages': {'language': ('语言类型', '语种', '语言', 'language'), 'proficiency': ('掌握程度', '熟练程度', 'proficiency'),
                  'listening_speaking': ('听说',), 'reading_writing': ('读写',),
                  'certificate': ('语言证书及成绩', '语言证书', '证书及成绩', '证书', '成绩', 'certificate')},
}
PERIOD_FIELDS = {'start_date': ('开始时间', '起始时间', '入学时间', '入职时间', 'start date'),
                 'end_date': ('结束时间', '毕业时间', '离职时间', 'end date')}
EXPERIENCE_SECTIONS = {'projects', 'work_experience', 'internships'}
PERIOD_SECTIONS = EXPERIENCE_SECTIONS | {'education'}
DEGREES = ('高中及以下', '中专', '大专', '本科', '硕士', '博士')
DEGREE = re.compile(r'高中及以下|中专|大专|专科|本科|学士|硕士|博士|\b(?:bachelor|master|phd)\b', re.I)
DEGREE_ALIASES = {'专科': '大专', '学士': '本科', 'bachelor': '本科', 'master': '硕士', 'phd': '博士'}
MONTH = r'(?:0?[1-9]|1[0-2])(?!\d)'
DAY = r'(?:0?[1-9]|[12]\d|3[01])(?!\d)'
DATE = rf'(?:19\d{{2}}|20\d{{2}})(?:\s*年(?:\s*{MONTH}\s*月(?:\s*{DAY}\s*日)?)?|[./-]\s*{MONTH}(?:[./-]\s*{DAY})?)?'
DATE_RANGE = re.compile(rf'(?<!\d)(?P<start>{DATE})(?!\d)\s*(?:[-–—~～－〜]+|至|到|\bto\b|(?=至今))\s*'
                        rf'(?P<end>{DATE}(?!\d)|至今|现在|目前|present\b|current\b)', re.I)
DATE_TOKEN = re.compile(rf'(?<!\d){DATE}(?!\d)')
ONGOING = re.compile(r'至今|现在|目前|present|current', re.I)
COLUMNS = re.compile(r'[|｜\t]+|[ \u3000]{2,}')
BULLET = re.compile(r'^(?:[•●▪◆◇·‣◦*]|[-–—](?=\s)|\d{1,2}[、.．)](?!\d)|[（(][一二三四五六七八九十0-9]{1,3}[）)])\s*')
SENTENCE = re.compile(r'[。！？；;]')
SCHOOL = re.compile(r'大学|学院|中学|学校|研究所|\buniversity\b|\bcollege\b', re.I)
ROLE = re.compile(r'(工程师|实习生|经理|主管|总监|助理|专员|研究员|开发人员|开发|负责人|组长)$')
EMPTY = re.compile(r'(?:暂无?|没有|不适用)(?:(?:相关)?(?:工作|实习|项目|获奖|语言)(?:经验|经历|情况)?)?[。.]?|[-—–/]+|n/?a|none', re.I)
AWARD = re.compile(r'奖|冠军|奖学金|荣誉|荣获|名次|银牌|铜牌|金牌|\baward\b|\bhonor\b', re.I)
LANGUAGE = re.compile(r'^(英语|英文|法语|日语|德语|韩语|俄语|西班牙语|汉语|中文|普通话|粤语|'
                      r'English|French|Japanese|German|Korean|Russian|Spanish|Chinese)(?=$|[^A-Za-z])', re.I)
CERTIFICATE = re.compile(r'CET[ -]?[46]|(?:大学)?英语[四六]级|IELTS|TOEFL|雅思|托福|JLPT|日语能力考试', re.I)


def _date(value):
    parts = re.findall(r'\d+', value)
    return '-'.join([parts[0], *(part.zfill(2) for part in parts[1:])]) if parts else ''


def _degree(value):
    match = DEGREE.search(value)
    if match:
        return DEGREE_ALIASES.get(match[0].lower(), match[0])
    return ''


def _heading(value):
    title, *rest = re.split('[:：]', BULLET.sub('', value), maxsplit=1)
    key = re.sub(r'\s+', '', title).strip('【】[]「」').lower()
    return (SECTION_KEYS[key], rest[0].strip() if rest else '') if key in SECTION_KEYS else (None, value)


def _labeled(section, value):
    name, separator, text = value.replace('：', ':', 1).partition(':')
    if not separator:
        return '', value
    name, text = name.strip().lower(), text.strip()
    fields = SECTION_FIELDS[section]
    if section in PERIOD_SECTIONS:
        fields = fields | PERIOD_FIELDS
    for field, labels in fields.items():
        if name in labels or (section == 'projects' and field == 'name' and re.fullmatch(r'项目[一二三四五六七八九十\d]+', name)):
            return field, text
    return '', value


def _put(row, field, text):
    if field in {'date', 'start_date', 'end_date'}:
        if field == 'end_date' and ONGOING.fullmatch(text):
            row['current'] = True
            return
        match = DATE_TOKEN.search(text)
        text = _date(match[0]) if match else ''
    if field == 'degree':
        text = _degree(text) or text
    if text:
        if field == 'description' and field in row:
            row[field] += '\n' + text
        else:
            row.setdefault(field, text)


def _identity_columns(section, row, columns):
    """按标题行中可区分的列匹配字段，内容行使用原文。"""
    if section == 'education':
        for column in columns:
            degree = _degree(column)
            if SCHOOL.search(column) and 'school' not in row:
                _put(row, 'school', column)
            elif degree:
                _put(row, 'degree', degree)
            elif column.endswith('学院'):
                _put(row, 'college', column)
            elif 'school' in row:
                _put(row, 'major', column)
        return
    _put(row, IDENTITY_FIELDS[section], columns[0])
    if len(columns) > 1:
        _put(row, 'role', columns[1])
    elif section != 'projects':
        name, _, role = columns[0].rpartition(' ')
        if name and ROLE.search(role):
            row[IDENTITY_FIELDS[section]] = name.rstrip()
            _put(row, 'role', role)


def _add_list_entry(section, row, text):
    if section == 'awards' and AWARD.search(text):
        match = DATE_TOKEN.search(text)
        if match:
            _put(row, 'date', match[0])
            text = (text[:match.start()] + text[match.end():]).strip(' \t|｜-—–：:、')
        _put(row, 'name', text)
        return True
    if section == 'languages':
        language = LANGUAGE.match(text)
        certificate = CERTIFICATE.search(text)
        if language or certificate:
            if language:
                _put(row, 'language', language[0])
                text = text[language.end():].strip(' \t|｜：:、')
            elif certificate[0].upper().startswith('JLPT') or '日语' in certificate[0]:
                _put(row, 'language', '日语')
            else:
                _put(row, 'language', '英语')
            _put(row, 'certificate' if certificate else 'proficiency', text)
            return True
    return False


def _date_only(text):
    return bool(DATE_RANGE.fullmatch(text.strip(' \t|｜')))


def _parse_section(section, lines):
    identity = IDENTITY_FIELDS[section]
    row, traces, items = {}, [], []

    def finish():
        nonlocal row, traces
        if row.get(identity):
            items.append((row, traces))
        row, traces = {}, []

    # shortcut: 无明确标题、日期或字段标签的多段经历可能合为一条，按实际简历样式补充可区分的边界。
    for index, (original, locator) in enumerate(lines):
        text = BULLET.sub('', original).strip()
        if not text or EMPTY.fullmatch(text):
            continue
        columns = [part.strip() for part in COLUMNS.split(text) if part.strip()]
        if not columns:
            continue
        field, value = _labeled(section, text)
        if field:
            if field == identity and identity in row:
                finish()
            if field != 'description' and len(columns) > 1 and all(_labeled(section, part)[0] for part in columns):
                for part in columns:
                    _put(row, *_labeled(section, part))
            else:
                _put(row, field, value)
        elif section in PERIOD_SECTIONS:
            period = DATE_RANGE.search(text)
            following = lines[index + 1][0] if index + 1 < len(lines) else ''
            short_heading = len(text) <= 100 and not SENTENCE.search(text)
            dated_heading = period and (period.start() == 0 or (short_heading and original == text))
            has_identity = identity in row
            if dated_heading:
                title = (text[:period.start()] + '  ' + text[period.end():]).strip(' \t|｜')
                if has_identity and ('start_date' in row or 'description' in row or title):
                    finish()
                _put(row, 'start_date', period['start'])
                _put(row, 'end_date', period['end'])
                if title:
                    _identity_columns(section, row, [part.strip() for part in COLUMNS.split(title) if part.strip()])
            elif short_heading and (_date_only(following)
                                    or (section == 'education' and SCHOOL.search(columns[0])
                                        and ('school' not in row or not columns[0].endswith('学院')))
                                    or (section in EXPERIENCE_SECTIONS and len(columns) > 1 and ROLE.search(columns[1])
                                        and original == text)):
                if has_identity:
                    finish()
                _identity_columns(section, row, columns)
            elif section == 'education' and DEGREE.fullmatch(text):
                _put(row, 'degree', text)
            elif section in EXPERIENCE_SECTIONS and short_heading and original == text and not has_identity:
                _identity_columns(section, row, columns)
            elif (section in EXPERIENCE_SECTIONS and short_heading and original == text and has_identity
                  and 'role' not in row and 'description' not in row and ROLE.search(text)):
                _put(row, 'role', text)
            elif section in EXPERIENCE_SECTIONS and has_identity:
                _put(row, 'description', original)
            else:
                continue
        else:
            new_entry = {}
            if _add_list_entry(section, new_entry, text):
                if identity in row:
                    finish()
                row.update(new_entry)
            elif section == 'awards' and identity in row:
                _put(row, 'description', original)
            else:
                continue
        traces.append((original, locator))
    finish()
    return items


def _provenance(source_file, traces):
    return {'source_file': str(source_file), 'locator': '；'.join(dict.fromkeys(locator for _, locator in traces)),
            'original': '\n'.join(text for text, _ in traces)}


def parse_resume(blocks, source_file):
    """返回已匹配的结构化资料与出处，未知章节和未出现的值留空。"""
    groups, locations = {}, {}
    section, lines = '', []

    def save_section():
        if not section:
            return
        for row, traces in _parse_section(section, lines):
            is_internship = section == 'work_experience' and re.search(r'实习|\bintern(?:ship)?\b', row.get('role', ''), re.I)
            group = 'internships' if is_internship else section
            groups.setdefault(group, []).append(row)
            locations.setdefault(group, []).extend(traces)

    for block in blocks:
        for line in block['text'].splitlines():
            text = line.strip()
            if not text:
                continue
            heading, rest = _heading(text)
            if heading is not None:
                save_section()
                section, lines = heading, []
            if section and rest:
                lines.append((rest, block['locator']))
    save_section()
    sources = {field: _provenance(source_file, traces) for field, traces in locations.items()}
    degrees = [item.get('degree') for item in groups.get('education', [])]
    if known := [degree for degree in degrees if degree in DEGREES]:
        groups['highest_degree'] = max(known, key=DEGREES.index)
        sources['highest_degree'] = sources['education']
    return groups, sources
