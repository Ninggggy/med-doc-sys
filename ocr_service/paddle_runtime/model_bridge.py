import json
import subprocess
import threading
import time
import select
from pathlib import Path
from common import read


class ModelBridge:
    def __init__(self,run):
        self.run=Path(run);self.config=read(self.run/'config.effective.json')
        self.lock=threading.Lock();self.process=None;self.model_pids=[]
    def request(self,request):
        with self.lock:
            if self.process is None:
                self.log=(self.run/'model-host.log').open('a')
                python=Path(self.config['private_root'])/'venv/bin/python'
                self.process=subprocess.Popen([str(python),'-u',str(Path(__file__).with_name('model_host.py')),str(self.run)],
                    stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.log,text=True)
            self.process.stdin.write(json.dumps(request)+'\n');self.process.stdin.flush()
            deadline=time.monotonic()+self.config['document_timeout']+self.config['model_load_timeout']+60
            while True:
                remaining=deadline-time.monotonic()
                if remaining<=0:raise TimeoutError('local model host response deadline')
                if not select.select([self.process.stdout],[],[],min(remaining,1))[0]:continue
                line=self.process.stdout.readline()
                if not line:raise RuntimeError('model host exited; see model-host.log')
                if line.startswith('OCR_HOST_RESPONSE '):break
                self.log.write(line);self.log.flush()
            response=json.loads(line[len('OCR_HOST_RESPONSE '):])
            if not response['ok']:raise RuntimeError(response['error'])
            self.model_pids.append(response['model_pid'])
            return response
    def parse_file(self,path):return read(self.request({'operation':'parse','path':str(path)})['path'])
    def predict(self,sample):return self.request({'operation':'predict','sample':sample})['result']
    def close(self):
        if self.process:
            if self.process.poll() is None:
                self.process.stdin.write('{"operation":"close"}\n');self.process.stdin.flush()
                self.process.wait(timeout=30)
            self.log.close();self.process=None
