# HISTORICAL ONLY: local paths removed; use ../verify_evidence.py for offline replay.
import json,sys,time
from pathlib import Path
from PIL import Image
sys.path.insert(0,str(Path.cwd()/'ocr_service/paddle_runtime'))
from auxiliary_recognition import AuxiliaryRecognizer
from pipeline import Engine
r=Path('[LOCAL_PATH_OMITTED]')
if __name__=='__main__':
 rows=json.load(open(r/'group-responses.json'));parts=[];origins=[]
 # Actual recognized printed unit anchors; no predicted digit inserted.
 for i,z in enumerate(rows):
  if i and z['response']['text'] in ('年','月','日'):
   p=Image.open(rows[i-1]['geometry']['image']);parts.append(p.copy());origins.append(rows[i-1]['geometry'])
 h=max(p.height for p in parts);gap=round(h*.5);canvas=Image.new('RGB',(sum(p.width for p in parts)+gap*(len(parts)-1),h),'white');x=0
 for p,g in zip(parts,origins):canvas.paste(p,(x,0));g['output_origin_px']=[x,0];x+=p.width+gap
 path=r/'value-line.png';canvas.save(path);g=dict(method='actual_value_objects_with_unit_roles_separate',components=origins,image=str(path))
 m=AuxiliaryRecognizer('[LOCAL_PATH_OMITTED]')
 try:a=m.predict(path,[],g)
 finally:m.close()
 cfg=json.load(open('[LOCAL_PATH_OMITTED]'));run=r/'value-runtime';run.mkdir();e=Engine('paddle',cfg,run)
 try:b=e.predict(dict(sample_id='actual-value-objects',image=str(path),granularity='line-rec',stage='finite_sequence_scoring',sequence_candidates=[a['text']],input_to_page=[[1,0,0],[0,1,0],[0,0,1]]),60)
 finally:e.close()
 (r/'value-response.json').write_text(json.dumps(dict(geometry=g,auxiliary=a,primary=b),ensure_ascii=False,indent=2));print(a['text'],b['text_assembled'])
