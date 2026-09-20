import asyncio
import time
import unittest
from unittest.mock import AsyncMock, patch
from test_ocr_numeric_verification import ocr


class RemainingBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_budget_is_rejected_before_file_or_worker(self):
        for value in ('nan','inf','0','-1','oops'):
            file = AsyncMock()
            response = await ocr.run_request(file,'eng',6,'data',value)
            self.assertEqual(response.status_code,400)
            file.read.assert_not_called()

    async def test_request_cannot_extend_existing_limit(self):
        file = AsyncMock(); file.read.return_value=b'fake'
        with patch.object(ocr.executor,'run',new_callable=AsyncMock) as run:
            start=time.monotonic()
            await ocr.run_request(file,'eng',6,'data','9999')
            self.assertLessEqual(run.call_args.kwargs['admission_deadline']-start,ocr.OCR_EXECUTION_TIMEOUT+0.1)

    async def test_queue_respects_remaining_budget_without_running_worker(self):
        executor=ocr.OCRExecutor(concurrency=1,waiting=1,queue_timeout=30)
        await executor.semaphore.acquire()
        called=[]
        with self.assertRaises(ocr.OCRExecutionTimeout):
            await executor.run(lambda:called.append(1),admission_deadline=time.monotonic()+0.02)
        self.assertEqual(called,[])
        self.assertEqual(executor.admitted,0)
        executor.semaphore.release()

if __name__=='__main__':unittest.main()
