"""桌面投递记录的分页列表、日历与单条详情。"""
from datetime import date


# 历史记录有时缺少开始时间，沿用记录页按更新时间归日的行为。
_START = 'coalesce(julianday(started),julianday(updated))'
_SUMMARY = 'id,run_id,company,job,channel,status,started,updated'


def _day(value):
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError('日期须使用 YYYY-MM-DD')
    return parsed


def _date_filters(params):
    start, end = params.get('date_from'), params.get('date_to')
    clauses, values = [], []
    if start:
        clauses.append(f"{_START} >= julianday(?, 'utc')")
        values.append(_day(start).isoformat())
    if end:
        clauses.append(f"{_START} < julianday(?, '+1 day', 'utc')")
        values.append(_day(end).isoformat())
    if start and end and start > end:
        raise ValueError('开始日期不能晚于结束日期')
    return clauses, values


def list_records(store, params):
    page, size = params.get('page', 1), params.get('page_size', 50)
    if type(page) is not int or page < 1 or type(size) is not int or not 1 <= size <= 100:
        raise ValueError('页码须从 1 开始，每页 1 至 100 条')
    order = {'newest': 'DESC', 'oldest': 'ASC'}.get(params.get('sort', 'newest'))
    if not order:
        raise ValueError('未知记录排序方式')
    clauses, values = _date_filters(params)
    for column in ('channel', 'status'):
        if params.get(column):
            clauses.append(f'{column}=?')
            values.append(params[column])
    query = params.get('query', '')
    if not isinstance(query, str):
        raise ValueError('搜索词须为文本')
    search = query.strip().casefold()
    if search:
        # instr 将 %、_ 等也作为普通搜索词，casefold 支持非 ASCII 公司名。
        store.conn.create_function('search_fold', 1, str.casefold)
        clauses.append("instr(search_fold(coalesce(company,'') || ' ' || coalesce(job,'')),?) > 0")
        values.append(search)
    where = ' WHERE ' + ' AND '.join(clauses) if clauses else ''
    total = store.conn.execute('SELECT count(*) FROM attempts' + where, values).fetchone()[0]
    page = min(page, max(1, (total + size - 1) // size))
    records = store.conn.execute(f'SELECT {_SUMMARY} FROM attempts{where} '
                                 f'ORDER BY julianday(updated) {order}, id {order} LIMIT ? OFFSET ?',
                                 [*values, size, (page - 1) * size])
    return {'records': [dict(row) for row in records], 'total': total, 'page': page, 'page_size': size}


def record_days(store, params):
    start, end = _day(params['date_from']), _day(params['date_to'])
    if not 0 <= (end - start).days <= 61:
        raise ValueError('日历一次最多查询 62 天')
    clauses, values = _date_filters(params)
    days = store.conn.execute(f"SELECT DISTINCT date({_START}, 'localtime') AS day FROM attempts WHERE "
                              + ' AND '.join(clauses) + ' ORDER BY day', values)
    return {'days': [row[0] for row in days]}


def record_detail(store, params):
    identity = params['attempt_id']
    attempt = store.conn.execute(f'SELECT {_SUMMARY},reason,url FROM attempts WHERE id=?', (identity,)).fetchone()
    if not attempt:
        raise ValueError('这条投递记录已不存在')
    result = {'attempt': dict(attempt)}
    for table, columns in (
        ('fields', 'attempt_id,name,value,source,evidence_id'),
        ('actions', 'id,run_id,attempt_id,kind,status,reason,evidence_id,updated'),
        ('evidence', 'id,attempt_id,kind,created'),
    ):
        result[table] = [dict(row) for row in store.conn.execute(
            f'SELECT {columns} FROM {table} WHERE attempt_id=?', (identity,))]
    return result
