"""非默认后端阻止旧OCR追加；默认后端仍执行既有路径。"""
import unittest
from unittest.mock import patch
from agent.agent_backend.services.filing_change_form_parser_service import FilingChangeFormParserService


class ExperimentalFormGuardTests(unittest.TestCase):
    def test_experimental_result_never_reenters_old_ocr(self):
        service = FilingChangeFormParserService()
        parsed = dict(raw_text='6. 药品通用名称：测试药品', items=[], pages=[],
                      ocr_backend='ppocr_v6_medium_experimental')
        with patch.object(service, '_refine_pdf_items', side_effect=AssertionError('旧OCR被调用')), \
             patch.object(service, '_apply_pdf_region_ocr_enhancements', side_effect=AssertionError('旧OCR被追加')):
            result = service.form_from_pdf_result(parsed, file_path='/not-read/scan.pdf')
        self.assertEqual(result['raw_text'], parsed['raw_text'])
        self.assertIs(result['pdf_parse_result'], parsed)

    def test_default_consumer_keeps_existing_hooks(self):
        service = FilingChangeFormParserService()
        with patch.object(service, '_refine_pdf_items', return_value=[]) as refine, \
             patch.object(service, '_apply_pdf_region_ocr_enhancements') as enhance:
            service.form_from_pdf_result(dict(raw_text='', items=[], pages=[]), file_path='/not-read/default.pdf')
        refine.assert_called_once()
        enhance.assert_called_once()


if __name__ == '__main__':
    unittest.main()
