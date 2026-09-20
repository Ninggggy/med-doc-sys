"""完整矩形子表下方续行未闭合：独立期望与真实OCR，不猜造末行。"""
import os
import json
from pathlib import Path
import tempfile
import unittest
import fitz
from agent.agent_backend.utils.parser.pdf_page_extractor import extract_pdf_pages
from agent.agent_backend.utils.parser.drug_supplement_pdf_parser import OCRServiceClient


class OpenTailTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('T2_OCR_URL'), '需显式本地OCR服务')
    def test_scaled_skewed_scan_retains_closed_rows_and_tail(self):
        import cv2
        import numpy as np
        with tempfile.TemporaryDirectory() as root:
            source = Path(root)/'source.pdf'
            self.make(source, scan=True)
            with fitz.open(source) as original:
                pix = original[0].get_pixmap(matrix=fitz.Matrix(3,3), alpha=False)
                image = np.frombuffer(pix.samples, np.uint8).reshape(pix.height,pix.width,3)
            for angle in (-.5,.5):
                with self.subTest(angle=angle):
                    matrix = cv2.getRotationMatrix2D((pix.width/2,pix.height/2),angle,1)
                    tilted = cv2.warpAffine(image,matrix,(pix.width,pix.height),borderValue=(255,255,255))
                    path = Path(root)/f'skew-{angle}.pdf'
                    with fitz.open() as doc:
                        page = doc.new_page(width=500,height=400)
                        page.insert_image(page.rect,stream=cv2.imencode('.png',tilted)[1].tobytes())
                        doc.xref_set_key(page.xref,'UserUnit','2')
                        doc.save(path)
                    page = extract_pdf_pages(str(path),ocr_client=OCRServiceClient(os.environ['T2_OCR_URL']),lang='eng')[0]
                    if os.environ.get('OPEN_TAIL_EVIDENCE'):
                        evidence = Path(os.environ['OPEN_TAIL_EVIDENCE'])
                        evidence.mkdir(parents=True,exist_ok=True)
                        (evidence/f'skew-{angle}.pdf').write_bytes(path.read_bytes())
                        (evidence/f'skew-{angle}.json').write_text(json.dumps(page,ensure_ascii=False,indent=2))
                    self.assertEqual(page['tables'][0]['rows'],[
                        ['Item','A','B'],['Alpha','3.0','4.0'],['Beta','5.0','6.0']])
                    for word in ('Tail','7.0','8.0','Footnote'):
                        self.assertIn(word,page['text'])
                    self.assertEqual(page['status'],'partial')
                    self.assertLessEqual(page['ocr_calls'],5)

    @unittest.skipUnless(os.environ.get('T2_OCR_URL'), '需显式本地OCR服务')
    def test_sampling_is_per_page_not_carried_to_next_page(self):
        with tempfile.TemporaryDirectory() as root:
            source, path = Path(root)/'source.pdf', Path(root)/'mixed-units.pdf'
            self.make(source, scan=True)
            with fitz.open(source) as original, fitz.open() as doc:
                doc.insert_pdf(original)
                doc.insert_pdf(original)
                doc.xref_set_key(doc[0].xref, 'UserUnit', '2')
                doc.save(path)
            pages = extract_pdf_pages(str(path), ocr_client=OCRServiceClient(os.environ['T2_OCR_URL']), lang='eng')
            self.assertEqual([p['dpi'] for p in pages], [108,216])
            for page in pages:
                self.assertEqual(page['tables'][0]['rows'], [
                    ['Item','A','B'], ['Alpha','3.0','4.0'], ['Beta','5.0','6.0']])
                self.assertEqual(page['status'], 'partial')
                self.assertLessEqual(page['ocr_calls'],5)

    def test_sampling_leaves_low_resolution_multi_image_and_vector_pages_unchanged(self):
        from agent.agent_backend.utils.parser.pdf_page_extractor import scan_sampling_dpi
        with fitz.open() as doc:
            page = doc.new_page(width=1000,height=800)
            image = {'bbox':[0,0,1000,800], 'width':1500, 'height':1200,
                     'transform':[1000,0,0,800,0,0]}
            self.assertEqual(scan_sampling_dpi(page,[image],216,2),108)
            self.assertEqual(scan_sampling_dpi(page,[image],216,1),216)
            self.assertEqual(scan_sampling_dpi(page,[{**image,'width':500,'height':400}],216,2),216)
            self.assertEqual(scan_sampling_dpi(page,[image,image],216,2),216)
            self.assertEqual(scan_sampling_dpi(page,[{**image,'width':1600}],216,2),216)
            page.draw_line((1,1),(10,10))
            self.assertEqual(scan_sampling_dpi(page,[image],216,2),216)

    def check_geometry_matrix(self, scan=False):
        from agent.agent_backend.services.filing_parse_readiness import source_issues
        from agent.agent_backend.services.filing_parse_resolution import apply_resolutions
        from copy import deepcopy
        with tempfile.TemporaryDirectory() as root:
            source = Path(root)/'source.pdf'
            self.make(source, scan=scan)
            for unit in (.5, 1, 2):
                for rotation in (0, 90, 180, 270):
                    with self.subTest(unit=unit, rotation=rotation):
                        path = Path(root)/f'u{unit}-r{rotation}.pdf'
                        with fitz.open(source) as doc:
                            doc[0].set_cropbox(fitz.Rect(20,30,480,300))
                            doc[0].set_rotation(rotation)
                            doc.xref_set_key(doc[0].xref, 'UserUnit', str(unit))
                            doc.save(path)
                        client = OCRServiceClient(os.environ['T2_OCR_URL']) if scan else None
                        page = extract_pdf_pages(str(path), ocr_client=client, lang='eng')[0]
                        if os.environ.get('OPEN_TAIL_EVIDENCE'):
                            evidence = Path(os.environ['OPEN_TAIL_EVIDENCE'])
                            evidence.mkdir(parents=True, exist_ok=True)
                            label = f'{"scan" if scan else "native"}-u{unit}-r{rotation}'
                            (evidence/(label+'.pdf')).write_bytes(path.read_bytes())
                            (evidence/(label+'.json')).write_text(json.dumps(page, ensure_ascii=False, indent=2))
                        self.assertEqual(page['status'], 'partial')
                        if scan:
                            self.assertLessEqual(page['ocr_calls'], 5)
                        else:
                            self.assertEqual(page['ocr_calls'], 0)
                        self.assertEqual(len(page['tables']), 1)
                        self.assertEqual(page['tables'][0]['rows'], [
                            ['Item','A','B'], ['Alpha','3.0','4.0'], ['Beta','5.0','6.0']])
                        for text in ('Tail', '7.0', '8.0', 'Footnote'):
                            self.assertIn(text, page['text'])
                        region = next(e for e in page['errors'] if e.get('source') == 'visible_grid_open_tail')
                        for actual, expected in zip(region['unclosed_bbox_pdf'], [20*unit,150*unit,440*unit,190*unit]):
                            self.assertAlmostEqual(actual, expected, delta=unit)
                        issues = source_issues({'content_status':'partial'}, 'submission', [page])
                        issue = next(i for i in issues if i['code'] == 'table_structure_unresolved')
                        self.assertEqual(issue.get('table_index'), 0)
                        original = deepcopy(page)
                        rows = page['tables'][0]['rows'] + [['Tail', '7.0', '8.0']]
                        fixed, resolved, _ = apply_resolutions([page], issues, [{
                            'issue_key': issue['issue_key'], 'action':'correct_table', 'reason':'合成原表完整核对',
                            'table': {'row_count':4, 'column_count':3, 'cells': [
                                {'row':r, 'column':c, 'text':v} for r,row in enumerate(rows) for c,v in enumerate(row)]}}])
                        self.assertIn(issue['issue_key'], resolved)
                        self.assertEqual(len(fixed[0]['tables']), 1)
                        self.assertEqual(fixed[0]['tables'][0]['rows'], rows)
                        self.assertEqual(page, original)

    def test_native_geometry_matrix_retains_tail_diagnosis_and_table_link(self):
        self.check_geometry_matrix()

    @unittest.skipUnless(os.environ.get('T2_OCR_URL'), '需显式本地OCR服务')
    def test_scanned_geometry_matrix_retains_content_and_tail(self):
        self.check_geometry_matrix(scan=True)

    def make(self, path, scan=False, filled_edges=False):
        with fitz.open() as source:
            page = source.new_page(width=500, height=400)
            for y in (60, 100, 140, 180):
                if filled_edges:
                    page.draw_rect(fitz.Rect(40, y-.4, 460, y+.4), color=None, fill=(0,0,0))
                else:
                    page.draw_line((40, y), (460, y))
            for x in (40, 180, 320, 460):
                if filled_edges:
                    page.draw_rect(fitz.Rect(x-.4, 60, x+.4, 220), color=None, fill=(0,0,0))
                else:
                    page.draw_line((x, 60), (x, 220))
            for r, row in enumerate((('Item', 'A', 'B'), ('Alpha', '3.0', '4.0'),
                                     ('Beta', '5.0', '6.0'), ('Tail', '7.0', '8.0'))):
                for c, text in enumerate(row):
                    page.insert_text((50+140*c, 85+40*r), text, fontsize=14)
            page.insert_text((40, 250), 'Footnote outside the table.', fontsize=12)
            if not scan:
                source.save(path)
            else:
                with fitz.open() as doc:
                    doc.new_page(width=500, height=400).insert_image(
                        page.rect, stream=page.get_pixmap(matrix=fitz.Matrix(3, 3)).tobytes('png'))
                    doc.save(path)

    def check(self, scan):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'open.pdf'
            self.make(path, scan)
            client = OCRServiceClient(os.environ['T2_OCR_URL']) if scan else None
            page = extract_pdf_pages(str(path), ocr_client=client, lang='eng')[0]
            if os.environ.get('OPEN_TAIL_EVIDENCE'):
                evidence = Path(os.environ['OPEN_TAIL_EVIDENCE'])
                evidence.mkdir(parents=True, exist_ok=True)
                label = 'scan' if scan else 'native'
                (evidence / (label+'.pdf')).write_bytes(path.read_bytes())
                (evidence / (label+'.json')).write_text(json.dumps(page, ensure_ascii=False, indent=2))
            self.assertEqual(len(page['tables']), 1)
            self.assertEqual(page['tables'][0]['rows'], [
                ['Item', 'A', 'B'], ['Alpha', '3.0', '4.0'], ['Beta', '5.0', '6.0']])
            self.assertIn('Tail', page['text'])
            self.assertIn('7.0', page['text'])
            self.assertIn('8.0', page['text'])
            self.assertIn('Footnote', page['text'])
            self.assertEqual(page['status'], 'partial')
            self.assertTrue(any(e.get('source') == 'visible_grid_open_tail' for e in page['errors']))
            if scan:
                self.assertLessEqual(page['ocr_calls'], 5)
            else:
                self.assertEqual(page['ocr_calls'], 0)

    def test_native_open_tail_is_not_silently_complete(self):
        self.check(False)

    def test_scaled_filled_rectangle_edges_keep_open_tail_diagnosis(self):
        with tempfile.TemporaryDirectory() as root:
            source, path = Path(root)/'source.pdf', Path(root)/'scaled.pdf'
            self.make(source, filled_edges=True)
            with fitz.open(source) as doc:
                doc.xref_set_key(doc[0].xref, 'UserUnit', '2')
                doc.save(path)
            page = extract_pdf_pages(str(path))[0]
            self.assertEqual(page['status'], 'partial')
            self.assertTrue(any(e.get('source') == 'visible_grid_open_tail' for e in page['errors']))
            self.assertEqual(page['tables'][0]['rows'][2], ['Beta', '5.0', '6.0'])

    @unittest.skipUnless(os.environ.get('T2_OCR_URL'), '需显式本地OCR服务')
    def test_scanned_open_tail_keeps_closed_table(self):
        self.check(True)
