"""输入归一化，不改写用户提供的源文件。"""
import csv
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit


def digest_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def web_url(value):
    value = str(value or '').strip()
    parsed = urlsplit(value)
    if parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('网站必须为无内嵌凭据的 HTTP/HTTPS 地址')
    return value


def read_companies(path, columns=None, sheet=None):
    path = Path(path)
    aliases = {'company': ['公司名称', '公司', 'company'], 'url': ['申请网站', '网申地址', '招聘网址', 'url'],
               'job': ['岗位', '岗位名称', 'job']}
    if path.suffix.lower() == '.csv':
        with path.open(encoding='utf-8-sig', newline='') as stream:
            rows = list(csv.reader(stream))
    elif path.suffix.lower() == '.xlsx':
        from openpyxl import load_workbook
        book = load_workbook(path, read_only=True, data_only=True)
        try:
            page = book[sheet] if sheet else book.active
            rows = list(page.values)
        finally:
            book.close()
    else:
        raise ValueError('只支持 .xlsx 或 UTF-8 CSV；请先转换旧版 .xls')
    if not rows:
        raise ValueError('公司表格为空')
    headers = [str(v or '').strip() for v in rows[0]]
    indices = {}
    for field, names in aliases.items():
        candidates = [(columns or {})[field]] if field in (columns or {}) else names
        matches = [i for i, header in enumerate(headers) if header in candidates]
        if len(matches) > 1:
            raise ValueError(f'{field} 列不唯一，请指定列映射')
        if matches:
            indices[field] = matches[0]
        elif field != 'job':
            raise ValueError(f'缺少 {field} 列，请提供列映射')
    result, seen = [], set()
    for number, row in enumerate(rows[1:], 2):
        if not any(v is not None and str(v).strip() for v in row):
            continue
        record = {key: str(row[i] or '').strip() if i < len(row) else '' for key, i in indices.items()}
        if not record['company']:
            raise ValueError(f'第 {number} 行公司名称为空')
        record['url'] = web_url(record['url'])
        key = (record['company'], record['url'], record.get('job', ''))
        if key not in seen:
            result.append({**record, 'source_row': number})
            seen.add(key)
    return result


def validate_config(config):
    if not config.get('channels') or not set(config['channels']) <= {'web', 'boss'}:
        raise ValueError('channels 必须包含 web 或 boss')
    if not config.get('target_roles'):
        raise ValueError('必须明确目标岗位名称及方向')
    if 'boss' in config['channels']:
        if config.get('boss_mode') not in ('filtered', 'screened'):
            raise ValueError('请选择 filtered 或 screened 投递模式')
        if type(config.get('max_contacts')) is not int or config['max_contacts'] < 1:
            raise ValueError('必须设置正整数沟通数量上限')
    if config.get('unknown_reply_mode', 'human') not in ('human', 'ai'):
        raise ValueError('unknown_reply_mode 必须为 human 或 ai')
    if type(config.get('poll_seconds', 60)) is not int or config.get('poll_seconds', 60) < 15:
        raise ValueError('消息检查间隔不得小于 15 秒')
    return config
