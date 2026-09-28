"""围绕最终对象核查候选资格；历史尝试保留为观察，不冒充当前内容冲突。"""
from copy import deepcopy
import math
from assembly import bounds, coverage
from regional_adoption import compact, differences, candidate_qualification, span_box


def result_applicability(result):
    if result.get('status')!='completed':return False,'request_not_completed'
    spans=[s for s in result.get('body_spans',result.get('spans',[])) if compact(s.get('text_raw',''))]
    if not spans or not compact(result.get('text_assembled','')):return False,'no_candidate_text'
    if compact(''.join(s.get('text_raw','') for s in spans))!=compact(result.get('text_assembled','')):
        return False,'text_score_alignment_unavailable'
    for s in spans:
        if s.get('original_text_raw') and s['original_text_raw']!=s.get('text_raw'):
            return False,'score_belongs_to_different_text'
        failure,_=candidate_qualification(s,{'result':result},[])
        if failure:return False,failure
    return True,'complete_scored_candidate'


def original_body_evidence(result,regions):
    """独立像素角色证据核查旧格值；不能用adopted标志当证据。"""
    from PIL import Image,ImageDraw
    import numpy as np
    try:
        with Image.open(result['input']['image']) as im:rgb=np.asarray(im.convert('RGB'),dtype=float)
    except (OSError,KeyError):return None,[]
    body=[];roles=[]
    for s in result.get('spans',[]):
        polygon=s.get('polygon_input_px');page=s.get('polygon_page')
        if not polygon or not page:return None,roles
        mask=Image.new('L',(rgb.shape[1],rgb.shape[0]));ImageDraw.Draw(mask).polygon([tuple(p) for p in polygon],fill=1)
        ink=(rgb[:,:,1:3].min(axis=2)<180)&(np.asarray(mask)>0)
        red=(rgb[:,:,0]-rgb[:,:,1]>35)&(rgb[:,:,0]-rgb[:,:,2]>35)&ink
        dark=(rgb.max(axis=2)<150)&ink;n=int(ink.sum());fraction=float(red.sum()/max(n,1));neutral=float(dark.sum()/max(n,1))
        cx=sum(p[0] for p in page)/len(page);cy=sum(p[1] for p in page)/len(page)
        def inside(poly):
            found=False
            for (x1,y1),(x2,y2) in zip(poly,poly[1:]+poly[:1]):
                if (y1>cy)!=(y2>cy) and cx<(x2-x1)*(cy-y1)/(y2-y1)+x1:found=not found
            return found
        overlay=n>=20 and fraction>=.85 and neutral<.05 and any(inside(r['polygon_page']) for r in regions)
        roles.append(dict(span_id=s.get('span_id'),text=s.get('text_raw'),independent_red_overlay=overlay,red_fraction=fraction,neutral_fraction=neutral))
        if not overlay:body.append(s.get('text_raw',''))
    return compact(''.join(body)),roles


def dense_arm_applicability(arm, parent):
    parts=arm.get('parts',[])
    if not parts:return False,dict(reason='segmented_results_missing')
    if compact(arm.get('text',''))!=compact(''.join(p.get('text_assembled','') for p in parts)):
        return False,dict(reason='segmented_text_mismatch')
    details=[]
    for part in parts:
        valid,reason=result_applicability(part)
        if not valid:return False,dict(reason=reason,request=part.get('sample_id'))
        # 原调用的8像素白边不是完整输入证据；检查白边以内的实际分段切口。
        from PIL import Image
        import numpy as np
        with Image.open(part['input']['image']) as im:
            rgb=np.asarray(im.convert('RGB'),dtype=float)
        red=(rgb[:,:,0]-rgb[:,:,1]>35)&(rgb[:,:,0]-rgb[:,:,2]>35)
        ink=(rgb.min(axis=2)<150)&~red
        if min(ink.shape)<=16:return False,dict(reason='segmented_input_too_small')
        inner=ink[8:-8,8:-8]
        if max(inner[:,0].sum(),inner[:,-1].sum())>max(1,inner.shape[0]*.04):
            return False,dict(reason='segmentation_cuts_visible_ink',request=part.get('sample_id'))
        details.append(dict(request=part.get('sample_id'),status='qualified'))
    # 历史黑色臂不因未来停止生成而失效：完成状态、字形边界和分段证据仍逐项核查。
    return True,dict(reason='qualified_segmented_input',parts=details)


def candidate_assessments(view,page,errors):
    """诊断身份及全部旧证据保留；只能有依据地改变当前处理状态。"""
    assessments=[];lines={l.get('source_span_id'):l for l in view['lines']}
    parent=page.get('original_page_result',page.get('page_result',{}))
    for index,original in enumerate(errors):
        issue=deepcopy(original);reason=None;decision={}
        sid=issue.get('source_span_id');candidate=issue.get('candidate_evidence')
        if isinstance(candidate,dict) and 'arms' in candidate:
            line=lines.get(candidate.get('span_id'));effective=line.get('text','') if line else None
            qualified=[]
            for arm in candidate['arms']:
                valid,evidence=dense_arm_applicability(arm,parent)
                decision.setdefault('arms',[]).append(dict(valid=valid,**evidence))
                if valid:qualified.append(arm)
            if not qualified:reason='no_applicable_candidate; primary_content_not_certified'
            elif effective is not None:
                competing=[a for a in qualified if compact(a['text'])!=compact(effective)]
                if not competing:
                    reason='qualified_candidates_match_effective_source; historical_difference_only'
                    decision['adoption_limit']='完整输入、分段切口及当前来源已核查；同模型一致不构成原件正确证明'
                    original_span=next((s for s in parent.get('spans',[]) if s.get('span_id')==candidate['span_id']),None)
                    if original_span and compact(original_span['text_raw'])!=compact(effective):
                        old_result={**parent,'spans':[original_span],'text_assembled':original_span['text_raw']}
                        valid,why=result_applicability(old_result)
                        decision['original_source_applicability']=dict(valid=valid,reason=why)
                        if valid:
                            reason=None;issue['text']=effective
                            issue['reason']='当前已采用分段候选，但原输入仍提供合格的实质分歧；没有独立字符证据可解除'
                            issue['differences']=differences(effective,original_span['text_raw'])
                else:
                    issue['text']=effective
                    issue['differences']=[dict(arm=i,**d) for i,a in enumerate(competing) for d in differences(effective,a['text'])]
                    issue['reason']='合格局部候选与当前有效文字存在具体差异；须核对所列字符或标点'
            issue['candidate_applicability']=decision
        elif issue.get('code')=='ocr_candidate_conflict' and isinstance(candidate,dict) and 'result' in candidate:
            valid,why=result_applicability(candidate['result']);decision=dict(valid=valid,reason=why)
            if not valid:reason='rejected_regional_candidate; primary_coverage_separate'
            elif str(issue.get('adoption_reason','')).endswith('; not_adopted'):
                reason='incomplete_or_inapplicable_input; primary_coverage_separate'
        if sid and sid not in lines and issue['code'] in ('ocr_quality','ocr_score_unavailable'):
            replacements=[l for l in view['lines'] if sid in l.get('replaces_span_ids',[])]
            if replacements:
                reason='source_replaced_with_independent_visible_gap_evidence'
                decision['replacement_span_ids']=[l['source_span_id'] for l in replacements]
        if type(issue.get('table_index')) is int and type(issue.get('cell_index')) is int:
            cell=view['tables'][issue['table_index']]['cells'][issue['cell_index']]
            role=next((e for e in cell.get('role_evidence',[]) if e.get('span_id')==sid),None)
            if role and role.get('role')=='red_overlay':
                issue['content_role']='independent_overlay';issue['affects_body']=False
                issue['reason']='独立印章/叠印片段识别不确定；核对该片段，不代表地址或整格正文低分'
            if issue['code']=='ocr_overlay_conflict' and isinstance(candidate,list):
                qualified=[];decision['candidates']=[]
                for raw_candidate in candidate:
                    from adoption_replay import qualified_projection
                    c=qualified_projection(raw_candidate)
                    valid,why=result_applicability(c);decision['candidates'].append(dict(valid=valid,reason=why))
                    if valid:qualified.append(c)
                effective=cell.get('text','')
                if not qualified:reason='no_applicable_cell_candidate; primary_risks_retained'
                elif all(compact(c['text_assembled'])==compact(effective) for c in qualified):
                    # 是否采用另有记录；不再把已采用候选与被替代旧文本重复报成新冲突。
                    reason='qualified_cell_candidates_match_effective_value'
                    decision['adoption_evidence']=dict(original_result=cell.get('original_result'),role_evidence=cell.get('role_evidence'),
                        limitation='当前候选不再分歧；保留原始结果，不以adopted或一致判定原件正确')
                    original_result=cell.get('original_result',cell['result'])
                    body,roles=original_body_evidence(original_result,page.get('stamp_regions',[]))
                    decision['original_pixel_roles']=roles
                    if body!=compact(effective):
                        reason=None;issue['text']=effective;issue['candidate_applicability']=decision
                        issue['reason']='当前格值与合格候选一致，但旧图像的正文/叠印差异尚未获得完整独立证据'
                        issue['differences']=differences(effective,body if body is not None else original_result.get('text_assembled',''))
                else:
                    issue['text']=effective;issue['candidate_applicability']=decision
                    issue['differences']=[d for c in qualified for d in differences(effective,c['text_assembled'])]
        if reason and issue.get('content_role')=='mixed_cell_text_object' and issue.get('role_evidence'):
            decision['candidate_history_reason']=reason
            issue['candidate_applicability']=decision
            issue['reason']='候选已退出竞争，但原图正文与章墨的归属仍未完成核验'
            reason=None
        if reason:
            record=dict(code='diagnostic_reconciliation',disposition='historical_observation',
                reason=reason,original_diagnostic_index=index,original_diagnostic=original,evidence=decision)
        else:record=None
        issue['original_diagnostic_index']=index
        assessments.append((issue,record))
    return assessments


def reconcile(view,page,errors,observations):
    rows=candidate_assessments(view,page,errors)
    observations.extend(record for issue,record in rows if record)
    view['raw_diagnostic_count']=len(errors)
    return [issue for issue,record in rows if not record]
