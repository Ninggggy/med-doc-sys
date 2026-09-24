"""解析就绪的纯数据检查；不把任务完成或旧正文存在当成解析完整。"""
from copy import deepcopy


def effective_parse_ready(source):
    """仅供服务端已校验来源的业务输入使用；不替代启动前的逐项检查。"""
    state = (source.get('latest_attempt') or {}).get('content_status') or source.get('parse_status') or source.get('content_status')
    resolution = source.get('parse_resolution') or {}
    if resolution.get('unresolved_count', 0):
        return False
    return state == 'success' or (state == 'partial' and resolution.get('manually_reviewed') is True
        and type(resolution.get('revision')) is int and resolution['revision'] > 0
        and type(resolution.get('unresolved_count')) is int and resolution['unresolved_count'] == 0)


def source_issues(source, kind, chunks=()):
    attempt = source.get('latest_attempt') or {}
    state = attempt.get('content_status') or source.get('content_status') or source.get('parse_status') or 'not_parsed'
    diagnostics = attempt.get('parse_diagnostics') or source.get('parse_diagnostics') or {}
    identity = {key: attempt.get(key, '') for key in ('task_id', 'attempted_at', 'source_file_id', 'source_doc_id')}
    base = {'source_kind': kind, 'doc_id': source.get('doc_id') or source.get('original_file_id') or '',
            'file_name': source.get('file_name') or source.get('title') or ('申请表' if kind == 'application_form' else ''),
            'source_attempt': identity, 'parse_status': state}
    issues = []

    def add(key, code, message, page=None, bbox=None, **extra):
        issues.append({**base, 'issue_key': key, 'code': code, 'message': message,
                       'page': page, 'bbox_pdf': deepcopy(bbox or []), **extra})

    # 失败或正在处理的新尝试不可通过确认此前有效正文来放行。
    if state not in ('success', 'partial'):
        add('source_state', 'parse_' + str(state), '资料尚未完整解析，请完成或重试解析。')
        return issues
    # 页级诊断与汇总诊断可能是同一证据的两份副本，但有页级错误
    # 不代表汇总中的其他页/区域已经覆盖；只能逐条排除确切的副本。
    def evidence(error, page):
        detail = {k: deepcopy(v) for k, v in error.items()
                  if k not in ('page', 'message', 'reason', 'coordinate_unit')}
        # outcome为未声明阶段的页错误补上page和空异常类型，属于同一证据。
        detail.setdefault('stage', 'page')
        detail.setdefault('exception_type', '')
        if error.get('code') in ('field_region_crossing', 'field_region_unassigned') and error.get('bbox_pdf'):
            # outcome的汇总保留PDF坐标/原文/候选字段/来源，但不复制像素坐标
            # 和派生needs_review；不能将同一字段问题的这两份表达重复阻塞。
            for key in ('bbox_px', 'dpi', 'needs_review'):
                detail.pop(key, None)
        return (page, error.get('reason') or error.get('message') or '', detail)

    chunk_evidence = []
    for ci, chunk in enumerate(chunks or []):
        for ei, error in enumerate(chunk.get('errors') or []):
            chunk_evidence.append(evidence(error, chunk.get('page')))
            # 核对区域可能大于已恢复子表，不能要求二者坐标完全相等。
            # 仅使用该页唯一匹配的服务端表ID预填，不根据相邻位置猜表。
            table_matches = [ti for ti, table in enumerate(chunk.get('tables') or [])
                             if error.get('table_id') and table.get('id') == error['table_id']]
            table_link = ({'table_index': table_matches[0]} if len(table_matches) == 1
                          and error.get('code') == 'table_structure_unresolved' else {})
            add(f'chunk:{ci}:error:{ei}', error.get('code') or error.get('stage') or 'parse_unknown',
                error.get('reason') or '页面存在未解决的解析问题', chunk.get('page'), error.get('bbox_pdf'),
                chunk_index=ci, error_index=ei, **table_link)
        if chunk.get('status') in ('failed', 'partial') and not chunk.get('errors'):
            add(f'chunk:{ci}:state', 'page_incomplete', '页面未完整解析，需核对原页。', chunk.get('page'),
                chunk_index=ci)
        for ti, table in enumerate(chunk.get('tables') or []):
            if table.get('needs_review') or table.get('unassigned_words'):
                add(f'chunk:{ci}:table:{ti}', 'table_review_required', '表格结构或内容需要核对。',
                    table.get('page', chunk.get('page')), table.get('bbox_pdf'), chunk_index=ci, table_index=ti)
    for ei, error in enumerate(diagnostics.get('errors') or []):
        if evidence(error, error.get('page')) in chunk_evidence:
            continue
        add(f'diagnostic:{ei}', error.get('code') or error.get('stage') or 'parse_unknown',
            error.get('message') or error.get('reason') or '资料存在未解决的解析问题',
            error.get('page'), error.get('bbox_pdf'), diagnostic_index=ei)
    for field in ('missing_pages', 'failed_pages'):
        for page in diagnostics.get(field) or []:
            # missing 是独立的缺页证据；failed 则可由该页具体错误表达。
            if any(i['page'] == page and (field == 'failed_pages' or i['code'] == 'page_incomplete')
                   for i in issues):
                continue
            add(f'{field}:{page}', 'page_incomplete', '页面未完整解析，需核对或重新解析原页。', page)
    # 历史记录可能只有状态/页码；没有细项也必须保持阻塞。
    if not issues and (state == 'partial' or diagnostics.get('status') in ('partial', 'failed')
                       or diagnostics.get('failed_pages') or diagnostics.get('missing_pages')):
        add('source_incomplete', 'parse_incomplete', '解析不完整；尚无足够明细证明已恢复，请重试解析。')
    from agent.agent_backend.services.filing_parse_resolution import assembly_table_scope, quality_confirmation_available, numeric_text_review_available
    for issue in issues:
        ci = issue.get('chunk_index')
        if (kind == 'application_form' and type(ci) is int and 0 <= ci < len(chunks)
                and type(chunks[ci].get('page')) is int and chunks[ci]['page'] > 1
                and issue['code'] in ('ocr_quality', 'ocr_coverage', 'ocr_empty', 'text_assembly_unresolved', 'table_structure_unresolved', 'table_review_required', 'numeric_uncertain')):
            from agent.agent_backend.utils.parser.drug_supplement_pdf_parser import ITEM_TITLES
            issue['continuation_field_options'] = [{'item_no': n, 'title': title} for n, title in ITEM_TITLES.items()]
        if issue['code'] == 'numeric_uncertain':
            issue['numeric_text_review_allowed'] = (type(ci) is int and 0 <= ci < len(chunks)
                                                   and numeric_text_review_available(chunks[ci], issue))
        if issue['code'] == 'ocr_quality':
            issue['confirm_allowed'] = (type(ci) is int and 0 <= ci < len(chunks)
                                        and quality_confirmation_available(chunks[ci], issue))
        if kind == 'application_form' and issue['code'] in ('field_region_crossing', 'field_region_unassigned') and type(ci) is int:
            from agent.agent_backend.utils.parser.drug_supplement_pdf_parser import ITEM_TITLES
            error = chunks[ci]['errors'][issue['error_index']]
            candidates = [n for n in error.get('candidate_items', []) if type(n) is int and n in ITEM_TITLES]
            issue['field_options'] = [{'item_no': n, 'title': ITEM_TITLES[n]} for n in (candidates or ITEM_TITLES)]
        if issue['code'] in ('text_assembly_unresolved', 'ocr_coverage', 'ocr_quality') and type(ci) is int:
            try:
                issue.update(assembly_table_scope(chunks[ci], issue) or {})
            except (ValueError, KeyError, TypeError):
                pass  # 缺少可靠几何证据时仍阻塞，不伪造编辑范围。
    return issues


def readiness_result(issues):
    return {'ready': not bool(issues), 'blocking_count': len(issues), 'blocking_issues': issues,
            'code': '' if not issues else 'parse_review_required'}
