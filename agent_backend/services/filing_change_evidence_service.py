"""从参与本轮判断的事实位置整理证据，正文快照随现有结果 JSON 保存。"""
from copy import deepcopy


class FilingChangeEvidenceService:
    @staticmethod
    def _sanitize_text(value, max_len=None):
        text = ''.join(c for c in str(value if value is not None else '') if c in '\t\n\r' or ord(c) >= 32)
        return text[:max_len] if max_len else text

    def collect(self, submissions, references, matched_rules, parsed_submission_map=None, form_json=None, module_results=None):
        docs = {str(s.get('doc_id')): s for s in submissions or []}
        refs, modules = [], {}
        seen = {}

        def evidence(node):
            if isinstance(node, list):
                for child in node: yield from evidence(child)
            elif isinstance(node, dict):
                source = node.get('source')
                if isinstance(source, dict) and (source.get('doc_id') or source.get('file_name')):
                    yield node, source
                elif node.get('source_doc_id'):
                    yield node, node.get('source_position') or {'doc_id': node['source_doc_id'], 'page': node.get('source_page'),
                                 'section': node.get('source_caption'), 'table': node.get('source_table_index'),
                                 'row': node.get('source_row_index') + 1 if isinstance(node.get('source_row_index'), int) else None, 'cell': node.get('source_column_index') + 1 if isinstance(node.get('source_column_index'), int) else None}
                else:
                    for child in node.values():
                        if isinstance(child, (dict, list)): yield from evidence(child)

        def build(node, source):
            source = deepcopy(source)
            doc_id = str(source.get('doc_id') or '')
            form = doc_id == 'application_form'
            doc = docs.get(doc_id, {})
            title = '本轮申请表' if form else doc.get('file_name') or source.get('file_name') or '来源文件未记录'
            if title.lower().endswith(('.doc', '.docx')): source.pop('page', None)
            parts = []
            for key, label in [('field', '字段'), ('subfield', '子字段'), ('page', '页'), ('section', '章节'),
                               ('table', '表'), ('row', '行'), ('cell', '列'), ('line', '文本行')]:
                if source.get(key) is not None and source.get(key) != '':
                    display = source[key]
                    if form and key == 'field':
                        display = ((form_json or {}).get(str(display)) or {}).get('field_name') or display
                    if form and key == 'subfield':
                        display = {'selected_values': '所选事项', 'original_validity_period': '原有效期', 'proposed_validity_period': '拟延长后有效期', 'storage_condition': '贮藏条件', 'applicant_name': '申请人名称'}.get(str(display), display)
                    parts.append(label + ' ' + str(display))
            value = node.get('raw_value', node.get('result_text', node.get('value', node.get('text', ''))))
            # 同时保留上下文、精确位置和原值，历史查看不会请求当前解析版本。
            text = self._sanitize_text(value)
            if not text: return None
            return {'source_type': 'application_form' if form else 'submission', 'doc_id': doc_id,
                    'title': title, 'position': ' / '.join(parts) or '具体位置未记录', 'snippet': text,
                    'source': source, 'fact': deepcopy(node), 'availability': node.get('availability') or node.get('available_status') or '本轮判断所用快照',
                    'latest_attempt': deepcopy(doc.get('latest_attempt') or {}),
                    'access_mode': 'snapshot', 'access_message': '本轮证据内容快照；不跳转到当前材料新版本'}

        inputs = {**(module_results or {}), **{'rule:' + str(r.get('rule_code')): r for r in matched_rules or []}}
        for module, data in inputs.items():
            local = []
            for node, source in evidence(data):
                ref = build(node, source)
                if not ref: continue
                # 不按短摘要去重；批次/条件/事实不同全部保留。
                key = repr((ref['source'], ref['fact']))
                if key not in seen:
                    ref['evidence_id'] = 'E' + str(len(refs) + 1)
                    seen[key] = ref
                    refs.append(ref)
                local.append(seen[key])
            modules[module] = list({x['evidence_id']: x for x in local}.values())
        for rule in matched_rules or []:
            rule['evidence_ids'] = [x['evidence_id'] for x in modules.get('rule:' + str(rule.get('rule_code')), [])]
        category = (form_json or {}).get('item_5_application_matter_category') or {}
        if category.get('selected_values'):
            ref = build({'raw_value': category['selected_values']}, {'doc_id': 'application_form', 'field': 'item_5_application_matter_category', 'subfield': 'selected_values'})
            ref['evidence_id'] = 'E' + str(len(refs) + 1)
            refs.append(ref); modules['change_category_suggestion'] = [ref]
        return {'evidence_refs': refs, 'module_evidence': modules, 'matched_rules': matched_rules or []}
