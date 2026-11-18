"""岗位偏好只保存页面标签，平台选择由浏览器实时核对。"""
import json
import unicodedata
import uuid


JOB_TYPES = {'internship': '实习', 'campus': '全职', 'full_time': '全职', 'part_time': '兼职', 'any': '不限'}
FILTER_LABELS = {
    'city': '城市', 'province': '省份', 'district': '工作区域', 'business_area': '商圈',
    'metro_line': '地铁线路', 'metro_station': '地铁站', 'query': '搜索关键词',
    'salary': '薪资待遇', 'experience': '工作经验', 'degree': '学历要求',
    'company_size': '公司规模', 'financing': '融资阶段',
}


def _label(value, field, limit=64):
    if not isinstance(value, str) or any(unicodedata.category(c) in ('Cc', 'Cf') for c in value):
        raise ValueError(f'{field}必须是单行文字')
    value = value.strip()
    if len(value) > limit:
        raise ValueError(f'{field}最多 {limit} 个字符')
    return value


def _labels(value, field, maximum, limit=64):
    if not isinstance(value, list) or len(value) > maximum:
        raise ValueError(f'{field}最多设置 {maximum} 项')
    return list(dict.fromkeys(label for raw in value if (label := _label(raw, field, limit))))


def normalize_filters(value, employment):
    if not isinstance(value, dict) or set(value) - set(FILTER_LABELS) - {'positions', 'industries'}:
        raise ValueError('投递筛选条件包含未支持的字段')
    filters = {key: label for key, name in FILTER_LABELS.items()
               if key in value and (label := _label(value[key], name, 100 if key == 'query' else 64))}
    if filters.get('business_area') == '不限':
        filters.pop('business_area')
    if filters.get('city') in ('不限', '全国', '全部'):
        raise ValueError('城市必须是一个具体城市，留空可沿用页面城市')
    if not filters.get('city') and any(key in filters for key in
                                       ('province', 'district', 'business_area', 'metro_line', 'metro_station')):
        raise ValueError('设置省份、工作区域或地铁前，请先填写城市')
    if filters.get('business_area') and filters.get('district') in (None, '不限'):
        raise ValueError('设置商圈前，请先填写所属工作区域')
    if filters.get('metro_line') == '不限':
        raise ValueError('地铁线路请填写具体线路；留空可沿用 BOSS 当前地铁条件')
    if filters.get('metro_station') and not filters.get('metro_line'):
        raise ValueError('设置地铁站前，请先填写所属地铁线路')
    if any(key in filters for key in ('metro_line', 'metro_station')) and any(
            key in filters for key in ('district', 'business_area')):
        raise ValueError('工作区域和地铁是两种独立的平台筛选，请选择其中一种')
    if employment == 'campus' and filters.get('experience') not in (None, '应届生'):
        raise ValueError('校招使用 BOSS 的「全职」和「应届生」，请清空其他工作经验要求')
    positions = value.get('positions', [])
    if not isinstance(positions, list) or len(positions) > 10:
        raise ValueError('职位类型最多设置 10 项')
    paths = []
    for path in positions:
        if not isinstance(path, list) or not 1 <= len(path) <= 3:
            raise ValueError('每项职位类型应填写 1 至 3 层名称')
        labels = [_label(raw, '职位类型') for raw in path]
        if not all(labels):
            raise ValueError('职位类型的层级名称不能为空')
        if labels not in paths:
            paths.append(labels)
    if paths:
        if ['不限'] in paths and len(paths) > 1:
            raise ValueError('「不限」不能和具体职位类型同时选择')
        filters['positions'] = paths
    industries = _labels(value.get('industries', []), '公司行业', 10)
    if industries:
        if '不限' in industries and len(industries) > 1:
            raise ValueError('「不限」不能和具体公司行业同时选择')
        filters['industries'] = industries
    return filters


def platform_values(filters, employment):
    values = {**normalize_filters(filters, employment), 'job_type': JOB_TYPES[employment]}
    if employment == 'campus':
        values['experience'] = '应届生'
    return values


def read_preferences(store):
    path = store.root / 'job-preferences.json'
    if not path.is_file():
        return None
    return _normalize_preferences(json.loads(path.read_text(encoding='utf-8')))


def _normalize_preferences(value):
    if not isinstance(value, dict) or set(value) - {'target_roles', 'employment_type', 'boss_filters'}:
        raise ValueError('投递岗位信息格式无效')
    employment = value.get('employment_type')
    if not isinstance(employment, str) or employment not in JOB_TYPES:
        raise ValueError('请选择求职类型')
    return {'target_roles': _labels(value.get('target_roles', []), '岗位方向', 20),
            'employment_type': employment,
            'boss_filters': normalize_filters(value.get('boss_filters', {}), employment)}


def save_preferences(store, value):
    saved = _normalize_preferences(value)
    temporary = store.root / ('job-preferences-' + uuid.uuid4().hex + '.tmp')
    temporary.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(store.root / 'job-preferences.json')
    return saved
