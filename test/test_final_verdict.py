"""最终范围验证与显示替换独立；机制样例不充当识别成绩。"""
import copy,tempfile,unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ocr_service/paddle_runtime'))
from PIL import Image,ImageDraw
from seal_review import reconcile_seal_issues
from sequence_adjudication import decide_sequence
from glyph_adjudication import input_evidence
import test_positive_adjudication as positive
import test_sequence_adjudication as sequence
class FinalVerdict(unittest.TestCase):
 def test_optional_final_scoring_failure_preserves_existing_response(self):
  from unittest.mock import patch
  from bounded_review import complete_final_sequences
  response={'sequence_evidence':{'primary_text':'旧','candidate_texts':['新']},'image':'existing.png','source_geometry':{'source':'retained'}}
  saved=copy.deepcopy(response)
  decision={'accepted':True,'text':'新','whole_sequence_review':{'selected_sequence_scored':False}}
  with patch('sequence_adjudication.decide_sequence',return_value=decision),patch('auxiliary_recognition.AuxiliaryRecognizer',side_effect=RuntimeError('optional unavailable')):
   self.assertEqual(complete_final_sequences({},[('source',response)],None,'unused'),0)
  self.assertEqual(response['sequence_evidence'],saved['sequence_evidence']);self.assertTrue(response['final_sequence_failure']['previous_evidence_preserved'])
 def test_covered_table_fragment_needs_no_body_replacement(self):
  d=positive.PositiveAdjudication().decision();d['applied_source_span_ids']=[]
  e=dict(code='ocr_quality',source_span_id='seal-fragment',text='机',table_index=0,cell_index=2)
  history=[];v={'seal_adjudications':[d]};original=copy.deepcopy(v)
  self.assertEqual(reconcile_seal_issues(v,[e],history),[]);self.assertEqual(v,original);self.assertEqual(history[0]['original_issue'],e)
 def test_partial_wrong_source_and_black_text_are_not_cleared(self):
  e=dict(code='ocr_quality',source_span_id='seal-fragment',text='机')
  for field,value in [('red_ink_coverage',.5),('red_ink_fraction',.5),('neutral_dark_fraction',.5),('source_span_id','another-seal')]:
   d=positive.PositiveAdjudication().decision();d['covered_sources'][0][field]=value
   self.assertEqual(reconcile_seal_issues({'seal_adjudications':[d]},[e],[]),[e])
 def test_multiple_sources_only_actual_covered_member_finishes(self):
  d=positive.PositiveAdjudication().decision();a=dict(code='ocr_quality',source_span_id='seal-fragment',text='机');b=dict(code='ocr_quality',source_span_id='number',text='1010')
  self.assertEqual(reconcile_seal_issues({'seal_adjudications':[d]},[a,b],[]),[b])
 def test_final_string_support_includes_unchanged_positions(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'a.png';im=Image.new('RGB',(100,50),'white');ImageDraw.Draw(im).rectangle([20,15,75,35],fill='black');im.save(p)
   response,g=sequence.SequenceAdjudication().response('甲丙','甲乙',['甲丙'],p)
   d=decide_sequence('甲乙',['甲丙'],response,g)
   self.assertEqual(d['verification_scope']['kind'],'whole_object');self.assertEqual(d['verification_scope']['ranges'],[[1,2]])
   response['ctc']['tokens'][0]['posterior']=.3
   self.assertEqual(decide_sequence('甲乙',['甲丙'],response,g)['verification_scope']['kind'],'local_edits')
 def test_stamp_alias_uses_red_target_but_not_blank(self):
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'red.png';im=Image.new('RGB',(100,50),'white');ImageDraw.Draw(im).rectangle([20,15,75,35],fill=(220,20,20));im.save(p)
   meta=dict(target_polygon_input_px=[[10,10],[90,10],[90,40],[10,40]],role='stamp')
   a=input_evidence(p,metadata=meta);b=input_evidence(p,metadata={**meta,'role':'seal'});self.assertEqual(a,b);self.assertTrue(a['eligible'])
   self.assertFalse(input_evidence(p,metadata={**meta,'role':'body'})['eligible'])
   Image.new('RGB',(100,50),'white').save(p);self.assertFalse(input_evidence(p,metadata=meta)['eligible'])
if __name__=='__main__':unittest.main()
