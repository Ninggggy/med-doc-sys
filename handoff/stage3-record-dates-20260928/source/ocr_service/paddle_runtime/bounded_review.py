"""一次有界局部自动复核；原始响应、未采用输出、决策证据均随页面保留。"""
from copy import deepcopy
from pathlib import Path
import math
from PIL import Image,ImageOps
from regional_adoption import input_geometry,input_box,span_box,compact
from adoption_replay import replay_adoptions,adopt_dense_responses,candidate_objects
from glyph_adjudication import decide_dense
from cell_adjudication import decide_cell


def input_metadata(primary,span,q,padding_px=0):
 inv=input_geometry(primary)[1]
 polygon=[[u-q[0]+padding_px,v-q[1]+padding_px] for point in span['polygon_page'] for u,v in [inv(*point)]]
 return dict(source_bbox_px=q,padding_px=padding_px,source_span_id=span['span_id'],
             target_polygon_input_px=polygon,source_input=deepcopy(primary['input']),
             preprocessing='original',method='full_affine_target_polygon')


def dense_input(primary,span,path):
 poly=span.get('polygon_page',[])
 if len(poly)==4:
  dx,dy=poly[1][0]-poly[0][0],poly[1][1]-poly[0][1]
  # 已近水平的密集小字保留源像素，避免无收益插值损失标点细节。
  if abs(math.degrees(math.atan2(dy,dx)))>2:return rectified_line_input(primary,span,path)
 b=span_box(span);inv=input_geometry(primary)[1];box=input_box(b,inv);top,bottom=box[1]-4,box[3]+4
 for other in primary['spans']:
  ob=span_box(other)
  if other is span or not ob or min(b[2],ob[2])-max(b[0],ob[0])<.5*(b[2]-b[0]):continue
  q=input_box(ob,inv)
  if q[3]<=box[1]:top=max(top,(box[1]+q[3])/2)
  if q[1]>=box[3]:bottom=min(bottom,(box[3]+q[1])/2)
 q=[math.floor(box[0])-3,math.floor(top),math.ceil(box[2])+3,math.ceil(bottom)]
 with Image.open(primary['input']['image']) as im:ImageOps.expand(im.crop(q),8,fill='white').save(path)
 return input_metadata(primary,span,q,8)


def rectified_line_input(primary,span,path):
 import cv2
 import numpy as np
 inv=input_geometry(primary)[1]
 polygon=np.array([inv(*p) for p in span['polygon_page']],np.float32)
 width=max(np.linalg.norm(polygon[1]-polygon[0]),np.linalg.norm(polygon[2]-polygon[3]))
 height=max(np.linalg.norm(polygon[3]-polygon[0]),np.linalg.norm(polygon[2]-polygon[1]))
 margin=max(4,int(height*.2));pad=8
 destination=np.array([[margin+pad,margin+pad],[width+margin+pad,margin+pad],
                       [width+margin+pad,height+margin+pad],[margin+pad,height+margin+pad]],np.float32)
 transform=cv2.getPerspectiveTransform(polygon,destination)
 image=cv2.imread(primary['input']['image'])
 size=(int(math.ceil(width))+2*(margin+pad)+1,int(math.ceil(height))+2*(margin+pad)+1)
 rectified=cv2.warpPerspective(image,transform,size,flags=cv2.INTER_CUBIC,borderMode=cv2.BORDER_CONSTANT,borderValue=(255,255,255))
 # 白边只标记额外画布；源图缺失部分仍由真实输入范围核验。
 inverse=np.linalg.inv(transform);corners=np.c_[destination,np.ones(4)]@inverse.T
 corners=corners[:,:2]/corners[:,2:]
 complete=bool(np.all(corners>=0) and np.all(corners[:,0]<image.shape[1]) and np.all(corners[:,1]<image.shape[0]))
 cv2.imwrite(str(path),rectified)
 return dict(source_span_id=span['span_id'],target_polygon_input_px=destination.tolist(),
             source_input=deepcopy(primary['input']),source_to_rectified=transform.tolist(),
             source_target_complete=complete,padding_px=0,preprocessing='original',method='target_polygon_perspective_rectification')


def object_input(primary,span,path):
 b=input_box(span_box(span),input_geometry(primary)[1]);margin=max(8,math.ceil((b[3]-b[1])*.25))
 q=[math.floor(b[0])-5-margin,math.floor(b[1])-5-margin,math.ceil(b[2])+5+margin,math.ceil(b[3])+5+margin]
 with Image.open(primary['input']['image']) as im:
  q=[max(0,q[0]),max(0,q[1]),min(im.width,q[2]),min(im.height,q[3])]
  ImageOps.expand(im.crop(q),8,fill='white').save(path)
 return input_metadata(primary,span,q,8)


def field_value_input(primary,span,path):
 """完整短值行独立缩放；不让大标签和上一字段残行占据识别输入。"""
 source=primary
 native=primary['input'].get('original_raster')
 if native and native.get('origin')!='single_visible_page_raster':native=None
 if native and native.get('origin')=='single_visible_page_raster':
  source={**primary,'input_size':native['size'],'input':{**native,'sample_id':primary.get('sample_id')}}
 b=input_box(span_box(span),input_geometry(source)[1])
 margin=max(2,math.ceil((b[3]-b[1])*(.25 if native else .1)));pad=8
 q=[math.floor(b[0])-margin,math.floor(b[1])-margin,math.ceil(b[2])+margin,math.ceil(b[3])+margin]
 # 不为了加上下文引入上一行。保留完整目标本身，不以邻行框裁掉目标字形。
 if native:
  for other in primary.get('spans',[]):
   if other.get('span_id')==span.get('span_id') or not other.get('polygon_page'):continue
   ob=input_box(span_box(other),input_geometry(source)[1])
   if min(ob[2],b[2])<=max(ob[0],b[0]):continue
   if ob[1]<b[1] and ob[3]<(b[1]+b[3])/2:q[1]=max(q[1],min(math.floor(b[1]),math.ceil(ob[3])))
  inv=input_geometry(source)[1]
  if any(min(q[2],ob[2])>max(q[0],ob[0]) and min(q[3],ob[3])>max(q[1],ob[1])
         for box in native.get('excluded_overlay_bboxes',[]) for ob in [input_box(box,inv)]):
   fallback={**primary,'input':{k:v for k,v in primary['input'].items() if k!='original_raster'}}
   return field_value_input(fallback,span,path)
 with Image.open(source['input']['image']) as im:
  q=[max(0,q[0]),max(0,q[1]),min(im.width,q[2]),min(im.height,q[3])]
  crop=ImageOps.expand(im.crop(q),pad,fill='white')
  scale=max(1,math.ceil(32/(b[3]-b[1]))) if native else 2 if b[3]-b[1]<32 else 1
  if scale>1:crop=crop.resize((crop.width*scale,crop.height*scale),Image.Resampling.LANCZOS)
  crop.save(path)
 metadata=input_metadata(source,span,q,pad)
 metadata['target_polygon_input_px']=[[x*scale,y*scale] for x,y in metadata['target_polygon_input_px']]
 metadata.update(padding_px=pad*scale,scale=scale,method='complete_observed_field_value',
                 source_sampling='original_page_raster' if native else 'page_render')
 if native:
  neighbors=[]
  for other in primary.get('spans',[]):
   if other.get('span_id')==span.get('span_id') or other.get('coordinate_source')!='engine_polygon':continue
   if not other.get('polygon_page'):continue
   polygon=input_metadata(source,other,q,pad)['target_polygon_input_px']
   if max(x for x,y in polygon)<0 or max(y for x,y in polygon)<0:continue
   if min(x for x,y in polygon)>q[2]-q[0]+2*pad or min(y for x,y in polygon)>q[3]-q[1]+2*pad:continue
   neighbors.append(dict(source_span_id=other['span_id'],polygon_input_px=[[x*scale,y*scale] for x,y in polygon]))
  metadata['detected_neighbors']=neighbors
 return metadata


def cell_line_input(cell,span,path,candidate=None):
 from assembly import coverage
 original=cell.get('original_result',cell['result']);b=span_box(span);texts=[span['text_raw']]
 for result in [original,*cell.get('content_candidates',[])]:
  for other in result.get('spans',[]):
   ob=span_box(other)
   if ob and coverage(ob,b)>.7 and coverage(b,ob)>.7 and other.get('text_raw'):texts.append(other['text_raw'])
 source=original;preservation=None
 if candidate is not None:
  from cell_adjudication import pixel_preservation
  preservation=pixel_preservation(original,candidate,span)
  if preservation['eligible']:source=candidate
 geometry=dense_input({**source,'spans':[span]},span,path)
 if source is not original:
  geometry.update(preprocessing='neutral_preserving',pixel_preservation=preservation,
      original_source_input=deepcopy(original['input']))
 geometry['review_scope']='cell_body'
 return geometry,list(dict.fromkeys(texts))


def complete_cell_line_input(cell,span,path,candidate):
 """依据真实中性笔画和同基线检测行准备完整对象，不使用文本格式补值。"""
 import cv2
 import numpy as np
 from assembly import bounds,coverage
 original=cell.get('original_result',cell['result'])
 from cell_adjudication import pixel_preservation
 preservation=pixel_preservation(original,candidate,span)
 if not preservation['eligible']:return cell_line_input(cell,span,path,candidate)
 source=candidate;selected=[span];b=span_box(span);height=b[3]-b[1]
 with Image.open(original['input']['image']) as im:rgb=np.asarray(im.convert('RGB'),float)
 inv=input_geometry(original)[1]
 def neutral_role(other):
  poly=np.array([inv(*p) for p in other['polygon_page']],np.int32)
  region=np.zeros(rgb.shape[:2],np.uint8);cv2.fillConvexPoly(region,poly,1)
  ink=(rgb.min(2)<180)&(region>0)
  red=(rgb[:,:,0]-rgb[:,:,1]>35)&(rgb[:,:,0]-rgb[:,:,2]>35)&ink
  return int(red.sum())/max(int(ink.sum()),1)<=.05
 for other in [s for result in [source,original,*cell.get('content_candidates',[])] for s in result.get('spans',[])]:
  if other is span or not other.get('text_raw'):continue
  ob=span_box(other)
  if not ob:continue
  overlap=min(b[3],ob[3])-max(b[1],ob[1]);gap=max(b[0],ob[0])-min(b[2],ob[2])
  if overlap>=.7*min(height,ob[3]-ob[1]) and gap<=3*height:
   if (other in source.get('spans',[]) or neutral_role(other)) and pixel_preservation(original,source,other)['eligible']:
    if not any(s['span_id']==other['span_id'] for s in selected):selected.append(other)
 points=np.array([inv(*point) for s in selected for point in s['polygon_page']],float)
 mask=np.zeros(rgb.shape[:2],np.uint8)
 cv2.fillConvexPoly(mask,cv2.convexHull(points.astype(np.int32)),1)
 from cell_adjudication import neutral_ink
 neutral=neutral_ink(rgb).astype(np.uint8)
 # 以完整连通字形为单位收集，不能把目标多边形边缘裁成半个字。
 count,labels,stats,_=cv2.connectedComponentsWithStats(neutral,8)
 pieces=[]
 for index in range(1,count):
  x,y,w,h,area=stats[index]
  if area<3:continue
  inside=int(((labels[y:y+h,x:x+w]==index)&(mask[y:y+h,x:x+w]>0)).sum())
  if inside/area>=.6:pieces.append((x,y,x+w,y+h))
 if not pieces:return cell_line_input(cell,span,path,candidate)
 # 连通域只允许向外补足检测范围，不能向内裁掉浅色笔画。
 # 中性深色像素完整保留不等于整个字形完整保留。
 component_bounds=bounds(pieces)
 x0=math.floor(min(component_bounds[0],points[:,0].min()))
 y0=math.floor(min(component_bounds[1],points[:,1].min()))
 x1=math.ceil(max(component_bounds[2],points[:,0].max()))
 y1=math.ceil(max(component_bounds[3],points[:,1].max()))
 # 同时保留完整行高与上下文；不得用细条带只识别剩下的笔画。
 q=[max(0,int(x0)-6),max(0,int(y0)-6),min(rgb.shape[1],int(x1)+6),min(rgb.shape[0],int(y1)+6)]
 with Image.open(source['input']['image']) as im:
  crop=ImageOps.expand(im.crop(q),8,fill='white');crop=crop.resize((crop.width*2,crop.height*2),Image.Resampling.LANCZOS);crop.save(path)
 matrix=np.asarray(original['input']['input_to_page'],float)
 poly=np.array([[x0,y0,1],[x1,y0,1],[x1,y1,1],[x0,y1,1]])@matrix.T
 target=(poly[:,:2]/poly[:,2:]).tolist()
 full_span={**span,'polygon_input_px':[[x0,y0],[x1,y0],[x1,y1],[x0,y1]],'polygon_page':target}
 preservation=pixel_preservation(original,source,full_span)
 geometry=dict(source_bbox_px=q,padding_px=16,source_span_id=span['span_id'],
     target_polygon_input_px=[[(x-q[0]+8)*2,(y-q[1]+8)*2] for x,y in [(x0,y0),(x1,y0),(x1,y1),(x0,y1)]],
     target_polygon_page=target,covered_source_span_ids=[s['span_id'] for s in selected],
     source_input=deepcopy(source['input']),original_source_input=deepcopy(original['input']),
     preprocessing='neutral_preserving',pixel_preservation=preservation,
     method='complete_neutral_components_and_same_baseline_detections',scale=2,
     review_scope='cell_body')
 texts=[span['text_raw']]
 own=[s for s in selected if s in source.get('spans',[])];own.sort(key=lambda s:span_box(s)[0]);texts.append(''.join(s['text_raw'] for s in own))
 for result in [original,*cell.get('content_candidates',[])]:
  matching=[s for s in result.get('spans',[]) if s.get('text_raw') and span_box(s) and (result is not original or neutral_role(s)) and
            min(b[3],span_box(s)[3])-max(b[1],span_box(s)[1])>.7*min(height,span_box(s)[3]-span_box(s)[1])]
  matching.sort(key=lambda s:span_box(s)[0])
  # 局部观察不等于完整行的删除候选；沿用完整行输入已有的双向几何对应要求。
  # 不按字数判断完整，也不丢弃原spans；这里只限定进入整行竞争的文字对象。
  if matching:
   extent=bounds([span_box(s) for s in matching])
   if coverage(extent,b)>.7 and coverage(b,extent)>.7:
    texts.append(''.join(s['text_raw'] for s in matching))
 return geometry,list(dict.fromkeys(texts))


def cell_review_input(page,cell,span,path,candidate=None):
 """已检测正文在完整章旁时优先复核原色，章角色不由预处理后的灰像素猜测。"""
 import numpy as np
 from glyph_adjudication import input_evidence
 regions=[r for r in page.get('stamp_regions',[]) if r.get('source')=='closed_red_circular_outline']
 same_page=any(cell is c for table in page.get('tables',[]) for c in table.get('cells',[]))
 if candidate is not None and regions and same_page:
  geometry,texts=cell_line_input(cell,span,path)
  source=cell.get('original_result',cell['result'])
  inv=np.linalg.inv(np.asarray(source['input']['input_to_page'],float))
  mapped=[]
  for region in regions:
   points=np.c_[np.asarray(region['polygon_page'],float),np.ones(len(region['polygon_page']))]@inv.T
   points=points[:,:2]/points[:,2:]
   if geometry.get('source_to_rectified'):
    points=np.c_[points,np.ones(len(points))]@np.asarray(geometry['source_to_rectified'],float).T
    points=points[:,:2]/points[:,2:]
   else:
    points=points-np.asarray(geometry['source_bbox_px'][:2])+geometry.get('padding_px',0)
   mapped.append(dict(source=region['source'],polygon_input_px=points.tolist(),polygon_page=region['polygon_page']))
  geometry['seal_overlay_role']=dict(source_page_input=deepcopy(page['page_result']['input']),regions=mapped)
  evidence=input_evidence(path,metadata=geometry)
  if evidence.get('eligible') and evidence.get('seal_overlay_role'):
   geometry['target_polygon_page']=deepcopy(span['polygon_page'])
   return geometry,texts
 return cell_line_input(cell,span,path) if candidate is None else complete_cell_line_input(cell,span,path,candidate)


def review_unresolved_cell_overlays(page,cells,model_dir,run,engine,limit):
 """只复核仍未完成的正文；不替换已经完整核实的输入，失败新视图保留为观察。"""
 from auxiliary_recognition import AuxiliaryRecognizer
 from sequence_adjudication import decide_sequence
 pending=[]
 for (ti,ci),entries in cells.items():
  cell=page['tables'][ti]['cells'][ci]
  if decide_cell(cell,entries).get('resolved'):continue
  for (ai,si),old in entries.items():
   if ai<0 or len(pending)>=limit:continue
   candidate=cell['content_candidates'][ai];span=candidate['spans'][si]
   path=Path(run)/f'original-cell-{ti}-{ci}-{ai}-{si}.png'
   geometry,texts=cell_review_input(page,cell,span,path,candidate)
   if not geometry.get('seal_overlay_role'):continue
   pending.append((entries,(ai,si),old,path,geometry,texts))
 if not pending:return 0
 model=AuxiliaryRecognizer(model_dir);scoring=[]
 try:
  for entries,key,old,path,geometry,texts in pending:
   try:
    response=model.predict(path,candidates=texts,geometry=geometry)
    scoring.append((str(path),response));old['original_color_review']=response
   except Exception as exc:old['original_color_review_failure']=dict(reason=repr(exc),previous_evidence_preserved=True)
 finally:model.close()
 complete_sequence_reviews(page['page_result'],scoring,engine)
 additional=complete_final_sequences(page['page_result'],scoring,engine,model_dir)
 for entries,key,old,path,geometry,texts in pending:
  response=old.get('original_color_review')
  if not response:continue
  # 此轮实际请求的评分失败，不得在这里退成辅助单臂后替换原证据。
  main=response.get('primary_sequence_review',{})
  failed=response.get('primary_sequence_failure') or main.get('status')=='failed'
  recovered=(main.get('status')=='completed' and main.get('image')==response.get('image')
       and main.get('source_geometry')==geometry
       and main.get('sequence_evidence',{}).get('method')=='full_ctc_forward_sum')
  if failed and not recovered:continue
  decision=decide_sequence(texts[0],texts[1:],response,geometry)
  # 完整原色对象核验通过才替换用于采用的视图；其余证据不删、不拼旧final。
  if decision.get('whole_sequence_review',{}).get('complete'):
   prior={k:v for k,v in old.items() if k!='original_color_review'}
   entries[key]={**response,'sequence_response':deepcopy(response),'prior_processed_review':prior}
 return len(scoring)+additional


def title_targets(page):
 from assembly import coverage,bounds
 primary=page.get('original_page_result',page['page_result'])
 targets=[]
 for region in page.get('layout_result',{}).get('regions',[]):
  if region.get('label')!='doc_title':continue
  spans=[span for span in primary['spans'] if span_box(span) and coverage(span_box(span),region['bbox_pdf'])>.5]
  if len(spans)<2:continue
  spans.sort(key=lambda span:span_box(span)[0]);b=bounds([span_box(span) for span in spans]+[region['bbox_pdf']])
  # 多行标题不伪装成可靠单行。
  if max(span_box(x)[1] for x in spans)-min(span_box(x)[1] for x in spans)>(b[3]-b[1])*.35:continue
  targets.append(dict(span_id=region['region_id']+':complete-title',text_raw=''.join(x['text_raw'] for x in spans),
                      polygon_page=[[b[0],b[1]],[b[2],b[1]],[b[2],b[3]],[b[0],b[3]]],
                      source_span_ids=[x['span_id'] for x in spans],role='doc_title'))
 return targets


def title_input(page, target, path):
 """只有像素证明叠加是同页红章时，使用PDF中原有的完整底图文字。"""
 import numpy as np
 from PIL import ImageDraw
 primary=page.get('original_page_result',page['page_result']);native=primary['input'].get('original_raster') or {}
 reference=native.get('rendered_reference')
 def fallback():return object_input(primary,target,path)
 if not reference or reference['input_to_page']!=primary['input']['input_to_page']:return fallback()
 with Image.open(primary['input']['image']) as im:a=np.asarray(im.convert('RGB'),dtype=float)
 with Image.open(reference['image']) as im:b=np.asarray(im.convert('RGB'),dtype=float)
 if a.shape!=b.shape:return fallback()
 inv=input_geometry(primary)[1];poly=[inv(*p) for p in target['polygon_page']]
 mask=Image.new('L',(a.shape[1],a.shape[0]));ImageDraw.Draw(mask).polygon(poly,fill=1);target_pixels=np.asarray(mask)>0
 delta=a-b;changed=(np.max(abs(delta),axis=2)>0)&target_pixels
 if not changed.any():return fallback()
 assigned=Image.new('L',mask.size);draw=ImageDraw.Draw(assigned)
 for region in page.get('stamp_regions',[]):
  if region.get('source')=='closed_red_circular_outline':draw.polygon([inv(*p) for p in region['polygon_page']],fill=1)
 coverage=float((changed&(np.asarray(assigned)>0)).sum()/changed.sum())
 # 红色前景的alpha合成必须使R不减、G/B不增；黑色改字、白色遮盖均不得取底图放行。
 if coverage<.95:return fallback()
 # 逐像素检验同一红色前景的混合关系；2为8位通道合成舍入误差，不是识别阈值。
 def matches_red_blend(multiply):
  foreground=np.zeros_like(b);foreground[:,:,0]=b[:,:,0] if multiply else 255
  direction=foreground-b;axis=np.argmax(abs(direction),axis=2)
  denominator=np.take_along_axis(direction,axis[:,:,None],axis=2)[:,:,0]
  numerator=np.take_along_axis(delta,axis[:,:,None],axis=2)[:,:,0]
  opacity=np.divide(numerator,denominator,out=np.zeros_like(numerator),where=denominator!=0)
  expected=b+opacity[:,:,None]*direction
  return (opacity>=0)&(opacity<=1)&(np.max(abs(a-expected),axis=2)<=2)
 normal=matches_red_blend(False);multiply=matches_red_blend(True)
 fit=normal if int((changed&normal).sum())>=int((changed&multiply).sum()) else multiply
 unproven=changed&~fit
 # 未证明属于红章的改动不能被底图替换。涉及字形时拒绝；仅背景差异也保留整页原像素。
 if np.any(unproven&((a.max(2)<200)|(b.max(2)<200))):return fallback()
 if not np.any(changed&fit):return fallback()
 source={**primary,'input_size':native['size'],'input':{**native,'sample_id':primary.get('sample_id')}}
 if unproven.any():
  restored=a.astype('uint8');restored[changed&fit]=b[changed&fit].astype('uint8')
  retained_path=Path(path).with_suffix('.layer.png');Image.fromarray(restored).save(retained_path)
  source={**primary,'input':{**reference,'image':str(retained_path),'sample_id':primary.get('sample_id')},'input_size':reference['size']}
 g=object_input(source,target,path)
 g.update(role=target.get('role','doc_title'),source_sampling='original_page_raster',
     original_page_input=deepcopy(primary['input']),
     raster_layer_evidence=dict(method='same_page_raster_and_red_alpha_overlay',changed_pixels=int(changed.sum()),
       seal_coverage=coverage,blend='normal' if fit is normal else 'multiply',retained_background_pixels=int(unproven.sum()),
       reference_image=reference['image'],hidden_text_used=False,seal_text_verified=False))
 return g


def recover_uncovered_field_lines(page,engine,auxiliary,run):
 """标签截短时，依据已有区域识别与原始底图恢复标签和值；只处理实际覆盖缺口。"""
 import numpy as np
 from assembly import field,coverage
 from field_coverage import field_value_geometry
 from product_consumer import consumer_page
 from sequence_adjudication import decide_sequence
 primary=page.get('original_page_result',page['page_result']);native=primary['input'].get('original_raster')
 if not native:return
 view=consumer_page(page);issues={e.get('region_id'):e for e in view['errors'] if e.get('stage')=='visible_region_gap'}
 directory=Path(run);directory.mkdir(parents=True,exist_ok=True)
 for index,entry in enumerate(page.get('regional_recognition',[])):
  region=entry['region'];issue=issues.get(region['region_id'])
  if not issue or region.get('label')!='text':continue
  label,value=field(entry.get('result',{}).get('text_assembled',''))
  if not label:continue
  members=[s for s in primary['spans'] if s.get('polygon_page') and coverage(span_box(s),region['bbox_pdf'])>.5]
  if len(members)!=1 or members[0].get('human_revision'):continue
  source=members[0]
  if field(source['text_raw'])[0]:continue # 既有完整标签由普通字段恢复负责。
  native_result={**primary,'input':native,'input_size':native['size']}
  inv=input_geometry(native_result)[1];b=input_box(region['bbox_pdf'],inv)
  x0,y0,x1,y1=map(int,[math.floor(b[0]),math.floor(b[1]),math.ceil(b[2]),math.ceil(b[3])])
  with Image.open(native['image']) as im:rgb=np.asarray(im.convert('RGB'))
  if not (0<=x0<x1<=rgb.shape[1] and 0<=y0<y1<=rgb.shape[0]):continue
  # 只沿同一实际字高找区域右缘后的文字，遇到一个字高的真空隙或相邻对象即停止。
  limit=rgb.shape[1]
  for other in primary['spans']:
   if other['span_id']==source['span_id'] or not other.get('polygon_page'):continue
   ob=input_box(span_box(other),inv)
   if ob[0]>=x1 and min(y1,ob[3])>max(y0,ob[1]):limit=min(limit,int(ob[0]))
  occupied=(rgb[y0:y1].max(2)<200).any(0);gap=0;end=x1
  for x in range(x1,limit):
   if occupied[x]:end=x+1;gap=0
   else:gap+=1
   if gap>=y1-y0:break
  matrix=np.asarray(native['input_to_page'],float)
  corners=np.array([[x0,y0,1],[end,y0,1],[end,y1,1],[x0,y1,1]])@matrix.T
  polygon=(corners[:,:2]/corners[:,2:]).tolist()
  target={**source,'polygon_page':polygon,'role':'field_line'}
  layer=title_input(page,target,directory/f'field-layer-{index}.png')
  if not layer.get('raster_layer_evidence'):continue
  analysis_input=layer['source_input'];analysis_result={**primary,'input':analysis_input,'input_size':analysis_input['size']}
  observed=field_value_geometry(analysis_result,target)
  if not observed:continue
  review=dict(source_span_id=source['span_id'],region_id=region['region_id'],label=label,
      original=source['text_raw'],accepted=False,reason='field_line_recovery_pending',field_geometry=observed,
      layer_evidence=layer['raster_layer_evidence'],original_issue=deepcopy(issue))
  page.setdefault('field_value_reviews',[]).append(review)
  try:
   # 原始底图已经过整个字段范围的叠加核验；子输入继承同一来源，不读取隐藏文字。
   validated_native={**analysis_input,'origin':'single_visible_page_raster','excluded_overlay_bboxes':[]}
   prepared={**analysis_result,'input':{**analysis_input,'original_raster':validated_native}}
   results=[]
   for part,poly in [('value',observed['polygon_page']),('label',observed['label_polygon_page'])]:
    path=directory/f'field-{index}-{part}.png';g=field_value_input(prepared,{**source,'polygon_page':poly},path)
    g['raster_layer_evidence']=deepcopy(layer['raster_layer_evidence'])
    m=deepcopy(g['source_input']['input_to_page']);q=g['source_bbox_px'];scale=g.get('scale',1);pad=g['padding_px']/scale
    for row in m[:2]:row[2]+=row[0]*(q[0]-pad)+row[1]*(q[1]-pad);row[0]/=scale;row[1]/=scale
    result=engine.predict(dict(sample_id=primary['sample_id']+f'-field-gap-{index}-{part}',image=str(path),
        granularity='line-rec',stage='covered_field_line_recovery',input_to_page=m),60)
    if result.get('status')!='completed' or len(result.get('spans',[]))!=1:break
    text=result['spans'][0]['text_raw'];candidates=[text] if part=='value' else [label+'：',text]
    response=auxiliary.predict(path,candidates=list(dict.fromkeys(candidates)),geometry=g)
    complete_sequence_reviews(primary,[(source['span_id']+'-'+part,response)],engine)
    decision=decide_sequence(candidates[0],list(dict.fromkeys(candidates))[1:],response,g)
    if part=='value':response,decision=complete_weak_value_competition(primary,source['span_id'],response,decision,engine,auxiliary)
    results.append((result,response,decision))
    review[part+'_attempt']=dict(result=result,response=response,decision=decision)
   if len(results)!=2:review['reason']='field_part_recognition_failed';continue
   result,response,decision=results[0];label_decision=results[1][2]
   span=deepcopy(result['spans'][0]);span.update(polygon_page=observed['polygon_page'],coordinate_source='connected_glyphs')
   label_span=deepcopy(results[1][0]['spans'][0]);label_span.update(polygon_page=observed['label_polygon_page'],coordinate_source='connected_glyphs')
   review.update(span=span,result=result,response=response,decision=decision,text=decision['text'],label_decision=label_decision,
       label_span=label_span,
       label_verified=label_decision.get('whole_sequence_review',{}).get('complete',False) and label_decision['text'].replace(':','：')==label+'：')
   review['accepted']=bool(review['label_verified'] and decision.get('whole_sequence_review',{}).get('complete'))
   review['reason']='complete_field_line_recovered' if review['accepted'] else 'field_line_sequence_unverified'
  except Exception as exc:review.update(reason='optional_field_line_recovery_failed',failure=repr(exc))


def qualified_dense_arms(page,candidate,span):
 from diagnostic_reconciliation import dense_arm_applicability
 arms=[deepcopy(a) for a in candidate.get('arms',[]) if dense_arm_applicability(a,page.get('original_page_result',page['page_result']))[0]]
 if span.get('original_text_raw'):arms.append({'text':span['original_text_raw']})
 return arms


def complete_sequence_reviews(primary, scoring_objects, engine):
 for index,(sid,response) in enumerate(scoring_objects):
  groups=response.get('sequence_evidence',{}).get('local_competitions',[])
  if not groups and all(t.get('posterior',0)>=.97 for t in response.get('ctc',{}).get('tokens',[])):continue
  if response.get('sequence_evidence',{}).get('primary_text') is None:
   response['primary_sequence_failure']=dict(reason='missing_primary_scoring_context',main_result_preserved=True);continue
  candidates=list(dict.fromkeys([response['sequence_evidence']['primary_text'],
      *[v['text'] for group in groups if group.get('available') for v in group['variants']]]))
  existing=response.get('primary_sequence_review',{})
  if (existing.get('status')=='completed' and existing.get('image')==response.get('image')
      and existing.get('source_geometry')==response.get('source_geometry')
      and set(candidates).issubset({row['text'] for row in existing.get('sequence_evidence',{}).get('scores',[]) if row.get('available')})):continue
  try:
   # 字段、正文和补充评分可分批调用；批内index会重复，不能覆盖已存响应。
   request_index=getattr(engine,'sequence_review_request_count',0)
   engine.sequence_review_request_count=request_index+1
   result=engine.predict(dict(sample_id=primary.get('sample_id','page')+f'-sequence-support-{request_index}',
     image=response['image'],granularity='line-rec',stage='finite_sequence_scoring',sequence_candidates=candidates,
     input_to_page=[[1,0,0],[0,1,0],[0,0,1]],coordinate_note='rectified input pixels, not page geometry'),60)
   response['primary_sequence_review']=dict(status=result['status'],model='PP-OCRv6_medium_rec',
     image=response['image'],text=result.get('text_assembled',''),source_geometry=deepcopy(response['source_geometry']),
     sequence_evidence=result.get('sequence_evidence',{}),ctc=result.get('ctc',{}),seconds=result.get('seconds'))
  except Exception as exc:response['primary_sequence_failure']=dict(reason=repr(exc),main_result_preserved=True)


def complete_final_sequences(primary,scoring_objects,engine,model_dir):
 """局部编辑合成的最终字符串最多补一次有限评分；不枚举编辑组合。"""
 from sequence_adjudication import decide_sequence
 from auxiliary_recognition import AuxiliaryRecognizer
 pending=[]
 for sid,response in scoring_objects:
  evidence=response.get('sequence_evidence',{});text=evidence.get('primary_text')
  if text is None:continue
  candidates=evidence.get('candidate_texts',[])
  main=response.get('primary_sequence_review',{})
  decoded=main.get('text','')
  if (response.get('source_geometry',{}).get('review_scope')=='cell_body'
      and main.get('status')=='completed' and main.get('image')==response.get('image')
      and main.get('source_geometry')==response.get('source_geometry') and decoded and decoded not in candidates):
   # 当前原图复核主模型实际解码也进入同一有限集合；不能只有否决权。
   pending.append((sid,response,list(dict.fromkeys([text,*candidates,decoded]))));continue
  decision=decide_sequence(text,candidates,response,response.get('source_geometry',{}))
  if decision.get('accepted') and decision.get('whole_sequence_review') and not decision['whole_sequence_review']['selected_sequence_scored']:
   pending.append((sid,response,list(dict.fromkeys([text,*candidates,decision['text']]))))
 if not pending:return 0
 try:model=AuxiliaryRecognizer(model_dir)
 except Exception as exc:
  for _,response,_ in pending:response['final_sequence_failure']=dict(reason=repr(exc),previous_evidence_preserved=True)
  return 0
 completed=[]
 try:
  for sid,response,candidates in pending:
   old=deepcopy(response.get('sequence_evidence',{}))
   try:refreshed=model.predict(response['image'],candidates,response['source_geometry'])
   except Exception as exc:
    response['final_sequence_failure']=dict(reason=repr(exc),previous_evidence_preserved=True);continue
   response.update(refreshed);response['prior_final_sequence_evidence']=old
   completed.append((sid,response))
 finally:model.close()
 complete_sequence_reviews(primary,completed,engine)
 return len(pending)


def apply_review(page,dense_responses,cell_responses,object_responses=None,region_responses=None):
 from adapters import assemble
 from text_roles import apply_cell_roles
 output=adopt_dense_responses(replay_adoptions(page));decisions=[]
 primary=output['page_result'];byid={s['span_id']:s for s in primary['spans']}
 for candidate in output.get('dense_candidates',[]):
  sid=candidate['span_id'];response=dense_responses.get(sid);span=byid.get(sid)
  if not response or not span:continue
  # 历史原值也是比较对象，不能只比较已经相同的两臂。
  arms=qualified_dense_arms(page,candidate,span)
  decision=decide_dense(span['text_raw'],arms,response)
  # 必须确有字形证据，不能把两个字符串相同当自动解决。
  decision['accepted']=decision['accepted'] and bool(decision['glyphs'] or decision.get('sequence_verified'))
  summary=deepcopy(response)
  if summary.get('primary_sequence_review'):
   primary_review=summary.pop('primary_sequence_review')
   summary['primary_sequence_review_reference']=dict(collection='bounded_review_responses.dense',source_span_id=sid,
       model=primary_review.get('model'),status=primary_review.get('status'),text=primary_review.get('text'))
  decision.update(kind='dense',source_span_id=sid,original=span['text_raw'],response=summary)
  if decision['text']!=span['text_raw']:
   span.setdefault('original_text_raw',span['text_raw']);span['text_raw']=decision['text'];span['content_source']='bounded_local_glyph_adjudication; score remains original line'
  decisions.append(decision)
 for sid,response in (object_responses or {}).items():
  span=byid.get(sid)
  if not span:continue
  from sequence_adjudication import decide_sequence
  decision=decide_sequence(span['text_raw'],[],response,response.get('source_geometry',{}))
  decision.update(kind='object',source_span_id=sid,original=span['text_raw'],response=deepcopy(response))
  if decision.get('sequence_verified') and decision['text']!=span['text_raw']:
   span.setdefault('original_text_raw',span['text_raw']);span['text_raw']=decision['text'];span['content_source']='finite_sequence_adjudication; original score retained'
  decisions.append(decision)
 primary['text_assembled']=assemble(primary['spans'])
 for ti,t in enumerate(output['tables']):
  for ci,c in enumerate(t['cells']):
   if not c.get('content_candidates'):continue
   decision=decide_cell(c,cell_responses.get((ti,ci),{}));decision.update(kind='cell',table_index=ti,cell_index=ci,
       original_body_text=c['result'].get('text_assembled',''))
   if decision['accepted']:
    c.setdefault('original_result',deepcopy(c['result']));c['result']=deepcopy(decision['result']);c['content_candidate_adopted']=True
    apply_cell_roles(c,output.get('stamp_regions',[]))
   decisions.append(decision)
 for ti,table in enumerate(output['tables']):
  for ci,cell in enumerate(table['cells']):
   result=cell['result']
   for span in result.get('spans',[]):
    response=(object_responses or {}).get(span['span_id'])
    if not response:continue
    if span.get('visual_role')=='red_overlay':
     decisions.append(dict(kind='object',source_span_id=span['span_id'],original=span['text_raw'],text=span['text_raw'],
        accepted=False,recovered=False,reason='requires_complete_seal_object_review',table_index=ti,cell_index=ci,response=response))
     continue
    from sequence_adjudication import decide_sequence
    decision=decide_sequence(span['text_raw'],[],response,response.get('source_geometry',{}))
    decision.update(kind='object',source_span_id=span['span_id'],original=span['text_raw'],table_index=ti,cell_index=ci,response=response)
    if decision.get('sequence_verified') and decision['text']!=span['text_raw']:
     span.setdefault('original_text_raw',span['text_raw']);span['text_raw']=decision['text'];span['content_source']='finite_sequence_adjudication; original score retained'
    decisions.append(decision)
   result['text_assembled']=assemble(result.get('body_spans',result['spans']))
 for target,response in region_responses or []:
  from sequence_adjudication import decide_sequence
  decision=decide_sequence(target['text_raw'],[],response,response.get('source_geometry',{}))
  from difflib import SequenceMatcher
  if any(op=='delete' for op,*rest in SequenceMatcher(None,target['text_raw'],decision['text'],autojunk=False).get_opcodes()):
   decision.update(accepted=False,recovered=False,text=target['text_raw'],reason='region_deletion_without_independent_coverage_proof')
  supporting=response.get('primary_candidate') or {}
  if supporting.get('status')!='completed' or supporting.get('text_assembled','').strip()!=response.get('text','').strip():
   decision.update(accepted=False,recovered=False,text=target['text_raw'],reason='region_requires_independent_supported_candidate')
  decision.update(kind='region',target=deepcopy(target),source_span_ids=target['source_span_ids'],original=target['text_raw'],response=response)
  decisions.append(decision)
 output['automatic_adjudications']=decisions
 output['bounded_review_responses']=dict(dense=[dict(source_span_id=sid,response=deepcopy(response)) for sid,response in dense_responses.items()],
     objects=[dict(source_span_id=sid,response=deepcopy(response)) for sid,response in (object_responses or {}).items()],
     cells=[dict(table_index=ti,cell_index=ci,arm_index=ai,span_index=si,response=deepcopy(response)) for (ti,ci),responses in cell_responses.items() for (ai,si),response in responses.items()],
     regions=[dict(target=deepcopy(target),response=deepcopy(response)) for target,response in region_responses or []])
 return output


def sequence_assessments(view,page,errors):
 from difflib import SequenceMatcher
 def candidate_scope_covered(issue,decision):
  from sequence_adjudication import punctuation_width_only
  candidate=issue.get('candidate_evidence',{});arms=candidate.get('arms')
  if not arms or candidate.get('span_id')!=decision.get('source_span_id'):return False
  applicability=issue.get('candidate_applicability',{}).get('arms',[])
  proof=decision.get('sequence_evidence',{}).get('verified',[])
  ranges=[(p['start'],p['end']) for p in proof]
  found=False
  for i,arm in enumerate(arms):
   if applicability and (i>=len(applicability) or not applicability[i].get('valid')):continue
   for op,a,b,c,d in SequenceMatcher(None,decision['original'],arm['text'],autojunk=False).get_opcodes():
    if op=='equal':continue
    found=True
    if punctuation_width_only(decision['original'][a:b],arm['text'][c:d]):continue
    if a==b:
     if (a,b) not in ranges:return False
    elif not all(any(lo<=position<hi for lo,hi in ranges) for position in range(a,b)):return False
  return found
 assessments=[]
 for e in errors:
  resolved=None
  for d in page.get('automatic_adjudications',[]):
   if not d.get('accepted'):continue
   if d['kind'] in ('dense','object') and e['code'] in ('ocr_candidate_conflict','ocr_quality') and e.get('source_span_id')==d['source_span_id']:
    scope=d.get('verification_scope',{})
    if e['code']=='ocr_candidate_conflict' and candidate_scope_covered(e,d):resolved=d
    elif d.get('input_evidence',{}).get('eligible') and scope.get('kind')=='whole_object':resolved=d
   if d['kind']=='cell' and d.get('resolved') and e['code']=='ocr_overlay_conflict' and e.get('table_index')==d['table_index'] and e.get('cell_index')==d['cell_index']:resolved=d
  if resolved:
   from sequence_adjudication import punctuation_width_only
   arms=e.get('candidate_evidence',{}).get('arms',[]) if isinstance(e.get('candidate_evidence'),dict) else []
   display_only=bool(arms) and all(punctuation_width_only(resolved.get('original',''),a.get('text','')) for a in arms)
   record=dict(code='diagnostic_presentation' if display_only else 'automatic_resolution',
      disposition='display_difference_only' if display_only else 'content_recovered' if resolved.get('recovered') else 'original_or_role_verified',
      original_diagnostic=deepcopy(e),evidence=deepcopy(resolved))
  else:
   record=None
   for d in page.get('automatic_adjudications',[]):
    if d.get('source_span_id')!=e.get('source_span_id') or not e.get('source_span_id') or e['code']!='ocr_candidate_conflict':continue
    sequence=d.get('sequence_evidence',{})
    if sequence.get('verified') or sequence.get('unresolved'):
     e['verified_local_edits']=deepcopy(sequence.get('verified',[]))
     e['remaining_competitions']=deepcopy(sequence.get('unresolved',[]))
     if sequence.get('unresolved'):
      e['reason']='仍需核对局部差异：'+'；'.join(' / '.join(repr(v) for v in dict.fromkeys([d['original'][g['start']:g['end']],*[v['fragment'] for v in g.get('variants',[])]])) for g in sequence['unresolved'])+'；其他已验证位置不重复核对'
  assessments.append((e,record))
 return assessments


def reconcile_adjudications(view,page,errors,observations):
 rows=sequence_assessments(view,page,errors)
 observations.extend(record for issue,record in rows if record)
 view['automatic_adjudications']=deepcopy(page.get('automatic_adjudications',[]))
 return [issue for issue,record in rows if not record]


def adjudication_observations(view,page,existing=()):
 remaining=list(existing);initial=len(remaining)
 # 采用的完整候选若缺少独立字符裁决，始终明确保留未决，不能被资格过滤隐藏。
 for d in page.get('automatic_adjudications',[]):
  if (d['kind']=='cell' and d.get('accepted') and not d.get('resolved')
      and ('original_body_text' not in d or d['original_body_text']!=d.get('result',{}).get('text_assembled'))):
   ti,ci=d['table_index'],d['cell_index']
   if not any(e.get('table_index')==ti and e.get('cell_index')==ci and e['code']=='ocr_overlay_conflict' for e in remaining):
    c=view['tables'][ti]['cells'][ci];remaining.append(dict(code='ocr_overlay_conflict',stage='bounded_adoption',reason='完整候选恢复了未覆盖正文，但独立字符核验仍不确定',table_index=ti,cell_index=ci,bbox_pdf=c['bbox_pdf'],text=c['text'],adoption_evidence=deepcopy(d)))
 for d in page.get('automatic_adjudications',[]):
  if d['kind']=='region' and not d.get('accepted') and d.get('response',{}).get('text','')!=d.get('original',''):
   target=d['target'];poly=target['polygon_page']
   remaining.append(dict(code='ocr_coverage',stage='bounded_region_verification',reason='完整标题区域仍有未识别或叠印遮挡文字；辅助候选未获完整证据，不按模板补齐',
                         bbox_pdf=[min(x for x,y in poly),min(y for x,y in poly),max(x for x,y in poly),max(y for x,y in poly)],
                         source_span_ids=target['source_span_ids'],text=d['original'],region_id=target['span_id'],adjudication_evidence=deepcopy(d)))
 return remaining[initial:]


def recover_uncovered_regions(page,engine,run,limit=6):
 from product_consumer import consumer_page
 view=consumer_page(page);output=deepcopy(page);attempts=[];seen=set()
 issues=list(view['errors'])
 for entry in page.get('regional_recognition',[]):
  result=entry.get('result',{})
  if result.get('status')=='completed' and not result.get('text_assembled','').strip() and not entry.get('bounded_recovery'):
   issues.append(dict(code='ocr_coverage',region_id=entry['region']['region_id']))
 for issue in issues:
  if issue.get('code')!='ocr_coverage' or len(attempts)>=limit:continue
  rid=issue.get('region_id')
  entry=next((e for e in page.get('regional_recognition',[]) if e['region']['region_id']==rid),None)
  if not entry or rid in seen:continue
  seen.add(rid);source=entry.get('result',{}).get('input',{})
  if not source.get('image'):continue
  # 原尝试是纯行识别时，补一次完整区域检测；不重复已成功的同一推理策略。
  detection=entry.get('result',{}).get('internal',{}).get('detection')
  empty=not entry.get('result',{}).get('text_assembled','').strip()
  if detection is not False and not empty:continue
  sample={**source,'sample_id':source.get('sample_id','region')+'-bounded-detection','granularity':'block','stage':'bounded_region_detection'}
  with Image.open(source['image']) as im:
   if detection is not False and im.height>=128:continue
   if im.height<128:
    enlarged=Path(run)/('complete-region-'+str(len(attempts))+'.png');im.resize((im.width*2,im.height*2),Image.Resampling.LANCZOS).save(enlarged)
    matrix=deepcopy(source['input_to_page'])
    for row in matrix[:2]:row[0]/=2;row[1]/=2
    sample.update(image=str(enlarged),input_to_page=matrix)
  result=engine.predict(sample,60);new={**deepcopy(entry),'result':result,'bounded_recovery':True}
  output.setdefault('regional_recognition',[]).append(new);attempts.append(new)
 output['bounded_recovery_attempts']=attempts
 return output


def complete_weak_value_competition(primary,source_id,response,decision,engine,auxiliary):
 """保留值支持不足时，用已解码N-best完成有限竞争，不改阈值或添加答案。"""
 from difflib import SequenceMatcher
 from sequence_adjudication import decide_sequence
 whole=decision.get('whole_sequence_review') or {}
 if not decision.get('accepted') or whole.get('complete') or not decision.get('input_evidence',{}).get('eligible'):return response,decision
 text=decision['text'];tokens=whole.get('selected_alignment') or [];main=whole.get('primary_selected_alignment') or []
 if len(tokens)!=len(text):return response,decision
 weak={i for i,t in enumerate(tokens) if max(t.get('posterior',0),main[i].get('posterior',0) if len(main)==len(tokens) else 0)<.97}
 if not weak:return response,decision
 candidates=[text]
 alternatives=[]
 for candidate in response.get('sequence_evidence',{}).get('nbest',[]):
  edits=[(op,a,b) for op,a,b,c,d in SequenceMatcher(None,text,candidate,autojunk=False).get_opcodes() if op!='equal']
  if edits and all((set(range(a,b)).issubset(weak) if b>a else a in weak or a-1 in weak) for op,a,b in edits):alternatives.append(candidate)
 if not alternatives:return response,decision
 candidates=list(dict.fromkeys([*candidates,*alternatives]))
 revised=auxiliary.predict(response['image'],candidates=candidates,geometry=response['source_geometry'])
 complete_sequence_reviews(primary,[(source_id+'-retained-value',revised)],engine)
 resolved=decide_sequence(text,candidates[1:],revised,revised['source_geometry'])
 revised['retained_value_review']=dict(reason='retained_positions_below_existing_full_sequence_support',
      positions=sorted(weak),candidates=candidates,previous_reason=decision['reason'])
 return revised,resolved


def recover_missing_field_values(page,engine,auxiliary,run,source_span_ids=None):
 """完整标签行定位值，值和标签分别核验；任何部分未通过均保留对应风险。"""
 from assembly import field
 from field_coverage import field_value_geometry,field_missing_issues
 from product_consumer import consumer_page,score_evidence
 from sequence_adjudication import decide_sequence
 cached_reviews={r['source_span_id']:r for r in page.get('field_value_reviews',[])}
 view=consumer_page({**page,'field_value_reviews':[]});primary=page.get('original_page_result',page['page_result'])
 missing={e['source_span_id']:e for e in field_missing_issues(view,page)}
 reviews=[];directory=Path(run);directory.mkdir(parents=True,exist_ok=True)
 for index,source in enumerate(primary.get('spans',[])):
  if source_span_ids is not None and source['span_id'] not in source_span_ids:continue
  label,value=field(source['text_raw'])
  if not label or source.get('human_revision'):continue
  score=score_evidence(source)
  if value and score['score_available'] and score['score']>=score['threshold']:continue
  observed=field_value_geometry(primary,source)
  if not observed:continue
  element=next((e for e in view['readable_elements'] if source['span_id'] in e.get('source_span_ids',[])),None)
  if not element or element.get('human_revision'):continue
  review=dict(source_span_id=source['span_id'],region_id=element['region_id'],label=label,
      original=source['text_raw'],accepted=False,reason='value_unverified',field_geometry=observed)
  if source['span_id'] in missing:review['issue']=deepcopy(missing[source['span_id']])
  reviews.append(review)
  try:
   previous=cached_reviews.get(source['span_id'],{})
   cached_result=previous.get('result',{});cached_bytes=None
   try:cached_bytes=Path(cached_result['input']['image']).read_bytes()
   except (KeyError,OSError):pass
   target={**source,'polygon_page':observed['polygon_page']}
   path=directory/f'value-{index}.png';geometry=field_value_input(primary,target,path)
   matrix=deepcopy(geometry['source_input']['input_to_page']);q=geometry['source_bbox_px'];scale=geometry.get('scale',1);pad=geometry['padding_px']/scale
   for row in matrix[:2]:
    row[2]+=row[0]*(q[0]-pad)+row[1]*(q[1]-pad);row[0]/=scale;row[1]/=scale
   reusable=(cached_result.get('status')=='completed' and cached_bytes==path.read_bytes()
             and cached_result.get('input',{}).get('input_to_page')==matrix)
   result=deepcopy(cached_result) if reusable else engine.predict(dict(sample_id=primary['sample_id']+f'-value-{index}',image=str(path),input_to_page=matrix,
       granularity='line-rec',stage='complete_field_value'),60)
   review['result']=result
   if result.get('status')!='completed' or len(result.get('spans',[]))!=1:
    review['reason']='value_recognition_failed';continue
   span=deepcopy(result['spans'][0]);span.update(polygon_page=observed['polygon_page'],
       polygon_input_px=geometry['target_polygon_input_px'],coordinate_source='connected_glyphs')
   if not span.get('text_raw','').strip():review['reason']='value_recognition_empty';continue
   candidates=list(dict.fromkeys(([value] if value else [])+[span['text_raw']]))
   prior_response=previous.get('response',{})
   same_auxiliary_pixels=False
   try:same_auxiliary_pixels=Path(prior_response['image']).read_bytes()==path.read_bytes()
   except (KeyError,OSError):pass
   if reusable and same_auxiliary_pixels and prior_response.get('candidate_texts')==candidates:
    response=deepcopy(prior_response);response.update(image=str(path),source_geometry=geometry)
    supporting=response.get('primary_sequence_review')
    if supporting and supporting.get('image')==prior_response.get('image'):
     supporting.update(image=str(path),source_geometry=deepcopy(geometry))
   else:response=auxiliary.predict(path,candidates=candidates,geometry=geometry)
   review.update(span=span,response=response)
   complete_sequence_reviews(primary,[(source['span_id'],response)],engine)
   decision=decide_sequence(candidates[0],candidates[1:],response,geometry)
   response,decision=complete_weak_value_competition(primary,source['span_id'],response,decision,engine,auxiliary)
   review.update(decision=decision,reason=decision['reason'])
   if not decision.get('whole_sequence_review',{}).get('complete'):continue
   review.update(text=decision['text'],reason='value_verified_label_geometry_pending')
   # 分隔符位置也必须经完整标签核验；字形中的两点不能误充冒号，把标签尾字当值。
   if decision.get('whole_sequence_review',{}).get('complete'):
    polygon=observed['label_polygon_page']
    label_span={**source,'polygon_page':polygon};label_path=directory/f'label-{index}.png'
    label_geometry=field_value_input(primary,label_span,label_path)
    prior_label=previous.get('label_response',{})
    label_response=(deepcopy(prior_label) if reusable and prior_label.get('source_geometry')==label_geometry
        else auxiliary.predict(label_path,candidates=[label+'：'],geometry=label_geometry))
    complete_sequence_reviews(primary,[(source['span_id']+'-label',label_response)],engine)
    label_decision=decide_sequence(label+'：',[],label_response,label_geometry)
    review.update(label_response=label_response,label_decision=label_decision,
        label_verified=label_decision.get('whole_sequence_review',{}).get('complete',False) and
        label_decision['text'].replace(':','：')==label+'：')
    if review['label_verified']:review.update(accepted=True,reason='same_field_label_and_complete_value_verified')
  except Exception as exc:review.update(reason='optional_field_review_failed',failure=repr(exc),accepted=False)
 page['field_value_reviews']=([r for r in page.get('field_value_reviews',[]) if r['source_span_id'] not in source_span_ids]
     if source_span_ids is not None else [])+reviews
 return page


def run_bounded_review(page,model_dir,run,max_requests=64,engine=None):
 from auxiliary_recognition import AuxiliaryRecognizer
 requests=0;dense={};cells={};objects={};regions=[];run=Path(run);run.mkdir(parents=True,exist_ok=True)
 if engine is not None:page=recover_uncovered_regions(page,engine,run)
 model=AuxiliaryRecognizer(model_dir)
 try:
  if engine is not None:recover_missing_field_values(page,engine,model,run/'field-values')
  if engine is not None:recover_uncovered_field_lines(page,engine,model,run/'field-lines')
  primary=page.get('original_page_result',page['page_result']);byid={s['span_id']:s for s in primary['spans']}
  effective=adopt_dense_responses(replay_adoptions(page));current_byid={s['span_id']:s for s in effective['page_result']['spans']}
  for candidate in page.get('dense_candidates',[]):
   if requests>=max_requests:break
   span=byid.get(candidate['span_id'])
   if not span:continue
   current=current_byid.get(span['span_id'],span);arms=qualified_dense_arms(page,candidate,current)
   path=run/f'line-{requests}.png';geometry=dense_input(primary,span,path);response=model.predict(path,candidates=[current['text_raw']]+[a['text'] for a in arms],geometry=geometry);dense[span['span_id']]=response;requests+=1
  from product_consumer import consumer_page
  for issue in consumer_page(page)['errors']:
   sid=issue.get('source_span_id');span=byid.get(sid);owner=primary
   if span is None and type(issue.get('table_index')) is int:
    cell=page['tables'][issue['table_index']]['cells'][issue['cell_index']];owner=cell.get('original_result',cell['result'])
    span=next((s for s in owner.get('spans',[]) if s['span_id']==sid),None)
   if issue['code']!='ocr_quality' or not span or sid in dense or requests>=max_requests:continue
   path=run/f'object-{requests}.png';geometry=object_input(owner,span,path)
   independent_overlay=issue.get('content_role')=='independent_overlay' or span.get('visual_role')=='red_overlay'
   if type(issue.get('table_index')) is int:
    cell=page['tables'][issue['table_index']]['cells'][issue['cell_index']]
    independent_overlay=independent_overlay or any(e.get('span_id')==sid and e.get('role')=='red_overlay'
                                                   for e in cell.get('role_evidence',[]))
   # 已有独立章角色只进入完整章路径；不再对半个章字做普通横排识别。
   if independent_overlay:continue
   objects[sid]=model.predict(path,candidates=[span['text_raw']],geometry=geometry);requests+=1
  for target in title_targets(page):
   if requests>=max_requests:break
   path=run/f'region-{requests}.png';geometry=title_input(page,target,path)
   response=model.predict(path,candidates=[target['text_raw']],geometry=geometry)
   if engine is not None:
    q=geometry['source_bbox_px'];pad=geometry['padding_px'];matrix=deepcopy(geometry['source_input']['input_to_page'])
    for row in matrix[:2]:row[2]+=row[0]*(q[0]-pad)+row[1]*(q[1]-pad)
    response['primary_candidate']=engine.predict(dict(sample_id=primary.get('sample_id','page')+'-'+target['span_id'].replace(':','-')+'-bounded',image=str(path),input_to_page=matrix,granularity='line-rec',stage='bounded_title_recovery'),60)
   regions.append((target,response));requests+=1
  for ti,t in enumerate(page['tables']):
   for ci,c in enumerate(t['cells']):
    responses={}
    original=c.get('original_result',c['result'])
    body_ids={s['span_id'] for s in c['result'].get('body_spans',c['result'].get('spans',[]))}
    # 已有混章/候选触发才复核原图正文，不扩展为所有成功单元格的重复识别。
    sources=[(-1,original),*enumerate(c['content_candidates'])] if c.get('content_candidates') else []
    for ai,candidate in sources:
     for si,obj in enumerate(candidate_objects(candidate)):
      if not obj['eligible'] or requests>=max_requests:continue
      if ai==-1 and obj['span']['span_id'] not in body_ids:continue
      path=run/f'cell-{requests}.png'
      geometry,texts=(cell_line_input(c,obj['span'],path) if ai==-1 else complete_cell_line_input(c,obj['span'],path,candidate))
      response=model.predict(path,candidates=texts,geometry=geometry)
      responses[(ai,si)]={**response,'sequence_response':deepcopy(response)};requests+=1
    cells[(ti,ci)]=responses
 finally:model.close()
 if engine is not None:
  scoring_objects=[*dense.items(),*objects.items(),*((target['span_id'],response) for target,response in regions)]
  scoring_objects.extend((f'cell-{ti}-{ci}-{ai}-{si}',response['sequence_response'])
      for (ti,ci),entries in cells.items() for (ai,si),response in entries.items() if response.get('sequence_response'))
  complete_sequence_reviews(primary,scoring_objects,engine)
  requests+=complete_final_sequences(primary,scoring_objects,engine,model_dir)
  try:requests+=review_unresolved_cell_overlays(page,cells,model_dir,run,engine,max(0,max_requests-requests))
  except Exception as exc:page['optional_original_color_review_failure']=dict(reason=repr(exc),main_result_preserved=True)
 output=apply_review(page,dense,cells,objects,regions);output['bounded_review_resource']=dict(requests=requests,request_limit=max_requests,iterations=1)
 return output
