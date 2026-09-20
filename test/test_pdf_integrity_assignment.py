"""独立检查跨格文字唯一归属和原图回退协议。"""
import time
import unittest
from unittest.mock import Mock
import fitz
from agent.agent_backend.utils.parser import pdf_page_extractor as parser


class IntegrityAssignmentTests(unittest.TestCase):
    def test_issue_table_link_requires_unique_local_id(self):
        from agent.agent_backend.services.filing_parse_readiness import source_issues
        error = {'code': 'table_structure_unresolved', 'table_id': 'local', 'bbox_pdf': [0, 0, 100, 100]}
        for tables, expected in (([{'id': 'other'}, {'id': 'local'}], 1),
                                 ([{'id': 'other'}], None),
                                 ([{'id': 'local'}, {'id': 'local'}], None)):
            issue = source_issues({'content_status': 'partial'}, 'submission',
                                  [{'page': 1, 'errors': [error], 'tables': tables}])[0]
            self.assertEqual(issue.get('table_index'), expected)

    def test_open_tail_preserves_closed_cells_only_with_explicit_diagnostics(self):
        with fitz.open() as doc:
            page = doc.new_page(width=300, height=300)
            for y in (40, 80, 120):
                page.draw_line((40, y), (240, y))
            for x in (40, 140, 240):
                page.draw_line((x, 40), (x, 160))
            pix = page.get_pixmap(matrix=fitz.Matrix(3, 3), alpha=False)
            self.assertEqual(parser.raster_cells(pix, (0, 0), 3), [])
            pending = []
            groups = parser.raster_cells(pix, (0, 0), 3, incomplete_regions=pending)
            self.assertEqual(len(groups), 1)
            self.assertEqual(len(groups[0]), 4)
            self.assertEqual(len(pending), 1)
            self.assertEqual(pending[0]['source'], 'visible_grid_open_tail')
            self.assertAlmostEqual(pending[0]['bbox_pdf'][1], 40, delta=1)
            self.assertAlmostEqual(pending[0]['unclosed_bbox_pdf'][1], 120, delta=1)
            self.assertAlmostEqual(pending[0]['bbox_pdf'][3], 160, delta=1)
            self.assertLess(max(c[3] for c in groups[0]), 122)

    def test_decorative_box_with_tail_does_not_become_partial_table(self):
        with fitz.open() as doc:
            page = doc.new_page(width=300, height=300)
            page.draw_rect(fitz.Rect(40, 40, 240, 120))
            page.draw_line((40, 120), (40, 160))
            page.draw_line((240, 120), (240, 160))
            pending = []
            groups = parser.raster_cells(page.get_pixmap(matrix=fitz.Matrix(3, 3), alpha=False),
                                         (0, 0), 3, incomplete_regions=pending)
            self.assertEqual(groups, [])
            self.assertEqual(pending, [])

    def test_short_multiline_header_reuses_word_line_order(self):
        words = [{'text': 'Time', 'bbox': [0, 10, 30, 28], 'source': 'ocr'},
                 {'text': 'Mobile', 'bbox': [100, 0, 150, 18], 'source': 'ocr'}]
        line = {'text': 'Mobile\nTime', 'bbox': [0, 0, 150, 28], 'source': 'ocr'}
        table = {'source_words': words, 'bbox_pdf': [0, 0, 180, 50], 'markdown': '| Time | Mobile |'}
        text, errors = parser.assemble_page_text([line], words, [table])
        self.assertEqual(errors, [])
        self.assertEqual(text, table['markdown'])
        line['text'] = 'Mobile\nMISSING Time'
        text, errors = parser.assemble_page_text([line], words, [table])
        self.assertIn('MISSING', text)
        self.assertEqual(errors[0]['code'], 'text_assembly_unresolved')

    def test_cross_cell_word_is_not_duplicated_or_guessed(self):
        cells = [[0, 0, 50, 30], [50, 0, 100, 30]]
        words = [{'text': 'uncertain', 'bbox': [40, 10, 60, 20]}]
        table = parser.table_entry(cells, words, 1, 1, 'test')
        self.assertEqual(table['rows'], [['', '']])
        self.assertEqual(table['unassigned_words'][0]['text'], 'uncertain')
        self.assertEqual(table['unassigned_words'][0]['candidate_cells'], [0, 1])
        self.assertTrue(table['needs_review'])
        self.assertIn('uncertain', table['raw_text'])
        self.assertIn('uncertain', table['markdown'])

    def test_empty_cell_is_not_itself_evidence_of_missing_text(self):
        cells = [[0, 0, 50, 30], [50, 0, 100, 30]]
        words = [{'text': 'Label', 'bbox': [5, 5, 30, 20]}]
        table = parser.table_entry(cells, words, 1, 1, 'test')
        self.assertEqual(table['rows'], [['Label', '']])
        self.assertFalse(table['needs_review'])
        self.assertEqual(table['cells'][1]['content_state'], 'empty_unverified')

    def test_merged_cell_geometry_is_preserved(self):
        cells = [[0, 0, 100, 30], [0, 30, 50, 60], [50, 30, 100, 60]]
        table = parser.table_entry(cells, [{'text': 'Title', 'bbox': [20, 5, 80, 20]}], 1, 1, 'test')
        self.assertEqual(table['cells'][0]['colspan'], 2)
        self.assertEqual(table['cells'][0]['text'], 'Title')

    def test_gray_option_requires_server_acknowledgement(self):
        doc = fitz.open()
        page = doc.new_page(width=100, height=100)
        client = Mock(supports_execution_budget=True, supports_preprocessing=True)
        client.image_to_data.return_value = {'text': [], 'execution_budget_enforced': True}
        with self.assertRaisesRegex(ValueError, '原图灰度'):
            parser.ocr_region(page, page.rect, client, 'eng', 72, 6, original=True,
                              deadline=time.monotonic() + 5)
        self.assertEqual(client.image_to_data.call_args.kwargs['preprocessing'], 'original_gray')
        client.image_to_data.return_value['preprocessing'] = 'original_gray'
        words, _, _ = parser.ocr_region(page, page.rect, client, 'eng', 72, 6, original=True,
                                        deadline=time.monotonic() + 5)
        self.assertEqual(words, [])
        doc.close()


if __name__ == '__main__':
    unittest.main()
