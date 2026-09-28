# HISTORICAL ONLY: local paths removed; use ../verify_evidence.py for offline replay.
import json,sys,time,resource
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'ocr_service/paddle_runtime'))
from bounded_review import complete_sequence_reviews
from sequence_adjudication import decide_sequence
from pipeline import Engine
r=Path('[LOCAL_PATH_OMITTED]')
if __name__=='__main__':
 start=time.monotonic();v=json.load(open(r/'final-component-score.json'));a=v['response'];g=a['source_geometry'];g['source_input']=json.load(open(r/'input.json'));a.pop('primary_sequence_review',None)
 cfg=json.load(open('[LOCAL_PATH_OMITTED]'));run=r/'complete-score-runtime';run.mkdir();e=Engine('paddle',cfg,run)
 try:complete_sequence_reviews({},[('day',a)],e)
 finally:e.close()
 d=decide_sequence(v['candidates'][0],v['candidates'][1:],a,g);v.update(response=a,decision=d,additional_main_calls=1,additional_seconds=time.monotonic()-start,additional_engine_peak_rss_bytes=e.peak,additional_parent_peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
 (r/'final-complete-score.json').write_text(json.dumps(v,ensure_ascii=False,indent=2));print(d['reason']);print([(x.get('reason'),x.get('selected_fragment'),x.get('margins'),x.get('min_disputed_posterior')) for x in d['sequence_evidence']['unresolved']])
