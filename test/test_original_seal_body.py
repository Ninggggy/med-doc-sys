"""章角色只解输入归属，不能代替文字验证或清除独立章风险。"""
import sys,tempfile,unittest
from unittest.mock import patch
from pathlib import Path
from PIL import Image,ImageDraw
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ocr_service/paddle_runtime'))
from glyph_adjudication import input_evidence
from sequence_adjudication import decide_sequence


class OriginalSealBodyTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.path=Path(self.tmp.name)/'line.png'
  im=Image.new('RGB',(140,60),'white');d=ImageDraw.Draw(im)
  d.rectangle((25,22,35,40),fill='black');d.rectangle((70,22,82,40),fill='black')
  d.line((15,12,52,46),fill=(220,20,20),width=4);im.save(self.path)
  self.g=dict(preprocessing='original',review_scope='cell_body',padding_px=8,
      source_input={'image':str(self.path)},target_polygon_input_px=[[12,10],[100,10],[100,49],[12,49]],
      seal_overlay_role=dict(source_page_input={'image':'same-page'},regions=[dict(
       source='closed_red_circular_outline',polygon_input_px=[[9,9],[60,9],[60,50],[9,50]])]))
 def test_assigned_red_is_role_evidence_not_text_confirmation(self):
  e=input_evidence(self.path,metadata=self.g)
  self.assertTrue(e['eligible']);self.assertFalse(e['seal_overlay_role']['seal_text_verified'])
  d=decide_sequence('正文',[],dict(status='completed',image=str(self.path)),self.g)
  self.assertFalse(d['accepted']);self.assertEqual(d['reason'],'missing_full_sequence_evidence')
 def test_layout_box_alone_cannot_assign_red(self):
  self.g['seal_overlay_role']['regions'][0]['source']='PP-DocLayoutV3_seal'
  self.assertFalse(input_evidence(self.path,metadata=self.g)['eligible'])
 def test_wrong_seal_or_partial_coverage_cannot_clear_input(self):
  self.g['seal_overlay_role']['regions'][0]['polygon_input_px']=[[9,9],[24,9],[24,25],[9,25]]
  self.assertFalse(input_evidence(self.path,metadata=self.g)['eligible'])
 def test_real_black_cut_remains_rejected(self):
  with Image.open(self.path) as im:
   ImageDraw.Draw(im).rectangle((7,28,32,34),fill='black');im.save(self.path)
  e=input_evidence(self.path,metadata=self.g)
  self.assertFalse(e['eligible']);self.assertEqual(e['reason'],'target_ink_touches_input_boundary')
 def test_role_proof_not_reused_for_changed_processed_pixels(self):
  self.g['preprocessing']='neutral_preserving'
  self.assertFalse(input_evidence(self.path,metadata=self.g)['eligible'])
 def test_complete_object_does_not_start_new_color_competition(self):
  from bounded_review import review_unresolved_cell_overlays
  page={'tables':[{'cells':[{}]}]};entries={(0,0):{(0,0):{'text':'已核实'}}}
  with patch('bounded_review.decide_cell',return_value={'resolved':True}),patch('auxiliary_recognition.AuxiliaryRecognizer') as model:
   self.assertEqual(review_unresolved_cell_overlays(page,entries,'unused',self.tmp.name,None,10),0)
   model.assert_not_called()
  self.assertEqual(entries[(0,0)][(0,0)],{'text':'已核实'})
 def test_separate_scoring_batches_never_overwrite_response_ids(self):
  from bounded_review import complete_sequence_reviews
  class Engine:
   def __init__(self):self.ids=[]
   def predict(self,sample,timeout):
    assert sample['sample_id'] not in self.ids
    self.ids.append(sample['sample_id'])
    return dict(status='completed',text_assembled='正文')
  engine=Engine()
  def response():return dict(image=str(self.path),source_geometry=self.g,ctc={'tokens':[{'posterior':.5}]},sequence_evidence={'primary_text':'正文','local_competitions':[]})
  a=response();b=response()
  complete_sequence_reviews({'sample_id':'page'},[('first',a)],engine)
  complete_sequence_reviews({'sample_id':'page'},[('second',b)],engine)
  self.assertEqual(len(set(engine.ids)),2)
  self.assertEqual(b['primary_sequence_review']['status'],'completed')

if __name__=='__main__':unittest.main()
