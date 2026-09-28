"""像素短符号判别；无OCR文字输入。直线形倾斜数字与斜杠无法可靠区分时拒判。"""
import cv2
import numpy as np
from segmentation import sparse_ink_box


def classify(image):
    proposal=sparse_ink_box(image)
    result=dict(label='unknown',proposal=proposal,features=[])
    if proposal is None:return result
    x0,y0,x1,y1=proposal
    gray=cv2.cvtColor(image[y0:y1,x0:x1],cv2.COLOR_BGR2GRAY)
    for threshold in (140,170,200):
        yy,xx=np.where(gray<threshold)
        if len(xx)<8:continue
        h=int(np.ptp(yy))+1;w=int(np.ptp(xx))+1
        if h<9:continue
        slope,intercept=np.polyfit(yy,xx,1)
        residual=float(np.std(xx-(slope*yy+intercept))/h)
        rows=np.unique(yy);coverage=len(rows)/h
        # 中心轨迹在顶部/底部不能出现数字1的钩或底座。
        centers=np.array([np.mean(xx[yy==y]) for y in rows])
        deviations=centers-(slope*rows+intercept)
        bend=float(np.max(abs(deviations))/h)
        widths=np.array([np.ptp(xx[yy==y])+1 for y in rows])
        width_ratio=float(np.max(widths)/max(1,np.median(widths)))
        valid=bool(-.9<slope<-.28 and residual<.065 and bend<.085 and coverage>.84 and width_ratio<2.6 and h/w>1.3)
        result['features'].append(dict(threshold=threshold,slope=float(slope),residual=residual,bend=bend,
            coverage=coverage,width_ratio=width_ratio,height=h,width=w,slash_shape=valid))
    if len(result['features'])>=2 and all(f['slash_shape'] for f in result['features']):
        result['label']='slash_shape'
    result['limitation']='shape evidence only; an indistinguishable single-stroke tilted 1 remains semantically ambiguous'
    return result
