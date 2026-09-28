"""跨隔离Python环境的单模型宿主；只在本机管道接收显式PDF路径。"""
import contextlib
import json
import sys
import traceback
from pathlib import Path
from experimental_parser import ExperimentalParser
from common import dump


def main():
    run=Path(sys.argv[1]).resolve();parser=ExperimentalParser(run)
    import importlib.metadata
    dump(run/'runtime-source.json',dict(runtime_dir=str(Path(__file__).resolve().parent),
        runtime_version=parser.config.get('runtime_version','external_unversioned'),
        model_det=parser.config['model_det'],model_rec=parser.config['model_rec'],
        layout_model=parser.config.get('layout_model'),
        packages={name:importlib.metadata.version(name) for name in ('paddleocr','paddlex','paddlepaddle')}))
    try:
        for line in sys.stdin:
            request=json.loads(line)
            if request['operation']=='close':break
            try:
                with contextlib.redirect_stdout(sys.stderr):
                    if request['operation']=='parse':
                        pages=parser.parse_file(request['path'])
                        response={'ok':True,'path':str(run/'consumers'/f'doc{parser.sequence}.json')}
                    elif request['operation']=='predict':
                        response={'ok':True,'result':parser.engine.predict(request['sample'])}
                    else:raise ValueError('unsupported operation')
                response['model_pid']=parser.engine.engine.p.pid
            except Exception:
                response={'ok':False,'error':traceback.format_exc()}
            print('OCR_HOST_RESPONSE '+json.dumps(response,ensure_ascii=False),flush=True)
    finally:
        if parser.engine:
            dump(run/'resident-resource.json',dict(model_pid=parser.engine.engine.p.pid,
                 peak_rss_tree_bytes=parser.engine.peak,model_load_seconds=parser.engine.load,
                 startup_seconds=parser.engine.startup,model_instances=1,queue_capacity=2))
        parser.close()


if __name__=='__main__':main()
