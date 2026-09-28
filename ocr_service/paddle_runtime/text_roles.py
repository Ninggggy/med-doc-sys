"""按原图墨色区分已有独立文字框；不改字、不使用词表或重复次数。"""
import copy
import cv2
import numpy as np
from adapters import assemble


def stamp_regions(page_result):
    image=cv2.imread(page_result['input']['image'])
    b,g,r=image.astype(float).transpose(2,0,1)
    mask=((r-g>35)&(r-b>35)).astype('uint8')*255
    regions=[]
    matrix=page_result['input']['input_to_page']
    for contour in cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)[0]:
        # 低清圆边的锯齿不应增加几何周长；凸包用于外轮廓，不修改识别输入。
        contour=cv2.convexHull(contour)
        area=cv2.contourArea(contour); perimeter=cv2.arcLength(contour,True)
        x,y,w,h=cv2.boundingRect(contour)
        if min(w,h)<64 or not .75<w/h<1.33 or 4*np.pi*area/max(perimeter**2,1)<.7:continue
        poly=np.c_[contour[:,0,:],np.ones(len(contour))]@np.array(matrix).T
        regions.append(dict(polygon_page=poly[:,:2].tolist(),source='closed_red_circular_outline',area_px=area))
    return regions


def separate_roles(result,regions):
    image=cv2.imread(result['input']['image'])
    if image is None: raise FileNotFoundError(result['input']['image'])
    b,g,r=image.astype(float).transpose(2,0,1)
    effective=copy.deepcopy(result); evidence=[]; body=[]
    for span in effective['spans']:
        mask=np.zeros(image.shape[:2],np.uint8)
        cv2.fillPoly(mask,[np.array(span['polygon_input_px'],np.int32)],1)
        ink=(np.minimum(b,g)<180)&(mask>0)
        red=(r-g>35)&(r-b>35)&ink
        neutral=(r<150)&(g<150)&(b<150)&ink&~red
        count=int(ink.sum());fraction=float(red.sum()/max(count,1));dark=float(neutral.sum()/max(count,1))
        # 混框有黑字则保留未知，绝不从字符串中扣除章字。
        page_poly=span.get('polygon_page',[])
        # 弧形字的矩形框角可在圆外；以框中心定位，墨色仍独立排除混入的黑字。
        enclosed=bool(page_poly) and any(cv2.pointPolygonTest(np.array(region['polygon_page'],np.float32),
            tuple(np.mean(page_poly,axis=0)),False)>=0 for region in regions)
        role='red_overlay' if enclosed and count>=20 and fraction>=.85 and dark<.05 else 'mixed_or_body'
        row=dict(span_id=span['span_id'],role=role,red_ink_fraction=fraction,neutral_dark_fraction=dark,
                 red_ink_pixels=int(red.sum()),neutral_dark_pixels=int(neutral.sum()),
                 polygon_input_px=span['polygon_input_px'],text_raw=span['text_raw'],inside_red_circular_outline=enclosed)
        evidence.append(row);span['visual_role']=role
        if role!='red_overlay':body.append(span)
    effective['text_assembled']=assemble(body)
    # 所有原始框原文留在spans，有效正文和叠加角色分开，消费者使用单独投影。
    effective['body_spans']=body
    effective['role_evidence']=evidence
    return effective


def apply_cell_roles(cell,regions):
    previous_roles=[copy.deepcopy(e) for e in cell.get('role_evidence',[]) if e.get('role')=='red_overlay']
    previous_sources={s['span_id']:s for source in (cell.get('original_result',{}),cell.get('before_role_result',{}))
                      for s in source.get('spans',[])}
    current=cell['result'];effective=separate_roles(current,regions)
    cell.setdefault('original_role_evidence',copy.deepcopy(effective['role_evidence']))
    cell['role_evidence']=effective['role_evidence']
    if effective['text_assembled']!=current['text_assembled']:
        cell['before_role_result']=current
        cell.setdefault('original_result',current)
        cell['result']=effective
        cell['role_revision']=dict(reason='independent_red_overlay_polygon',automatic=True,
            old=current['text_assembled'],new=effective['text_assembled'],human_confirmed=False)
    # 字符串已是正文也必须保存角色投影，否则下一次组装会把保留的章号重新拼入正文。
    cell['result']=effective
    # 采用黑色正文候选不删除独立章号对象；旧来源仍由原色图的角色证据约束。
    existing={s['span_id'] for s in cell['result']['spans']}
    retained=[e for e in previous_roles if e['span_id'] not in existing and e['span_id'] in previous_sources]
    if retained:
        body=cell['result'].get('body_spans',copy.deepcopy(cell['result']['spans']))
        cell['result']['body_spans']=body
        for role in retained:
            span=copy.deepcopy(previous_sources[role['span_id']]);span['visual_role']='red_overlay'
            span['source_input']=copy.deepcopy(cell.get('original_result',{}).get('input',{}))
            cell['result']['spans'].append(span)
        cell['role_evidence'].extend(retained)
        cell['result']['text_assembled']=assemble(body)
