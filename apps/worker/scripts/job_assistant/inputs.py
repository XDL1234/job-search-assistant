"""输入归一化，不改写用户提供的源文件。"""
import csv
import hashlib
import io
import json
from pathlib import Path, WindowsPath
from urllib.parse import urlsplit


def digest_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def path_key(path):
    """对比 Windows 普通与长路径写法，保留调用方原来的文件路径。"""
    path = Path(path)
    if isinstance(path, WindowsPath) and path.is_absolute():
        if len(path.drive) == 2 and path.drive.endswith(':'):
            return WindowsPath('\\\\?\\' + str(path))
        if path.drive.startswith('\\\\') and not path.drive.startswith(('\\\\?\\', '\\\\.\\')):
            return WindowsPath('\\\\?\\UNC\\' + str(path)[2:])
    return path


def load_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def web_url(value):
    value = str(value or '').strip()
    parsed = urlsplit(value)
    if (parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or any(c.isspace() or ord(c) < 32 for c in value)):
        raise ValueError('网站必须为无内嵌凭据的 HTTP/HTTPS 地址')
    if parsed.port == 0:
        raise ValueError('网站端口无效')
    return value


def inspect_company_table(path, columns=None, sheet=None):
    path = Path(path).resolve()
    aliases = {'company': ['公司名称', '公司', 'company'], 'url': ['申请网站', '投递官网', '网申地址', '招聘网址', 'url'],
               'job': ['岗位', '岗位名称', 'job']}
    if path.suffix.lower() not in ('.csv', '.xlsx'):
        raise ValueError('只支持 .xlsx 或 UTF-8 CSV；请先转换旧版 .xls')
    content = path.read_bytes()
    if path.suffix.lower() == '.csv':
        rows = list(csv.reader(io.StringIO(content.decode('utf-8-sig'), newline='')))
    else:
        from openpyxl import load_workbook
        from zipfile import BadZipFile
        try:
            book = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        except (BadZipFile, OSError) as error:
            raise ValueError('无法读取 Excel 文件，请确认是有效的 .xlsx 表格') from error
        try:
            if sheet and sheet not in book.sheetnames:
                raise ValueError('找不到指定的工作表')
            page = book[sheet] if sheet else book.active
            # 根据实际行读取，避免整行样式把每条记录扩成 16384 列。
            page.reset_dimensions()
            rows = list(page.values)
        finally:
            book.close()
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
        record['source_row'] = number
        problem = ''
        if not record['company']:
            problem = '缺少公司名称'
        elif not record['url']:
            problem = '缺少网址'
        else:
            try:
                record['url'] = web_url(record['url'])
            except ValueError:
                problem = '网址格式无效'
        key = (record['company'], record['url'], record.get('job', ''))
        if problem:
            result.append({**record, 'status': 'invalid', 'reason': problem})
        elif key in seen:
            result.append({**record, 'status': 'duplicate', 'reason': '公司、网址和岗位重复'})
        else:
            result.append({**record, 'status': 'valid', 'reason': ''})
            seen.add(key)
    return {'path': str(path), 'name': path.name, 'sha256': hashlib.sha256(content).hexdigest(), 'rows': result}


def valid_company_rows(inspection):
    return [{key: value for key, value in row.items() if key not in ('status', 'reason')}
            for row in inspection['rows'] if row['status'] == 'valid']


def read_companies(path, columns=None, sheet=None):
    inspection = inspect_company_table(path, columns, sheet)
    invalid = next((row for row in inspection['rows'] if row['status'] == 'invalid'), None)
    if invalid:
        raise ValueError(f"第 {invalid['source_row']} 行{invalid['reason']}")
    return valid_company_rows(inspection)


def validate_config(config):
    from .job_preferences import JOB_TYPES
    if 'employment_type' in config and (not isinstance(config['employment_type'], str) or config['employment_type'] not in JOB_TYPES):
        raise ValueError('求职类型不支持')
    if not config.get('channels') or not set(config['channels']) <= {'web', 'boss'}:
        raise ValueError('channels 必须包含 web 或 boss')
    if (not isinstance(config.get('target_roles'),list) or ('boss' in config['channels'] and not config['target_roles'])
            or any(not isinstance(role,str) or not role.strip() for role in config['target_roles'])):
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
