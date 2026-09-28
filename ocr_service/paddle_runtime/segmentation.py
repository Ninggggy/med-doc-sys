"""Image-only sparse ink proposals. Never supplies a character or semantic answer."""
import cv2
import numpy as np


def sparse_ink_box(image):
    gray=cv2.cvtColor(image,cv2.COLOR_BGR2GRAY)
    # Blurry low-resolution ink can be fragmented. Join nearby fragments in the
    # proposal mask only; the recognizer always receives unchanged RGB pixels.
    mask=cv2.dilate((gray<180).astype('uint8'),np.ones((5,5),np.uint8))
    n,labels,stats,centroids=cv2.connectedComponentsWithStats(mask,8)
    h,w=gray.shape;candidates=[]
    for x,y,ww,hh,area in stats[1:]:
        # Edge-touching lines remain in the original image, not erased from OCR.
        if x<=1 or y<=1 or x+ww>=w-1 or y+hh>=h-1:continue
        if area<4 or hh<3 or ww<2:continue
        if max(ww/hh,hh/ww)>10:continue
        if ww>w*.4 or hh>h*.85 or area>w*h*.05:continue
        candidates.append([int(x),int(y),int(x+ww),int(y+hh)])
    if len(candidates)!=1:return None
    x0,y0,x1,y1=candidates[0];pad=max(3,round((y1-y0)*.3))
    return [max(0,x0-pad),max(0,y0-pad),min(w,x1+pad),min(h,y1+pad)]


def detection_line_boxes(spans):
    """Detector polygons, not projected ink bands, define recognition lines."""
    return [dict(span_id=s.get('span_id'),polygon=s['polygon_input_px'],text_raw=s['text_raw'])
            for s in spans if s.get('granularity')=='line' and s.get('polygon_input_px')]
