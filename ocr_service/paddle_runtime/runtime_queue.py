"""Bounded local admission around one resident model, no HTTP or database."""
import queue
import threading
import time
from concurrent.futures import Future


class ModelQueue:
    def __init__(self,engine,capacity=4):
        self.engine=engine;self.capacity=threading.BoundedSemaphore(capacity)
        self.queue=queue.Queue();self.pending={};self.lock=threading.Lock();self.closed=False
        self.thread=threading.Thread(target=self._work,daemon=False);self.thread.start()

    def submit(self,sample,timeout=None):
        with self.lock:
            if self.closed:raise RuntimeError('queue closed')
            if sample['sample_id'] in self.pending:raise ValueError('duplicate request')
            if not self.capacity.acquire(blocking=False):raise queue.Full('model queue full')
            future=Future();cancel=threading.Event();self.pending[sample['sample_id']]=cancel
            self.queue.put((sample,future,cancel,time.monotonic(),timeout))
        return future

    def cancel(self,sample_id):
        with self.lock:
            if sample_id in self.pending:self.pending[sample_id].set()

    def _work(self):
        try:
            while True:
                item=self.queue.get()
                if item is None:break
                sample,future,cancel,submitted,timeout=item
                try:
                    started=time.monotonic()
                    if cancel.is_set():result=dict(status='cancelled_before_execution',text_assembled='')
                    else:result=self.engine.predict(sample,timeout=timeout,cancel_event=cancel)
                    result['queue_seconds']=started-submitted
                    result['request_seconds']=time.monotonic()-submitted
                    future.set_result(result)
                except BaseException as exc:future.set_exception(exc)
                finally:
                    with self.lock:self.pending.pop(sample['sample_id'],None)
                    self.capacity.release() # only after inference returns / process has been reaped
        finally:self.engine.close()

    def close(self):
        with self.lock:
            self.closed=True
            for event in self.pending.values():event.set()
            self.queue.put(None)
        self.thread.join()
