"""对象覆盖及最终投影；机制验证不冒充实际识别成绩。"""
import copy,tempfile,sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ocr_service/paddle_runtime'))
from PIL import Image,ImageDraw
from cell_adjudication import object_coverage
from product_consumer import final_unresolved
class ObjectResolution(unittest.TestCase):
 def sample(self,directory):
  p=Path(directory)/'cell.png';im=Image.new('RGB',(220,100),'white');d=ImageDraw.Draw(im)
  d.rectangle([0,0,219,99],outline='black',width=2);d.rectangle([20,25,75,45],fill='black');d.ellipse([120,15,190,85],outline='red',width=4);im.save(p)
  span={'span_id':'body','text_raw':'正文','coordinate_source':'engine_polygon','polygon_input_px':[[18,22],[78,22],[78,48],[18,48]],'polygon_page':[[18,22],[78,22],[78,48],[18,48]]}
  source={'image':str(p),'input_to_page':[[1,0,0],[0,1,0],[0,0,1]],'geometry_origin':'automatic_visible_grid'}
  return {'bbox_pdf':[0,0,220,100],'result':{'input':source,'spans':[span]},'content_candidates':[]},span,p
 def test_body_coverage_excludes_proven_grid_not_red_or_unknown_roles(self):
  with tempfile.TemporaryDirectory() as t:
   cell,span,p=self.sample(t);e=object_coverage(cell,[span]);self.assertTrue(e['complete']);self.assertGreater(e['grid_pixels'],0);self.assertGreater(e['red_pixels'],0)
   self.assertEqual(e['coverage'],1);self.assertEqual(e['objects'][0]['source_span_id'],'body')
 def test_visible_date_outside_verified_body_stays_unfinished(self):
  with tempfile.TemporaryDirectory() as t:
   cell,span,p=self.sample(t);im=Image.open(p);ImageDraw.Draw(im).rectangle([100,70,115,90],fill='black');im.save(p)
   e=object_coverage(cell,[span]);self.assertEqual(e['coverage'],1);self.assertGreater(e['unassigned_neutral_pixels'],0);self.assertFalse(e['complete'])
   self.assertTrue(e['body_complete']) # 已核实正文独立成立，未知日期仍阻止整个对象完成。
 def test_empty_span_does_not_make_visible_ink_blank(self):
  with tempfile.TemporaryDirectory() as t:
   cell,span,p=self.sample(t);cell['result']['spans'][0]['text_raw']='';e=object_coverage(cell,[])
   self.assertGreater(e['target_ink_pixels'],0);self.assertFalse(e['complete'])
 def test_dark_red_is_not_neutral_body_ink(self):
  import numpy as np
  from cell_adjudication import neutral_ink
  pixels=np.array([[[100,20,20],[90,90,90],[240,240,240]]],dtype=float)
  self.assertEqual(neutral_ink(pixels).tolist(),[[False,True,False]])
 def test_sheared_geometry_uses_inverse_full_matrix(self):
  with tempfile.TemporaryDirectory() as t:
   cell,span,p=self.sample(t);before=object_coverage(cell,[span]);cell['result']['input']['input_to_page']=[[1,.2,10],[.1,1,20],[0,0,1]]
   span['polygon_page']=[[x+.2*y+10,.1*x+y+20] for x,y in span['polygon_input_px']];cell['bbox_pdf']=None
   after=object_coverage(cell,[span]);self.assertEqual(before['target_ink_pixels'],after['target_ink_pixels']);self.assertEqual(after['coverage'],1)
 def test_same_observation_seen_by_all_proofs_and_only_one_final_projection(self):
  raw=[{'code':'ocr_quality','source_span_id':'s','text':'旧'}];saved=copy.deepcopy(raw);history=[];v={'lines':[]}
  historical={'code':'diagnostic_reconciliation','disposition':'historical_observation'}
  supported={'code':'automatic_resolution','disposition':'original_or_role_verified'}
  with patch('diagnostic_reconciliation.candidate_assessments',return_value=[(raw[0],historical)]),patch('bounded_review.sequence_assessments',return_value=[(raw[0],supported)]) as seq,patch('seal_review.seal_assessments',return_value=[(raw[0],None)]) as seal,patch('field_coverage.layout_coverage_assessments',return_value=[(raw[0],None)]) as cover:
   self.assertEqual(final_unresolved(v,{},raw,history),[])
   self.assertEqual(seq.call_args[0][2],raw);self.assertEqual(seal.call_args[0][1],raw);self.assertEqual(cover.call_args[0][2],raw)
  self.assertEqual(raw,saved);self.assertEqual(len(history),1);self.assertEqual(history[0]['code'],'automatic_resolution')
 def test_red_plane_marker_does_not_prove_pixel_integrity(self):
  from seal_review import red_signal_preserved
  with tempfile.TemporaryDirectory() as t:
   p=Path(t)/'red.png';q=Path(t)/'view.png';Image.new('RGB',(30,20),'red').save(p);Image.new('L',(30,20),255).save(q)
   self.assertFalse(red_signal_preserved(p,q))
 def test_short_authority_fragment_uses_same_seal_relation_not_length(self):
  from test_positive_adjudication import PositiveAdjudication
  from seal_review import attach_authority_values
  view=PositiveAdjudication().view();field=view['readable_elements'][0]
  field.update(value='例登机',text='登记机关：例登机')
  attach_authority_values(view);self.assertEqual(field['value'],'示例登记机关')
  self.assertEqual(field['previous_effective_value'],'例登机')
  view=PositiveAdjudication().view();field=view['readable_elements'][0]
  field.update(value='例登机',text='登记机关：例登机',human_revision=True)
  attach_authority_values(view);self.assertEqual(field['value'],'例登机')
 def test_verified_missing_value_does_not_clear_label_quality(self):
  missing=dict(code='ocr_coverage',stage='field_value_coverage',source_span_id='label',text='姓名：')
  label=dict(code='ocr_quality',source_span_id='label',text='姓名：',bbox_pdf=[0,0,20,10])
  proof=dict(accepted=True,source_span_id='label',decision={'whole_sequence_review':{'complete':True}})
  view={'lines':[{'source_span_id':'label','text':'姓名：示例','field_value_verification':proof}],
        'field_value_reviews':[proof]}
  self.assertEqual([x['code'] for x in final_unresolved(view,{},[missing,label],[])],['ocr_quality'])
  proof['decision']['whole_sequence_review']['complete']=False
  self.assertEqual(len(final_unresolved(view,{},[missing,label],[])),2)
 def test_complete_short_field_confirmation_has_exact_source_and_label_scope(self):
  issue=dict(code='ocr_quality',source_span_id='classification',text='分类码：Bh')
  other=dict(code='ocr_quality',source_span_id='seal-number',text='1010')
  proof=dict(accepted=True,label_verified=True,source_span_id='classification',original='分类码：Bh',
             decision={'whole_sequence_review':{'complete':True}})
  view={'lines':[{'source_span_id':'classification','text':'分类码：Bh','field_value_verification':proof}],
        'field_value_reviews':[proof]}
  self.assertEqual(final_unresolved(view,{},[issue,other],[]),[{**other,'original_diagnostic_index':1}])
  for key,value in [('label_verified',False),('original','分类码：B1'),('accepted',False)]:
   changed=copy.deepcopy(view);changed['field_value_reviews'][0][key]=value
   self.assertEqual(len(final_unresolved(changed,{},[issue,other],[])),2)
  changed=copy.deepcopy(view);changed['lines'][0].pop('field_value_verification')
  self.assertEqual(len(final_unresolved(changed,{},[issue,other],[])),2)
if __name__=='__main__':unittest.main()
