"""有限通用恢复；只读取本轮输入与候选，无样本ID/参考答案分支。"""
import copy
from pathlib import Path
import cv2
from adapters import assemble
from content_inputs import black_ink,red_fraction,split_line
from short_symbols import classify


def substantive(text):
    # 只去排版空白；分号、冒号、横线以及限制关系都属于候选差异。
    return ''.join(c for c in text if not c.isspace())


def recover_cell(cell,engine,run,remaining):
    original=cell['result'];im=cv2.imread(original['input']['image']);sid=original['sample_id']
    if not original['text_assembled']:
        evidence=classify(im);cell['symbol_shape']=evidence
        # 字形只能产生候选；不能排除无钩的手写倾斜1，因此保留未知语义出口。
        cell['symbol_candidate']='/' if evidence['label']=='slash_shape' else None
        cell['symbol_candidate_adopted']=False
        return
    if red_fraction(im)<.02:return
    candidates=[]
    for label,threshold in [('red_channel',220),('dark_ink',165)]:
        path=run/'images'/(sid+'-'+label+'.png');cv2.imwrite(str(path),black_ink(im,threshold))
        sample={**original['input'],'sample_id':sid+'-'+label,'image':str(path),
            'stage':'local_stamp_recovery','parent_region_id':sid}
        candidates.append(engine.predict(sample,min(60,remaining)))
    cell['content_candidates']=candidates
    a,b=candidates
    from diagnostic_reconciliation import result_applicability
    qualification=[result_applicability(c) for c in candidates]
    cell['content_candidate_qualification']=[dict(valid=v,reason=why) for v,why in qualification]
    # 逐行内容一致并且两臂有可信原始识别分数；不依据草稿挑选。
    agree=a['text_assembled']==b['text_assembled'] and bool(a['text_assembled'])
    credible=all(v for v,_ in qualification)
    cell['content_candidate_adopted']=bool(agree and credible and a['text_assembled']!=original['text_assembled'])
    if cell['content_candidate_adopted']:
        cell['original_result']=original;cell['result']=copy.deepcopy(b)
        cell['result']['adoption_reason']='two_local_pixel_inputs_agree'


def recover_dense(result,blocks,engine,run,layout_regions=()):
    image=cv2.imread(result['input']['image']);candidates=[];replacements={}
    for block in blocks:
        spans=block['spans']
        long=[]
        for span in spans:
            p=span['polygon_input_px'];xs=[q[0] for q in p];ys=[q[1] for q in p]
            w=max(xs)-min(xs);h=max(ys)-min(ys)
            if h>0 and w/h>18:long.append((span,xs,ys))
        for span,xs,ys in long:
            x0=max(0,int(min(xs))-2);y0=max(0,int(min(ys))-2)
            x1=min(image.shape[1],int(max(xs))+3);y1=min(image.shape[0],int(max(ys))+3)
            crop=image[y0:y1,x0:x1];sid=span['span_id'].replace(':','-line');arms=[]
            score=span.get('score_raw')
            overlay=red_fraction(crop)>=.02
            low_score=type(score) in (int,float) and 0<=score<.8
            emissions=span.get('ctc_emission_evidence',{})
            weak_positions=[dict(index=i,**token) for i,token in enumerate(emissions.get('tokens',[]))
                            if emissions.get('text')==span['text_raw'] and token.get('posterior',1)<.8]
            from regional_adoption import span_box
            from assembly import coverage
            b=span_box(span)
            overlaps=[r for r in layout_regions if r.get('label') in ('seal','image','figure') and b and
                      r.get('bbox_pdf') and coverage(b,r['bbox_pdf'])>0]
            # 版面框是复核触发依据，不据此把图形判成真章或删除正文。
            if not overlay and not low_score and not overlaps and not weak_positions:continue
            trigger='visible_red_overlay' if overlay else 'layout_graphic_text_overlap' if overlaps else 'weak_primary_ctc_positions' if weak_positions else 'primary_recognition_below_threshold'
            for label,pixels in [('original',crop),('black',black_ink(crop,200))]:
                parts=[]
                for j,(left,right) in enumerate(split_line(pixels)):
                    path=run/'images'/(sid+f'-{label}-{j}.png')
                    cv2.imwrite(str(path),cv2.copyMakeBorder(pixels[:,left:right],8,8,8,8,cv2.BORDER_CONSTANT,value=(255,255,255)))
                    matrix=copy.deepcopy(result['input']['input_to_page'])
                    dx,dy=x0+left-8,y0-8
                    matrix[0][2]+=matrix[0][0]*dx+matrix[0][1]*dy
                    matrix[1][2]+=matrix[1][0]*dx+matrix[1][1]*dy
                    parts.append(engine.predict(dict(sample_id=sid+f'-{label}-{j}',image=str(path),
                        granularity='line-rec',stage='dense_recovery',input_to_page=matrix,parent_region_id=span['span_id'])))
                arms.append(dict(text=''.join(p['text_assembled'] for p in parts),parts=parts))
            from diagnostic_reconciliation import dense_arm_applicability
            qualification=[dense_arm_applicability(a,result) for a in arms]
            adopt=bool(all(v for v,_ in qualification) and arms[0]['text']==arms[1]['text'] and arms[0]['text'] and substantive(arms[0]['text'])!=substantive(span['text_raw'])
                and all(s['score_raw']>=.9 for arm in arms for p in arm['parts'] for s in p['spans']))
            punctuation_only=bool(arms[0]['text']!=span['text_raw'] and substantive(arms[0]['text'])==substantive(span['text_raw']))
            candidates.append(dict(span_id=span['span_id'],original=span['text_raw'],arms=arms,adopted=adopt,
                trigger=trigger,input_evidence=dict(red_fraction=red_fraction(crop),primary_score=score,
                    weak_primary_ctc_positions=weak_positions,ctc_threshold=.8,ctc_is_correctness_probability=False,
                    overlapping_layout_regions=[dict(region_id=r['region_id'],label=r['label'],bbox_pdf=r['bbox_pdf']) for r in overlaps]),
                candidate_qualification=[dict(valid=v,**e) for v,e in qualification],
                punctuation_only=punctuation_only,
                pending_evidence='independent_context_or_human_confirmation' if punctuation_only else None,
                agreement_is_ground_truth=False))
            if adopt:replacements[span['span_id']]=arms[0]['text']
    effective=copy.deepcopy(result)
    for span in effective['spans']:
        if span['span_id'] in replacements:
            span['original_text_raw']=span['text_raw'];span['text_raw']=replacements[span['span_id']]
            span['content_source']='agreed_local_recognition_parts; original line geometry retained'
    effective['text_assembled']=assemble(effective['spans'])
    return effective,candidates
