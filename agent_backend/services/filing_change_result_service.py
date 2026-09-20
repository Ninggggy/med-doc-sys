"""同一轮结果的汇总与读取；仅使用已有 JSON，不修改历史业务结论。"""
from copy import deepcopy


UNKNOWN = '历史结果未记录'


def aggregate_result(review, technical, rules):
    checks, issues = [], []

    def add(module, status, reason, evidence=None, kind=None):
        if status not in ('通过', '不适用', '发现问题', '证据不足', '解析异常', '执行失败'):
            reason = '未识别的结果状态，需人工核对：' + str(status) + '；' + reason
            status = '证据不足'
        item = {'module': module, 'status': status, 'reason': reason,
                'evidence': evidence or []}
        checks.append(item)
        if status not in ('通过', '不适用'):
            item = {**item, 'kind': kind or ('execution_error' if status == '执行失败' else 'technical_issue' if status == '发现问题' else 'content_missing')}
            item['required_action'] = (
                '核对原件或重试解析，必要时人工核查' if item['kind'] == 'parse_error' else
                '重试该检查并人工核查，不能认定申请人未提交' if status == '执行失败' else
                '补充所列类别材料' if item['kind'] == 'file_missing' else
                '核对双方材料及具体值，说明差异或更正对应内容' if item['kind'] == 'conflict' else
                '核对具体技术问题并补充相应处理说明或论证' if item['kind'] == 'technical_issue' else
                '核对所列材料并补充对应字段、数据或论证')
            issues.append(item)

    category = technical.get('change_category_suggestion') or {}
    if (technical.get('change_identification') or {}).get('need_manual_review') or category.get('need_manual_review'):
        add('变更事项类别', '证据不足', category.get('reason') or '申请事项及其分类依据需人工确认', category.get('evidence'))

    formal = review.get('formal_review') or {}
    for key, kind, state in [('missing_materials', 'file_missing', '发现问题'),
                             ('content_missing', 'content_missing', '证据不足'),
                             ('parse_issues', 'parse_error', '解析异常')]:
        if key not in formal or formal[key] is None:
            add('形式审查', '证据不足', key + '：检查结果未记录')
        for item in formal.get(key, []) or []:
            add('形式审查', state, str(item.get('message') or item.get('label') or item) if isinstance(item, dict) else str(item), [item], kind)
    for item in formal.get('not_applicable', []) or []:
        add('形式审查', '不适用', str(item), [item])
    if formal.get('result') == '通过' and not any(i['module'] == '形式审查' for i in issues):
        add('形式审查', '通过', '必需材料及内容检查已完成')
    elif not any(i['module'] == '形式审查' for i in issues):
        add('形式审查', '证据不足', str(formal.get('result') or '模块未执行或结果为空'))

    comparisons = (review.get('consistency_check') or {}).get('comparisons')
    if not comparisons:
        add('一致性', '证据不足', '缺少逐项一致性核验结果')
    for item in comparisons or []:
        ev = [x for x in (item.get('left'), item.get('right')) if x]
        values = '；'.join(str((x.get('source') or {}).get('file_name', '来源未记录')) + '：' + str(x.get('raw_value', '未记录')) for x in ev)
        state = {'一致': '通过', '前后变更': '通过', '不适用': '不适用', '不一致': '发现问题'}.get(item.get('status'), '证据不足')
        if state == '通过' and (len(ev) != 2 or any(x.get('raw_value') in (None, '') or not x.get('source') for x in ev)):
            state = '证据不足'
        if state == '不适用' and not item.get('reason'):
            state = '证据不足'
        add('一致性', state,
            str(item.get('field_label') or item.get('field')) + '：' + item.get('reason', '') + '；' + values, ev,
            'conflict' if item.get('status') == '不一致' else 'content_missing')
    for key, label, success in [('quality_standard_check', '质量', ('基本符合', '通过')),
                                ('stability_trend_analysis', '稳定性', ('支持延长', '通过'))]:
        data = technical.get(key) or {}
        status = data.get('result')
        evidence = data.get('items') or data.get('records') or data.get('evidence') or []
        state = ('执行失败' if data.get('execution_status') == 'failed' else
                 '不适用' if status == '不适用' and data.get('reason') else
                 '发现问题' if status in ('存在不一致', '不支持延长', '发现问题') else
                 '通过' if status in success and evidence else '证据不足')
        reason = data.get('summary') or data.get('reason') or data.get('execution_error') or str(status or '模块未执行或结果为空')
        details = data.get('manual_review_items') or data.get('missing_data') or data.get('non_compliance_items') or data.get('risk_items') or []
        add(label, state, label + '：' + reason + ('；' + '；'.join(map(str, details)) if details else ''), evidence,
            'conflict' if status == '存在不一致' else None)
    results = review.get('rule_results') or []
    expected = [r for r in rules if r.get('enabled') is not False]
    if not expected:
        add('规则', '证据不足', '未记录本轮所需适用规则范围，不能按空规则通过')
    for rule in expected:
        if not any(r.get('rule_code') == rule.get('rule_code') for r in results):
            add(str(rule.get('rule_code')), '证据不足', '所需规则未执行或结果未记录')
    for rule in results:
        name = str(rule.get('rule_code', '规则'))
        if rule.get('execution_status') != 'completed':
            add(name, '执行失败', rule.get('review_conclusion') or '规则执行未完成')
            continue
        if not rule.get('subchecks'):
            add(name, '证据不足', '规则没有实际子检查结果')
        for sub in rule.get('subchecks') or []:
            state = sub.get('status') or '证据不足'
            if state == '通过' and not sub.get('evidence'):
                state = '证据不足'
            if state == '不适用' and not sub.get('reason'):
                state = '证据不足'
            kind = ('conflict' if name == 'TVS-CONS-001' and state == '发现问题' else
                    'file_missing' if name == 'TVS-FR-001' and sub.get('name') == '文件齐全性' and state == '发现问题' else
                    'content_missing' if name == 'TVS-FORM-001' and state == '发现问题' else None)
            add(name, state, str(sub.get('name', '')) + '：' + str(sub.get('reason') or '缺少理由或证据'), sub.get('evidence'), kind)
    states = {x['status'] for x in checks}
    support = '存在已确认问题' if '发现问题' in states else ('证据不足，尚不能支持申请' if issues else '现有证据支持继续审评')
    actions = []
    if '发现问题' in states: actions.append('核对并处理已确认问题')
    if '证据不足' in states: actions.append('补充对应材料、数据或论证')
    if states & {'解析异常', '执行失败'}: actions.append('重试或人工核查异常')
    if not actions: actions.append('继续审评并由审评员复核')
    points = []
    for module in dict.fromkeys(x['module'] for x in issues):
        group = [x for x in issues if x['module'] == module]
        reason = group[0]['reason']
        # 摘要呈现主因，所有逐批/逐条件细节仍在issues与规则明细中完整保留。
        if len(reason) > 180:
            reason = reason.split('；')[0][:180] + '（完整明细见对应检查）'
        points.append(module + '：' + reason + (f'；另有{len(group)-1}项待处理，见明细' if len(group) > 1 else ''))
    summary = '；'.join(points) or '所需模块及全部适用规则已有实际结果和支持证据。'
    groups = {kind: [i for i in issues if i['kind'] == kind] for kind in ('file_missing', 'content_missing', 'parse_error', 'conflict')}
    counts = {k: len(v) for k, v in groups.items()}
    for key, kind in [('missing_materials', 'file_missing'), ('content_missing', 'content_missing'), ('parse_issues', 'parse_error')]:
        if key not in formal or formal[key] is None: counts[kind] = None
    if not comparisons: counts['conflict'] = None
    return {'summary_points': points or [summary], 'checks': checks, 'issues': issues, 'issue_counts': counts,
            'issue_details': groups, 'technical_support': {'status': support, 'checks': checks},
            'recommended_action': '；'.join(actions), 'overall_conclusion': {
                'result': 'AI 初步建议：需补正或人工确认' if issues else 'AI 初步建议：可继续审评',
                'summary': summary, 'need_manual_review': bool(issues)}}


def result_from_rows(row, run, load):
    """网页、再生成共用同一行数据。旧字段不补造零值或当前时间。"""
    ext = load(row.conclusion_json, {})
    payload = deepcopy(ext) if isinstance(ext, dict) else {}
    for field, column, default in [('formal_review', 'formal_review_json', {}),
                                   ('change_category_suggestion', 'category_suggestion_json', {}),
                                   ('quality_standard_check', 'quality_check_json', {}),
                                   ('stability_trend_analysis', 'stability_analysis_json', {}),
                                   ('risk_points', 'risk_points_json', []), ('evidence_refs', 'evidence_json', [])]:
        payload[field] = load(getattr(row, column), default)
    saved = load(run.result_json, {})
    payload['manual_confirmation'] = saved.get('manual_confirmation') or payload.get('manual_confirmation') or {}
    payload.update(run_id=run.run_id, project_id=row.project_id, task_type='extend_validity_period')
    payload.setdefault('review_completed_at', run.finished_at.strftime('%Y-%m-%d %H:%M:%S') if run.finished_at else None)
    payload.setdefault('issue_counts', {k: None for k in ('file_missing', 'content_missing', 'parse_error', 'conflict')})
    payload.setdefault('technical_support', {'status': UNKNOWN})
    payload.setdefault('recommended_action', UNKNOWN)
    return payload
