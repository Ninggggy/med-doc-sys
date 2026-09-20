import unittest
from unittest.mock import patch, Mock
from agent.agent_backend.utils.parser.ocr_request_budget import request_budgets
from agent.agent_backend.utils.parser import ctd_paser, drug_supplement_pdf_parser


class ResponseBudgetTests(unittest.TestCase):
    def test_nested_deadlines_never_expand_original_timeout(self):
        for configured, remaining in [(120, None), (120, 116), (15, 120), (120, .01)]:
            with self.subTest(configured=configured, remaining=remaining):
                transport, execution = request_budgets(configured, remaining)
                self.assertEqual(transport, min(configured, remaining) if remaining else configured)
                self.assertGreater(execution, 0)
                self.assertLess(execution, transport)

    def test_invalid_budget_rejected(self):
        for value in [0, -1, float('nan'), float('inf')]:
            for args in [(value, None), (120, value)]:
                with self.assertRaises(ValueError):
                    request_budgets(*args)

    def test_both_business_clients_keep_completed_uncertain_result(self):
        payload = {'text': [], 'numeric_verification': [
            {'status': 'numeric_uncertain', 'reason_code': 'numeric_verification_failed'}]}
        for module in [ctd_paser, drug_supplement_pdf_parser]:
            for budget in [None, 116, .01]:
                with self.subTest(module=module.__name__, budget=budget):
                    response = Mock()
                    response.json.return_value = {'data': payload}
                    with patch.object(module.requests, 'post', return_value=response) as post:
                        result = module.OCRServiceClient('http://isolated').image_to_data(
                            b'synthetic', 'eng', execution_budget_seconds=budget)
                    self.assertEqual(result, payload)
                    options = post.call_args.kwargs
                    execution = float(options['data']['execution_budget_seconds'])
                    self.assertLess(execution, options['timeout'])
                    self.assertLessEqual(options['timeout'], budget or 120)
                    self.assertEqual(post.call_count, 1)
