"""仅补识别版面空缺或跨区域的 OCR 框；复用 v6 medium，不全页重跑。"""
from copy import deepcopy
from pathlib import Path
import math
from assembly import coverage, bounds


def recovery_regions(page):
    regions=[r for r in page.get('layout_result',{}).get('regions',[]) if r['label'] in ('text','doc_title','paragraph_title')]
    spans=page['page_result'].get('spans',[])
    chosen={}
    for r in regions:
        matched=[s for s in spans if s.get('polygon_page') and coverage(bounds([[p[0],p[1],p[0],p[1]] for p in s['polygon_page']]),r['bbox_pdf'])>=.5]
        if not matched:chosen[r['region_id']]=dict(region=r,trigger='layout_text_without_ocr')
    for span in spans:
        poly=span.get('polygon_page')
        if not poly:continue
        b=[min(p[0] for p in poly),min(p[1] for p in poly),max(p[0] for p in poly),max(p[1] for p in poly)]
        hits=[r for r in regions if coverage(b,r['bbox_pdf'])>.12]
        if len(hits)>1 and max(coverage(b,r['bbox_pdf']) for r in hits)<.8:
            for r in hits:chosen[r['region_id']]=dict(region=r,trigger='ocr_span_crosses_layout_regions',span_id=span['span_id'])
    # 版面是完整标题/单行，而 OCR 只覆盖其一部分：只产生同模型局部候选。
    for r in regions:
        rb=r['bbox_pdf']
        matched=[s for s in spans if s.get('polygon_page') and coverage(bounds([[p[0],p[1],p[0],p[1]] for p in s['polygon_page']]),rb)>=.5]
        if not matched:continue
        boxes=[bounds([[p[0],p[1],p[0],p[1]] for p in s['polygon_page']]) for s in matched]
        union=bounds(boxes);width=(union[2]-union[0])/max(1,rb[2]-rb[0])
        single_row=union[3]-union[1] <= 1.5*max(b[3]-b[1] for b in boxes)
        if single_row and width < (.85 if r['label']=='doc_title' else .55):
            chosen.setdefault(r['region_id'],dict(region=r,trigger='layout_single_line_partial_coverage'))
    return list(chosen.values())[:4]


def recover_regions(page, engine, run, timeout=60):
    import time
    from PIL import Image
    from common import dump
    source=page['page_result']['input'];matrix=source['input_to_page']
    # 当前页输入来自无旋转的渲染映射；不猜测复杂投影的逆变换。
    if matrix[0][1] or matrix[1][0]:return []
    from text_coverage import missing_text_rows
    entries=recovery_regions(page)+missing_text_rows(page)
    page['internal_coverage_evidence']=[e for e in entries if e['trigger'].startswith('internal_')]
    result=[]
    deadline=time.monotonic()+timeout
    with Image.open(source['image']) as image:
        for index,entry in enumerate(entries):
            remaining=deadline-time.monotonic()
            if remaining<=0:break
            from regional_adoption import crop_scope, span_box
            scope=crop_scope(entry,page['page_result'].get('spans',[]))
            if entry['trigger'].startswith('internal_'):
                rb=entry['region']['bbox_pdf'];h=rb[3]-rb[1]
                nearby=[span_box(s) for s in page['page_result'].get('spans',[]) if span_box(s) and
                        max(span_box(s)[1]-rb[3],rb[1]-span_box(s)[3])<h*.6 and
                        min(span_box(s)[2],rb[2])>max(span_box(s)[0],rb[0])]
                full=bounds([rb]+nearby)
                scope.update(bbox_pdf=[full[0]-2,full[1]-3,full[2]+2,full[3]+3],
                             replacement_span_ids=[],fully_contains_source_lines=True,mode='supplement_only_with_full_line_context')
            if not scope['fully_contains_source_lines']:

                members=[s for s in page['page_result'].get('spans',[]) if s['span_id'] in scope['replacement_span_ids']]
                if not members:
                    rb=entry['region']['bbox_pdf'];safe=scope['bbox_pdf'][:]
                    for neighbor in page['page_result'].get('spans',[]):
                        if neighbor['span_id'] not in scope['blocked_by_span_ids']:continue
                        nb=span_box(neighbor)
                        if nb[1]>=rb[3]:safe[3]=min(safe[3],nb[1]-max(abs(matrix[1][1]),.1))
                        elif nb[3]<=rb[1]:safe[1]=max(safe[1],nb[3]+max(abs(matrix[1][1]),.1))
                        elif nb[0]>=rb[2]:safe[2]=min(safe[2],nb[0]-max(abs(matrix[0][0]),.1))
                        elif nb[2]<=rb[0]:safe[0]=max(safe[0],nb[2]+max(abs(matrix[0][0]),.1))
                    if safe[2]>safe[0] and safe[3]>safe[1] and all(coverage(span_box(s),safe)<.01 for s in page['page_result'].get('spans',[]) if s['span_id'] in scope['blocked_by_span_ids']):
                        scope.update(bbox_pdf=safe,fully_contains_source_lines=True,mode='missing_region_padding_bounded_by_neighbors')
                top=entry['region']['bbox_pdf'][:]
                top[3]=min([span_box(s)[1] for s in members] or [top[1]])-1
                if not scope['fully_contains_source_lines'] and top[3]-top[1] < 12:
                    result.append({**entry,'crop_scope':scope,'result':{'status':'skipped','spans':[]}})
                    continue
                # 只补缺失的上行，已有续行完全不进入替换范围。
                if not scope['fully_contains_source_lines']:
                    scope.update(bbox_pdf=top,replacement_span_ids=[],mode='missing_prefix_only')
            b=scope['bbox_pdf'];sx=matrix[0][0];sy=matrix[1][1]
            pixel=[max(0,math.floor((b[0]-matrix[0][2])/sx)),max(0,math.floor((b[1]-matrix[1][2])/sy)),
                   min(image.width,math.ceil((b[2]-matrix[0][2])/sx)),min(image.height,math.ceil((b[3]-matrix[1][2])/sy))]
            sid=page['page_result']['sample_id']+f'-layout-region-{index}'
            path=Path(run)/'images'/f'{sid}.png';path.parent.mkdir(parents=True,exist_ok=True)
            image.crop(pixel).save(path)
            transform=deepcopy(matrix);transform[0][2]+=pixel[0]*sx;transform[1][2]+=pixel[1]*sy
            granularity='block'
            # 已由版面和原检测框证明为单行的缺口，用同一 v6 行识别器读取完整行；
            # 避免再次检测把章覆盖的日期/字段拆成无序单字。仍经统一采用资格检查。
            if entry['trigger']=='layout_single_line_partial_coverage':
                rb=entry['region']['bbox_pdf']
                source_heights=[span_box(s)[3]-span_box(s)[1] for s in page['page_result'].get('spans',[])
                                if span_box(s) and coverage(span_box(s),rb)>=.5]
                # 仅检测到一条续行不能证明整个版面框是单行。
                if source_heights and rb[3]-rb[1]<=1.5*max(source_heights):granularity='line-rec'
            if entry['trigger'].startswith('internal_'):
                from text_coverage import full_glyph_line_input
                transform=full_glyph_line_input(entry,source,path);granularity='line-rec'
            recognized=engine.predict(dict(sample_id=sid,image=str(path),granularity=granularity,stage='layout_region_recovery',
                input_to_page=transform,parent_region_id=entry['region']['region_id']),remaining)
            result.append({**entry,'crop_scope':scope,'result':recognized})
    return result
