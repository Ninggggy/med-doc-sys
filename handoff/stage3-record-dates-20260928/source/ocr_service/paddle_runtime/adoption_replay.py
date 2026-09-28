"""从原始响应重新执行采用；同一资格结果供采用与诊断共同使用。"""
from copy import deepcopy
from regional_adoption import candidate_qualification,compact


def candidate_objects(result):
    """一个空/低分片段不否定其他对象；失败请求不能提供合格对象。"""
    objects=[]
    for span in result.get('spans',[]):
        reason='request_not_completed' if result.get('status')!='completed' else None
        if not str(span.get('text_raw','')).strip():reason=reason or 'empty_span_requires_coverage'
        if not reason and span.get('original_text_raw') and span['original_text_raw']!=span.get('text_raw'):reason='score_belongs_to_different_text'
        if not reason:reason,_=candidate_qualification(span,{'result':result},[])
        objects.append(dict(span=deepcopy(span),eligible=not reason,reason=reason or 'qualified_line_object'))
    return objects


def qualified_projection(result):
    objects=candidate_objects(result);accepted=[o['span'] for o in objects if o['eligible']]
    projection=deepcopy(result);projection['spans']=accepted;projection.pop('body_spans',None)
    projection['text_assembled']='\n'.join(s['text_raw'] for s in accepted)
    projection['object_qualification']=objects
    return projection


def replay_adoptions(page):
    """不读取旧final/adopted作为采用依据；保留全部响应供后续局部裁决。"""
    from text_roles import apply_cell_roles
    output=deepcopy(page);output['page_result']=deepcopy(page.get('original_page_result',page['page_result']))
    output['adoption_replay']=dict(source='original_primary_and_local_responses',version=1)
    for candidate in output.get('dense_candidates',[]):
        candidate['previous_adopted']=candidate.get('adopted',False);candidate['adopted']=False
    for table in output.get('tables',[]):
        for cell in table['cells']:
            primary=deepcopy(cell.get('original_result',cell.get('before_role_result',cell['result'])))
            cell['result']=primary;cell['content_candidate_adopted']=False
            for k in ('role_evidence','original_role_evidence','role_revision','before_role_result'):cell.pop(k,None)
            # 墨色角色由原图重新判定；不能沿用被采用候选的分数与正文。
            apply_cell_roles(cell,output.get('stamp_regions',[]))
            cell['candidate_objects']=[candidate_objects(c) for c in cell.get('content_candidates',[])]
    return output


def adopt_dense_responses(page):
    """统一重放原长行采用条件；采用不解除可信分歧。"""
    from diagnostic_reconciliation import dense_arm_applicability
    from adapters import assemble
    primary=page['page_result'];byid={s['span_id']:s for s in primary['spans']}
    for candidate in page.get('dense_candidates',[]):
        arms=candidate.get('arms',[]);eligible=[a for a in arms if dense_arm_applicability(a,primary)[0]]
        # 至少两臂仅用于保留既有候选投影，不作为解决诊断依据。
        if len(eligible)<2 or len({a['text'] for a in eligible})!=1:continue
        if not all(s['score_raw']>=.9 for a in eligible for p in a['parts'] for s in p['spans']):continue
        s=byid.get(candidate['span_id'])
        from difflib import SequenceMatcher
        import unicodedata
        substantive_change=s and any(unicodedata.category(ch)[0] in ('L','N') for op,a,b,c,d in SequenceMatcher(None,s['text_raw'],eligible[0]['text'],autojunk=False).get_opcodes() if op!='equal' for ch in s['text_raw'][a:b]+eligible[0]['text'][c:d])
        if not substantive_change:continue  # 标点差异交逐字形裁决，不能整行覆盖
        if s and s['text_raw']!=eligible[0]['text']:
            s['original_text_raw']=s['text_raw'];s['text_raw']=eligible[0]['text'];s['content_source']='replayed_qualified_local_parts; unresolved_original_evidence_retained';candidate['adopted']=True
    primary['text_assembled']=assemble(primary['spans'])
    return page
