"""解析结果的可用性与安全提示；复用共享解析器的逐页诊断。"""
from pathlib import Path
import zipfile
import re
import logging
import traceback
from contextvars import ContextVar

parse_task_id = ContextVar('filing_parse_task_id', default='')

# 与物料表解析共用列名；表头本身不能充当数据行。
MATERIAL_SOURCE_ALIASES = {
    'material_name': ['原/辅料/包材', '原辅包', '物料名称', '原料名称', '名称'],
    'register_no': ['登记号'], 'accept_no': ['受理号'],
    'manufacturer': ['生产企业', '供应商'],
}


def log_diagnostics(diagnostics, project_id, file_id):
    for error in diagnostics.get('errors', []):
        logging.getLogger(__name__).warning('filing parse project_id=%s task_id=%s file_id=%s page=%s stage=%s exception_type=%s code=%s http_status=%s',
            project_id, parse_task_id.get(), file_id, error.get('page'), error.get('stage'), error.get('exception_type'), error.get('code'), error.get('http_status'))


def log_failure(exc, project_id, file_id, stage, task_id=''):
    # 保留异常链类型及调用位置，不记录可能含全文、密钥或服务响应的异常消息。
    chain = []
    current = exc
    while current is not None:
        chain.append({'exception_type': type(current).__name__, 'frames': [
            {'file': Path(f.filename).name, 'line': f.lineno, 'function': f.name}
            for f in traceback.extract_tb(current.__traceback__)]})
        current = current.__cause__
    logging.getLogger(__name__).error('filing parse project_id=%s task_id=%s file_id=%s stage=%s exceptions=%s',
                                     project_id, task_id or parse_task_id.get(), file_id, stage, chain)
    log_diagnostics(getattr(exc, 'diagnostics', {}), project_id, file_id)


MESSAGES = {
    'file_access': '文件不存在、不可读或访问失败，请重新上传并检查读取权限。',
    'empty_file': '文件为空，请选择包含正文的文件重新上传。',
    'format_mismatch': '文件实际格式与声明格式不符，请用原软件重新导出。',
    'pdf_password': 'PDF 需要密码，请上传已解除密码保护的副本。',
    'pdf_damaged': 'PDF 损坏或无法打开，请确认原文件可打开后重新导出。',
    'ocr_timeout': 'OCR 识别超时，请稍后重试或拆分文件。',
    'ocr_unreachable': 'OCR 服务无法连接，请联系管理员检查服务后重试。',
    'ocr_response': 'OCR 返回异常，请联系管理员检查识别服务后重试。',
    'ocr_http': 'OCR 服务请求失败，请联系管理员核对服务状态。',
    'ocr_busy': 'OCR 服务繁忙或排队超时，请稍后重试；此前有效结果保留。',
    'ocr_invalid_response': 'OCR 响应格式或坐标数据不完整，请联系管理员检查接口兼容性。',
    'ocr_quality': '文字已识别，但部分文字置信度较低，请核对原页。',
    'ocr_coverage': '已有可用文字，但部分可见区域尚未完整识别，请核对原页。',
    'numeric_uncertain': '数值核验存在分歧或尚未完成，请核对原页的符号、数字和单位。',
    'diagnostic_unknown': '解析存在待核查问题，请查看原页及诊断详情。',
    'ocr_empty': 'OCR 未得到可用文字，请上传更清晰的扫描件。',
    'table_structure_unresolved': '文字已识别，表格结构未恢复，请核对原表的行列和单元格归属。',
    'text_assembly_unresolved': '文字已保留，但正文与表格的对应或阅读顺序尚未确认，请核对原页的文字及归属。',
    'parser_failed': '解析器执行失败，请重新导出文件后重试，或联系管理员。',
    'empty_result': '未解析到可用正文，请确认文件包含正文并重新上传。',
    'empty_form': '申请表只有字段标签、未选候选、表头、模板提示或默认单位，未发现本次填写内容；此前有效原件和字段已保留。',
    'form_content_unconfirmed': '已保留本次原文及位置，但无法确认残留文字属于填写内容；未替换此前有效原件，请核对填写区域后重新上传。',
    'field_region_crossing': '文字跨越并列字段区域，归属不确定；已保留原文位置，请人工核对。',
    'field_region_unassigned': '文字缺少可确认的字段归属，已保留原文位置，请人工核对。',
    'page_geometry': 'PDF 页面尺度或页面框无效，无法可靠转换原文位置；请检查原件或重新导出。',
    'persist_failed': '解析结果保存失败，此前有效结果已保留，请稍后重试或联系管理员。',
    'file_too_large': '文件超过上传大小上限，请拆分文件后重新上传。',
    'unsupported_format': '此格式暂不支持解析，请导出为 PDF、Word 或文本文件后上传。',
    'task_interrupted': '解析任务已中断，本次结果未提交，请重新发起解析。',
    'rollback_failed': '恢复此前结果时发生异常，请联系管理员核对文件和数据后再操作。',
}


class ParseFailure(ValueError):
    def __init__(self, code, diagnostics=None):
        self.code = code
        self.diagnostics = diagnostics or {}
        super().__init__(MESSAGES[code])


class OCRResponseError(ValueError):
    """已确认发生在 OCR 响应校验阶段的格式问题。"""


def ocr_exception_details(exc):
    """只记录安全的异常类别与状态码，不输出服务响应或地址。"""
    name = type(exc).__name__
    status = getattr(getattr(exc, 'response', None), 'status_code', None)
    if name in ('Timeout', 'ReadTimeout', 'ConnectTimeout', 'TimeoutError'):
        code = 'ocr_timeout'
    elif name in ('ConnectionError', 'ConnectError'):
        code = 'ocr_unreachable'
    elif name == 'HTTPError':
        code = 'ocr_busy' if status == 503 else 'ocr_timeout' if status == 504 else 'ocr_http'
    elif isinstance(exc, OCRResponseError) or name == 'JSONDecodeError':
        code = 'ocr_invalid_response'
    else:
        code = 'diagnostic_unknown'
    return {'code': code, 'exception_type': name,
            **({'http_status': status} if isinstance(status, int) else {})}


def failure(exc, stage='parse'):
    if isinstance(exc, ParseFailure):
        code = exc.code
    elif stage == 'persist':
        code = 'persist_failed'
    elif stage == 'rollback':
        code = 'rollback_failed'
    elif type(exc).__name__ in ('Timeout', 'ReadTimeout', 'ConnectTimeout', 'TimeoutError'):
        code = 'ocr_timeout'
    elif type(exc).__name__ in ('ConnectionError', 'ConnectError'):
        code = 'ocr_unreachable'
    elif isinstance(exc, OSError):
        code = 'file_access'
    else:
        code = 'parser_failed'
    return {'content_status': 'failed', 'content_available': False, 'code': code,
            'task_id': parse_task_id.get(),
            'message': MESSAGES[code], 'stage': stage, 'exception_type': type(exc).__name__,
            'parse_diagnostics': getattr(exc, 'diagnostics', {})}


def check_file(file_path):
    path = Path(file_path)
    # 真实打开读取；签名只用于拒绝明显错格式，不作为解析成功依据。
    with path.open('rb') as stream:
        prefix = stream.read(1024)
    if not prefix:
        raise ParseFailure('empty_file')
    ext = path.suffix.lower()
    if ext == '.pdf':
        if b'%PDF-' not in prefix:
            raise ParseFailure('format_mismatch')
        import fitz
        try:
            with fitz.open(path) as doc:
                if doc.needs_pass:
                    raise ParseFailure('pdf_password')
                if not doc.is_pdf or not len(doc):
                    raise ParseFailure('pdf_damaged')
        except ParseFailure:
            raise
        except Exception as exc:
            raise ParseFailure('pdf_damaged') from exc
    elif ext == '.docx':
        try:
            with zipfile.ZipFile(path) as package:
                if 'word/document.xml' not in package.namelist():
                    raise ParseFailure('format_mismatch')
        except zipfile.BadZipFile as exc:
            raise ParseFailure('format_mismatch') from exc
    elif ext == '.doc' and not prefix.startswith(bytes.fromhex('d0cf11e0a1b11ae1')):
        raise ParseFailure('format_mismatch')


def outcome(rows):
    rows = [r for r in (rows or []) if isinstance(r, dict)]
    diagnostics = dict(next((r['parse_diagnostics'] for r in rows if r.get('parse_diagnostics')), {}))
    usable = []
    errors = []
    for row in rows:
        text = str(row.get('text', row.get('raw_text', '')) or '').strip()
        body = '\n'.join(line for line in text.splitlines() if not line.lstrip().startswith('#'))
        placeholder = re.fullmatch(r'\s*(?:\[.*(?:错误|失败|error|failed).*\]|(?:解析失败|识别失败|错误[:：]|error[:：]|Failed to open file).*)\s*', body, re.I)
        title_only = body.strip() in {str(row.get('title', '')).strip(), '药品注册补充申请表', '境内生产药品注册备案表'}
        tables = any(any(str(v).strip() for v in values) for table in row.get('tables', []) if isinstance(table, dict)
                     for values in (table.get('rows') or table.get('data') or []) if isinstance(values, (list, tuple)))
        if row.get('content_available') is not False and not placeholder and ((any(c.isalnum() for c in body) and not title_only) or tables):
            usable.append(row.get('page'))
        for error in row.get('errors', []):
            reason = str(error.get('reason', '')).lower()
            stage = error.get('stage', 'page')
            code = 'page_geometry' if stage == 'page_geometry' else 'parser_failed'
            if str(stage).startswith('ocr'):
                code = ('ocr_timeout' if 'timeout' in reason or 'timed out' in reason else
                        'ocr_unreachable' if 'connection' in reason or 'connecterror' in reason else
                        'ocr_http' if 'httperror' in reason else
                        'ocr_empty' if '未返回' in reason or 'empty' in reason else 'diagnostic_unknown')
            code = error['code'] if error.get('code') in MESSAGES else code
            # 历史记录曾把这两个质量阶段误归为服务异常，按阶段纠正展示。
            if stage in ('ocr_quality', 'ocr_coverage'):
                code = stage
            elif stage in ('table_geometry', 'table_structure'):
                code = 'table_structure_unresolved'
            elif stage == 'text_assembly' and not error.get('exception_type'):
                code = 'text_assembly_unresolved'
            error['code'] = code
            match = re.match(r'^([A-Za-z][A-Za-z0-9_]*):', str(error.get('reason', '')))
            error['exception_type'] = error.get('exception_type') or (match.group(1) if match else '')
            errors.append({'page': row.get('page'), 'stage': stage, 'code': code, 'message': MESSAGES[code], 'exception_type': error['exception_type'],
                           **({'http_status': error['http_status']} if isinstance(error.get('http_status'), int) else {}),
                           **({'bbox_pdf':error['bbox_pdf'], 'coordinate_unit':'pdf_point'} if error.get('bbox_pdf') else {}),
                           **({'numeric_verification': error['numeric_verification']} if code == 'numeric_uncertain' and error.get('numeric_verification') else {}),
                           **({'quality_evidence': error['quality_evidence']} if code == 'ocr_quality' and error.get('quality_evidence') else {}),
                           **({key: error[key] for key in ('recovery_conflicts', 'uncovered_components', 'low_confidence_words')
                               if key in error} if code in ('ocr_quality', 'ocr_coverage') else {}),
                           **({key: error[key] for key in ('text', 'candidate_items', 'source_regions') if key in error}
                              if code in ('field_region_crossing', 'field_region_unassigned') else {})})
            error['reason'] = MESSAGES[code]
    diagnostics['errors'] = errors
    diagnostics['failed_pages'] = sorted(set(diagnostics.get('failed_pages', [])) | {e['page'] for e in errors if isinstance(e['page'], int)})
    diagnostics.setdefault('available_pages', [p for p in usable if p is not None])
    diagnostics['missing_pages'] = sorted({r['page'] for r in rows if isinstance(r.get('page'), int)
                                           and r.get('content_available') is False and r.get('errors')})
    status = 'failed' if not usable else ('partial' if errors or diagnostics.get('failed_pages') or diagnostics.get('status') in ('partial', 'failed') else 'success')
    diagnostics['status'] = status
    if status == 'failed':
        raise ParseFailure(errors[0]['code'] if errors else 'empty_result', diagnostics)
    return {'content_status': status, 'content_available': True, 'parse_diagnostics': diagnostics,
            'message': '部分成功：存在未解析页面或区域，请查看缺失范围并重试。' if status == 'partial' else '解析成功'}


def form_content(text, excluded_fragments=()):
    """剔除独立模板片段；不通过旧记录、表题、候选选项判断本次填写成果。"""
    from agent.agent_backend.utils.parser.form_field_semantics import heading, selection_evidence
    from agent.agent_backend.utils.parser.drug_supplement_pdf_parser import SUBFIELD_ALIASES
    labels = {re.sub(r'\s+', '', label) for name, aliases in SUBFIELD_ALIASES.items() for label in [name, *aliases]}
    labels.update(('原有效期', '拟延长后有效期', '变更后有效期', '贮藏条件', '储存条件', '有效期单位', '序号'))
    labels.update(label for names in MATERIAL_SOURCE_ALIASES.values() for label in names)
    compact = lambda value: re.sub(r'\s+', '', str(value)).strip(' :：;；|')
    excluded = {compact(fragment) for fragment in excluded_fragments if compact(fragment)}
    content = []
    for part in re.split(r'[\n|\t]', str(text or '')):
        part = part.strip(' :：;；_＿—-()（）[]【】')
        if not part:
            continue
        if not any(char.isalnum() for char in part):
            continue
        if compact(part) in excluded:
            continue
        found = heading(part)
        if found:
            part = found[2].strip(' :：;；_＿—-()（）[]【】')
        if not part or part in ('月', '个月', '年', '天', '日', 'mg', 'g', 'mL', '%', '内容', '填写内容',
                                '药品注册补充申请表', '境内生产药品注册备案表'):
            continue
        if re.sub(r'\s+', '', part) in labels:
            continue
        if re.fullmatch(r'(?:' + '|'.join(re.escape(label) for label in sorted(labels, key=len, reverse=True)) + r'|\s|[:：])+', part):
            continue
        selection = selection_evidence(part, multiple=True, require_mark=True)
        if selection['entries'] and all(entry['selected'] is False for entry in selection['entries']):
            continue
        if re.fullmatch(r'(?:请(?:在此)?填写.*|请输入.*|点击(?:或轻触)?此处.*|待填写|待填|未填写)', part):
            continue
        content.append(part)
    return '\n'.join(content)


def _choice_template_fragments(field):
    """只排除已经按本次单元格/选项证据确认未选的候选，不按名称全局删词。"""
    entries = field.get('selection_evidence') or []
    if entries and all(entry.get('selected') is not True and entry.get('status') in
                       ('unselected', 'unconfirmed', 'category') for entry in entries):
        return [entry.get('source_text', '') for entry in entries]
    return []


def _has_filled_value(value):
    if isinstance(value, dict):
        return any(_has_filled_value(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(_has_filled_value(v) for v in value)
    return bool(form_content(value)) if value is not None else False


def annotate_form_fields(form, uncertain_pages=()):
    """新解析的每个值与识别状态、位置一起保存；不读取历史记录。"""
    from copy import deepcopy
    from agent.agent_backend.services.filing_form_revision import values, present, put
    for field in form.values():
        if not isinstance(field, dict) or 'field_type' not in field:
            continue
        source = field.get('source_text', '')
        template_options = _choice_template_fragments(field)
        if template_options:
            field['semantic_issues'] = [issue for issue in field.get('semantic_issues', [])
                                        if issue.get('reason') != '选项未确认，待核对']
        conflict = any('冲突' in str(issue.get('reason', '')) for issue in field.get('semantic_issues', []))
        uncertain = any(region.get('page') in uncertain_pages for region in field.get('source_regions', []))
        # 已逐项读到未选符号的原生选项有明确证据；同页额外 OCR 失败仍留在
        # 页面诊断中，不把这些已经读到的未选符号降成“未识别”。
        explicit_unselected = bool(template_options) and all(
            entry.get('selected') is False for entry in field.get('selection_evidence', []))
        blank = (bool(field.get('source_regions') or source) and not form_content(source, template_options)
                 and not field.get('semantic_issues') and (not uncertain or explicit_unselected))
        statuses = []
        for path, value in values(field).items():
            if path == 'sub_fields.validity_unit':
                status = 'default'
            else:
                if present(value) and not _has_filled_value(value):
                    value = [] if isinstance(value, list) else {} if isinstance(value, dict) else ''
                    put(field, path, value)
                status = ('extracted' if present(value) else 'conflict' if conflict else
                          'explicit_blank' if blank else 'unrecognized')
                statuses.append(status)
            evidence = deepcopy(field.get('value_sources', {}).get(path, {}))
            evidence.update(value=deepcopy(value), recognition_status=status)
            for meta, default in [('source_text', ''), ('source_file', ''), ('source_file_id', ''), ('source_regions', []), ('parse_confidence', 0)]:
                evidence.setdefault(meta, deepcopy(field.get(meta, default)))
            if status != 'extracted':
                evidence['parse_confidence'] = 0
            field.setdefault('value_sources', {})[path] = evidence
        field['recognition_status'] = ('conflict' if conflict else 'extracted' if 'extracted' in statuses or field.get('reported_value') else
                                       'explicit_blank' if blank else 'unrecognized')
        if 'extracted' not in statuses and not field.get('reported_value'):
            field['parse_confidence'] = 0


def form_outcome(parsed):
    pdf = parsed.get('pdf_parse_result')
    uncertain_pages = {page.get('page') for page in (pdf or {}).get('pages', []) if page.get('errors')} if isinstance(pdf, dict) else set()
    annotate_form_fields(parsed.get('form_json') or {}, uncertain_pages)
    fields = [field for field in (parsed.get('form_json') or {}).values()
              if isinstance(field, dict) and 'field_type' in field]
    fragments = [fragment for field in fields for fragment in _choice_template_fragments(field)]
    filled_fields = [field for field in fields if any(
        source.get('recognition_status') == 'extracted' and _has_filled_value(source.get('value'))
        for source in field.get('value_sources', {}).values()) or _has_filled_value(field.get('reported_value'))]
    selected = any(entry.get('selected') is True for field in fields for entry in field.get('selection_evidence', []))
    structure = parsed.get('form_structure')
    if structure is None and isinstance(pdf, dict):
        structure = pdf.get('form_structure')
    raw_content = form_content(structure['body_text'] if structure is not None else parsed.get('raw_text', ''), fragments)
    uncertain_regions = [r for r in (structure or {}).get('regions', []) if r['role'] == 'uncertain']
    # 未映射字段也可以有确切填写证据：字段原文、同行的填写格或未知栏目的键值。
    # 单独残留的文字只作为待核对原文，不据此授权替换旧原件。
    from agent.agent_backend.utils.parser.form_field_semantics import heading
    from agent.agent_backend.utils.parser.form_content_evidence import filling_label
    confirmed_unmapped = bool(selected)
    for region in (structure or {}).get('regions', []):
        if region['role'] != 'body':
            continue
        text = region['text']
        found = heading(text)
        row = region.get('row_values', [])
        pair = re.match(r'[^:：\n]+[:：](.+)', text)
        if ((found and form_content(found[2], fragments)) or
                (region.get('column_label') and form_content(text, fragments)) or
                (len(row) > 1 and filling_label(row[0]) and any(form_content(v, fragments) for v in row[1:])) or
                (pair and filling_label(text.split(pair.group(1))[0]) and form_content(pair.group(1), fragments))):
            confirmed_unmapped = True
    def classify(result, extra_content=''):
        diagnostics = result['parse_diagnostics']
        diagnostics['replacement_eligible'] = bool(filled_fields or confirmed_unmapped)
        if structure is not None:
            diagnostics['form_content_regions'] = structure['regions']
        if not filled_fields and not selected and not raw_content and not extra_content and not uncertain_regions:
            # 识别中断不能证明未填写；保留原始失败范围与真实失败原因。
            code = next((error['code'] for error in diagnostics.get('errors', [])
                         if error.get('code', '').startswith('ocr_')), 'empty_form')
            raise ParseFailure(code, {**diagnostics, 'status': 'failed', 'content_available': False,
                                      'form_content_status': 'unavailable' if code != 'empty_form' else 'empty_template',
                                      'reason': MESSAGES[code]})
        unresolved = not filled_fields
        diagnostics['form_content_status'] = 'unrecognized_content' if unresolved else 'filled_content'
        if unresolved:
            diagnostics['status'] = 'partial'
            diagnostics['filling_evidence'] = 'confirmed' if confirmed_unmapped else 'unconfirmed'
            diagnostics['unmapped_content'] = raw_content
            result.update(content_status='partial', message='部分成功：存在本次填写内容，但字段尚未确认；请查看原文和识别异常后核对。')
            if not confirmed_unmapped:
                result['message'] = '部分识别：原文已保留，但尚不能确认其属于填写内容；请核对原文位置，暂不用于替换此前有效原件。'
        return result
    if isinstance(pdf, dict) and 'pages' in pdf:
        result = outcome(pdf['pages'])
        return classify(result)
    rows = [{'text': parsed.get('raw_text', '')}]
    if isinstance(pdf, dict):
        # 兼容旧字段/区域识别结果；无逐页覆盖信息时只能说明部分可用。
        rows.extend({'text': item.get('raw_text', '')} for item in pdf.get('items', []))
        rows.extend({'page': error.get('page'), 'errors': [error], 'content_available': False} for error in pdf.get('region_errors', []))
        for node in (parsed.get('form_json') or {}).values():
            if not isinstance(node, dict) or not node.get('parse_confidence'):
                continue
            values = [node.get('value', '')] + list((node.get('sub_fields') or {}).values())
            rows.extend({'text': value} for value in values if isinstance(value, str) and value.strip())
    result = outcome(rows)
    result = classify(result, '\n'.join(form_content(row.get('text', ''), fragments) for row in rows[1:]))
    if isinstance(pdf, dict):
        result.update(content_status='partial', message='部分成功：已提取内容，但旧解析器未提供逐页覆盖信息，请人工核对全文或重新解析。')
        result['parse_diagnostics'].update(status='partial', available_range='unknown')
    return result
