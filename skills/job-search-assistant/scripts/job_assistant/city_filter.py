"""只根据卡片的明确城市预筛；未知地点留给详情判断。"""
import re


REGIONS = set('河北 山西 辽宁 吉林 黑龙江 江苏 浙江 安徽 福建 江西 山东 河南 湖北 湖南 广东 海南 四川 贵州 云南 陕西 甘肃 青海 台湾 内蒙古 广西 西藏 宁夏 新疆'.split())


def city_match(value, cities):
    """返回同城 True、明确异城 False、地点不明 None；空范围保持原行为。"""
    if not cities:
        return True
    if not isinstance(value, str) or not value.strip():
        return None
    if re.search(r'全国|多地|多城|远程|居家|不限|待定|未定|未知|不详|其他|其它|待确认|面议|附近|海外|异地|remote|[/、,，;；\n]|或', value, re.I):
        return None
    head = re.split(r'[·•\s|｜]+', value.strip())[0]
    head = re.sub(r'^.*?(?:省|自治区)', '', head)
    explicit = re.match(r'^([\u4e00-\u9fff]{2,12}?市)', head)
    if explicit:
        city = explicit.group(1)[:-1]
    elif (head in REGIONS or head.endswith(('区', '县', '镇', '乡', '街道', '自治州', '盟'))
          or not re.fullmatch(r'[\u4e00-\u9fff]{2,12}', head)):
        return None
    else:
        city = head
    return city in {name.strip().removesuffix('市') for name in cities}
