"""CTC仅定位搜索邻域；裁决证据来自原图字形，不均分文字宽度。"""
import math
from functools import lru_cache
from difflib import SequenceMatcher
from PIL import Image,ImageOps
import numpy as np


def ink_mask(image):
 with Image.open(image) as im:rgb=np.asarray(im.convert('RGB'),dtype=float)
 return rgb.max(2)<200


def source_body_mask(metadata,size):
 """去章视图的边界检查仍使用原色角色，不能把变灰的红章残笔当成正文截字。"""
 if metadata.get('preprocessing')!='neutral_preserving' or not metadata.get('pixel_preservation',{}).get('eligible'):return None
 source=metadata.get('original_source_input',{});q=metadata.get('source_bbox_px')
 if not source.get('image') or not q or metadata.get('source_to_rectified'):return None
 scale=metadata.get('scale',1);pad=metadata.get('padding_px',0)/scale
 if int(pad)!=pad:return None
 with Image.open(source['image']) as im:
  crop=ImageOps.expand(im.convert('RGB').crop(q),int(pad),fill='white')
  rgb=np.asarray(crop,float)
 red=(rgb[:,:,0]-rgb[:,:,1]>35)&(rgb[:,:,0]-rgb[:,:,2]>35)
 mask=Image.fromarray((((rgb.max(2)<200)&~red)*255).astype('uint8'))
 if (int(mask.width*scale),int(mask.height*scale))!=size:return None
 return np.asarray(mask.resize(size,Image.Resampling.NEAREST))>0


def input_evidence(image,padding_px=0,metadata=None):
 metadata=metadata or {};padding_px=metadata.get('padding_px',padding_px)
 if metadata.get('source_target_complete') is False:return dict(eligible=False,reason='target_polygon_outside_real_input')
 ink=ink_mask(image);h,w=ink.shape;poly=metadata.get('target_polygon_input_px')
 body_mask=source_body_mask(metadata,(w,h))
 if body_mask is not None:ink &= body_mask
 if poly:
  from PIL import ImageDraw
  mask=Image.new('L',(w,h));ImageDraw.Draw(mask).polygon([tuple(p) for p in poly],fill=1)
  target=np.asarray(mask)>0
  focus=metadata.get('focus_interval')
  if focus:
   columns=np.arange(w);target=target&((columns>=focus[0])&(columns<=focus[1]))[None,:]
  selected=[];ignored=[]
  with Image.open(image) as im:rgb=np.asarray(im.convert('RGB'),dtype=float)
  colored=(rgb.min(2)<180)&target
  red=(rgb[:,:,0]-rgb[:,:,1]>40)&(rgb[:,:,0]-rgb[:,:,2]>40)&colored
  red_fraction=float(red.sum()/max(colored.sum(),1))
  overlay=None
  if red_fraction>.05 and metadata.get('role','body')=='body':
   # 红墨触发复核；同页实际闭合章轮廓只证明其角色，不证明章文或正文正确。
   # 未归属红墨仍拒绝。正文采用还必须经过完整序列与对象覆盖核验。
   role=metadata.get('seal_overlay_role',{})
   assigned=Image.new('L',(w,h));draw=ImageDraw.Draw(assigned)
   for region in role.get('regions',[]):
    if region.get('source')=='closed_red_circular_outline' and len(region.get('polygon_input_px',[]))>=3:
     draw.polygon([tuple(p) for p in region['polygon_input_px']],fill=1)
   covered=int((red&(np.asarray(assigned)>0)).sum());total=int(red.sum())
   if (metadata.get('review_scope')!='cell_body' or metadata.get('preprocessing')!='original'
       or not role.get('source_page_input') or total<20 or covered/max(total,1)<.95):
    return dict(eligible=False,reason='overlay_crosses_target_without_role_separation',red_ink_fraction=red_fraction)
   overlay=dict(reason='same_page_closed_seal_outline_covers_red_ink',red_pixels=total,
                assigned_red_pixels=covered,coverage=covered/total,seal_text_verified=False)
   ink &= ~red
   body_mask=ink
  seal_role=metadata.get('role') in ('seal','stamp')
  if seal_role:ink=red
  for c in mask_components(ink) if seal_role or body_mask is not None else connected_components(str(image)):
   x0,y0,x1,y1=c['box'];region=ink[y0:y1,x0:x1];inside=int((region&target[y0:y1,x0:x1]).sum())
   # 邻行残笔必须与目标多边形分离；不能因触边就否定完整目标。
   if inside/max(c['area'],1)>=.6:selected.append(c)
   else:ignored.append(c['box'])
  if not selected:return dict(eligible=False,reason='no_target_red_glyph' if seal_role else 'no_target_neutral_glyph',ignored_components=ignored)
  touching=[c for c in selected if c['box'][0]<=padding_px or c['box'][1]<=padding_px or c['box'][2]>=w-padding_px or c['box'][3]>=h-padding_px]
  neighbor_boundary=None
  if touching:
   neighbor_mask=Image.new('L',(w,h));draw=ImageDraw.Draw(neighbor_mask)
   for neighbor in metadata.get('detected_neighbors',[]):
    if neighbor.get('source_span_id')!=metadata.get('source_span_id') and neighbor.get('polygon_input_px'):
     draw.polygon([tuple(p) for p in neighbor['polygon_input_px']],fill=1)
   edge=np.zeros_like(ink)
   edge[:padding_px+1,:]=True;edge[max(0,h-padding_px-1):,:]=True
   edge[:,:padding_px+1]=True;edge[:,max(0,w-padding_px-1):]=True
   component_boxes=np.zeros_like(ink)
   for c in touching:
    x0,y0,x1,y1=c['box'];component_boxes[y0:y1,x0:x1]=True
   boundary=ink&edge&component_boxes
   # 整个连通域可同时含两行；仅当触边像素全部属于已检测邻行且不进入目标，才不算目标截断。
   if not boundary.any() or np.any(boundary&target) or np.any(boundary&~(np.asarray(neighbor_mask)>0)):
    return dict(eligible=False,reason='target_ink_touches_input_boundary',ignored_components=ignored)
   neighbor_boundary=dict(reason='boundary_ink_belongs_to_detected_neighbor_outside_target',pixels=int(boundary.sum()),
                          source_span_ids=[n['source_span_id'] for n in metadata.get('detected_neighbors',[])])
  # 未截取目标之外的行：检查多边形本身完整处于真实输入。
  if any(not(padding_px<=x<w-padding_px and padding_px<=y<h-padding_px) for x,y in poly):
   return dict(eligible=False,reason='target_polygon_outside_real_input')
  return dict(eligible=True,reason='complete_target_polygon_and_components',target_components=[c['box'] for c in selected],ignored_components=ignored,padding_px=padding_px,seal_overlay_role=overlay,
              **({'neighbor_boundary_evidence':neighbor_boundary} if neighbor_boundary else {}))
 if padding_px:ink=ink[padding_px:-padding_px,padding_px:-padding_px]
 ys,xs=np.nonzero(ink)
 if not len(xs):return dict(eligible=False,reason='no_neutral_glyph')
 if min(xs)<=1 or min(ys)<=1 or max(xs)>=ink.shape[1]-2 or max(ys)>=ink.shape[0]-2:return dict(eligible=False,reason='ink_touches_input_boundary')
 rows=ink.sum(1);active=np.where(rows>max(2,ink.shape[1]*.008))[0];runs=[]
 for y in active:
  if not runs or y>runs[-1][-1]+2:runs.append([int(y)])
  else:runs[-1].append(int(y))
 significant=[v for v in runs if len(v)>=5]
 if len(significant)>1:return dict(eligible=False,reason='multiple_visible_rows')
 return dict(eligible=True,reason='complete_single_visible_row',ink_bbox=[int(min(xs)),int(min(ys)),int(max(xs)+1),int(max(ys)+1)])


@lru_cache(maxsize=256)
def connected_components(image):
 return mask_components(ink_mask(image))


def mask_components(ink):
 remaining=set(zip(*np.nonzero(ink)));components=[]
 while remaining:
  y,x=remaining.pop();stack=[(y,x)];pts=[]
  while stack:
   y,x=stack.pop();pts.append((int(y),int(x)))
   for q in [(y-1,x),(y+1,x),(y,x-1),(y,x+1)]:
    if q in remaining:remaining.remove(q);stack.append(q)
  if len(pts)>=3:components.append(dict(box=[min(x for y,x in pts),min(y for y,x in pts),max(x for y,x in pts)+1,max(y for y,x in pts)+1],area=len(pts)))
 return components


def glyph_evidence(response,index):
 tokens=response['ctc']['tokens'];w,h=response['input_size'];extent=max(w,h*320/48);step=extent/response['ctc']['timesteps'];token=tokens[index]
 center=(token['timestep']+.5)*step
 previous=(tokens[index-1]['timestep']+.5)*step if index else 0
 following=(tokens[index+1]['timestep']+.5)*step if index+1<len(tokens) else w
 a=(previous+center)/2;b=(center+following)/2;pitch=(following-previous)/2
 # 使用全输入连通域，窗口只选择连通域，绝不在窗口边缘截断笔画。
 components=[c for c in connected_components(response['image']) if a<sum(c['box'][::2])/2<b]
 if not components:return None
 components.sort(key=lambda c:c['box'][1]);kind=None
 if len(components)==2:
  up,down=[c['box'] for c in components];uh=up[3]-up[1];dh=down[3]-down[1]
  if max(up[2]-up[0],down[2]-down[0])<pitch*.6:
   if dh>=1.45*uh and dh>=3 and components[1]['area']>=1.4*components[0]['area']:kind='semicolon'
   elif max(dh,uh)<=1.4*min(dh,uh):kind='colon'
 elif len(components)==1:
  c=components[0]['box'];gw,gh=c[2]-c[0],c[3]-c[1]
  if gw>=2*gh and gw<pitch*.65:kind='short_horizontal_stroke'
 box=[min(c['box'][0] for c in components),min(c['box'][1] for c in components),max(c['box'][2] for c in components),max(c['box'][3] for c in components)]
 return dict(bbox_input_px=box,components=components,shape=kind,ctc_anchor=center,token=token,source='original_image_connected_ink',local_emission_pitch=pitch)


def decide_dense(primary,arms,response):
 if response.get('sequence_evidence'):
  from sequence_adjudication import decide_sequence
  return decide_sequence(primary,[a['text'] for a in arms],response,response.get('source_geometry',{}))
 evidence=input_evidence(response['image'],metadata=response.get('source_geometry',{}));decision=dict(accepted=False,recovered=False,text=primary,input_evidence=evidence,glyphs=[],reason='unresolved_character_evidence')
 if not evidence['eligible']:return decision
 # 只裁决有真实字形判别依据的冒号/分号。其他字符没有独立证据时不放行整条。
 allowed={':','：',';','；','-','一'};text=primary;tokens=response['ctc']['tokens'];aux=''.join(t['text'] for t in tokens)
 edits=[];unresolved=False
 disputed=set()
 for arm in arms:
  for op,a,b,c,d in SequenceMatcher(None,primary,arm['text'],autojunk=False).get_opcodes():
   if op!='equal':disputed.update(range(a,b))
 for op,a,b,c,d in SequenceMatcher(None,primary,aux,autojunk=False).get_opcodes():
  if op=='equal':continue
  if not any(i in disputed for i in range(a,b)):continue
  if op!='replace' or b-a!=d-c:unresolved=True;continue
  for pi,ai in zip(range(a,b),range(c,d)):
   if pi not in disputed:continue
   if primary[pi] not in allowed or aux[ai] not in allowed:unresolved=True;continue
   g=glyph_evidence(response,ai);decision['glyphs'].append(g)
   expected='short_horizontal_stroke' if aux[ai]=='-' else 'unproven_chinese_one' if aux[ai]=='一' else 'semicolon' if aux[ai] in ';；' else 'colon'
   if not g or g['shape']!=expected or tokens[ai]['posterior']<.8:unresolved=True;continue
   edits.append((pi,aux[ai]))
 for i,ch in edits:text=text[:i]+ch+text[i+1:]
 # 即使主文与辅助一致，也要逐个检验旧臂争议位置的实际笔画。
 for arm in arms:
  other=arm['text']
  for op,a,b,c,d in SequenceMatcher(None,text,other,autojunk=False).get_opcodes():
   if op=='equal':continue
   if op!='replace' or b-a!=d-c:unresolved=True;continue
   for i,j in zip(range(a,b),range(c,d)):
    if text[i] not in allowed or other[j] not in allowed:unresolved=True;continue
    # 只在辅助与当前前后完整对齐时使用CTC，避免错位映射。
    matches=[(x,y,z) for tag,x,y,z,end in SequenceMatcher(None,text,aux,autojunk=False).get_opcodes() if tag=='equal' and x<=i<y]
    if not matches:unresolved=True;continue
    x,y,z=matches[0];ai=z+i-x;g=glyph_evidence(response,ai);decision['glyphs'].append(g)
    expected='short_horizontal_stroke' if text[i]=='-' else 'unproven_chinese_one' if text[i]=='一' else 'semicolon' if text[i] in ';；' else 'colon'
    if not g or g['shape']!=expected or tokens[ai]['posterior']<.8:unresolved=True
 decision.update(text=text,accepted=not unresolved,reason='original_glyph_punctuation_verified' if not unresolved else 'remaining_substantive_or_unproven_difference',recovered=text!=primary)
 return decision
