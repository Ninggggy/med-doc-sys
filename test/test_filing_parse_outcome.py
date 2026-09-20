import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import fitz

from agent.agent_backend.services.filing_parse_outcome import ParseFailure, check_file, outcome, failure
from agent.agent_backend.services.filing_change_material_service import FilingChangeMaterialService
from agent.agent_backend.services.filing_change_form_parser_service import FilingChangeFormParserService
from agent.agent_backend.services.filing_change_rule_review_service import FilingChangeRuleReviewService


class FilingParseOutcomeTest(unittest.TestCase):
    def test_text_assembly_diagnostic_remains_reviewable_not_parser_failure(self):
        for code in ('text_assembly_unresolved', 'parser_failed'):
            with self.subTest(code=code):
                result=outcome([{'page':2,'text':'Preserved text','errors':[
                    {'stage':'text_assembly','code':code,'reason':'historical diagnostic',
                     'bbox_pdf':[1,2,30,40]}]}])
                error=result['parse_diagnostics']['errors'][0]
                self.assertEqual(error['code'],'text_assembly_unresolved')
                self.assertEqual(result['content_status'],'partial')
                self.assertEqual(error['bbox_pdf'],[1,2,30,40])
                self.assertNotIn('执行失败',error['message'])

    def test_quality_coverage_and_table_are_not_service_failures(self):
        for stage in ('ocr_quality', 'ocr_coverage', 'table_structure_unresolved'):
            result = outcome([{'page': 1, 'text': 'Usable text', 'errors': [
                {'stage': stage, 'code': 'ocr_response' if stage.startswith('ocr') else stage,
                 'reason': 'historical diagnostic', 'bbox_pdf': [1, 2, 3, 4]}]}])
            self.assertEqual(result['content_status'], 'partial')
            error = result['parse_diagnostics']['errors'][0]
            self.assertEqual(error['code'], stage)
            self.assertNotIn('管理员', error['message'])
            self.assertEqual(error['bbox_pdf'], [1, 2, 3, 4])
            self.assertEqual(result['parse_diagnostics']['available_pages'], [1])

    def test_structured_http_and_unknown_exception_are_safe(self):
        from requests import HTTPError, Response
        from agent.agent_backend.services.filing_parse_outcome import ocr_exception_details
        response = Response()
        response.status_code = 503
        details = ocr_exception_details(HTTPError('secret response', response=response))
        self.assertEqual(details, {'code': 'ocr_busy', 'exception_type': 'HTTPError', 'http_status': 503})
        self.assertEqual(ocr_exception_details(RuntimeError('secret'))['code'], 'diagnostic_unknown')
        self.assertNotIn('secret', str(details))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'sample.pdf'

    def test_real_files_empty_damaged_encrypted_and_mismatched(self):
        for content, code in [(b'', 'empty_file'), (b'%PDF-1.7\nbroken', 'pdf_damaged'), (b'Word text', 'format_mismatch')]:
            with self.subTest(code=code):
                self.path.write_bytes(content)
                with self.assertRaises(ParseFailure) as caught:
                    check_file(self.path)
                self.assertEqual(code, caught.exception.code)
        doc = fitz.open()
        doc.new_page().insert_text((50, 50), 'Protected application content')
        self.path.write_bytes(doc.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw='owner', user_pw='reader'))
        doc.close()
        with self.assertRaises(ParseFailure) as caught:
            check_file(self.path)
        self.assertEqual('pdf_password', caught.exception.code)

    def test_missing_and_unreadable_file_are_access_failures(self):
        with self.assertRaises(FileNotFoundError) as caught:
            check_file(self.path)
        self.assertEqual('file_access', failure(caught.exception)['code'])
        with patch.object(Path, 'open', side_effect=PermissionError('/internal/private/path')):
            with self.assertRaises(PermissionError) as caught:
                check_file(self.path)
        self.assertNotIn('/internal', failure(caught.exception)['message'])

    def test_empty_title_and_error_placeholder_are_not_success(self):
        for rows in ([], [{'text': '# 申请表'}], [{'text': '[解析失败]'}], [{'text': 'Heading', 'title': 'Heading'}], [{'tables': [{'columns': ['A'], 'data': []}]}]):
            with self.subTest(rows=rows), self.assertRaises(ParseFailure):
                outcome(rows)

    def test_ocr_failures_and_partial_pages_keep_explicit_reasons(self):
        for reason, code in [('ConnectionError: secret', 'ocr_unreachable'), ('ReadTimeout: secret', 'ocr_timeout'), ('HTTPError: secret', 'ocr_http'), ('OCR 未返回有效文字', 'ocr_empty')]:
            with self.subTest(code=code):
                failed = {'page': 2, 'text': '', 'content_available': False, 'errors': [{'stage': 'ocr', 'reason': reason}]}
                with self.assertRaises(ParseFailure) as caught:
                    outcome([failed])
                self.assertEqual(code, caught.exception.code)
                partial = outcome([{'page': 1, 'text': 'Actual application body'}, failed])
                self.assertEqual('partial', partial['content_status'])
                self.assertEqual(2, partial['parse_diagnostics']['errors'][0]['page'])
                self.assertNotIn('secret', str(partial))

    def test_real_native_pdf_material_and_application_parse(self):
        doc = fitz.open()
        doc.new_page().insert_text((50, 50), 'Application for drug registration\nMinoxidil solution 5 percent\nShelf life proposed 24 months.\nApplicant Example Pharmaceutical Company.')
        doc.save(self.path)
        doc.close()
        rows = FilingChangeMaterialService().parse_file(str(self.path))
        self.assertEqual('success', outcome(rows)['content_status'])
        self.assertIn('Minoxidil', rows[0]['text'])
        form = FilingChangeFormParserService().parse_form_file(str(self.path))
        # 正文确实可用，但这些自由文本未映射到申请表字段，不能宣称字段解析完整成功。
        self.assertEqual('partial', form['content_status'])
        self.assertEqual('unrecognized_content', form['parse_diagnostics']['form_content_status'])
        self.assertIn('Minoxidil', form['raw_text'])

    def test_real_scanned_pdf_ocr_transport_and_response_failures(self):
        from requests import ConnectionError, ReadTimeout
        from unittest.mock import Mock
        source = fitz.open()
        source.new_page().insert_text((50, 50), 'Application content requiring OCR recognition')
        image = source[0].get_pixmap()
        doc = fitz.open()
        page = doc.new_page()
        page.insert_image(page.rect, stream=image.tobytes('png'))
        doc.save(self.path)
        doc.close()
        source.close()
        bad_response = Mock()
        bad_response.json.return_value = {'unexpected': 'value'}
        empty_response = Mock()
        empty_response.json.return_value = {'data': {'text': []}}
        for response, code in [(ConnectionError('internal endpoint'), 'ocr_unreachable'), (ReadTimeout('internal endpoint'), 'ocr_timeout'), (bad_response, 'ocr_invalid_response'), (empty_response, 'ocr_empty')]:
            with self.subTest(code=code):
                options = {'side_effect': response} if isinstance(response, Exception) else {'return_value': response}
                with patch('agent.agent_backend.utils.parser.drug_supplement_pdf_parser.requests.post', **options):
                    rows = FilingChangeMaterialService().parse_file(str(self.path))
                with self.assertRaises(ParseFailure) as caught:
                    outcome(rows)
                self.assertEqual(code, caught.exception.code)
                self.assertEqual([1], caught.exception.diagnostics['failed_pages'])
                self.assertNotIn('internal endpoint', str(caught.exception.diagnostics))

    def test_review_uploaded_failure_is_not_missing_material(self):
        result = FilingChangeRuleReviewService().run({}, [
            {'doc_id': 'failed-file', 'parse_status': 'pending', 'latest_attempt': {'content_status': 'failed', 'message': 'OCR 超时'}},
            {'doc_id': 'partial-file', 'parse_status': 'partial', 'parse_diagnostics': {'available_pages': [1], 'failed_pages': [2]}},
        ], [], {'missing_items': []})
        self.assertEqual([], result['formal_review']['missing_materials'])
        self.assertEqual(['failed', 'partial'], [x['status'] for x in result['formal_review']['parse_issues']])
        self.assertEqual('需人工确认', result['formal_review']['result'])

    def test_optional_form_region_ocr_failure_remains_a_partial_diagnostic(self):
        from requests import ReadTimeout
        from agent.agent_backend.utils.parser.drug_supplement_pdf_parser import OCRServiceClient, ocr_pdf_region, ocr_region_errors
        doc = fitz.open()
        doc.new_page().insert_text((50, 50), 'Existing usable body')
        doc.save(self.path)
        doc.close()
        errors = []
        token = ocr_region_errors.set(errors)
        try:
            with patch.object(OCRServiceClient, 'image_to_text', side_effect=ReadTimeout('internal address')):
                self.assertEqual('', ocr_pdf_region(self.path, {'page': 1, 'bbox_pdf': [40, 30, 250, 70]}))
        finally:
            ocr_region_errors.reset(token)
        result = outcome([{'page': 1, 'text': 'Existing usable body', 'errors': errors}])
        self.assertEqual('partial', result['content_status'])
        self.assertEqual('ocr_timeout', result['parse_diagnostics']['errors'][0]['code'])
        self.assertNotIn('internal address', str(result))
