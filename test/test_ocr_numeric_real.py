"""使用已确认负号可见的合成审计输入，真实运行当前Tesseract入口。"""
from pathlib import Path
import os
import json
import time
import unittest

import cv2

from test_ocr_numeric_verification import ocr


class NumericRealTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('NUMERIC_SAMPLE_DIR'), '需要已保存的真实OCR采样反例')
    def test_large_raster_digits_cannot_silently_agree_on_wrong_value(self):
        root = Path(os.environ['NUMERIC_SAMPLE_DIR'])
        gray = cv2.imread(str(root / 'ocr-input.png'), cv2.IMREAD_GRAYSCALE)
        page = json.loads((root / 'original-result.json').read_text())[-1]
        words = [w for w in page['words'] if w['source'] == 'ocr']
        # 从保存的实际主识别词框复核，不把正确答案传给业务函数。
        # 局部区域源于PDF (30,90)，渲染比例为4。
        data = {'text': [], 'left': [], 'top': [], 'width': [], 'height': []}
        for w in words:
            x0,y0,x1,y1 = w['bbox']
            for key, value in [('text',w['text']),('left',round((x0-30)*4)),
                               ('top',round((y0-90)*4)),('width',round((x1-x0)*4)),
                               ('height',round((y1-y0)*4))]:
                data[key].append(value)
        checks = ocr.verify_numeric_regions(gray, data, 'eng', time.monotonic()+120)
        wrong = [i for i, text in enumerate(data['text']) if text == '90.0']
        self.assertEqual(len(wrong), 2)
        for i in wrong:
            check = next(c for c in checks if i in c['word_indices'])
            self.assertEqual(check['status'], 'numeric_uncertain', check)
            self.assertEqual(check['primary'], '90.0')
            self.assertEqual(check['secondary'], '50.0')

    def test_negative_number_is_correct_or_has_explicit_uncertainty(self):
        source = Path(__file__).parent / 'current_system_audit_20260918/ocr-negative/ocr-input.png'
        self.assertTrue(source.is_file(), '审计原始合成图片必须存在，不用跳过代替验证')
        result = ocr.recognize(source.read_bytes(), 'eng', 3, 'data')['data']
        values = [(i, str(v)) for i, v in enumerate(result['text']) if '1.25' in str(v)]
        self.assertTrue(values, '主识别必须保留可用数值文字')
        for index, value in values:
            evidence = [v for v in result['numeric_verification'] if index in v['word_indices']]
            self.assertEqual(len(evidence), 1)
            if value != '-1.25':
                self.assertEqual(evidence[0]['status'], 'numeric_uncertain')
            self.assertTrue(evidence[0]['primary'])
            self.assertTrue(evidence[0]['bbox_pixel'])


if __name__ == '__main__':
    unittest.main()
