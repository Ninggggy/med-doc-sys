"""仅按输入像素处理局部正文，不读取参考或匹配识别字符串。"""
import cv2
import numpy as np


def black_ink(image, threshold=165):
    # 红章R通道亮，黑字三通道均暗；只生成局部独立候选，不覆盖原图。
    red=image[:,:,2]
    gray=np.where(red<threshold,red,255).astype('uint8')
    return cv2.cvtColor(gray,cv2.COLOR_GRAY2BGR)


def red_fraction(image):
    b,g,r=cv2.split(image.astype('int16'))
    return float(((r-g>35)&(r-b>35)).mean())


def split_line(image, target_ratio=13):
    """从长行中间的字间白缝分段，保持顺序；不按文本长度定位。"""
    h,w=image.shape[:2]
    if w/h<18:return [(0,w)]
    ink=np.min(image,axis=2)<160
    count=max(2,round(w/(h*target_ratio)))
    boundaries=[0]
    for i in range(1,count):
        target=round(w*i/count);radius=max(2,round(h*.8))
        lo=max(boundaries[-1]+h,target-radius);hi=min(w-h,target+radius)
        if hi<=lo:continue
        density=ink[:,lo:hi].sum(axis=0)
        options=np.flatnonzero(density==density.min())+lo
        cut=int(options[np.argmin(abs(options-target))])
        if density.min()<=max(1,h*.04):boundaries.append(cut)
    return list(zip(boundaries,boundaries[1:]+[w]))
