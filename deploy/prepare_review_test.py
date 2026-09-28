"""准备本机隔离测试产品；复用指定缓存，不下载/升级、不访问生产DB。"""
import argparse,os,shutil,subprocess,sys,json
from pathlib import Path
CORE='common adapters observe imaging pipeline replay runtime_queue experimental_parser model_host model_bridge content_inputs content_recovery short_symbols segmentation text_roles product_consumer'.split()

def main():
 p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--private',type=Path,required=True);p.add_argument('--business-python',type=Path,required=True);p.add_argument('--node-bin',type=Path,required=True);a=p.parse_args()
 repo=Path(__file__).resolve().parents[1];root=a.root.resolve();root.mkdir(parents=True,exist_ok=True)
 # 独立进程有截止时间；读取失败报绝对路径，绝不以空内容替代依赖。
 check='''import sys, pathlib, importlib.metadata as m, json
root=pathlib.Path(sys.argv[1]);sizes={}
for model in ('PP-OCRv6_medium_det','PP-OCRv6_medium_rec'):
 for name in ('inference.json','inference.yml','inference.pdiparams'):
  p=root/'models/official_models'/model/name;print('READ '+str(p),flush=True);n=0
  with p.open('rb') as f:
   while block:=f.read(1048576):n+=len(block)
  if not n:raise ValueError('empty required model file '+str(p))
  sizes[str(p)]=n
import paddleocr,cv2,pymupdf,psutil
print(json.dumps({'packages':{x:m.version(x) for x in ('paddleocr','paddlepaddle','paddlex','numpy','PyMuPDF')},'model_bytes':sizes}))
'''
 with (root/'offline-preflight.log').open('w') as log:
  subprocess.run([str(a.private/'venv/bin/python'),'-c',check,str(a.private.resolve())],check=True,timeout=90,stdout=log,stderr=log,env={**os.environ,'HF_HUB_OFFLINE':'1','PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK':'True'})
 dest=root/'release/agent'
 for file in (repo/'agent_backend').rglob('*.py'):
  target=dest/file.relative_to(repo);target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(file,target)
 shutil.copy2(repo/'__init__.py',dest/'__init__.py')
 code=dest/'prototype/ocr_local_trial';code.mkdir(parents=True,exist_ok=True)
 for name in CORE:shutil.copy2(repo/'prototype/ocr_local_trial'/f'{name}.py',code/f'{name}.py')
 deploy=dest/'deploy';deploy.mkdir(exist_ok=True);shutil.copy2(repo/'deploy/review_test_app.py',deploy/'review_test_app.py')
 front=root/'frontend-source';front.mkdir(exist_ok=True)
 for name in ('src','public'):shutil.copytree(repo/'agent_fronted'/name,front/name,dirs_exist_ok=True)
 # 旧测试登录组件只曾位于此路径；增量复制后移除该已废弃文件。
 obsolete=front/'src/components/ReviewTestSession.vue'
 if obsolete.is_file() and not (repo/'agent_fronted/src/components/ReviewTestSession.vue').exists():obsolete.unlink()
 for name in ('package.json','package-lock.json','babel.config.js','vue.config.js'):shutil.copy2(repo/'agent_fronted'/name,front/name)
 env={**os.environ,'PATH':str(a.node_bin)+os.pathsep+os.environ['PATH'],'NODE_OPTIONS':'--openssl-legacy-provider'}
 # 已有离线安装由同一锁文件生成；有变化时npm ci仍严格按锁文件恢复。
 npm=shutil.which('npm',path=env['PATH'])
 if not npm:raise FileNotFoundError('npm not found; restore the existing Node/npm runtime')
 with (root/'frontend-build.log').open('w') as log:
  subprocess.run([npm,'ci','--offline','--no-audit','--no-fund'],cwd=front,env=env,check=True,timeout=180,stdout=log,stderr=log)
  subprocess.run([npm,'run','build','--','--dest',str(root/'frontend')],cwd=front,env=env,check=True,timeout=180,stdout=log,stderr=log)
 config=dict(root=str(root),business_python=str(a.business_python.absolute()),private=str(a.private.resolve()),code=str(code),release=str(root/'release'),frontend=str(root/'frontend'))
 (root/'test-runtime.json').write_text(json.dumps(config,ensure_ascii=False,indent=2))
 print('本机测试版本准备完成；配置：'+str(root/'test-runtime.json'))
if __name__=='__main__':main()
