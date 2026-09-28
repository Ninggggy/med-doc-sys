"""Offline only: no model, HTTP client, database or original cache required."""
import gzip,json,os,sys
from pathlib import Path
root=Path(__file__).resolve().parent
sys.path.insert(0,str(root/'source/ocr_service/paddle_runtime'))
from sequence_adjudication import decide_sequence
os.chdir(root/'evidence')
with gzip.open('final-complete-score.json.gz','rt',encoding='utf8') as f:entry=json.load(f)
a=entry['response'];decision=decide_sequence(entry['candidates'][0],entry['candidates'][1:],a,a['source_geometry'])
assert decision['accepted'] is False
assert decision['reason']=='remaining_local_competition_uncertain',decision['reason']
assert decision['input_evidence']['eligible'] is True
assert all(x['reason']=='finite_local_competition_uncertain' for x in decision['sequence_evidence']['unresolved'])
for n,sid,issue in [(2,'fcs_edd7103be1f64131','chunk:4:error:0'),(3,'fcs_1b65ebf94df84d60','chunk:4:error:1')]:
 with gzip.open(f'doc{n}-record.json.gz','rt',encoding='utf8') as f:record=json.load(f)
 assert record['document_id']==sid and record['issue_key']==issue
 assert any(x['doc_id']==sid and x['issue_key']==issue for x in record['final_api_issues'])
 assert Path(record['native_record_input']['image']).is_file()
summary=json.load(open('final-api-status.json'))
assert sum(x['issues'] for x in summary)==42 and sum(x['objects'] for x in summary)==26
print(json.dumps(dict(offline_replay='passed',product_stage='NOT_COMPLETED',issues=42,objects=26,day_decision=decision['reason'],new_ocr_calls=0),ensure_ascii=False,indent=2))
