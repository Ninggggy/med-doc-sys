"""不依赖 OCR 服务的原因分类及诊断保留回归。"""
import unittest
from types import SimpleNamespace
from agent.agent_backend.services.filing_parse_outcome import (
    outcome, ParseFailure, OCRResponseError, ocr_exception_details,
)


class DiagnosticCodesTest(unittest.TestCase):
    def test_transport_and_schema_exceptions(self):
        for name, code in [('ReadTimeout', 'ocr_timeout'), ('ConnectionError', 'ocr_unreachable'),
                           ('HTTPError', 'ocr_busy'), ('JSONDecodeError', 'ocr_invalid_response'),
                           ('RuntimeError', 'diagnostic_unknown'), ('ValueError', 'diagnostic_unknown')]:
            with self.subTest(name=name):
                exc = type(name, (Exception,), {})('secret body')
                exc.response = SimpleNamespace(status_code=503)
                detail = ocr_exception_details(exc)
                self.assertEqual(detail['code'], code)
                self.assertEqual(detail['http_status'], 503)
                self.assertNotIn('secret', str(detail))
        self.assertEqual(ocr_exception_details(OCRResponseError('private'))['code'], 'ocr_invalid_response')
        for status, expected in ((504, 'ocr_timeout'), (500, 'ocr_http'), (403, 'ocr_http')):
            error = type('HTTPError', (Exception,), {})('secret')
            error.response = SimpleNamespace(status_code=status)
            self.assertEqual(ocr_exception_details(error)['code'], expected)

    def test_legacy_quality_and_coverage_override_incorrect_code(self):
        for stage in ('ocr_quality', 'ocr_coverage', 'table_structure', 'table_geometry'):
            with self.subTest(stage=stage):
                result = outcome([{'page': 2, 'text': 'useful content', 'errors': [
                    {'stage': stage, 'code': 'ocr_response', 'reason': 'secret', 'bbox_pdf': [1, 2, 3, 4]}]}])
                self.assertEqual(result['content_status'], 'partial')
                d = result['parse_diagnostics']
                self.assertEqual(d['available_pages'], [2])
                self.assertEqual(d['missing_pages'], [])
                self.assertEqual(d['errors'][0]['code'], stage if stage.startswith('ocr') else 'table_structure_unresolved')
                self.assertEqual(d['errors'][0]['bbox_pdf'], [1, 2, 3, 4])
                self.assertNotIn('管理员', d['errors'][0]['message'])
                self.assertNotIn('secret', str(d))

    def test_empty_mixed_and_retry_states(self):
        bad = {'page': 2, 'text': '', 'content_available': False,
               'errors': [{'stage': 'ocr', 'code': 'ocr_empty', 'reason': 'empty'}]}
        with self.assertRaises(ParseFailure) as caught:
            outcome([bad])
        self.assertEqual(caught.exception.code, 'ocr_empty')
        self.assertEqual(caught.exception.diagnostics['missing_pages'], [2])
        partial = outcome([{'page': 1, 'text': 'readable content'}, bad])
        self.assertEqual(partial['content_status'], 'partial')
        retry = outcome([{'page': 1, 'text': 'readable content'}, {'page': 2, 'text': 'recovered content'}])
        self.assertEqual(retry['content_status'], 'success')
        self.assertEqual(retry['parse_diagnostics']['errors'], [])


if __name__ == '__main__':
    unittest.main()
