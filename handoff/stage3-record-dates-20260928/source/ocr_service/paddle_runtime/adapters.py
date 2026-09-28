import csv
import io
import os
import time
from pathlib import Path
from common import run_process, polygon


class Tesseract:
    def predict(self, path, granularity='block'):
        psm = 7 if granularity=='line' else 3 if granularity=='page' else 6
        r=run_process(['tesseract',str(path),'stdout','-l','chi_sim+eng','--psm',str(psm),'tsv'],
                      env={**os.environ,'OMP_THREAD_LIMIT':'1'})
        spans=[]
        if r['status']=='completed':
            for i,w in enumerate(csv.DictReader(io.StringIO(r['stdout']),delimiter='\t')):
                if w.get('text','').strip():
                    x,y,ww,h=[int(w[k]) for k in ('left','top','width','height')]
                    spans.append(dict(text_raw=w['text'],score_raw=float(w['conf']),score_kind='tesseract_conf',
                                      granularity='word',polygon_input_px=polygon([x,y,x+ww,y+h]),
                                      coordinate_source='engine_polygon',raw_output_index=i,
                                      line_id=[w['block_num'],w['par_num'],w['line_num']]))
        return dict(raw=r,spans=spans,status=r['status'],internal=dict(psm=psm,preprocessing='Tesseract internal; not_observed'))


class Paddle:
    def __init__(self, private, run):
        os.environ['PADDLE_PDX_CACHE_HOME']=str(Path(private)/'models')
        os.environ['HF_HOME']=str(Path(private)/'hf')
        os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK']='True'
        os.environ['HF_HUB_OFFLINE']='1'
        from paddleocr import PaddleOCR
        root=Path(private)/'models'/'official_models'
        self.model=PaddleOCR(text_detection_model_name='PP-OCRv6_medium_det',
            text_recognition_model_name='PP-OCRv6_medium_rec',
            text_detection_model_dir=str(root/'PP-OCRv6_medium_det'),
            text_recognition_model_dir=str(root/'PP-OCRv6_medium_rec'),
            use_doc_orientation_classify=False,use_doc_unwarping=False,use_textline_orientation=False,
            device='cpu',cpu_threads=2,enable_mkldnn=False,text_rec_score_thresh=0.0,
            text_det_limit_side_len=64,text_det_limit_type='min',text_recognition_batch_size=6)
        self.model.export_paddlex_config_to_yaml(str(Path(run)/'paddle-effective.yaml'))
        from observe import instrument
        self.events=instrument(self)

    def predict(self,path,granularity='block',sequence_candidates=None):
        import json
        self.events.clear()
        if granularity=='line-rec':
            from PIL import Image
            recognizer=self.model.paddlex_pipeline.text_rec_model
            captured=[];post=recognizer.post_op
            if sequence_candidates is not None:
                from auxiliary_recognition import sequence_capture
                class Capture:
                    def __call__(self,pred,**kwargs):
                        import numpy as np
                        captured.extend(sequence_capture(p,post.character,sequence_candidates) for p in np.asarray(pred[0]))
                        return post(pred,**kwargs)
                recognizer.post_op=Capture()
            try:responses=list(recognizer.predict(str(path)))
            finally:recognizer.post_op=post
            if len(responses)!=1:raise ValueError('recognition-only result mismatch')
            raw=responses[0].json
            if isinstance(raw,str):raw=json.loads(raw)
            d=raw.get('res',raw)
            with Image.open(path) as im:w,h=im.size
            return dict(raw=raw,status='completed',**({'ctc':captured[0],'sequence_evidence':captured[0]['sequence_evidence']} if captured else {}),spans=[dict(text_raw=d['rec_text'],score_raw=d['rec_score'],
                score_kind='paddle_rec_score',granularity='region',polygon_input_px=polygon([0,0,w,h]),
                coordinate_source='crop_region',raw_output_index=0)],internal=dict(actual_transforms=list(self.events),detection=False))
        # 页级行均分会掩盖个别弱字符；复用同次前向输出，仅保存实际发射位置。
        # 识别批次可能按宽度重排，不能把捕获数组下标当成检测行下标。
        import numpy as np
        recognizer=self.model.paddlex_pipeline.text_rec_model;post=recognizer.post_op;emissions=[]
        class CaptureEmissions:
            def __call__(self,pred,**kwargs):
                decoded=post(pred,**kwargs)
                for probabilities,text,score in zip(np.asarray(pred[0]),decoded[0],decoded[1]):
                    if not isinstance(text,str):continue
                    ids=probabilities.argmax(-1);tokens=[]
                    for i,label in enumerate(ids):
                        if label==0 or (i and label==ids[i-1]):continue
                        tokens.append(dict(text=post.character[int(label)],timestep=i,posterior=float(probabilities[i,label])))
                    if ''.join(t['text'] for t in tokens)==text:
                        emissions.append(dict(text=text,line_score=score,tokens=tokens,
                            scope='ctc_emissions_not_calibrated_correctness_or_glyph_boxes',frames=len(ids)))
                return decoded
        recognizer.post_op=CaptureEmissions()
        try:responses=list(self.model.predict(str(path)))
        finally:recognizer.post_op=post
        if len(responses)!=1: raise ValueError('expected one image result')
        raw=responses[0].json
        if isinstance(raw,str):raw=json.loads(raw)
        d=raw.get('res',raw)
        texts=d.get('rec_texts',[]); scores=d.get('rec_scores',[]); polys=d.get('rec_polys',[])
        if not len(texts)==len(scores)==len(polys):raise ValueError('SDK output mismatch')
        spans=[dict(text_raw=t,score_raw=s,score_kind='paddle_rec_score',granularity='line',
                    polygon_input_px=p,coordinate_source='engine_polygon',raw_output_index=i)
               for i,(t,s,p) in enumerate(zip(texts,scores,polys))]
        for span in spans:
            matches=[e for e in emissions if e['text']==span['text_raw'] and abs(e['line_score']-span['score_raw'])<1e-7]
            if len(matches)==1:span['ctc_emission_evidence']=matches[0]
        return dict(raw=raw,spans=spans,status='completed',internal=dict(
            sdk_filtered=True,network_tensor='observed_shapes_only',actual_transforms=list(self.events),det_limit_side_len=64,det_limit_type='min',
            det_max_side_limit=4000,rec_shape='model config; dynamic width; tensors not_observed'))


class PaddleRec:
    """Diagnostic only, valid for separately established single-line images."""
    def __init__(self,private,run):
        os.environ['PADDLE_PDX_CACHE_HOME']=str(Path(private)/'models')
        os.environ['PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK']='True'
        os.environ['HF_HUB_OFFLINE']='1'
        from paddleocr import TextRecognition
        self.model=TextRecognition(model_name='PP-OCRv6_medium_rec',
            model_dir=str(Path(private)/'models/official_models/PP-OCRv6_medium_rec'),
            device='cpu',cpu_threads=2,enable_mkldnn=False)

    def predict(self,path,granularity='line'):
        from PIL import Image
        import json
        response=list(self.model.predict(str(path)))
        if len(response)!=1:raise ValueError('recognition result length mismatch')
        raw=response[0].json
        if isinstance(raw,str):raw=json.loads(raw)
        d=raw.get('res',raw)
        with Image.open(path) as im:w,h=im.size
        return dict(raw=raw,status='completed',spans=[dict(text_raw=d['rec_text'],score_raw=d['rec_score'],
            score_kind='paddle_rec_score',granularity='region',polygon_input_px=polygon([0,0,w,h]),
            coordinate_source='crop_region',raw_output_index=0)],internal=dict(network_tensor='not_observed',detection=False))


def assemble(spans):
    """Preserve engine order and repeated text; no semantic correction."""
    lines=[]; prev=None
    for s in spans:
        key=s.get('line_id')
        if key is not None and key==prev:lines[-1]+=' '+s['text_raw']
        else:lines.append(s['text_raw'])
        prev=key
    return '\n'.join(lines)
