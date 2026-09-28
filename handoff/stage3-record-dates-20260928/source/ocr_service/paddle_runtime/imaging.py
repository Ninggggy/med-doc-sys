import math
import sys
import time
from pathlib import Path
import pymupdf as fitz
import numpy as np
from common import dump


def render(page, path, scale=3, box=None, annots=True):
    clip=fitz.Rect(box) if box else page.rect
    pix=page.get_pixmap(matrix=fitz.Matrix(scale,scale),clip=clip,alpha=False,annots=annots)
    if pix.width*pix.height>16000000:raise ValueError('crop exceeds 16M pixels; tile required')
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    pix.save(str(path))
    # MuPDF pixmap integer origin accounts for floor/ceil of fractional clips.
    inv=[[1/scale,0,pix.x/scale],[0,1/scale,pix.y/scale],[0,0,1]]
    return dict(image=str(Path(path).resolve()),size=[pix.width,pix.height],
                input_to_page=inv,coordinate_space='rotated_crop_local_PDF_points',
                clip=list(clip),scale=scale,origin_px=[pix.x,pix.y])


def geometry(page):
    return dict(mediabox=list(page.mediabox),cropbox=list(page.cropbox),rect=list(page.rect),
                rotation=page.rotation,userunit=page.parent.xref_get_key(page.xref,'UserUnit'),
                transformation=list(page.transformation_matrix),rotation_matrix=list(page.rotation_matrix),
                images=page.get_image_info())


def original_page_raster(page, path, reference=None):
    """提取唯一整页扫描底图；叠加对象范围必须由局部使用者排除。"""
    images=page.get_images(full=True)
    if len(images)!=1 or images[0][1]:return None
    # 隐藏OCR层不是原文依据；有可见PDF文字时必须继续使用完整页面渲染。
    xref=images[0][0];placements=page.get_image_rects(xref,transform=True)
    if len(placements)!=1:return None
    rect,transform=placements[0]
    rotated=rect*page.rotation_matrix
    if any(abs(a-b)>1 for a,b in zip(rotated,page.rect)):return None
    pix=fitz.Pixmap(page.parent,xref)
    if pix.alpha or pix.width*pix.height>16000000:return None
    if pix.n!=3:pix=fitz.Pixmap(fitz.csRGB,pix)
    matrix=fitz.Matrix(1/pix.width,1/pix.height)*transform*page.rotation_matrix
    overlays=[];base_seen=False
    for kind,bbox in page.get_bboxlog():
        if kind=='ignore-text':continue
        if kind=='fill-image' and not base_seen and all(abs(a-b)<1 for a,b in zip(bbox,rect)):
            base_seen=True;continue
        overlays.append(list(fitz.Rect(bbox)*page.rotation_matrix))
    for annot in page.annots() or []:
        overlays.append(list(annot.rect*page.rotation_matrix))
    if not base_seen:return None
    Path(path).parent.mkdir(parents=True,exist_ok=True);pix.save(str(path))
    result=dict(image=str(Path(path).resolve()),size=[pix.width,pix.height],
        input_to_page=[[matrix.a,matrix.c,matrix.e],[matrix.b,matrix.d,matrix.f],[0,0,1]],
        coordinate_space='rotated_crop_local_PDF_points',origin='single_visible_page_raster',
        hidden_text_used=False,excluded_overlay_bboxes=overlays)
    if reference and page.rotation==0 and matrix.b==0 and matrix.c==0:
        # 用同一MuPDF栅格化比较底图与整页；不使用不同插值器制造颜色差异。
        with fitz.open() as base:
            import re
            # 保留PDF原有数值精度，避免把float32反算的变换重新写入后出现伪差异。
            number=rb'[-+]?(?:\d*\.\d+|\d+\.?\d*)'
            prefix=re.match(rb'\s*q\s+(?:'+number+rb'\s+){6}cm\s+/[^\s/]+\s+Do\s+Q\b',page.read_contents())
            if prefix:
                base.insert_pdf(page.parent,from_page=page.number,to_page=page.number)
                bp=base[0];xref=base.get_new_xref();base.update_object(xref,'<<>>')
                base.update_stream(xref,prefix.group());bp.set_contents(xref)
                # 非标准Watermark批注不一定由annots()枚举；仅在内存副本去掉批注引用。
                base.xref_set_key(bp.xref,'Annots','[]')
                bp=base.reload_page(bp)
                operations=bp.get_bboxlog()
                if len(operations)!=1 or operations[0][0]!='fill-image':return result
            else:
                bp=base.new_page(width=page.rect.width,height=page.rect.height)
                bp.insert_image(rect,pixmap=pix,keep_proportion=False)
            result['rendered_reference']=render(bp,Path(path).with_name(Path(path).stem+'-reference.png'),reference['scale'],annots=False)
    return result


def visible_tables(page,repo,scale=1.5):
    """Read-only reuse of current raster_cells, no OCR or expected dimensions."""
    sys.path.insert(0,str(Path(repo).parent))
    # 使用真实包与依赖。注册仅发生在隔离进程内，不用空包绕过导入。
    from agent.agent_backend.utils.parser.pdf_page_extractor import raster_cells
    pix=page.get_pixmap(matrix=fitz.Matrix(scale,scale),alpha=False)
    incomplete=[]
    groups=raster_cells(pix,(0,0),scale,pixel_scale=scale/3,incomplete_regions=incomplete)
    tables=[]
    for boxes in groups:
        xs=sorted(set(x for b in boxes for x in (b[0],b[2])))
        ys=sorted(set(y for b in boxes for y in (b[1],b[3])))
        cells=[dict(row=ys.index(b[1]),column=xs.index(b[0]),rowspan=ys.index(b[3])-ys.index(b[1]),
                    colspan=xs.index(b[2])-xs.index(b[0]),bbox_pdf=b) for b in boxes]
        tables.append(dict(cells=sorted(cells,key=lambda c:(c['row'],c['column'])),
                           bbox_pdf=[xs[0],ys[0],xs[-1],ys[-1]],geometry_origin='automatic_visible_grid'))
    return tables,incomplete


def line_crops(path, output):
    """Image projection diagnostic. All occupied row bands retained, no lexical filter."""
    import cv2
    a=cv2.imread(str(path)); gray=cv2.cvtColor(a,cv2.COLOR_BGR2GRAY)
    mask=(gray<160).sum(axis=1)>max(2,a.shape[1]*.003)
    inds=np.flatnonzero(mask); groups=[]
    for y in inds:
        if not groups or y-groups[-1][-1]>2:groups.append([])
        groups[-1].append(int(y))
    out=[]
    for i,g in enumerate(groups):
        y0=max(0,g[0]-1);y1=min(a.shape[0],g[-1]+2)
        dest=Path(output)/f'line-{i}.png';dest.parent.mkdir(parents=True,exist_ok=True)
        cv2.imwrite(str(dest),a[y0:y1])
        out.append(dict(image=str(dest.resolve()),bbox_input=[0,y0,a.shape[1],y1]))
    return out
