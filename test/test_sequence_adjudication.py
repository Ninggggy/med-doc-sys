"""有限序列裁决机制测试；不替代真实识别留出验证。"""
import unittest,itertools,tempfile,sys
from pathlib import Path
import numpy as np
from PIL import Image,ImageDraw
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ocr_service/paddle_runtime'))
from sequence_adjudication import ctc_log_probability,score_candidates,decide_sequence,bounded_nbest
from glyph_adjudication import input_evidence

class SequenceAdjudication(unittest.TestCase):
 def test_forward_matches_enumerated_paths_including_repeats(self):
  p=np.array([[.2,.5,.3],[.4,.5,.1],[.3,.4,.3]])
  for target in ([1],[1,1],[1,2],[]):
   total=0
   for path in itertools.product(range(3),repeat=3):
    decoded=[v for i,v in enumerate(path) if v and (not i or path[i-1]!=v)]
    if decoded==list(target):total+=np.prod([p[i,v] for i,v in enumerate(path)])
   self.assertAlmostEqual(np.exp(ctc_log_probability(p,target)),total)
 def response(self,truth,primary,candidates,path):
  alphabet=['']+list(dict.fromkeys(truth+primary+''.join(candidates)));p=np.full((len(truth)*2+1,len(alphabet)),1e-6)
  p[:,0]=.999
  tokens=[]
  for i,ch in enumerate(truth):
   p[i*2+1,:]=1e-6;p[i*2+1,alphabet.index(ch)]=.999
   tokens.append(dict(text=ch,posterior=.999,timestep=i*2+1))
  g=dict(target_polygon_input_px=[[10,10],[90,10],[90,40],[10,40]],source_input={'image':str(path)},preprocessing='original')
  return dict(image=str(path),input_size=[100,50],status='completed',text=truth,ctc={'tokens':tokens,'timesteps':len(p)},sequence_evidence=score_candidates(p,alphabet,[primary,*candidates],truth)),g
 def test_general_char_digit_insert_delete_and_retain(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'a.png';im=Image.new('RGB',(100,50),'white');ImageDraw.Draw(im).rectangle([20,15,75,35],fill='black');im.save(path)
   for primary,truth,candidates in [('测式27','测试27',['测试27']),('地址21','地址27',['地址27']),('不可用','不得可用',['不得可用']),('编号0027','编号027',['编号027']),('测试27','测试27',['测式27'])]:
    response,g=self.response(truth,primary,candidates,path);result=decide_sequence(primary,candidates,response,g)
    self.assertTrue(result['accepted'],result);self.assertEqual(result['text'],truth)
 def test_auxiliary_new_mistake_outside_dispute_not_adopted(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'a.png';im=Image.new('RGB',(100,50),'white');ImageDraw.Draw(im).rectangle([20,15,75,35],fill='black');im.save(path)
   response,g=self.response('测试丶外用','测式、外用',['测试、外用'],path)
   result=decide_sequence('测式、外用',['测试、外用'],response,g)
   self.assertFalse(result['accepted']);self.assertEqual(result['text'],'测试、外用')
   self.assertEqual(result['verification_scope']['kind'],'local_edits')
   self.assertTrue(result['sequence_evidence']['unresolved'])
 def test_neighbor_edge_ink_is_not_target_cut_but_target_cut_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'a.png';im=Image.new('RGB',(100,70),'white');draw=ImageDraw.Draw(im);draw.rectangle([20,25,75,45],fill='black');draw.rectangle([10,0,80,5],fill='black');im.save(path)
   g=dict(target_polygon_input_px=[[10,20],[90,20],[90,50],[10,50]])
   self.assertTrue(input_evidence(path,metadata=g)['eligible'])
   draw.rectangle([20,45,40,69],fill='black');im.save(path)
   # 不复用旧图的连通域缓存。
   from glyph_adjudication import connected_components
   connected_components.cache_clear()
   self.assertFalse(input_evidence(path,metadata=g)['eligible'])
 def test_no_raw_probability_or_geometry_shortcut(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'a.png';im=Image.new('RGB',(100,50),'white');ImageDraw.Draw(im).rectangle([20,15,75,35],fill='black');im.save(path)
   response,g=self.response('不','可',['不'],path);response.pop('sequence_evidence')
   self.assertFalse(decide_sequence('可',['不'],response,g)['accepted'])
 def test_overlay_is_not_certified_by_identical_model_text(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'a.png';im=Image.new('RGB',(100,50),'white');draw=ImageDraw.Draw(im);draw.rectangle([20,15,75,35],fill='black');draw.rectangle([35,15,60,35],fill='red');im.save(path)
   response,g=self.response('药品生证','药品',['药品生证'],path)
   result=decide_sequence('药品',['药品生证'],response,g)
   self.assertFalse(result['accepted']);self.assertIn('overlay',result['input_evidence']['reason'])
 def test_effective_source_mismatch_cannot_reuse_old_offsets(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'a.png';im=Image.new('RGB',(100,50),'white');ImageDraw.Draw(im).rectangle([20,15,75,35],fill='black');im.save(path)
   response,g=self.response('测试27','测式27',['测试27'],path)
   result=decide_sequence('已有新前缀测式27',['测试27'],response,g)
   self.assertFalse(result['accepted']);self.assertEqual(result['reason'],'effective_source_differs_from_scored_target')
 def test_full_affine_metadata(self):
  from bounded_review import input_metadata
  primary={'input_size':[100,100],'input':{'input_to_page':[[2,.5,10],[.3,3,20],[0,0,1]],'image':'test'}}
  source=[[20,30],[40,30],[40,50],[20,50]]
  span={'span_id':'test','polygon_page':[[2*x+.5*y+10,.3*x+3*y+20] for x,y in source]}
  result=input_metadata(primary,span,[10,20,60,70],8)
  for actual,(x,y) in zip(result['target_polygon_input_px'],source):
   self.assertAlmostEqual(actual[0],x-10+8);self.assertAlmostEqual(actual[1],y-20+8)
 def test_neighbor_padding_can_shrink_without_cutting_source_line(self):
  from regional_adoption import crop_scope
  def span(sid,b):return {'span_id':sid,'polygon_page':[[b[0],b[1]],[b[2],b[1]],[b[2],b[3]],[b[0],b[3]]]}
  target=span('target',[20,50,170,80]);neighbor=span('neighbor',[20,20,170,46])
  result=crop_scope({'region':{'bbox_pdf':[20,50,170,80]}},[target,neighbor])
  self.assertTrue(result['fully_contains_source_lines']);self.assertGreater(result['bbox_pdf'][1],46);self.assertLess(result['bbox_pdf'][1],50)
  intersecting=span('intersecting',[0,45,25,70])
  result=crop_scope({'region':{'bbox_pdf':[20,50,170,80]}},[target,intersecting])
  self.assertGreaterEqual(result['bbox_pdf'][2],170)
 def test_high_posterior_cross_category_confusion_requires_stronger_evidence(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'a.png';im=Image.new('RGB',(100,50),'white');ImageDraw.Draw(im).rectangle([20,15,75,35],fill='black');im.save(path)
   response,g=self.response('丶','、',['丶'],path);response['ctc']['tokens'][0]['posterior']=.991
   self.assertFalse(decide_sequence('、',['丶'],response,g)['accepted'])
   response,g=self.response('照','昭',['照'],path);response['ctc']['tokens'][0]['posterior']=.991
   self.assertTrue(decide_sequence('昭',['照'],response,g)['accepted'])
   self.assertEqual(decide_sequence('昭',['照'],response,g)['verification_scope']['kind'],'whole_object')
   response,g=self.response('测试；内容','测试：内容',['测试；内容'],path)
   # 标点之外的未变位置缺少支持，不能由局部标点通过清除整行风险。
   response['ctc']['tokens'][0]['posterior']=.5
   self.assertEqual(decide_sequence('测试：内容',['测试；内容'],response,g)['verification_scope']['kind'],'local_edits')
 def test_unscored_historical_candidate_cannot_be_silently_ignored(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'a.png';im=Image.new('RGB',(100,50),'white');ImageDraw.Draw(im).rectangle([20,15,75,35],fill='black');im.save(path)
   response,g=self.response('测试','测式',['测试'],path)
   self.assertEqual(decide_sequence('测式',['测试','测侍'],response,g)['reason'],'candidate_not_scored')
 def test_local_conflict_resolution_does_not_clear_whole_line_quality(self):
  from bounded_review import reconcile_adjudications
  d={'kind':'dense','source_span_id':'s','accepted':True,'input_evidence':{'eligible':False},'original':'甲：乙',
     'sequence_evidence':{'verified':[{'start':1,'end':2}]}}
  issues=[{'code':'ocr_candidate_conflict','source_span_id':'s','candidate_evidence':{'span_id':'s','arms':[{'text':'甲；乙'}]}},{'code':'ocr_quality','source_span_id':'s'},{'code':'ocr_coverage','source_span_id':'s'}]
  self.assertEqual(reconcile_adjudications({}, {'automatic_adjudications':[d]},issues,[]),issues[1:])
  d['input_evidence']['eligible']=True
  d['verification_scope']={'kind':'local_edits','ranges':[[1,2]]}
  self.assertEqual(reconcile_adjudications({}, {'automatic_adjudications':[d]},issues,[]),issues[1:])
  other={'code':'ocr_candidate_conflict','source_span_id':'s','candidate_evidence':{'span_id':'s','arms':[{'text':'甲：丙'}]}}
  self.assertEqual(reconcile_adjudications({}, {'automatic_adjudications':[d]},[other],[]),[other])
 def test_duplicate_insertion_and_adjacent_edits_are_applied_once(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'a.png';im=Image.new('RGB',(100,50),'white');ImageDraw.Draw(im).rectangle([20,15,75,35],fill='black');im.save(path)
   for primary,truth,candidates in [('AB','AXB',['AXB','AYB','AXB']),('AB','AXC',['AXB','AC']),('AB','AXY',['AX','ABY']),('AAB','AAAB',['AAAB','AAAB'])]:
    response,g=self.response(truth,primary,candidates,path)
    result=decide_sequence(primary,candidates,response,g)
    self.assertTrue(result['accepted'],result);self.assertEqual(result['text'],truth)
    self.assertEqual(decide_sequence(primary,candidates,response,g)['text'],truth)
 def test_red_fraction_is_a_ratio_of_the_same_ink_population(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'red.png';im=Image.new('RGB',(100,50),(255,200,200));ImageDraw.Draw(im).rectangle([20,15,75,35],fill='red');im.save(path)
   result=input_evidence(path,metadata={'target_polygon_input_px':[[10,10],[90,10],[90,40],[10,40]]})
   self.assertGreater(result['red_ink_fraction'],0);self.assertLessEqual(result['red_ink_fraction'],1)
 def test_nbest_keeps_real_non_greedy_alternative_without_dictionary(self):
  p=np.array([[.01,.001,.001,.988],[.02,.63,.34,.01],[.99,.003,.003,.004]])
  candidates=bounded_nbest(p,['','丶','、','甲'])
  self.assertIn('甲丶',candidates);self.assertIn('甲、',candidates)
  scoring=score_candidates(p,['','丶','、','甲'],['甲'],'甲丶')
  self.assertTrue(any(v['fragment']=='、' for g in scoring['local_competitions'] for v in g['variants']))
 def test_red_role_does_not_require_black_glyph(self):
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'red.png';im=Image.new('RGB',(100,50),'white');ImageDraw.Draw(im).rectangle([20,15,75,35],fill='red');im.save(path)
   geometry={'target_polygon_input_px':[[10,10],[90,10],[90,40],[10,40]],'role':'seal'}
   self.assertTrue(input_evidence(path,metadata=geometry)['eligible'])
 def test_authority_association_is_unique_local_and_does_not_guess_value(self):
  from seal_review import associate_authority_regions
  field={'label':'登记机关','value':'','region_id':'field','source_span_ids':['label'],'bbox_pdf':[10,40,70,50]}
  view={'readable_elements':[field],'layout_regions':[{'label':'seal','region_id':'seal','bbox_pdf':[75,30,120,70]}]}
  self.assertEqual(len(associate_authority_regions(view,{})),1);self.assertEqual(field['value'],'')
  view['layout_regions'].append({'label':'seal','region_id':'other','bbox_pdf':[90,30,130,70]})
  self.assertEqual(associate_authority_regions(view,{}),[])
 def test_seal_requests_do_not_collide_across_pages(self):
  from unittest.mock import patch,MagicMock
  from seal_review import review_page_seals
  seen=set()
  def predict(sample,timeout):
   self.assertNotIn(sample['sample_id'],seen);seen.add(sample['sample_id']);return {'text_assembled':'测试'}
  engine=MagicMock();engine.predict.side_effect=predict
  detector=MagicMock();detector.detect.side_effect=lambda *args:[{'image':'curve.png'}]
  auxiliary=MagicMock();auxiliary.predict.return_value={'text':'测试'}
  with patch('seal_review.SealCurveDetector',return_value=detector),patch('seal_review.complete_seal_inputs',return_value=[{'seal_index':0}]),patch('auxiliary_recognition.AuxiliaryRecognizer',return_value=auxiliary),patch('seal_review.curve_integrity',return_value={'eligible':False,'reason':'geometry_not_under_test'}):
   for page in ('first-page','second-page'):
    review_page_seals({'page_result':{'sample_id':page}},'detector','recognizer',engine,'unused')
  self.assertEqual(len(seen),2)
if __name__=='__main__':unittest.main()
