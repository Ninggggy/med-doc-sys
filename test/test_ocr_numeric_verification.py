"""数值局部复核的独立行为用例；不把OCR候选当成真实值。"""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np

spec = importlib.util.spec_from_file_location('numeric_ocr_app', Path(__file__).resolve().parents[1] / 'ocr_service/app.py')
ocr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ocr)


def data(texts=('1.25',), lefts=(40,)):
    n = len(texts)
    return dict(text=list(texts), left=list(lefts), top=[20]*n, width=[30]*n,
                height=[10]*n, conf=[99]*n, block_num=[1]*n, par_num=[1]*n, line_num=[1]*n)


class NumericVerificationTests(unittest.TestCase):
    def verify(self, primary, secondary, deadline=100):
        gray = np.full((80, 180), 255, dtype=np.uint8)
        with patch.object(ocr.time, 'monotonic', return_value=10), patch.object(
                ocr.pytesseract, 'image_to_string', return_value=secondary) as call:
            result = ocr.verify_numeric_regions(gray, primary, 'eng', deadline)
        return result, call

    def test_high_confidence_disagreement_preserves_candidates_and_left_margin(self):
        results, call = self.verify(data(), '-1.25')
        self.assertEqual(len(results), 1)
        item = results[0]
        self.assertEqual(item['status'], 'numeric_uncertain')
        self.assertEqual(item['reason_code'], 'numeric_candidates_disagree')
        self.assertEqual(item['primary'], '1.25')
        self.assertEqual(item['secondary'], '-1.25')
        self.assertLessEqual(item['bbox_pixel'][0], 30)
        self.assertIn('--psm 7', call.call_args.kwargs['config'])
        self.assertEqual(call.call_args.kwargs['timeout'], 90)

    def test_sign_decimal_exponent_comparator_and_unit_are_not_discarded(self):
        for primary, secondary in [('1.2','12'), ('1e-3','1e3'), ('<1','1'),
                                   ('1mg','1μg'), ('-1','1'), ('1',''), ('1 2', '12')]:
            with self.subTest(primary=primary):
                result, _ = self.verify(data((primary,)), secondary)
                self.assertEqual(result[0]['status'], 'numeric_uncertain')
        result, _ = self.verify(data(('-1.25',)), '-1.25\n')
        self.assertEqual(result[0]['status'], 'verified')

    def test_adjacent_modifier_and_unit_and_overlapping_regions_are_one_call(self):
        primary = data(('<', '1.25', 'mg', '2'), (15, 40, 72, 95))
        result, call = self.verify(primary, '< 1.25 mg 2')
        self.assertEqual(call.call_count, 1)
        self.assertEqual(result[0]['word_indices'], [0, 1, 2, 3])
        self.assertEqual(result[0]['status'], 'verified')

    def test_expired_budget_and_local_failure_preserve_main_result(self):
        result, call = self.verify(data(), '', deadline=10)
        call.assert_not_called()
        self.assertEqual(result[0]['reason_code'], 'numeric_verification_budget_exhausted')
        with patch.object(ocr.time, 'monotonic', return_value=10), patch.object(
                ocr.pytesseract, 'image_to_string', side_effect=RuntimeError('SENSITIVE')):
            result = ocr.verify_numeric_regions(np.zeros((80,180),dtype=np.uint8), data(), 'eng', 100)
        self.assertEqual(result[0]['status'], 'numeric_uncertain')
        self.assertNotIn('SENSITIVE', str(result))

    def test_no_numbers_does_not_call_ocr(self):
        result, call = self.verify(data(('Alpha',)), 'Alpha')
        self.assertEqual(result, [])
        call.assert_not_called()

    def test_large_glyph_sampling_is_bounded_and_preserves_source_coordinates(self):
        primary = data(('90.0',), (40,))
        primary['height'] = [64]
        primary['width'] = [90]
        gray = np.full((200, 240), 255, dtype=np.uint8)
        with patch.object(ocr.time, 'monotonic', return_value=10), patch.object(
                ocr.pytesseract, 'image_to_string', return_value='50.0') as call:
            result = ocr.verify_numeric_regions(gray, primary, 'eng', 100)
        check = result[0]
        self.assertEqual(call.call_count, 1)
        self.assertEqual(check['secondary_image_scale'], .5)
        self.assertEqual(check['status'], 'numeric_uncertain')
        self.assertEqual(check['primary'], '90.0')
        self.assertEqual(check['secondary'], '50.0')
        self.assertEqual(primary['text'], ['90.0'])
        box = check['bbox_pixel']
        self.assertEqual(call.call_args.args[0].shape,
                         (round((box[3]-box[1])*.5),round((box[2]-box[0])*.5)))
        self.assertEqual(call.call_args.kwargs['timeout'], 90)

    def test_padding_never_cuts_recognized_neighbor_words(self):
        primary = data(('Alpha', 'Beta', '1.25', 'Gamma', 'Delta'), (0, 40, 75, 110, 145))
        primary['width'][0] = 35
        result, call = self.verify(primary, 'Alpha Beta 1.25 Gamma Delta')
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['word_indices'], [0, 1, 2, 3, 4])
        self.assertEqual(result[0]['status'], 'verified')
        self.assertEqual(result[0]['bbox_pixel'][0], 0)
        self.assertGreaterEqual(result[0]['bbox_pixel'][2], 175)


if __name__ == '__main__':
    unittest.main()
