"""完整印章的曲线文字输入；显式离线检测权重，不加载第二套版面或识别器。"""
from pathlib import Path
import json
import numpy as np


def red_plane_input(image, destination):
    """仅分离红色信号，保持尺寸、位置和信号强弱顺序；不二值化或补笔画。"""
    import cv2
    original=cv2.imread(str(image)).astype(float);b,g,r=original.transpose(2,0,1)
    signal=np.maximum(r-np.maximum(g,b),0)
    if signal.max()<=0:return None
    plane=np.rint(255*(1-signal/signal.max())).astype(np.uint8)
    cv2.imwrite(str(destination),plane)
    return dict(image=str(destination),source_image=str(image),transform='linear_positive_R_minus_max_G_B',
                geometry_unchanged=True,signal_max=float(signal.max()))


def red_signal_preserved(source, image):
    """检查已生成视图的真实像素，不让一个元数据标志冒充保真证据。"""
    from PIL import Image
    try:
        with Image.open(source) as im:rgb=np.asarray(im.convert('RGB'),float)
        with Image.open(image) as im:actual=np.asarray(im.convert('L'))
    except (OSError,TypeError):return False
    signal=np.maximum(rgb[:,:,0]-rgb[:,:,1:3].max(2),0)
    if signal.max()<=0 or actual.shape!=signal.shape:return False
    return bool(np.array_equal(actual,np.rint(255*(1-signal/signal.max())).astype(np.uint8)))


def seal_sequence_geometry(curve,response,target):
    """曲线来源和文本输入分开记录，不将曲线宽度伪装成逐字坐标。"""
    from PIL import Image
    with Image.open(response['image']) as im:w,h=im.size;rgb=np.asarray(im.convert('RGB'),float)
    red=response['image']==curve.get('red_plane_validation',{}).get('image')
    from glyph_adjudication import mask_components
    ink=rgb.max(2)<200 if red else ((rgb[:,:,0]-rgb[:,:,1]>40)&(rgb[:,:,0]-rgb[:,:,2]>40))
    components=mask_components(ink);glyphs=[];outline=[]
    for c in components:
        x0,y0,x1,y1=c['box']
        # 曲线展开边缘的整条章圈不是字形。仅排除独立连接的细长边线；
        # 与字形相接、折叠或触及左右切口的文字仍不通过。
        if x1-x0>6*(y1-y0) and (y0==0 or y1==h):outline.append(c['box'])
        else:glyphs.append(c['box'])
    x0=min((b[0] for b in glyphs),default=0);y0=min((b[1] for b in glyphs),default=0)
    x1=max((b[2]-1 for b in glyphs),default=w-1);y1=max((b[3]-1 for b in glyphs),default=h-1)
    return dict(target_polygon_input_px=[[x0,y0],[x1,y0],[x1,y1],[x0,y1]],padding_px=0,
        independent_outline_components=outline,
        role='body' if red else 'seal',preprocessing='faithful_red_signal' if red else 'original',
        red_plane_source=curve['image'] if red else None,source_input=dict(image=target['image'],
        input_to_page=target['input_to_page']),source_curve_polygon_page=curve['polygon_page'],
        method='verified_complete_seal_curve',source_target_complete=True)


def repair_annular_curve(target, curve, destination):
    """圆形章的连续径向展开。源曲线给出角度和厚度，不拼接独立四边形。"""
    import cv2
    image=cv2.imread(target['image']);h,w=image.shape[:2]
    b,g,r=image.astype(float).transpose(2,0,1)
    red=((r-g>35)&(r-b>35)).astype(np.uint8)
    points=cv2.findNonZero(red)
    if points is None or len(points)<20:return None
    hull=cv2.convexHull(points)
    if len(hull)<5:return None
    ellipse=cv2.fitEllipse(hull);(cx,cy),(ew,eh),angle=ellipse
    if min(ew,eh)<64 or max(ew,eh)/min(ew,eh)>1.5:return None
    inverse=np.linalg.inv(np.asarray(target['input_to_page'],float))
    p=np.c_[curve['polygon_page'],np.ones(len(curve['polygon_page']))]@inverse.T
    p=p[:,:2]/p[:,2:]-target['crop_origin_px']
    mask=np.zeros((h,w),np.uint8);cv2.fillPoly(mask,[np.rint(p).astype(np.int32)],1)
    # 在实测椭圆坐标里采样，避免弧形两边配对翻折。角度分辨率取源周长。
    count=int(np.ceil(np.pi*max(ew,eh)));radial=int(np.ceil(max(ew,eh)/2))
    theta=np.linspace(0,2*np.pi,count,endpoint=False);radius=np.linspace(0,1.05,radial)
    rotation=np.radians(angle);xx=np.cos(theta)[None,:]*radius[:,None]*ew/2;yy=np.sin(theta)[None,:]*radius[:,None]*eh/2
    mx=(cx+xx*np.cos(rotation)-yy*np.sin(rotation)).astype(np.float32)
    my=(cy+xx*np.sin(rotation)+yy*np.cos(rotation)).astype(np.float32)
    sampled=cv2.remap(mask,mx,my,cv2.INTER_NEAREST,borderMode=cv2.BORDER_CONSTANT)
    active=sampled.sum(0)>=3
    gaps=[]
    for start in np.where(~active & np.roll(active,1))[0]:
        length=0
        while length<count and not active[(start+length)%count]:length+=1
        gaps.append((length,int(start)))
    if not gaps:return None
    gap,start=max(gaps);indices=(np.arange(count-gap)+start+gap)%count
    if len(indices)<count*.25:return None
    bounds=[]
    for col in indices:
        rs=np.flatnonzero(sampled[:,col])
        if len(rs):bounds.append((int(rs[0]),int(rs[-1])))
    if not bounds:return None
    # 保留整条曲线的最大字高，不让模型多边形边缘抖动逐列挤压或截短笔画。
    lo=min(a for a,b in bounds);hi=max(b for a,b in bounds)
    height=hi-lo+1
    maps=[]
    for k,col in enumerate(indices):
        rad=np.linspace(radius[hi],radius[lo],height)
        x=np.cos(theta[col])*rad*ew/2;y=np.sin(theta[col])*rad*eh/2
        maps.append((cx+x*np.cos(rotation)-y*np.sin(rotation),cy+x*np.sin(rotation)+y*np.cos(rotation)))
    mapx=np.array([x for x,y in maps],np.float32).T;mapy=np.array([y for x,y in maps],np.float32).T
    crop=cv2.remap(image,mapx,mapy,cv2.INTER_CUBIC,borderMode=cv2.BORDER_CONSTANT,borderValue=(255,255,255))
    cv2.imwrite(str(destination),crop)
    return dict(image=str(destination),rectification='continuous_elliptic_rays',
                repair_evidence=dict(ellipse=[list(ellipse[0]),list(ellipse[1]),ellipse[2]],
                    angle_samples=len(indices),radial_samples=height,continuous_nonfolding_map=True,
                    source_polygon_px=p.tolist(),source_image=target['image']))


def curve_integrity(target, curve):
    """核查官方展开实际使用的源四边形，而不是把展开后的白边当完整证据。"""
    import cv2
    from shapely.geometry import Polygon
    from paddlex.inference.pipelines.components import CropByPolys
    from paddlex.inference.pipelines.components.common.seal_det_warp import CurveTextRectifier
    image=cv2.imread(target['image'])
    inverse=np.linalg.inv(np.asarray(target['input_to_page'],float))
    points=np.c_[curve['polygon_page'],np.ones(len(curve['polygon_page']))]@inverse.T
    points=np.rint(points[:,:2]/points[:,2:]-target['crop_origin_px']).astype(np.int32)
    h,w=image.shape[:2];polygon=Polygon(points)
    evidence=dict(eligible=False,source_polygon_px=points.tolist(),folded_strips=[])
    if not polygon.is_valid or polygon.area<=0:
        return dict(evidence,reason='invalid_source_polygon')
    if np.any(points<1) or np.any(points[:,0]>=w-1) or np.any(points[:,1]>=h-1):
        return dict(evidence,reason='curve_reaches_real_crop_boundary')
    rectangle=Polygon(cv2.boxPoints(cv2.minAreaRect(points)))
    ratio=polygon.intersection(rectangle).area/polygon.union(rectangle).area
    evidence['rectangle_iou']=ratio
    repaired=curve.get('rectification')=='continuous_elliptic_rays' and curve.get('repair_evidence',{}).get('continuous_nonfolding_map')
    if repaired:evidence['continuous_repair']=curve['repair_evidence']
    if ratio<.7 and not repaired:
        crop=CropByPolys('poly');sample=crop.sample_points_on_bbox(points).astype(np.int32)
        _,_,top,bottom=crop.reorder_poly_edge(sample)
        top=crop.sample_points_on_bbox_bp(top,15);bottom=crop.sample_points_on_bbox_bp(bottom,15)
        if np.mean(top,0)[1]>np.mean(bottom,0)[1]:top,bottom=bottom,top
        outline=np.concatenate([top,bottom]).reshape(-1).tolist();rectifier=CurveTextRectifier()
        source,_,_=rectifier.horizontal_text_process(outline) if rectifier.horizontal_text_estimate(outline) else rectifier.vertical_text_process(outline,(w,h))
        source=source.reshape(-1,2)
        for j in range(len(source)//2-1):
            quad=np.concatenate([source[j:j+2],source[::-1][j:j+2][::-1]]).astype(np.float32)
            if not cv2.isContourConvex(quad):evidence['folded_strips'].append(j)
        if evidence['folded_strips']:return dict(evidence,reason='source_strips_fold_or_cross')
    mask=np.zeros((h,w),np.uint8);cv2.fillPoly(mask,[points],1)
    b,g,r=image.astype(float).transpose(2,0,1)
    ink=(np.minimum(b,g)<180)&(mask>0);red=(r-g>35)&(r-b>35)&ink
    neutral=(r<150)&(g<150)&(b<150)&ink&~red
    evidence.update(red_ink_pixels=int(red.sum()),red_ink_fraction=float(red.sum()/max(ink.sum(),1)),
                    neutral_dark_fraction=float(neutral.sum()/max(ink.sum(),1)))
    if red.sum()<20 or evidence['red_ink_fraction']<.85 or evidence['neutral_dark_fraction']>=.05:
        validation=curve.get('red_plane_validation',{});response=validation.get('response',{})
        text=response.get('text');tokens=response.get('ctc',{}).get('tokens',[])
        proven=(red.sum()>=20 and validation.get('geometry_unchanged') and
                validation.get('source_image')==curve['image'] and
                red_signal_preserved(curve['image'],validation.get('image')))
        if not proven:return dict(evidence,reason='curve_role_mixed_or_not_red_text')
        evidence['red_plane_validation']={k:v for k,v in validation.items() if k!='response'}
        evidence['red_plane_validation'].update(text=response.get('text'),model=response.get('model'),
            full_response_source='seal_review.curves.red_plane_validation.response',
            scope='pixel_integrity_only; text_requires_independent_sequence_verification')
    return dict(evidence,eligible=True,reason='complete_red_curve_without_folded_source_strips')


def red_view_required(target, curve):
    """图像/角色触发视图，与旧识别是否同文无关；不修复无效多边形。"""
    evidence=curve_integrity(target,{k:v for k,v in curve.items() if k not in ('integrity','red_plane_validation')})
    return evidence.get('reason')=='curve_role_mixed_or_not_red_text' and evidence.get('red_ink_pixels',0)>=20


def select_seal_evidence(curve):
    """颜色视图可提出新文本，保留原色候选；几何和逐位置检查由调用者完成。"""
    original=curve.get('primary',{});auxiliary=curve.get('auxiliary',{})
    validation=curve.get('red_plane_validation',{});red=validation.get('response',{})
    red_primary=validation.get('primary_response',{})
    if (validation.get('geometry_unchanged') and validation.get('source_image')==curve.get('image')
            and red.get('status')=='completed' and red.get('text')):
        if red_primary.get('status')=='completed' and red_primary.get('text',red_primary.get('text_assembled'))==red['text']:
            return red['text'],red,red_primary,'faithful_red_signal'
        if original.get('text')==red['text']:
            return red['text'],red,original,'original_and_red_signal'
    return original.get('text',''),auxiliary,original,'original_color'


def review_red_view(target, curve, auxiliary, engine, destination, owner):
    if not red_view_required(target,curve):return
    validation=curve.get('red_plane_validation')
    if not validation or validation.get('source_image')!=curve['image']:
        validation=red_plane_input(curve['image'],destination)
    if not validation:return
    if not validation.get('response'):
        validation['response']=auxiliary.predict(validation['image'],candidates=[curve.get('primary',{}).get('text',''),curve.get('auxiliary',{}).get('text','')])
    red=validation['response']
    if red.get('text') and red['text']!=curve.get('primary',{}).get('text') and not validation.get('primary_response'):
        validation['primary_response']=engine.predict(dict(sample_id=owner+'-red',image=validation['image'],
            granularity='line-rec',stage='seal_red_signal_review',input_to_page=[[1,0,0],[0,1,0],[0,0,1]]),60)
    curve['red_plane_validation']=validation;curve.pop('integrity',None)


def adjudicate_seals(page):
    """完整曲线判定与原碎片的像素覆盖对应。禁止用框相交或字符串包含解除问题。"""
    primary=page.get('original_page_result') or page['page_result']
    if not primary.get('input',{}).get('image'):
        page['seal_adjudications']=[];page['seal_adjudication_failure']='source_image_unavailable'
        return []
    import cv2
    from copy import deepcopy
    image=cv2.imread(primary['input']['image']);h,w=image.shape[:2]
    inverse=np.linalg.inv(np.asarray(primary['input']['input_to_page'],float))
    b,g,r=image.astype(float).transpose(2,0,1)
    ink=np.minimum(b,g)<180;red=(r-g>35)&(r-b>35)&(ink);neutral=(r<150)&(g<150)&(b<150)&ink&~red
    # 完整章可跨单元格；已有可见网格不能作为章框中的黑色正文。
    # 只沿原图实际连续的网格中心线计账，不消除红章或未知黑字。
    grid=np.zeros((h,w),np.uint8)
    for table in page.get('tables',[]):
        for cell in table.get('cells',[]):
            source=cell.get('original_result',cell.get('result',{})).get('input',{})
            if source.get('geometry_origin')!='automatic_visible_grid' or not cell.get('bbox_pdf'):continue
            x0,y0,x1,y1=cell['bbox_pdf']
            vertices=np.array([[x0,y0,1],[x1,y0,1],[x1,y1,1],[x0,y1,1]])@inverse.T
            vertices=vertices[:,:2]/vertices[:,2:]
            for first,last in zip(vertices,np.roll(vertices,-1,axis=0)):
                length=max(1,int(np.linalg.norm(last-first)));samples=np.rint(np.linspace(first,last,length)).astype(int)
                samples=samples[(samples[:,0]>=0)&(samples[:,0]<w)&(samples[:,1]>=0)&(samples[:,1]<h)]
                if len(samples)<32 or float(ink[samples[:,1],samples[:,0]].mean())<.8:continue
                cv2.line(grid,tuple(np.rint(first).astype(int)),tuple(np.rint(last).astype(int)),1,1)
    grid_neutral=(grid>0)&neutral
    ink=ink&~grid_neutral;neutral=neutral&~grid_neutral
    def mask(poly):
        points=np.c_[poly,np.ones(len(poly))]@inverse.T
        m=np.zeros((h,w),np.uint8);cv2.fillPoly(m,[np.rint(points[:,:2]/points[:,2:]).astype(np.int32)],1)
        return m>0
    spans=list(primary.get('spans',[]))
    for table in page.get('tables',[]):
        for cell in table.get('cells',[]):spans.extend(cell.get('result',{}).get('spans',[]))
    decisions=[]
    for row in page.get('seal_review',[]):
        for index,curve in enumerate(row['curves']):
            integrity=curve.get('integrity') or curve_integrity(row['target'],curve)
            curve['integrity']=integrity
            text,response,primary_support,selected_view=select_seal_evidence(curve)
            tokens=response.get('ctc',{}).get('tokens',[])
            decision=dict(seal_index=row['target']['seal_index'],curve_index=index,text=text,
                          polygon_page=deepcopy(curve['polygon_page']),integrity=deepcopy(integrity),
                          seal_polygon_page=deepcopy(row['target'].get('layout_polygon_page',row['target']['polygon_page'])),
                          accepted=False,covered_sources=[],reason='curve_input_unverified')
            decisions.append(decision)
            if not integrity['eligible']:continue
            finite=curve.get('sequence_review')
            if finite:
                from sequence_adjudication import decide_sequence
                candidates=finite.get('candidate_texts',[])
                verification=decide_sequence(candidates[0],candidates,finite,finite.get('source_geometry',{})) if candidates else {}
                decision['complete_sequence_review']=verification
                if not verification.get('whole_sequence_review',{}).get('complete'):
                    decision['reason']='curve_complete_sequence_unresolved';continue
                text=verification['text'];decision['text']=text;response=finite
                primary_support=finite.get('primary_sequence_review',primary_support)
                selected_view='finite_verified_red_signal' if finite['source_geometry'].get('preprocessing')=='faithful_red_signal' else 'finite_verified_original_curve'
                tokens=finite.get('ctc',{}).get('tokens',[])
            if not finite and (response.get('status')!='completed' or not text or response.get('text')!=text):
                decision['reason']='distinct_weights_disagree_or_unreadable';continue
            if not finite and (''.join(t['text'] for t in tokens)!=text or not tokens or any(
                    type(t.get('posterior')) not in (float,int) or not .97<=t['posterior']<=1 for t in tokens)):
                decision['reason']='curve_contains_unverified_sequence_positions';continue
            decision.update(accepted=True,reason='complete_red_curve_geometry_and_independent_sequence_support',
                            sequence_evidence=deepcopy(response.get('sequence_evidence',{})),
                            primary_evidence=deepcopy(primary_support),selected_view=selected_view,
                            original_color_text=curve.get('primary',{}).get('text',''))
            curve_mask=mask(curve['polygon_page'])
            for span in spans:
                if not span.get('polygon_page'):continue
                source_mask=mask(span['polygon_page']);colored=source_mask&ink;red_source=source_mask&red
                count=int(colored.sum());red_count=int(red_source.sum())
                fraction=red_count/max(count,1);dark=float((source_mask&neutral).sum()/max(count,1))
                covered=float((red_source&curve_mask).sum()/max(red_count,1))
                if red_count>=20 and fraction>=.85 and dark<.05 and covered>=.98:
                    decision['covered_sources'].append(dict(source_span_id=span['span_id'],text=span['text_raw'],
                        polygon_page=deepcopy(span['polygon_page']),red_ink_fraction=fraction,
                        neutral_dark_fraction=dark,red_ink_coverage=covered,red_ink_pixels=red_count,
                        verified_grid_pixels=int((source_mask&grid_neutral).sum())))
    # 一个旧检测框可能跨同一章的两条已核实曲线；验证覆盖取实际曲线墨迹并集。
    # 不跨章合并，也不以整章外接框代替字形覆盖。
    groups={}
    for decision in decisions:
        if decision['accepted']:groups.setdefault(decision['seal_index'],[]).append(decision)
    for group in groups.values():
        if len(group)<2:continue
        union=np.zeros((h,w),bool)
        for decision in group:union |= mask(decision['polygon_page'])
        existing={s['source_span_id'] for decision in group for s in decision['covered_sources']}
        for span in spans:
            if not span.get('polygon_page') or span['span_id'] in existing:continue
            source_mask=mask(span['polygon_page']);count=int((source_mask&ink).sum());red_count=int((source_mask&red).sum())
            covered=int((source_mask&red&union).sum())/max(red_count,1)
            fraction=red_count/max(count,1);dark=int((source_mask&neutral).sum())/max(count,1)
            if red_count>=20 and fraction>=.85 and dark<.05 and covered>=.98:
                group[0]['covered_sources'].append(dict(source_span_id=span['span_id'],text=span['text_raw'],
                    polygon_page=deepcopy(span['polygon_page']),red_ink_fraction=fraction,
                    neutral_dark_fraction=dark,red_ink_coverage=covered,red_ink_pixels=red_count,
                    coverage_curve_indices=[d['curve_index'] for d in group],
                    coverage_scope='same_seal_verified_curve_union'))
    page['seal_adjudications']=decisions
    return decisions


def complete_seal_sequences(page,engine,model_dir):
    """只补未完成曲线缺少的主权重有限评分，复用既有检测与辅助帧证据。"""
    from copy import deepcopy
    from bounded_review import complete_sequence_reviews,complete_final_sequences
    from glyph_adjudication import input_evidence
    before=adjudicate_seals(page);scoring=[]
    for row in page.get('seal_review',[]):
        for index,curve in enumerate(row['curves']):
            decision=next((d for d in before if d['seal_index']==row['target']['seal_index'] and d['curve_index']==index),None)
            if not decision:continue
            if decision['accepted'] or not decision['integrity'].get('eligible'):continue
            red=curve.get('red_plane_validation',{}).get('response',{})
            response=red if red.get('status')=='completed' else curve.get('auxiliary',{})
            if response.get('sequence_evidence',{}).get('primary_text') is None:continue
            geometry=seal_sequence_geometry(curve,response,row['target'])
            qualification=input_evidence(response['image'],metadata=geometry)
            if not qualification['eligible']:
                curve['sequence_input_observation']=qualification;continue
            finite=deepcopy(response);finite['source_geometry']=geometry
            curve['sequence_review']=finite
            scoring.append((f"seal-{row['target']['seal_index']}-{index}",finite))
    primary=page.get('original_page_result') or page['page_result']
    complete_sequence_reviews(primary,scoring,engine)
    complete_final_sequences(primary,scoring,engine,model_dir)
    adjudicate_seals(page)
    return len(scoring)


def adopt_verified_seals(lines, page):
    from copy import deepcopy
    from experimental_parser import box
    result=deepcopy(lines);used=set();applied=[]
    for decision in page.get('seal_adjudications',[]):
        if not decision.get('accepted') or not decision.get('integrity',{}).get('eligible'):continue
        sources={s['source_span_id']:s for s in decision['covered_sources']}
        members=[l for l in result if l.get('source_span_id') in sources]
        if any(l.get('human_revision') or l['text']!=sources[l['source_span_id']]['text'] for l in members):continue
        ids=[l['source_span_id'] for l in members]
        if used.intersection(ids):continue
        curve_id=f"seal:{decision['seal_index']}:curve:{decision['curve_index']}"
        if any(l.get('source_span_id')==curve_id for l in result):continue
        position=min((result.index(l) for l in members),default=len(result))
        replacement=dict(text=decision['text'],bbox=box(decision['polygon_page']),polygon=deepcopy(decision['polygon_page']),
                         source_span_id=ids[0] if ids else curve_id,source_span_ids=ids or [curve_id],source='verified_seal_curve',granularity='seal_curve',
                         original_source_lines=deepcopy(members),seal_verification=deepcopy(decision))
        result=[l for l in result if l not in members];result.insert(position,replacement)
        used.update(ids);applied.append(dict(**deepcopy(decision),applied_source_span_ids=ids))
    return result,applied


def attach_authority_values(view):
    """已有唯一邻接关系加完整已验证弧形章文；共享组装调用，保存刷新不再走另一套取值。"""
    from copy import deepcopy
    from experimental_parser import box
    associations=associate_authority_regions(view,{})
    for relation in associations:
        # 章对象必须与版面章对象完全同源。不是用文字框相交猜关系。
        matches=[d for d in view.get('applied_seal_adjudications',[]) if d.get('accepted')
                 and box(d['seal_polygon_page'])==relation['seal_bbox_pdf']
                 and d['integrity'].get('rectangle_iou',1)<.7 and not d['text'].isdigit()]
        if len(matches)!=1:continue
        decision=matches[0]
        values=[l for l in view.get('lines',[]) if l.get('seal_verification')==decision or
                (l.get('seal_verification',{}).get('seal_index')==decision['seal_index'] and
                 l.get('seal_verification',{}).get('curve_index')==decision['curve_index'])]
        if len(values)!=1:continue
        field=next(e for e in view['readable_elements'] if e['region_id']==relation['field_region_id'])
        if field.get('human_revision') or any(l.get('human_revision') for l in field.get('source_lines',[])):continue
        old_value=field.get('value') or ''
        if old_value:
            # 只补同位置机关残缺值；字符冲突不靠机关格式或更长文字覆盖。
            iterator=iter(decision['text'])
            if not all(any(ch==other for other in iterator) for ch in old_value):continue
            field['previous_effective_value']=old_value
        field['value']=decision['text'];field['text']=field['label']+'：'+decision['text']
        field['source_lines'].extend(deepcopy(values))
        field['source_span_ids']=list(dict.fromkeys(sid for l in field['source_lines'] for sid in l.get('source_span_ids',[l['source_span_id']])))
        field['source_polygons']=[deepcopy(l.get('polygon')) for l in field['source_lines']]
        boxes=[field['bbox_pdf'],values[0]['bbox']]
        field['bbox_pdf']=[min(b[0] for b in boxes),min(b[1] for b in boxes),max(b[2] for b in boxes),max(b[3] for b in boxes)]
        field['authority_verification']=deepcopy(decision);relation['value_verified']=True
        field['related_seal_region']=deepcopy(relation)
        # 完整章文已成为机关字段的来源，不在正文再插入一遍。证据及原行仍完整保留。
        transferred=set(values[0]['source_span_ids'])
        standalone=[e for e in view['readable_elements'] if e is not field and e.get('label')=='印章文字'
                    and set(e.get('source_span_ids',[]))==transferred]
        view.setdefault('seal_evidence_elements',[]).extend(deepcopy(standalone))
        view['readable_elements'][:]=[e for e in view['readable_elements'] if e not in standalone]
    return associations


def seal_assessments(view, errors):
    from copy import deepcopy
    # 验证覆盖与正文替换不同：格内独立章字无需塞进地址，也可由本页完整章覆盖。
    verified={s['source_span_id']:(s,d)
              for d in view.get('seal_adjudications',view.get('applied_seal_adjudications',[]))
              if d.get('accepted') and d.get('integrity',{}).get('eligible')
              for s in d.get('covered_sources',[])
              if s.get('red_ink_coverage',0)>=.98 and s.get('red_ink_fraction',0)>=.85
              and s.get('neutral_dark_fraction',1)<.05}
    assessments=[]
    for issue in errors:
        match=verified.get(issue.get('source_span_id'))
        if not match or issue.get('code') not in ('ocr_quality','ocr_score_unavailable','ocr_empty'):
            assessments.append((issue,None));continue
        source,decision=match
        if issue.get('text')!=source['text']:
            assessments.append((issue,None));continue
        record=dict(code='ocr_seal_verified',reason='同页完整章曲线已验证该来源的全部红色墨迹；正文替换独立决定，不解除章下正文风险',
                                 original_issue=deepcopy(issue),seal_adjudication=deepcopy(decision))
        assessments.append((issue,record))
    return assessments


def reconcile_seal_issues(view,errors,observations):
    rows=seal_assessments(view,errors)
    observations.extend(record for issue,record in rows if record)
    return [issue for issue,record in rows if not record]


def complete_seal_inputs(page, directory):
    from PIL import Image
    primary=page.get('original_page_result') or page['page_result']
    source=primary['input'];matrix=np.asarray(source['input_to_page'],float)
    inverse=np.linalg.inv(matrix);directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    # 闭合红轮廓可能只是某个字。优先沿用已运行版面的完整章框，
    # 轮廓只补足章框，不将局部字的轮廓当成完整章。
    regions=[]
    boxes=page.get('layout_result',{}).get('raw',{}).get('res',{}).get('boxes',[])
    for box in boxes:
        if box.get('label')!='seal':continue
        x0,y0,x1,y1=box['coordinate']
        corners=np.array([[x0,y0,1],[x1,y0,1],[x1,y1,1],[x0,y1,1]])@matrix.T
        regions.append(dict(polygon_page=(corners[:,:2]/corners[:,2:]).tolist(),source='PP-DocLayoutV3_seal'))
    if not regions:regions=page.get('stamp_regions',[])
    with Image.open(source['image']) as image:
        for index,region in enumerate(regions):
            poly=np.asarray(region['polygon_page'],float)
            points=np.c_[poly,np.ones(len(poly))]@inverse.T
            points=points[:,:2]/points[:,2:]
            lo=np.floor(points.min(0)-12).astype(int);hi=np.ceil(points.max(0)+12).astype(int)
            lo=np.maximum(lo,0);hi=np.minimum(hi,image.size)
            if np.any(hi<=lo):continue
            # 模型框只是建议。闭合红外轮廓跨过建议框时，补足同一印章，保留原版面身份。
            import cv2
            extent=hi-lo;search_lo=np.maximum(0,lo-max(extent));search_hi=np.minimum(image.size,hi+max(extent))
            sample=np.asarray(image.crop(tuple(search_lo)+tuple(search_hi)).convert('RGB'),float)
            red=((sample[:,:,0]-sample[:,:,1]>35)&(sample[:,:,0]-sample[:,:,2]>35)).astype(np.uint8)
            closed=cv2.morphologyEx(red,cv2.MORPH_CLOSE,np.ones((3,3),np.uint8))
            expansions=[]
            contours=list(cv2.findContours(closed,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)[0])
            # 黑色日期可能将红圆边截成数段，整体凸包只用于几何范围，不改识别图。
            points_red=cv2.findNonZero(red)
            if points_red is not None:contours.append(cv2.convexHull(points_red))
            for contour in contours:
                hull=cv2.convexHull(contour);x,y,w,h=cv2.boundingRect(hull);perimeter=cv2.arcLength(hull,True)
                circularity=4*np.pi*cv2.contourArea(hull)/max(perimeter**2,1)
                left=search_lo+[x,y];right=left+[w,h]
                overlap=np.maximum(0,np.minimum(right,hi)-np.maximum(left,lo)).prod()
                if min(w,h)>=min(extent)*.7 and .75<w/h<1.33 and circularity>=.7 and overlap>=min(extent.prod(),w*h)*.5:
                    if not any(np.array_equal(left,a) and np.array_equal(right,b) for a,b in expansions):expansions.append((left,right))
            expansions=[e for e in expansions if not any(np.all(other[0]<=e[0]) and np.all(other[1]>=e[1]) and
                        (np.any(other[0]<e[0]) or np.any(other[1]>e[1])) for other in expansions)]
            layout_poly=poly.copy()
            if len(expansions)==1:
                left,right=expansions[0];lo=np.maximum(0,np.minimum(lo,left-12));hi=np.minimum(image.size,np.maximum(hi,right+12))
                corners=np.array([[lo[0],lo[1],1],[hi[0],lo[1],1],[hi[0],hi[1],1],[lo[0],hi[1],1]])@matrix.T
                poly=corners[:,:2]/corners[:,2:]
            path=directory/f'seal-{index}.png';image.crop(tuple(lo)+tuple(hi)).save(path)
            yield dict(seal_index=index,image=str(path),polygon_page=poly.tolist(),
                       layout_polygon_page=layout_poly.tolist(),
                       crop_origin_px=lo.tolist(),input_to_page=matrix.tolist(),
                       source_input=source,role='seal',source=region.get('source'))


class SealCurveDetector:
    def __init__(self,model_dir):
        import os
        os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK']='True'
        from paddleocr import SealTextDetection
        from paddlex.inference.pipelines.components import CropByPolys
        if not Path(model_dir).is_dir():raise FileNotFoundError(model_dir)
        self.model=SealTextDetection(model_name='PP-OCRv4_server_seal_det',model_dir=str(model_dir),
                                    device='cpu',cpu_threads=2,enable_mkldnn=False)
        self.crop=CropByPolys(det_box_type='poly')

    def detect(self,target,directory):
        import cv2
        directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
        result=list(self.model.predict(target['image']))[0].json
        if isinstance(result,str):result=json.loads(result)
        result=result.get('res',result);polys=result.get('dt_polys',[])
        image=cv2.imread(target['image']);rows=[]
        for index,(poly,crop) in enumerate(zip(polys,self.crop(image,polys))):
            path=directory/f'curve-{index}.png';cv2.imwrite(str(path),crop)
            points=np.asarray(poly,float)+target['crop_origin_px']
            points=np.c_[points,np.ones(len(points))]@np.asarray(target['input_to_page']).T
            rows.append(dict(image=str(path),polygon_page=(points[:,:2]/points[:,2:]).tolist(),
                             detector_score=result.get('dt_scores',[None]*len(polys))[index],
                             role='seal',rectification='official_CropByPolys',seal_index=target['seal_index']))
        return rows

    def close(self):self.model.close()


def associate_authority_regions(view,page):
    """本页显式机关标签的唯一邻接/叠印关系，空值及未确认残缺值均检查。"""
    from copy import deepcopy
    regions=[r for r in view.get('layout_regions',[]) if r.get('label')=='seal']
    associations=[]
    for element in view.get('readable_elements',[]):
        element.pop('related_seal_region',None)
        if element.get('label') not in ('登记机关','发证机关'):continue
        if element.get('human_revision') or any(l.get('human_revision') for l in element.get('source_lines',[])):continue
        b=element.get('bbox_pdf');matches=[]
        if not b:continue
        height=b[3]-b[1]
        for region in regions:
            q=region['bbox_pdf']
            if min(b[3],q[3])<=max(b[1],q[1]):continue
            gap=q[0]-b[2]
            overlap_width=min(b[2],q[2])-max(b[0],q[0])
            if (-height<=gap<=max(height*4,b[2]-b[0]) or
                    (element.get('value') and overlap_width>0 and q[0]>=b[0])):matches.append(region)
        if len(matches)!=1:continue
        relation=dict(field_region_id=element['region_id'],field_source_span_ids=element.get('source_span_ids',[]),
                      seal_region_id=matches[0]['region_id'],seal_bbox_pdf=matches[0]['bbox_pdf'],
                      evidence='unique_same_page_label_adjacent_or_overprinted_seal',value_verified=False,
                      previous_value=element.get('value') or '')
        element['related_seal_region']=deepcopy(relation);associations.append(relation)
    view['authority_region_associations']=associations
    return associations


def review_page_seals(page,detector_dir,recognizer_dir,engine,directory):
    """非默认局部路径。专用检测完成即释放；识别复用主引擎和既有辅助权重。"""
    from auxiliary_recognition import AuxiliaryRecognizer
    directory=Path(directory);rows=[];detector=SealCurveDetector(detector_dir)
    try:
        for target in complete_seal_inputs(page,directory):
            curves=detector.detect(target,directory/str(target['seal_index']))
            for index,curve in enumerate(curves):
                integrity=curve_integrity(target,curve)
                if integrity['reason']=='source_strips_fold_or_cross':
                    repaired=repair_annular_curve(target,curve,directory/f"continuous-{target['seal_index']}-{index}.png")
                    if repaired:
                        curve['previous_geometry']=dict(image=curve['image'],integrity=integrity)
                        curve.update(repaired)
            rows.append(dict(target=target,curves=curves))
    finally:detector.close()
    for row in rows:
        for index,curve in enumerate(row['curves']):
            # 曲线展开不是仿射变换，不伪造逐字页面坐标；原曲线单独保留。
            owner=(page.get('original_page_result') or page['page_result']).get('sample_id','page')
            sample=dict(sample_id=f"{owner}-seal-{row['target']['seal_index']}-{index}",image=curve['image'],
                        granularity='line-rec',stage='seal_curve_review',input_to_page=[[1,0,0],[0,1,0],[0,0,1]])
            response=engine.predict(sample,60)
            curve['primary_result']=response
            curve['primary']=dict(text=response.get('text_assembled',''),score_scope='curve_line')
            curve['coordinate_note']='recognizer coordinates are rectified input pixels; page polygon is detector evidence'
    auxiliary=AuxiliaryRecognizer(recognizer_dir)
    try:
        for row in rows:
            for curve in row['curves']:
                curve['auxiliary']=auxiliary.predict(curve['image'],candidates=[curve['primary']['text']])
                curve['state']='candidate_pending_curve_integrity_and_character_verification'
                integrity=curve_integrity(row['target'],curve)
                owner=(page.get('original_page_result') or page['page_result']).get('sample_id','page')+f"-seal-{row['target']['seal_index']}-{row['curves'].index(curve)}"
                review_red_view(row['target'],curve,auxiliary,engine,directory/f"red-{owner}.png",owner)
        page['seal_review']=rows
        decisions=adjudicate_seals(page)
        for decision in decisions:
            row=next(r for r in rows if r['target']['seal_index']==decision['seal_index'])
            index=decision['curve_index'];curve=row['curves'][index];integrity=decision['integrity']
            if (decision['accepted'] or curve.get('rectification')=='continuous_elliptic_rays'
                    or integrity.get('rectangle_iou',1)>=.7 or integrity.get('red_ink_pixels',0)<20):continue
            repaired=repair_annular_curve(row['target'],curve,directory/f"continuous-review-{decision['seal_index']}-{index}.png")
            if not repaired:continue
            from copy import deepcopy
            previous=deepcopy(curve);curve.update(repaired);curve.setdefault('previous_attempts',[]).append(previous)
            curve.pop('integrity',None);curve.pop('red_plane_validation',None)
            owner=(page.get('original_page_result') or page['page_result']).get('sample_id','page')
            response=engine.predict(dict(sample_id=f"{owner}-seal-{decision['seal_index']}-{index}-continuous",
                image=curve['image'],granularity='line-rec',stage='seal_curve_input_repair',input_to_page=[[1,0,0],[0,1,0],[0,0,1]]),60)
            curve['primary_result']=response;curve['primary']=dict(text=response.get('text_assembled',''),score_scope='curve_line')
            curve['auxiliary']=auxiliary.predict(curve['image'],candidates=[curve['primary']['text']])
            checked=curve_integrity(row['target'],curve)
            review_red_view(row['target'],curve,auxiliary,engine,directory/f"red-continuous-{decision['seal_index']}-{index}.png",owner+f'-continuous-{index}')
    finally:auxiliary.close()
    page['seal_review']=rows
    complete_seal_sequences(page,engine,recognizer_dir)
    return page


def unresolved_seal_objects(view, errors):
    result=[]
    for element in view['readable_elements']:
        if element.get('layout_label')!='seal' or element.get('label')=='印章文字' or not element.get('text','').strip():continue
        siblings=[e for e in view['readable_elements'] if e.get('label')=='印章文字'
                  and e.get('region_id','').startswith(element['region_id']+':seal:')]
        if not siblings:continue
        source_ids=set(element.get('source_span_ids',[]))
        if any(e.get('source_span_id') in source_ids for e in errors):continue
        completed={sid for line in element.get('source_lines',[]) if line.get('completion_evidence')
                   for sid in [line['source_span_id'],*line.get('completion_of_span_ids',[])]}
        nonempty={line['source_span_id'] for line in element.get('source_lines',[]) if line.get('text','').strip()}
        if nonempty and nonempty.issubset(completed):continue
        result.append((element,siblings))
    return result
