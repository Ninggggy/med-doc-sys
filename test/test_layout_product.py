"""行为回归：两栏与续行、修订隔离、诊断证据；不把合成数据当真实 OCR 验收。"""
import copy
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ocr_service/paddle_runtime'))
from assembly import assemble_chunk
from product_consumer import score_evidence
from agent.agent_backend.services.filing_parse_outcome import outcome
from agent.agent_backend.services.filing_parse_readiness import source_issues,readiness_result
from agent.agent_backend.services.filing_review_targets import review_targets,apply_targets
from agent.agent_backend.services.filing_parse_resolution import apply_resolutions
from agent.agent_backend.services.filing_change_extraction_service import source_lines

class LayoutProductTest(unittest.TestCase):
 def page(self):
  lines=[dict(text='生产范围：片剂（仅限',bbox=[0,0,80,10]),dict(text='住所：测试大道',bbox=[120,0,200,10]),
         dict(text='外用），不得口服。',bbox=[0,12,80,22]),dict(text='1号2层',bbox=[120,12,200,22])]
  p=dict(page=1,status='success',lines=lines,words=copy.deepcopy(lines),raw_text='原始行',tables=[],
    layout_regions=[dict(region_id='left',order=1,label='text',bbox_pdf=[0,0,85,25]),
                    dict(region_id='right',order=2,label='text',bbox_pdf=[115,0,210,25])])
  return assemble_chunk(p)
 def test_reading_order_and_field_value(self):
  p=self.page();self.assertEqual(p['text'],'生产范围：片剂（仅限外用），不得口服。\n\n住所：测试大道1号2层')
  self.assertEqual(p['raw_text'],'原始行')
  self.assertEqual(list(source_lines([p]))[0][1]['region_id'],'left')
 def test_line_edit_preserves_other_region_and_raw(self):
  p=self.page();before=copy.deepcopy(p);targets=review_targets({'doc_id':'doc'},'submission',[p]);t=targets[0]
  item=dict(issue_key=t['issue_key'],action='edit_value',reason='合成修订检查',source_value=t['value'],text='生产范围：乳膏（仅限',verified_value='生产范围：乳膏（仅限')
  updated,_,_=apply_targets([p],targets,[item]);q=updated[0]
  self.assertEqual(q['readable_elements'][1],p['readable_elements'][1]);self.assertEqual(q['original_raw_text'],p['raw_text']);self.assertEqual(q['raw_text'],q['text']);self.assertEqual(p,before)
  self.assertTrue(q['text'].startswith('生产范围：乳膏（仅限外用）'))
  self.assertEqual(assemble_chunk(copy.deepcopy(q))['text'],q['text'])
 def test_resolution_uses_identical_region_assembler(self):
  p=self.page();issue=dict(issue_key='problem',code='ocr_quality',chunk_index=0,page=1,bbox_pdf=[0,0,85,25])
  updated,_,_=apply_resolutions([p],[issue],[dict(issue_key='problem',action='correct_text',reason='合成正文修订',text='生产范围：仅限外用')])
  self.assertEqual(updated[0]['readable_elements'][1],p['readable_elements'][1]);self.assertEqual(updated[0]['original_raw_text'],'原始行');self.assertEqual(updated[0]['raw_text'],updated[0]['text'])
 def test_score_types_units_missing(self):
  for score in [None,True,'0.7',float('nan'),float('inf'),70,-1]:
   self.assertFalse(score_evidence(dict(score_raw=score,score_kind='paddle_rec_score'))['score_available'])
  self.assertEqual(score_evidence(dict(score_raw=.7,score_kind='paddle_rec_score'))['score'],.7)
 def test_specific_reason_evidence_and_distinct_positions(self):
  e=dict(code='ocr_candidate_conflict',reason='两次候选文字不同',bbox_pdf=[1,2,3,4],text='仅限外用',score=.98,score_available=True,candidate_evidence={'a':'仅限','b':'不限'})
  p=dict(page=1,text='生产范围：仅限外用',errors=[e,{**e,'bbox_pdf':[10,20,30,40]}]);r=outcome([p]);issues=source_issues(r,'submission',[p])
  self.assertEqual(len(issues),2);self.assertEqual(p['errors'][0]['reason'],'两次候选文字不同');self.assertEqual(issues[0]['candidate_evidence'],e['candidate_evidence']);self.assertIn('仅限外用',issues[0]['message'])
 def test_real_risk_cannot_be_marked_nonblocking(self):
  r=readiness_result([dict(code='ocr_coverage',blocking=False),dict(code='layout_observation')]);self.assertEqual(r['blocking_count'],1);self.assertEqual(len(r['diagnostic_observations']),1)
 def test_empty_text_region_is_not_success(self):
  p=self.page();p['layout_regions'].append(dict(region_id='missing',order=3,label='text',bbox_pdf=[0,50,50,60]));assemble_chunk(p)
  self.assertEqual(p['assembly_issues'][0]['code'],'ocr_coverage')

if __name__=='__main__':unittest.main()
