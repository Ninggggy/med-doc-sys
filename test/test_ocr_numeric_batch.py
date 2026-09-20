"""批数值复核协议及失败保护；不放宽数值相等规则。"""
import importlib.util
from pathlib import Path
import subprocess
import os
import time
import unittest
from unittest.mock import patch
import numpy as np

spec=importlib.util.spec_from_file_location('batch_ocr',Path(__file__).resolve().parents[1]/'ocr_service/app.py')
ocr=importlib.util.module_from_spec(spec)
spec.loader.exec_module(ocr)


class NumericBatchTests(unittest.TestCase):
    @unittest.skipUnless(os.getenv('RUN_LOCAL_OCR_PROTOCOL')=='1','需显式本地子进程实验')
    def test_real_timeout_reaps_tesseract_process(self):
        processes=[]
        real_popen=subprocess.Popen
        real_run=subprocess.run
        def tracked(*args,**kwargs):
            process=real_popen(*args,**kwargs)
            processes.append(process)
            return process
        def short_run(*args,**kwargs):
            kwargs['timeout']=min(kwargs['timeout'],.01)
            return real_run(*args,**kwargs)
        with patch.object(ocr.subprocess,'Popen',side_effect=tracked), \
                patch.object(ocr.subprocess,'run',side_effect=short_run):
            with self.assertRaises(subprocess.TimeoutExpired):
                ocr.numeric_batch_strings(self.images,'eng',time.monotonic()+10)
        self.assertEqual(len(processes),1)
        self.assertIsNotNone(processes[0].poll())

    def setUp(self):
        self.images=[np.full((20,80),255,dtype=np.uint8) for _ in range(3)]

    def test_count_preserves_blank_first_middle_last_and_optional_terminator(self):
        for payload,expected in ((b'\f1\f',['','1','']),
                                 (b'1\f\f2',['1','','2']),
                                 (b'\f1\f\f',['','1',''])):
            with self.subTest(payload=payload), patch.object(ocr.time,'monotonic',return_value=10), \
                    patch.object(ocr.subprocess,'run',return_value=subprocess.CompletedProcess([],0,payload)) as run:
                self.assertEqual(ocr.numeric_batch_strings(self.images,'eng',100),expected)
                self.assertEqual(run.call_args.kwargs['timeout'],90)
                self.assertFalse(Path(run.call_args.args[0][1]).exists())

    def test_missing_or_extra_results_are_rejected_not_shifted(self):
        for payload in (b'1\f2',b'1\f2\f3\f4'):
            with patch.object(ocr.time,'monotonic',return_value=10), \
                    patch.object(ocr.subprocess,'run',return_value=subprocess.CompletedProcess([],0,payload)):
                with self.assertRaisesRegex(ValueError,'output_mismatch'):
                    ocr.numeric_batch_strings(self.images,'eng',100)

    def test_expired_budget_starts_no_process(self):
        with patch.object(ocr.time,'monotonic',return_value=100),patch.object(ocr.subprocess,'run') as run:
            with self.assertRaises(ocr.OCRExecutionTimeout):
                ocr.numeric_batch_strings(self.images,'eng',100)
            run.assert_not_called()

    def test_failure_or_timeout_does_not_retry_and_removes_inputs(self):
        for error in (subprocess.TimeoutExpired('tesseract',1),subprocess.CalledProcessError(1,'tesseract')):
            with patch.object(ocr.time,'monotonic',return_value=10), \
                    patch.object(ocr.subprocess,'run',side_effect=error) as run:
                with self.assertRaises(type(error)):
                    ocr.numeric_batch_strings(self.images,'eng',100)
                self.assertEqual(run.call_count,1)
                self.assertFalse(Path(run.call_args.args[0][1]).exists())

    def test_chinese_one_is_not_normalized_to_minus(self):
        data={'text':['-1.25'],'left':[20],'top':[10],'width':[30],'height':[10]}
        with patch.object(ocr.time,'monotonic',return_value=10), \
                patch.object(ocr.pytesseract,'image_to_string',return_value='一1.25'):
            result=ocr.verify_numeric_regions(np.full((50,100),255,dtype=np.uint8),data,'eng',100)
        self.assertEqual(result[0]['status'],'numeric_uncertain')
        self.assertEqual(result[0]['primary'],'-1.25')
        self.assertEqual(result[0]['secondary'],'一1.25')

    def test_completed_batch_survives_later_failure_without_retry(self):
        data={'text':[str(i) for i in range(8)],'left':[20]*8,
              'top':[10+40*i for i in range(8)],'width':[30]*8,'height':[10]*8}
        with patch.object(ocr.time,'monotonic',return_value=10), \
                patch.object(ocr,'numeric_batch_strings',side_effect=[
                    ['0','1','2','3'],subprocess.TimeoutExpired('tesseract',1)]) as batch:
            result=ocr.verify_numeric_regions(np.full((400,100),255,dtype=np.uint8),data,'eng',100)
        self.assertEqual(batch.call_count,2)
        self.assertTrue(all(len(call.args[0])==4 and call.args[2]==100 for call in batch.call_args_list))
        self.assertEqual([item['status'] for item in result],['verified']*4+['numeric_uncertain']*4)
        self.assertEqual([item['primary'] for item in result],data['text'])


if __name__=='__main__':
    unittest.main()
