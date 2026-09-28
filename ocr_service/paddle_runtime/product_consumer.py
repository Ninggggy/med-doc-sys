"""产品投影：分数、候选冲突、版面、缺字分别记录，不使用研究答案。"""
from copy import deepcopy
import math
from experimental_parser import consumer_page as experimental_page, box
from assembly import assemble_chunk, coverage

REC_THRESHOLD = .8


def verified_region_supersedes(entry, decisions, lines):
    """完整区域验证替代同一区域的早期补识别；不触及原始行或邻区。"""
    region = entry.get('region') or {}
    for decision in decisions:
        target = decision.get('target') or {}
        whole = decision.get('whole_sequence_review') or {}
        if not (decision.get('accepted') and decision.get('kind') == 'region'
                and whole.get('complete') and whole.get('selected_sequence') == decision.get('text')
                and (decision.get('input_evidence') or {}).get('eligible')):
            continue
        if target.get('span_id') != str(region.get('region_id')) + ':complete-title':
            continue
        ids = decision.get('source_span_ids') or []
        if not ids or not all(any(line.get('source_span_id') == sid for line in lines) for sid in ids):
            continue
        if coverage(region['bbox_pdf'], box(target['polygon_page'])) < 1 - 1e-6:
            continue
        return decision
    return None


def score_evidence(span):
    raw=span.get('score_raw')
    valid=type(raw) in (float,int) and math.isfinite(raw) and 0<=raw<=1 and span.get('score_kind')=='paddle_rec_score'
    return dict(score_raw=raw,score_kind=span.get('score_kind'),score=raw if valid else None,
                score_available=valid,score_unit='probability_0_1',threshold=REC_THRESHOLD,
                source_span_id=span.get('span_id'),raw_output_index=span.get('raw_output_index'),
                text=span.get('text_raw',''),original_text=span.get('original_text_raw'),
                score_text=span.get('original_text_raw',span.get('text_raw','')),
                score_applies_to='original_line' if span.get('original_text_raw') else 'displayed_text',
                **({'supporting_line_score':span['supporting_line_score']} if span.get('supporting_line_score') is not None else {}),
                content_source=span.get('content_source'),polygon_page=span.get('polygon_page'))


def final_unresolved(view,page,raw_observations,history):
    """按有效来源和验证范围归结观察；不修改原始证据，也不生成新对象。"""
    from field_coverage import layout_coverage_assessments
    from diagnostic_reconciliation import candidate_assessments
    from bounded_review import sequence_assessments
    from seal_review import seal_assessments
    candidates=candidate_assessments(view,page,deepcopy(raw_observations))
    effective=[issue for issue,record in candidates]
    # 各类证据读取同一组完整观察，不再逐级删除，使后一模块看不到前一模块的风险。
    sequences=sequence_assessments(view,page,deepcopy(effective))
    seals=seal_assessments(view,effective)
    coverage_proofs=layout_coverage_assessments(view,page,effective)
    remaining=[]
    for index,((issue,historical),(scoped,sequence),(_,seal),(_,covered)) in enumerate(zip(candidates,sequences,seals,coverage_proofs)):
        # 正文序列、完整章覆盖和版面字形覆盖各有严格适用范围；候选失效只解释历史。
        record=sequence or seal or covered or historical
        if issue.get('stage')=='field_value_coverage':
            review=next((r for r in view.get('field_value_reviews',[]) if r.get('accepted') and
                         r.get('decision',{}).get('whole_sequence_review',{}).get('complete') and
                         r.get('source_span_id')==issue.get('source_span_id') and
                         any(l.get('field_value_verification')==r for l in view.get('lines',[]))),None)
            if review:record=dict(code='automatic_resolution',disposition='content_recovered',
                reason='本页同一字段完整值区已经检测并完成序列核验',evidence=deepcopy(review))
        if issue.get('code')=='ocr_quality':
            review=next((r for r in view.get('field_value_reviews',[]) if r.get('accepted') and r.get('label_verified')
                and r.get('decision',{}).get('whole_sequence_review',{}).get('complete')
                and r.get('source_span_id')==issue.get('source_span_id')
                and r.get('original')==issue.get('text')
                and any(l.get('field_value_verification')==r for l in view.get('lines',[]))),None)
            if review:record=dict(code='automatic_resolution',disposition='field_verified',
                reason='标签和实际值分别获得完整序列支持，低分整行的核验范围已完整覆盖',evidence=deepcopy(review))
        if record:
            record=deepcopy(record);record['original_diagnostic_index']=index
            record['original_diagnostic']=deepcopy(raw_observations[index])
            history.append(record)
        else:
            scoped['original_diagnostic_index']=index;remaining.append(scoped)
    view['raw_diagnostic_count']=len(raw_observations)
    view['automatic_adjudications']=deepcopy(page.get('automatic_adjudications',[]))
    return remaining


def consumer_page(page):
    view=experimental_page(page);errors=[];observations=[]
    def add(code,reason,bounds,**evidence):
        errors.append(dict(code=code,stage='local_paddle',reason=reason,bbox_pdf=bounds,**evidence))
    def check_span(span, **extra):
        evidence=score_evidence(span)
        fallback=extra.pop('fallback_box',view['page_bbox'])
        bounds=box(span['polygon_page']) if span.get('polygon_page') else fallback
        if not evidence['score_available']:
            add('ocr_score_unavailable','识别分数缺失、类型无效或超出声明量纲；未按高分/低分处理',bounds,**evidence,**extra)
        elif not evidence['text'].strip():
            add('ocr_empty','检测/识别区域未读出文字；不能据此判为空白',bounds,**evidence,**extra)
        elif evidence['score']<REC_THRESHOLD:
            add('ocr_quality',f'模型识别分数 {evidence["score"]:.4f} 低于阈值 {REC_THRESHOLD}，请核对该片段',bounds,**evidence,**extra)
    result=page.get('page_result',{})
    if result.get('status')!='completed' or page.get('error'):
        add('ocr_response','本页识别未完成，请重试',view['page_bbox'],processing_error=page.get('error'))
    lines=[]
    for span in result.get('spans',[]):
        if not span.get('polygon_page'):continue
        b=box(span['polygon_page'])
        if any(coverage(b,t['bbox_pdf'])>.98 for t in view['tables']):continue
        lines.append(dict(text=span['text_raw'],bbox=b,polygon=span['polygon_page'],
            source_span_id=span['span_id'],source=view['ocr_backend'],granularity=span['granularity'],
            recognition_evidence=score_evidence(span)))
        check_span(span)
    lines.extend(l for l in view['lines'] if l.get('table_index') is not None)
    view['original_lines']=deepcopy(lines)
    view['field_value_reviews']=deepcopy(page.get('field_value_reviews',[]))
    for review in view['field_value_reviews']:
        if not review.get('accepted') or not review.get('decision',{}).get('whole_sequence_review',{}).get('complete'):continue
        original=next((l for l in lines if l.get('source_span_id')==review['source_span_id']),None)
        if not original or original.get('human_revision') or original['text']!=review['original']:continue
        previous=deepcopy(original);value=review['span']
        original.update(text=review['label']+'：'+review['text'],original_source_lines=[previous],
            source_span_ids=list(dict.fromkeys([review['source_span_id'],value['span_id']])),
            value_source=deepcopy(value),field_value_verification=deepcopy(review))
        if review.get('layer_evidence') and review.get('label_verified') and review.get('label_span'):
            label=review['label_span'];points=label['polygon_page']+value['polygon_page'];b=box(points)
            original.update(bbox=b,ordering_bbox=deepcopy(previous['bbox']),polygon=[[b[0],b[1]],[b[2],b[1]],[b[2],b[3]],[b[0],b[3]]],
                source_span_ids=list(dict.fromkeys([review['source_span_id'],label['span_id'],value['span_id']])))
            original['original_source_lines'].extend(dict(text=s['text_raw'],source_span_id=s['span_id'],
                bbox=box(s['polygon_page']),polygon=deepcopy(s['polygon_page'])) for s in (label,value))
            observations.append(dict(code='automatic_resolution',disposition='content_recovered',
                reason='完整标签及值分别获得原始栅格与序列支持',original_diagnostic=deepcopy(review.get('original_issue')),
                evidence=deepcopy(review)))
        if review.get('issue'):errors.append(deepcopy(review['issue']))
    from background_text import separate_background
    lines,background=separate_background(lines,result)
    view['background_overlays']=background
    if background:
        observations.append(dict(code='ocr_runtime_observation',reason='重复浅色背景文字独立保留，不拼入内容字段',evidence=background))
    view['bounded_review_responses']=deepcopy(page.get('bounded_review_responses',{}))
    view['bounded_recovery_attempts']=deepcopy(page.get('bounded_recovery_attempts',[]))
    view['regional_recognition']=deepcopy(page.get('regional_recognition',[]))
    from regional_adoption import adopt_region, differences
    for entry in page.get('regional_recognition',[]):
        entry=deepcopy(entry)
        entry['source_input_to_page']=(result.get('input') or {}).get('input_to_page')
        r=entry.get('result') or {};region=entry['region'];spans=r.get('spans',[])
        if r.get('status')!='completed':
            observations.append(dict(code='ocr_runtime_observation',reason='区域请求未完成；不参与字符冲突判断，覆盖风险单独核查',candidate_evidence=deepcopy(entry)))
            continue
        verified = verified_region_supersedes(entry, page.get('automatic_adjudications', []), lines)
        if verified:
            observations.append(dict(code='ocr_runtime_observation',
                reason='同一完整标题已验证；早期区域补识别不再重复拼入有效正文',
                candidate_evidence=deepcopy(entry), verified_target=verified['target']['span_id']))
            continue
        def make_line(span):
            return dict(text=span['text_raw'],bbox=box(span['polygon_page']),polygon=span['polygon_page'],
                        source_span_id=span['span_id'],source=view['ocr_backend'],granularity=span['granularity'],
                        recognition_evidence=score_evidence(span))
        lines,decision=adopt_region(lines,entry,make_line)
        for span in spans:
            if span['span_id'] in decision['added_span_ids']:check_span(span)
        observations.append(dict(code='ocr_runtime_observation',reason='区域识别记录；仅采用不覆盖已有文字的新增行',
            bbox_pdf=region['bbox_pdf'],adoption=decision,candidate_evidence=deepcopy(entry)))
        for conflict in decision['conflicts']:
            add('ocr_candidate_conflict','局部候选与已有文字有差异或裁剪不完整；保留原文待核对',
                conflict['bbox_pdf'],text=''.join(l['text'] for l in conflict['original_lines']),
                candidate_evidence=deepcopy(entry),adoption_reason=conflict['reason'],**{k:v for k,v in conflict.items() if k not in ('bbox_pdf','reason')})
    # 序列裁决作用于明确来源行，晚于区域补全，早于字段组装。
    for decision in page.get('automatic_adjudications',[]):
        if not (decision.get('accepted') or decision.get('recovered')) or decision.get('kind') not in ('dense','object'):continue
        sid=decision['source_span_id']
        for line in lines:
            if line.get('source_span_id')!=sid:continue
            if line['text'] not in (decision['original'],decision['text']):
                decision['accepted']=False;decision['reason']='effective_source_changed_by_region';break
            line['text']=decision['text']
            line['automatic_adjudication']=deepcopy(decision)
    for decision in page.get('automatic_adjudications',[]):
        if not decision.get('accepted') or decision.get('kind')!='region':continue
        ids=decision['source_span_ids'];members=[line for line in lines if line.get('source_span_id') in ids]
        if len(members)!=len(ids):
            decision['accepted']=False;decision['reason']='effective_region_sources_changed';continue
        target=decision['target'];replacement=deepcopy(members[0]);replacement.update(
            text=decision['text'],bbox=box(target['polygon_page']),polygon=target['polygon_page'],
            source_span_id=target['span_id'],source_span_ids=ids,original_source_lines=deepcopy(members),
            automatic_adjudication=deepcopy(decision))
        from assembly import bounds
        replacement['ordering_bbox']=bounds([line.get('ordering_bbox',line['bbox']) for line in members])
        position=min(lines.index(line) for line in members)
        lines=[line for line in lines if line not in members];lines.insert(position,replacement)
    # 跨区域的短竖排检测框，只有每个原字符均由同位置独立字框重现，才退出有效行。
    # 原框完整保留在 original_lines；不以区域非空或总分作为替换依据。
    cross_ids={e.get('span_id') for e in page.get('regional_recognition',[]) if e.get('trigger')=='ocr_span_crosses_layout_regions'}
    for sid in cross_ids:
        old=next((l for l in lines if l.get('source_span_id')==sid),None)
        if not old or not 1<len(old['text'].strip())<=4:continue
        chars=[l for l in lines if l.get('recovery_region_id') and len(l['text'].strip())==1 and coverage(l['bbox'],old['bbox'])>.45]
        chars.sort(key=lambda l:l['bbox'][1])
        if ''.join(l['text'].strip() for l in chars)==old['text'].strip():
            lines.remove(old)
            observations.append(dict(code='ocr_runtime_observation',reason='跨区短竖排框的全部原字符由独立字框逐一重现',
                bbox_pdf=old['bbox'],original_lines=[deepcopy(old)],source_span_ids=[l['source_span_id'] for l in chars]))
    for evidence in page.get('internal_coverage_evidence',[]):
        b=evidence.get('uncovered_bbox_pdf') or evidence['visible_bbox_pdf']
        if not any(coverage(b,l['bbox'])>.6 for l in lines):
            add('ocr_coverage','正文块内存在完整可见字形行但检测/识别未覆盖',b,coverage_evidence=deepcopy(evidence))
    view['internal_coverage_evidence']=deepcopy(page.get('internal_coverage_evidence',[]))
    from seal_review import adopt_verified_seals, reconcile_seal_issues
    lines,applied_seals=adopt_verified_seals(lines,page)
    view['applied_seal_adjudications']=applied_seals
    view['seal_adjudications']=deepcopy(page.get('seal_adjudications',[]))
    view['lines']=lines;view['words']=deepcopy(lines)
    view['layout_result']=deepcopy(page.get('layout_result') or {})
    view['layout_regions']=deepcopy(view['layout_result'].get('regions') or [])
    if len(view['layout_regions'])>1:
        observations.append(dict(code='layout_observation',reason='按版面模型区域及阅读顺序组装；多区域本身不是内容错误',
            region_count=len(view['layout_regions']),model='PP-DocLayoutV3'))
    assemble_chunk(view)
    from seal_review import associate_authority_regions
    if not view.get('authority_region_associations'):associate_authority_regions(view,page)
    view['seal_review']=deepcopy(page.get('seal_review',[]))
    for resolved in view.get('resolved_assembly_issues',[]):
        observations.append(dict(code='automatic_resolution',disposition='content_recovered',
            original_diagnostic=deepcopy(resolved),evidence=deepcopy(view['authority_region_associations'])))
    for association in view.get('authority_region_associations',[]):
        if association.get('value_verified'):continue
        related=[d for d in page.get('seal_adjudications',[]) if box(d['seal_polygon_page'])==association['seal_bbox_pdf']
                 and d.get('integrity',{}).get('red_ink_pixels',0)>=20]
        if related and not any(set(e.get('source_span_ids',[]))==set(association['field_source_span_ids']) for e in view['assembly_issues']):
            message=('机关字段与同页章文的对应仍未核实，当前残缺值不能据此自动补全' if association.get('previous_value')
                     else '机关字段的同页章区域有可见文字，但完整章文尚未可靠核实，机关值仍缺失')
            add('ocr_overlay_conflict',message,
                association['seal_bbox_pdf'],source_span_ids=association['field_source_span_ids'],
                source_span_id=next(iter(association['field_source_span_ids']),None),
                region_id=association['field_region_id'],content_role='authority_field',
                seal_evidence=deepcopy(related),region_association=deepcopy(association))
    if view['layout_result'].get('status')!='completed':
        add('text_assembly_unresolved','版面模型未提供有效区域与阅读顺序，保留原始行序列',view['page_bbox'],layout_evidence=view['layout_result'])
    errors.extend(view['assembly_issues'])
    for issue in errors:
        if issue.get('code')!='text_assembly_unresolved':continue
        for association in view['authority_region_associations']:
            if set(issue.get('source_span_ids',[])) & set(association['field_source_span_ids']):
                issue['region_association']=deepcopy(association)
                issue['reason']='机关标签已定位到本页唯一相邻完整印章；章文尚未完整核实，未猜填机关名称'
    by_id={s['span_id']:s for s in result.get('spans',[])}
    for candidate in page.get('dense_candidates',[]):
        if any(a['text']!=candidate['original'] for a in candidate.get('arms',[])):
            span=by_id.get(candidate['span_id'])
            if span:add('ocr_candidate_conflict','长行不同输入产生不同候选；候选一致也不等于原件正确',box(span['polygon_page']),
                **score_evidence(span),candidate_evidence=deepcopy(candidate),
                differences=[dict(arm=i,**d) for i,a in enumerate(candidate.get('arms',[])) for d in differences(candidate['original'],a['text'])])
    for ti,t in enumerate(view['tables']):
        before=len(errors)
        for ci,c in enumerate(t['cells']):
            r=c.get('result',{});b=c.get('bbox_pdf',t['bbox_pdf']);meta=dict(table_index=ti,cell_index=ci)
            if r.get('status')!='completed':add('ocr_coverage','该单元格识别未完成',b,**meta,result_status=r.get('status'))
            for span in r.get('spans',[]):check_span(span,**meta,fallback_box=b)
            candidates=c.get('content_candidates',[])
            if candidates and len({v.get('text_assembled','') for v in [c.get('original_result',r),*candidates]})>1:
                add('ocr_overlay_conflict','叠印区域的原始识别与局部候选不一致',b,**meta,
                    text=c.get('text',''),candidate_evidence=deepcopy(candidates),original_result=deepcopy(c.get('original_result',r)))
            if ((c.get('symbol_shape') or {}).get('label')=='unknown'
                    and (c.get('symbol_shape') or {}).get('proposal') is not None
                    and not r.get('text_assembled','').strip()):
                add('ocr_coverage','单元格存在未识别墨迹，符号类别未知；尚不能确认空白',b,**meta,
                    text=c.get('text',''),symbol_evidence=deepcopy(c['symbol_shape']))
                c['content_state']='ink_unresolved'
            if c.get('symbol_candidate'):
                add('ocr_candidate_conflict','独立符号有形状候选，尚不能确定原件字符',b,**meta,
                    text=c.get('text',''),symbol_evidence=deepcopy(c.get('symbol_shape')),candidate_text=c['symbol_candidate'])
            if c.get('role_revision'):
                evidence=c.get('role_evidence') or []
                separated=[e for e in evidence if e.get('role')=='red_overlay']
                proven=separated and all(e.get('inside_red_circular_outline') and e.get('red_ink_fraction',0)>=.85
                                        and e.get('neutral_dark_fraction',1)<.05 for e in separated)
                if proven:
                    observations.append(dict(code='ocr_overlay_observation',reason='独立红色叠印已单独保留；没有据此修改黑色正文',
                        bbox_pdf=b,**meta,role_evidence=deepcopy(evidence),role_revision=deepcopy(c['role_revision'])))
                else:
                    add('ocr_overlay_conflict','正文与叠印分离证据不完整，需核对',b,**meta,
                        text=c.get('text',''),role_evidence=deepcopy(evidence),role_revision=deepcopy(c['role_revision']))
            mixed=[e for e in c.get('original_role_evidence',c.get('role_evidence',[])) if e.get('role')=='mixed_or_body'
                   and e.get('inside_red_circular_outline') and e.get('red_ink_pixels',0)>=20
                   and e.get('neutral_dark_pixels',0)>=20 and e.get('red_ink_fraction',0)>e.get('neutral_dark_fraction',1)]
            if mixed:
                existing=next((e for e in errors if e.get('table_index')==ti and e.get('cell_index')==ci and e['code']=='ocr_overlay_conflict'),None)
                if existing is not None:
                    # 候选差异与真实墨色归属共用入口，但失效候选不能连带解除归属风险。
                    existing.update(role_evidence=deepcopy(mixed),content_role='mixed_cell_text_object')
                else:
                    add('ocr_overlay_conflict','该文字框同时包含正文和占主导的章墨，尚未证明当前文字属于正文；高识别分数不解决归属',
                        b,**meta,text=c.get('text',''),role_evidence=deepcopy(mixed),content_role='mixed_cell_text_object')
            if not r.get('text_assembled'):c.setdefault('content_state','empty_unverified')
        t['needs_review']=False
        t['diagnostic_indices']=list(range(before,len(errors)))
    for region in page.get('incomplete_regions',[]):
        add('table_structure_unresolved','可见表格边界未完整恢复',region.get('bbox_pdf',view['page_bbox']),geometry_evidence=deepcopy(region))
    from field_coverage import field_missing_issues,reconcile_layout_coverage,regional_gap_issues
    from diagnostic_reconciliation import reconcile
    errors.extend(field_missing_issues(view,page))
    errors.extend(regional_gap_issues(view,page))
    raw_count=len(errors)+len(view.get('resolved_assembly_issues',[]))
    from bounded_review import adjudication_observations
    errors.extend(adjudication_observations(view,page,errors))
    before_residual=len(errors)
    from seal_review import unresolved_seal_objects
    for element,siblings in unresolved_seal_objects(view,errors):
        # 同一原对象只核实了章的一部分；未归属的剩余行不能因旧低分碎片消失而自动通过。
        add('ocr_overlay_conflict','完整章的部分来源已核实，但本对象仍有未被覆盖的文字或叠加墨迹；需核对其归属',
            element['bbox_pdf'],source_span_ids=element.get('source_span_ids',[]),
            source_span_id=next(iter(element.get('source_span_ids',[])),None),region_id=element['region_id'],
            text=element['text'],content_role='residual_after_partial_seal_adoption',
            verified_seal_regions=[e['region_id'] for e in siblings])
    raw_count=len(errors)+len(view.get('resolved_assembly_issues',[]))
    # 所有观察先收齐，最终只产生一次未决投影；此后不再追加风险。
    errors=final_unresolved(view,page,errors,observations)
    view['raw_diagnostic_count']=raw_count
    for ti,table in enumerate(view['tables']):
        table['diagnostic_indices']=[i for i,e in enumerate(errors) if e.get('table_index')==ti]
    view['errors']=errors
    view['diagnostic_observations']=observations
    if page.get('adoption_replay'):
        original=deepcopy(page.get('original_page_result',page['page_result']))
        view['raw_result']=original;view['raw_text']=original.get('text_assembled','')
        view['original_lines']=[dict(text=s['text_raw'],bbox=box(s['polygon_page']),polygon=s['polygon_page'],source_span_id=s['span_id'],recognition_evidence=score_evidence(s)) for s in original.get('spans',[]) if s.get('polygon_page')]
        view['raw_cell_results']=[[deepcopy(c.get('original_result',c['result'])) for c in t['cells']] for t in page['tables']]
    view['status']='partial' if errors else 'success';view['parse_status']=view['status']
    view['unresolved_reasons']=[e['reason'] for e in errors];view['unresolved_evidence']=deepcopy(errors)
    view['automatic_review_policy']='distinct_score_layout_coverage_candidate_evidence'
    return view
