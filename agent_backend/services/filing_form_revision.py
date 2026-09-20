"""申请表修订只保存到现有 JSON；数组作为整体保护，不依赖行号。"""
from copy import deepcopy
from agent.agent_backend.utils.parser.form_field_semantics import normalized_validity


def values(field):
    out = {k: field.get(k, [] if k != 'value' else '') for k in ('value', 'selected_values', 'table_rows')}
    for key, value in field.get('sub_fields', {}).items():
        if key != 'columns':
            out['sub_fields.' + key] = value
    return out


def put(field, path, value):
    if path.startswith('sub_fields.'):
        field.setdefault('sub_fields', {})[path.split('.', 1)[1]] = deepcopy(value)
    else:
        field[path] = deepcopy(value)


def present(value):
    return value not in ('', None, [], {})


def legacy_party_notes(form):
    """旧contact/address没有逐值原文时，仅提示含义未定，不猜测迁移。"""
    notes = []
    for key in ('item_30_applicant_info','item_31_manufacturer_info','item_32_cro_info'):
        field = form.get(key) or {}
        for sub in ('contact','address','license_no'):
            value = (field.get('sub_fields') or {}).get(sub)
            source = (field.get('value_sources') or {}).get('sub_fields.'+sub) or {}
            if present(value) and (not source or source.get('recognition_status') == 'legacy_unspecified'):
                notes.append({'field':key, 'path':'sub_fields.'+sub, 'value':value,
                              'reason':'历史字段缺少明确语义依据，原值和来源保留，具体角色、地址用途或代码类别待核对。'})
    return notes


def value_source(field, path, value):
    source = deepcopy(field.get('value_sources', {}).get(path, {}))
    for meta, default in [('source_text', ''), ('source_file', ''), ('source_file_id', ''),
                          ('source_regions', []), ('parse_confidence', 0)]:
        source.setdefault(meta, deepcopy(field.get(meta, default)))
    source['value'] = deepcopy(value)
    source.setdefault('recognition_status', 'extracted' if present(value) else field.get('recognition_status', 'unrecognized'))
    if (path in ('sub_fields.contact','sub_fields.address') and present(value)
            and any(k in field.get('sub_fields',{}) for k in ('applicant_name','manufacturer_name','organization_name'))
            and not field.get('value_sources',{}).get(path)):
        source['recognition_status'] = 'legacy_unspecified'
        source['reason'] = '历史人员/地址字段缺少逐值依据，具体含义待核对，保留原值。'
    if path == 'sub_fields.license_no' and present(value) and '许可证' not in source.get('source_text', ''):
        source['recognition_status'] = 'legacy_unspecified'
        source['reason'] = '历史license_no缺少许可证依据，原值及来源保留，代码类别待核对。'
    return source


def sync_field_source(field):
    """字段级摘要只代表当前值；不同子字段来源在 value_sources 中逐项查看。"""
    paths = values(field)
    primary = 'value' if field.get('field_type') in ('input', 'textarea', 'radio', 'radio_with_input') else None
    sources = [field.get('value_sources', {}).get(primary)] if primary else [
        source for path, source in field.get('value_sources', {}).items()
        if path != 'sub_fields.validity_unit' and (present(paths.get(path)) or path in field.get('manual_paths', []))]
    sources = [s for s in sources if s]
    if not sources:
        return
    identities = {(s.get('source_file_id'), s.get('source_file'), s.get('recognition_status')) for s in sources}
    if len(identities) == 1:
        source = sources[0]
        for meta in ('source_file', 'source_file_id', 'source_text', 'source_regions', 'parse_confidence', 'recognition_status'):
            if meta in source:
                field[meta] = deepcopy(source[meta])
    else:
        field.update(source_file='', source_file_id='', source_text='', source_regions=[],
                     parse_confidence=0, recognition_status='mixed_sources')


def save_revision(old, submitted, resolutions=None, edited_paths=None, expected_values=None, require_loaded=False):
    result = deepcopy(old)
    for key, incoming in submitted.items():
        if not isinstance(incoming, dict) or 'field_type' not in incoming:
            if key not in result:
                result[key] = deepcopy(incoming)
            continue
        previous = old.get(key, {})
        field = deepcopy(previous or incoming)
        before = values(previous)
        incoming_values = values(incoming)
        loaded = (expected_values or {}).get(key)
        if not isinstance(loaded, dict):
            loaded = {p:s['value'] for p,s in incoming.get('value_sources', {}).items() if 'value' in s}
            if not require_loaded:
                loaded = {**before, **loaded}
        if edited_paths is not None:
            edits = (edited_paths or {}).get(key, [])
        else:
            edits = [p for p,v in incoming_values.items() if p in loaded and v != loaded[p]]
            unknown = [p for p,v in incoming_values.items() if p not in loaded and v != before.get(p, [] if p in ('selected_values','table_rows') else '')]
            if previous and unknown:
                raise ValueError('旧保存请求缺少打开时的字段值，请刷新后重新编辑；未覆盖当前数据。')
            if not previous:
                edits.extend(unknown)
        paths = list(previous.get('manual_paths', []))
        # 历史整字段人工标记没有细粒度信息时，保守保护已有字段。
        if previous.get('manual_modified') and 'manual_paths' not in previous:
            paths = list(before)
        # 决策也会写入：在同一项目锁/事务内，先核对打开时的值、人工来源和候选，
        # 再处理任何编辑。不能用本次编辑后的值来证明旧决策仍然有效。
        for path, decision in (resolutions or {}).get(key, {}).items():
            candidate = previous.get('candidates', {}).get(path)
            seen_candidate = (decision.get('candidate') if isinstance(decision, dict)
                              else incoming.get('candidates', {}).get(path))
            if not candidate or seen_candidate != candidate:
                raise ValueError('候选结果已更新，请刷新后重新核对；本页输入仍保留。')
            seen_source = incoming.get('value_sources', {}).get(path)
            if require_loaded and (path not in loaded or not seen_source or
                                   not ('manual_paths' in incoming or 'manual_modified' in incoming)):
                raise ValueError('旧候选请求缺少可靠的决策上下文，请刷新后重新核对；未覆盖当前数据。')
            seen_paths = incoming.get('manual_paths', list(incoming_values) if incoming.get('manual_modified') else [])
            if (loaded.get(path) != before.get(path)
                    or (path in seen_paths) != (path in paths)
                    or (seen_source is not None and seen_source != previous.get('value_sources', {}).get(path))):
                raise ValueError(f'字段 {key}.{path} 已被另一页面修改，请刷新并核对最新内容后再决定；本页输入仍保留。')
        for path in edits:
            if path not in incoming_values:
                raise ValueError('本次编辑路径无对应值，请刷新后重试。')
            value = incoming_values[path]
            if path == 'sub_fields.validity_unit' and not previous and value == '月':
                continue
            current = before.get(path, [] if path in ('selected_values','table_rows') else '')
            if previous and path not in loaded:
                raise ValueError('本次编辑缺少打开时的值，请刷新后重新编辑。')
            if current != loaded.get(path, current) and current != value:
                if path in paths or value_source(previous, path, current).get('recognition_status') == 'manual':
                    raise ValueError(f'字段 {key}.{path} 已被另一页面修改，请核对最新值后再保存；本页输入仍保留。')
                # 自动解析先完成时保留人工输入，并公开新自动值供核对。
                field.setdefault('candidates', {})[path] = value_source(previous, path, current)
            previous_source = value_source(field, path, before.get(path))
            put(field, path, value)
            field.setdefault('value_sources', {})[path] = {
                'value': deepcopy(value), 'recognition_status': 'manual', 'source_file': '',
                'source_file_id': '', 'source_text': '', 'source_regions': [], 'parse_confidence': None,
                'edited_from': previous_source,
            }
            if path not in paths:
                paths.append(path)
        for path, action in (resolutions or {}).get(key, {}).items():
            candidate = field.get('candidates', {}).get(path)
            if isinstance(action, dict):
                if action.get('candidate') != candidate:
                    raise ValueError('候选结果已更新，请刷新后重新核对。')
                action = action.get('action')
            if not candidate or action not in ('adopt', 'keep'):
                continue
            field.setdefault('resolution_history', []).append({'path': path, 'action': action, 'candidate': deepcopy(candidate)})
            if action == 'adopt':
                field.setdefault('value_history', []).append({
                    'path': path, **value_source(field, path, values(field).get(path)), 'historical': True})
                put(field, path, candidate['value'])
                field.setdefault('value_sources', {})[path] = deepcopy(candidate)
                paths = [p for p in paths if p != path]
            else:
                if path not in paths:
                    paths.append(path)
            del field['candidates'][path]
        field['manual_paths'] = paths
        field['manual_modified'] = bool(paths)
        sync_field_source(field)
        result[key] = field
    return result


def interrupted_source_scope(previous, fresh, diagnostics):
    """仅同件未识别值可回用；局部错误不得扩张为整页或整表失败。"""
    if (not previous.get('source_file_id') or previous.get('source_file_id') != fresh.get('source_file_id')
            or present(fresh.get('value')) or fresh.get('recognition_status') != 'unrecognized'
            or diagnostics.get('status') not in ('partial', 'failed')):
        return None
    errors = [e for e in diagnostics.get('errors', []) if isinstance(e, dict)]
    matches = []
    pages = set()
    for region in previous.get('source_regions', []):
        page = region.get('page')
        if not isinstance(page, int):
            continue
        page_errors = [e for e in errors if e.get('page') == page]
        # 有区域错误时，failed_pages 是汇总，不能据此保留同页其他区域的旧值。
        if not page_errors and page in diagnostics.get('failed_pages', []):
            pages.add(page)
        for error in page_errors:
            if not (str(error.get('stage', '')).startswith('ocr') or
                    str(error.get('code', '')).startswith('ocr_') or error.get('stage') == 'page'):
                continue
            box, old_box = error.get('bbox_pdf'), region.get('bbox_pdf')
            if box:
                if not old_box or len(box) != 4 or len(old_box) != 4:
                    continue
                if (error.get('coordinate_unit', 'pdf_point') != 'pdf_point' or
                        region.get('coordinate_unit', 'pdf_point') != 'pdf_point'):
                    continue
                if not (max(box[0], old_box[0]) < min(box[2], old_box[2]) and
                        max(box[1], old_box[1]) < min(box[3], old_box[3])):
                    continue
            pages.add(page)
            if error not in matches:
                matches.append(deepcopy(error))
    if not pages:
        return None
    return {'status': diagnostics['status'], 'failed_pages': sorted(pages), 'errors': matches}


def previous_result(source, scope):
    """当前保留值明确属于此前提取，重试不会嵌套复制上一轮失败记录。"""
    original = deepcopy(source.get('previous_source', source))
    return {**deepcopy(original), 'recognition_status': 'previous_result', 'historical': True,
            'previous_source': original, 'reparse_diagnostics': deepcopy(scope)}


def merge_parse(old, parsed, mode='supplement', diagnostics=None):
    """diagnostics 为本次解析诊断；返回字段中的此前结果不改变本次 partial 状态。"""
    if mode not in ('replace', 'reparse', 'supplement'):
        raise ValueError('unknown parse mode')
    result = deepcopy(parsed)
    for key, previous in old.items():
        if not isinstance(previous, dict) or 'field_type' not in previous:
            if key == 'latest_task_attempts':
                result[key] = deepcopy(previous)
            if mode == 'supplement':
                # 完整空骨架同样包含表单类型等元数据；空值不代表本次有更新。
                if key not in result or (present(previous) and not present(result[key])):
                    result[key] = deepcopy(previous)
            continue
        fresh = parsed.get(key, {})
        # OCR 增强返回完整空骨架；没有更新的字段必须连同选项、位置及历史整体保留。
        if (mode == 'supplement' and not str(fresh.get('source_text') or '').strip()
                and not fresh.get('source_regions')
                and not any(present(value) for path, value in values(fresh).items()
                            if not (path == 'sub_fields.validity_unit' and value == '月'))):
            result[key] = deepcopy(previous)
            continue
        field = deepcopy(fresh or previous)
        field['candidates'] = {}
        field['resolution_history'] = deepcopy(previous.get('resolution_history', []))
        field['value_history'] = deepcopy(previous.get('value_history', []))
        protected = previous.get('manual_paths', list(values(previous)) if previous.get('manual_modified') else [])
        for path in dict.fromkeys([*values(previous), *values(fresh)]):
            value = values(fresh).get(path, [] if path in ('selected_values', 'table_rows') else '')
            current = values(previous).get(path, [] if path in ('selected_values', 'table_rows') else '')
            source = value_source(fresh, path, value)
            old_source = value_source(previous, path, current)
            score, old_score = float(source.get('parse_confidence') or 0), float(old_source.get('parse_confidence') or 0)
            chosen = source
            scope = interrupted_source_scope(old_source, source, diagnostics or {}) if mode == 'reparse' else None
            if path in protected:
                chosen = old_source
                if chosen.get('recognition_status') != 'manual':
                    chosen = {'value': deepcopy(current), 'recognition_status': 'manual', 'source_file': '',
                              'source_file_id': '', 'source_text': '', 'source_regions': [], 'parse_confidence': None,
                              'edited_from': old_source}
                candidate = previous.get('candidates', {}).get(path)
                candidate_scope = (interrupted_source_scope(candidate, source, diagnostics or {})
                                   if mode == 'reparse' and candidate else None)
                if candidate_scope:
                    field['candidates'][path] = previous_result(candidate, candidate_scope)
                elif not (key == 'item_15_validity_period' and path in (
                        'sub_fields.original_validity_period', 'sub_fields.proposed_validity_period')
                        and normalized_validity(value) == normalized_validity(current)) and value != current:
                    field.setdefault('candidates', {})[path] = source
            elif scope and present(current):
                chosen = previous_result(old_source, scope)
            elif path == 'sub_fields.license_no' and old_source.get('recognition_status') == 'legacy_unspecified' and present(current):
                chosen = old_source
                if present(value) and current != value:
                    field.setdefault('candidates', {})[path] = source
            elif mode == 'supplement' and source.get('recognition_status') not in ('explicit_blank', 'conflict'):
                if not present(value) or score < .5 or (present(current) and score < old_score):
                    chosen = old_source
            if mode == 'replace' and (current != value or old_source.get('source_file_id') != source.get('source_file_id')):
                field['value_history'].append({'path': path, **old_source, 'historical': True})
            elif mode == 'reparse' and current != value:
                history = {'path': path, **deepcopy(old_source.get('previous_source', old_source)), 'historical': True}
                if history not in field['value_history']:
                    field['value_history'].append(history)
            put(field, path, chosen['value'])
            field.setdefault('value_sources', {})[path] = chosen
        field['manual_paths'] = list(protected)
        field['manual_modified'] = bool(protected)
        sync_field_source(field)
        result[key] = field
    return result


def validity_cross_checks(form, materials):
    """仅比较申请表有效期；其他文件提供候选，绝不冒充申请表填写值。"""
    import re
    from agent.agent_backend.utils.parser.form_field_semantics import validity_evidence, normalized_validity
    field = form.get('item_15_validity_period')
    if not isinstance(field, dict):
        return form
    checks = []
    for material in materials:
        source = material.get('text', '')
        detail = validity_evidence(source)
        extracted = dict(detail['values'])
        if material.get('is_approval') and not detail['issues'] and detail['reported_value']:
            extracted.setdefault('original_validity_period', detail['reported_value'])
        for issue in detail['issues']:
            checks.append({'path':'', 'application_value':'', 'value':'', 'status':issue['reason'],
                           'source_text':source, 'source_file':material.get('source_file',''),
                           'source_regions':material.get('source_regions',[]), 'evidence':issue})
        for path, value in extracted.items():
            application_value = field.get('sub_fields', {}).get(path, '')
            equivalent = normalized_validity
            status = '缺失，候选补充值待确认' if not application_value else ('一致' if equivalent(application_value) == equivalent(value) else '存在冲突，需人工确认')
            checks.append({'path': path, 'application_value': application_value, 'value': value,
                           'source_text': source, 'source_file': material.get('source_file', ''),
                           'source_regions': material.get('source_regions', []), 'status': status})
    field['cross_checks'] = checks
    return form
