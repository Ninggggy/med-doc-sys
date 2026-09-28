"""最终对象诊断回归；已知像素与文字仅用于开发测试。"""
import sys,unittest,tempfile
from pathlib import Path
from copy import deepcopy
from PIL import Image,ImageDraw
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ocr_service/paddle_runtime'))
from diagnostic_reconciliation import result_applicability,reconcile
from field_coverage import field_missing_issues,reconcile_layout_coverage,visible_ink,field_value_geometry
from regional_adoption import differences

class FinalDiagnostics(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'input.png'
  im=Image.new('RGB',(300,180),'white');d=ImageDraw.Draw(im);d.rectangle([130,44,139,57],fill='black');im.save(self.path)
 def tearDown(self):self.tmp.cleanup()
 def result(self,text='已知文字',score=.99):
  return dict(status='completed',input={'image':str(self.path),'input_to_page':[[1,0,0],[0,1,0],[0,0,1]]},input_size=[300,180],text_assembled=text,
   spans=[dict(span_id='s',text_raw=text,score_raw=score,score_kind='paddle_rec_score',coordinate_source='engine_polygon',polygon_page=[[120,40],[160,40],[160,60],[120,60]])])
 def test_failed_low_missing_illegal_and_misaligned_candidates(self):
  good=self.result();self.assertTrue(result_applicability(good)[0])
  bad=[]
  r=deepcopy(good);r['status']='failed';bad.append(r)
  for score in [.1,None,'0.99',True,float('nan'),2]:bad.append(self.result(score=score))
  r=deepcopy(good);r['text_assembled']='其他文字';bad.append(r)
  r=deepcopy(good);r['spans'][0]['original_text_raw']='历史原文';bad.append(r)
  for r in bad:self.assertFalse(result_applicability(r)[0])
 def test_invalid_regional_candidate_leaves_independent_coverage(self):
  r=self.result(score=.1);view={'lines':[],'tables':[]};obs=[]
  errors=[dict(code='ocr_candidate_conflict',candidate_evidence={'result':r}),dict(code='ocr_coverage',region_id='gap')]
  current=reconcile(view,{'page_result':self.result()},errors,obs)
  self.assertEqual([e['code'] for e in current],['ocr_coverage']);self.assertEqual(len(obs),1)
  self.assertEqual(obs[0]['original_diagnostic'],errors[0])
 def test_invalid_cell_candidate_does_not_erase_mixed_role_risk(self):
  raw=self.result('混框文字');bad=self.result('无效候选',.1)
  view={'lines':[],'tables':[{'cells':[{'result':raw,'text':'混框文字'}]}]}
  issue=dict(code='ocr_overlay_conflict',table_index=0,cell_index=0,candidate_evidence=[bad],
             content_role='mixed_cell_text_object',role_evidence=[{'role':'mixed_or_body','inside_red_circular_outline':True}])
  out=reconcile(view,{'page_result':raw},[issue],[])
  self.assertEqual(len(out),1)
  self.assertIn('candidate_history_reason',out[0]['candidate_applicability'])
 def test_actual_glyph_coverage_crosses_layout_but_empty_frame_stays(self):
  r=self.result();view={'lines':[{'source_span_id':'s','bbox':[120,40,160,60]}]};obs=[]
  issues=[dict(code='ocr_coverage',stage='layout_coverage',bbox_pdf=[120,40,160,60]),dict(code='ocr_coverage',stage='layout_coverage',bbox_pdf=[200,40,240,60])]
  remaining=reconcile_layout_coverage(view,{'page_result':r},issues,obs)
  self.assertEqual(remaining,[issues[1]]);self.assertEqual(len(obs),1)
  r['spans'][0]['coordinate_source']='crop_region'
  self.assertEqual(len(reconcile_layout_coverage(view,{'page_result':r},issues,[])),2)
 def test_empty_high_score_label_with_ink_and_blank_negative(self):
  def field(label,value,b,sid):return dict(kind='field',label=label,value=value,text=label+'：'+value,bbox_pdf=b,region_id=sid,source_span_ids=[sid],source_lines=[{'text':label+'：'+value,'bbox':b}])
  view={'readable_elements':[field('法定代表人','',[10,40,140,60],'empty'),field('企业负责人','甲乙',[10,70,145,90],'known')]}
  # 真实来源包含完整标签行及可见分隔符；不再靠邻字段字数推算值区。
  im=Image.open(self.path);draw=ImageDraw.Draw(im)
  for x in [12,42,72]:draw.rectangle([x,43,x+9,58],fill='black')
  draw.rectangle([100,46,102,48],fill='black');draw.rectangle([100,53,102,55],fill='black');im.save(self.path)
  raw=self.result();raw['spans'][0].update(span_id='empty',text_raw='法定代表人：',polygon_page=[[10,40],[140,40],[140,60],[10,60]])
  errors=field_missing_issues(view,{'page_result':raw});self.assertEqual(len(errors),1);self.assertEqual(errors[0]['source_span_id'],'empty')
  geometry=field_value_geometry(raw,raw['spans'][0]);self.assertGreater(geometry['value_bbox_pdf'][0],100)
  self.assertLess(geometry['label_polygon_page'][1][0],geometry['value_bbox_pdf'][0])
  # 输出标签范围和实际分隔符一起变换，不能把 page-x 当成输入中的竖直切口。
  transformed=deepcopy(raw);transformed['input']['input_to_page']=[[1,.2,10],[.1,1,20],[0,0,1]]
  transformed['spans'][0]['polygon_page']=[[x+.2*y+10,.1*x+y+20] for x,y in raw['spans'][0]['polygon_page']]
  observed=field_value_geometry(transformed,transformed['spans'][0]);self.assertIsNotNone(observed)
  points=observed['label_polygon_page'];self.assertAlmostEqual(points[2][0]-points[1][0],.2*(points[2][1]-points[1][1]))
  Image.new('RGB',(300,180),'white').save(self.path)
  self.assertFalse(field_missing_issues(view,{'page_result':raw}))
 def test_effective_source_match_is_history_but_credible_old_difference_remains(self):
  candidate={'span_id':'s','arms':[{'text':'当前值','parts':[self.result('当前值')]}],'adopted':True}
  view={'lines':[{'source_span_id':'s','text':'当前值'}],'tables':[]};issue={'code':'ocr_candidate_conflict','source_span_id':'s','candidate_evidence':candidate}
  obs=[];self.assertFalse(reconcile(view,{'page_result':self.result('当前值')},[issue],obs));self.assertEqual(len(obs),1)
  out=reconcile(view,{'original_page_result':self.result('历史实质分歧')},[issue],[])
  self.assertEqual(len(out),1);self.assertTrue(out[0]['differences'])
 def test_qualified_punctuation_variant_still_blocks(self):
  candidate={'span_id':'s','arms':[{'text':'甲：乙','parts':[self.result('甲：乙')]}]}
  view={'lines':[{'source_span_id':'s','text':'甲；乙'}],'tables':[]}
  out=reconcile(view,{'page_result':self.result('甲；乙')},[{'code':'ocr_candidate_conflict','candidate_evidence':candidate}],[])
  self.assertEqual(len(out),1);self.assertEqual(out[0]['differences'][0]['original'],'；')
 def test_punctuation_is_a_substantive_difference(self):
  for a,b in [('仅限甲；乙','仅限甲：乙'),('802-1','802一1')]:self.assertTrue(differences(a,b))
 def test_overlay_low_score_is_not_address_low_score(self):
  view={'lines':[],'tables':[{'cells':[{'role_evidence':[{'span_id':'seal','role':'red_overlay'}]}]}]}
  issue=dict(code='ocr_quality',source_span_id='seal',table_index=0,cell_index=0)
  out=reconcile(view,{},[issue],[])[0];self.assertFalse(out['affects_body']);self.assertEqual(out['content_role'],'independent_overlay')

if __name__=='__main__':unittest.main()
