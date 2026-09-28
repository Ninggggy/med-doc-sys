# HISTORICAL ONLY: local paths removed; use ../verify_evidence.py for offline replay.
import json,sys,time,numpy as np
from pathlib import Path
from PIL import Image
sys.path.insert(0,str(Path.cwd()/'ocr_service/paddle_runtime'))
from auxiliary_recognition import AuxiliaryRecognizer
from pipeline import Engine
root=Path('[LOCAL_PATH_OMITTED]')
if __name__=='__main__':
 a=np.asarray(Image.open(root/'doc2-date-native-composite.png').convert('RGB'),dtype=float);background=np.percentile(a.reshape(-1,3),95,axis=0)
 od=-np.log(np.clip((a+1)/(background+1),1/256,1));red=(a[:,:,0]-a[:,:,1]>40)&(a[:,:,0]-a[:,:,2]>40)&(a[:,:,0]>180)
 basis=np.median(od[red],axis=0);basis/=np.linalg.norm(basis);mat=np.stack([np.ones(3)/np.sqrt(3),basis],axis=1);coef=od@np.linalg.pinv(mat).T
 gray=(255*np.exp(-np.maximum(coef[:,:,0],0)/np.sqrt(3))).clip(0,255).astype('uint8');out=Image.fromarray(gray).convert('RGB');out.save(root/'source-fitted-density.png')
 meta=dict(background=background.tolist(),red_direction=basis.tolist(),method='same_input_optical_density_two_component_fit',negative_fraction=float((coef[:,:,0]<0).mean()),model_residual=float(np.abs(od-coef@mat.T).mean()))
 (root/'unmix-metadata.json').write_text(json.dumps(meta,indent=2))
 groups=json.load(open(root/'group-responses.json'));records=[]
 # Same component windows as original; only the measured stain model changes.
 for z in groups:
  gg=z['geometry'];b=gg['component_bbox_px'];pad=max(4,round((b[3]-b[1])*.12));q=[max(0,b[0]-pad),max(0,b[1]-pad),min(out.width,b[2]+pad),min(out.height,b[3]+pad)];path=root/f'density-group-{z["index"]}.png';out.crop(q).save(path);records.append(dict(index=z['index'],image=str(path)))
 m=AuxiliaryRecognizer('[LOCAL_PATH_OMITTED]')
 try:
  for z in records:z['auxiliary']=m.predict(z['image'],[],meta)
 finally:m.close()
 cfg=json.load(open('[LOCAL_PATH_OMITTED]'));run=root/'density-runtime';run.mkdir();e=Engine('paddle',cfg,run)
 try:
  for z in records:z['primary']=e.predict(dict(sample_id=f'density-date-{z["index"]}',image=z['image'],granularity='line-rec',stage='finite_sequence_scoring',sequence_candidates=[z['auxiliary']['text']],input_to_page=[[1,0,0],[0,1,0],[0,0,1]]),60)
 finally:e.close()
 (root/'density-responses.json').write_text(json.dumps(records,ensure_ascii=False,indent=2));print([(z['index'],z['auxiliary']['text'],z['primary']['text_assembled']) for z in records])
