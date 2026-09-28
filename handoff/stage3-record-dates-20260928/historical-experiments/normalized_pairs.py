# HISTORICAL ONLY: local paths removed; use ../verify_evidence.py for offline replay.
import json,sys,time,numpy as np
from pathlib import Path
from PIL import Image
sys.path.insert(0,str(Path.cwd()/'ocr_service/paddle_runtime'))
from auxiliary_recognition import AuxiliaryRecognizer
from pipeline import Engine
r=Path('[LOCAL_PATH_OMITTED]')
if __name__=='__main__':
 rows=json.load(open(r/'group-responses.json'));g=json.load(open(r/'input.json'));im=Image.open(g['image']);pairs=[];start=time.monotonic()
 for i,unit in enumerate(rows):
  if not i or unit['response']['text'] not in ('年','月','日'):continue
  components=[rows[i-1],unit];target=max(z['geometry']['component_bbox_px'][3]-z['geometry']['component_bbox_px'][1] for z in components);patches=[];transforms=[]
  for z in components:
   b=z['geometry']['component_bbox_px'];margin=round((b[3]-b[1])*.12);q=[max(0,b[0]-margin),max(0,b[1]-margin),min(im.width,b[2]+margin),min(im.height,b[3]+margin)];s=target/(b[3]-b[1]);piece=im.crop(q);new=(round(piece.width*s),round(piece.height*s));patches.append(piece.resize(new,Image.Resampling.LANCZOS));transforms.append(dict(source_bbox_px=q,scale_x=new[0]/piece.width,scale_y=new[1]/piece.height))
  # The source gap is explicitly retained; no synthetic character or pixel filling.
  gap=components[1]['geometry']['component_bbox_px'][0]-components[0]['geometry']['component_bbox_px'][2];gap=max(0,gap);canvas=Image.new('RGB',(sum(p.width for p in patches)+gap,max(p.height for p in patches)),(255,255,255));x=0
  for p,t in zip(patches,transforms):canvas.paste(p,(x,0));t['output_origin_px']=[x,0];x+=p.width+gap
  path=r/f'normalized-pair-{i}.png';canvas.save(path);gg=dict(image=str(path),source_input=g,component_transforms=transforms,geometry_origin='observed_components_independent_height_normalization',review_scope='cell_body',preprocessing='original_component_resampling');pairs.append(dict(unit=unit['response']['text'],geometry=gg))
 m=AuxiliaryRecognizer('[LOCAL_PATH_OMITTED]')
 try:
  for p in pairs:p['auxiliary']=m.predict(p['geometry']['image'],[],p['geometry'])
 finally:m.close()
 cfg=json.load(open('[LOCAL_PATH_OMITTED]'));run=r/'normalized-runtime';run.mkdir();e=Engine('paddle',cfg,run)
 try:
  for i,p in enumerate(pairs):p['primary']=e.predict(dict(sample_id=f'normalized-date-{i}',image=p['geometry']['image'],granularity='line-rec',stage='finite_sequence_scoring',sequence_candidates=[p['auxiliary']['text']],input_to_page=[[1,0,0],[0,1,0],[0,0,1]]),60)
 finally:e.close()
 (r/'normalized-responses.json').write_text(json.dumps(pairs,ensure_ascii=False,indent=2));print([(p['unit'],p['auxiliary']['text'],p['primary']['text_assembled']) for p in pairs]);print('seconds',time.monotonic()-start)
