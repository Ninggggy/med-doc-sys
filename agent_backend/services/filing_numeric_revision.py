"""OCR单元格人工修订视图。原始解析内容不可变，修订只覆盖新分析副本。"""
from copy import deepcopy


def numeric_check_confirmed(check):
    return check.get('status') == 'verified' or (
        check.get('status') == 'manual_confirmed'
        and isinstance(check.get('manual_value'), str) and bool(check['manual_value'].strip())
        and isinstance(check.get('manual_reason'), str) and bool(check['manual_reason'].strip()))


def content_attempt(meta):
    return next((attempt.get('attempted_at', '') for attempt in reversed(meta.get('parse_attempts', []))
                 if attempt.get('content_status') in ('success', 'partial')), '')


def active_confirmations(meta):
    saved = meta.get('numeric_revision') or {}
    return saved.get('items', []) if content_attempt(meta) and saved.get('source_attempt') == content_attempt(meta) else []


def resolved_numeric_issues(chunks, issues, meta):
    """仅消除与有效单元格确认完全对应的数值问题，不消除文字/结构诊断。"""
    items = active_confirmations(meta)
    if not items:
        return set()
    # 复用原接口对地址、原值及依据的校验；无效确认不能放行。
    try:
        apply_confirmations(chunks, items)
    except ValueError:
        return set()
    checks, tables = [], set()
    for item in items:
        ci, ti, si = map(int, item['key'].split(':'))
        cell = chunks[ci]['tables'][ti]['cells'][si]
        checks.extend((ci, check) for check in cell.get('numeric_verification', []))
        tables.add((ci, ti))
    def matches(ci, check):
        return bool(check.get('bbox_pdf')) and any(c == ci and candidate == check for c, candidate in checks)
    resolved = set()
    confirmed_keys = {item['key'] for item in items}
    for issue in issues:
        ci = issue.get('chunk_index')
        if type(ci) is not int or not 0 <= ci < len(chunks):
            continue
        if issue['code'] == 'numeric_uncertain' and type(issue.get('error_index')) is int:
            error = chunks[ci].get('errors', [])[issue['error_index']]
            candidates = error.get('numeric_verification') or []
            if candidates and all(numeric_check_confirmed(check) or matches(ci, check) for check in candidates):
                resolved.add(issue['issue_key'])
        ti = issue.get('table_index')
        if issue['code'] != 'table_review_required' or (ci, ti) not in tables:
            continue
        table = chunks[ci]['tables'][ti]
        reasons = table.get('review_reasons')
        if reasons != ['numeric_uncertain'] or table.get('unassigned_words') or table.get('continuation_reason'):
            continue
        uncertain = [f'{ci}:{ti}:{si}' for si, cell in enumerate(table.get('cells', []))
                     if cell.get('numeric_status') == 'numeric_uncertain' or any(
                         not numeric_check_confirmed(c) for c in cell.get('numeric_verification', []))]
        if uncertain and all(key in confirmed_keys for key in uncertain):
            resolved.add(issue['issue_key'])
    return resolved


def numeric_cells(chunks):
    result = []
    for ci, chunk in enumerate(chunks):
        for ti, table in enumerate(chunk.get('tables', [])):
            for si, cell in enumerate(table.get('cells', [])):
                checks = cell.get('numeric_verification') or []
                if cell.get('numeric_status') != 'numeric_uncertain' and not any(
                        check.get('status') == 'numeric_uncertain' for check in checks):
                    continue
                result.append({'key': f'{ci}:{ti}:{si}', 'page': table.get('page', chunk.get('page')),
                               'table': ti + 1, 'row': cell['row'] + 1, 'column': cell['column'] + 1,
                               'original_text': cell.get('text', ''), 'bbox_pdf': deepcopy(cell.get('bbox_pdf', [])),
                               'candidates': deepcopy(checks)})
    return result


def apply_confirmations(chunks, confirmations):
    """地址和原值必须同时对应，禁止负索引、任意字段写入及静默陈旧覆盖。"""
    if not isinstance(confirmations, list):
        raise ValueError('人工确认必须是列表')
    available = {item['key']: item for item in numeric_cells(chunks)}
    seen = set()
    for item in confirmations:
        if not isinstance(item, dict) or item.get('key') not in available or item['key'] in seen:
            raise ValueError('确认位置无效或重复，请重新读取解析结果')
        original = available[item['key']]
        if item.get('original_text') != original['original_text']:
            raise ValueError('原始内容已变化，请重新核对')
        if not isinstance(item.get('value'), str) or not item['value'].strip() or len(item['value']) > 2000:
            raise ValueError('请填写非空确认值，最长2000字符')
        if not isinstance(item.get('reason'), str) or not item['reason'].strip() or len(item['reason']) > 2000:
            raise ValueError('请填写核对依据，最长2000字符')
        seen.add(item['key'])
    result = deepcopy(chunks)
    changed = set()
    for item in confirmations:
        ci, ti, si = (int(value) for value in item['key'].split(':'))
        table = result[ci]['tables'][ti]
        cell = table['cells'][si]
        r, c = cell['row'], cell['column']
        if not (0 <= r < len(table.get('rows', [])) and 0 <= c < len(table['rows'][r])):
            raise ValueError('表格坐标无法对应，请重新核对原件')
        cell['manual_confirmation'] = deepcopy(item)
        cell['text'] = item['value'].strip()
        cell['numeric_status'] = 'manual_confirmed'
        for check in cell.get('numeric_verification', []):
            check['ocr_status'] = check.get('status')
            check['status'] = 'manual_confirmed'
            check['manual_value'] = cell['text']
            check['manual_reason'] = item['reason'].strip()
        table['rows'][r][c] = cell['text']
        changed.add((ci, ti))
    for ci, ti in changed:
        table = result[ci]['tables'][ti]
        rows = table['rows']
        def md(row):
            return '| ' + ' | '.join(str(v).replace('|', '\\|').replace('\n', ' ') for v in row) + ' |'
        previous = table.get('markdown', '')
        table['markdown'] = '\n'.join([md(rows[0]), md(['---'] * len(rows[0]))] + [md(row) for row in rows[1:]])
        table['columns'], table['data'] = rows[0], rows[1:]
        # 自动缓存来自未确认原值，不能覆盖修订后的按行解析。
        table.pop('structured_data', None)
        chunk = result[ci]
        if previous and previous in str(chunk.get('text', '')):
            if chunk['text'].count(previous) != 1:
                raise ValueError('正文存在重复表格块，无法唯一应用修订，请人工整理后重新上传')
            chunk['text'] = chunk['text'].replace(previous, table['markdown'], 1)
        # 不改变解析质量、原始图像、原始候选和诊断，也不伪称OCR重新通过。
    return result
