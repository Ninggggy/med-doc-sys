"""正文组装必须保留表格外文字；预期独立于覆盖检测算法。"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import fitz
from agent.agent_backend.utils.parser import pdf_page_extractor as parser


def word(text, x0, x1, y=10):
    return {'text': text, 'bbox': [x0, y, x1, y + 10], 'source': 'native'}


class TextAssemblyTests(unittest.TestCase):
    def test_cell_incomplete_or_duplicate_order_keeps_geometry_fallback(self):
        a, b, c = [word(text, x, x+10, y) for text, x, y in
                   [('A', 10, 20), ('B', 30, 10), ('C', 50, 20)]]
        cells = [[0, 0, 70, 40]]
        expected = parser.table_entry(cells, [a,b,c], 1, 1, 'ocr')
        for lines in ([[a,c]], [[a,b,b,c]], [[a,b,c],[b]]):
            with self.subTest(lines=lines):
                actual = parser.table_entry(cells, [a,b,c], 1, 1, 'ocr', ordered_lines=lines)
                self.assertEqual(actual, expected)

    def test_cell_uses_valid_ocr_order_without_importing_other_column(self):
        a,b,c,other = [word(text,x,x+10,y) for text,x,y in
                       [('批准',10,20),('内',30,10),('容',50,20),('OTHER',90,20)]]
        for item in (a,b,c,other):
            item['source'] = 'ocr'
        b['bbox'][3] = 32
        table = parser.table_entry([[0,0,70,40],[70,0,110,40]], [a,b,c,other],1,1,'ocr',
                                   ordered_lines=[[a,b,c,other]])
        self.assertEqual(table['rows'], [['批准 内 容','OTHER']])
        self.assertEqual(table['unassigned_words'], [])

    def test_table_replacement_uses_explicit_order_even_for_tall_ocr_line(self):
        words=[word(text,x,x+10,y) for text,x,y in
               [('Alpha',10,20),('Beta',30,36),('Gamma',50,20)]]
        for item in words:
            item['source']='ocr'
        line={'text':'Alpha Beta Gamma','bbox':[10,20,60,46],'source':'ocr'}
        table=parser.table_entry([[29,35,41,47]],[words[1]],1,1,'ocr')
        text,errors=parser.assemble_page_text([line],words,[table],line_word_order={id(line):words})
        self.assertEqual(errors,[])
        for token in ('Alpha','Beta','Gamma'):
            self.assertEqual(text.count(token),1)

    def test_ordered_ocr_line_table_replacement_preserves_outside_words(self):
        a,b,c=[word(text,x,x+10,y) for text,x,y in
               [('Alpha',10,20),('Beta',30,26),('Gamma',50,20)]]
        for item in (a,b,c):
            item['source']='ocr'
        line=parser.retained_ocr_line({'text':'Alpha Beta Gamma','bbox':[10,20,60,36],
                                      'source':'ocr','_ordered_words':[a,b,c]},[a,b,c])
        table=parser.table_entry([[29,25,41,37]],[b],1,1,'ocr')
        text,errors=parser.assemble_page_text([line],[a,b,c],[table])
        self.assertEqual(errors,[])
        self.assertEqual(text.count('Alpha'),1)
        self.assertEqual(text.count('Beta'),1)
        self.assertEqual(text.count('Gamma'),1)
        self.assertLess(text.index('Alpha'),text.index('Gamma'))

    def test_ocr_order_filter_does_not_restore_duplicate_or_neighbor_words(self):
        a,b,c=[word(text,x,x+10) for text,x in [('A',10),('B',30),('C',50)]]
        neighbor=word('OTHER',35,40)
        line={'text':'A B C','bbox':[0,0,100,50],'source':'ocr','_ordered_words':[a,b,c]}
        result=parser.retained_ocr_line(line,[a,c,neighbor])
        self.assertEqual(result['text'],'A C')
        self.assertNotIn('_ordered_words',result)
        self.assertIsNone(parser.retained_ocr_line(line,[neighbor]))
        self.assertEqual(line['text'],'A B C')

    def test_full_page_keeps_explicit_ocr_line_order(self):
        data={'text':['Alpha','Beta','Gamma'],'left':[10,40,70],'top':[20,26,20],
              'width':[20]*3,'height':[10]*3,'conf':[95]*3,
              'block_num':[1]*3,'par_num':[1]*3,'line_num':[1]*3,'word_num':[1,2,3]}
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'order.pdf'
            with fitz.open() as doc:
                page=doc.new_page(width=120,height=100)
                page.insert_text((10,30),'Alpha Beta Gamma',fontsize=8)
                doc.save(path)
            client=Mock();client.timeout_seconds=120;client.image_to_data.return_value=data
            row=parser.extract_pdf_pages(str(path),ocr_client=client,dpi=72,force_ocr=True)[0]
        self.assertEqual(row['text'].strip(),'Alpha Beta Gamma')
        self.assertEqual(len(row['words']),3)

    def test_explicit_ocr_line_order_survives_vertical_glyph_offsets(self):
        data={'text':['A','B','C'],'left':[10,30,50],'top':[20,26,20],
              'width':[10]*3,'height':[10]*3,'conf':[95]*3,
              'block_num':[1]*3,'par_num':[1]*3,'line_num':[1]*3,'word_num':[1,2,3]}
        with fitz.open() as document:
            page=document.new_page(width=100,height=100)
            client=Mock();client.image_to_data.return_value=data
            _, lines, _=parser.ocr_region(page,page.rect,client,'eng',72,6,original=True)
        self.assertEqual([line['text'] for line in lines],['A B C'])

    def test_missing_ocr_order_keeps_geometry_fallback(self):
        data={'text':['A','B','C'],'left':[10,30,50],'top':[20,40,20],
              'width':[10]*3,'height':[10]*3,'conf':[95]*3}
        with fitz.open() as document:
            page=document.new_page(width=100,height=100)
            client=Mock();client.image_to_data.return_value=data
            _, lines, _=parser.ocr_region(page,page.rect,client,'eng',72,6,original=True)
        self.assertEqual([line['text'] for line in lines],['A C\nB'])

    def test_invalid_or_duplicate_ocr_word_ids_do_not_claim_valid_order(self):
        for order in ([1,1,2],[1,1.5,3],[True,2,3],'123'):
            with self.subTest(order=order), fitz.open() as document:
                page=document.new_page(width=100,height=100)
                client=Mock();client.image_to_data.return_value={
                    'text':['A','B','C'],'left':[10,30,50],'top':[20,40,20],
                    'width':[10]*3,'height':[10]*3,'conf':[95]*3,
                    'block_num':[1]*3,'par_num':[1]*3,'line_num':[1]*3,'word_num':order}
                _, lines, _=parser.ocr_region(page,page.rect,client,'eng',72,6,original=True)
            self.assertEqual([line['text'] for line in lines],['A C\nB'])

    def assemble(self, text, words, selected, box=(30, 0, 100, 30)):
        table = parser.table_entry([list(box)], selected, 1, 1, 'native')
        line = {'text': text, 'bbox': [0, 10, 110, 20], 'source': 'native'}
        return parser.assemble_page_text([line], words, [table])

    def test_cross_boundary_line_preserves_outside_word_once(self):
        words = [word('OUTSIDE', 0, 25), word('INSIDE', 40, 80)]
        text, errors = self.assemble('OUTSIDE INSIDE', words, words[1:])
        self.assertEqual(text.count('OUTSIDE'), 1)
        self.assertEqual(text.count('INSIDE'), 1)
        self.assertEqual(errors, [])

    def test_same_text_at_different_positions_is_not_deduplicated(self):
        words = [word('same', 0, 25), word('same', 40, 80)]
        text, errors = self.assemble('same same', words, words[1:])
        self.assertEqual(text.count('same'), 2)
        self.assertEqual(errors, [])

    def test_native_spacing_outside_table_is_preserved(self):
        words = [word('Alpha', 0, 10), word('beta', 12, 25), word('Cell', 40, 80)]
        text, errors = self.assemble('Alpha   beta Cell', words, words[2:])
        self.assertIn('Alpha   beta', text)
        self.assertEqual(text.count('Cell'), 1)
        self.assertEqual(errors, [])

    def test_unmatched_line_is_retained_with_diagnostic(self):
        words = [word('Cell', 40, 80)]
        text, errors = self.assemble('unmapped Cell content', words, words)
        self.assertIn('unmapped Cell content', text)
        self.assertTrue(errors)
        self.assertEqual(errors[0]['code'], 'text_assembly_unresolved')

    def test_word_missing_from_lines_still_reaches_body(self):
        text, errors = parser.assemble_page_text([], [word('orphan', 0, 25)], [])
        self.assertIn('orphan', text)
        self.assertTrue(errors)

    def test_real_pdf_line_across_table_boundary_keeps_all_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'boundary.pdf'
            with fitz.open() as doc:
                page = doc.new_page(width=400, height=200)
                for x in (100, 220, 350):
                    page.draw_line((x, 70), (x, 140))
                for y in (70, 105, 140):
                    page.draw_line((100, y), (350, y))
                page.insert_text((20, 95), 'OUTSIDE alpha beta gamma delta epsilon', fontsize=12)
                doc.save(path)
            client = Mock()
            client.image_to_data.side_effect = AssertionError('native page must not call OCR')
            result = parser.extract_pdf_pages(str(path), ocr_client=client)[0]
            self.assertTrue(result['tables'])
            for token in ('OUTSIDE', 'alpha', 'beta', 'gamma', 'delta', 'epsilon'):
                self.assertEqual(result['text'].count(token), 1, (token, result['text']))
            client.image_to_data.assert_not_called()


if __name__ == '__main__':
    unittest.main()
