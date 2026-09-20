import asyncio
import importlib.util
import io
from pathlib import Path
import threading
import shutil
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from starlette.datastructures import UploadFile

spec = importlib.util.spec_from_file_location('audit_ocr_app', Path(__file__).resolve().parents[1]/'ocr_service/app.py')
ocr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ocr)


class OCRCapacityTests(unittest.IsolatedAsyncioTestCase):
    def test_thin_symbol_preservation_is_local_and_budgeted(self):
        gray = np.full((80,120),255,dtype=np.uint8)
        gray[39:41,30:40] = 70
        gray[25:55,44:49] = 40
        processed = np.where(gray < 128,0,255).astype(np.uint8)
        result = ocr.preserve_thin_symbols(gray,processed,time.monotonic()+5)
        self.assertTrue(np.array_equal(result[38:42,29:41],gray[38:42,29:41]))
        mask = np.ones(gray.shape,dtype=bool)
        mask[38:42,29:41] = False
        self.assertTrue(np.array_equal(result[mask],processed[mask]))
        isolated = np.full_like(gray,255)
        isolated[39:41,30:40] = 70
        self.assertTrue(np.array_equal(ocr.preserve_thin_symbols(isolated,processed,time.monotonic()+5),processed))
        with self.assertRaises(ocr.OCRExecutionTimeout):
            ocr.preserve_thin_symbols(gray,processed,time.monotonic()-1)

    async def test_asgi_queue_overflow_budget_health_and_recovery(self):
        import httpx
        pool = ocr.OCRExecutor(concurrency=1, waiting=4, queue_timeout=30)
        entered, finish = threading.Event(), threading.Event()
        def slow(*args):
            entered.set()
            if not finish.wait(5):
                raise AssertionError('test cleanup deadline exceeded')
            return {'data': {'text': ['SYNTHETIC']}}
        tasks = []
        with patch.object(ocr, 'executor', pool), patch.object(ocr, 'recognize', side_effect=slow):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ocr.app), base_url='http://isolated') as client:
                async def request(budget=None):
                    return await client.post('/ocr/data', files={'file': ('synthetic.png', b'fixture', 'image/png')},
                                             data={} if budget is None else {'execution_budget_seconds': budget})
                async def until(predicate):
                    deadline = time.monotonic() + 2
                    while not predicate() and time.monotonic() < deadline:
                        await asyncio.sleep(.005)
                    self.assertTrue(predicate())
                try:
                    first = asyncio.create_task(request())
                    tasks.append(first)
                    await until(entered.is_set)
                    tasks.extend(asyncio.create_task(request('0.8')) for _ in range(4))
                    await until(lambda: pool.admitted == 5)
                    overflow = await request()
                    self.assertEqual(overflow.status_code, 503)
                    self.assertEqual(overflow.json(), {'error': 'ocr_busy'})
                    self.assertEqual(overflow.headers['retry-after'], '2')
                    for _ in range(20):
                        response = await client.get('/health')
                        self.assertEqual(response.status_code, 200)
                        self.assertEqual(response.json(), {'status': 'ok'})
                    for waiting in tasks[1:]:
                        response = await waiting
                        self.assertEqual(response.status_code, 504)
                        self.assertEqual(response.json(), {'error': 'ocr_timeout'})
                    self.assertEqual(pool.admitted, 1)
                    first.cancel()
                    with self.assertRaises(asyncio.CancelledError):
                        await first
                    self.assertEqual(pool.admitted, 1)
                finally:
                    finish.set()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    await until(lambda: pool.admitted == 0)
                self.assertEqual((await request()).status_code, 200)

    @unittest.skipUnless(shutil.which('tesseract'), 'requires actual isolated Tesseract')
    async def test_real_tesseract_timeout_is_reaped(self):
        spawned=[]
        original=ocr.pytesseract.pytesseract.subprocess.Popen
        def capture(*args, **kwargs):
            child=original(*args, **kwargs)
            spawned.append(child)
            return child
        start=time.monotonic()
        # 固定预算时钟，让这个用例仍真实启动子进程并验证超时回收，
        # 而不是在图像处理耗尽1ms后尚未启动就退出。
        with patch.object(ocr, 'OCR_EXECUTION_TIMEOUT', .001), patch.object(ocr, 'time', SimpleNamespace(monotonic=lambda: 10)), patch.object(ocr.pytesseract.pytesseract.subprocess,'Popen',side_effect=capture):
            with self.assertRaises(ocr.OCRExecutionTimeout):
                await asyncio.to_thread(ocr.recognize, ocr.cv2.imencode('.png',np.zeros((600,600,3),dtype=np.uint8))[1].tobytes(), 'eng', 6, 'text')
        self.assertLess(time.monotonic()-start,5)
        self.assertTrue(spawned)
        self.assertTrue(all(child.poll() is not None for child in spawned))

    async def test_running_work_does_not_block_loop_or_release_on_cancel(self):
        pool = ocr.OCRExecutor(concurrency=1, waiting=1, queue_timeout=.05)
        entered, finish = threading.Event(), threading.Event()
        def slow():
            entered.set()
            finish.wait(3)
            return 'done'
        first = asyncio.create_task(pool.run(slow))
        try:
            while not entered.is_set():
                await asyncio.sleep(.001)
            self.assertEqual(ocr.health(), {'status':'ok'})
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first
            self.assertEqual(pool.admitted, 1)
            waiting = asyncio.create_task(pool.run(lambda:'next'))
            await asyncio.sleep(.001)
            with self.assertRaises(ocr.OCRCapacityError):
                await pool.run(lambda:'overflow')
            with self.assertRaises(ocr.OCRCapacityError):
                await waiting
            self.assertEqual(pool.admitted, 1)
        finally:
            finish.set()
            for _ in range(200):
                if pool.admitted == 0: break
                await asyncio.sleep(.005)
        self.assertEqual(pool.admitted, 0)
        self.assertEqual(await pool.run(lambda:'next'), 'next')

    async def test_http_classification_and_sanitization(self):
        file = UploadFile(io.BytesIO(b'synthetic'))
        for error, status in ((ocr.OCRCapacityError(),503), (ocr.OCRExecutionTimeout(),504), (RuntimeError('SENSITIVE'),500)):
            with patch.object(ocr.executor, 'run', side_effect=error):
                response = await ocr.run_request(file,'eng',6,'text')
                self.assertEqual(response.status_code,status)
                self.assertNotIn(b'SENSITIVE',response.body)

    async def test_subprocess_timeout_is_passed_to_both_endpoints(self):
        image = np.zeros((10,10,3), dtype=np.uint8)
        with patch.object(ocr, 'read_image', return_value=image):
            for kind, method in (('text','image_to_string'),('data','image_to_data')):
                with patch.object(ocr.pytesseract, method, side_effect=RuntimeError('Tesseract process timeout')) as call:
                    with self.assertRaises(ocr.OCRExecutionTimeout):
                        ocr.recognize(b'', 'eng', 6, kind)
                    # 图像处理和后续数值复核共用原120秒预算，不能给子调用重置时间。
                    self.assertGreater(call.call_args.kwargs['timeout'], 0)
                    self.assertLessEqual(call.call_args.kwargs['timeout'],120)


if __name__ == '__main__':
    unittest.main()
