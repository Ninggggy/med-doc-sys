# HISTORICAL ONLY: local paths removed; use ../verify_evidence.py for offline replay.
import json,sys,numpy as np,time,resource
from pathlib import Path
sys.path.insert(0,str(Path.cwd()/'ocr_service/paddle_runtime'))
from auxiliary_recognition import AuxiliaryRecognizer
from pipeline import Engine
from sequence_adjudication import decide_sequence
r=Path('[LOCAL_PATH_OMITTED]')
if __name__=='__main__':
 start=time.monotonic();rows=json.load(open(r/'group-main-responses.json'));z=rows[4];g=z['geometry'];g['review_scope']='cell_body';g['preprocessing']='original';inv=np.linalg.inv(np.array(g['input_to_page']));p=np.c_[np.array(g['target_polygon_page']),np.ones(4)]@inv.T;g['target_polygon_input_px']=(p[:,:2]/p[:,2:]).tolist();page=json.load(open('[LOCAL_PATH_OMITTED]'));regions=[]
 for region in page.get('stamp_regions',[]):
  if region.get('source')!='closed_red_circular_outline':continue
  p=np.c_[np.array(region['polygon_page']),np.ones(len(region['polygon_page']))]@inv.T;regions.append(dict(source=region['source'],polygon_input_px=(p[:,:2]/p[:,2:]).tolist(),polygon_page=region['polygon_page']))
 g['seal_overlay_role']=dict(source_page_input=page['page_result']['input'],regions=regions)
 # Bounded top decoded alternatives from unchanged actual input, no truth data.
 candidates=list(dict.fromkeys([z['primary']['text_assembled'],*z['primary']['sequence_evidence']['nbest'][:4],*z['response']['sequence_evidence']['nbest'][:4]]))
 m=AuxiliaryRecognizer('[LOCAL_PATH_OMITTED]')
 try:a=m.predict(g['image'],candidates,g)
 finally:m.close()
 cfg=json.load(open('[LOCAL_PATH_OMITTED]'));run=r/'final-score-runtime';run.mkdir();e=Engine('paddle',cfg,run)
 try:b=e.predict(dict(sample_id='day-component-complete-scoring',image=g['image'],granularity='line-rec',stage='finite_sequence_scoring',sequence_candidates=candidates,input_to_page=g['input_to_page']),60)
 finally:e.close()
 a['primary_sequence_review']=dict(status=b['status'],model='PP-OCRv6_medium_rec',image=g['image'],text=b['text_assembled'],source_geometry=g,sequence_evidence=b['sequence_evidence'],ctc=b.get('ctc',{}))
 d=decide_sequence(candidates[0],candidates[1:],a,g)
 out=dict(response=a,decision=d,candidates=candidates,seconds=time.monotonic()-start,engine_peak_rss_bytes=e.peak,parent_peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,instances_closed=True)
 (r/'final-component-score.json').write_text(json.dumps(out,ensure_ascii=False,indent=2));print(json.dumps(dict(candidates=candidates,accepted=d['accepted'],text=d['text'],reason=d['reason'],input=d['input_evidence'],unresolved=d.get('sequence_evidence',{}).get('unresolved')),ensure_ascii=False))
