import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('stability_current', Path(__file__).resolve().parents[1] / 'agent_backend/services/filing_change_stability_service.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class UncertainCompositeTests(unittest.TestCase):
    def setUp(self):
        self.service = module.FilingChangeStabilityService()

    def test_equal_unknown_text_never_proves_compliance(self):
        for value in ('<LOQ', '待确认', '未检测', '待检', '未知', '任意自由描述'):
            with self.subTest(value=value):
                self.assertIsNone(self.service._judge_within_standard(value, value))

    def test_supported_qualitative_rules_remain(self):
        for result, limit, expected in [('符合规定', '应符合规定', True), ('不合格', '合格', False),
                                        ('未检出', '不得检出', True), ('检出', '不得检出', False),
                                        ('无色澄明液体', '无色至淡黄色澄明液体', True)]:
            with self.subTest(result=result):
                self.assertIs(self.service._judge_within_standard(result, limit), expected)

    def test_known_failure_survives_missing_or_duplicate_other_components(self):
        for result, limit in [('A:2;B:0', 'A:≤1;B:≤1;C:≤1'),
                              ('A:2;B:0;B:1', 'A:≤1;B:≤1'),
                              ('B:0;A:2', 'C:≤1;A:≤1'),
                              ('A:2mg/ml', 'A:≤1mg/ml;C:≤1')]:
            with self.subTest(result=result, limit=limit):
                decision = self.service._evaluate_limit(result, limit)
                self.assertIs(decision['within_standard'], False)
                self.assertTrue(any(c['item'] == 'A' and c['within_standard'] is False for c in decision['components']))
                self.assertTrue(any(c['within_standard'] is None for c in decision['components']))

    def test_missing_duplicate_or_malformed_components_do_not_pass(self):
        for result, limit in [('A:0', 'A:≤1;B:≤1'), ('A:0;A:2', 'A:≤1'),
                              ('A:0;B:0', 'A:≤1;B:≤1;尾部无法解释')]:
            with self.subTest(result=result):
                self.assertIsNone(self.service._judge_within_standard(result, limit))

    def test_reordering_and_units_preserve_composite_decision(self):
        for result in ('A:1mg/ml;B:0', 'B:0;A:1000ug/ml'):
            self.assertIs(self.service._judge_within_standard(result, 'B:≤1;A:≤1mg/ml'), True)

    def test_all_exception_details_are_returned(self):
        for count in (25, 100, 1000):
            with self.subTest(count=count):
                failed = [self.service._enrich_record({'indicator': f'超限{i}', 'result_text': '2', 'standard_text': '≤1'}) for i in range(count)]
                unknown = [self.service._enrich_record({'indicator': f'待确认{i}', 'result_text': '<LOQ', 'standard_text': '<LOQ'}) for i in range(count)]
                data = self.service._build_limit_check(failed + unknown, failed, unknown)
                self.assertEqual(data['out_of_spec_count'], count)
                self.assertEqual(data['undecidable_count'], count)
                self.assertEqual(len(data['out_of_spec_details']), count)
                self.assertEqual(len(data['undecidable_details']), count)
                self.assertEqual(data['out_of_spec_details'][-1]['indicator'], f'超限{count - 1}')
