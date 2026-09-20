"""扫描页的可见几何及旧文字证据；不含证照名称或固定版面规则。"""
from copy import deepcopy
import re
import fitz


def complete_visible_cells(cells, axes, grid, pixel_scale):
    """在观测轴内补验缺格；每条边须连续，最多容忍已有尺度的短缺口。"""
    import numpy as np
    radius = max(1, round(3 * pixel_scale))
    gap_limit = max(1, round(6 * pixel_scale))

    def edge(x0, y0, x1, y1):
        horizontal = y0 == y1
        a, b = sorted((round(x0 if horizontal else y0), round(x1 if horizontal else y1)))
        fixed = round(y0 if horizontal else x0)
        data = grid if horizontal else grid.T
        strip = data[max(0, fixed-radius):fixed+radius+1, max(0,a):b+1]
        if not strip.size or strip.shape[1] < b-a:
            return False
        support = (strip > 0).any(axis=0)
        if support.mean() < .95:
            return False
        missing = np.flatnonzero(~support)
        return not len(missing) or max(len(g) for g in np.split(missing, np.where(np.diff(missing)>1)[0]+1)) <= gap_limit

    def bounded(b):
        x0,y0,x1,y1 = b
        return (x1>x0 and y1>y0 and edge(x0,y0,x1,y0) and edge(x0,y1,x1,y1)
                and edge(x0,y0,x0,y1) and edge(x1,y0,x1,y1))

    result = [list(b) for b in cells]
    if not result or not all(bounded(b) for b in result):
        return None
    for y0,y1 in zip(axes[1],axes[1][1:]):
        for x0,x1 in zip(axes[0],axes[0][1:]):
            owners = [b for b in result if b[0]<=x0 and b[2]>=x1 and b[1]<=y0 and b[3]>=y1]
            if len(owners)>1:
                return None
            if not owners:
                box = [x0,y0,x1,y1]
                if not bounded(box):
                    return None
                result.append(box)
    return result


def regular_visible_grid(grid, bounds, pixel_scale):
    """完整贯穿线先确定规则网格；任何缺边都回退到原合并格拓扑检测。"""
    import numpy as np
    x,y,w,h = bounds
    crop = grid[y:y+h,x:x+w]>0
    def centers(values):
        indices=np.flatnonzero(values)
        if not len(indices):return []
        return [float(g.mean()) for g in np.split(indices,np.where(np.diff(indices)>1)[0]+1)]
    ys=centers(crop.mean(axis=1)>=.8)
    if len(ys)<2:return None
    lo,hi=round(ys[0]),round(ys[-1])+1
    xs=centers(crop[lo:hi].mean(axis=0)>=.8)
    if len(xs)<2 or max(len(xs),len(ys))<3:return None
    axes=[[x+a for a in xs],[y+a for a in ys]]
    # 规则投影不能吞掉合并表中的真实短分隔线。短线两端若落在已观测
    # 横边上，且不伸到表外，必须交回原轮廓路径恢复合并关系。
    import cv2
    vertical=cv2.morphologyEx(grid[y:y+h,x:x+w],cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT,(1,max(3,round(20*pixel_scale)))))
    count,_,stats,_=cv2.connectedComponentsWithStats(vertical)
    tolerance=6*pixel_scale
    for vx,vy,vw,vh,area in stats[1:]:
        center=x+vx+(vw-1)/2
        if vw>tolerance or any(abs(center-a)<=tolerance for a in axes[0]):continue
        start,end=y+vy,y+vy+vh-1
        if (start>=axes[1][0]-tolerance and end<=axes[1][-1]+tolerance
                and any(abs(start-a)<=tolerance for a in axes[1])
                and any(abs(end-a)<=tolerance for a in axes[1])):
            return None
    horizontal=cv2.morphologyEx(grid[y:y+h,x:x+w],cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT,(max(3,round(20*pixel_scale)),1)))
    count,_,stats,_=cv2.connectedComponentsWithStats(horizontal)
    for hx,hy,hw,hh,area in stats[1:]:
        center=y+hy+(hh-1)/2
        if hh>tolerance or any(abs(center-a)<=tolerance for a in axes[1]):continue
        start,end=x+hx,x+hx+hw-1
        if (start>=axes[0][0]-tolerance and end<=axes[0][-1]+tolerance
                and any(abs(start-a)<=tolerance for a in axes[0])
                and any(abs(end-a)<=tolerance for a in axes[0])):
            return None
    if any(b-a<12*pixel_scale for axis in axes for a,b in zip(axis,axis[1:])):
        return None
    cells=[[x0,y0,x1,y1] for y0,y1 in zip(axes[1],axes[1][1:])
           for x0,x1 in zip(axes[0],axes[0][1:])]
    return complete_visible_cells(cells,axes,grid,pixel_scale)


def partition_text(page, words, lines):
    """逐词用文字绘制轨迹判断来源，混合页保留可见文字。"""
    traces = [t for t in page.get_texttrace() if t.get('chars')]
    hidden, visible, unknown = [], [], []
    for word in words:
        box = fitz.Rect(word['bbox'])
        matches = [t for t in traces if (box & fitz.Rect(t['bbox'])).get_area() > .5*box.get_area()]
        if matches and all(t.get('type') == 3 or t.get('opacity',1) == 0 for t in matches):
            hidden.append(deepcopy(word))
        elif matches and any(t.get('type') != 3 and t.get('opacity',1) != 0 for t in matches):
            visible.append(word)
        else:
            unknown.append(deepcopy(word))
    if not hidden and not unknown:
        return words, lines, []
    retained_lines = []
    for line in lines:
        bounds = fitz.Rect(line['bbox']) + (-.5,-.5,.5,.5)
        members = [w for w in visible if bounds.contains(fitz.Rect(w['bbox']))]
        if members:
            members.sort(key=lambda w:w['bbox'][0])
            retained_lines.append({**line,'text':' '.join(w['text'] for w in members)})
    for word in hidden:
        word['text_layer'] = 'invisible'
    for word in unknown:
        word['text_layer'] = 'unverified'
    return visible, retained_lines, hidden + unknown


def ordered_word_lines(words, lines=()):
    """先保留可靠引擎词序，其余按原词框成行；不扩框链式吸收相邻行。"""
    remaining = list(words)
    groups = []
    ambiguous = False
    for line in lines:
        ordered = line.get('_ordered_words', [])
        key=lambda w:(w['text'],tuple(w['bbox']))
        if not ordered or len({key(w) for w in ordered})!=len(ordered):
            continue
        selected=[]
        for item in ordered:
            matches=[w for w in remaining if key(w)==key(item)]
            if len(matches)==1:selected.append(matches[0])
        if not selected:continue
        groups.append(selected)
        remaining = [w for w in remaining if not any(w is item for item in selected)]
    def same_line(a,b):
        a,b = fitz.Rect(a['bbox']),fitz.Rect(b['bbox'])
        return (min(a.y1,b.y1)-max(a.y0,b.y0) >= .5*min(a.height,b.height)
                and abs(a.y1-b.y1) <= .6*max(a.height,b.height))
    fallback = []
    for word in sorted(remaining,key=lambda w:(w['bbox'][1],w['bbox'][0])):
        matches = [g for g in fallback if all(same_line(word,other) for other in g)]
        if len(matches)==1:
            matches[0].append(word)
        else:
            ambiguous |= len(matches)>1
            fallback.append([word])
    groups.extend(sorted(g,key=lambda w:w['bbox'][0]) for g in fallback)
    groups.sort(key=lambda g:(min(w['bbox'][1] for w in g),min(w['bbox'][0] for w in g)))
    return groups, ambiguous


def ordered_text(words, lines=()):
    groups, ambiguous = ordered_word_lines(words,lines)
    return '\n'.join(' '.join(w['text'] for w in group) for group in groups), ambiguous


def word_cell(word, cells):
    box = fitz.Rect(word['bbox'])
    owners = [i for i,c in enumerate(cells) if (fitz.Rect(c)+(-.5,-.5,.5,.5)).contains(box)]
    touches = [i for i,c in enumerate(cells) if (fitz.Rect(c)&box).get_area()>0]
    return (owners[0] if len(owners)==1 else ('ambiguous' if touches else 'outside')), touches


def pixel_crop(page, crop, dpi):
    """以MuPDF的像素取整规则统一裁剪；不按邻近或重叠合并不同输入。"""
    scale=dpi/72
    bounds=fitz.Rect(crop)
    if isinstance(page,fitz.Page):
        bounds &= page.rect
    window=(bounds*fitz.Matrix(scale,scale)).irect
    return fitz.Rect(window)/fitz.Matrix(scale,scale),tuple(window)


def image_line_support(page, crop, dpi, deadline):
    """独立原图副本中的字形行；仅提供位置证据，不修改OCR输入。"""
    import time
    if not isinstance(page,fitz.Page) or time.monotonic()>=deadline:
        return []
    import cv2
    import numpy as np
    scale=dpi/72
    if crop.get_area()*scale*scale>16000000:
        return []
    pix=page.get_pixmap(matrix=fitz.Matrix(scale,scale),clip=crop,alpha=False)
    gray=cv2.cvtColor(np.frombuffer(pix.samples,np.uint8).reshape(pix.height,pix.width,pix.n),cv2.COLOR_RGB2GRAY)
    ink=cv2.threshold(gray,0,255,cv2.THRESH_BINARY_INV+cv2.THRESH_OTSU)[1]
    # 去除仅供检测的贯穿边线；原灰度图和识别输入不变。
    for size in ((max(15,pix.width//2),1),(1,max(15,pix.height//2))):
        ink[cv2.morphologyEx(ink,cv2.MORPH_OPEN,cv2.getStructuringElement(cv2.MORPH_RECT,size))>0]=0
    if time.monotonic()>=deadline:
        return []
    _,_,stats,_=cv2.connectedComponentsWithStats(ink)
    pieces=[]
    for x,y,w,h,area in stats[1:]:
        if h>=max(2,scale) and w>=1 and area>=max(3,scale) and .08<=w/h<=4 and .08<=area/(w*h)<=.95:
            pieces.append({'text':'','bbox':[(pix.x+x)/scale,(pix.y+y)/scale,(pix.x+x+w)/scale,(pix.y+y+h)/scale]})
    # 连通域是笔画而非词；中文字的分离部件不能用词基线规则分行。
    # 使用实际字形部件的纵向投影带，行内再按横向空白分开版面块。
    bands=[]
    for piece in sorted(pieces,key=lambda w:w['bbox'][1]):
        if not bands or piece['bbox'][1]>bands[-1][1]:
            bands.append([piece['bbox'][1],piece['bbox'][3],[piece]])
        else:
            bands[-1][1]=max(bands[-1][1],piece['bbox'][3])
            bands[-1][2].append(piece)
    groups=[sorted(band[2],key=lambda w:w['bbox'][0]) for band in bands]
    result=[]
    for group in groups:
        # 明显的栏间空白不得串成一条文字行。
        chunks=[]
        for w in group:
            if not chunks or w['bbox'][0]-chunks[-1][-1]['bbox'][2]>3*max(
                    w['bbox'][3]-w['bbox'][1],chunks[-1][-1]['bbox'][3]-chunks[-1][-1]['bbox'][1]):
                chunks.append([])
            chunks[-1].append(w)
        for chunk in chunks:
            if len(chunk)<3:
                continue
            box=fitz.Rect(chunk[0]['bbox'])
            for w in chunk[1:]:box |= fitz.Rect(w['bbox'])
            if box.width<2*box.height:
                continue
            result.append({'bbox':list(box),'component_bboxes':[w['bbox'] for w in chunk]})
    return result


def image_supports_word(word, support, *, engine_line_verified=False):
    if not re.search(r'[\w\u4e00-\u9fff]',word['text']):
        return False
    box=fitz.Rect(word['bbox'])
    matches=[]
    for line in support:
        bounds=fitz.Rect(line['bbox'])
        positioned=(bounds+(-bounds.height/2,-bounds.height/2,bounds.height/2,bounds.height/2)).contains(box)
        # TSV可能把中文词框上、下沿扩到行外。已验证的引擎行内词，额外
        # 要求中心仍落在原图行内、横向仍在该行范围；保留原框，不猜分字框。
        if engine_line_verified and not re.search(r'\d',word['text']) and not word.get('numeric_verification'):
            positioned |= (bounds.y0 <= (box.y0+box.y1)/2 <= bounds.y1
                and box.x0 >= bounds.x0-bounds.height/2 and box.x1 <= bounds.x1+bounds.height/2)
        if (.5<=box.height/max(bounds.height,.001)<=2.5
                and positioned
                and sum((fitz.Rect(c)&box).get_area()>0 for c in line['component_bboxes'])>=1):
            matches.append(line)
    return len(matches)==1


def recovery_input_size(window, support, dpi):
    """只缩小字高一致且偏大的原图文字行；小字、混合字号及无证据保持原样。"""
    heights=[fitz.Rect(line['bbox']).height*dpi/72 for line in support
             if line.get('component_bboxes')]
    if not heights or min(heights)<=28 or max(heights)>1.5*min(heights):
        return None
    # 以约28像素字高选择有界整数采样，最多四等分。补白对齐原页采样网格，
    # 不因裁剪起点或目标尺寸取整改变采样相位。
    factor=max(2,min(4,round(max(heights)/28)))
    return ((window[2]+factor-1)//factor-window[0]//factor,
            (window[3]+factor-1)//factor-window[1]//factor)


def recovery_segmentation(words, lines, support):
    groups,ambiguous=ordered_word_lines(words,lines)
    count=len(groups) if words and not ambiguous else len(support)
    reason='text_lines' if words and not ambiguous else ('image_lines' if support else 'line_count_uncertain')
    if len(support)>count:
        count=len(support)
        reason='image_lines'
    return (7 if count==1 else 6),reason


def embedded_comparisons(old_words, current_words, *, cells=(), current_lines=(), old_lines=()):
    """按相交的词框连通分组，兼容两次识别的分词差异。"""
    def compact(value):
        return ''.join(value.split())
    def corresponding(a,b):
        a,b=fitz.Rect(a),fitz.Rect(b)
        return (min(a.height,b.height)>0 and max(a.height,b.height)<=2.5*min(a.height,b.height)
                and abs(a.y1-b.y1)<=.6*max(a.height,b.height)
                and min(a.y1,b.y1)-max(a.y0,b.y0)>=.5*min(a.height,b.height)
                and (a&b).get_area()>.2*min(a.get_area(),b.get_area()))
    old_owners=[word_cell(w,cells)[0] for w in old_words]
    new_owners=[word_cell(w,cells)[0] for w in current_words]
    pending = set(range(len(old_words)))
    output = []
    while pending:
        old_ids, new_ids = {min(pending)}, set()
        changed = True
        while changed:
            before = (len(old_ids),len(new_ids))
            for j,word in enumerate(current_words):
                if new_owners[j]=='ambiguous' or new_owners[j]!=old_owners[min(old_ids)]:
                    continue
                box = fitz.Rect(word['bbox'])
                if any(corresponding(box,old_words[i]['bbox']) for i in old_ids):
                    new_ids.add(j)
            for i in pending-old_ids:
                if old_owners[i]=='ambiguous' or old_owners[i]!=old_owners[min(old_ids)]:
                    continue
                box = fitz.Rect(old_words[i]['bbox'])
                if any(corresponding(box,current_words[j]['bbox']) for j in new_ids):
                    old_ids.add(i)
            changed = before != (len(old_ids),len(new_ids))
        pending -= old_ids
        old = [old_words[i] for i in sorted(old_ids)]
        new = [current_words[i] for i in sorted(new_ids)]
        bounds = fitz.Rect(old[0]['bbox'])
        for word in old[1:]+new:
            bounds |= fitz.Rect(word['bbox'])
        a,old_ambiguous = ordered_text(old,old_lines)
        b,new_ambiguous = ordered_text(new,current_lines)
        crossing=[w for j,w in enumerate(current_words) if new_owners[j]=='ambiguous'
                  and (fitz.Rect(w['bbox'])&bounds).get_area()>0]
        geometry_ambiguous=old_owners[min(old_ids)]=='ambiguous' or bool(crossing)
        output.append({'bbox_pdf':list(bounds),'embedded_words':deepcopy(old),
            'primary_words':deepcopy(new),'embedded_text':a,'primary_text':b,
            'status':'matched' if compact(a)==compact(b) and not (old_ambiguous or new_ambiguous or geometry_ambiguous) else ('conflict' if new else 'missing'),
            'trigger':'ocr_quality' if new else 'ocr_coverage',
            'geometry_ambiguous':geometry_ambiguous,'order_ambiguous':old_ambiguous or new_ambiguous,
            'comparison_cell':old_owners[min(old_ids)],'comparison_cells':deepcopy(list(cells)),
            'ambiguous_primary_words':deepcopy(crossing)})
    return output


def finalize_embedded_evidence(evidence, attempts, current_words=()):
    """只有原主结果与独立局部候选一致才淘汰旧错字，预算不足保持未解决。"""
    def compact(value):
        return ''.join(value.split())
    errors = []
    for evidence_index,entry in enumerate(evidence):
        if entry.get('geometry_ambiguous') or entry.get('order_ambiguous'):
            errors.append({'stage':'embedded_ocr','code':'table_structure_unresolved' if entry.get('geometry_ambiguous') else 'ocr_quality',
                'bbox_pdf':entry['bbox_pdf'],'reason':'候选跨格或阅读顺序无法唯一确定，保留原文字证据',
                'embedded_evidence_index':evidence_index})
            if entry.get('geometry_ambiguous'):
                errors.append({'stage':'embedded_ocr','code':entry['trigger'],
                    'bbox_pdf':entry['bbox_pdf'],'reason':'跨格候选不能作为旧层核验结果，双方证据保留',
                    'embedded_evidence_index':evidence_index})
            continue
        if entry['status']=='matched':
            continue
        box = fitz.Rect(entry['bbox_pdf'])
        relevant=[]
        for index,attempt in enumerate(attempts):
            area=fitz.Rect(attempt.get('bbox_pdf',[0,0,0,0]))
            if (area+(-.5,-.5,.5,.5)).contains(box):
                relevant.append((index,attempt))
        entry['recovery_attempt_indices']=[i for i,_ in relevant]
        entry['recovery_candidates']=[]
        for _,attempt in relevant:
            candidates=[w for w in attempt.get('candidates',[]) if w.get('bbox')
                        and (fitz.Rect(w['bbox']) & box).get_area() > .2*fitz.Rect(w['bbox']).get_area()]
            entry['recovery_candidates'].extend(deepcopy(candidates))
            if entry.get('comparison_cells'):
                candidates=[w for w in candidates if word_cell(w,entry['comparison_cells'])[0]==entry['comparison_cell']]
            value,ambiguous=ordered_text(candidates,attempt.get('candidate_lines',()))
            if ambiguous:
                continue
            primary=entry['primary_words']
            safe=lambda ws: bool(ws) and all(w.get('confidence',0)>=80
                and (not re.search(r'\d',w['text']) or w.get('numeric_verification',{}).get('status')=='verified')
                and w.get('numeric_verification',{}).get('status','verified')=='verified' for w in ws)
            if (entry['status']=='missing' and safe(candidates)
                    and compact(value)==compact(entry['embedded_text'])
                    and all(any(w['text']==candidate['text'] and
                        (fitz.Rect(w['bbox']) & fitz.Rect(candidate['bbox'])).get_area() >
                        .5*fitz.Rect(candidate['bbox']).get_area() for w in current_words) for candidate in candidates)
                    and not attempt.get('has_conflicting_candidates')):
                entry.update(status='revalidated',reason='原图局部识别恢复旧层文字，已通过补识别采用检查')
                break
            if (entry['status']=='conflict' and safe(primary) and safe(candidates)
                    and compact(value)==compact(entry['primary_text'])
                    and not attempt.get('has_conflicting_candidates')):
                entry.update(status='revalidated',reason='主识别与原图局部复核一致，旧文本保留为证据')
                break
        if entry['status'] not in ('matched','revalidated'):
            errors.append({'stage':'embedded_ocr','code':entry['trigger'], 'bbox_pdf':list(box),
                'reason':'旧OCR层与当前识别存在差异，尚无充分图像证据完成核验',
                'embedded_evidence_index':evidence_index})
    return errors
