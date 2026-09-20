"""扫描线框的小角度校正；保留逆变换，不推断缺失文字。"""
import math


def deskew_image(image, deadline):
    import time
    import cv2
    import numpy as np
    if time.monotonic() >= deadline:
        return None
    height, width = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    # 亚像素端点避免小倾角被整数端点量化成多个相互矛盾的方向。
    lines = cv2.createLineSegmentDetector().detect(gray)[0]
    families = [[], []]
    for x0,y0,x1,y1 in ([] if lines is None else lines[:,0]):
        if np.hypot(x1-x0,y1-y0) < min(gray.shape)*.08:
            continue
        angle = (float(np.degrees(np.arctan2(y1-y0,x1-x0)))+90)%180-90
        if abs(angle) <= 3:
            families[0].append(angle)
        elif abs(angle) >= 87:
            families[1].append(angle-90 if angle>0 else angle+90)
    if min(map(len, families)) < 3:
        return None
    medians = [float(np.median(v)) for v in families]
    if abs(medians[0]-medians[1])>.2 or any(np.percentile(v,75)-np.percentile(v,25)>.2 for v in families):
        return None
    angle = sum(medians)/2
    if abs(angle) < .15:
        return None
    if time.monotonic() >= deadline:
        return None
    matrix = cv2.getRotationMatrix2D((width/2,height/2),angle,1)
    corners = np.array([[0,0,1],[width,0,1],[width,height,1],[0,height,1]],dtype=float) @ matrix.T
    lower, upper = np.floor(corners.min(axis=0)), np.ceil(corners.max(axis=0))
    matrix[:,2] -= lower
    corrected = cv2.warpAffine(image,matrix,tuple((upper-lower).astype(int)),
                               flags=cv2.INTER_CUBIC,borderValue=(255,255,255))
    return corrected, {'angle':angle, 'pixel_matrix':matrix.tolist(),
                       'pixel_inverse':cv2.invertAffineTransform(matrix).tolist()}


def source_quad(box, inverse, scale):
    """校正页PDF点→原页PDF点；四角用于定位，外接框不能替代旋转多边形证据。"""
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError('invalid deskew scale')
    x0,y0,x1,y1 = box
    return [[(inverse[0][0]*x*scale+inverse[0][1]*y*scale+inverse[0][2])/scale,
             (inverse[1][0]*x*scale+inverse[1][1]*y*scale+inverse[1][2])/scale]
            for x,y in ((x0,y0),(x1,y0),(x1,y1),(x0,y1))]


def restore_coordinates(row, inverse, scale, original_box):
    seen = set()
    def mapped(box):
        quad = source_quad(box,inverse,scale)
        return [min(p[0] for p in quad),min(p[1] for p in quad),
                max(p[0] for p in quad),max(p[1] for p in quad)], quad
    def visit(value):
        if isinstance(value,(list,dict)):
            if id(value) in seen:
                return
            seen.add(id(value))
        if isinstance(value,list):
            for item in value:
                visit(item)
        elif isinstance(value,dict):
            for key,item in list(value.items()):
                if key in ('bbox','bbox_pdf','uncovered_bbox_pdf','unclosed_bbox_pdf','source_bbox_pdf','requested_bbox_pdf') and isinstance(item,list) and len(item)==4:
                    value[key], quad = mapped(item)
                    value[key+'_deskewed'] = item
                    value[key+'_source_quad'] = quad
                elif key in ('uncovered_components','image_regions','component_bboxes','comparison_cells') and isinstance(item,list):
                    value[key] = [mapped(box)[0] for box in item]
                else:
                    visit(item)
    visit(row)
    row['deskewed_page_bbox'] = row['page_bbox']
    row['page_bbox'] = original_box
