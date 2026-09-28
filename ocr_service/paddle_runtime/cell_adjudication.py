"""逐行采用使用独立辅助响应和原图笔画保留证据；未覆盖对象保持未决。"""
from copy import deepcopy
from PIL import Image,ImageDraw
import numpy as np
from regional_adoption import compact
from adoption_replay import candidate_objects


def neutral_ink(rgb):
 # 深红章字也可能三个通道都小于150；不能同时算作正文黑字和章墨。
 red=(rgb[:,:,0]-rgb[:,:,1]>35)&(rgb[:,:,0]-rgb[:,:,2]>35)
 return (rgb.max(2)<150)&~red


def object_coverage(cell, accepted):
 """在原输入空间核验文字墨迹；格线、独立红墨和未归属墨迹分别记账。"""
 original=cell.get('original_result',cell['result'])
 with Image.open(original['input']['image']) as im:rgb=np.asarray(im.convert('RGB'),dtype=float)
 height,width=rgb.shape[:2];shape=(width,height)
 inverse=np.linalg.inv(np.asarray(original['input']['input_to_page'],dtype=float))
 def polygon_mask(spans):
  mask=Image.new('L',shape);draw=ImageDraw.Draw(mask)
  for span in spans:
   if span.get('coordinate_source')!='engine_polygon' or not span.get('polygon_page'):continue
   points=np.c_[np.asarray(span['polygon_page'],float),np.ones(len(span['polygon_page']))]@inverse.T
   points=points[:,:2]/points[:,2:]
   draw.polygon([tuple(p) for p in points],fill=1)
  return np.asarray(mask)>0
 all_spans=[s for result in [original,*cell.get('content_candidates',[])] for s in result.get('spans',[])]
 target=polygon_mask(all_spans);verified=polygon_mask(accepted)
 neutral=neutral_ink(rgb)
 # 只剔除已有可见网格边界上实际连续的线像素，不擦除正文多边形内的笔画。
 grid=np.zeros_like(neutral)
 if original['input'].get('geometry_origin')=='automatic_visible_grid' and cell.get('bbox_pdf'):
  x0,y0,x1,y1=cell['bbox_pdf']
  points=np.array([[x0,y0,1],[x1,y0,1],[x1,y1,1],[x0,y1,1]])@inverse.T
  points=points[:,:2]/points[:,2:]
  border=Image.new('L',shape)
  vertices=[tuple(max(0,min(extent-1,float(value))) if -1<=value<=extent+1 else float(value)
                  for value,extent in zip(point,(width,height))) for point in points]
  radius=max(1,int(round(max(np.linalg.norm(inverse[0,:2]),np.linalg.norm(inverse[1,:2])))))
  # 使用完整变换后的四条网格边，不将剪切后的边重新轴对齐。
  ImageDraw.Draw(border).line(vertices+[vertices[0]],fill=1,width=2*radius+1)
  grid=neutral&(np.asarray(border)>0)
 neutral &= ~grid
 target_ink=neutral&target
 unknown=neutral&~target
 colored=(rgb.min(2)<180)
 red=(rgb[:,:,0]-rgb[:,:,1]>35)&(rgb[:,:,0]-rgb[:,:,2]>35)&colored
 n=int(target_ink.sum());covered=int((target_ink&verified).sum())
 objects=[]
 for span in original.get('spans',[]):
  mask=polygon_mask([span]);ink=neutral&mask;count=int(ink.sum())
  objects.append(dict(source_span_id=span.get('span_id'),text=span.get('text_raw',''),
      target_ink_pixels=count,verified_ink_pixels=int((ink&verified).sum()),
      covered_fraction=float((ink&verified).sum()/max(count,1)),
      role='neutral_text' if count else 'red_or_unread_region',polygon_page=span.get('polygon_page')))
 body_complete=n>0 and covered/max(n,1)>=.95
 return dict(method='engine_polygons_in_original_input; visible_grid_separate',
     target_ink_pixels=n,verified_ink_pixels=covered,coverage=covered/max(n,1),
     grid_pixels=int(grid.sum()),red_pixels=int(red.sum()),unassigned_neutral_pixels=int(unknown.sum()),
     objects=objects,body_complete=body_complete,
     complete=body_complete and int(unknown.sum())<=.05*max(int(neutral.sum()),1))


def pixel_preservation(original,candidate,span):
 try:
  with Image.open(original['input']['image']) as im:a=np.asarray(im.convert('RGB'),dtype=float)
  with Image.open(candidate['input']['image']) as im:b=np.asarray(im.convert('RGB'),dtype=float)
 except (OSError,KeyError):return dict(eligible=False,reason='image_unavailable')
 if a.shape!=b.shape or original['input']['input_to_page']!=candidate['input']['input_to_page']:return dict(eligible=False,reason='different_geometry')
 mask=Image.new('L',(a.shape[1],a.shape[0]));ImageDraw.Draw(mask).polygon([tuple(p) for p in span['polygon_input_px']],fill=1)
 dark=neutral_ink(a)&(np.asarray(mask)>0);n=int(dark.sum());kept=int((dark&(b.max(2)<180)).sum())
 return dict(eligible=n>=20 and kept/max(n,1)>=.99,neutral_pixels=n,retained_pixels=kept,retained_fraction=kept/max(n,1),reason='same_geometry_original_neutral_strokes_preserved')


def decide_cell(cell,auxiliary):
 original=cell.get('original_result',cell['result']);choices=[]
 # 原图正文也是实际候选，不能只让去章预处理结果参加复核。
 body_ids={s['span_id'] for s in cell['result'].get('body_spans',cell['result'].get('spans',[]))}
 for ai,result in [(-1,original),*enumerate(cell.get('content_candidates',[]))]:
  objects=candidate_objects(result);accepted=[];proof=[]
  for si,obj in enumerate(objects):
   if not obj['eligible']:continue
   aux=auxiliary.get((ai,si));span=obj['span']
   if ai==-1 and span['span_id'] not in body_ids:continue
   if not aux or aux.get('status')!='completed':continue
   sequence=aux.get('sequence_response');decision=None
   if sequence:
    from sequence_adjudication import decide_sequence
    scored=sequence.get('sequence_evidence',{}).get('local_competitions',[])
    # 竞争集合来自实际相交对象，已在请求时固定；不用整格字符串拼出伪候选。
    competition=sequence.get('candidate_texts',[])
    decision=decide_sequence(span['text_raw'],competition,sequence,sequence.get('source_geometry',{}))
   verified=decision and decision['accepted']
   if ai==-1 and not (verified and decision.get('whole_sequence_review',{}).get('complete')):continue
   if not verified:
    if compact(aux['text'])!=compact(span['text_raw']):continue
    tokens=aux['ctc']['tokens']
    if not tokens or any(t['posterior']<.8 for t in tokens):continue
   pixels=pixel_preservation(original,result,span)
   if not pixels['eligible']:continue
   if verified:
    span=deepcopy(span)
    if decision['text']!=span['text_raw']:
     span.setdefault('original_text_raw',span['text_raw']);span['text_raw']=decision['text'];span['content_source']='finite_sequence_cell_object'
    geometry=sequence.get('source_geometry',{})
    if geometry.get('target_polygon_page') and decision.get('whole_sequence_review',{}).get('complete'):
     span['original_polygon_page']=span['polygon_page'];span['polygon_page']=geometry['target_polygon_page']
     inv=np.linalg.inv(np.asarray(result['input']['input_to_page'],float))
     points=np.c_[np.asarray(span['polygon_page'],float),np.ones(len(span['polygon_page']))]@inv.T
     span['polygon_input_px']=(points[:,:2]/points[:,2:]).tolist()
     span['covered_source_span_ids']=geometry.get('covered_source_span_ids',[])
   accepted.append(span);proof.append(dict(span_id=span['span_id'],pixel_evidence=pixels,auxiliary=aux,sequence_decision=decision))
  if not accepted:continue
  # 一个完整行覆盖的旧片段不再重复显示；保留覆盖关系，不改变单元格身份。
  keep=[]
  for index,span in enumerate(accepted):
   superseded=any(span['span_id'] in other.get('covered_source_span_ids',[]) and
                  len(other.get('covered_source_span_ids',[]))>len(span.get('covered_source_span_ids',[]))
                  for other in accepted if other is not span)
   if not superseded:keep.append(index)
  accepted=[accepted[i] for i in keep];proof=[proof[i] for i in keep]
  evidence=object_coverage(cell,accepted)
  required={s['span_id'] for s in original.get('spans',[]) if s['span_id'] in body_ids and s.get('text_raw','').strip()}
  confirmed={s['span_id'] for s in accepted}
  original_complete=ai!=-1 or required.issubset(confirmed)
  choices.append(dict(arm_index=ai,spans=accepted,proof=proof,neutral_coverage=evidence['coverage'],
                      coverage_evidence=evidence,complete=evidence['complete'] and original_complete,
                      body_complete=evidence['body_complete'] and original_complete,object_qualification=objects))
 eligible=[c for c in choices if c['complete'] or (c['body_complete'] and all(
     p.get('sequence_decision',{}).get('whole_sequence_review',{}).get('complete') for p in c['proof']))]
 if not eligible:
  # 完整格的线条、章和未归属墨迹不能否定已经独立核实的文字对象。
  # 只替换实际引擎多边形对应的行；整格 crop_region 不是文字位置证明。
  from assembly import coverage
  from regional_adoption import span_box
  current=deepcopy(cell['result']);replacements=[]
  for choice in choices:
   for span,proof in zip(choice['spans'],choice['proof']):
    decision=proof.get('sequence_decision') or {}
    if not decision.get('accepted') or span.get('coordinate_source')!='engine_polygon':continue
    b=span_box(span)
    matches=[(i,old) for i,old in enumerate(current['spans']) if old.get('coordinate_source')=='engine_polygon'
             and span_box(old) and coverage(span_box(old),b)>.85 and coverage(b,span_box(old))>.85]
    if len(matches)!=1:continue
    i,old=matches[0]
    if old.get('human_revision') or old['text_raw']==span['text_raw']:continue
    if any(x['index']==i for x in replacements):continue
    revised=deepcopy(span);revised['span_id']=old['span_id'];revised['original_text_raw']=old['text_raw']
    revised['content_source']='verified_cell_text_object; other cell roles unresolved'
    current['spans'][i]=revised;replacements.append(dict(index=i,original=deepcopy(old),candidate=deepcopy(span),proof=proof))
  if replacements:
   current.pop('body_spans',None);current['text_assembled']='\n'.join(s['text_raw'] for s in current['spans'])
   return dict(accepted=True,resolved=False,reason='verified_text_objects_only; remaining_cell_roles_unresolved',
               result=current,object_replacements=replacements,choices=choices)
  # 主框只覆盖少量正文墨迹时，可采用完整区域候选，但字符冲突继续未决。
  def ink_coverage(spans):
   return object_coverage(cell,spans)['coverage']
  primary_coverage=ink_coverage(original.get('body_spans',original['spans']))
  if primary_coverage<.65:
   usable=[]
   for ai,result in enumerate(cell.get('content_candidates',[])):
    spans=[o['span'] for o in candidate_objects(result) if o['eligible']]
    if spans and object_coverage(cell,spans)['complete'] and all(pixel_preservation(original,result,s)['eligible'] for s in spans):usable.append((ai,spans))
   if usable and len({compact(''.join(s['text_raw'] for s in spans)) for _,spans in usable})==1:
    ai,spans=usable[0];result=deepcopy(cell['content_candidates'][ai]);result['spans']=spans;result.pop('body_spans',None);result['text_assembled']='\n'.join(s['text_raw'] for s in spans)
    return dict(accepted=True,resolved=False,reason='primary_missing_neutral_glyphs_completed; character_risk_retained',result=result,evidence=dict(primary_coverage=primary_coverage,candidate_coverage=ink_coverage(spans)),choices=choices)
  return dict(accepted=False,reason='uncovered_neutral_content_or_unverified_line',choices=choices)
 texts={compact(''.join(s['text_raw'] for s in c['spans'])) for c in eligible}
 if len(texts)!=1:return dict(accepted=False,reason='eligible_object_character_conflict',choices=choices)
 choice=eligible[0]
 if choice['arm_index']==-1:
  # 核实正文不等于删除独立章号；保留其原始来源和自己的未决。
  result=deepcopy(original);verified={s['span_id']:s for s in choice['spans']}
  result['spans']=[deepcopy(verified.get(s['span_id'],s)) for s in result['spans']]
 else:
  result=deepcopy(cell['content_candidates'][choice['arm_index']]);result['spans']=choice['spans']
 result.pop('body_spans',None);result['text_assembled']='\n'.join(s['text_raw'] for s in choice['spans'])
 complete_sequence=all(p.get('sequence_decision',{}).get('whole_sequence_review',{}).get('complete') for p in choice['proof'])
 return dict(accepted=True,resolved=complete_sequence and choice['complete'],body_verified=complete_sequence,
     residual_scope='unassigned_ink_outside_verified_text' if not choice['complete'] else None,
     reason='independent_recognition_and_original_stroke_coverage',result=result,evidence=choice,choices=choices)
