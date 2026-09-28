"""自动裁决的错误放行反例；不依赖原件答案或全页OCR。"""
import sys,unittest,tempfile
from pathlib import Path
from PIL import Image,ImageDraw
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ocr_service/paddle_runtime'))
from glyph_adjudication import input_evidence,decide_dense
from cell_adjudication import pixel_preservation
from bounded_review import reconcile_adjudications

class AutomaticAdjudication(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)
 def tearDown(self):self.tmp.cleanup()
 def test_internal_stroke_loss_rejected_even_when_outer_crop_complete(self):
  a=Image.new('RGB',(100,50),'white');ImageDraw.Draw(a).rectangle([20,10,50,35],fill='black');a.save(self.path/'a.png')
  b=a.copy();ImageDraw.Draw(b).rectangle([20,10,35,35],fill='white');b.save(self.path/'b.png')
  inp=lambda name:{'input':{'image':str(self.path/name),'input_to_page':[[1,0,0],[0,1,0],[0,0,1]]}}
  span={'polygon_input_px':[[10,5],[60,5],[60,40],[10,40]]}
  self.assertFalse(pixel_preservation(inp('a.png'),inp('b.png'),span)['eligible'])
  self.assertTrue(pixel_preservation(inp('a.png'),inp('a.png'),span)['eligible'])
 def test_blank_cut_and_multiple_rows_rejected(self):
  for kind in ['blank','cut','rows']:
   a=Image.new('RGB',(120,80),'white');d=ImageDraw.Draw(a)
   if kind=='cut':d.rectangle([0,10,60,30],fill='black')
   if kind=='rows':d.rectangle([10,10,90,25],fill='black');d.rectangle([10,50,90,65],fill='black')
   p=self.path/(kind+'.png');a.save(p);self.assertFalse(input_evidence(p)['eligible'])
 def test_white_padding_does_not_hide_cut_input(self):
  from PIL import ImageOps
  a=Image.new('RGB',(120,60),'white');ImageDraw.Draw(a).rectangle([0,10,60,30],fill='black');p=self.path/'padded.png';ImageOps.expand(a,8,fill='white').save(p)
  self.assertFalse(input_evidence(p,padding_px=8)['eligible'])
 def test_high_score_auxiliary_cannot_resolve_substantive_character(self):
  p=self.path/'text.png';a=Image.new('RGB',(120,60),'white');ImageDraw.Draw(a).rectangle([20,15,80,40],fill='black');a.save(p)
  response={'image':str(p),'input_size':[120,60],'ctc':{'timesteps':10,'tokens':[{'text':'不','timestep':4,'posterior':.999}]}}
  result=decide_dense('可',[{'text':'不'}],response);self.assertFalse(result['accepted']);self.assertEqual(result['text'],'可')
 def test_unresolved_other_issue_and_same_cell_empty_not_cleared(self):
  page={'automatic_adjudications':[{'kind':'cell','accepted':True,'resolved':True,'table_index':0,'cell_index':0}]}
  issues=[{'code':'ocr_overlay_conflict','table_index':0,'cell_index':0},{'code':'ocr_empty','table_index':0,'cell_index':0},{'code':'ocr_overlay_conflict','table_index':0,'cell_index':1}]
  obs=[];out=reconcile_adjudications({},page,issues,obs);self.assertEqual(out,issues[1:]);self.assertEqual(len(obs),1)
 def test_unverified_adoption_remains_blocking(self):
  d={'kind':'cell','accepted':True,'resolved':False,'table_index':0,'cell_index':0};view={'tables':[{'cells':[{'bbox_pdf':[0,0,10,10],'text':'候选'}]}]}
  from bounded_review import adjudication_observations
  page={'automatic_adjudications':[d]}
  out=reconcile_adjudications(view,page,adjudication_observations(view,page),[]);self.assertEqual(out[0]['code'],'ocr_overlay_conflict')
if __name__=='__main__':unittest.main()
