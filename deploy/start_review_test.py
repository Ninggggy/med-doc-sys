"""启动已准备好的单进程测试产品；没有登录或模型下载步骤。"""
import argparse,json,os
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--port',type=int,default=5073);a=p.parse_args()
c=json.loads(a.config.read_text());root=Path(c['root'])
env={**os.environ,'PYTHONPATH':c['release'],'FILING_PDF_BACKEND':'ppocr_v6_medium',
 'FILING_PADDLE_PRIVATE':c['private'],'FILING_PADDLE_CODE':c['code'],'FILING_PADDLE_RUNTIME':str(root/'model-runs'),
 'HF_HUB_OFFLINE':'1','PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK':'True'}
os.chdir(c['release'])
os.execve(c['business_python'],[c['business_python'],'-u',str(Path(c['release'])/'agent/deploy/review_test_app.py'),
 '--data',str(root/'test-data'),'--frontend',c['frontend'],'--port',str(a.port)],env)
