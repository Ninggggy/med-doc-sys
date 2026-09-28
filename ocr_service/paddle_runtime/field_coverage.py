"""字段及布局覆盖使用实际字形像素；空值本身不构成错误。"""
from copy import deepcopy
from assembly import bounds,coverage
from regional_adoption import input_geometry,input_box


def field_value_geometry(result, span):
    """从完整标签行的实际冒号和字形取得值区，不按字符数估算位置。"""
    import numpy as np
    from PIL import Image
    geometry=input_geometry(result)
    if not geometry or not span.get('polygon_page'):return None
    points=np.array([geometry[1](*p) for p in span['polygon_page']],float)
    q=[float(points[:,0].min()),float(points[:,1].min()),float(points[:,0].max()),float(points[:,1].max())]
    source_input_box=list(q)
    with Image.open(result['input']['image']) as im:
        q=[max(0,int(q[0])-5),max(0,int(q[1])-5),min(im.width,int(q[2])+5),min(im.height,int(q[3])+5)]
        rgb=np.asarray(im.convert('RGB').crop(q),float)
    # 深色核心只用于定位；完整输入检查仍独立检查浅色笔画和边界。
    red=(rgb[:,:,0]-rgb[:,:,1]>35)&(rgb[:,:,0]-rgb[:,:,2]>35)
    mask=((rgb.max(2)<150)&~red).astype('uint8')
    yy,xx=np.nonzero(mask);remaining=set(zip(xx.tolist(),yy.tolist()));pieces=[]
    while remaining:
        seed=remaining.pop();stack=[seed];points=[seed]
        while stack:
            x,y=stack.pop()
            for dx in (-1,0,1):
                for dy in (-1,0,1):
                    p=(x+dx,y+dy)
                    if p in remaining:remaining.remove(p);stack.append(p);points.append(p)
        if len(points)<6:continue
        a=np.asarray(points);lo=a.min(0);hi=a.max(0)
        pieces.append(([int(lo[0]),int(lo[1]),int(hi[0]-lo[0]+1),int(hi[1]-lo[1]+1),len(points)],a.mean(0)))
    large=[s[3] for s,c in pieces if s[3]>.4*mask.shape[0] and s[0]<mask.shape[1]*.8]
    if not large:return None
    height=float(np.median(large));pairs=[]
    for s,c in pieces:
        x,y,w,h,area=map(int,s)
        if x<mask.shape[1]/2 or max(w,h)>.3*height:continue
        for t,d in pieces:
            xx,yy,ww,hh,aa=map(int,t)
            if yy<=y+h or abs(c[0]-d[0])>max(w,ww)*.35:continue
            if max(ww,hh)>.3*height or not .2*height<yy-y<.6*height:continue
            if min(area,aa)/max(area,aa)<.6:continue
            right=max(x+w,xx+ww);mid=(y+yy+hh)/2
            # 完整小字行可比标签矮；用实际基线及完整组件确定范围。
            values=[a for a,b in pieces if a[0]>right+.2*height and
                    b[1]>mid-.35*height and b[1]<mid+.4*height and
                    a[1]+a[3]>=yy+hh-.25*height]
            if not values:continue
            box=[min(a[0] for a in values),min(a[1] for a in values),
                 max(a[0]+a[2] for a in values),max(a[1]+a[3] for a in values)]
            pairs.append((right,box,[x,y,max(x+w,xx+ww),yy+hh]))
    if not pairs:return None
    # 标签后的第一个独立分隔符；值中字形的上下两点不是第二个标签。
    _,b,colon=min(pairs,key=lambda p:p[0]);matrix=np.asarray(result['input']['input_to_page'],float)
    def page_polygon(box):
        x0,y0,x1,y1=box
        p=np.array([[x0+q[0],y0+q[1],1],[x1+q[0],y0+q[1],1],
                    [x1+q[0],y1+q[1],1],[x0+q[0],y1+q[1],1]])@matrix.T
        return (p[:,:2]/p[:,2:]).tolist()
    polygon=page_polygon(b)
    label_box=[source_input_box[0]-q[0],source_input_box[1]-q[1],
               colon[2],source_input_box[3]-q[1]]
    return dict(method='observed_label_separator_and_value_baseline',polygon_page=polygon,
        value_bbox_pdf=[min(p[0] for p in polygon),min(p[1] for p in polygon),
                        max(p[0] for p in polygon),max(p[1] for p in polygon)],separator_polygon_page=page_polygon(colon),
        label_polygon_page=page_polygon(label_box),
        source_span_id=span['span_id'],source_polygon_page=span['polygon_page'])


def visible_ink(result, box):
    """返回中性深色连通字形，排除红章、细线和零散背景点。"""
    geometry=input_geometry(result)
    if not geometry:return None
    from PIL import Image
    import numpy as np
    try:
        with Image.open(result['input']['image']) as im:
            q=input_box(box,geometry[1]);q=[max(0,int(q[0])),max(0,int(q[1])),min(im.width,int(q[2]+1)),min(im.height,int(q[3]+1))]
            if q[2]<=q[0] or q[3]<=q[1]:return []
            rgb=np.asarray(im.convert('RGB').crop(q),dtype=float)
    except (OSError,KeyError):return None
    mask=(rgb.max(axis=2)<150);ys,xs=np.nonzero(mask);remaining=set(zip(xs.tolist(),ys.tolist()));components=[]
    minimum_height=max(3,(q[3]-q[1])*.22)
    while remaining:
        x,y=remaining.pop();stack=[(x,y)];xx=[x];yy=[y]
        while stack:
            x,y=stack.pop()
            for p in [(x-1,y),(x+1,y),(x,y-1),(x,y+1)]:
                if p in remaining:remaining.remove(p);stack.append(p);xx.append(p[0]);yy.append(p[1])
        w=max(xx)-min(xx)+1;h=max(yy)-min(yy)+1
        if len(xx)<8 or h<minimum_height or w>6*h:continue
        m=result['input']['input_to_page'];corners=[]
        for x,y in [(x+q[0],y+q[1]) for x in (min(xx),max(xx)+1) for y in (min(yy),max(yy)+1)]:
            corners.append([m[0][0]*x+m[0][1]*y+m[0][2],m[1][0]*x+m[1][1]*y+m[1][2]])
        components.append([min(p[0] for p in corners),min(p[1] for p in corners),max(p[0] for p in corners),max(p[1] for p in corners)])
    return components


def field_missing_issues(view,page):
    result=page.get('original_page_result',page.get('page_result',{}));elements=view['readable_elements'];issues=[]
    for e in elements:
        if e.get('kind')!='field' or str(e.get('value') or '').strip():continue
        b=e['bbox_pdf'];height=b[3]-b[1]
        source=next((s for s in result.get('spans',[]) if s.get('span_id') in e.get('source_span_ids',[])),None)
        observed=field_value_geometry(result,source) if source else None
        if not observed:continue
        value_box=observed['value_bbox_pdf']
        ink=visible_ink(result,value_box)
        if not ink:continue
        # 上一字段较矮的续行不能误算为本字段姓名；要求具有本行实际字高。
        ink=[g for g in ink if g[3]-g[1]>=height*.25 and (g[1]+g[3])/2>b[1]+height*.4]
        if not ink:continue
        box=bounds(ink)
        issues.append(dict(code='ocr_coverage',stage='field_value_coverage',reason='字段标签已识别，但值为空；对应值区仍有未读出的可见字形',
            bbox_pdf=box,text=e['text'],region_id=e['region_id'],source_span_id=(e.get('source_span_ids') or [None])[0],
            field_label=e['label'],coverage_evidence=dict(observed,value_bbox_pdf=value_box,glyph_boxes_pdf=ink)))
    return issues


def layout_coverage_assessments(view,page,issues):
    """归入相邻布局不代表漏字；整块crop_region也不代表逐字覆盖。"""
    result=page.get('original_page_result',page.get('page_result',{}));assessments=[]
    for issue in issues:
        if issue.get('stage')!='layout_coverage':assessments.append((issue,None));continue
        b=issue['bbox_pdf'];ink=visible_ink(result,b)
        if not ink:assessments.append((issue,None));continue
        actual=[]
        for l in view['lines']:
            evidence=l.get('recognition_evidence') or {}
            # 检测框和有独立缺口证据的补全支持字形覆盖；其他输入矩形不支持。
            raw=next((s for s in page.get('page_result',{}).get('spans',[]) if s.get('span_id')==l.get('source_span_id')),None)
            if raw and raw.get('coordinate_source')=='engine_polygon':actual.append(l['bbox'])
            elif l.get('completion_evidence'):actual.append(l['completion_evidence']['visible_gap'])
            else:
                for entry in page.get('regional_recognition',[]):
                    if any(s.get('span_id')==l.get('source_span_id') and s.get('coordinate_source')=='engine_polygon' for s in entry.get('result',{}).get('spans',[])):actual.append(l['bbox'])
        uncovered=[g for g in ink if not any(coverage(g,a)>.65 for a in actual)]
        if not uncovered:
            record=dict(code='diagnostic_reconciliation',reason='visible_glyphs_covered_across_layout_groups',
                disposition='historical_observation',original_diagnostic=deepcopy(issue),evidence=dict(glyph_boxes_pdf=ink,covering_boxes_pdf=actual))
            assessments.append((issue,record))
        else:assessments.append((issue,None))
    return assessments


def reconcile_layout_coverage(view,page,issues,observations):
    rows=layout_coverage_assessments(view,page,issues)
    observations.extend(record for issue,record in rows if record)
    return [issue for issue,record in rows if not record]


def regional_gap_issues(view,page):
    """拒绝候选后仍独立检查已观察到的漏检区域，不将拒绝等同于问题解决。"""
    result=page.get('original_page_result',page.get('page_result',{}));found=[]
    spans={s['span_id']:s for s in page.get('page_result',{}).get('spans',[])}
    for entry in page.get('regional_recognition',[]):
        for s in entry.get('result',{}).get('spans',[]):spans[s['span_id']]=s
    actual=[l['bbox'] for l in view['lines'] if spans.get(l.get('source_span_id'),{}).get('coordinate_source')=='engine_polygon']
    for line in view['lines']:
        review=line.get('field_value_verification') or {}
        if not (review.get('accepted') and review.get('label_verified') and review.get('layer_evidence')):continue
        if not review.get('decision',{}).get('whole_sequence_review',{}).get('complete'):continue
        for name in ('span','label_span'):
            verified=review.get(name) or {}
            if verified.get('coordinate_source')=='connected_glyphs' and verified.get('polygon_page'):
                actual.append(bounds([[x,y,x,y] for x,y in verified['polygon_page']]))
    actual += [l['completion_evidence']['visible_gap'] for l in view['lines'] if l.get('completion_evidence')]
    for entry in page.get('regional_recognition',[]):
        if entry.get('trigger') not in ('layout_single_line_partial_coverage','layout_text_without_ocr'):continue
        rb=entry['region']['bbox_pdf'];ink=visible_ink(result,rb)
        if not ink:continue
        missing=[g for g in ink if not any(coverage(g,b)>.65 for b in actual)]
        if not missing:continue
        b=bounds(missing)
        if b[3]-b[1]<(rb[3]-rb[1])*.35:continue
        found.append(dict(code='ocr_coverage',stage='visible_region_gap',reason='局部候选的处理不代表覆盖完整；版面区域仍有未被有效字形覆盖的墨迹',
            bbox_pdf=b,region_id=entry['region']['region_id'],coverage_evidence=dict(method='uncovered_neutral_glyphs',glyph_boxes_pdf=missing)))
    return found
