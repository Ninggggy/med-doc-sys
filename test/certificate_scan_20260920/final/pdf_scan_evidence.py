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


def embedded_comparisons(old_words, current_words):
    """按相交的词框连通分组，兼容两次识别的分词差异。"""
    def compact(value):
        return ''.join(value.split())
    def text(words):
        return ' '.join(w['text'] for w in sorted(words,key=lambda w:(round(w['bbox'][1]/max(1,w['bbox'][3]-w['bbox'][1])),w['bbox'][0])))
    pending = set(range(len(old_words)))
    output = []
    while pending:
        old_ids, new_ids = {min(pending)}, set()
        changed = True
        while changed:
            before = (len(old_ids),len(new_ids))
            for j,word in enumerate(current_words):
                box = fitz.Rect(word['bbox'])
                if any((box & fitz.Rect(old_words[i]['bbox'])).get_area() > .2*min(box.get_area(),fitz.Rect(old_words[i]['bbox']).get_area()) for i in old_ids):
                    new_ids.add(j)
            for i in pending-old_ids:
                box = fitz.Rect(old_words[i]['bbox'])
                if any((box & fitz.Rect(current_words[j]['bbox'])).get_area() > .2*min(box.get_area(),fitz.Rect(current_words[j]['bbox']).get_area()) for j in new_ids):
                    old_ids.add(i)
            changed = before != (len(old_ids),len(new_ids))
        pending -= old_ids
        old = [old_words[i] for i in sorted(old_ids)]
        new = [current_words[i] for i in sorted(new_ids)]
        bounds = fitz.Rect(old[0]['bbox'])
        for word in old[1:]+new:
            bounds |= fitz.Rect(word['bbox'])
        a,b = text(old),text(new)
        output.append({'bbox_pdf':list(bounds),'embedded_words':deepcopy(old),
            'primary_words':deepcopy(new),'embedded_text':a,'primary_text':b,
            'status':'matched' if compact(a)==compact(b) else ('conflict' if new else 'missing'),
            'trigger':'ocr_quality' if new else 'ocr_coverage'})
    return output


def finalize_embedded_evidence(evidence, attempts, current_words=()):
    """只有原主结果与独立局部候选一致才淘汰旧错字，预算不足保持未解决。"""
    def compact(value):
        return ''.join(value.split())
    errors = []
    for evidence_index,entry in enumerate(evidence):
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
            value=' '.join(w['text'] for w in sorted(candidates,key=lambda w:(w['bbox'][1],w['bbox'][0])))
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
