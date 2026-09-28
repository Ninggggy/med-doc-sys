"""Local-only experiment utilities. No application bootstrap or database imports."""
import json
import os
import signal
import subprocess
import time
from pathlib import Path


def dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    tmp.replace(path)


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def run_process(args, timeout=60, env=None):
    """Reap the actual process group before returning timeout capacity."""
    start = time.monotonic()
    p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         start_new_session=True, env=env)
    state = 'completed'
    try:
        out, err = p.communicate(timeout=timeout)
    except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
        state = 'timeout' if isinstance(exc, subprocess.TimeoutExpired) else 'cancelled'
        os.killpg(p.pid, signal.SIGKILL)
        out, err = p.communicate()
    return dict(status=state if state != 'completed' or p.returncode == 0 else 'failed',
                returncode=p.returncode, stdout=out.decode('utf-8', 'replace'),
                stderr=err.decode('utf-8', 'replace'), seconds=time.monotonic()-start,
                pid=p.pid, reaped=p.poll() is not None)


def progress(run, stage, message):
    with (Path(run)/'progress.md').open('a', encoding='utf-8') as f:
        f.write(f'\n- {time.strftime("%Y-%m-%d %H:%M:%S")} {stage}: {message}\n')


def polygon(box):
    x0,y0,x1,y1=box
    return [[x0,y0],[x1,y0],[x1,y1],[x0,y1]]


def map_points(points, matrix):
    import numpy as np
    p=np.c_[np.asarray(points),np.ones(len(points))] @ np.asarray(matrix).T
    return (p[:,:2]/p[:,2:]).tolist()


def validate_batch(ids, outputs):
    if len(outputs) != len(ids) or len(set(ids)) != len(ids):
        raise ValueError('batch length/identity mismatch')
    by_id={o['sample_id']:o for o in outputs}
    if len(by_id)!=len(ids) or set(by_id)!=set(ids):
        raise ValueError('batch identity mismatch')
    return [by_id[i] for i in ids]
