# HISTORICAL ONLY: local paths removed; use ../verify_evidence.py for offline replay.
import json,sys,time,numpy as np
from pathlib import Path
from PIL import Image
sys.path.insert(0,str(Path.cwd()/'ocr_service/paddle_runtime'))
from auxiliary_recognition import AuxiliaryRecognizer
from bounded_review import complete_sequence_reviews
from pipeline import Engine
root=Path('[LOCAL_PATH_OMITTED]')
if __name__=='__main__':
 cc=json.load(open(root/'components.json'));g=json.load(open(root/'input.json'));mat=np.asarray(g['input_to_page'])
 # Match observed baseline of the original numeric detection. No date text supplied.
 numeric_box=[250.6666666667,217.3333333333,271.6666666667,227.0]
 inv=np.linalg.inv(mat);p=inv@np.array([numeric_box[0],numeric_box[1],1]);q=inv@np.array([numeric_box[2],numeric_box[3],1]);lo,hi=p[1],q[1]
 cc=[c for c in cc if lo<=(c['bbox'][1]+c['bbox'][3])/2<=hi and c['bbox'][3]-c['bbox'][1]>= (hi-lo)*.15]
 # Actual connected components, merged only across observed small natural gaps.
 gap=float(np.median([c['bbox'][3]-c['bbox'][1] for c in cc]));groups=[]
 for c in sorted(cc,key=lambda c:c['bbox'][0]):
  b=c['bbox']
  if groups and b[0]-groups[-1][2]<=gap:
   z=groups[-1];groups[-1]=[min(z[0],b[0]),min(z[1],b[1]),max(z[2],b[2]),max(z[3],b[3])]
  else:groups.append(b[:])
 im=Image.open(g['image']);records=[]
 for i,b in enumerate(groups):
  pad=max(4,round((b[3]-b[1])*.12));crop=[max(0,b[0]-pad),max(0,b[1]-pad),min(im.width,b[2]+pad),min(im.height,b[3]+pad)]
  path=root/f'group-{i}.png';im.crop(crop).save(path);trans=mat@np.array([[1,0,crop[0]],[0,1,crop[1]],[0,0,1]])
  poly=[(mat@np.array([x,y,1]))[:2].tolist() for x,y in [(b[0],b[1]),(b[2],b[1]),(b[2],b[3]),(b[0],b[3])]]
  gg=dict(image=str(path),input_to_page=trans.tolist(),target_polygon_page=poly,size=[crop[2]-crop[0],crop[3]-crop[1]],review_scope='record_component',geometry_origin='original_color_actual_connected_components',component_bbox_px=b)
  records.append(dict(index=i,geometry=gg))
 m=AuxiliaryRecognizer('[LOCAL_PATH_OMITTED]')
 try:
  for r in records:r['response']=m.predict(r['geometry']['image'],[],r['geometry'])
 finally:m.close()
 cfg=json.load(open('[LOCAL_PATH_OMITTED]'));run=root/'group-runtime';run.mkdir();e=Engine('paddle',cfg,run)
 try:complete_sequence_reviews({},[(str(r['index']),r['response']) for r in records],e)
 finally:e.close()
 (root/'group-responses.json').write_text(json.dumps(records,ensure_ascii=False,indent=2))
 print(json.dumps([dict(i=r['index'],bbox=r['geometry']['component_bbox_px'],aux=r['response']['text'],main=r['response'].get('primary_sequence_review',{}).get('text')) for r in records],ensure_ascii=False))
