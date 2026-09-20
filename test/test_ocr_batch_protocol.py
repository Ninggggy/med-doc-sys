"""现有Tesseract清单输入协议验证；未接入生产批处理。"""
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

import cv2
import numpy as np
import pytesseract


@unittest.skipUnless(os.getenv('RUN_LOCAL_OCR_PROTOCOL') == '1', '仅显式本地OCR实验')
class BatchProtocolTests(unittest.TestCase):
    def test_empty_first_middle_last_regions_preserve_count_and_order(self):
        values=['', '-1.25', '', '0.20', '90%', '']
        deadline=time.monotonic()+120
        with tempfile.TemporaryDirectory(prefix='ocr-protocol-') as directory:
            paths=[]
            expected=[]
            for index,value in enumerate(values):
                pixels=np.full((70,260),255,dtype=np.uint8)
                if value:
                    cv2.putText(pixels,value,(15,48),cv2.FONT_HERSHEY_SIMPLEX,1.1,0,2,cv2.LINE_AA)
                path=Path(directory)/f'{index}.png'
                self.assertTrue(cv2.imwrite(str(path),pixels))
                paths.append(str(path))
                expected.append(pytesseract.image_to_string(pixels,lang='chi_sim+eng',
                    config='--oem 1 --psm 7',timeout=max(.001,deadline-time.monotonic())).strip())
            listing=Path(directory)/'images.txt'
            listing.write_text('\n'.join(paths)+'\n',encoding='utf8')
            result=subprocess.run(['tesseract',str(listing),'stdout','-l','chi_sim+eng',
                                   '--oem','1','--psm','7'],capture_output=True,check=True,
                                  timeout=max(.001,deadline-time.monotonic()))
            chunks=result.stdout.decode('utf8').split('\f')
            if len(chunks)==len(values)+1 and not chunks[-1].strip():
                chunks.pop()
            actual=[chunk.strip() for chunk in chunks]
            self.assertEqual(len(actual),len(values))
            self.assertEqual(actual,expected)
            for index in (1,3,4):
                self.assertEqual(actual[index],values[index])


if __name__ == '__main__':
    unittest.main()
