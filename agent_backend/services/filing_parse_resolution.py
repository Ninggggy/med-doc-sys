"""人工修订副本：原始OCR证据不变，过期修订不参与新分析。"""
from copy import deepcopy
import math


def rect(value):
    if (not isinstance(value, (list, tuple)) or len(value) != 4 or
            any(type(v) not in (int, float) or not math.isfinite(v) for v in value) or
            value[2] <= value[0] or value[3] <= value[1]):
        raise ValueError('问题区域缺少有效坐标，请重新解析并核对原页')
    return list(value)


def overlaps(a, b):
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


def contains(a, b):
    return a[0] <= b[0] and a[1] <= b[1] and a[2] >= b[2] and a[3] >= b[3]


def numeric_text_review_available(chunk, issue):
    """仅开放有完整定位证据、不触及表格的原正文区域。"""
    if issue.get('code') != 'numeric_uncertain':
        return False
    try:
        bounds = rect(issue.get('bbox_pdf'))
        index = issue.get('error_index')
        errors = chunk.get('errors') or []
        if type(index) is not int or not 0 <= index < len(errors):
            return False
        error = errors[index]
        checks = error.get('numeric_verification')
        if (error.get('code') != 'numeric_uncertain' or rect(error.get('bbox_pdf')) != bounds
                or not isinstance(checks, list) or not checks
                or not all(contains(bounds, rect(check.get('bbox_pdf'))) for check in checks)):
            return False
        if any(overlaps(bounds, rect(t.get('bbox_pdf'))) for t in chunk.get('tables') or []):
            return False
        words = chunk.get('words') or []
        affected = [w for w in words if overlaps(bounds, rect(w.get('bbox')))]
        return bool(affected) and all(contains(bounds, rect(w.get('bbox'))) for w in affected)
    except (ValueError, TypeError, AttributeError):
        return False


def quality_confirmation_available(chunk, issue):
    if issue.get('code') != 'ocr_quality':
        return False
    error_index = issue.get('error_index')
    errors = chunk.get('errors') or []
    if (type(error_index) is int and 0 <= error_index < len(errors)
            and errors[error_index].get('recovery_conflicts')):
        # 有候选冲突/重排不确定时，普通确认没有记录最终采用的文字。
        # 必须完整修订，不能以“该区域有字”替代解决真实分歧。
        return False
    try:
        bounds = rect(issue.get('bbox_pdf'))
    except ValueError:
        return False
    for word in chunk.get('words') or []:
        if not isinstance(word, dict) or not str(word.get('text') or '').strip():
            continue
        try:
            if overlaps(bounds, rect(word.get('bbox'))):
                return True
        except ValueError:
            continue
    return False


def assembly_table_scope(chunk, issue):
    """由服务器原始证据确定完整核对范围，不接受客户端扩大/缩小坐标。"""
    if issue.get('code') not in ('text_assembly_unresolved', 'ocr_coverage', 'ocr_quality'):
        return None
    box = rect(issue.get('bbox_pdf'))
    tables = chunk.get('tables', [])
    matches = [i for i, table in enumerate(tables) if overlaps(box, rect(table['bbox_pdf']))]
    if not matches:
        return None
    def union(a, b):
        return [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]
    blocks = chunk.get('words', []) + chunk.get('lines', []) + [{'bbox': t['bbox_pdf']} for t in tables]
    # 有限集合闭包：每次扩展至少完整纳入一个已知文字块。
    for _ in range(len(blocks) + 1):
        previous = box[:]
        for block in blocks:
            bounds = rect(block.get('bbox'))
            if overlaps(box, bounds):
                box = union(box, bounds)
        if box == previous:
            break
    indices = [i for i, table in enumerate(tables) if overlaps(box, rect(table['bbox_pdf']))]
    return {'repair_bbox_pdf': box, **({'repair_table_index': indices[0]} if len(indices) == 1
                                      else {'repair_table_indices': indices})}


def manual_table(value, bbox, page, reason):
    if not isinstance(value, dict):
        raise ValueError('请提交表格结构')
    rows, cols, cells = value.get('row_count'), value.get('column_count'), value.get('cells')
    if any(type(v) is not int or v < 1 or v > 200 for v in (rows, cols)) or rows * cols > 10000:
        raise ValueError('表格行列数量无效或过大')
    if not isinstance(cells, list) or not cells or len(cells) > rows * cols:
        raise ValueError('请提交全部单元格，包括空白格和合并格')
    matrix = [['' for _ in range(cols)] for _ in range(rows)]
    covered, result = set(), []
    for cell in cells:
        if not isinstance(cell, dict):
            raise ValueError('单元格格式无效')
        r, c, rs, cs = (cell.get(k, 1 if k.endswith('span') else None) for k in ('row', 'column', 'rowspan', 'colspan'))
        if (any(type(v) is not int for v in (r, c, rs, cs)) or min(r, c) < 0 or min(rs, cs) < 1
                or r + rs > rows or c + cs > cols):
            raise ValueError('单元格位置或合并范围无效')
        positions = {(y, x) for y in range(r, r + rs) for x in range(c, c + cs)}
        if covered & positions:
            raise ValueError('合并单元格重叠，不能保存')
        covered |= positions
        text = cell.get('text')
        if not isinstance(text, str) or len(text) > 20000:
            raise ValueError('单元格文字无效或过长')
        matrix[r][c] = text
        result.append({'row': r, 'column': c, 'rowspan': rs, 'colspan': cs, 'text': text,
                       'position_kind': 'manual_table', 'source_bbox_pdf': list(bbox),
                       'numeric_status': 'manual_confirmed',
                       'manual_confirmation': {'value': text, 'reason': reason}})
    if len(covered) != rows * cols:
        raise ValueError('表格仍有未定义单元格，请补齐空白格或合并关系')
    def md(row):
        return '| ' + ' | '.join(t.replace('|', '\\|').replace('\n', ' ') for t in row) + ' |'
    return {'page': page, 'bbox_pdf': list(bbox), 'source': 'manual_revision', 'cells': result,
            'rows': matrix, 'columns': matrix[0], 'data': matrix[1:], 'needs_review': False,
            'markdown': '\n'.join([md(matrix[0]), md(['---'] * cols)] + [md(row) for row in matrix[1:]])}


def apply_resolutions(chunks, issues, items):
    """逐项核对服务器问题；不信任客户端的坐标、原因码或已解决标记。"""
    current = {issue['issue_key']: issue for issue in issues}
    output, resolved, saved = deepcopy(chunks), set(), []
    repaired_tables = []
    numeric_text_regions = []
    if not isinstance(items, list) or len(items) > 10000:
        raise ValueError('核对记录必须为列表且不超过10000项')
    for item in items:
        if not isinstance(item, dict) or item.get('issue_key') not in current or item['issue_key'] in resolved:
            raise ValueError('问题已变化、重复或不属于本文件，请重新读取')
        issue = current[item['issue_key']]
        action, reason = item.get('action'), item.get('reason')
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 2000:
            raise ValueError('必须填写逐项核对依据，最长2000字符')
        ci = issue.get('chunk_index')
        if type(ci) is not int or not 0 <= ci < len(output):
            raise ValueError('该问题没有可修订的原页内容，请重新解析或更换清晰原件')
        chunk = output[ci]
        code = issue.get('code')
        record = {'issue_key': item['issue_key'], 'action': action, 'reason': reason.strip()}
        continuation = item.get('continuation_item_no')
        if continuation is not None:
            from agent.agent_backend.utils.parser.drug_supplement_pdf_parser import ITEM_TITLES, item_start, SECTION_LINES, compact_for_matching
            if (issue.get('source_kind') != 'application_form' or action not in ('correct_text', 'correct_table')
                    or type(continuation) is not int or continuation not in ITEM_TITLES
                    or not isinstance(chunk.get('page'), int) or chunk['page'] <= 1
                    or item.get('field_assignments') or item.get('target_item_no')):
                raise ValueError('仅申请表后续页的完整正文可指定续接字段，不能与逐段字段分配混用')
            complete = item.get('outside_text') if action == 'correct_table' else item.get('text')
            prefix = [s.strip() for s in str(complete or '').splitlines() if s.strip()
                      and compact_for_matching(s).strip(' |:') not in SECTION_LINES]
            if not prefix or item_start(prefix[0]):
                raise ValueError('本区域没有无标题续文，请清除续接字段选择；不能覆盖已有明确标题')
            record['continuation_item_no'] = continuation
        if action == 'confirm':
            if code != 'ocr_quality' or not str(chunk.get('raw_text') or chunk.get('text') or '').strip():
                raise ValueError('缺失内容或不确定表格不能仅点击确认，须修订或重新解析')
            if not quality_confirmation_available(chunk, issue):
                raise ValueError('本区域缺少可定位文字或存在冲突候选，不能直接确认；请完整修订文字或表格，或重新解析')
        elif action in ('correct_text', 'irrelevant_region', 'correct_table'):
            bbox = rect(issue.get('bbox_pdf'))
            if str(chunk.get('raw_text') or chunk.get('text') or '').strip() and not chunk.get('words'):
                raise ValueError('历史正文没有可定位文字块，不能局部覆盖；请重新解析后核对')
            if action == 'correct_table':
                scope = assembly_table_scope(chunks[ci], issue)
                if continuation is not None and not scope:
                    raise ValueError('当前表格修订不含表外正文，不能指定续接字段')
                if code not in ('table_review_required', 'table_structure_unresolved') and not scope:
                    raise ValueError('当前问题不是可修订的表格结构问题')
                table_bbox = bbox
                scope_indices = []
                if scope:
                    bbox = scope['repair_bbox_pdf']
                    scope_indices = scope.get('repair_table_indices', [scope.get('repair_table_index')])
                    table_bbox = rect(chunks[ci]['tables'][scope_indices[0]]['bbox_pdf'])
                    outside = item.get('outside_text')
                    if item.get('outside_text_verified') is not True or not isinstance(outside, str) or len(outside) > 100000:
                        raise ValueError('必须逐字核对完整区域的表外文字；没有表外文字时也须明确确认空白')
                    record.update(outside_text=outside, outside_text_verified=True)
                    if item.get('numeric_text_verified') is True:
                        # 表内逐格修订之外，表外数值须另行明确核对。
                        # 整个范围仍由服务器闭包决定，客户端不能扩大。
                        record['numeric_text_verified'] = True
                        numeric_text_regions.append((ci, list(bbox)))
                    if outside.strip():
                        chunk.setdefault('_manual_outside_blocks', []).append({
                            'text': outside.strip(), 'bbox': list(bbox), 'source': 'manual_revision',
                            **({'manual_continuation_item_no': continuation} if continuation is not None else {}),
                            'position_kind': 'review_region_not_glyph', 'manual_confirmation': {'reason': reason.strip()}})
                matching = [i for i, table in enumerate(chunk.get('tables', [])) if overlaps(bbox, table['bbox_pdf'])]
                if any(not contains(bbox, chunk['tables'][i]['bbox_pdf']) for i in matching):
                    raise ValueError('修订区域截断其他表格，请扩大核对范围或重新解析')
                replacements = []
                if len(scope_indices) > 1:
                    values = item.get('tables')
                    if (not isinstance(values, list) or len(values) != len(scope_indices)
                            or any(not isinstance(v, dict) or type(v.get('table_index')) is not int or v.get('verified') is not True for v in values)
                            or sorted(v['table_index'] for v in values) != scope_indices):
                        raise ValueError('跨界区域包含多个独立表格，必须逐个提交全部表格，不能缺项、重复或合并')
                    by_index = {v['table_index']: v['table'] for v in values if 'table' in v}
                    if len(by_index) != len(scope_indices):
                        raise ValueError('多表修订缺少完整单元格结构')
                    for ti in scope_indices:
                        original = chunks[ci]['tables'][ti]
                        table = manual_table(by_index[ti], rect(original['bbox_pdf']), chunk.get('page'), reason.strip())
                        table['id'] = original.get('id') or f'manual_{ci}_{ti}'
                        replacements.append(table)
                    record['tables'] = deepcopy(values)
                else:
                    table = manual_table(item.get('table'), table_bbox, chunk.get('page'), reason.strip())
                    table['id'] = (chunk['tables'][matching[0]].get('id') if len(matching) == 1 else None) or f'manual_{ci}_{len(saved)}'
                    replacements.append(table)
                    record['table'] = deepcopy(item['table'])
                if any(overlaps(bbox, w['bbox']) and not contains(bbox, w['bbox']) for w in chunk.get('words', [])):
                    raise ValueError('表格边界截断正文，请核对完整区域后重新处理')
                chunk['words'] = [w for w in chunk.get('words', []) if not contains(bbox, w['bbox'])]
                chunk['tables'] = [table0 for i, table0 in enumerate(chunk.get('tables', [])) if i not in matching] + replacements
                repaired_tables.extend((ci, list(table['bbox_pdf']), list(bbox) if scope else None) for table in replacements)
            else:
                if any(overlaps(bbox, t['bbox_pdf']) for t in chunk.get('tables', [])):
                    raise ValueError('区域涉及表格，必须修订表格，不能删除或用正文覆盖')
                field_correction = (issue.get('source_kind') == 'application_form'
                                    and code in ('field_region_crossing', 'field_region_unassigned'))
                if field_correction:
                    target = item.get('target_item_no')
                    options = [v['item_no'] for v in issue.get('field_options', [])]
                    assignments = item.get('field_assignments')
                    if action != 'correct_text':
                        raise ValueError('须修订完整区域文字并明确选择其所属申请字段，不能仅确认或忽略')
                    if assignments is not None:
                        if (not isinstance(assignments, list) or not 1 <= len(assignments) <= 50
                                or item.get('all_text_verified') is not True
                                or any(not isinstance(part, dict) or type(part.get('target_item_no')) is not int
                                       or part['target_item_no'] not in options or not isinstance(part.get('text'), str)
                                       or not part['text'].strip() or len(part['text']) > 100000 for part in assignments)):
                            raise ValueError('请逐段填写完整文字及所属字段，并确认已覆盖整个区域')
                        # 只允许段与段之间的排版空白，不删除段内空白后比较。
                        # 否则完整正文的“1 2 mg”可被静默合并成“12 mg”。
                        complete = item.get('text')
                        cursor, matches = 0, isinstance(complete, str)
                        if matches:
                            for part in assignments:
                                while cursor < len(complete) and complete[cursor].isspace():
                                    cursor += 1
                                segment = part['text'].strip()
                                if not complete.startswith(segment, cursor):
                                    matches = False
                                    break
                                cursor += len(segment)
                        if not matches or complete[cursor:].strip():
                            raise ValueError('分配到各字段的文字须按顺序完整覆盖核对正文，不能遗漏或重复')
                        record['field_assignments'] = [{'target_item_no': part['target_item_no'], 'text': part['text'].strip()}
                                                       for part in assignments]
                        record['all_text_verified'] = True
                    else:
                        if type(target) is not int or target not in options:
                            raise ValueError('须修订完整区域文字并明确选择其所属申请字段，不能仅确认或忽略')
                        record['target_item_no'] = target
                elif code == 'numeric_uncertain':
                    if (action != 'correct_text' or item.get('numeric_text_verified') is not True
                            or not numeric_text_review_available(chunks[ci], issue)):
                        raise ValueError('须完整修订原正文区域并明确核对全部数值、单位及符号；位置不完整或涉及表格时不能直接覆盖')
                elif code not in ('ocr_quality', 'ocr_coverage', 'ocr_empty', 'text_assembly_unresolved'):
                    raise ValueError('服务故障或未定位的问题须先重新解析')
                affected = [w for w in chunk.get('words', []) if overlaps(bbox, w['bbox'])]
                if any(not contains(bbox, w['bbox']) for w in affected):
                    raise ValueError('区域边界截断已有文字，请核对完整区域后重新处理')
                if action == 'irrelevant_region':
                    if code != 'ocr_coverage' or affected or item.get('non_text_kind') not in ('decoration', 'stamp', 'blank'):
                        raise ValueError('仅允许明确无正文、无表格的非文字区域；缺失文字不能忽略')
                    record['non_text_kind'] = item['non_text_kind']
                    text = ''
                else:
                    text = item.get('text')
                    if not isinstance(text, str) or not text.strip() or len(text) > 100000:
                        raise ValueError('请填写从原页核对的完整文字，最长100000字符')
                    record['text'] = text.strip()
                    # 正文补录不默认等于数值核对；用户须明确核对完整
                    # 区域内数值、单位和符号，仍以服务端原区域为界。
                    if action == 'correct_text' and item.get('numeric_text_verified') is True:
                        record['numeric_text_verified'] = True
                        numeric_text_regions.append((ci, list(bbox)))
                chunk['words'] = [w for w in chunk.get('words', []) if not overlaps(bbox, w['bbox'])]
                if text:
                    parts = record.get('field_assignments') or [{'text': text.strip(), 'target_item_no': record.get('target_item_no')}]
                    for part in parts:
                        chunk['words'].append({'text': part['text'], 'bbox': bbox, 'source': 'manual_revision',
                                               **({'manual_continuation_item_no': continuation} if continuation is not None else {}),
                                               'position_kind': 'review_region_not_glyph',
                                               **({'manual_target_item_no': part['target_item_no']} if field_correction else {}),
                                               'manual_confirmation': {'reason': reason.strip()}})
            # 正文和表格完整修订共用逐项关联核对；无关区域确认不适用。
            related = item.get('related_issues', [])
            if (not isinstance(related, list) or len(related) > len(issues)
                    or (related and action not in ('correct_text', 'correct_table'))):
                raise ValueError('关联问题核对记录无效')
            reviewed = set()
            related_records = []
            for entry in related:
                if not isinstance(entry, dict):
                    raise ValueError('关联问题核对记录无效')
                key, explanation = entry.get('issue_key'), entry.get('reason')
                other = current.get(key) if isinstance(key, str) else None
                if (not other or key == item['issue_key'] or key in reviewed or key in resolved
                        or other.get('chunk_index') != ci
                        or other.get('code') not in ('ocr_quality', 'ocr_coverage', 'ocr_empty', 'text_assembly_unresolved',
                                                   'field_region_crossing', 'field_region_unassigned')
                        or not contains(bbox, rect(other.get('bbox_pdf')))
                        or not isinstance(explanation, str) or not explanation.strip() or len(explanation) > 2000):
                    raise ValueError('关联问题必须位于本次完整修订范围内，并逐项填写核对依据；不能处理数值或服务故障')
                related_record = {'issue_key': key, 'reason': explanation.strip()}
                review_scope = entry.get('review_scope')
                if review_scope not in (None, 'complete_repair_region'):
                    raise ValueError('关联问题核对范围无效')
                expanded_scope = review_scope == 'complete_repair_region'
                if expanded_scope and other['code'] not in ('field_region_crossing', 'field_region_unassigned'):
                    raise ValueError('完整区域字段核对不能用于解除其他类型的问题')
                if other['code'] in ('field_region_crossing', 'field_region_unassigned'):
                    assignments = entry.get('field_assignments')
                    if assignments is not None:
                        complete = entry.get('reviewed_text')
                        if (not isinstance(assignments, list) or not 2 <= len(assignments) <= 50
                                or entry.get('target_item_no') is not None
                                or not isinstance(complete, str) or not complete.strip() or len(complete) > 100000):
                            raise ValueError('关联多字段须填写整个问题区域正文并逐段分配，不能同时指定单字段')
                        cursor = 0
                        for part in assignments:
                            segment = part.get('reviewed_text') if isinstance(part, dict) else None
                            if not isinstance(segment, str) or not segment.strip():
                                raise ValueError('关联多字段的每段文字不能为空')
                            while cursor < len(complete) and complete[cursor].isspace():
                                cursor += 1
                            if not complete.startswith(segment.strip(), cursor):
                                raise ValueError('逐段文字须按顺序完整覆盖问题区域正文，不能遗漏、重复或改变段内空白')
                            cursor += len(segment.strip())
                        if complete[cursor:].strip():
                            raise ValueError('逐段文字未完整覆盖问题区域正文')
                    parts = assignments if assignments is not None else [entry]
                    if any(part.get('source_kind') not in (None, 'table', 'outside_text') for part in parts):
                        raise ValueError('关联段来源类型无效')
                    mixed_sources = any(part.get('source_kind') == 'outside_text' for part in parts)
                    if expanded_scope and not mixed_sources:
                        raise ValueError('扩大核对范围须明确逐段核对完整表格及表外正文来源')
                    if mixed_sources:
                        if (assignments is None or action != 'correct_table' or not scope
                                or (not expanded_scope and rect(other.get('bbox_pdf')) != bbox) or not record.get('outside_text_verified')
                                or not record.get('outside_text', '').strip()):
                            raise ValueError('表内外联合核对须覆盖整个修订区域，逐段明确来源并完整核对表外正文')
                        outside_complete = record['outside_text']
                        outside_cursor = 0
                        for part in parts:
                            if part.get('source_kind') != 'outside_text':
                                continue
                            if part.get('table_index') is not None:
                                raise ValueError('表外正文不能同时指定来源表格')
                            while outside_cursor < len(outside_complete) and outside_complete[outside_cursor].isspace():
                                outside_cursor += 1
                            segment = part['reviewed_text'].strip()
                            if not outside_complete.startswith(segment, outside_cursor):
                                raise ValueError('表外各段须按顺序完整覆盖本次修订的表外正文')
                            outside_cursor += len(segment)
                        if outside_complete[outside_cursor:].strip():
                            raise ValueError('表外正文仍有未分配内容，不能解除整个混合区域的问题')
                    options = [option['item_no'] for option in other.get('field_options', [])]
                    checked_parts = []
                    assigned_spans = []
                    required_table_indices = set()
                    assigned_table_indices = set()
                    checked_table_values = set()
                    for part in parts:
                        target, excerpt = part.get('target_item_no'), part.get('reviewed_text')
                        if (issue.get('source_kind') != 'application_form' or other.get('source_kind') != 'application_form'
                                or action not in ('correct_text', 'correct_table') or type(target) is not int or target not in options
                                or entry.get('field_text_verified') is not True
                                or not isinstance(excerpt, str) or not excerpt.strip() or len(excerpt) > 100000):
                            raise ValueError('关联字段问题须逐项选择归属字段、填写修订正文中的完整对应文字，并明确核对整个问题区域')
                        table_matches = []
                        chosen_table_index = None
                        outside_source = part.get('source_kind') == 'outside_text'
                        if action == 'correct_table':
                            # 用户显式核对服务器确定的完整修订区域，不推测新文字在旧问题框中的位置。
                            field_box = bbox if expanded_scope else rect(other.get('bbox_pdf'))
                            table_sources = dict(zip(scope_indices or matching, replacements))
                            intersecting = [(index,t) for index,t in table_sources.items() if overlaps(field_box, t['bbox_pdf'])]
                            if mixed_sources:
                                required_table_indices = {index for index,_ in intersecting}
                                if not required_table_indices or any(not contains(field_box,t['bbox_pdf']) for _,t in intersecting):
                                    raise ValueError('混合区域必须完整包含所核对的原表')
                            if outside_source:
                                from agent.agent_backend.utils.parser.drug_supplement_pdf_parser import manual_table_field_pairs
                                for _, source_table in intersecting:
                                    source_pairs = manual_table_field_pairs(source_table)
                                    if source_pairs is None or any(number == target for number,_ in source_pairs):
                                        raise ValueError('表内标签必须明确，表外字段不能与表内字段重复归属')
                                intersecting = []
                            if intersecting:
                                from agent.agent_backend.utils.parser.drug_supplement_pdf_parser import manual_table_field_pairs
                                if len(intersecting) > 1 or mixed_sources:
                                    # 只处理完整包含各独立表格、且已逐字确认表外为空的区域。
                                    # 不能凭跨界OCR词框推断表外文字，也不能将两表合成一表。
                                    required_table_indices = {index for index,_ in intersecting}
                                    chosen_table_index = part.get('table_index')
                                    if (assignments is None or type(chosen_table_index) is not int
                                            or chosen_table_index not in required_table_indices
                                            or not record.get('outside_text_verified') or (not mixed_sources and record.get('outside_text','').strip())
                                            or any(not contains(field_box,t['bbox_pdf']) for _,t in intersecting)):
                                        raise ValueError('跨表字段须逐段选择原表，完整核对所有涉及表格，且表外确认为空；局部跨界或含表外正文不能猜测归属')
                                    all_pairs = [manual_table_field_pairs(t) for _,t in intersecting]
                                    if any(value is None for value in all_pairs) or sum(n == target for value in all_pairs for n,_ in value) != 1:
                                        raise ValueError('各独立表的标签和值必须明确且字段归属唯一，不能用重复标签或不明结构解除跨表问题')
                                    selected_table = table_sources[chosen_table_index]
                                    assigned_table_indices.add(chosen_table_index)
                                else:
                                    chosen_table_index, selected_table = intersecting[0]
                                    if not contains(selected_table['bbox_pdf'], field_box):
                                        raise ValueError('字段问题跨越表格边界，请分别核对完整来源，不能整体猜测归属')
                                    if part.get('table_index') is not None and (type(part['table_index']) is not int or part['table_index'] != chosen_table_index):
                                        raise ValueError('所选原表与当前问题区域不一致')
                                pairs = manual_table_field_pairs(selected_table)
                                if pairs is None:
                                    raise ValueError('表格标签和值仍无法唯一对应，请核对行列及合并关系')
                                table_matches = [number for number, value in pairs if excerpt.strip() in value]
                                if (table_matches != [target] or sum(number == target for number, _ in pairs) != 1
                                        or excerpt.strip() in record.get('outside_text', '')):
                                    raise ValueError('对应文字必须唯一属于修订表格中的所选字段，不能重复或混用表外文字')
                                if expanded_scope:
                                    full_value = next(value for number, value in pairs if number == target)
                                    if excerpt.strip() != full_value.strip():
                                        raise ValueError('完整区域核对须逐字包含每个表格字段的完整值，不能仅摘录片段')
                                    checked_table_values.add((chosen_table_index, target))
                        if action == 'correct_table' and not table_matches and (
                                not record.get('outside_text_verified')
                                or (not outside_source and any(overlaps(rect(other.get('bbox_pdf')), table['bbox_pdf'])
                                       for table in chunks[ci].get('tables', [])))):
                            raise ValueError('关联字段必须完整位于表外正文；表内或跨表文字不能用表外确认解除')
                        if not table_matches and (part.get('source_kind') == 'table' or part.get('table_index') is not None):
                            raise ValueError('指定的表格来源未与实际修订文字匹配')
                        # 人工核对没有逐字坐标，不凭旧OCR词框猜测新文字归属。
                        from agent.agent_backend.utils.parser.drug_supplement_pdf_parser import _page_form_lines
                        line = {'text': record.get('outside_text', '') if action == 'correct_table' else record['text'], 'bbox': bbox, 'source': 'manual_revision',
                                'position_kind': 'review_region_not_glyph',
                                **({'manual_continuation_item_no': continuation} if continuation is not None else {})}
                        mapped = _page_form_lines({'page': chunk.get('page'), 'tables': [], 'words': [], 'lines': [line]}, 72)
                        match_text = excerpt.strip()
                        if assignments is not None and not table_matches:
                            from agent.agent_backend.utils.parser.form_field_semantics import heading
                            part_heading = heading(match_text)
                            if part_heading:
                                # 下游已去掉明确标题；原摘录仍须完整存在于修订正文，
                                # 不允许凭空补标题或仅用选择值强制改变字段。
                                if part_heading[0] != target or line['text'].count(match_text) != 1:
                                    raise ValueError('分段标题及完整文字须与本次修订正文一致')
                                match_text = part_heading[2].strip()
                                if not match_text:
                                    raise ValueError('分段只有标题，缺少需要核对的实际内容')
                        matches = [line_part for line_part in mapped if match_text in line_part.text]
                        matched_target = matches[0].manual_target_item_no if len(matches) == 1 else None
                        if len(matches) == 1 and not matched_target:
                            # 单标题仍走原几何路径；这里只核实明确标题与值。
                            from agent.agent_backend.utils.parser.form_field_semantics import heading
                            single = heading(matches[0].text)
                            if single and match_text in single[2]:
                                matched_target = single[0]
                        if not table_matches and (len(matches) != 1 or matched_target != target):
                            raise ValueError('对应文字须唯一位于所选字段的修订正文中；请保留明确字段标题并核对归属')
                        if assignments is not None:
                            source_id = ('table', chosen_table_index, next(i for i, pair in enumerate(pairs) if pair[0] == target)) if table_matches else ('text', mapped.index(matches[0]))
                            source_text = next(value for number, value in pairs if number == target) if table_matches else matches[0].text
                            if source_text.count(match_text) != 1:
                                raise ValueError('分段文字在对应字段中不唯一，请扩大摘录以明确来源')
                            start = source_text.index(match_text)
                            end = start + len(match_text)
                            if any(identity == source_id and start < old_end and end > old_start
                                   for identity, old_start, old_end in assigned_spans):
                                raise ValueError('各段不能重复使用或交叠引用同一处修订文字')
                            if assigned_spans:
                                previous_id, _, previous_end = assigned_spans[-1]
                                if source_id[0] == previous_id[0] and (source_id[1:], start) < (previous_id[1:], previous_end):
                                    raise ValueError('各段顺序须与完整修订正文或表格中的顺序一致')
                            assigned_spans.append((source_id, start, end))
                        checked_parts.append({'target_item_no': target, 'reviewed_text': excerpt.strip(),
                                              **({'source_kind': part['source_kind']} if part.get('source_kind') in ('table','outside_text') else {}),
                                              **({'table_index': chosen_table_index} if part.get('table_index') is not None and table_matches else {})})
                    if assigned_table_indices != required_table_indices:
                        raise ValueError('跨表关联核对遗漏涉及的原表，请逐个核对，不得只处理其中一表')
                    if expanded_scope:
                        # 仅“每张表选过一段”不足以证明整个区域完成，必须逐字段完整覆盖。
                        from agent.agent_backend.utils.parser.drug_supplement_pdf_parser import manual_table_field_pairs
                        expected_values = set()
                        for index, source_table in table_sources.items():
                            source_pairs = manual_table_field_pairs(source_table)
                            if source_pairs is None or len({number for number, _ in source_pairs}) != len(source_pairs):
                                raise ValueError('完整区域内存在无法唯一归属的表格字段，不能解除关联问题')
                            expected_values.update((index, number) for number, _ in source_pairs)
                        if checked_table_values != expected_values:
                            raise ValueError('完整区域内仍有表格字段未逐段核对，不能解除关联问题')
                        related_record['review_scope'] = review_scope
                    if assignments is not None:
                        related_record.update(field_assignments=checked_parts, reviewed_text=complete.strip(), field_text_verified=True)
                    else:
                        related_record.update(**checked_parts[0], field_text_verified=True)
                related_records.append(related_record)
                reviewed.add(key)
            if related:
                record['related_issues'] = related_records
                resolved.update(reviewed)
            chunk['_manual_content_changed'] = True
            if any(overlaps(bbox, old) for old in chunk.get('_manual_regions', [])):
                raise ValueError('多项内容修订区域重叠，不能按顺序覆盖。请修改原修订项，或撤销重叠项后统一核对。')
            chunk.setdefault('_manual_regions', []).append(bbox)
        else:
            raise ValueError('不支持的核对动作')
        resolved.add(item['issue_key'])
        saved.append(record)
    for ci, chunk in enumerate(output):
        if not chunk.pop('_manual_content_changed', False):
            continue
        tables = chunk.get('tables', [])
        elements = [(t['bbox_pdf'][1], t['bbox_pdf'][0], t['markdown']) for t in tables]
        outside_blocks = chunk.pop('_manual_outside_blocks', [])
        elements.extend((block['bbox'][1], block['bbox'][0], block['text']) for block in outside_blocks)
        regions = chunk.pop('_manual_regions', [])
        preserved_lines = []
        compact = lambda text: ''.join(str(text).split())
        # 行与词不对应时，词重建并不包含全部原文。只允许完整区域替换，
        # 其他位置保留原行；跨界行不能证明删改范围，拒绝而不是丢字。
        for line in chunks[ci].get('lines', []):
            box = rect(line.get('bbox'))
            members = sorted((w for w in chunks[ci].get('words', [])
                              if contains(box, w['bbox'])), key=lambda w: (w['bbox'][1], w['bbox'][0]))
            if compact(line.get('text', '')) == ''.join(compact(w['text']) for w in members):
                continue
            if any(contains(region, box) for region in regions):
                continue
            if any(overlaps(region, box) for region in regions):
                raise ValueError('修订区域截断无法逐词对应的原文行，请核对完整区域后重新处理')
            preserved_lines.append(deepcopy(line))
            elements.append((box[1], box[0], line.get('text', '')))
        # 修订后的副本按词框保留全部非表格内容，不按整行中心点丢弃表外文字。
        groups = []
        # 人工完整补录只有区域坐标，不是单词坐标；不得与邻近词按行距合并。
        def complete_manual_region(word):
            return (word.get('source') == 'manual_revision'
                    and word.get('position_kind') == 'review_region_not_glyph'
                    and not word.get('manual_target_item_no'))
        for word in sorted(chunk.get('words', []), key=lambda w: (w['bbox'][1], w['bbox'][0])):
            box = word['bbox']
            if any(contains(line['bbox'], box) for line in preserved_lines):
                continue
            if not any(contains(t['bbox_pdf'], box) for t in tables):
                if (not groups or complete_manual_region(word) or complete_manual_region(groups[-1][0])
                        or abs(box[1] - groups[-1][0]['bbox'][1]) > max(2, (box[3] - box[1]) * .5)):
                    groups.append([])
                groups[-1].append(word)
        body_lines = []
        for group in groups:
            group.sort(key=lambda w: w['bbox'][0])
            text = ' '.join(w['text'] for w in group)
            bbox = [min(w['bbox'][0] for w in group), min(w['bbox'][1] for w in group),
                    max(w['bbox'][2] for w in group), max(w['bbox'][3] for w in group)]
            elements.append((bbox[1], bbox[0], text))
            body_lines.append({'text': text, 'bbox': bbox, 'source': 'revision_view',
                               **({'source': 'manual_revision', 'position_kind': 'review_region_not_glyph'}
                                  if len(group) == 1 and complete_manual_region(group[0]) else {}),
                               **({'manual_continuation_item_no': group[0]['manual_continuation_item_no']}
                                  if len(group) == 1 and group[0].get('manual_continuation_item_no') else {})})
        chunk['text'] = '\n\n'.join(text for _, _, text in sorted(elements))
        chunk['raw_text'] = chunk['text']
        chunk['lines'] = preserved_lines + body_lines + outside_blocks
        chunk['lines'].extend({'text': t['markdown'], 'bbox': t['bbox_pdf'], 'source': t.get('source', 'native')}
                              for t in tables)
        chunk.pop('structured_data', None)
        chunk['manual_content_applied'] = True
        # 原始status/errors保持不变；是否就绪由独立的解决记录判定。
    # 完整表格修订能解决同一范围内结构诊断的重复表达，不能解除OCR覆盖、
    # 服务故障或其他区域问题；跨界正文仅在额外完整核对后才解除。
    for ci, table_box, region_box in repaired_tables:
        for issue in issues:
            if issue.get('chunk_index') != ci:
                continue
            bounds = issue.get('bbox_pdf')
            try:
                bounds = rect(bounds)
            except ValueError:
                continue
            if (issue.get('code') in ('table_review_required', 'table_structure_unresolved')
                    and contains(table_box, bounds)):
                resolved.add(issue['issue_key'])
            elif (region_box and issue.get('code') == 'text_assembly_unresolved'
                    and contains(region_box, bounds)):
                resolved.add(issue['issue_key'])
    # 完整逐格修订或明确核对的正文区域可处理其中的数值；全部候选
    # 必须各自落入已核对区域，不能凭页级总框清除未定位/未核对项。
    from agent.agent_backend.services.filing_numeric_revision import numeric_check_confirmed
    for issue in issues:
        ci, ei = issue.get('chunk_index'), issue.get('error_index')
        if issue.get('code') != 'numeric_uncertain' or type(ci) is not int or type(ei) is not int:
            continue
        boxes = [box for index, box, _ in repaired_tables if index == ci]
        boxes.extend(box for index, box in numeric_text_regions if index == ci)
        if not boxes or not 0 <= ci < len(chunks):
            continue
        errors = chunks[ci].get('errors', [])
        if not 0 <= ei < len(errors):
            continue
        checks = errors[ei].get('numeric_verification') or []
        try:
            if checks and all(numeric_check_confirmed(check) or
                              any(contains(box, rect(check.get('bbox_pdf'))) for box in boxes)
                              for check in checks):
                resolved.add(issue['issue_key'])
        except (ValueError, TypeError, AttributeError):
            pass  # 无法定位的原始证据保持阻塞。
    return output, resolved, saved


def apply_compatible_resolutions(chunks, issues, items, confirmations):
    """先在原地址应用数值确认，再重建结构；同一表的两类修订不得静默覆盖。"""
    from agent.agent_backend.services.filing_numeric_revision import apply_confirmations
    if not isinstance(items,list) or any(not isinstance(i,dict) for i in items):
        raise ValueError('核对记录必须为对象列表')
    numeric = apply_confirmations(chunks, confirmations)
    current = {issue['issue_key']: issue for issue in issues}
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or item.get('action') != 'correct_table':
            continue
        issue = current.get(item.get('issue_key'), {})
        ci = issue.get('chunk_index')
        bbox = rect(issue.get('bbox_pdf'))
        if type(ci) is int and 0 <= ci < len(chunks):
            scope = assembly_table_scope(chunks[ci], issue)
            if scope:
                bbox = scope['repair_bbox_pdf']
        for confirmation in confirmations:
            ni, ti, _ = map(int, confirmation['key'].split(':'))
            if ni == ci and overlaps(bbox, rect(chunks[ni]['tables'][ti]['bbox_pdf'])):
                raise ValueError('同一表格已有数值确认与结构修订，不能相互覆盖。请先在数值核对中撤销该表确认，再完整修订表格；当前输入未保存。')
    # 问题身份来自原始证据；数值确认可能改变表格顺序前必须先应用。
    target_items = [i for i in items if str(i.get('issue_key', '')).startswith('target:')]
    ordinary = [i for i in items if i not in target_items]
    if target_items:
        from agent.agent_backend.services.filing_review_targets import apply_targets
        by_key = {i['issue_key']: i for i in issues}
        for target_item in target_items:
            target = by_key.get(target_item.get('issue_key'), {})
            for confirmation in confirmations:
                ni, ti, _ = map(int, confirmation['key'].split(':'))
                if ni == target.get('chunk_index') and (target.get('target_kind') == 'chunk' or
                    (target.get('bbox_pdf') and overlaps(rect(target['bbox_pdf']), rect(chunks[ni]['tables'][ti]['bbox_pdf'])))):
                    raise ValueError('对象修订与已有数值确认不能相互覆盖，请先撤销旧数值确认')
        for item in ordinary:
            issue = by_key.get(item.get('issue_key'), {})
            for target_item in target_items:
                target = by_key.get(target_item.get('issue_key'), {})
                if (issue.get('chunk_index') == target.get('chunk_index') and issue.get('bbox_pdf') and target.get('bbox_pdf')
                        and overlaps(rect(issue.get('repair_bbox_pdf') or issue['bbox_pdf']), rect(target['bbox_pdf']))):
                    raise ValueError('已有区域修订与当前对象重叠，请修改已有修订；原确认不能继续覆盖新内容')
        updated, resolved, saved = apply_resolutions(numeric, issues, ordinary)
        updated, target_resolved, target_saved = apply_targets(updated, issues, target_items)
        return updated, resolved | target_resolved, saved + target_saved
    return apply_resolutions(numeric, issues, items)
