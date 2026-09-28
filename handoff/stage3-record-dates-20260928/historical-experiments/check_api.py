# HISTORICAL ONLY: local paths removed; use ../verify_evidence.py for offline replay.
import json
from pathlib import Path
r=Path('[LOCAL_PATH_OMITTED]');out=Path('test/record_dates_20260928')
def pick(v,keys):return {k:v.get(k) for k in keys}
def issues(v):return [pick(i,['issue_key','doc_id','code','text','edit_target_key','source_span_id','bbox_pdf','resolved']) for i in v['issues']]
def tables(c):
 return [dict(**pick(t,['rows','raw_rows','bbox_pdf','table_index','id']),cells=[dict(**pick(x,['row','column','rowspan','colspan','bbox_pdf','text']),spans=[pick(s,['span_id','text_raw','polygon_page','covered_source_span_ids']) for s in x.get('result',{}).get('spans',[])]) for x in t.get('cells',[])]) for t in c.get('tables',[])]
def content(v,key):return [dict(**pick(c,['page','text','effective_text','raw_text']),tables=tables(c)) for c in v[key]]
def targets(v):return [pick(t,['doc_id','chunk_index','target_kind','element_index','region_id','source_span_ids','bbox_pdf','value','source_value','resolved']) for t in v['targets']]
result=[]
for name in ['doc1','doc2','doc3']:
 old=json.load(open(Path('[LOCAL_PATH_OMITTED]')/(name+'.json')));new=json.load(open(r/(name+'-final-api.json')))
 result.append(dict(document=name,original_content_table_values_and_span_sources_unchanged=content(old,'original_chunks')==content(new,'original_chunks'),effective_content_table_values_and_span_sources_unchanged=content(old,'effective_chunks')==content(new,'effective_chunks'),issue_identity_scope_and_text_unchanged=issues(old)==issues(new),target_values_and_source_ids_unchanged=targets(old)==targets(new),full_payload_equal=old==new,note='旧包与当前API的尝试记录和压缩证据不同；只比较列明内容投影，不宣称整个JSON相同'))
(out/'api-comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(result)
assert all(all(v for k,v in row.items() if k.endswith('_unchanged')) for row in result)
