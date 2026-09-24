"""显式开发/测试开关：普通材料与申请表共用同一驻留模型及有界入口。"""
import atexit
import importlib.util
import json
import os
from pathlib import Path
import sys
import threading
import uuid

_instance=None
_lock=threading.Lock()


def enabled():
    return os.getenv('FILING_PDF_BACKEND','existing')=='ppocr_v6_medium'


class LocalPaddle:
    def __init__(self):
        code=Path(os.environ['FILING_PADDLE_CODE']).resolve()
        private=Path(os.environ['FILING_PADDLE_PRIVATE']).resolve()
        root=Path(os.environ['FILING_PADDLE_RUNTIME']).resolve()
        repo=Path(__file__).resolve().parents[2]
        self.run=root/('session-'+uuid.uuid4().hex)
        self.run.mkdir(parents=True)
        config=dict(repo_root=str(repo),private_root=str(private),run_dir=str(self.run),
            model_det='PP-OCRv6_medium_det',model_rec='PP-OCRv6_medium_rec',scale=3,page_scale=3,geometry_scale=3,
            crop_timeout=60,page_timeout=300,document_timeout=1800,total_timeout=7200,cpu_threads=2,
            max_pixels=16000000,concurrency=1,model_load_timeout=300,content_recovery=True,text_roles=True,
            sparse_ink_diagnostic=True,consumer='product')
        (self.run/'config.effective.json').write_text(json.dumps(config,ensure_ascii=False,indent=2))
        sys.path.insert(0,str(code))
        spec=importlib.util.spec_from_file_location('filing_local_model_bridge',code/'model_bridge.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        self.bridge=module.ModelBridge(self.run);self.capacity=threading.BoundedSemaphore(2)
        atexit.register(self.bridge.close)

    def parse_file(self,path):
        from agent.agent_backend.services.filing_parse_outcome import ParseFailure
        if not self.capacity.acquire(blocking=False):raise ParseFailure('ocr_busy')
        try:return self.bridge.parse_file(path)
        finally:self.capacity.release()


def parse_pdf(path):
    global _instance
    with _lock:
        if _instance is None:_instance=LocalPaddle()
    return _instance.parse_file(path)
