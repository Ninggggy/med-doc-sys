"""完整对象范围、章候选与首屏证据投影；不以机制样例冒充真实识别。"""
import sys,copy,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ocr_service/paddle_runtime'))
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from sequence_adjudication import local_competitions,punctuation_width_only
from seal_review import select_seal_evidence,attach_authority_values,unresolved_seal_objects
import test_positive_adjudication as fixtures
from agent.agent_backend.services.filing_review_evidence import compact_review_evidence
class ObjectCompletion(unittest.TestCase):
 def test_equal_body_string_still_saves_role_projection(self):
  from unittest.mock import patch
  from text_roles import apply_cell_roles
  body={'span_id':'body','text_raw':'地址'};seal={'span_id':'seal','text_raw':'1010'}
  cell={'result':{'spans':[body,seal],'text_assembled':'地址'}}
  projected=dict(copy.deepcopy(cell['result']),body_spans=[body],role_evidence=[{'span_id':'seal','role':'red_overlay'}])
  with patch('text_roles.separate_roles',return_value=projected):apply_cell_roles(cell,[])
  self.assertEqual(cell['result']['body_spans'],[body]);self.assertEqual(len(cell['result']['spans']),2)
  self.assertEqual(cell['result']['text_assembled'],'地址')
 def test_body_adoption_keeps_independent_overlay_source(self):
  from unittest.mock import patch
  from text_roles import apply_cell_roles
  body={'span_id':'body','text_raw':'地址','polygon_page':[[0,0],[1,0],[1,1],[0,1]]}
  stamp={'span_id':'stamp','text_raw':'1010','score_raw':.4}
  cell={'result':{'spans':[body],'text_assembled':'地址'},'original_result':{'spans':[body,stamp],'input':{'image':'original.png'}},'role_evidence':[{'span_id':'stamp','role':'red_overlay'}]}
  original=copy.deepcopy(cell['original_result'])
  projected=dict(copy.deepcopy(cell['result']),body_spans=[body],role_evidence=[])
  with patch('text_roles.separate_roles',return_value=projected):apply_cell_roles(cell,[])
  self.assertEqual(cell['result']['text_assembled'],'地址')
  self.assertEqual([s['span_id'] for s in cell['result']['spans']],['body','stamp'])
  self.assertEqual(cell['result']['spans'][1]['visual_role'],'red_overlay')
  self.assertEqual(cell['original_result'],original)
 def test_partial_seal_cannot_complete_residual_object(self):
  element={'region_id':'same','layout_label':'seal','text':'剩余文字','source_span_ids':['left'],
           'source_lines':[{'source_span_id':'left','text':'剩余文字'}]}
  verified={'region_id':'same:seal:verified','label':'印章文字','text':'核实章文'}
  view={'readable_elements':[element,verified]}
  self.assertEqual(unresolved_seal_objects(view,[])[0][0],element)
  self.assertEqual(unresolved_seal_objects(view,[{'source_span_id':'left'}]),[])
 def test_independently_completed_body_is_not_polluted_by_seal(self):
  element={'region_id':'same','layout_label':'seal','text':'2031年5月8日','source_span_ids':['old','gap'],
           'source_lines':[{'source_span_id':'old','text':'2031年5'},
                           {'source_span_id':'gap','text':'月8日','completion_of_span_ids':['old'],'completion_evidence':{'visible_gap':[1,1,2,2]}}]}
  view={'readable_elements':[element,{'region_id':'same:seal:verified','label':'印章文字'}]}
  self.assertEqual(unresolved_seal_objects(view,[]),[])
 def test_display_width_never_equates_semantic_punctuation_or_units(self):
  self.assertTrue(punctuation_width_only('（','('))
  self.assertTrue(punctuation_width_only('；',';'))
  for left,right in [('；','：'),('-','一'),('、','丶'),('㎎','mg'),('；','')]:
   self.assertFalse(punctuation_width_only(left,right))
 def test_current_auxiliary_difference_outside_old_dispute_participates(self):
  g=local_competitions('甲乙；',['甲乙：'],'甲丙；')
  self.assertEqual([(x['start'],x['end']) for x in g],[(1,2),(2,3)])
  self.assertEqual({v['fragment'] for v in g[0]['variants']},{'乙','丙'})
 def test_red_candidate_can_correct_original_but_needs_primary_support(self):
  c={'image':'curve.png','primary':{'text':'错字'},'auxiliary':{'status':'completed','text':'另一错字'},'red_plane_validation':{'source_image':'curve.png','geometry_unchanged':True,'response':{'status':'completed','text':'原文'},'primary_response':{'status':'completed','text_assembled':'原文'}}}
  self.assertEqual(select_seal_evidence(c)[0],'原文')
  c['red_plane_validation'].pop('primary_response');self.assertEqual(select_seal_evidence(c)[0],'错字')
 def test_wrong_source_red_view_cannot_change_original(self):
  c={'image':'curve.png','primary':{'text':'原文'},'red_plane_validation':{'source_image':'other.png','geometry_unchanged':True,'response':{'status':'completed','text':'其他'},'primary_response':{'status':'completed','text':'其他'}}}
  self.assertEqual(select_seal_evidence(c)[0],'原文')
 def test_incomplete_nonempty_authority_and_human_protection(self):
  v=fixtures.PositiveAdjudication().view();f=v['readable_elements'][0];f['value']='示例登机关';f['text']='登记机关：示例登机关'
  attach_authority_values(v);self.assertEqual(f['value'],'示例登记机关');self.assertEqual(f['previous_effective_value'],'示例登机关')
  v=fixtures.PositiveAdjudication().view();f=v['readable_elements'][0];f['value']='示例登机关';f['human_revision']=True
  attach_authority_values(v);self.assertEqual(f['value'],'示例登机关')
 def test_unrelated_company_or_signer_does_not_fill_nonempty_authority(self):
  v=fixtures.PositiveAdjudication().view();v['readable_elements'][0]['value']='其他机关';v['applied_seal_adjudications'][0]['text']='示例有限公司'
  attach_authority_values(v);self.assertEqual(v['readable_elements'][0]['value'],'其他机关')
 def test_compaction_preserves_object_identity_values_and_raw_store(self):
  original={'source_identity':{'version':1,'attempt':{'candidate_evidence':{'text':'版本证据'}}},'original_chunks':[{'text':'原文','candidate_evidence':{'spans':[{'text_raw':'候选'}]}}],'targets':[{'issue_key':'stable','source_span_ids':['s'],'value':'原文'}]}
  saved=copy.deepcopy(original);result=compact_review_evidence(original)
  self.assertEqual(original,saved);self.assertEqual(result['targets'],original['targets'])
  self.assertEqual(result['source_identity'],original['source_identity'])
  self.assertEqual(result['original_chunks'][0]['candidate_evidence']['evidence_ref'],'/original_chunks/0/candidate_evidence')
if __name__=='__main__':unittest.main()
