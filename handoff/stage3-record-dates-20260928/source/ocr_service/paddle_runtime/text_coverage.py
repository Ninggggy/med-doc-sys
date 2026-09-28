"""正文块内完整连通字形行与检测框比较；不把非空块视为完整，不切投影细条。"""
from assembly import coverage, bounds
from regional_adoption import span_box


def missing_text_rows(page):
    import cv2
    import numpy as np
    r=page['page_result'];source=r['input'];m=source['input_to_page']
    if m[0][1] or m[1][0]:return []
    image=cv2.imread(source['image'])
    if image is None:return []
    sx,sy=m[0][0],m[1][1];result=[]
    for region in page.get('layout_result',{}).get('regions',[]):
        if region.get('label')!='text':continue
        b=region['bbox_pdf'];known=[span_box(s) for s in r.get('spans',[]) if span_box(s) and coverage(span_box(s),b)>.5]
        if len(known)<2:continue
        heights=[q[3]-q[1] for q in known];height=float(np.median(heights))/sy
        if (b[3]-b[1])/sy<height*1.5:continue
        x0=max(0,int((b[0]-m[0][2])/sx));y0=max(0,int((b[1]-m[1][2])/sy))
        x1=min(image.shape[1],int((b[2]-m[0][2])/sx)+1);y1=min(image.shape[0],int((b[3]-m[1][2])/sy)+1)
        crop=image[y0:y1,x0:x1];gray=cv2.cvtColor(crop,cv2.COLOR_BGR2GRAY)
        # 排除红色章色；保留黑字及灰色字形。底纹不能仅凭非白像素触发。
        red=(crop[:,:,2].astype(float)-crop[:,:,1]>40)&(crop[:,:,2].astype(float)-crop[:,:,0]>40)&(crop[:,:,2]>100)
        mask=((gray<150)&~red).astype('uint8')*255
        _,_,stats,centers=cv2.connectedComponentsWithStats(mask,8)
        glyphs=[]
        for (x,y,w,h,ink),(cx,cy) in zip(stats[1:],centers[1:]):
            if .35*height<=h<=1.7*height and 2<=w<=1.7*height and ink>=8 and .04<=ink/(w*h)<=.85:
                glyphs.append([int(x),int(y),int(x+w),int(y+h)])
        rows=[]
        for g in sorted(glyphs,key=lambda g:(g[1]+g[3])/2):
            cy=(g[1]+g[3])/2
            choices=[row for row in rows if abs(cy-np.median([(q[1]+q[3])/2 for q in row]))<height*.3]
            if choices:choices[0].append(g)
            else:rows.append([g])
        for row in rows:
            if len(row)<6:continue
            q=bounds(row)
            if q[2]-q[0]<height*4:continue
            rb=[(q[0]+x0)*sx+m[0][2],(q[1]+y0)*sy+m[1][2],(q[2]+x0)*sx+m[0][2],(q[3]+y0)*sy+m[1][2]]
            matched=[k for k in known if abs(sum(rb[1::2])/2-sum(k[1::2])/2)<.4*min(rb[3]-rb[1],k[3]-k[1])]
            uncovered=[]
            trigger='internal_visible_text_without_detection'
            if matched:
                glyph_pdf=[[(g[0]+x0)*sx+m[0][2],(g[1]+y0)*sy+m[1][2],(g[2]+x0)*sx+m[0][2],(g[3]+y0)*sy+m[1][2]] for g in row]
                uncovered=[g for g in glyph_pdf if g[3]-g[1]>.5*height*sy and all(coverage(g,k)<.5 for k in matched)]
                if not uncovered:continue
                trigger='internal_partial_glyph_coverage'
            elif any((min(rb[3],k[3])-max(rb[1],k[1]))/max(1,rb[3]-rb[1])>.65 for k in known):continue
            margin=max(1,(rb[3]-rb[1])*.18)
            full=[rb[0]-margin,rb[1]-margin,rb[2]+margin,rb[3]+margin]
            result.append(dict(region={**region,'region_id':region['region_id']+f':missing:{len(result)}','bbox_pdf':full},
                trigger=trigger,visible_bbox_pdf=rb,uncovered_bbox_pdf=bounds(uncovered) if uncovered else None,
                coverage_evidence=dict(method='connected_glyph_rows',glyph_count=len(row),glyph_boxes_px=row,
                    expected_height_px=height,source_region_id=region['region_id'],pixel_origin=[x0,y0],matched_line_boxes_pdf=matched)))
    return result


def full_glyph_line_input(entry,source,destination):
    """完整连通字形给出斜行包络；识别器直接读行，绕过已漏检的检测器。"""
    import cv2
    import numpy as np
    e=entry['coverage_evidence'];glyphs=np.array(e['glyph_boxes_px'],float)
    ox,oy=e['pixel_origin'];centers=(glyphs[:,:2]+glyphs[:,2:])/2
    slope=float(np.polyfit(centers[:,0],centers[:,1],1)[0])
    # 包络包含全部候选字形的四角，不使用投影峰值截窄字高。
    points=np.array([[x,y] for x0,y0,x1,y1 in glyphs for x,y in [(x0,y0),(x0,y1),(x1,y0),(x1,y1)]])
    intercepts=points[:,1]-slope*points[:,0]
    x0=float(glyphs[:,0].min());x1=float(glyphs[:,2].max())
    m=source['input_to_page']
    for b in e.get('matched_line_boxes_pdf',[]):
        x0=min(x0,(b[0]-m[0][2])/m[0][0]-ox);x1=max(x1,(b[2]-m[0][2])/m[0][0]-ox)
    top=float(intercepts.min());bottom=float(intercepts.max())
    source_image=cv2.imread(source['image']);pad=4
    transform=np.array([[1,0,-(ox+x0)+pad],[-slope,1,slope*ox-oy-top+pad]],float)
    width=int(np.ceil(x1-x0))+2*pad;height=int(np.ceil(bottom-top))+2*pad
    crop=cv2.warpAffine(source_image,transform,(width,height),borderValue=(255,255,255))
    # 输入边界来自完整字形包络；增加白边不引入上下行字符。
    crop[:pad,:]=255;crop[-pad:,:]=255;crop[:,:pad]=255;crop[:,-pad:]=255
    cv2.imwrite(str(destination),crop)
    inverse=np.vstack([cv2.invertAffineTransform(transform),[0,0,1]])
    matrix=(np.array(source['input_to_page'])@inverse).tolist()
    return matrix
