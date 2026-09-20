"""独立绘制无框表，期望值来自绘制输入，不由解析器反推。"""
import tempfile
import unittest
from pathlib import Path
import fitz
from agent.agent_backend.utils.parser.pdf_page_extractor import extract_pdf_pages


class BorderlessIntegrityTests(unittest.TestCase):
    def test_ocr_table_preserves_valid_header_order_with_tall_glyph(self):
        from agent.agent_backend.utils.parser.pdf_page_extractor import borderless_tables
        def token(text, box):
            return {'text':text, 'bbox':box, 'source':'ocr'}
        header = [token('批准',[10,20,20,30]), token('内',[30,10,40,40]),
                  token('容',[50,20,60,30]), token('Limit',[150,20,180,30])]
        lines = [header] + [[token(label,[10,y,20,y+10]), token(value,[150,y,170,y+10])]
                            for label,value,y in [('A','1.0',60),('B','2.0',90),('C','3.0',120)]]
        words = [word for line in lines for word in line]
        tables, issues = borderless_tables(words, [], 1, ordered_lines=lines)
        self.assertEqual(issues, [])
        self.assertEqual(len(tables), 1)
        self.assertEqual(tables[0]['rows'], [['批准 内 容','Limit'],['A','1.0'],['B','2.0'],['C','3.0']])
        self.assertEqual(tables[0]['unassigned_words'], [])

    def test_numbered_fields_keep_all_text_in_native_pdf(self):
        labels=['1. Application type:','2. Registration class:','3. Product status:']
        values=['Chemical','Category three','Marketed']
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'fields.pdf'
            with fitz.open() as doc:
                page=doc.new_page(width=500,height=300)
                for row,(label,value) in enumerate(zip(labels,values)):
                    page.insert_text((30,50+row*30),label,fontsize=11)
                    page.insert_text((240,50+row*30),value,fontsize=11)
                doc.save(path)
            result=extract_pdf_pages(str(path))[0]
        self.assertEqual(result['tables'],[])
        self.assertEqual(result['status'],'success')
        self.assertEqual(result['ocr_calls'],0)
        for text in labels+values:
            self.assertIn(text,result['text'])

    def test_numbered_form_labels_are_not_table_headers(self):
        from copy import deepcopy
        from agent.agent_backend.utils.parser.pdf_page_extractor import borderless_tables
        for labels in (['1. Application type:', '2. Registration class:', '3. Product status:'],
                       ['1. 本申请属于：','2. 药品注册分类：','3. 原申请品种状态：']):
            with self.subTest(labels=labels):
                words=[]
                for row,(label,value) in enumerate(zip(labels,['Chemical','Category three','Marketed'])):
                    y=30+row*25
                    words.extend([{'text':label,'bbox':[30,y,170,y+12],'source':'native'},
                                  {'text':value,'bbox':[220,y,300,y+12],'source':'native'}])
                original=deepcopy(words)
                tables,issues=borderless_tables(words,[],1)
                self.assertEqual(tables,[])
                self.assertEqual(issues,[])
                self.assertEqual(words,original)

    def test_two_column_narrative_is_not_a_table_and_keeps_negation(self):
        columns = [
            ['The sample was not approved.', 'No result may be treated as final.', 'A new review is required.'],
            ['The control remains unchanged.', 'The investigator recorded all details.', 'Evidence is retained for inspection.']]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'columns.pdf'
            with fitz.open() as doc:
                page = doc.new_page(width=650,height=450)
                for ci, lines in enumerate(columns):
                    for li, text in enumerate(lines):
                        page.insert_text((25+ci*320,80+li*30),text,fontsize=10)
                doc.save(path)
            page = extract_pdf_pages(str(path))[0]
        self.assertEqual(page['ocr_calls'],0)
        self.assertEqual(page['tables'],[])
        self.assertEqual(page['status'],'success')
        for lines in columns:
            positions = []
            for text in lines:
                self.assertEqual(page['text'].count(text),1)
                positions.append(page['text'].index(text))
            self.assertEqual(positions,sorted(positions))

    def extract(self, rows):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'synthetic.pdf'
            with fitz.open() as doc:
                page = doc.new_page(width=600, height=500)
                page.insert_text((30, 30), 'Table 1 Stability', fontsize=12)
                for index, row in enumerate(rows):
                    for column, text in enumerate(row):
                        if text:
                            page.insert_text((40 + column * 170, 80 + index * 35), text, fontsize=11)
                page.insert_text((30, 350), 'Note: independent footer retained.', fontsize=11)
                doc.save(path)
            return extract_pdf_pages(str(path))[0]

    def test_aligned_columns_keep_empty_cell_and_symbols(self):
        expected = [['Item', 'Result', 'Limit'], ['A', '0.5', '<=2.0'],
                    ['B', '', '>=0.1'], ['C', '-1.25', '<=0.0'], ['D', '1e-3', '<=0.1']]
        page = self.extract(expected)
        self.assertEqual(page['ocr_calls'], 0)
        self.assertEqual(len(page['tables']), 1)
        self.assertEqual(page['tables'][0]['rows'], expected)
        self.assertIn('independent footer retained', page['text'])
        self.assertEqual(page['tables'][0]['rows'][2][1], '')

    def test_repeated_values_remain_in_distinct_cells(self):
        expected = [['Batch', 'Month0', 'Month6'], ['A', '99.0', '99.0'],
                    ['B', '99.0', '99.0'], ['C', '98.0', '98.0']]
        page = self.extract(expected)
        self.assertEqual(len(page['tables']), 1)
        self.assertEqual(page['tables'][0]['rows'], expected)
        positions = {(c['row'], c['column']) for c in page['tables'][0]['cells'] if c['text'] == '99.0'}
        self.assertEqual(positions, {(1, 1), (1, 2), (2, 1), (2, 2)})


if __name__ == '__main__':
    unittest.main()
