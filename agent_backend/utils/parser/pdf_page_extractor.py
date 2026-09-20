"""公共页面提取；坐标为未旋转裁剪页左上角起算的 PDF point（1/72 英寸）。"""
from __future__ import annotations

import time
import re
import math
import unicodedata
from copy import deepcopy

import fitz
import pdfplumber


def text_quality(text):
    # 目录引导点不参与有效字符比例，原文仍完整保留。
    measured = re.sub(r'[.．·…]{3,}', '', text)
    chars = [c for c in measured if not c.isspace()]
    bad = sum(c == '\ufffd' or unicodedata.category(c) in {'Cc', 'Co', 'Cn', 'Cs'} for c in chars)
    effective = sum(c.isalnum() for c in chars)
    usable = bool(chars) and bad / len(chars) < .03 and effective / len(chars) >= .35
    return {'usable': usable, 'characters': len(chars), 'abnormal_characters': bad,
            'effective_characters': effective}


def native_lines(page):
    lines = []
    # 关闭MuPDF按字体宽度猜测插入的空格；PDF中真实空格仍然保留。
    for block in page.get_text('dict', flags=fitz.TEXTFLAGS_DICT | fitz.TEXT_INHIBIT_SPACES)['blocks']:
        for line in block.get('lines', []):
            spans = line.get('spans', [])
            text = ''.join(s['text'] for s in spans).strip()
            if text:
                lines.append({'text': text, 'bbox': list(line['bbox']), 'source': 'native'})
    return sorted(lines, key=lambda x: (x['bbox'][1], x['bbox'][0]))


def inside(box, region):
    return region[0] <= (box[0]+box[2])/2 <= region[2] and region[1] <= (box[1]+box[3])/2 <= region[3]


def page_geometry(page):
    """核对未旋转页的实际尺度；拒绝底层库对非法页面框的默认尺寸兜底。"""
    media, crop = page.mediabox, page.cropbox
    for name, box in (('MediaBox', media), ('CropBox', crop), ('page.rect', page.rect)):
        if not all(math.isfinite(v) for v in box) or box.width <= 0 or box.height <= 0:
            raise ValueError(f'{name} 非有限或无有效面积，无法可靠转换页面坐标')
    # CropBox 的纵坐标相对 MediaBox 顶边；只用两者实际相交的可见区域。
    visible_crop = crop & fitz.Rect(media.x0, 0, media.x1, media.height)
    if visible_crop.is_empty:
        raise ValueError('CropBox 与 MediaBox 无交集，无法可靠转换页面坐标')
    kind, value = page.parent.xref_get_key(page.xref, 'UserUnit')
    if kind == 'xref':
        value = page.parent.xref_object(int(value.split()[0]), compressed=True).strip()
    elif kind == 'null':
        value = '1'
    elif kind not in {'int', 'float'}:
        raise ValueError('UserUnit 不是数值，无法可靠转换页面坐标')
    try:
        unit = float(value)
    except (ValueError, OverflowError) as exc:
        raise ValueError('UserUnit 不是有效尺度，无法可靠转换页面坐标') from exc
    if not math.isfinite(unit) or unit <= 0:
        raise ValueError('UserUnit 必须为有限正数，无法可靠转换页面坐标')
    sx, sy = page.rect.width / crop.width, page.rect.height / crop.height
    if any(not math.isfinite(v) or v <= 0 or not math.isclose(v, unit, rel_tol=1e-5)
           for v in (sx, sy)):
        raise ValueError('页面实际尺度与 UserUnit 不一致，无法可靠转换页面坐标')
    return media, crop, fitz.Matrix(sx, sy)


def plumber_to_page_matrix(source, page, rotation):
    """将 pdfplumber 的旋转 MediaBox 坐标转换为原生文字/渲染的裁剪页坐标。"""
    media, crop, scale = page_geometry(page)
    if (not all(math.isfinite(v) for v in source.mediabox)
            or source.mediabox[2] <= source.mediabox[0]
            or source.mediabox[3] <= source.mediabox[1]):
        raise ValueError('pdfplumber MediaBox 无效，无法可靠转换表格坐标')
    # pdfplumber 的页面原点可能非零，旋转平移量由完整 MediaBox 决定，
    # 不能使用 PyMuPDF 基于 CropBox 尺寸生成的 derotation_matrix。
    origin = fitz.Matrix(1, 0, 0, 1, -source.mediabox[0], -source.mediabox[1])
    unrotate = {
        0: fitz.Matrix(1, 0, 0, 1, 0, 0),
        90: fitz.Matrix(0, -1, 1, 0, 0, media.height),
        180: fitz.Matrix(-1, 0, 0, -1, media.width, media.height),
        270: fitz.Matrix(0, 1, -1, 0, media.width, 0),
    }[rotation]
    # PyMuPDF 的 cropbox.x0 保留 PDF 横坐标，y0 已相对 MediaBox 顶边。
    cropped = fitz.Matrix(1, 0, 0, 1, media.x0-crop.x0, -crop.y0)
    # MediaBox/CropBox 和 pdfplumber 使用页面用户单位；原生文字及 page.rect
    # 已计入 UserUnit，使用实际 PDF point。调用方已将 page 旋转归零，
    # 由未旋转页面实际尺寸取得尺度，并在裁剪平移后统一缩放。
    return origin * unrotate * cropped * scale


def word_lines(words):
    groups = []
    for word in sorted(words, key=lambda w: (w['bbox'][1], w['bbox'][0])):
        if not groups or abs(word['bbox'][1] - groups[-1][0]['bbox'][1]) > max(2, (word['bbox'][3]-word['bbox'][1])*.5):
            groups.append([])
        groups[-1].append(word)
    return [sorted(g, key=lambda w: w['bbox'][0]) for g in groups]


def words_text(words):
    return '\n'.join(' '.join(w['text'] for w in group) for group in word_lines(words))


def assemble_page_text(lines, words, tables, *, line_word_order=None):
    """仅替换有原词证据的表格内容，不能以整行中心点决定删除正文。"""
    def identity(word):
        return (word['text'], tuple(word['bbox']), word.get('source'))

    def compact(text):
        return ''.join(c for c in text if not c.isspace())

    table_words = {identity(w) for table in tables for w in table.get('source_words', [])}
    elements, errors, represented = [], [], set()
    for line in lines:
        bounds = fitz.Rect(line['bbox']) + (-.5, -.5, .5, .5)
        members = [(i, w) for i, w in enumerate(words)
                   if i not in represented and bounds.contains(fitz.Rect(w['bbox']))
                   and w.get('source') == line.get('source')]
        represented.update(i for i, _ in members)
        replaced = {i for i, w in members if identity(w) in table_words}
        if not replaced:
            elements.append((line['bbox'][1], line['bbox'][0], line['text']))
            continue
        # 单行按横坐标排列，OCR组合行按词行分组；不重写原文空格、负号或单位。
        height = min(w['bbox'][3] - w['bbox'][1] for _, w in members)
        explicit_order = (line_word_order or {}).get(id(line))
        member_ids = {id(w) for _, w in members}
        explicit_ids = [id(w) for w in explicit_order] if explicit_order is not None else []
        if (explicit_ids and len(set(explicit_ids)) == len(explicit_ids)
                and set(explicit_ids) == member_ids):
            order = {identity: index for index, identity in enumerate(explicit_ids)}
            members.sort(key=lambda pair: order[id(pair[1])])
        elif '\n' in line['text']:
            # 已组装的多行OCR须复用其实际分行规则；合并表头高度可能不足两个字高。
            order = {id(w): position for position, w in enumerate(
                w for group in word_lines([w for _, w in members]) for w in group)}
            members.sort(key=lambda pair: order[id(pair[1])])
        elif bounds.height <= 2 * max(height, 1):
            members.sort(key=lambda pair: pair[1]['bbox'][0])
        else:
            members.sort(key=lambda pair: (pair[1]['bbox'][1], pair[1]['bbox'][0]))
        original = line['text']
        positions = [i for i, c in enumerate(original) if not c.isspace()]
        if compact(original) != ''.join(compact(w['text']) for _, w in members):
            # 无法证明词与行一一对应时宁可显示原文并标记核对，不能删除未知部分。
            elements.append((line['bbox'][1], line['bbox'][0], original))
            errors.append({'stage': 'text_assembly', 'code': 'text_assembly_unresolved',
                           'bbox_pdf': list(line['bbox']),
                           'reason': '行文字与表格原词无法逐项对应，已保留整行原文，请核对重复或归属'})
            continue
        cursor, removed = 0, set()
        for i, w in members:
            length = len(compact(w['text']))
            if i in replaced and length:
                removed.update(range(positions[cursor], positions[cursor + length - 1] + 1))
            cursor += length
        text = ''.join(c for i, c in enumerate(original) if i not in removed).strip()
        if text:
            elements.append((line['bbox'][1], line['bbox'][0], text))
    for i, w in enumerate(words):
        if i not in represented and identity(w) not in table_words:
            elements.append((w['bbox'][1], w['bbox'][0], w['text']))
            errors.append({'stage': 'text_assembly', 'code': 'text_assembly_unresolved',
                           'bbox_pdf': list(w['bbox']),
                           'reason': '识别词未关联到文本行，已独立保留，请核对阅读顺序'})
    elements.extend((t['bbox_pdf'][1], t['bbox_pdf'][0], t['markdown']) for t in tables)
    return '\n\n'.join(text for _, _, text in sorted(elements, key=lambda e: (e[0], e[1]))), errors


def table_entry(cells, words, page_no, index, source, *, ordered_lines=None):
    xs = sorted({round(c[0], 2) for c in cells} | {round(c[2], 2) for c in cells})
    ys = sorted({round(c[1], 2) for c in cells} | {round(c[3], 2) for c in cells})
    rows = [['' for _ in xs[:-1]] for _ in ys[:-1]]
    structured = []
    assigned = [[] for _ in cells]
    unassigned = []
    for word in words:
        word_box = fitz.Rect(word['bbox'])
        # 中心点恰好落在分隔线上时，原逻辑会同时填入两个格。
        # 只接受整个词框（含坐标取整容差）唯一位于某格的情况。
        owners = [i for i, box in enumerate(cells)
                  if (fitz.Rect(box) + (-.5, -.5, .5, .5)).contains(word_box)]
        if len(owners) == 1:
            assigned[owners[0]].append(word)
        else:
            unassigned.append({**deepcopy(word), 'candidate_cells': [i for i, box in enumerate(cells)
                               if (fitz.Rect(box) & word_box).get_area() > 0]})
    for index_in_table, box in enumerate(cells):
        x0, y0, x1, y1 = [round(v, 2) for v in box]
        r, c = ys.index(y0), xs.index(x0)
        cell_words = assigned[index_in_table]
        text = words_text(cell_words)
        if ordered_lines and cell_words:
            wanted = {id(word) for word in cell_words}
            groups = [[word for word in group if id(word) in wanted] for group in ordered_lines]
            groups = [group for group in groups if group]
            observed = [id(word) for group in groups for word in group]
            # 仅用完整且唯一对应本单元格的已验证行序；未知/重复关联不猜。
            if set(observed) == wanted and len(observed) == len(wanted):
                groups.sort(key=lambda group: (min(word['bbox'][1] for word in group),
                                               min(word['bbox'][0] for word in group)))
                text = '\n'.join(' '.join(word['text'] for word in group) for group in groups)
        rows[r][c] = text
        numeric_checks = [deepcopy(w['numeric_verification']) for w in cell_words if w.get('numeric_verification')]
        structured.append({'row': r, 'column': c, 'rowspan': ys.index(y1)-r,
                           'colspan': xs.index(x1)-c, 'text': text, 'bbox_pdf': list(box),
                           'content_state': 'recognized' if text else 'empty_unverified',
                           **({'numeric_verification': numeric_checks,
                               'numeric_status': 'numeric_uncertain' if any(v['status'] != 'verified' for v in numeric_checks) else 'verified'}
                              if numeric_checks else {})})
    def md(row):
        return '| ' + ' | '.join(v.replace('|', '\\|').replace('\n', ' ') for v in row) + ' |'
    markdown = '\n'.join([md(rows[0]), md(['---'] * len(rows[0]))] + [md(r) for r in rows[1:]])
    if unassigned:
        markdown += '\n\n> 表格内以下文字的单元格归属待确认，未填入数据格：\n\n' + words_text(unassigned)
    return {'id': f'p{page_no}_table_{index}', 'page': page_no, 'bbox_pdf': [xs[0], ys[0], xs[-1], ys[-1]],
            'coordinate_unit': 'pdf_point', 'source': source, 'rows': rows, 'cells': structured,
            'columns': rows[0], 'data': rows[1:], 'markdown': markdown,
            'source_words': [{'text': w['text'], 'bbox': list(w['bbox']), 'source': w.get('source')}
                             for w in words],
            'unassigned_words': unassigned,
            'review_reasons': (['cell_assignment'] if unassigned else []) + (
                ['numeric_uncertain'] if any(c.get('numeric_status') == 'numeric_uncertain' for c in structured) else []),
            # 空格只说明没有识别词；是否遗漏须由可见墨迹覆盖检查判断。
            'needs_review': bool(unassigned) or any(c.get('numeric_status') == 'numeric_uncertain' for c in structured),
            'continuation': 'independent', 'raw_text': words_text(words)}


def repair_short_grid_gaps(horizontal, vertical, binary, pixel_scale=1):
    """仅桥接共轴、两侧均有交点且缺口无墨迹的短线；不用修复线擦除原图。"""
    import numpy as np
    repaired = []
    gap_limit = max(1, round(8 * pixel_scale))
    support = max(1, round(50 * pixel_scale))
    for lines, cross, ink in ((horizontal, vertical, binary),
                              (vertical.T, horizontal.T, binary.T)):
        result = lines.copy()
        for row_index, row in enumerate(lines):
            positions = np.flatnonzero(row)
            if not len(positions):
                continue
            breaks = np.flatnonzero(np.diff(positions) > 1)
            starts = np.r_[0, breaks + 1]
            ends = np.r_[breaks, len(positions) - 1]
            for i in range(len(starts) - 1):
                left0, left1 = positions[starts[i]], positions[ends[i]]
                right0, right1 = positions[starts[i+1]], positions[ends[i+1]]
                if not (0 < right0-left1-1 <= gap_limit):
                    continue
                if min(left1-left0+1, right1-right0+1) < support:
                    continue
                if not (cross[row_index, left0:left1+1].any()
                        and cross[row_index, right0:right1+1].any()):
                    continue
                # 缺口附近有字形/符号时不补线，避免穿过有效文字。
                margin = max(1, round(2 * pixel_scale))
                if ink[max(0, row_index-margin):row_index+margin+1, left1+1:right0].any():
                    continue
                result[row_index, left1+1:right0] = 255
        repaired.append(result)
    return repaired[0] | repaired[1].T


def raster_grid(pix, pixel_scale=1, *, repair=False):
    import cv2
    import numpy as np
    img = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width, pix.n).copy()
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    threshold = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[0]
    # 覆盖检查包含灰度200以内的墨迹，线框检测也须包含其抗锯齿边缘。
    binary = cv2.threshold(gray, max(200, threshold), 255, cv2.THRESH_BINARY_INV)[1]
    # 原阈值以216 dpi、默认用户单位的像素为准；放大后不可把字形笔画当线框。
    minimum_line = max(1, round(50 * pixel_scale))
    horizontal_size = max(minimum_line, pix.width//20)
    vertical_size = max(minimum_line, pix.height//40)
    # 补线要求端点与原图同轴；偶数核默认锚点会平移一像素。
    if repair:
        horizontal_size |= 1
        vertical_size |= 1
    horizontal = cv2.morphologyEx(binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (horizontal_size, 1)))
    vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, vertical_size)))
    grid = cv2.bitwise_or(horizontal, vertical)
    if repair:
        grid = repair_short_grid_gaps(horizontal, vertical, binary, pixel_scale)
    return img, grid


def native_open_tail_candidate(page, tables, unit_scale=1):
    """仅用原生竖边判断是否需要栅格核对；不据此生成或延长单元格。"""
    if not tables:
        return False
    verticals = []
    for drawing in page.get_drawings():
        for item in drawing.get('items', []):
            if item[0] == 'l':
                a, b = item[1:3]
                if abs(a.x-b.x) <= unit_scale:
                    verticals.append(((a.x+b.x)/2, min(a.y,b.y), max(a.y,b.y)))
            elif item[0] == 're':
                box = fitz.Rect(item[1])
                if box.width <= unit_scale and box.height > 2*unit_scale:
                    verticals.append(((box.x0+box.x1)/2, box.y0, box.y1))
    for table in tables:
        xs = sorted({c[j] for c in table['cells'] for j in (0, 2)})
        bottom = table['bbox'][3]
        if len(xs) >= 3 and all(any(abs(x-vx) <= unit_scale and top <= bottom+unit_scale and end > bottom+2*unit_scale
                                   for vx, top, end in verticals) for x in xs):
            return True
    return False


def record_incomplete_tables(row, regions, *, tolerance=.1):
    """两种文字来源共用结构诊断，正文和可靠子表均保持原样。"""
    for region in regions:
        related = [t for t in row['tables'] if
                   (fitz.Rect(region['bbox_pdf']) + (-tolerance, -tolerance, tolerance, tolerance)).contains(fitz.Rect(t['bbox_pdf']))]
        if len(related) == 1:
            region['table_id'] = related[0]['id']
            # 栅格轮廓在边线内侧，原生格可在边线中心；保留检测框，
            # 将人工核对框并入唯一原生表边界，防止保存时截断旧表。
            region['source_bbox_pdf'] = list(region['bbox_pdf'])
            region['bbox_pdf'] = list(fitz.Rect(region['bbox_pdf']) | fitz.Rect(related[0]['bbox_pdf']))
        region['source_words'] = deepcopy([w for w in row['words'] if inside(w['bbox'], region['bbox_pdf'])])
        row.setdefault('unresolved_tables', []).append(region)
        row['errors'].append({'stage': 'table_geometry', 'code': 'table_structure_unresolved', **deepcopy(region)})


def raster_cells(pix, origin, scale, pixel_scale=1, *, incomplete_regions=None):
    # 只按闭合线框恢复单元格；不按内容删列，也不猜测无框表格的列边界。
    import cv2
    _, grid = raster_grid(pix, pixel_scale, repair=True)
    contours, hierarchy = cv2.findContours(grid, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    if hierarchy is None:
        return []
    groups = {}
    for i, contour in enumerate(contours):
        parent = hierarchy[0][i][3]
        if parent < 0 or hierarchy[0][i][2] >= 0:
            continue
        x, y, w, h = cv2.boundingRect(contour)
        if w < 20 * pixel_scale or h < 12 * pixel_scale:
            continue
        # 闭合轮廓还须接近矩形；圆形/装饰孔洞不能仅用其外接框变成单元格。
        if cv2.contourArea(contour) < .95 * max(1, (w - 1) * (h - 1)):
            continue
        groups.setdefault(parent, []).append([x, y, x+w, y+h])
    output = []
    for parent, boxes in groups.items():
        from agent.agent_backend.utils.parser.pdf_scan_evidence import regular_visible_grid
        regular = regular_visible_grid(grid,cv2.boundingRect(contours[parent]),pixel_scale)
        if regular is not None:
            bottom = cv2.boundingRect(contours[parent])[1]+cv2.boundingRect(contours[parent])[3]
            if bottom-max(c[3] for c in regular) <= 6*pixel_scale:
                output.append([[origin[j%2]+b[j]/scale for j in range(4)] for b in regular])
                continue
        # 同一条边两侧的像素轮廓存在数像素差异，按几何距离吸附。
        axes = []
        for axis in (0, 1):
            clusters = []
            for v in sorted(b[j] for b in boxes for j in (axis, axis+2)):
                if not clusters or v-clusters[-1][-1] > 6 * pixel_scale:
                    clusters.append([])
                clusters[-1].append(v)
            axes.append([sum(c)/len(c) for c in clusters])
        # 必须有真实内部分隔线，且所有闭合格恰好覆盖外框；不再要求至少四格。
        # 单一装饰方框没有内部分隔线，局部闭合的开放图形不能覆盖外框。
        if max(len(axis) for axis in axes) < 3:
            continue
        snapped = [[min(axes[j % 2], key=lambda v: abs(v-b[j])) for j in range(4)] for b in boxes]
        x, y, width, height = cv2.boundingRect(contours[parent])
        outer = [x, y, x + width, y + height]
        extent = [axes[0][0], axes[1][0], axes[0][-1], axes[1][-1]]
        # 印章与外框相连会扩大父轮廓，短边角缺口会使单格连到外部。
        # 只以已经观察到的矩形轴为候选，并逐边验证原栅格；不从行数补表。
        from agent.agent_backend.utils.parser.pdf_scan_evidence import complete_visible_cells
        verified = complete_visible_cells(snapped, axes, grid, pixel_scale)
        if verified is not None:
            # 向下延伸的开放尾行仍由原路径记录，不能用上部矩形隐藏它。
            tail = outer[3] - extent[3] > 6 * pixel_scale
            if not tail:
                output.append([[origin[j % 2] + b[j]/scale for j in range(4)] for b in verified])
                continue
        open_tail = None
        if any(abs(a - b) > 6 * pixel_scale for a, b in zip(outer, extent)):
            # 页尾续表可没有底线。仅在调用方会保留未闭合区域诊断时，
            # 接受其上方完整的矩形子表；不得凭外接框补造最后一行。
            tolerance = 6 * pixel_scale
            if (incomplete_regions is None or len(axes[0]) < 3 or len(axes[1]) < 3
                    or any(abs(outer[k]-extent[k]) > tolerance for k in (0, 1, 2))
                    or outer[3]-extent[3] <= tolerance):
                continue
            start, stop = round(extent[3]), outer[3]
            radius = max(1, round(3 * pixel_scale))
            # 尾部每条列边必须有持续的同轴墨迹；斜线/装饰连接不算续行。
            supports = [(grid[start:stop, max(0, round(x)-radius):round(x)+radius+1] > 0)
                        .any(axis=1) for x in axes[0]]
            # 旋转插值可能让线端相差一个像素；只允许既有几何半径内的
            # 末端差异，不跨过内部断点，也不补造尾行底边。
            def continuous_to_endpoint(support):
                occupied = support.nonzero()[0]
                if not len(occupied):
                    return False
                end = occupied[-1]+1
                return len(support)-end <= radius and support[:end].all()
            if not all(continuous_to_endpoint(support) for support in supports):
                continue
            open_tail = [extent[0], extent[3], extent[2], outer[3]]
        complete = all(b[0] < b[2] and b[1] < b[3] for b in snapped)
        for y0, y1 in zip(axes[1], axes[1][1:]):
            spans = sorted((b[0], b[2]) for b in snapped if b[1] <= y0 and b[3] >= y1)
            if (not spans or spans[0][0] != axes[0][0] or spans[-1][1] != axes[0][-1]
                    or any(left[1] != right[0] for left, right in zip(spans, spans[1:]))):
                complete = False
                break
        if complete:
            output.append([[origin[j % 2] + b[j]/scale for j in range(4)] for b in snapped])
            if open_tail is not None:
                incomplete_regions.append({
                    # 人工核对范围包括已恢复部分，允许修订成一张完整表，
                    # 避免尾行被另建为缺少原表头的独立表格。
                    'bbox_pdf': [origin[j % 2]+v/scale for j, v in enumerate(
                        [extent[0], extent[1], extent[2], outer[3]])],
                    'unclosed_bbox_pdf': [origin[j % 2]+open_tail[j]/scale for j in range(4)],
                    'source': 'visible_grid_open_tail', 'needs_review': True,
                    'reason': '表格底部未闭合：已恢复上方完整单元格，尾部文字保留待核对，未推断行或合并关系'})
    return output


def table_block_mode(img, groups, scale, psm):
    """仅单一闭合表格且没有侧栏内容时采用块分割，不按OCR候选择优。"""
    if psm != 3 or len(groups) != 1 or not groups[0]:
        return psm
    import numpy as np
    cells = groups[0]
    # groups使用相对于本张裁剪图的PDF单位，转换回像素。
    left = min(b[0] for b in cells) * scale
    right = max(b[2] for b in cells) * scale
    margin = max(2, round(img.shape[1] * .01))
    gray = img.min(axis=2) if img.ndim == 3 else img
    ink = gray < 128
    if (np.any(ink[:, :max(0, int(left)-margin)]) or
            np.any(ink[:, min(img.shape[1], int(right)+margin):])):
        return psm
    # 大字形的块分割实验不能推广到普通小字。仅统计表内连通字形，
    # 不让表外大标题影响选择；保留主识别/独立复核，绝不按输出值选模式。
    import cv2
    top = max(0, int(min(b[1] for b in cells) * scale))
    bottom = min(img.shape[0], int(max(b[3] for b in cells) * scale))
    region = ink[top:bottom, max(0, int(left)):min(img.shape[1], int(right))]
    if not region.size:
        return psm
    _, _, stats, _ = cv2.connectedComponentsWithStats(region.astype(np.uint8))
    heights = [int(h) for x, y, w, h, area in stats[1:]
               if h >= 8 and w >= 2 and .08 <= w / h <= 3 and area / (w * h) >= .08]
    if len(heights) < 8 or np.median(heights) < 48 or np.percentile(heights, 75) < 64:
        return psm
    return 6


def ocr_region(page, rect, client, lang, dpi, psm, covered_words=(), *, original=False, deadline=None, metadata=None, raw_scan=False, input_size=None):
    scale = dpi / 72
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=rect, alpha=False)
    import cv2
    import numpy as np
    pixel_scale = scale * page_geometry(page)[2].a / 3
    img, grid = raster_grid(pix, pixel_scale)
    # 表格线只从 OCR 输入中去除，坐标、边框检测和原图均保持不变。
    incomplete_regions = []
    cell_groups = raster_cells(pix, (0, 0), scale, pixel_scale,
                               incomplete_regions=incomplete_regions if metadata is not None else None) if not original else []
    if cell_groups and not raw_scan:
        margin = 2 * max(1, round(pixel_scale)) + 1
        if incomplete_regions:
            metadata['recovery_cell_inset'] = page_geometry(page)[2].a
            # 只对有明确表格几何的区域去线；不扩大到其他图形或正文。
            mask = np.zeros(grid.shape, np.uint8)
            boxes = [r['bbox_pdf'] for r in incomplete_regions]
            boxes += [[min(c[0] for c in g), min(c[1] for c in g),
                       max(c[2] for c in g), max(c[3] for c in g)] for g in cell_groups]
            for box in boxes:
                x0,y0,x1,y1 = [round(v*scale) for v in box]
                mask[max(0,y0-margin):min(grid.shape[0],y1+margin),
                     max(0,x0-margin):min(grid.shape[1],x1+margin)] = 255
            grid = cv2.bitwise_and(grid, mask)
        img[cv2.dilate(grid, np.ones((margin, margin), np.uint8)) > 0] = 255
    # 已有可用文字只从识别输入中遮去，原生内容和真实坐标仍进入最终结果。
    for word in (() if original else covered_words):
        x0, y0, x1, y1 = [round(v*scale - (pix.x if i % 2 == 0 else pix.y)) for i, v in enumerate(word['bbox'])]
        img[max(0, y0):max(0, y1), max(0, x0):max(0, x1)] = 255
    effective_psm = table_block_mode(img, cell_groups, scale, psm) if not original and not raw_scan else psm
    sx=sy=1.0
    ox,oy=pix.x,pix.y
    if input_size is not None:
        factors=[k for k in (2,3,4) if tuple(input_size)==((pix.width+pix.x%k+k-1)//k,(pix.height+pix.y%k+k-1)//k)]
        if not original or len(factors)!=1:
            raise ValueError('invalid OCR input size')
        factor=factors[0]
        left,top=pix.x%factor,pix.y%factor
        sx=sy=float(factor)
        ox,oy=pix.x-left,pix.y-top
        img=cv2.copyMakeBorder(img,top,(-(pix.height+top))%factor,left,(-(pix.width+left))%factor,cv2.BORDER_CONSTANT,value=(255,255,255))
        img=cv2.resize(img,input_size,interpolation=cv2.INTER_AREA)
    ok, encoded = cv2.imencode('.png', cv2.cvtColor(img, cv2.COLOR_RGB2BGR))
    if not ok:
        raise ValueError('OCR 图像编码失败')
    options = {}
    if (original or raw_scan) and getattr(client, 'supports_preprocessing', False) is True:
        options['preprocessing'] = 'original_gray'
    if deadline is not None and getattr(client, 'supports_execution_budget', False) is True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('OCR区域执行预算已耗尽')
        options['execution_budget_seconds'] = remaining
    data = client.image_to_data(encoded.tobytes(), lang=lang, psm=effective_psm, **options)
    if options.get('preprocessing') and data.get('preprocessing') != 'original_gray':
        # 旧OCR服务可能静默忽略参数；不得宣称原图灰度回退已经生效。
        raise ValueError('OCR服务未确认原图灰度处理模式，请核对服务版本')
    if metadata is not None:
        # 完整栅格也必须约束补识别归属，不能只给结构残缺表传单元格。
        if cell_groups:
            metadata['recovery_cell_inset'] = page_geometry(page)[2].a
            metadata['recovery_cells'] = [[v+(pix.x if j % 2 == 0 else pix.y)/scale
                                           for j, v in enumerate(cell)]
                                          for group in cell_groups for cell in group]
        metadata['segmentation_psm'] = effective_psm
        metadata['preprocessing'] = data.get('preprocessing', 'standard')
        metadata['render_dpi'] = dpi
        metadata['segmentation_reason'] = 'single_grid_without_side_content' if effective_psm != psm else 'requested_mode'
        metadata['execution_budget_enforced'] = data.get('execution_budget_enforced') is True and 'execution_budget_seconds' in options
    verifications = data.get('numeric_verification')
    if not isinstance(verifications, list):
        verifications = []
    words, grouped, word_indices = [], {}, {}
    for i, raw in enumerate(data.get('text', [])):
        text = str(raw).strip()
        if not text:
            continue
        x, y = float(data['left'][i]), float(data['top'][i])
        w, h = float(data['width'][i]), float(data['height'][i])
        box = [(ox+x*sx)/scale, (oy+y*sy)/scale, (ox+(x+w)*sx)/scale, (oy+(y+h)*sy)/scale]
        word = {'text': text, 'bbox': box, 'source': 'ocr', 'confidence': float(data['conf'][i])}
        matches = [v for v in verifications if isinstance(v, dict)
                   and isinstance(v.get('word_indices'), list) and i in v['word_indices']]
        if re.search(r'\d', text) or matches:
            evidence = deepcopy(matches[0]) if len(matches) == 1 else {
                'status': 'numeric_uncertain', 'reason_code': 'numeric_verification_missing',
                'primary': text, 'secondary': ''}
            pixel_box = evidence.get('bbox_pixel')
            valid_box = (isinstance(pixel_box, list) and len(pixel_box) == 4
                         and all(isinstance(v, (int, float)) and math.isfinite(v) for v in pixel_box))
            if evidence.get('status') not in ('verified', 'numeric_uncertain') or not valid_box:
                evidence.update(status='numeric_uncertain', reason_code='numeric_verification_missing')
            evidence['bbox_pdf'] = [(ox+pixel_box[0]*sx)/scale, (oy+pixel_box[1]*sy)/scale,
                                    (ox+pixel_box[2]*sx)/scale, (oy+pixel_box[3]*sy)/scale] if valid_box else box
            evidence['page'] = page.number + 1
            evidence['source'] = 'ocr'
            word['numeric_verification'] = evidence
        words.append(word)
        word_indices[id(word)] = i
        key = tuple(data.get(k, [0]*len(data['text']))[i] for k in ('block_num', 'par_num', 'line_num'))
        grouped.setdefault(key, []).append(word)
    lines = []
    for key, group in grouped.items():
        box = [min(w['bbox'][0] for w in group), min(w['bbox'][1] for w in group),
               max(w['bbox'][2] for w in group), max(w['bbox'][3] for w in group)]
        line_text = None
        ordered_words = None
        try:
            raw_sequence = data['word_num']
            if not isinstance(raw_sequence, (list,tuple)):
                raise ValueError('invalid word sequence')
            raw_orders = [raw_sequence[word_indices[id(word)]] for word in group]
            valid_id = lambda value: (not isinstance(value,bool) and int(value) > 0 and float(value) == int(value))
            sequence = [int(value) for value in raw_orders]
            if (all(valid_id(value) for value in key) and all(valid_id(value) for value in raw_orders)
                    and len(set(sequence)) == len(sequence)):
                # 引擎已确定行号和词序时，不因下沉字形、标点或上下标再次
                # 按字框顶部拆行。缺少/无效序号仍沿用几何回退。
                ordered_words = [word for _,word in sorted(zip(sequence,group),key=lambda pair:pair[0])]
                line_text = ' '.join(word['text'] for word in ordered_words)
        except (KeyError, IndexError, TypeError, ValueError, OverflowError):
            pass
        line = {'text': line_text if line_text is not None else words_text(group), 'bbox': box, 'source': 'ocr'}
        if ordered_words is not None:
            # 仅在本次提取中使用对象身份；落库前移除此内部关联。
            line['_ordered_words'] = ordered_words
        lines.append(line)
    return words, lines, pix


def retained_ocr_line(line, unique):
    """保留本行仍有效的词序，不把邻行或已去重的词重新加入正文。"""
    output = {key: value for key, value in line.items() if key != '_ordered_words'}
    if '_ordered_words' in line:
        identities = {id(word) for word in unique}
        retained = [word for word in line['_ordered_words'] if id(word) in identities]
        output['text'] = ' '.join(word['text'] for word in retained)
    else:
        retained = [word for word in unique if inside(word['bbox'], line['bbox'])]
        output['text'] = words_text(retained)
    return output if retained else None


def uncovered_image(page, rect, words, dpi):
    """用可见墨迹与文字位置逐块核对覆盖；字符数不能证明图片已被识别。"""
    import cv2
    scale = dpi / 72
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=rect, alpha=False)
    unit = page_geometry(page)[2].a
    pixel_scale = scale * unit / 3
    img, grid = raster_grid(pix, pixel_scale)
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    ink = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY_INV)[1]
    ink[grid > 0] = 0
    for word in words:
        if text_quality(word['text'])['abnormal_characters']:
            continue
        # 字框边缘的抗锯齿容差按实际用户单位缩放；不放宽未覆盖组件的面积要求。
        margin = .8 * unit
        box = fitz.Rect(word['bbox']) + (-margin, -margin, margin, margin)
        x0, y0, x1, y1 = [round(v*scale - (pix.x if i % 2 == 0 else pix.y)) for i, v in enumerate(box)]
        ink[max(0, y0):max(0, y1), max(0, x0):max(0, x1)] = 0
    count, _, stats, _ = cv2.connectedComponentsWithStats(ink)
    missing = []
    for x, y, w, h, area in stats[1:]:
        # 排除栅格交点残屑；保留分散文字、负号及图形，无法确认的图形仍需识别。
        if area >= 5 and (w >= 3 or h >= 3):
            missing.append([(pix.x+x)/scale, (pix.y+y)/scale, (pix.x+x+w)/scale, (pix.y+y+h)/scale])
    return missing, pix


def same_word(left, right):
    a, b = fitz.Rect(left['bbox']), fitz.Rect(right['bbox'])
    return (left['text'].strip() == right['text'].strip() and
            (a & b).get_area() > .5 * min(a.get_area(), b.get_area()))


def recovery_boxes(missing, region, deadline=None):
    """按邻近字形合并原始缺失区域；完整返回用于记录，调用者只执行前四个。"""
    components = [fitz.Rect(box) for box in missing if not fitz.Rect(box).is_empty]
    expanded = [box + (-max(box.height,1),-max(box.height,1)/3,
                       max(box.height,1),max(box.height,1)/3) for box in components]
    parents = list(range(len(components)))
    def root(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index
    # 邻接只能来自原始墨迹。合并后的外接框含有空白，不能用更大的
    # 框和高度继续吸收原本不相邻的其他行/列；双向判断消除输入顺序影响。
    expired = False
    # 证照底纹可能生成数万连通域。按扩展框横轴扫描，先排除不可能
    # 相交的框；精确邻接条件仍与原来的双向矩形相交完全相同。
    # 使用数值边界判断，避免为每一对构造昂贵的 MuPDF 交集对象。
    bounds = [tuple(box) for box in components]
    expanded_bounds = [tuple(box) for box in expanded]
    order = sorted(range(len(components)), key=lambda i: expanded_bounds[i][0])
    def overlaps(a, b):
        return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]
    for position, i in enumerate(order):
        left = expanded_bounds[i]
        for offset in range(position+1, len(order)):
            j = order[offset]
            right = expanded_bounds[j]
            if right[0] >= left[2]:
                break
            if deadline is not None and time.monotonic() >= deadline:
                expired = True
                break
            if not overlaps(left, right):
                continue
            if overlaps(left, bounds[j]) or overlaps(right, bounds[i]):
                parents[root(j)] = root(i)
        if expired:
            break
    merged = {}
    for i, box in enumerate(components):
        key = root(i)
        merged[key] = merged[key] | box if key in merged else box
    groups = list(merged.values())
    return [(box, (box + (-max(box.height, 1), -max(box.height, 1), max(box.height, 1), max(box.height, 1))) & region)
            for box in sorted(groups, key=lambda b: (b.y0, b.x0))]


def ocr_quality_evidence(words):
    low = [w for w in words if w['confidence'] < 50]
    mean = sum(w['confidence'] for w in words)/max(1,len(words))
    return low, mean, bool(words and (len(low)/len(words) > .1 or mean < 80))


def quality_recovery_lines(words, lines, cells=()):
    result, seen = [], set()
    # 同一阈值同时检查明确单元格，不能让其他格/正文的高置信度
    # 稀释一个完整业务字段的低质量。原页总体规则保持不变。
    for cell in dict.fromkeys(tuple(cell) for cell in cells):
        members = [word for word in words if fitz.Rect(cell).contains(fitz.Rect(word['bbox']))]
        cell_low, cell_mean, cell_review = ocr_quality_evidence(members)
        if cell_review:
            box = (min(w['bbox'][0] for w in members),min(w['bbox'][1] for w in members),
                   max(w['bbox'][2] for w in members),max(w['bbox'][3] for w in members))
            if box not in seen:
                seen.add(box)
                result.append({'bbox':list(box),'source_line_bbox':list(cell),'quality_scope':'table_cell',
                    'mean_confidence':cell_mean,'low_confidence_word_count':len(cell_low),'word_count':len(members)})
    low, _, needs_review = ocr_quality_evidence(words)
    if not needs_review:
        return sorted(result,key=lambda line:(line['bbox'][1],line['bbox'][0]))
    review = low or [w for w in words if w['confidence'] < 80]
    for line in lines:
        members = sorted([w for w in words if inside(w['bbox'],line['bbox'])],
                         key=lambda w:(w['bbox'][0],w['bbox'][1]))
        groups = []
        for word in members:
            box = fitz.Rect(word['bbox'])
            if groups:
                previous = groups[-1]
                right = max(w['bbox'][2] for w in previous)
                height = max([box.height] + [w['bbox'][3]-w['bbox'][1] for w in previous])
                # 仅拆开远超字高的明确空白，不按文件名、文字内容或固定页坐标裁剪。
                if box.x0-right <= 3*max(height,1):
                    previous.append(word)
                    continue
            groups.append([word])
        for group in groups:
            if not any(w in review for w in group):
                continue
            box = (min(w['bbox'][0] for w in group),min(w['bbox'][1] for w in group),
                   max(w['bbox'][2] for w in group),max(w['bbox'][3] for w in group))
            if box not in seen:
                seen.add(box)
                result.append({'bbox':list(box),'source_line_bbox':list(line['bbox'])})
    return sorted(result,key=lambda line:(line['bbox'][1],line['bbox'][0]))


def recovery_layout_blocks(page, rect, pix, dpi, deadline):
    """问题过碎时复用原图空白带；只组织识别输入，不改变覆盖证据。"""
    if time.monotonic() >= deadline:
        return []
    import cv2
    import numpy as np
    scale = dpi / 72
    image, grid = raster_grid(pix, scale * page_geometry(page)[2].a / 3)
    ink = cv2.threshold(cv2.cvtColor(image, cv2.COLOR_RGB2GRAY), 200, 255,
                        cv2.THRESH_BINARY_INV)[1]
    ink[grid > 0] = 0
    active = np.flatnonzero(np.any(ink > 0, axis=1))
    if not len(active) or time.monotonic() >= deadline:
        return []
    runs = np.split(active, np.where(np.diff(active) > 1)[0] + 1)
    height = float(np.median([len(run) for run in runs]))
    groups = []
    for run in runs:
        start, end = int(run[0]), int(run[-1])+1
        if groups and start-groups[-1][1] <= 3*height:
            groups[-1][1] = end
        else:
            groups.append([start,end])
    # 邻行链式合并可能把大半页变成一次昂贵的补识别。仅对近整页
    # 的高块，在已有且足够宽的空白带处分开；没有可靠空白则不切字。
    bounded_groups = []
    for start, end in groups:
        pieces = [[start, end]]
        if end-start > max(12*height, .65*pix.height):
            for _ in range(3):
                choices = []
                for pi, (low, high) in enumerate(pieces):
                    if high-low <= 12*height:
                        continue
                    local = [run for run in runs if low <= run[0] and run[-1] < high]
                    for left, right in zip(local, local[1:]):
                        gap = int(right[0])-int(left[-1])-1
                        if gap >= 2*max(2, height/3):
                            bottom, top = int(left[-1])+1, int(right[0])
                            choices.append((gap, min(bottom-low,high-top), pi, bottom, top))
                if not choices:
                    break
                _, _, pi, bottom, top = max(choices)
                low, high = pieces[pi]
                pieces[pi:pi+1] = [[low,bottom],[top,high]]
        bounded_groups.extend(pieces)
    groups = bounded_groups
    # 原生字形可重新渲染细节；扫描位图不因该路径强制放大。
    image_boxes = [fitz.Rect(image['bbox']) for image in page.get_image_info()]
    result = []
    for start,end in groups:
        if time.monotonic() >= deadline:
            break
        columns = np.flatnonzero(np.any(ink[start:end] > 0, axis=0))
        padding = max(2,height)
        vertical_padding = max(2,height/3)
        crop = fitz.Rect((pix.x+int(columns[0])-padding)/scale,
                         (pix.y+start-vertical_padding)/scale,
                         (pix.x+int(columns[-1])+1+padding)/scale,
                         (pix.y+end+vertical_padding)/scale) & rect
        vector_only = not any((box & crop).get_area() > 0 for box in image_boxes)
        target_dpi = max(dpi,300) if vector_only else dpi
        if crop.get_area() * (target_dpi/72)**2 > 16000000:
            target_dpi = dpi
        result.append({'bbox':list(crop),'mode':7 if end-start <= 1.5*height else 6,
                       'dpi':target_dpi})
    return result


def recovery_layout_outside_cells(layout, cells):
    """跨格块仅保留明确表格外的矩形分区；不因碰到表格拆碎整段正文。"""
    result = []
    for block in layout:
        x0,y0,x1,y1 = block['bbox']
        touching = [c for c in cells if min(c[2],x1)>max(c[0],x0)
                    and min(c[3],y1)>max(c[1],y0)]
        if not touching or (len(touching)==1 and fitz.Rect(touching[0]).contains(fitz.Rect(block['bbox']))):
            result.append(block)
            continue
        ys = sorted({y0,y1} | {max(y0,min(y1,c[i])) for c in touching for i in (1,3)})
        pieces = []
        for top,bottom in zip(ys,ys[1:]):
            intervals = sorted((max(x0,c[0]),min(x1,c[2])) for c in touching
                               if min(bottom,c[3])>max(top,c[1]))
            cursor = x0
            gaps = []
            for left,right in intervals:
                if left>cursor:
                    gaps.append((cursor,left))
                cursor = max(cursor,right)
            if cursor<x1:
                gaps.append((cursor,x1))
            for left,right in gaps:
                previous = next((piece for piece in pieces
                                 if piece[0]==left and piece[2]==right and piece[3]==top),None)
                if previous is not None:
                    previous[3] = bottom
                else:
                    pieces.append([left,top,right,bottom])
        result.extend({**block,'bbox':box} for box in pieces)
    return result


def recovery_layout_quality_bounds(layout, quality_lines, rect):
    """裁剪容纳唯一相交的质量词框；不改字框/覆盖掩膜，不跨其他版面块。"""
    result = deepcopy(layout)
    for line in quality_lines:
        box = fitz.Rect(line['bbox']) & rect
        if box.is_empty:
            continue
        matches = [i for i, block in enumerate(result)
                   if (fitz.Rect(block['bbox']) & box).get_area() > 0]
        if len(matches) != 1:
            continue
        index = matches[0]
        expanded = (fitz.Rect(result[index]['bbox']) | box) & rect
        if any(i != index and (expanded & fitz.Rect(block['bbox'])).get_area() > 0
               for i, block in enumerate(result)):
            continue
        result[index]['bbox'] = list(expanded)
    return result


def recover_ocr_coverage(page, rect, client, lang, dpi, words, deadline, metadata, *, quality_lines=()):
    from agent.agent_backend.utils.parser.pdf_scan_evidence import (
        pixel_crop, recovery_segmentation, ordered_word_lines, image_line_support, image_supports_word, recovery_input_size)
    try:
        missing, pix = uncovered_image(page, rect, words, dpi)
    except Exception as exc:
        metadata['coverage_check_failed'] = True
        return [], [], [{'bbox_pdf': list(rect), 'status': 'not_attempted',
                         'reason_code': 'recovery_coverage_failed', 'exception_type': type(exc).__name__}]
    metadata['initial_uncovered'] = missing
    embedded = metadata.get('embedded_evidence', [])
    if not missing and not quality_lines and not any(e['status'] != 'matched' for e in embedded):
        return [], [], []
    attempts, added, lines = [], [], []
    crop_results = {}
    support_cache = {}
    def original_support(crop, recovery_dpi):
        canonical,window=pixel_crop(page,crop,recovery_dpi)
        key=(window,recovery_dpi)
        if key not in support_cache:
            support_cache[key]=image_line_support(page,canonical,recovery_dpi,deadline)
        return support_cache[key]
    added_lineage = {}
    def candidate_key(word):
        return (word['text'], tuple(word['bbox']))
    jobs = [(ink,crop,'ocr_coverage') for ink,crop in recovery_boxes(missing, rect, deadline)]
    for line in quality_lines:
        ink = fitz.Rect(line['bbox']) & rect
        if not ink.is_empty:
            padding = max(ink.height/3, 1)
            jobs.append((ink,(ink+(-padding,-padding,padding,padding)) & rect,'ocr_quality'))
    for evidence in embedded:
        if evidence['status'] != 'matched':
            ink = fitz.Rect(evidence['bbox_pdf']) & rect
            if not ink.is_empty:
                padding = max(ink.height/3,1)
                jobs.append((ink,(ink+(-padding,-padding,padding,padding)) & rect,evidence['trigger']))
    jobs.sort(key=lambda job:(job[0].y0,job[0].x0))
    # 同一主结果的词框不会在恢复过程中变动；避免每个缺失区域重复构造。
    word_contexts = [(word, fitz.Rect(word['bbox'])) for word in words]
    layout = []
    if len(jobs) > 4 and pix is not None and page is not None:
        try:
            layout = recovery_layout_blocks(page,rect,pix,dpi,deadline)
            layout = recovery_layout_outside_cells(layout,metadata.get('recovery_cells',[]))
            layout = recovery_layout_quality_bounds(layout, quality_lines, rect)
        except Exception as exc:
            metadata['recovery_layout_exception_type'] = type(exc).__name__
    planned = []
    for index, (ink_box, crop, trigger) in enumerate(jobs):
        choices = [b for b in layout if fitz.Rect(b['bbox']).contains(ink_box)]
        layout_choice = choices[0] if len(choices) == 1 else None
        if layout_choice:
            block = fitz.Rect(layout_choice['bbox'])
            touches = [fitz.Rect(c) for c in metadata.get('recovery_cells', [])
                       if (fitz.Rect(c) & block).get_area() > 0]
            if touches and not (len({tuple(c) for c in touches}) == 1 and touches[0].contains(block)):
                layout_choice = None
            else:
                crop = block
        owners = [fitz.Rect(c) for c in metadata.get('recovery_cells', []) if fitz.Rect(c).contains(ink_box)]
        # 笔画碎片自身高度不能代表字高。仅取紧邻且纵向包含该碎片的
        # 原始词框提供上下文，不扩张覆盖遮罩，也不据新外框递归吸收邻词。
        for old, context in (() if layout_choice else word_contexts):
            if (context.height > 2 * ink_box.height
                    and context.y0 <= ink_box.y0 and context.y1 >= ink_box.y1
                    and max(context.x0-ink_box.x1, ink_box.x0-context.x1, 0) <= context.height / 4
                    and text_quality(old['text'])['usable']
                    and (not metadata.get('recovery_cells')
                         or (len(owners) == 1 and owners[0].contains(context)))):
                crop = (crop | context) & rect
        reflow_cell = False
        if len(owners) == 1:
            # 原灰度裁剪避开已知单元格边线，不去线也不裁掉候选墨迹。
            inset = metadata.get('recovery_cell_inset', 1)
            # ink_box已经过MuPDF矩形交集运算，内框也经同一路径，避免
            # float64内框与float32交集在边缘舍入后错误判为越界。
            interior = (owners[0] + (inset, inset, -inset, -inset)) & rect
            if not interior.is_empty and interior.contains(ink_box):
                crop = crop & interior
                owned_words = [old for old,context in word_contexts if owners[0].contains(context)]
                reflow_cell = (page is not None and interior.width < interior.height
                    and len(word_lines(owned_words)) >= 2
                    and getattr(client,'supports_execution_budget',False) is True
                    and getattr(client,'supports_preprocessing',False) is True)
                if reflow_cell:
                    crop = interior & rect
        attempt = {'bbox_pdf': list(crop), 'status': 'not_attempted', 'trigger': trigger}
        if layout_choice:
            attempt['recovery_path'] = 'original_layout_block'
        attempt['uncovered_bbox_pdf' if trigger == 'ocr_coverage' else 'quality_bbox_pdf'] = list(ink_box)
        attempts.append(attempt)
        if trigger == 'ocr_quality' and metadata.get('recovery_cells') and len(owners) != 1:
            attempt['reason_code'] = 'recovery_context_ambiguous'
            continue
        recovery_dpi = layout_choice['dpi'] if layout_choice else dpi
        attempt['recovery_dpi'] = recovery_dpi
        original_crop=list(crop)
        crop,window=pixel_crop(page,crop,recovery_dpi)
        attempt.update(bbox_pdf=list(crop),requested_bbox_pdf=original_crop,pixel_window=list(window))
        context=[w for w in words if crop.contains(fitz.Rect(w['bbox']))]
        # 主结果可能仅剩多行中的一行；不能把已识别行数当作原图总行数。
        support=original_support(crop,recovery_dpi)
        mode,mode_reason=recovery_segmentation(context,metadata.get('primary_lines',()),support)
        attempt.update(segmentation_psm=mode,segmentation_reason=mode_reason,
                       image_line_support=support)
        input_size=None if reflow_cell else recovery_input_size(window,support,recovery_dpi)
        if input_size is not None:
            attempt.update(ocr_input_size=list(input_size),input_size_reason='large_consistent_image_lines')
        # 只复用本次区域内完全相同的图像与分段方式；缺失证据仍逐项判断。
        frame=(id(page),tuple(page.transformation_matrix),page.rotation) if isinstance(page,fitz.Page) else (id(page),)
        crop_key = (frame,window,mode if not reflow_cell else 'cell_reflow',recovery_dpi,'original_gray','unmasked',input_size)
        planned.append((index,ink_box,crop,trigger,mode,recovery_dpi,reflow_cell,crop_key,attempt))
    # 先按文字证据优先级、再按同级诊断面积分配四个名额。
    groups = {}
    for index,ink_box,_,trigger,_,_,_,crop_key,_ in planned:
        group = groups.setdefault(crop_key,{'first':index,'boxes':set(),'priority':0})
        group['boxes'].add(tuple(ink_box))
        # 文字差异和已定位正文优先于孤立底纹；不删除未获配额的证据。
        if any(e['status'] != 'matched' and fitz.Rect(e['bbox_pdf']).intersects(ink_box) for e in embedded):
            group['priority'] = 2
        elif trigger == 'ocr_quality' or any(fitz.Rect(c).contains(ink_box) for c in metadata.get('recovery_cells',[])):
            group['priority'] = max(group['priority'],1)
    region_limit = min(4,max(0,metadata.get('remaining_recovery_regions',4)))
    selected = set(sorted(groups,key=lambda key:(
        -groups[key]['priority'], -sum(fitz.Rect(box).get_area() for box in groups[key]['boxes']),groups[key]['first']))[:region_limit])
    for index,ink_box,crop,trigger,mode,recovery_dpi,reflow_cell,crop_key,attempt in planned:
        cached = crop_results.get(crop_key)
        if not metadata.get('execution_budget_enforced'):
            attempt['reason_code'] = 'recovery_budget_unsupported'
            continue
        if cached is None and time.monotonic() >= deadline:
            attempt['reason_code'] = 'recovery_budget_exhausted'
            continue
        if cached is None and (crop_key not in selected or len(crop_results) >= region_limit):
            attempt['reason_code'] = 'recovery_region_limit'
            continue
        attempt['status'] = 'attempted'
        try:
            if cached is None:
                cached = {'index': index}
                crop_results[crop_key] = cached
                try:
                    reflow_candidates = None
                    if reflow_cell:
                        from agent.agent_backend.utils.parser.ocr_reflow_geometry import recognize_cell_candidates
                        reflow_candidates = recognize_cell_candidates(page,crop,client,lang,recovery_dpi,deadline)
                    if reflow_candidates is not None:
                        cached['reflow_candidates'] = reflow_candidates
                        candidates, candidate_lines = [], []
                    else:
                        candidates, candidate_lines, _ = ocr_region(page, crop, client, lang, recovery_dpi, mode,
                                                      original=True, deadline=deadline,
                                                      **({'input_size':tuple(attempt['ocr_input_size'])} if 'ocr_input_size' in attempt else {}))
                    cached['candidates'] = deepcopy(candidates)
                    cached['candidate_lines'] = deepcopy(candidate_lines)
                    cached['candidate_boxes'] = [fitz.Rect(word['bbox']) for word in candidates]
                    # 只接受ocr_region已经验证的行内对象关联，不从几何猜词序。
                    positions = {}
                    source_ids = {id(word) for word in candidates}
                    for line_index, line in enumerate(candidate_lines):
                        ordered = line.get('_ordered_words', [])
                        keys = [candidate_key(word) for word in ordered]
                        if (not ordered or len(set(keys)) != len(keys)
                                or any(id(word) not in source_ids for word in ordered)):
                            continue
                        for position, key in enumerate(keys):
                            positions.setdefault(key, []).append((line_index, position))
                    cached['lineage'] = {key: values[0] for key, values in positions.items() if len(values) == 1}
                except Exception as exc:
                    cached['exception_type'] = type(exc).__name__
                    raise
            else:
                attempt['reused_attempt_index'] = cached['index']
                if 'exception_type' in cached:
                    attempt.update(status='unresolved', reason_code='recovery_failed',
                                   exception_type=cached['exception_type'])
                    continue
                candidates = [deepcopy(word) for word, box in zip(cached['candidates'],cached['candidate_boxes'])
                              if not (box & ink_box).is_empty]
            if 'reflow_candidates' in cached:
                attempt.update(status='unresolved',reason_code='recovery_reflow_review_required',
                               recovery_path='original_cell_reflow',accepted_count=0)
                # 同次整格候选只保存一次；复用项保留与本问题相交的分离来源。
                candidate_values = cached['reflow_candidates']
                if 'reused_attempt_index' in attempt:
                    attempt['candidate_scope'] = 'issue_region'
                    candidate_values = [candidate for candidate in candidate_values
                        if any((fitz.Rect(box)&ink_box).get_area()>0
                               for box in candidate.get('source_bboxes_pdf',[]))]
                attempt['candidates'] = deepcopy(candidate_values)
                continue
            # 同一裁剪的完整证据保留于首次尝试；复用项只重复本问题区域
            # 的候选，并通过既有index指向完整证据，避免整块逐字复制数百遍。
            if 'reused_attempt_index' in attempt:
                attempt['candidate_scope'] = 'issue_region'
            attempt['candidates'] = deepcopy(candidates)
            candidate_keys={candidate_key(w) for w in candidates}
            attempt['candidate_lines'] = [
                {'_ordered_words':[deepcopy(w) for w in line.get('_ordered_words',[]) if candidate_key(w) in candidate_keys]}
                for line in cached.get('candidate_lines',[])]
            accepted = []
            processing_candidates = list(candidates)
            for candidate_index, word in enumerate(processing_candidates):
                box = fitz.Rect(word['bbox'])
                if (box & ink_box).is_empty or not text_quality(word['text'])['usable']:
                    continue
                if any(same_word(word, old) for old in words + added):
                    continue
                current_lineage = cached.get('lineage', {}).get(candidate_key(word))
                def ordered_neighbor(old):
                    previous = added_lineage.get(id(old))
                    if (current_lineage is None or previous is None or previous[0] != crop_key
                            or previous[1][0] != current_lineage[0]
                            or previous[1][1] == current_lineage[1]
                            or old['text'] == word['text']
                            or re.search(r'\d', old['text']+word['text'])
                            or old.get('numeric_verification') or word.get('numeric_verification')):
                        return False
                    old_box = fitz.Rect(old['bbox'])
                    # 同次已验证引擎行的词框可覆盖不止一个后续词；词序和
                    # 框中心方向仍须相符，重复词及数值不走此例外。
                    return ((box.x0+box.x1-old_box.x0-old_box.x1)
                            * (current_lineage[1]-previous[1][1]) > 0)
                overlaps = [old for old in words + added if (box & fitz.Rect(old['bbox'])).get_area() > .2 * min(box.get_area(), fitz.Rect(old['bbox']).get_area())
                            and not ordered_neighbor(old)]
                # 同一字形的两次词框可能因像素取整刚好错开；不能把亚像素
                # 间隙解释为新字。仅保留为冲突，不静默删除真实重复字候选。
                tolerance = 72 / min(dpi, recovery_dpi)
                for old in words + added:
                    if old['text'].strip() != word['text'].strip():
                        continue
                    old_box = fitz.Rect(old['bbox'])
                    if (min(old_box.y1,box.y1)-max(old_box.y0,box.y0) > .5*min(old_box.height,box.height)
                            and max(old_box.x0-box.x1,box.x0-old_box.x1,0) <= tolerance
                            and old not in overlaps):
                        overlaps.append(old)
                if overlaps:
                    attempt['reason_code'] = 'recovery_conflict'
                    # 后续候选仍可能改写原因码；已有冲突不能因此消失。
                    attempt['has_conflicting_candidates'] = True
                    # 不覆盖主值；数值冲突也不能继续作为已核验值使用。
                    for old in overlaps:
                        if old.get('numeric_verification'):
                            old['numeric_verification'].update(status='numeric_uncertain', reason_code='recovery_conflict')
                            old['numeric_verification'].setdefault('recovery_candidates', []).append(deepcopy(word))
                    continue
                if word['confidence'] < 80:
                    continue
                if (re.search(r'\d',word['text']) or word.get('numeric_verification')) and word.get('numeric_verification',{}).get('status')!='verified':
                    attempt['reason_code']='recovery_context_ambiguous'
                    continue
                # 覆盖补识别也必须遵守单元格唯一归属；保留跨格候选供核对，
                # 不能因字高/置信度合格就把跨列文字补入有效正文。
                intersecting_cells = {tuple(c) for c in metadata.get('recovery_cells', [])
                                      if (fitz.Rect(c) & box).get_area() > 0}
                containing_cells = [c for c in intersecting_cells if fitz.Rect(c).contains(box)]
                if intersecting_cells and len(containing_cells) != 1:
                    attempt['reason_code'] = 'recovery_context_ambiguous'
                    attempt.setdefault('ambiguous_cell_candidates', []).append({
                        'text': word['text'], 'bbox_pdf': list(box),
                        'candidate_cell_bboxes': [list(c) for c in sorted(intersecting_cells)]})
                    continue
                # 多字符也可能由图形误识别而来（例如斜线方框→VA）。
                # 先要求与已取得文字有可比字高；单字还须有同排参照。
                # 不删除候选证据，也不把孤立大图形的一次高置信结果当正文。
                comparable = [old for old in words + added
                    if text_quality(old['text'])['usable']
                    and .5 <= fitz.Rect(old['bbox']).height / max(box.height, .001) <= 2]
                image_supported=False
                if len(containing_cells)==1 or not intersecting_cells:
                    support=attempt.get('image_line_support',[])
                    if not support:
                        support=original_support(crop,recovery_dpi)
                        attempt['image_line_support']=support
                    numeric=word.get('numeric_verification',{})
                    image_supported=(image_supports_word(word,support,engine_line_verified=current_lineage is not None)
                        and (not re.search(r'\d',word['text']) or numeric.get('status')=='verified')
                        and numeric.get('status','verified')=='verified')
                if (not comparable and not image_supported) or (not image_supported and len(word['text'].strip()) == 1 and not any(
                    text_quality(old['text'])['usable']
                    and .5 <= fitz.Rect(old['bbox']).height / max(box.height, .001) <= 2
                    and abs((fitz.Rect(old['bbox']).y0 + fitz.Rect(old['bbox']).y1 - box.y0 - box.y1) / 2) <= box.height * .6
                    and min(abs(fitz.Rect(old['bbox']).x1-box.x0), abs(box.x1-fitz.Rect(old['bbox']).x0)) <= box.height * 20
                    for old in comparable)):
                    if candidate_index < len(candidates):
                        # 后面的可靠多字词可能成为同行参照。仅延后核对一次，
                        # 不再次OCR、不放宽条件，第二次仍重新执行全部保护。
                        processing_candidates.append(word)
                        continue
                    attempt['reason_code'] = 'recovery_context_ambiguous'
                    continue
                word['recovery_origin'] = 'uncovered_original_crop'
                if image_supported:
                    word['recovery_adoption_reason']='original_image_line_support'
                added.append(word)
                if current_lineage is not None:
                    added_lineage[id(word)] = (crop_key, current_lineage)
                accepted.append(word)
            # 同次裁剪已接受的字按文字行组织，不让亚像素顶边差改变词序。
            # 仅包括通过全部保护的词；不会重新带入冲突或低置信候选。
            accepted_lines,order_ambiguous=ordered_word_lines(accepted,cached.get('candidate_lines',[]))
            if order_ambiguous:
                attempt['has_conflicting_candidates']=True
                attempt['reason_code']='recovery_context_ambiguous'
                accepted_lines=[[w] for w in accepted]
            for group in accepted_lines:
                lines.append({'text': ' '.join(word['text'] for word in group),
                              'bbox': [min(w['bbox'][0] for w in group), min(w['bbox'][1] for w in group),
                                       max(w['bbox'][2] for w in group), max(w['bbox'][3] for w in group)],
                              'source': 'ocr', '_ordered_words': group})
            attempt['accepted_count'] = len(accepted)
            attempt['status'] = 'recovered' if accepted else 'unresolved'
        except Exception as exc:
            attempt.update(status='unresolved', reason_code='recovery_failed', exception_type=type(exc).__name__)
    return added, lines, attempts


def header_rows(table):
    # 合并单元格跨行决定表头的最小范围；数字数据行不参与扩展。
    rows = table['rows']
    if not rows or not any(re.search(r'[A-Za-z\u4e00-\u9fff]', v) for v in rows[0]):
        return []
    end = 1
    while True:
        extended = max([end] + [c['row']+c['rowspan'] for c in table['cells'] if c['row'] < end])
        if extended == end:
            break
        end = extended
    # 无纵向合并的多层标题：上层存在跨列，下层为非数值标签。
    while end < len(rows) and any(c['colspan'] > 1 for c in table['cells'] if c['row'] == end-1):
        values = [v for v in rows[end] if v.strip()]
        if not values or any(re.search(r'\d', v) for v in values):
            break
        end += 1
    return list(range(min(end, len(rows))))


def column_structure(table):
    edges = sorted({round(c['bbox_pdf'][i], 1) for c in table['cells'] for i in (0, 2)})
    structure = sorted((c['row'], c['column'], c['rowspan'], c['colspan'])
                       for c in table['cells'] if c['row'] in table['header_rows'])
    body = sorted({(c['column'], c['colspan']) for c in table['cells'] if c['row'] not in table['header_rows']})
    return edges, (structure, body)


def continuation_candidates(table, previous):
    """邻页候选须同时满足表题、完整表头和几何结构，不取第一个猜测。"""
    normalize = lambda value: re.sub(r'[（(]?\s*(续表|续|continued)\s*[）)]?', '', value, flags=re.I).strip()
    title = normalize(table.get('caption', ''))
    edges, structure = column_structure(table)
    matches = []
    for prev in previous:
        if not title or title != normalize(prev.get('caption', '')):
            continue
        if not table['header_rows'] or [table['rows'][i] for i in table['header_rows']] != [prev['rows'][i] for i in prev['header_rows']]:
            continue
        old_edges, old_structure = column_structure(prev)
        if (len(edges) == len(old_edges) and all(abs(a-b) < 3 for a, b in zip(edges, old_edges))
                and structure == old_structure):
            matches.append(prev)
    return matches


def borderless_tables(words, existing, page_no, *, ordered_lines=None):
    """由重复行列对齐恢复无框表；不把正文空格当边框，也不推断业务列含义。"""
    import statistics
    available = [w for w in words if not any(inside(w['bbox'], t['bbox_pdf']) for t in existing)]
    if not available:
        return [], []
    height = statistics.median(w['bbox'][3]-w['bbox'][1] for w in available)
    bands = []
    for word in sorted(available, key=lambda w: ((w['bbox'][1]+w['bbox'][3])/2, w['bbox'][0])):
        center = (word['bbox'][1]+word['bbox'][3])/2
        if not bands or abs(center-statistics.mean((w['bbox'][1]+w['bbox'][3])/2 for w in bands[-1])) > height*.6:
            bands.append([])
        bands[-1].append(word)
    lines = []
    for band in bands:
        chunks = []
        for word in sorted(band, key=lambda w:w['bbox'][0]):
            if not chunks or word['bbox'][0]-chunks[-1][-1]['bbox'][2] > max(12, height*1.5):
                chunks.append([])
            chunks[-1].append(word)
        lines.append(chunks)
    def bounds(ws):
        return [min(w['bbox'][0] for w in ws), min(w['bbox'][1] for w in ws),
                max(w['bbox'][2] for w in ws), max(w['bbox'][3] for w in ws)]
    def flat(chunks):
        return [w for chunk in chunks for w in chunk]
    def numeric(chunk):
        return bool(re.fullmatch(r'[<>≤≥=+−\-]?\s*\d[\d.,]*(?:[eE][+−\-]?\d+)?(?:\s*[%％A-Za-zμµ/°℃]+)?', words_text(chunk)))
    def form_label(text):
        # 编号是字段标签的一部分，不是表格的数字列头。
        # 仅延续既有冒号标签判别，不按“申请”等业务词猜测资料类型。
        return bool(re.search(r'^(?:\d{1,3}[.．、)）]\s*)?[^\d:：]{1,30}[:：]', text))
    def narrative(chunk):
        text = words_text(chunk).strip()
        abbreviation = bool(re.search(r'\b(?:Inc|Ltd|Co|Corp|No|[A-Z])\.$', text))
        sentence_clause = bool(re.search(r'(?:此前|以往|本次|此次|现申请).*(?:由|从|为|将|保持)', text))
        return not numeric(chunk) and (len(text) > 60 or len(text.split()) > 7
                                       or bool(re.search(r'[。；;!?！？]', text)) or form_label(text)
                                       or (text.endswith('.') and not abbreviation) or sentence_clause)
    line_boxes = [bounds(flat(line)) for line in lines]
    tables, unresolved, consumed = [], [], set()
    for index, header in enumerate(lines):
        if index in consumed or len(header)<2 or all(numeric(c) for c in header):
            continue
        caption = words_text(flat(lines[index-1])) if index else ''
        titled = bool(re.search(r'^(?:Table\s*\d*|表\s*\d+)', caption, re.I))
        # 表题不是必要条件；重复列结构也适用于纯文字表。
        # 句子、键值标签和编号叙述不因横向空白而成为表格。
        if any(narrative(c) for c in header):
            continue
        if any(re.search(r'[☑☒☐□√✓✔■●▣○]', words_text(c)) for c in header):
            # 带勾选控件的一行是表单选项，不能作为后续不同字段的列头。
            # 普通列头下的数据格仍可含勾选符号。
            continue
        header_boxes = [bounds(c) for c in header]
        separators = [(a[2]+b[0])/2 for a,b in zip(header_boxes,header_boxes[1:])]
        def aligned_record(chunks):
            # 叙述可以是单元格内容：短记录标识与其余列共同对齐时，
            # 句号、冒号和说明长度不能覆盖行列证据。双栏正文没有这样的
            # 记录标识；以冒号标签开头的表单字段仍由原来的过滤处理。
            if (len(chunks) < 2 or all(narrative(c) for c in chunks)
                    or form_label(words_text(chunks[0]))):
                return False
            columns = [sum(bounds(c)[0] > edge for edge in separators) for c in chunks]
            return (len(set(columns)) == len(chunks)
                    and all(abs(bounds(c)[0]-header_boxes[column][0]) <= height*.8
                            for c, column in zip(chunks, columns)))
        # 同时收集可见行及多列行。只取有值行会把交替空值的行距放大一倍。
        anchors = [line_boxes[index][1]]
        visible = [anchors[0]]
        region_end = index+1
        last_y = anchors[0]
        for pos in range(index+1, len(lines)):
            following = lines[pos]
            y = line_boxes[pos][1]
            if y-last_y > height*7 or re.match(r'\s*(?:Note\b|Notes\b|注[:：]|表\d|Table\b)', words_text(flat(following)), re.I):
                break
            if any(narrative(c) for c in following) and not aligned_record(following):
                break
            # 单列段落必须落在候选列附近；缩进续行可稍偏移。
            if len(following)==1 and min(abs(line_boxes[pos][0]-b[0]) for b in header_boxes) > height*2:
                break
            last_y = y
            region_end = pos+1
            if len(following)>=2 or min(abs(line_boxes[pos][0]-b[0]) for b in header_boxes) <= height*.3:
                visible.append(y)
            if len(following) >= 2:
                anchors.append(y)
        intervals = [b-a for a,b in zip(anchors, anchors[1:])]
        pitch = statistics.median(intervals) if intervals else 0
        if len(intervals) == 2 and max(intervals) >= min(intervals)*1.7:
            pitch = min(intervals)
        visible_gaps = [b-a for a,b in zip(visible, visible[1:])]
        repeated_pitch = False
        if visible_gaps:
            tolerance = max(height*.25, statistics.median(visible_gaps)*.15)
            repeated = max(([g for g in visible_gaps if abs(g-proposal) <= tolerance]
                            for proposal in visible_gaps), key=len)
            competitors = [sum(abs(g-proposal)<=tolerance for g in visible_gaps) for proposal in visible_gaps
                           if abs(proposal-statistics.median(repeated)) > tolerance*2]
            if len(repeated) >= max(2, len(visible_gaps)*.5) and len(repeated)>max(competitors,default=0):
                pitch = statistics.median(repeated)
                repeated_pitch = True
        row_words = [[list(c) for c in header]]
        indices, bad, numeric_rows, multi_rows = [index], False, 0, 0
        for j in range(index+1, region_end):
            chunks = lines[j]
            ws = flat(chunks)
            text = words_text(ws)
            if re.match(r'\s*(?:[*†‡]|Note\b|Notes\b|注[:：]|表\d|Table\b)', text, re.I):
                break
            gap = bounds(ws)[1]-bounds([w for c in row_words[-1] for w in c])[3]
            if gap > height*6:
                break
            has_numbers = any(numeric(c) for c in chunks)
            if not has_numbers and len(chunks)==1:
                # 只有显著短于重复行距、且下一数据行仍落在原行节奏上才合并续行。
                # 等距的单列行有独立行位置，空格绝不能继承上一行的数值。
                delta = bounds(ws)[1]-bounds(flat(row_words[-1]))[1]
                next_anchor = next((y for y in anchors if y > bounds(ws)[1]+height*.2), None)
                if pitch:
                    # 下一独立行也可能只有名称（数值为空），不能要求它再次有数字。
                    next_rows = [bounds(flat(line))[1] for line in lines[j+1:]
                                 if bounds(flat(line))[1]-bounds(flat(row_words[-1]))[1] >= pitch*.65
                                 and abs(bounds(line[0])[0]-header_boxes[0][0]) <= height*.8
                                 and len(words_text(flat(line))) < 60]
                    if next_rows:
                        next_anchor = min(next_anchor or next_rows[0], next_rows[0])
                continuation = (len(row_words)>1 and pitch
                                and delta < pitch*.65 and gap < height*1.5)
                if continuation:
                    column = sum(bounds(chunks[0])[0] > edge for edge in separators)
                    if (next_anchor is None or abs(next_anchor-bounds(flat(row_words[-1]))[1]-pitch) > pitch*.3
                            or abs(bounds(chunks[0])[0]-header_boxes[column][0]) > height*2):
                        bad = True
                        # 保留整段证据，以便上游公开具体区域的归属异常。
                        indices.append(j)
                        row_words.append([list(chunks[0])] + [[] for _ in header[1:]])
                        continue
                    row_words[-1][column].extend(chunks[0])
                    indices.append(j)
                    continue
                # 同一行距、同一列位置上的短名称可对应空数值格。
                if not (gap <= height*6
                        and min(abs(bounds(chunks[0])[0]-b[0]) for b in header_boxes) <= height*.8
                        and len(text) < 40 and not re.search(r'[。.!?！？:：]', text)):
                    bad = True
            assigned = [[] for _ in header]
            for chunk in chunks:
                box = bounds(chunk)
                column = sum(box[0] > edge for edge in separators)
                if assigned[column] or (column<len(separators) and box[2]>separators[column]):
                    bad = True
                assigned[column].extend(chunk)
            row_words.append(assigned)
            indices.append(j)
            numeric_rows += int(has_numbers)
            multi_rows += int(len(chunks)>=2)
        if numeric_rows < 2 and multi_rows < 2 and not (multi_rows and len(row_words)>=3 and repeated_pitch):
            continue
        if (len(header) == 2
                and all(re.fullmatch(r'\d+[.)、]', words_text(row[0]).strip()) for row in row_words)
                and not any(numeric(row[1]) for row in row_words)):
            # 连续编号加叙述是列表；数字表头的放宽不改变这类文本的归属。
            continue
        # 每列必须有重复对齐的证据；允许文字左对齐、数字右对齐或居中。
        for column in range(len(header)):
            boxes = [bounds(row[column]) for row in row_words[1:] if row[column]]
            if not boxes:
                bad = True
                continue
            spreads = [max(b[k] for b in boxes)-min(b[k] for b in boxes) for k in (0,2)]
            centers = [(b[0]+b[2])/2 for b in boxes]
            if min(*spreads, max(centers)-min(centers)) > height*.8:
                bad = True
        all_words = [w for row in row_words for cell in row for w in cell]
        box = bounds(all_words)
        sources = {w.get('source', 'ocr') for w in all_words}
        source = 'native_alignment' if sources == {'native'} else 'ocr_alignment' if sources == {'ocr'} else 'mixed_alignment'
        if bad:
            if numeric_rows >= 2 or multi_rows:
                unresolved.append({'bbox_pdf':box, 'source':source, 'needs_review':True,
                                   'page':page_no, 'source_text':words_text(all_words),
                                   'words':deepcopy(all_words),
                                   'reason':'文字已识别，表格结构未恢复：列归属或行关系不确定'})
                consumed.update(indices)
            continue
        # 派生的单元格边界位于文字间隙，原始词坐标另存，不宣称原件存在边框。
        col_boxes = [bounds([w for row in row_words for w in row[c]]) for c in range(len(header))]
        xs = [box[0]-1] + [(a[2]+b[0])/2 for a,b in zip(col_boxes,col_boxes[1:])] + [box[2]+1]
        row_boxes = [bounds([w for cell in row for w in cell]) for row in row_words]
        ys = [box[1]-1] + [(a[3]+b[1])/2 for a,b in zip(row_boxes,row_boxes[1:])] + [box[3]+1]
        cells = [[xs[c],ys[r],xs[c+1],ys[r+1]] for r in range(len(row_words)) for c in range(len(header))]
        table = table_entry(cells,all_words,page_no,len(existing)+len(tables)+1,source,
                            ordered_lines=ordered_lines)
        table.update(boundary_source='inferred_from_text_positions', has_visible_borders=False)
        for cell in table['cells']:
            ws = row_words[cell['row']][cell['column']]
            cell['word_bboxes_pdf'] = [w['bbox'] for w in ws]
            cell['boundary_source'] = 'inferred_from_text_positions'
        tables.append(table)
        consumed.update(indices)
    return tables, unresolved


def scan_sampling_dpi(page, images, requested, unit_scale):
    """仅避免UserUnit对足够清晰的整页单一扫描图重复插值放大。"""
    images = [i for i in images if i.get('width',0)>1 and i.get('height',0)>1]
    if unit_scale <= 1 or len(images) != 1 or page.get_drawings():
        return requested
    image = images[0]
    box = fitz.Rect(image['bbox'])
    transform = image.get('transform', ())
    if (box.is_empty or not box.contains(page.rect) or len(transform) != 6
            or transform[0] <= 0 or transform[3] <= 0 or abs(transform[1])+abs(transform[2]) > 1e-6):
        return requested
    horizontal = image['width']*72/box.width
    vertical = image['height']*72/box.height
    if min(horizontal,vertical) <= 0 or abs(horizontal-vertical) > .01*max(horizontal,vertical):
        return requested
    native = min(horizontal,vertical)
    # 原始逻辑页达不到请求密度的低清图仍走既有路径，不能普遍取消放大。
    return min(requested,native) if native*unit_scale >= requested-.01 else requested


def extract_pdf_pages(file_path, *, ocr_client=None, dpi=216, lang=None, force_ocr=False, psm=3):
    from contextlib import ExitStack
    from agent.agent_backend.config.settings import settings
    from agent.agent_backend.utils.parser.drug_supplement_pdf_parser import OCRServiceClient
    client = ocr_client or OCRServiceClient(settings.ocr_service_url, settings.ocr_timeout_seconds)
    lang = lang or settings.ocr_lang or 'chi_sim+eng'
    started = time.perf_counter()
    pages = []
    page_units = {}
    deskew_frames = {}
    requested_dpi = dpi
    with fitz.open(file_path) as doc, pdfplumber.open(file_path) as plumber, ExitStack() as normalized_docs:
        for index in range(len(doc)):
            dpi = requested_dpi
            line_word_order = {}
            start = time.perf_counter()
            row = {'page': index+1, 'chunk_id': f'page_{index+1}', 'raw_pages': [index+1],
                   'page_start': index+1, 'page_end': index+1,
                   'text': '', 'tables': [], 'lines': [], 'words': [], 'image_regions': [],
                   'image_paths': [], 'unit_type': 'pdf_page', 'coordinate_unit': 'pdf_point',
                   'coordinate_origin': 'top_left', 'dpi': dpi, 'ocr_calls': 0, 'errors': [], 'path': []}
            pages.append(row)
            try:
                page = doc[index]
                rotation = page.rotation
                row['source_rotation'] = rotation
                # 使用未旋转页面坐标；渲染同步去掉页面旋转，裁剪可复用同一坐标。
                if rotation:
                    page.set_rotation(0)
                try:
                    media, crop, actual_scale = page_geometry(page)
                    visible_crop = crop & fitz.Rect(media.x0, 0, media.x1, media.height)
                    if visible_crop != crop:
                        # 仅修改内存页，不保存原件；文字、渲染和表格共同使用有效可见框。
                        # 先按原CropBox验证尺度，再裁剪，避免把裁剪比例误当UserUnit。
                        page.set_cropbox(visible_crop)
                        _, _, actual_scale = page_geometry(page)
                    row['page_bbox'] = list(page.rect)
                    page_units[index+1] = actual_scale.a
                    pixel_scale = dpi / 216 * actual_scale.a
                except Exception as exc:
                    # 不对无可靠坐标的页面继续 OCR，也不把库的默认页面框当作成功。
                    row.update(status='failed', content_available=False, page_kind='extraction_failed')
                    row['errors'].append({'stage': 'page_geometry', 'code': 'page_geometry',
                                          'reason': f'{type(exc).__name__}: {exc}'})
                    row['elapsed_seconds'] = round(time.perf_counter()-start, 3)
                    continue
                lines = native_lines(page)
                native = '\n'.join(l['text'] for l in lines)
                row['native_text'] = native
                quality = text_quality(native)
                row['text_quality'] = quality
                image_info = page.get_image_info()
                images = [list(i['bbox']) for i in image_info]
                row['image_regions'] = images
                if not native.strip():
                    dpi = scan_sampling_dpi(page, image_info, requested_dpi, actual_scale.a)
                    if dpi != requested_dpi:
                        row.update(dpi=dpi, raster_sampling={'requested_dpi':requested_dpi, 'effective_dpi':dpi,
                            'reason':'single_scan_native_pixels_without_userunit_upsampling'})
                        pixel_scale = dpi / 216 * actual_scale.a
                words = [{'text': w[4], 'bbox': list(w[:4]), 'source': 'native'}
                         for w in page.get_text('words', flags=fitz.TEXTFLAGS_WORDS | fitz.TEXT_INHIBIT_SPACES)]
                image_coverage = max((fitz.Rect(b).get_area()/page.rect.get_area() for b in images), default=0)
                fragments = sum(len(l['text'].strip()) <= 2 for l in lines) / max(1, len(lines))
                quality.update(image_coverage=image_coverage, short_line_ratio=fragments)
                embedded_words = []
                if native.strip() and images:
                    from agent.agent_backend.utils.parser.pdf_scan_evidence import partition_text
                    words, lines, embedded_words = partition_text(page, words, lines)
                    if embedded_words:
                        row['embedded_text_candidates'] = embedded_words
                        row['path'].append('embedded_ocr_revalidation')
                        native = '\n'.join(line['text'] for line in lines)
                        quality.update(text_quality(native))
                        quality['reason'] = '扫描图上的不可见或来源不明文本层需重新核验'
                        row['visible_native_text'] = native
                        fragments = sum(len(l['text'].strip()) <= 2 for l in lines)/max(1,len(lines))
                        if not native.strip():
                            dpi = scan_sampling_dpi(page,image_info,requested_dpi,actual_scale.a)
                            row['dpi'] = dpi
                            pixel_scale = dpi/216*actual_scale.a
                if image_coverage > .65 and fragments > .6 and len(lines) >= 8:
                    quality['usable'] = False
                    quality['reason'] = '图像覆盖页面且文本层高度碎片化'
                if quality['usable'] and not force_ocr:
                    row['path'].append('native')
                    row['lines'], row['words'] = lines, words
                    regions = []
                else:
                    regions = [page.rect] if force_ocr or native.strip() or images else []
                blank_render = False
                scan_deadline = (time.monotonic() + min(120, getattr(client,'timeout_seconds',getattr(client,'timeout',120)))) if embedded_words else None
                if not quality['usable'] and regions:
                    preview = page.get_pixmap(matrix=fitz.Matrix(.5, .5), colorspace=fitz.csGRAY, alpha=False)
                    ink = sum(v < 240 for v in preview.samples) / max(1, len(preview.samples))
                    blank_render = ink < .0001
                    row['ink_ratio'] = ink
                    if blank_render:
                        regions = []
                # 仅无原生文字的整页扫描有资格校正；混合页不能栅格化覆盖已有文字。
                if not native.strip() and image_coverage > .65 and len(regions) == 1 and regions[0] == page.rect:
                    import numpy as np
                    from agent.agent_backend.utils.parser.pdf_deskew import deskew_image
                    scan_deadline = scan_deadline or time.monotonic() + min(120, getattr(client, 'timeout_seconds', getattr(client, 'timeout', 120)))
                    scale = dpi/72
                    source_pix = page.get_pixmap(matrix=fitz.Matrix(scale,scale),alpha=False)
                    image = np.frombuffer(source_pix.samples,dtype=np.uint8).reshape(source_pix.height,source_pix.width,source_pix.n)
                    candidate = deskew_image(image,scan_deadline)
                    if candidate is not None:
                        import cv2
                        corrected, transform = candidate
                        original_box = list(page.rect)
                        normalized = normalized_docs.enter_context(fitz.open())
                        # 校正仅旋转像素，不改变原页逻辑单位；否则网格与裁剪
                        # 在UserUnit页面上会按不同尺度工作，丢失可靠单元格。
                        unit = actual_scale.a
                        page = normalized.new_page(width=corrected.shape[1]/scale/unit,
                                                   height=corrected.shape[0]/scale/unit)
                        encoded = cv2.imencode('.png',cv2.cvtColor(corrected,cv2.COLOR_RGB2BGR))[1].tobytes()
                        page.insert_image(page.rect,stream=encoded)
                        normalized.xref_set_key(page.xref, 'UserUnit', str(unit))
                        page = normalized.reload_page(page)
                        row['deskew'] = transform
                        # 旧层证据也进入校正坐标，最终与新词一起逆映射回原页。
                        matrix = transform['pixel_matrix']
                        for word in embedded_words:
                            x0,y0,x1,y1 = word['bbox']
                            points = [(x*scale,y*scale) for x,y in ((x0,y0),(x1,y0),(x1,y1),(x0,y1))]
                            points = [((matrix[0][0]*x+matrix[0][1]*y+matrix[0][2])/scale,
                                       (matrix[1][0]*x+matrix[1][1]*y+matrix[1][2])/scale) for x,y in points]
                            word['bbox'] = [min(x for x,y in points),min(y for x,y in points),
                                            max(x for x,y in points),max(y for x,y in points)]
                        row['page_bbox'] = list(page.rect)
                        images = [list(page.rect)]
                        row['image_regions'] = images
                        regions = [page.rect]
                        pixel_scale = dpi/216*unit
                        deskew_frames[index+1] = (transform['pixel_inverse'],scale,original_box)
                # 原生线框表格使用真正的单元格边界，保留合并关系。
                if quality['usable'] and not force_ocr and not embedded_words:
                    try:
                        source = plumber.pages[index]
                        transform = plumber_to_page_matrix(source, page, rotation)
                        native_tables = []
                        for table in source.find_tables():
                            cells = [fitz.Rect(c) * transform & page.rect for c in table.cells]
                            cells = [list(c) for c in cells if not c.is_empty]
                            if cells:
                                native_tables.append({'cells': cells,
                                    'bbox': list(fitz.Rect(table.bbox) * transform & page.rect)})
                    except Exception as exc:
                        native_tables = []
                        row['errors'].append({'stage': 'tables', 'reason': f'{type(exc).__name__}: {exc}'})
                    suspect_geometry = any(
                        c[2]-c[0] < 8 or c[3]-c[1] < 4
                        for t in native_tables for c in t['cells'])
                    incomplete_regions = []
                    if suspect_geometry or native_open_tail_candidate(page, native_tables, actual_scale.a):
                        # 某些 PDF 的文字背景矩形会被误当作表格线；用可见线框校验，仍使用原生文字。
                        pix = page.get_pixmap(matrix=fitz.Matrix(dpi/72, dpi/72), alpha=False)
                        groups = raster_cells(pix, (0, 0), dpi/72, pixel_scale,
                                              incomplete_regions=incomplete_regions)
                        for cells in groups if suspect_geometry else []:
                            box = [min(c[0] for c in cells), min(c[1] for c in cells), max(c[2] for c in cells), max(c[3] for c in cells)]
                            row['tables'].append(table_entry(cells, [w for w in words if inside(w['bbox'], box)], index+1, len(row['tables'])+1, 'native_visible_grid'))
                        if not groups and suspect_geometry:
                            row['errors'].append({'stage': 'table_geometry', 'code': 'table_structure_unresolved', 'reason': '可见表格线框未能可靠恢复，已保留原文和区域'})
                            row['unresolved_tables'] = [{'bbox_pdf': t['bbox'], 'needs_review': True} for t in native_tables]
                        if suspect_geometry:
                            native_tables = []
                    for table in native_tables:
                        cells, box = table['cells'], table['bbox']
                        row['tables'].append(table_entry(cells, [w for w in words if inside(w['bbox'], box)], index+1, len(row['tables'])+1, 'native'))
                    record_incomplete_tables(row, incomplete_regions, tolerance=2*pixel_scale/(dpi/72))
                if embedded_words:
                    # 隐藏文字不决定表格几何；旧PDF线段仅存候选，采用可见栅格。
                    try:
                        native_source = plumber.pages[index]
                        transform = plumber_to_page_matrix(native_source,doc[index],rotation)
                        row['embedded_table_candidates'] = [
                            {'original_bbox_pdf':list(fitz.Rect(t.bbox)*transform),
                             'original_cells_pdf':[list(fitz.Rect(c)*transform) for c in t.cells]}
                            for t in native_source.find_tables()]
                    except Exception as exc:
                        row['embedded_table_evidence_error'] = type(exc).__name__
                    pix = page.get_pixmap(matrix=fitz.Matrix(dpi/72,dpi/72),alpha=False)
                    incomplete_regions=[]
                    for cells in raster_cells(pix,(0,0),dpi/72,pixel_scale,incomplete_regions=incomplete_regions):
                        row['tables'].append(table_entry(cells,row['words'],index+1,len(row['tables'])+1,'visible_scan_grid'))
                    record_incomplete_tables(row,incomplete_regions,tolerance=2*pixel_scale/(dpi/72))
                if quality['usable'] and not force_ocr:
                    for box in sorted(images, key=lambda b: -fitz.Rect(b).get_area()):
                        rect = fitz.Rect(box) & page.rect
                        if rect.is_empty or any(rect in existing for existing in regions):
                            continue
                        try:
                            missing, pix = uncovered_image(page, rect, words, dpi)
                        except Exception as exc:
                            row['errors'].append({'stage': 'image_coverage', 'bbox_pdf': list(rect),
                                                  'reason': f'图像内容覆盖无法确认：{type(exc).__name__}'})
                            regions.append(rect)
                            continue
                        row.setdefault('image_content_coverage', []).append({'bbox_pdf': list(rect),
                            'uncovered_components': missing, 'verified': not bool(missing)})
                        if missing:
                            regions.append(rect)
                        else:
                            # 完整文字层覆盖扫描表时直接用真实栅格与原生文字恢复表。
                            for cells in raster_cells(pix, (pix.x/(dpi/72), pix.y/(dpi/72)), dpi/72, pixel_scale):
                                box = [min(c[0] for c in cells), min(c[1] for c in cells), max(c[2] for c in cells), max(c[3] for c in cells)]
                                if not any(inside(box, t['bbox_pdf']) for t in row['tables']):
                                    row['tables'].append(table_entry(cells, [w for w in words if inside(w['bbox'], box)], index+1, len(row['tables'])+1, 'native_visible_grid'))
                remaining_scan_recovery = 4
                for rect in regions:
                    row['ocr_calls'] += 1
                    row['path'].append('page_ocr' if rect == page.rect else 'region_ocr')
                    try:
                        region_psm = 7 if rect != page.rect and rect.height < 40 and rect.width > 2*rect.height else psm
                        deadline = scan_deadline if scan_deadline is not None else time.monotonic() + min(120, getattr(client, 'timeout_seconds', getattr(client, 'timeout', 120)))
                        budget_metadata = {'remaining_recovery_regions':remaining_scan_recovery} if embedded_words else {}
                        scan_options = {'raw_scan':True} if embedded_words else {}
                        ow, ol, pix = ocr_region(page, rect, client, lang, dpi, region_psm, row['words'], deadline=deadline, metadata=budget_metadata, **scan_options)
                        primary_words = list(ow)
                        if embedded_words:
                            from agent.agent_backend.utils.parser.pdf_scan_evidence import embedded_comparisons
                            old_region = [w for w in embedded_words if inside(w['bbox'],rect)]
                            evidence = embedded_comparisons(old_region,row['words']+primary_words,
                                cells=budget_metadata.get('recovery_cells',[]),current_lines=ol)
                            budget_metadata['embedded_evidence'] = evidence
                            row.setdefault('embedded_ocr_evidence',[]).extend(evidence)
                        attempts = []
                        budget_metadata['primary_lines']=ol
                        if ow or budget_metadata.get('execution_budget_enforced'):
                            quality_lines = quality_recovery_lines(primary_words,ol,budget_metadata.get('recovery_cells',[]))
                            for cell_quality in quality_lines:
                                if cell_quality.get('quality_scope') == 'table_cell':
                                    row['errors'].append({'stage':'ocr_quality','code':'ocr_quality',
                                        'bbox_pdf':cell_quality['source_line_bbox'],
                                        'reason':'单元格识别质量不足，请核对原格文字；全页平均质量不能替代此项检查',
                                        'quality_evidence':{key:cell_quality[key] for key in
                                            ('quality_scope','mean_confidence','low_confidence_word_count','word_count')}})
                            recovered, recovered_lines, attempts = recover_ocr_coverage(page, rect, client, lang, dpi,
                                row['words'] + ow, deadline, budget_metadata, quality_lines=quality_lines)
                            ow.extend(recovered)
                            ol.extend(recovered_lines)
                            recovery_calls = sum(a['status'] != 'not_attempted'
                                                    and 'reused_attempt_index' not in a for a in attempts)
                            row['ocr_calls'] += recovery_calls
                            remaining_scan_recovery -= recovery_calls
                            if attempts:
                                row.setdefault('ocr_recovery', []).extend(attempts)
                            conflicts = [a for a in attempts if a.get('has_conflicting_candidates') or a.get('reason_code') in
                                         ('recovery_conflict','recovery_reflow_review_required')]
                            for trigger in ('ocr_coverage','ocr_quality'):
                                relevant = [a for a in conflicts if a.get('trigger','ocr_coverage') == trigger]
                                if relevant:
                                    row['errors'].append({'stage': trigger, 'code': trigger, 'bbox_pdf': list(rect),
                                        'reason': '局部复核候选存在冲突或重排来源待确认，已保留双方，请核对原区域',
                                        'recovery_conflicts': relevant})
                        if embedded_words:
                            from agent.agent_backend.utils.parser.pdf_scan_evidence import finalize_embedded_evidence
                            row['errors'].extend(finalize_embedded_evidence(evidence,attempts,row['words']+ow))
                        unique = [w for w in ow if not any(same_word(w, old) for old in row['words'])]
                        row['words'].extend(unique)
                        for ti,table in enumerate(row['tables']):
                            if table['source']=='visible_scan_grid':
                                cells=[c['bbox_pdf'] for c in table['cells']]
                                box=table['bbox_pdf']
                                row['tables'][ti]=table_entry(cells,[w for w in row['words'] if inside(w['bbox'],box)],index+1,ti+1,'visible_scan_grid')
                        uncertain_numbers = [w for w in unique if w.get('numeric_verification', {}).get('status') == 'numeric_uncertain']
                        if uncertain_numbers:
                            row['errors'].append({'stage': 'ocr_numeric', 'code': 'numeric_uncertain',
                                'bbox_pdf': list(rect), 'reason': '数值核验存在分歧或尚未完成，请核对原页；这些值不能直接用于自动判定',
                                'numeric_verification': [w['numeric_verification'] for w in uncertain_numbers]})
                        for line in ol:
                            retained = retained_ocr_line(line, unique)
                            if retained is not None:
                                row['lines'].append(retained)
                                if '_ordered_words' in line:
                                    unique_ids = {id(word) for word in unique}
                                    line_word_order[id(retained)] = [word for word in line['_ordered_words']
                                                                    if id(word) in unique_ids]
                        # 补回高置信度词不能稀释主识别已有的质量警告。
                        quality_words = primary_words or ow
                        low_confidence, mean_confidence, needs_quality_review = ocr_quality_evidence(quality_words)
                        row.setdefault('ocr_quality', []).append({'bbox_pdf': list(rect), 'mean_confidence': mean_confidence,
                            'low_confidence_word_count': len(low_confidence), 'word_count': len(quality_words)})
                        if needs_quality_review:
                            row['errors'].append({'stage': 'ocr_quality', 'code': 'ocr_quality', 'bbox_pdf': list(rect),
                                'reason': f'OCR 低置信度词 {len(low_confidence)}/{len(quality_words)}，内容已保留，需核对原页',
                                'low_confidence_words': low_confidence})
                        if not ow:
                            row['errors'].append({'stage': 'ocr', 'code': 'ocr_empty', 'bbox_pdf': list(rect), 'reason': 'OCR 未返回有效文字；图像内容尚未识别'})
                        incomplete_regions = []
                        cell_groups = raster_cells(pix, (pix.x/(dpi/72), pix.y/(dpi/72)), dpi/72, pixel_scale,
                                                   incomplete_regions=incomplete_regions)
                        for cells in cell_groups:
                            bbox = [min(c[0] for c in cells), min(c[1] for c in cells), max(c[2] for c in cells), max(c[3] for c in cells)]
                            matching = [t for t in row['tables'] if inside(bbox, t['bbox_pdf'])]
                            if matching and all(not t['needs_review'] for t in matching):
                                continue
                            row['tables'] = [t for t in row['tables'] if t not in matching]
                            row['tables'].append(table_entry(cells, [w for w in row['words'] if inside(w['bbox'], bbox)], index+1, len(row['tables'])+1, 'ocr_grid',
                                                             ordered_lines=list(line_word_order.values())))
                        record_incomplete_tables(row, incomplete_regions, tolerance=2*pixel_scale/(dpi/72))
                        row.setdefault('ocr_regions', []).append({'bbox_pdf': list(rect), 'text': words_text(ow),
                            'preprocessing':budget_metadata.get('preprocessing','unknown'),
                            'render_dpi':budget_metadata.get('render_dpi',dpi),
                            'segmentation_psm':budget_metadata.get('segmentation_psm',region_psm),
                            'table_structure': 'grid' if cell_groups else 'not_applicable', 'needs_review': bool(uncertain_numbers)})
                        if budget_metadata.get('coverage_check_failed'):
                            row['errors'].append({'stage': 'ocr_coverage', 'code': 'ocr_coverage', 'bbox_pdf': list(rect),
                                'reason': '覆盖检查未完成，已保留识别文字，请核对原区域'})
                        elif ow:
                            missing = budget_metadata.get('initial_uncovered', [])
                            if attempts:
                                missing, _ = uncovered_image(page, rect, row['words'], dpi)
                            if missing:
                                row['errors'].append({'stage': 'ocr_coverage', 'code': 'ocr_coverage', 'bbox_pdf': list(rect),
                                    'uncovered_components': missing, 'reason': '识别后仍存在未覆盖的可见内容，已保留可用文字，请核对原区域'})
                    except Exception as exc:
                        from agent.agent_backend.services.filing_parse_outcome import ocr_exception_details, MESSAGES
                        detail = ocr_exception_details(exc)
                        row['errors'].append({'stage': 'ocr', 'bbox_pdf': list(rect), **detail, 'reason': MESSAGES[detail['code']]})
                if embedded_words:
                    # 主识别异常或旧框在裁剪外时，旧候选仍必须有明确去向。
                    from agent.agent_backend.utils.parser.pdf_scan_evidence import embedded_comparisons, finalize_embedded_evidence
                    represented = {(w['text'],tuple(w['bbox']))
                        for entry in row.get('embedded_ocr_evidence',[]) for w in entry['embedded_words']}
                    pending = [w for w in embedded_words if (w['text'],tuple(w['bbox'])) not in represented]
                    if pending:
                        evidence = embedded_comparisons(pending,row['words'],
                            cells=[c['bbox_pdf'] for t in row['tables'] for c in t['cells']],current_lines=row['lines'])
                        row.setdefault('embedded_ocr_evidence',[]).extend(evidence)
                        row['errors'].extend(finalize_embedded_evidence(evidence,[],row['words']))
                if row['words']:
                    recovered, unresolved = borderless_tables(row['words'], row['tables'], index+1,
                                                             ordered_lines=list(line_word_order.values()))
                    row['tables'].extend(recovered)
                    for candidate in unresolved:
                        row.setdefault('unresolved_tables', []).append(candidate)
                        row['errors'].append({'stage':'table_structure', 'code':'table_structure_unresolved',
                                              'bbox_pdf':candidate['bbox_pdf'], 'reason':candidate['reason']})
                    for region in row.get('ocr_regions', []):
                        if any(inside(t['bbox_pdf'], region['bbox_pdf']) for t in unresolved):
                            region.update(table_structure='unresolved', needs_review=True)
                        elif any(inside(t['bbox_pdf'], region['bbox_pdf']) for t in recovered):
                            region.update(table_structure='inferred_from_text_positions')
                for table in row['tables']:
                    if table.get('unassigned_words'):
                        row['errors'].append({'stage': 'table_structure', 'code': 'table_structure_unresolved',
                            'bbox_pdf': table['bbox_pdf'], 'table_id': table['id'],
                            'reason': '表格文字无法唯一归属单元格，原文已保留，请核对行列',
                            'unassigned_words': deepcopy(table['unassigned_words'])})
                row['raw_text'] = '\n'.join(l['text'] for l in row['lines'])
                # 正文内只渲染一次表格；结构化表仍保留在 tables 中供下游读取。
                row['text'], assembly_errors = assemble_page_text(row['lines'], row['words'], row['tables'],
                                                                 line_word_order=line_word_order)
                row['errors'].extend(assembly_errors)
                visual = bool(images or page.get_drawings()) and not blank_render
                row['page_kind'] = 'content' if row['raw_text'].strip() else ('visual_unresolved' if visual else 'blank')
                row['visual_content_reviewed'] = False
                row['content_available'] = bool(row['raw_text'].strip())
                row['status'] = ('partial' if row['errors'] else 'success') if row['content_available'] else ('blank' if not visual and not row['errors'] else 'failed')
            except Exception as exc:
                row.update(status='failed', content_available=False, page_kind='extraction_failed')
                row['errors'].append({'stage': 'page', 'reason': f'{type(exc).__name__}: {exc}'})
            row['elapsed_seconds'] = round(time.perf_counter()-start, 3)
    available = [p['page'] for p in pages if p['content_available']]
    # 跨页只关联有明确续表文字、同表题和相同表头/列边界的表；各页原表不丢失。
    previous = []
    for page in pages:
        unit = page_units.get(page['page'], 1)
        for table in page['tables']:
            above = [l for l in page['lines'] if 0 <= table['bbox_pdf'][1]-l['bbox'][3] < 70 * unit]
            table['caption'] = above[-1]['text'] if above else ''
            table['notes'] = [l for l in page['lines'] if 0 <= l['bbox'][1]-table['bbox_pdf'][3] < 45 * unit]
            caption = table['caption']
            table['header_rows'] = header_rows(table)
            if re.search(r'续表|续|continued', caption, re.I):
                candidates = continuation_candidates(table, previous)
                if len(candidates) == 1:
                    table.update(continuation='linked', continuation_of=candidates[0]['id'], repeated_header_rows=table['header_rows'])
                if table['continuation'] != 'linked':
                    reason = '续表存在多个匹配来源，保留独立原表' if len(candidates) > 1 else '续表标题、表头或列结构证据不足，保留独立原表'
                    table.update(needs_review=True, continuation_reason=reason,
                                 continuation_candidates=[p['id'] for p in candidates])
                    table.setdefault('review_reasons', []).append('continuation_unresolved')
                    page['errors'].append({'stage': 'table', 'code': 'table_structure_unresolved',
                                           'bbox_pdf': list(table['bbox_pdf']), 'reason': reason})
                    page['status'] = 'partial' if page['content_available'] else 'failed'
        previous = page['tables']
    for row in pages:
        # 所有核验和采用已结束。先共享重复几何，再统一恢复原页坐标，
        # 防止诊断及文档级汇总反复展开同一裁剪的大量连通域证据。
        from agent.agent_backend.utils.parser.pdf_scan_evidence import compact_recovery_geometry
        compact_recovery_geometry(row.get('ocr_recovery', []))
        if row['page'] in deskew_frames:
            from agent.agent_backend.utils.parser.pdf_deskew import restore_coordinates
            restore_coordinates(row,*deskew_frames[row['page']])
    failed = [p['page'] for p in pages if p['status'] in ('partial', 'failed')]
    diagnostics = {'parser': 'shared_pdf_pages', 'status': 'failed' if not available else ('partial' if failed else 'success'),
                   'successful_pages': [p['page'] for p in pages if p['status'] == 'success'],
                   'available_pages': available, 'failed_pages': failed,
                   'blank_pages': [p['page'] for p in pages if p['status'] == 'blank'],
                   'errors': [{**e, 'page':p['page'], 'message':e.get('message') or e.get('reason',''),
                               'coordinate_unit':'pdf_point'} for p in pages for e in p['errors']],
                   'ocr_calls': sum(p['ocr_calls'] for p in pages), 'elapsed_seconds': round(time.perf_counter()-started, 3)}
    for row in pages:
        row['parse_diagnostics'] = diagnostics
        row['tables_in_text'] = True
    return pages
