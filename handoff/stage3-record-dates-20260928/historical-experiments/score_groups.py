# HISTORICAL ONLY: local paths removed; use ../verify_evidence.py for offline replay.
import json,sys,time
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'ocr_service/paddle_runtime'))
from pipeline import Engine
from auxiliary_recognition import AuxiliaryRecognizer
root=Path('[LOCAL_PATH_OMITTED]')
if __name__=='__main__':
 records=json.load(open(root/'group-responses.json'));cfg=json.load(open('[LOCAL_PATH_OMITTED]'));run=root/'group-main-runtime';run.mkdir();engine=Engine('paddle',cfg,run)
 try:
  for i,r in enumerate(records):
   a=r['response'];r['primary']=engine.predict(dict(sample_id=f'date-group-{i}',image=a['image'],granularity='line-rec',stage='finite_sequence_scoring',sequence_candidates=[a['text']],input_to_page=r['geometry']['input_to_page']),60)
 finally:engine.close()
 (root/'group-main-responses.json').write_text(json.dumps(records,ensure_ascii=False,indent=2))
 print(json.dumps([dict(i=r['index'],aux=r['response']['text'],main=r['primary'].get('text_assembled'),scores=[(x.get('text'),x.get('log_probability')) for x in r['primary'].get('sequence_evidence',{}).get('scores',[])]) for r in records],ensure_ascii=False))
