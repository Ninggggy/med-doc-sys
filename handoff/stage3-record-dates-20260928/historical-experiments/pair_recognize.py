# HISTORICAL ONLY: local paths removed; use ../verify_evidence.py for offline replay.
import json,sys,time,numpy as np
from pathlib import Path
from PIL import Image
sys.path.insert(0,str(Path.cwd()/'ocr_service/paddle_runtime'))
from auxiliary_recognition import AuxiliaryRecognizer
from pipeline import Engine
root=Path('[LOCAL_PATH_OMITTED]')
if __name__=='__main__':
 start=time.monotonic();g=json.load(open(root/'input.json'));records=json.load(open(root/'group-responses.json'));mat=np.array(g['input_to_page']);im=Image.open(g['image']);pairs=[]
 # Unit positions come from actual decoder outputs, never a date template value.
 for i,r in enumerate(records):
  if r['response']['text'] not in ('年','月','日') or not i:continue
  left=records[i-1];a=left['geometry']['component_bbox_px'];b=r['geometry']['component_bbox_px'];bb=[min(a[0],b[0]),min(a[1],b[1]),max(a[2],b[2]),max(a[3],b[3])];pad=round((bb[3]-bb[1])*.12);q=[max(0,bb[0]-pad),max(0,bb[1]-pad),min(im.width,bb[2]+pad),min(im.height,bb[3]+pad)];path=root/f'pair-{i}.png';im.crop(q).save(path)
  trans=mat@np.array([[1,0,q[0]],[0,1,q[1]],[0,0,1]])
  gg=dict(image=str(path),input_to_page=trans.tolist(),target_polygon_input_px=[[bb[0]-q[0],bb[1]-q[1]],[bb[2]-q[0],bb[1]-q[1]],[bb[2]-q[0],bb[3]-q[1]],[bb[0]-q[0],bb[3]-q[1]]],review_scope='cell_body',preprocessing='original',geometry_origin='adjacent_observed_component_and_decoded_unit',component_bbox_px=bb)
  pairs.append(dict(unit=r['response']['text'],geometry=gg))
 m=AuxiliaryRecognizer('[LOCAL_PATH_OMITTED]')
 try:
  for p in pairs:p['auxiliary']=m.predict(p['geometry']['image'],[],p['geometry'])
 finally:m.close()
 cfg=json.load(open('[LOCAL_PATH_OMITTED]'));run=root/'pair-runtime';run.mkdir();e=Engine('paddle',cfg,run)
 try:
  for i,p in enumerate(pairs):p['primary']=e.predict(dict(sample_id=f'date-pair-{i}',image=p['geometry']['image'],granularity='line-rec',stage='finite_sequence_scoring',sequence_candidates=[p['auxiliary']['text']],input_to_page=p['geometry']['input_to_page']),60)
 finally:e.close()
 (root/'pair-responses.json').write_text(json.dumps(pairs,ensure_ascii=False,indent=2))
 print(json.dumps([dict(unit=p['unit'],aux=p['auxiliary']['text'],main=p['primary'].get('text_assembled')) for p in pairs],ensure_ascii=False));print('seconds',time.monotonic()-start,'peak_engine',e.peak)
