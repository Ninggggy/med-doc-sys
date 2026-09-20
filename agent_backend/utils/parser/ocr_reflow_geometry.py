"""重排裁剪的来源定位；不推断字符归属，也不生成覆盖掩膜。"""
import math


def recognize_cell_candidates(page, rect, client, lang, dpi, deadline):
    """一次受控识别，仅返回待核对候选；不生成可自动采纳的words。"""
    import io
    import re
    import time
    from copy import deepcopy
    import fitz
    from PIL import Image
    if (getattr(client, 'supports_execution_budget', False) is not True
            or getattr(client, 'supports_preprocessing', False) is not True):
        raise ValueError('reflow_controlled_service_required')
    if time.monotonic() >= deadline:
        raise TimeoutError('reflow_budget_exhausted')
    scale = dpi/72
    pix = page.get_pixmap(matrix=fitz.Matrix(scale,scale),clip=rect,alpha=False)
    image = Image.frombytes('RGB',(pix.width,pix.height),pix.samples).convert('L')
    result = reflow_lines(image)
    if result is None:
        return None
    image,mapping = result
    buffer = io.BytesIO()
    image.save(buffer,format='PNG')
    remaining = deadline-time.monotonic()
    if remaining <= 0:
        raise TimeoutError('reflow_budget_exhausted')
    data = client.image_to_data(buffer.getvalue(),lang,psm=7,
                               preprocessing='original_gray',execution_budget_seconds=remaining)
    if data.get('execution_budget_enforced') is not True or data.get('preprocessing') != 'original_gray':
        raise ValueError('reflow_service_mode_unconfirmed')
    candidates = []
    for i,value in enumerate(data.get('text',[])):
        text = str(value).strip()
        if not text:
            continue
        left,top,width,height = [float(data[key][i]) for key in ('left','top','width','height')]
        fragments = source_fragments([left,top,left+width,top+height],mapping)
        boxes = [[(pix.x+b[0])/scale,(pix.y+b[1])/scale,
                  (pix.x+b[2])/scale,(pix.y+b[3])/scale]
                 for fragment in fragments for b in [fragment['source_pixel_bbox']]]
        candidate = {'text':text,'source':'ocr_reflow_candidate','needs_review':True,
                     'confidence':float(data['conf'][i]),'source_bboxes_pdf':boxes}
        # 服务的数值核验仍执行，但重排坐标不得冒充原页坐标或自动放行。
        evidence = [deepcopy(v) for v in data.get('numeric_verification',[])
                    if isinstance(v,dict) and i in (v.get('word_indices') or [])]
        if re.search(r'\d',text) or evidence:
            candidate['numeric_verification'] = {
                'status':'numeric_uncertain','reason_code':'reflow_candidate_requires_review',
                'coordinate_space':'reflow_pixel','original_evidence':evidence}
        candidates.append(candidate)
    return candidates


def reflow_lines(image, *, gap=8, padding=20, max_pixels=16000000):
    """仅把明确空白行分隔的原灰度图块平移，不去线、不改字符像素。

    返回重排图和图块来源；空白/单行原图不需要重排。调用方仍须先
    确认输入是单一单元格而非多栏正文，并独立执行质量、冲突与预算检查。
    """
    import numpy as np
    from PIL import Image
    if image.mode != 'L':
        raise ValueError('reflow_requires_original_grayscale')
    if any(type(value) is not int or value < 0 for value in (gap,padding)):
        raise ValueError('invalid_reflow_spacing')
    if type(max_pixels) is not int or max_pixels <= 0:
        raise ValueError('invalid_reflow_pixel_limit')
    if image.width * image.height > max_pixels:
        raise ValueError('reflow_pixel_limit_exceeded')
    # 连浅灰抗锯齿像素也保留；无法找到纯白间隔时不强行切行。
    ink = np.asarray(image) < 255
    active = np.flatnonzero(ink.any(axis=1))
    if not len(active):
        return None
    runs = np.split(active, np.where(np.diff(active)>1)[0]+1)
    if len(runs) < 2:
        return None
    boxes = []
    for run in runs:
        top,bottom = int(run[0]),int(run[-1])+1
        xs = np.flatnonzero(ink[top:bottom].any(axis=0))
        boxes.append([int(xs[0]),top,int(xs[-1])+1,bottom])
    width = padding*2 + sum(b[2]-b[0] for b in boxes) + gap*(len(boxes)-1)
    height = padding*2 + max(b[3]-b[1] for b in boxes)
    if width*height > max_pixels:
        raise ValueError('reflow_pixel_limit_exceeded')
    result = Image.new('L',(width,height),255)
    mapping = []
    x = padding
    for box in boxes:
        piece = image.crop(tuple(box))
        y = height-padding-piece.height
        result.paste(piece,(x,y))
        mapping.append({'source_pixel_bbox':box,
                        'reflow_pixel_bbox':[x,y,x+piece.width,y+piece.height]})
        x += piece.width+gap
    return result,mapping


def _box(value):
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise ValueError('invalid_reflow_box')
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
           for v in value):
        raise ValueError('invalid_reflow_box')
    x0, y0, x1, y1 = value
    if x1 <= x0 or y1 <= y0:
        raise ValueError('invalid_reflow_box')
    return tuple(value)


def _intersection(a, b):
    result = (max(a[0],b[0]), max(a[1],b[1]), min(a[2],b[2]), min(a[3],b[3]))
    return result if result[2] > result[0] and result[3] > result[1] else None


def source_fragments(word_box, mapping):
    """返回词框与各未缩放图块交集的原图像素框，保留不连续来源。

    一个词可以对应多行；不能将结果取外包框用于覆盖检查，不能按框数
    拆分词中文字。没有交集返回空列表，由调用方保留待定位状态。
    此函数只证明几何来源，不证明OCR文字正确或该区域已完整识别。
    """
    word = _box(word_box)
    pieces = []
    for item in mapping:
        source = _box(item['source_pixel_bbox'])
        target = _box(item['reflow_pixel_bbox'])
        if any(not math.isclose(source[i+2]-source[i], target[i+2]-target[i],
                                rel_tol=0, abs_tol=1e-6) for i in (0,1)):
            raise ValueError('scaled_reflow_piece_not_supported')
        if any(_intersection(source, old_source) or _intersection(target, old_target)
               for old_source, old_target in pieces):
            raise ValueError('overlapping_reflow_pieces')
        pieces.append((source,target))
    fragments = []
    for index,(source,target) in enumerate(pieces):
        overlap = _intersection(word,target)
        if overlap is not None:
            dx,dy = source[0]-target[0],source[1]-target[1]
            fragments.append({'piece_index':index,
                              'source_pixel_bbox':[overlap[0]+dx,overlap[1]+dy,
                                                   overlap[2]+dx,overlap[3]+dy]})
    return fragments
