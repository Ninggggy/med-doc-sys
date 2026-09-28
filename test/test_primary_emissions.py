"""同次主识别帧证据的行对应和按实际弱位置触发，非真实识别成绩。"""
import unittest,sys,tempfile
from pathlib import Path
from types import SimpleNamespace
import numpy as np
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'ocr_service/paddle_runtime'))
from adapters import Paddle
class PrimaryEmissions(unittest.TestCase):
 def test_reordered_recognition_batches_match_text_and_score(self):
  class Post:
   character=['','甲','乙']
   def __call__(self,pred,**kwargs):return ['甲','乙'],[.7,.99]
  post=Post();recognizer=SimpleNamespace(post_op=post)
  def predict(path):
   recognizer.post_op([np.array([[[.2,.7,.1]],[[.005,.005,.99]]])])
   return [SimpleNamespace(json={'res':{'rec_texts':['乙','甲'],'rec_scores':[.99,.7],
    'rec_polys':[[[1,1],[5,1],[5,4],[1,4]],[[8,1],[12,1],[12,4],[8,4]]]}})]
  adapter=Paddle.__new__(Paddle);adapter.events=[];adapter.model=SimpleNamespace(paddlex_pipeline=SimpleNamespace(text_rec_model=recognizer),predict=predict)
  output=adapter.predict('not_read_in_mock')
  self.assertEqual([s['ctc_emission_evidence']['tokens'][0]['text'] for s in output['spans']],['乙','甲'])
  self.assertEqual(output['spans'][0]['raw_output_index'],0);self.assertIs(recognizer.post_op,post)
 def test_length_alone_does_not_trigger_but_actual_weak_emission_does(self):
  from PIL import Image,ImageDraw
  from content_recovery import recover_dense
  class Engine:
   def __init__(self):self.calls=[]
   def predict(self,s):
    self.calls.append(s)
    return dict(status='failed',spans=[],text_assembled='',sample_id=s['sample_id'])
  with tempfile.TemporaryDirectory() as temp:
   root=Path(temp);(root/'images').mkdir();p=root/'page.png';im=Image.new('RGB',(650,60),'white');ImageDraw.Draw(im).rectangle([15,24,600,33],fill='black');im.save(p)
   span=dict(span_id='line',text_raw='甲乙丙丁'*12,score_raw=.99,polygon_input_px=[[10,20],[620,20],[620,38],[10,38]],polygon_page=[[10,20],[620,20],[620,38],[10,38]])
   result={'spans':[span],'input':{'image':str(p),'input_to_page':[[1,0,0],[0,1,0],[0,0,1]]}}
   engine=Engine();recover_dense(result,[{'spans':[span]}],engine,root);self.assertFalse(engine.calls)
   span['ctc_emission_evidence']={'text':span['text_raw'],'tokens':[{'text':'甲','posterior':.3,'timestep':1}]}
   _,candidates=recover_dense(result,[{'spans':[span]}],engine,root)
   self.assertTrue(engine.calls);self.assertEqual(candidates[0]['trigger'],'weak_primary_ctc_positions');self.assertFalse(candidates[0]['adopted'])
if __name__=='__main__':unittest.main()
