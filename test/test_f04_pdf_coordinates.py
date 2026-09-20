"""F04：真实 PDF 的行列及来源坐标；正常 OCR 必须连接真实本机服务。"""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import fitz

from agent.test.test_pdf_page_extraction import gradient_page
from agent.agent_backend.utils.parser.pdf_page_extractor import extract_pdf_pages
from agent.agent_backend.utils.parser.drug_supplement_pdf_parser import OCRServiceClient


ROWS = [
    ['Time (min)', 'Mobile phase (%)', ''],
    ['', 'A', 'B'],
    ['0.5', '50.0', '50.0'],
    ['-1.25', '<=2.0', '98.0'],
]
NOTE = '* Range: 0.5-2.0 mg/mL; 95%; note 1.'
# 原件绘制的边界与合并关系，独立于解析器坐标变换。
CELLS = [
    (0, 0, 2, 1, [40, 80, 180, 140]),
    (0, 1, 1, 2, [180, 80, 460, 110]),
    (1, 1, 1, 1, [180, 110, 320, 140]),
    (1, 2, 1, 1, [320, 110, 460, 140]),
    (2, 0, 1, 1, [40, 140, 180, 180]),
    (2, 1, 1, 1, [180, 140, 320, 180]),
    (2, 2, 1, 1, [320, 140, 460, 180]),
    (3, 0, 1, 1, [40, 180, 180, 220]),
    (3, 1, 1, 1, [180, 180, 320, 220]),
    (3, 2, 1, 1, [320, 180, 460, 220]),
]


class F04CoordinatesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='f04-')
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'gradient.pdf'

    def make(self, angle=0, crop=(20, 30, 480, 490), media=None, mode='native'):
        with fitz.open() as doc, fitz.open() as src:
            original = gradient_page(src)
            if mode == 'native':
                page = gradient_page(doc, '3.2.P.5.1 Specification')
            else:
                if mode == 'mixed':
                    gradient_page(doc, '3.2.P.5.1 Specification')
                page = doc.new_page(width=500, height=500)
                region = original.rect
                if mode == 'local':
                    page.insert_text((40, 50), '3.2.P.5.1 Specification', fontsize=14)
                    region = fitz.Rect(35, 75, 465, 260)
                page.insert_image(region, stream=original.get_pixmap(
                    matrix=fitz.Matrix(3, 3), clip=region).tobytes('png'))
            if media:
                # 改 MediaBox 不平移原件内容，用于独立核验非零原点。
                doc.xref_set_key(page.xref, 'MediaBox', str(list(media)).replace(',', ''))
                page = doc.reload_page(page)
            if crop:
                page.set_cropbox(fitz.Rect(crop))
            page.set_rotation(angle)
            doc.save(self.path)

    def assert_box(self, actual, expected, delta=.02):
        self.assertEqual(len(actual), 4)
        for actual_value, expected_value in zip(actual, expected):
            self.assertAlmostEqual(actual_value, expected_value, delta=delta)

    def assert_table(self, page, offset=(20, 30), delta=.02, missing_title=False):
        # 本轮明确验收识别不完整时正确partial，不把漏字当作成功。
        self.assertEqual(page['status'], 'partial' if missing_title else 'success', page.get('errors'))
        expected_rows = [row[:] for row in ROWS]
        if missing_title:
            expected_rows[1][2] = ''
            failures = [e for e in page['errors'] if e['stage'] == 'ocr_coverage']
            glyph = fitz.Rect(341-offset[0],123-offset[1],348-offset[0],132-offset[1])
            self.assertTrue(any(fitz.Rect(box).intersects(glyph) for e in failures for box in e['uncovered_components']))
            self.assertTrue(all(e['code']=='ocr_coverage' for e in page['errors']))
        self.assertEqual(len(page['tables']), 1)
        table = page['tables'][0]
        self.assertEqual(table['rows'], expected_rows)
        self.assertEqual(table['header_rows'], [0, 1])
        self.assertEqual(table['needs_review'], missing_title)
        self.assertEqual(table['coordinate_unit'], 'pdf_point')
        self.assertEqual(table['page'], page['page'])
        self.assert_box(table['bbox_pdf'], [40-offset[0], 80-offset[1],
                                            460-offset[0], 220-offset[1]], delta)
        self.assertEqual(len(table['cells']), len(CELLS))
        for r, c, rowspan, colspan, box in CELLS:
            cell = next(v for v in table['cells'] if (v['row'], v['column']) == (r, c))
            self.assertEqual((cell['rowspan'], cell['colspan']), (rowspan, colspan))
            self.assertEqual(cell['text'], expected_rows[r][c])
            self.assert_box(cell['bbox_pdf'], [v-offset[i % 2] for i, v in enumerate(box)], delta)
        self.assertEqual([v['text'] for v in table['notes']], [NOTE])
        self.assertGreater(table['notes'][0]['bbox'][1], table['bbox_pdf'][3])
        self.assertEqual(page['text'].count(table['markdown']), 1)
        self.assertEqual(page['text'].count(NOTE), 1)
        # 数值词必须落在对应数据格，脚注词不能混入任何数据格。
        for value, row, col in [('0.5', 2, 0), ('-1.25', 3, 0), ('<=2.0', 3, 1), ('98.0', 3, 2)]:
            word = next(w for w in page['words'] if w['text'] == value)
            cell = next(v for v in table['cells'] if (v['row'], v['column']) == (row, col))
            self.assertTrue(fitz.Rect(word['bbox']) in fitz.Rect(cell['bbox_pdf']))

    def check_native(self, angle):
        self.make(angle)
        before = self.path.read_bytes()
        with patch.object(OCRServiceClient, 'image_to_data', side_effect=AssertionError('原生页不应调用 OCR')) as ocr:
            page = extract_pdf_pages(str(self.path))[0]
        ocr.assert_not_called()
        self.assert_table(page)
        self.assertEqual(page['source_rotation'], angle)
        self.assert_box(page['page_bbox'], [0, 0, 460, 460])
        self.assertEqual(page['ocr_calls'], 0)
        self.assertEqual(self.path.read_bytes(), before)

    def test_crop_rotation_0(self):
        self.check_native(0)

    def test_crop_rotation_90(self):
        self.check_native(90)

    def test_crop_rotation_180(self):
        self.check_native(180)

    def test_crop_rotation_270(self):
        self.check_native(270)

    def test_nonzero_media_origins_all_rotations(self):
        for media, offset in [((10, 15, 510, 615), (20, -85)),
                              ((-10, -15, 490, 585), (20, -55))]:
            for angle in (0, 90, 180, 270):
                with self.subTest(media=media, angle=angle):
                    self.make(angle, media=media)
                    self.assert_table(extract_pdf_pages(str(self.path))[0], offset=offset)

    def test_rectangular_crop_and_uncropped_all_rotations(self):
        for crop, offset, bounds in [((20, 15, 480, 450), (20, 15), [0, 0, 460, 435]),
                                     (None, (0, 0), [0, 0, 500, 500])]:
            for angle in (0, 90, 180, 270):
                with self.subTest(crop=crop, angle=angle):
                    self.make(angle, crop=crop)
                    page = extract_pdf_pages(str(self.path))[0]
                    self.assert_table(page, offset=offset)
                    self.assert_box(page['page_bbox'], bounds)

    def test_invisible_table_is_not_exposed(self):
        for angle in (0, 90, 180, 270):
            with self.subTest(angle=angle):
                self.make(angle, crop=(20, 230, 480, 490))
                page = extract_pdf_pages(str(self.path))[0]
                self.assertEqual(page['status'], 'success')
                self.assertEqual(page['tables'], [])
                self.assertEqual(page['text'], NOTE)

    def test_partly_cropped_cells_stay_in_visible_page(self):
        for angle in (0, 90, 180, 270):
            with self.subTest(angle=angle):
                self.make(angle, crop=(45, 75, 480, 490))
                page = extract_pdf_pages(str(self.path))[0]
                table = page['tables'][0]
                self.assertEqual(table['rows'], ROWS)
                self.assert_box(table['bbox_pdf'], [0, 5, 415, 145])
                for cell in table['cells']:
                    self.assertTrue(fitz.Rect(cell['bbox_pdf']) in fitz.Rect(page['page_bbox']))

    def test_filing_material_entry_all_rotations(self):
        from agent.agent_backend.services.filing_change_material_service import FilingChangeMaterialService
        for angle in (0, 90, 180, 270):
            with self.subTest(angle=angle):
                self.make(angle)
                pages = FilingChangeMaterialService().parse_file(str(self.path))
                self.assert_table(pages[0])

    def test_filing_upload_parse_and_readback_in_temporary_database(self):
        from agent.test.test_filing_change_review_service_regression import (
            FilingChangeReviewServiceRegressionTest, _Upload)
        from agent.agent_backend.services.filing_change_material_service import FilingChangeMaterialService
        fixture = FilingChangeReviewServiceRegressionTest()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        fixture._add_project('f04')
        fixture.service.material_service = FilingChangeMaterialService()
        for angle in (0, 90, 180, 270):
            with self.subTest(angle=angle):
                self.make(angle)
                ok, message, uploaded = fixture.service.upload_submission_files(
                    'f04', [_Upload(f'gradient-{angle}.pdf', self.path.read_bytes())], '5')
                self.assertTrue(ok, message)
                doc_id = uploaded['created'][0]['doc_id']
                ok, message, parsed = fixture.service.parse_submission('f04', doc_id)
                self.assertTrue(ok, message)
                self.assertEqual(parsed['content_status'], 'success')
                ok, message, saved = fixture.service.get_submission_parsed_markdown('f04', doc_id)
                self.assertTrue(ok, message)
                self.assert_table(saved['parsed_chunks'][0])
                self.assertIn('| -1.25 | <=2.0 | 98.0 |', saved['markdown'])

    def test_ctd_entries_all_rotations(self):
        from agent.agent_backend.utils.parser.ctd_paser import (
            parse_pdf_to_markdown_json, parse_bound_section_pdf_to_payload)
        for angle in (0, 90, 180, 270):
            with self.subTest(angle=angle):
                self.make(angle)
                payload = parse_pdf_to_markdown_json(str(self.path), [
                    {'section_id': '3.2.p.5.1', 'section_name': 'Specification'}])
                self.assert_table(payload['source_pages'][0])
                self.assertIn('| 0.5 | 50.0 | 50.0 |', str(payload['sections']))
                self.assertIn(NOTE, str(payload['sections']))
                bound = parse_bound_section_pdf_to_payload(str(self.path), section_id='3.2.p.5.1')
                section = bound['sections'][0]
                self.assertEqual(section['tables'][0]['rows'], ROWS)
                self.assertEqual(section['raw_pages'], [1])
                self.assertIn('| -1.25 | <=2.0 | 98.0 |', section['content'])

    def test_knowledge_parse_and_chunk_all_rotations(self):
        from agent.agent_backend.memory.rag.document import DocumentProcessor
        for angle in (0, 90, 180, 270):
            with self.subTest(angle=angle):
                self.make(angle)
                processor = DocumentProcessor()
                document = processor.parse_to_document(str(self.path))
                self.assert_table(document.metadata['source_pages'][0])
                chunks = processor.chunk_document(document)
                self.assertEqual(len(chunks), 1)
                chunk = chunks[0]
                self.assertEqual((chunk.page_start, chunk.page_end), (1, 1))
                self.assertEqual(chunk.metadata['tables'][0]['rows'], ROWS)
                self.assert_box(chunk.metadata['tables'][0]['bbox_pdf'], [20, 50, 440, 190])
                self.assertEqual(chunk.metadata['source_rotation'], angle)
                self.assertIn(NOTE, chunk.text)

    @unittest.skipUnless(os.getenv('T2_OCR_URL'), '设置 T2_OCR_URL 运行真实 OCR')
    def test_real_ocr_uncropped_scan_mixed_and_cropped_local(self):
        client = OCRServiceClient(os.environ['T2_OCR_URL'])
        for mode in ('scan', 'local', 'mixed'):
            for angle in (0, 90, 180, 270):
                with self.subTest(mode=mode, angle=angle):
                    crop = (20, 30, 480, 490) if mode == 'local' else None
                    self.make(angle, mode=mode, crop=crop)
                    pages = extract_pdf_pages(str(self.path), ocr_client=client, lang='eng')
                    missing_title = pages[-1]['tables'][0]['rows'][1][2] == ''
                    self.assert_table(pages[-1], offset=(20, 30) if crop else (0, 0), delta=1, missing_title=missing_title)
                    self.assertEqual(pages[-1]['source_rotation'], angle)
                    attempts = [a for a in pages[-1].get('ocr_recovery', []) if a['status'] != 'not_attempted']
                    self.assertLessEqual(len(attempts), 4)
                    self.assertEqual(pages[-1]['ocr_calls'], 1 + len(attempts))
                    self.assertEqual(pages[-1]['path'], ['native', 'region_ocr'] if mode == 'local' else ['page_ocr'])
                    if mode == 'mixed':
                        self.assert_table(pages[0], offset=(0, 0))
                        self.assertEqual(pages[0]['ocr_calls'], 0)


if __name__ == '__main__':
    unittest.main()
