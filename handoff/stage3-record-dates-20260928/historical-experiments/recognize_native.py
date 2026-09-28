# HISTORICAL ONLY: local paths removed; use ../verify_evidence.py for offline replay.
import json,sys,time,resource
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'ocr_service/paddle_runtime'))
from auxiliary_recognition import AuxiliaryRecognizer
from pipeline import Engine
from bounded_review import complete_sequence_reviews
root=Path('[LOCAL_PATH_OMITTED]')
if __name__=='__main__':
 start=time.perf_counter();g=json.loads((root/'input.json').read_text())
 # Existing same-page actual full-row decodes. No manually generated answer.
 texts=['2025年8月','2025年3月6日']
 m=AuxiliaryRecognizer('[LOCAL_PATH_OMITTED]')
 try:a=m.predict(g['image'],texts,g)
 finally:m.close()
 config=json.load(open('[LOCAL_PATH_OMITTED]'))
 run=root/'native-runtime';run.mkdir(exist_ok=False);e=Engine('paddle',config,run)
 try:complete_sequence_reviews({},[('date-native',a)],e)
 finally:e.close()
 (root/'native-response.json').write_text(json.dumps(a,ensure_ascii=False,indent=2))
 print(json.dumps({'aux':a['text'],'score':a['line_score'],'main':a.get('primary_sequence_review',{}).get('text'),'main_status':a.get('primary_sequence_review',{}).get('status'),'seconds':time.perf_counter()-start,'peak_parent':resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,'peak_engine':e.peak},ensure_ascii=False))
