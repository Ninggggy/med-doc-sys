import tempfile
import unittest
from pathlib import Path
from copy import deepcopy
import fitz
from test_pdf_page_extraction import gradient_page
from agent.agent_backend.utils.parser.pdf_page_extractor import extract_pdf_pages, continuation_candidates


class ContinuationIntegrityTests(unittest.TestCase):
    def pages(self, titles):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'synthetic.pdf'
            with fitz.open() as doc:
                for title in titles:
                    gradient_page(doc, title)
                doc.save(path)
            return extract_pdf_pages(str(path))

    def test_unmatched_continuation_is_partial_with_original_cells(self):
        pages = self.pages(['First experiment', 'Different experiment (continued)'])
        page = pages[1]
        self.assertEqual(page['status'], 'partial')
        self.assertTrue(any(e['code'] == 'table_structure_unresolved' for e in page['errors']))
        table = page['tables'][0]
        self.assertNotEqual(table['continuation'], 'linked')
        self.assertIn('-1.25', str(table['rows']))
        self.assertIn('continuation_unresolved', table['review_reasons'])

    def test_unique_header_title_geometry_and_ambiguous_candidates(self):
        pages = self.pages(['Experiment', 'Experiment (continued)'])
        previous, current = pages[0]['tables'][0], pages[1]['tables'][0]
        self.assertEqual(current['continuation_of'], previous['id'])
        duplicate = deepcopy(previous)
        duplicate['id'] = 'another-table'
        self.assertEqual(len(continuation_candidates(current, [previous, duplicate])), 2)
        altered = deepcopy(previous)
        altered['rows'][0][0] = 'Other measurement'
        self.assertEqual(continuation_candidates(current, [altered]), [])

    def test_non_adjacent_table_is_not_linked(self):
        pages = self.pages(['Experiment', 'Other', 'Experiment (continued)'])
        self.assertNotEqual(pages[2]['tables'][0]['continuation'], 'linked')
        self.assertEqual(pages[2]['status'], 'partial')


if __name__ == '__main__':
    unittest.main()
