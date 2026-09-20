"""实际PDF→真实HTTP OCR→单元格诊断，不以候选一致代替原件正确性。"""
import os
from pathlib import Path
import unittest

from agent.agent_backend.utils.parser.pdf_page_extractor import extract_pdf_pages
from agent.agent_backend.utils.parser.drug_supplement_pdf_parser import OCRServiceClient


class SavedNumericPDFTests(unittest.TestCase):
    @unittest.skipUnless(os.getenv('NUMERIC_SOURCE_PDF') and os.getenv('T2_OCR_URL'),
                         '需要明确提供已保存合成PDF及隔离真实OCR')
    def test_high_dpi_wrong_agreement_is_not_silent_success(self):
        source = Path(os.environ['NUMERIC_SOURCE_PDF'])
        original = source.read_bytes()
        page = extract_pdf_pages(str(source),
            ocr_client=OCRServiceClient(os.environ['T2_OCR_URL']), lang='eng', dpi=288)[0]
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(len(page['tables']), 1)
        table = page['tables'][0]
        for col in (1, 2):
            cell = next(c for c in table['cells'] if c['row'] == 2 and c['column'] == col)
            if cell['text'] != '50.0':
                self.assertEqual(cell['numeric_status'], 'numeric_uncertain', cell)
                self.assertTrue(any(c['status'] == 'numeric_uncertain'
                                    and c['secondary'] == '50.0'
                                    and c.get('bbox_pdf') and c['page'] == 1
                                    for c in cell['numeric_verification']))
                self.assertEqual(page['status'], 'partial')
                self.assertTrue(any(e['code'] == 'numeric_uncertain' for e in page['errors']))
        self.assertEqual(table['rows'][3], ['-1.25', '<=2.0', '98.0'])


if __name__ == '__main__':
    unittest.main()
