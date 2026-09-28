"""重复浅色背景文字的独立归属；不使用企业名、页码或水印词典。"""
from copy import deepcopy
import re
from statistics import median
from regional_adoption import input_geometry, input_box


def separate_background(lines, result):
    geometry=input_geometry(result)
    if not geometry:return lines,[]
    try:
        from PIL import Image
        with Image.open(result['input']['image']) as image:gray=image.convert('L')
    except (OSError,KeyError):return lines,[]
    _,inverse=geometry
    def pale(box):
        q=input_box(box,inverse);q=tuple(int(round(v)) for v in q)
        if q[2]<=q[0] or q[3]<=q[1]:return False
        hist=gray.crop(q).histogram();count=sum(hist)
        return count>0 and sum(hist[:150])/count<.002 and sum(hist[150:225])/count>.08
    candidates=[l for l in lines if re.fullmatch('[A-Z]{3,12}',l['text'].strip()) and pale(l['bbox'])]
    motifs=[]
    for l in candidates:
        text=l['text'].strip();peers=[p for p in candidates if p['text'].strip() in text or text in p['text'].strip()]
        if len(peers)<3:continue
        h=median(p['bbox'][3]-p['bbox'][1] for p in peers)
        if max(p['bbox'][0] for p in peers)-min(p['bbox'][0] for p in peers)<4*h:continue
        if max(p['bbox'][1] for p in peers)-min(p['bbox'][1] for p in peers)<3*h:continue
        motifs.append((text,median((p['bbox'][2]-p['bbox'][0])/len(p['text'].strip()) for p in peers),
                       [p['source_span_id'] for p in peers]))
    if not motifs:return lines,[]
    output=[];overlays=[]
    for l in lines:
        match=re.match('[A-Z]{2,12}',l['text'])
        choices=[m for m in motifs if match and match[0] in m[0]]
        if not choices:output.append(l);continue
        motif,width,peers=max(choices,key=lambda m:len(m[0]));prefix=match[0];b=l['bbox'][:]
        if len(prefix)<len(l['text']):b[2]=min(b[2],b[0]+width*len(prefix))
        if not pale(b):output.append(l);continue
        overlays.append(dict(text=prefix,bbox=b,source_line=deepcopy(l),role='repeated_pale_background',
                             evidence=dict(method='repetition_geometry_and_pixel_contrast',peer_span_ids=peers)))
        if len(prefix)<len(l['text']):
            clean=deepcopy(l);clean['text']=l['text'][len(prefix):].lstrip()
            clean['original_source_lines']=[deepcopy(l)];clean['background_prefix']=prefix
            clean['recognition_evidence']=deepcopy(l.get('recognition_evidence',{}))
            clean['recognition_evidence'].update(score_text=l['text'],score_applies_to='original_line')
            output.append(clean)
    return output,overlays
