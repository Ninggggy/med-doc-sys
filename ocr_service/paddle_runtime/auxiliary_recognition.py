"""显式启用的局部识别辅助；只接受既已定位的文字行，不替换主OCR。"""
import os,time
from pathlib import Path


def sequence_capture(probabilities,characters,candidates):
 import numpy as np
 from sequence_adjudication import score_candidates
 ids=probabilities.argmax(-1);tokens=[]
 for i,label in enumerate(ids):
  if label==0 or (i and label==ids[i-1]):continue
  end=i+1
  while end<len(ids) and ids[end]==label:end+=1
  top=probabilities[i].argsort()[-4:][::-1]
  tokens.append(dict(text=characters[int(label)],timestep=i,end_timestep=end,posterior=float(probabilities[i,label]),
                     alternatives=[dict(text=characters[int(k)],posterior=float(probabilities[i,k])) for k in top]))
 evidence=score_candidates(probabilities,characters,candidates,''.join(t['text'] for t in tokens))
 return dict(timesteps=len(ids),tokens=tokens,alignment_kind='ctc_emission_positions_not_glyph_boxes',sequence_evidence=evidence)

class AuxiliaryRecognizer:
 def __init__(self,model_dir,model_name='ch_SVTRv2_rec'):
  os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK']='True'
  from paddleocr import TextRecognition
  self.model_name=model_name
  self.model=TextRecognition(model_name=model_name,model_dir=str(model_dir),device='cpu',cpu_threads=2,enable_mkldnn=False)
  self.last=[];self.candidates=[];owner=self;post=self.model.paddlex_predictor.post_op
  class Capture:
   def __call__(self,pred,**kwargs):
    import numpy as np
    array=np.asarray(pred[0]);owner.last=[]
    for probabilities in array:
     ids=probabilities.argmax(-1);tokens=[]
     for i,label in enumerate(ids):
      if label==0 or (i and label==ids[i-1]):continue
      end=i+1
      while end<len(ids) and ids[end]==label:end+=1
      top=probabilities[i].argsort()[-4:][::-1]
      tokens.append(dict(text=post.character[int(label)],timestep=i,end_timestep=end,posterior=float(probabilities[i,label]),alternatives=[dict(text=post.character[int(k)],posterior=float(probabilities[i,k])) for k in top]))
     from sequence_adjudication import score_candidates
     evidence=score_candidates(probabilities,post.character,owner.candidates,''.join(t['text'] for t in tokens))
     owner.last.append(dict(timesteps=len(ids),tokens=tokens,alignment_kind='ctc_emission_positions_not_glyph_boxes',sequence_evidence=evidence))
    return post(pred,**kwargs)
  self.model.paddlex_predictor.post_op=Capture()
 def predict(self,image,candidates=None,geometry=None):
  import json
  from PIL import Image
  self.candidates=list(candidates or []);start=time.perf_counter();res=list(self.model.predict(str(image),batch_size=1))[0].json
  if isinstance(res,str):res=json.loads(res)
  d=res.get('res',res)
  with Image.open(image) as im:size=list(im.size)
  return dict(status='completed',candidate_texts=list(self.candidates),model=self.model_name,text=d['rec_text'],line_score=d['rec_score'],score_scope='whole_line',ctc=self.last[0],sequence_evidence=self.last[0]['sequence_evidence'],source_geometry=geometry or {},input_size=size,image=str(image),seconds=time.perf_counter()-start)
 def close(self):self.model.close()
