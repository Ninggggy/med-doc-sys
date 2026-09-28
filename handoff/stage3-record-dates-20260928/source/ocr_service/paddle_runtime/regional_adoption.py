"""局部识别只是候选；保留已有行，按明确的字符及几何覆盖采用新增行。"""
from copy import deepcopy
from difflib import SequenceMatcher
import math
from assembly import coverage, bounds


def span_box(span):
    p = span.get('polygon_page') or []
    return bounds([[x,y,x,y] for x,y in p]) if p else None


def compact(text):
    return ''.join(str(text).split())


def differences(old, new):
    return [dict(operation=op, original=old[a:b], candidate=new[c:d])
            for op,a,b,c,d in SequenceMatcher(None,old,new,autojunk=False).get_opcodes() if op!='equal']


def crop_scope(entry, spans):
    """补齐相交字形；跨入独立字段时拒绝扩大，而不是切掉原字形。"""
    rb=entry['region']['bbox_pdf']
    members=[s for s in spans if span_box(s) and coverage(span_box(s),rb)>=.5]
    wanted=bounds([rb]+[span_box(s) for s in members])
    height=min([span_box(s)[3]-span_box(s)[1] for s in members] or [rb[3]-rb[1]])
    margin=max(2,min(8,height*.35))
    padded=[wanted[0]-margin,wanted[1]-margin,wanted[2]+margin,wanted[3]+margin]
    # 仅多加的留白碰到邻行时，缩短留白到两者间隙；不得裁掉 wanted 内的字形。
    padding_adjustments=[]
    for other in spans:
        ob=span_box(other)
        if other in members or not ob or coverage(ob,rb)>.05:continue
        if min(wanted[2],ob[2])>max(wanted[0],ob[0]):
            if ob[3]<=wanted[1] and padded[1]<ob[3]:
                padded[1]=(ob[3]+wanted[1])/2;padding_adjustments.append(other['span_id'])
            if ob[1]>=wanted[3] and padded[3]>ob[1]:
                padded[3]=(ob[1]+wanted[3])/2;padding_adjustments.append(other['span_id'])
        if min(wanted[3],ob[3])>max(wanted[1],ob[1]):
            if ob[2]<=wanted[0] and padded[0]<ob[2]:
                padded[0]=(ob[2]+wanted[0])/2;padding_adjustments.append(other['span_id'])
            if ob[0]>=wanted[2] and padded[2]>ob[0]:
                padded[2]=(ob[0]+wanted[2])/2;padding_adjustments.append(other['span_id'])
    neighbors=[s for s in spans if s not in members and span_box(s) and
               coverage(span_box(s),padded)>.05 and coverage(span_box(s),rb)<=.05]
    return dict(bbox_pdf=padded,padding_adjusted_by_neighbor_gap=padding_adjustments, replacement_span_ids=[s['span_id'] for s in members],
                fully_contains_source_lines=not neighbors,
                blocked_by_span_ids=[s['span_id'] for s in neighbors],
                reason='expanded_to_complete_glyphs' if not neighbors else 'expansion_intersects_neighbor')


def input_geometry(result):
    """完整逆仿射：页面坐标回到实际识别输入，包含旋转与剪切。"""
    m=(result.get('input') or {}).get('input_to_page');size=result.get('input_size')
    if not m or not size or len(size)!=2 or min(size)<=0:return None
    a,b,c=m[0];d,e,f=m[1];det=a*e-b*d
    if not math.isfinite(det) or abs(det)<1e-12:return None
    return size,lambda x,y:((e*(x-c)-b*(y-f))/det,(-d*(x-c)+a*(y-f))/det)


def input_box(b, inverse):
    return bounds([[u,v,u,v] for x,y in [(b[0],b[1]),(b[2],b[1]),(b[2],b[3]),(b[0],b[3])]
                   for u,v in [inverse(x,y)]])


def candidate_qualification(span, entry, old):
    """所有新增内容共用资格检查；识别分数始终属于完整候选行。"""
    score=span.get('score_raw');result=entry.get('result') or {}
    if (span.get('score_kind')!='paddle_rec_score' or type(score) not in (int,float)
            or not math.isfinite(score) or not .8<=score<=1):
        return 'candidate_score_unavailable_or_below_0.8',None
    geometry=input_geometry(result)
    if not geometry:return 'input_geometry_unavailable',None
    size,inverse=geometry;w,h=size
    if old and entry.get('crop_scope',{}).get('fully_contains_source_lines') is False:
        return 'input_does_not_contain_source_lines',None
    source_points=[p for l in old for p in (l.get('polygon') or
                  [[l['bbox'][0],l['bbox'][1]],[l['bbox'][2],l['bbox'][1]],
                   [l['bbox'][2],l['bbox'][3]],[l['bbox'][0],l['bbox'][3]]])]
    visible=entry.get('coverage_evidence') or {};page_matrix=entry.get('source_input_to_page')
    if visible.get('method')=='connected_glyph_rows' and page_matrix:
        ox,oy=visible['pixel_origin'];source_points=[]
        for x0,y0,x1,y1 in visible['glyph_boxes_px']:
            for x,y in [(x0+ox,y0+oy),(x1+ox,y0+oy),(x1+ox,y1+oy),(x0+ox,y1+oy)]:
                source_points.append([page_matrix[0][0]*x+page_matrix[0][1]*y+page_matrix[0][2],
                                      page_matrix[1][0]*x+page_matrix[1][1]*y+page_matrix[1][2]])
    if any(not (0<=x<=w and 0<=y<=h) for p in source_points for x,y in [inverse(*p)]):
        return 'input_does_not_contain_source_lines',None
    # crop_region 是输入范围，不是字形。优先查实际输入墨迹，不能把矩形边缘当作截字。
    ink=None
    image=(result.get('input') or {}).get('image')
    if image:
        try:
            from PIL import Image
            with Image.open(image) as im:
                if im.size!=(w,h):return 'input_image_size_mismatch',None
                import numpy as np
                rgb=np.asarray(im.convert('RGB'),dtype=float)
                red=(rgb[:,:,0]-rgb[:,:,1]>40)&(rgb[:,:,0]-rgb[:,:,2]>40)&(rgb[:,:,0]>100)
                dark=(np.asarray(im.convert('L'))<150)&~red
                ink=Image.fromarray((dark*255).astype('uint8'))
            boundary_ink=ink
            if span.get('coordinate_source') in ('engine_polygon','connected_glyphs'):
                from PIL import ImageDraw,ImageChops
                polygon=span.get('polygon_input_px') or [inverse(x,y) for x,y in span.get('polygon_page',[])]
                if polygon:
                    mask=Image.new('L',(w,h));ImageDraw.Draw(mask).polygon([tuple(p) for p in polygon],fill=255)
                    boundary_ink=ImageChops.multiply(ink,mask)
            q=boundary_ink.getbbox()
            if not q:return 'no_visible_glyph_evidence',None
            if q and (q[0]==0 or q[1]==0 or q[2]==w or q[3]==h):
                return 'visible_ink_touches_input_edge',None
        except (OSError,ValueError):return 'input_image_unavailable',None
    else:
        polygon=span.get('polygon_input_px')
        if not polygon and span.get('coordinate_source')=='engine_polygon':
            polygon=[inverse(x,y) for x,y in span.get('polygon_page',[])]
        if span.get('coordinate_source')!='engine_polygon' or not polygon:
            return 'glyph_boundary_evidence_unavailable',None
        if any(x<=0 or y<=0 or x>=w or y>=h for x,y in polygon):
            return 'engine_polygon_touches_input_edge',None
    return None,dict(size=size,inverse=inverse,ink=ink)


def missing_fragment_box(entry, span, old, side, evidence):
    """仅接入旧检测框外可见的缺口；字符变化本身不证明漏检。"""
    b=span_box(span);previous=bounds([l['bbox'] for l in old]);missing=entry.get('uncovered_bbox_pdf')
    if missing and entry.get('trigger')=='internal_partial_glyph_coverage':
        if not all(coverage(missing,l['bbox'])<.6 for l in old) or coverage(missing,b)<.9:return None
        gap=missing
    elif side=='prefix':gap=[b[0],max(b[1],previous[1]),previous[0],min(b[3],previous[3])]
    else:gap=[previous[2],max(b[1],previous[1]),b[2],min(b[3],previous[3])]
    if gap[2]-gap[0]<(previous[3]-previous[1])*.5 or gap[3]<=gap[1]:return None
    ink=evidence['ink']
    if ink is None:return gap if missing and entry.get('trigger')=='internal_partial_glyph_coverage' else None
    q=input_box(gap,evidence['inverse']);w,h=evidence['size']
    q=[max(0,int(q[0])),max(0,int(q[1])),min(w,int(math.ceil(q[2]))),min(h,int(math.ceil(q[3])))]
    if q[2]<=q[0] or q[3]<=q[1]:return None
    visible=ink.crop(q);bb=visible.getbbox()
    if not bb or bb[3]-bb[1]<(q[3]-q[1])*.35 or sum(visible.histogram()[1:])<8:return None
    return gap


def adopt_region(lines, entry, make_line):
    """不以分数比较替换不同字符。新增行须与现有行不重叠，且完整位于输入内。"""
    output=list(lines); conflicts=[]; added=[]; matched=[]
    result=entry.get('result') or {}
    for span in result.get('spans',[]):
        b=span_box(span)
        if not b or not compact(span.get('text_raw','')):continue
        old=[l for l in output if l.get('table_index') is None and
             (abs(sum(l['bbox'][1::2])/2-sum(b[1::2])/2)<.4*min(l['bbox'][3]-l['bbox'][1],b[3]-b[1]) and (coverage(l['bbox'],b)>.2 or coverage(b,l['bbox'])>.2))]
        text=span['text_raw']; before=''.join(l['text'] for l in sorted(old,key=lambda l:(l['bbox'][1],l['bbox'][0])))
        if old and compact(before)==compact(text):
            matched.extend(l.get('source_span_id') for l in old);continue
        failure,evidence=candidate_qualification(span,entry,old)
        if failure:
            conflicts.append(dict(bbox_pdf=b,candidate_text=text,original_lines=deepcopy(old),
                differences=differences(before,text),reason=failure+'; not_adopted',candidate_score=span.get('score_raw')))
            continue
        if old:
            # 完整候选提供原检测框外的大量真实字形，而原短识别本身不合格时，
            # 可以恢复整行；不是仅凭新文本较长或分数较高覆盖原字符。
            weak=all(l.get('recognition_evidence',{}).get('score_available') and
                     l['recognition_evidence']['score']<.8 for l in old)
            if weak and evidence['ink'] is not None and len(compact(before))<=2 and len(compact(text))>=len(compact(before))+2:
                from PIL import Image,ImageDraw,ImageChops
                region_mask=Image.new('L',evidence['ink'].size)
                ImageDraw.Draw(region_mask).polygon([evidence['inverse'](*p) for p in span['polygon_page']],fill=255)
                ink=ImageChops.multiply(evidence['ink'],region_mask);mask=ink.copy();draw=ImageDraw.Draw(mask)
                for l in old:draw.rectangle(input_box(l['bbox'],evidence['inverse']),fill=0)
                all_ink=sum(ink.histogram()[1:]);outside=sum(mask.histogram()[1:])
                if all_ink>=20 and outside/all_ink>.65:
                    line=make_line(span);line['recovery_region_id']=entry['region']['region_id']
                    line['replaces_span_ids']=[l.get('source_span_id') for l in old]
                    line['original_source_lines']=deepcopy(old)
                    line['replacement_evidence']=dict(reason='weak_short_detection_with_visible_uncovered_line',outside_ink_fraction=outside/all_ink,
                                                       original=before,candidate=text,differences=differences(before,text))
                    output=[l for l in output if l not in old];output.append(line);added.append(span['span_id']);continue
            # 检测框的少量边缘交叠不等于同一字；要求新字形主要在原行右侧，且
            # 原行仅末缘与之相交。来源绑定使其在组装时回到同一个区域。
            previous_box=bounds([l['bbox'] for l in old])
            overlap=max(0,previous_box[2]-b[0])
            if (b[0]>previous_box[0] and b[2]>previous_box[2] and
                    overlap<(b[2]-b[0])*.4 and overlap<(previous_box[2]-previous_box[0])*.15):
                gap=missing_fragment_box(entry,span,old,'suffix',evidence)
                if gap:
                    line=make_line(span);line['recovery_region_id']=entry['region']['region_id']
                    line['completion_of_span_ids']=[l.get('source_span_id') for l in old]
                    line['completion_evidence']=dict(side='adjacent_detected_glyph',original=before,candidate=text,visible_gap=gap)
                    output.append(line);added.append(span['span_id']);continue
            previous=compact(before);candidate=compact(text)
            side=('prefix' if previous and candidate.endswith(previous) else
                  'suffix' if previous and candidate.startswith(previous) else None)
            missing=missing_fragment_box(entry,span,old,side,evidence) if side else None
            if missing:
                    fragment=candidate[:-len(previous)] if side=='prefix' else candidate[len(previous):]
                    supplement=deepcopy(span);supplement.update(text_raw=fragment,original_text_raw=text,
                        supporting_line_score=span.get('score_raw'),score_raw=None,score_kind='fragment_not_independently_scored',
                        content_source='visible_uncovered_'+side+'; existing_text_unchanged',
                        polygon_page=[[missing[0],missing[1]],[missing[2],missing[1]],[missing[2],missing[3]],[missing[0],missing[3]]])
                    line=make_line(supplement);line['recovery_region_id']=entry['region']['region_id']
                    line['completion_of_span_ids']=[l.get('source_span_id') for l in old]
                    line['completion_evidence']=dict(side=side,original=before,candidate=text,
                                                    differences=differences(before,text),visible_gap=missing)
                    output.append(line);added.append(span['span_id']);continue
            conflicts.append(dict(bbox_pdf=b,original_lines=deepcopy(old),candidate_text=text,
                differences=differences(before,text),reason='existing_text_differs; original_retained',
                crop_contains_original=True))
            continue
        # 表格或其他候选也不得相互覆盖；历史未记录输入尺寸时不宣称字形完整。
        collisions=[l for l in output if l.get('bbox') and coverage(b,l['bbox'])>.15 and
                    (l.get('table_index') is not None or
                     abs(sum(l['bbox'][1::2])/2-sum(b[1::2])/2)<.4*min(l['bbox'][3]-l['bbox'][1],b[3]-b[1]))]
        if collisions:
            conflicts.append(dict(bbox_pdf=b,candidate_text=text,original_lines=deepcopy(collisions),differences=[],reason='candidate_intersects_existing_object'))
            continue
        line=make_line(span);line['recovery_region_id']=entry['region']['region_id']
        output.append(line);added.append(span['span_id'])
    return output,dict(added_span_ids=added,matched_span_ids=list(dict.fromkeys(matched)),conflicts=conflicts,
                       replacement_span_ids=[],policy='preserve_existing_add_nonoverlapping_complete_lines')
