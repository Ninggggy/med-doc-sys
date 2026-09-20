"""受控 PDF 的期望值独立写定；正常扫描测试必须连接真实 OCR 服务。"""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import fitz

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from agent.agent_backend.utils.parser.pdf_page_extractor import extract_pdf_pages, text_quality
from agent.agent_backend.utils.parser.drug_supplement_pdf_parser import OCRServiceClient, parse_drug_supplement_pdf
from agent.agent_backend.utils.parser import ParserManager


def gradient_page(doc, title='Gradient elution', continuation=False):
    p = doc.new_page(width=500, height=500)
    p.insert_text((40, 40), title, fontsize=14)
    # 时间列跨两行；流动相分为 A、B 两列。
    for y in (80, 140, 180, 220):
        p.draw_line((40, y), (460, y))
    p.draw_line((180, 110), (460, 110))
    for x in (40, 180, 460):
        p.draw_line((x, 80), (x, 220))
    p.draw_line((320, 110), (320, 220))
    for x, y, text in [(50, 105, 'Time (min)'), (200, 100, 'Mobile phase (%)'),
                       (200, 132, 'A'), (340, 132, 'B'),
                       (50, 165, '0.5'), (200, 165, '50.0'), (340, 165, '50.0'),
                       (50, 205, '-1.25'), (200, 205, '<=2.0'), (340, 205, '98.0')]:
        p.insert_text((x, y), text, fontsize=12)
    p.insert_text((40, 250), '* Range: 0.5-2.0 mg/mL; 95%; note 1.', fontsize=11)
    return p


class PDFPagesTests(unittest.TestCase):
    def test_invisible_scan_text_is_not_used_to_mask_visible_source(self):
        from unittest.mock import Mock
        from agent.agent_backend.utils.parser import pdf_page_extractor as parser
        self.make(scan=True)
        with fitz.open(self.path) as source:
            source[0].insert_text((40,50), 'Outdated embedded recognition with wrong characters',render_mode=3)
            source.saveIncr()
        seen = []
        def recognize(page, rect, client, lang, dpi, psm, covered_words=(), **kwargs):
            seen.append(list(covered_words))
            word = {'text':'Visible original', 'bbox':[40,40,150,55], 'source':'ocr','confidence':99}
            return [word], [word], page.get_pixmap()
        with patch.object(parser,'ocr_region',side_effect=recognize), patch.object(
                parser,'recover_ocr_coverage',return_value=([],[],[])), patch.object(
                parser,'raster_cells',return_value=[]), patch.object(parser,'borderless_tables',return_value=([],[])):
            pages = parser.extract_pdf_pages(str(self.path),ocr_client=Mock(timeout_seconds=120))
        self.assertEqual(seen,[[]])
        self.assertIn('Visible original',pages[0]['text'])
        self.assertNotIn('Outdated',pages[0]['text'])
        self.assertIn('Outdated',pages[0]['native_text'])
        self.assertTrue(pages[0]['embedded_text_candidates'])
        self.assertIn('embedded_ocr_revalidation',pages[0]['path'])

    def test_repository_readable_stamp_region_with_original_gray_single_line(self):
        import time
        import unicodedata
        from agent.agent_backend.utils.parser import pdf_page_extractor as parser
        endpoint = os.environ.get('T2_OCR_URL')
        if not endpoint:
            self.skipTest('需要显式本地OCR地址')
        source = Path(__file__).resolve().parents[1]/'task/change_review/申请表模板.pdf'
        if not source.exists():
            self.skipTest('本地申请表模板缺失')
        with fitz.open(source) as document:
            region = fitz.Rect(235,668,318,696)
            words, _, _ = parser.ocr_region(document[0],region,OCRServiceClient(endpoint),
                'chi_sim+eng',180,7,original=True,deadline=time.monotonic()+120)
        # 预期独立来自原页视觉核对，不用“包含短语”掩盖多余识别内容。
        self.assertEqual(unicodedata.normalize('NFKC',''.join(w['text'] for w in words)),
                         '(加盖公章处)')
        self.assertTrue(all(region.contains(fitz.Rect(w['bbox'])) for w in words))

    def test_reused_recovery_result_is_not_counted_as_an_ocr_call(self):
        from agent.agent_backend.utils.parser import pdf_page_extractor as parser
        self.make(scan=True)
        word = {'text':'Synthetic', 'bbox':[40,30,100,45], 'confidence':99, 'source':'ocr'}
        attempts = [{'status':'unresolved', 'bbox_pdf':[40,30,100,45]},
                    {'status':'unresolved', 'bbox_pdf':[40,30,100,45], 'reused_attempt_index':0}]
        def primary(page, rect, *args, **kwargs):
            return [word], [word], page.get_pixmap(clip=rect)
        with patch.object(parser, 'ocr_region', side_effect=primary), patch.object(
                parser, 'recover_ocr_coverage', return_value=([],[],attempts)):
            page = parser.extract_pdf_pages(str(self.path))[0]
        self.assertEqual(page['ocr_calls'], 2)
        self.assertEqual(len(page['ocr_recovery']), 2)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / 'sample.pdf'

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, scan=False, mixed=False, garbled=False, local=False):
        doc = fitz.open()
        if scan or mixed or local:
            src = fitz.open()
            p = gradient_page(src)
            pix = p.get_pixmap(matrix=fitz.Matrix(3, 3))
            if mixed:
                gradient_page(doc)
            page = doc.new_page(width=500, height=500)
            if local:
                page.insert_text((40, 30), 'Quality standard native narrative', fontsize=12)
                pix = p.get_pixmap(matrix=fitz.Matrix(3, 3), clip=fitz.Rect(35, 75, 465, 260))
                page.insert_image(fitz.Rect(35, 75, 465, 260), stream=pix.tobytes('png'))
            else:
                page.insert_image(page.rect, stream=pix.tobytes('png'))
            if garbled:
                for n in range(12):
                    page.insert_text((20, 20+n*15), '~', render_mode=3)
            src.close()
        else:
            gradient_page(doc)
        doc.save(self.path)
        doc.close()

    def assert_table(self, page):
        self.assertEqual(len(page['tables']), 1)
        t = page['tables'][0]
        self.assertEqual(t['rows'][2], ['0.5', '50.0', '50.0'])
        self.assertEqual(t['rows'][3], ['-1.25', '<=2.0', '98.0'])
        self.assertTrue(any(c['rowspan'] == 2 for c in t['cells']))
        self.assertTrue(any(c['colspan'] == 2 for c in t['cells']))
        self.assertIn('mg/mL', page['text'])
        self.assertIn('95%', page['text'])
        self.assertEqual(page['text'].count(t['markdown']), 1)
        self.assertEqual(t['page'], page['page'])

    def test_native_table_symbols_and_real_coordinates(self):
        self.make()
        pages = ParserManager.parse(str(self.path))
        self.assert_table(pages[0])
        self.assertEqual(pages[0]['ocr_calls'], 0)
        self.assertAlmostEqual(pages[0]['tables'][0]['bbox_pdf'][0], 40, delta=1)
        self.assertEqual(json.loads(json.dumps(pages)), pages)

    def test_quality_accepts_english_numbers_rejects_corruption(self):
        for s in ['License approved', '0.5 -1.25 <=2.0 98.0 % mg/mL', '药品生产许可证']:
            self.assertTrue(text_quality(s)['usable'])
        for s in ['\ufffd'*100, '\ue000'*100, '~ , ! . '*30]:
            self.assertFalse(text_quality(s)['usable'])

    def test_rotated_native_coordinates(self):
        doc = fitz.open()
        gradient_page(doc).set_rotation(90)
        doc.save(self.path)
        doc.close()
        page = extract_pdf_pages(str(self.path))[0]
        self.assert_table(page)
        self.assertEqual(page['source_rotation'], 90)
        self.assertAlmostEqual(page['tables'][0]['bbox_pdf'][0], 40, delta=1)

    def test_unsectioned_license_and_form_position(self):
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 160), '6. Generic name: Example medicine')
        doc.save(self.path)
        doc.close()
        result = parse_drug_supplement_pdf(self.path)
        region = result['items'][5]['source_regions'][0]
        self.assertAlmostEqual(region['bbox_pdf'][0], 72, delta=1)
        self.assertGreater(region['bbox_pdf'][1], 140)
        self.assertEqual(result['parse_diagnostics']['ocr_calls'], 0)
        self.assertIn('Example medicine', result['raw_text'])

    def test_blank_visual_and_partial_failure(self):
        self.make(mixed=True)
        doc = fitz.open(self.path)
        doc.new_page()
        p = doc.new_page()
        p.draw_line((20, 200), (300, 70))
        out = Path(self.tmp.name)/'partial.pdf'
        doc.save(out)
        doc.close()
        with patch.object(OCRServiceClient, 'image_to_data', side_effect=TimeoutError('injected timeout')):
            pages = extract_pdf_pages(str(out))
        self.assert_table(pages[0])
        self.assertEqual([p['status'] for p in pages], ['success', 'failed', 'blank', 'failed'])
        self.assertEqual(pages[3]['page_kind'], 'visual_unresolved')
        self.assertEqual(pages[0]['parse_diagnostics']['status'], 'partial')
        self.assertIn('超时', pages[1]['errors'][0]['reason'])
        self.assertNotIn('injected timeout', pages[1]['errors'][0]['reason'])

    def test_entire_failure_is_explicit(self):
        self.make(scan=True)
        with patch.object(OCRServiceClient, 'image_to_data', side_effect=RuntimeError('offline')):
            pages = extract_pdf_pages(str(self.path))
        self.assertEqual(pages[0]['parse_diagnostics']['status'], 'failed')
        self.assertFalse(pages[0]['content_available'])

    def test_scanned_white_page_is_blank_without_ocr(self):
        src = fitz.open()
        blank = src.new_page()
        doc = fitz.open()
        page = doc.new_page()
        page.insert_image(page.rect, stream=blank.get_pixmap().tobytes('png'))
        doc.save(self.path)
        doc.close()
        src.close()
        result = extract_pdf_pages(str(self.path))[0]
        self.assertEqual(result['status'], 'blank')
        self.assertEqual(result['ocr_calls'], 0)
        self.assertEqual(result['parse_diagnostics']['status'], 'failed')

    def test_real_q2_visible_grid_and_percentages(self):
        source = Path(__file__).resolve().parents[1]/'agent_backend/data/uploads/0813b59cf9c2412c_Q2R2.pdf'
        if not source.exists() or getattr(source.stat(), 'st_flags', 0) & 0x40000000:
            self.skipTest('本地 Q2 原文件不存在或为云端占位文件')
        with fitz.open(source) as original, fitz.open() as doc:
            doc.insert_pdf(original, from_page=11, to_page=11)
            doc.save(self.path)
        page = extract_pdf_pages(str(self.path))[0]
        table = page['tables'][0]
        self.assertEqual(len(table['columns']), 3)
        self.assertEqual(table['rows'][2], ['效价', '最低接受标准-20%', '最高接受标准+20%'])
        self.assertIn('80%', table['rows'][1][1])
        self.assertIn('120%', table['rows'][1][2])
        self.assertEqual(page['ocr_calls'], 0)
        # 原页底部无闭合线，不能把可靠上部一起丢掉，也不能猜造尾行。
        self.assertEqual(page['status'], 'partial')
        self.assertIn('含量均匀度', page['text'])
        self.assertIn('70%', page['text'])
        self.assertIn('130%', page['text'])
        self.assertTrue(any(r.get('source') == 'visible_grid_open_tail'
                            for r in page.get('unresolved_tables', [])))
        from agent.agent_backend.services.filing_parse_readiness import source_issues, readiness_result
        issues = source_issues({'content_status': page['status']}, 'submission', [page])
        self.assertFalse(readiness_result(issues)['ready'])
        self.assertTrue(any(i['code'] == 'table_structure_unresolved' for i in issues))
        from copy import deepcopy
        from agent.agent_backend.services.filing_parse_resolution import apply_resolutions
        original_page = deepcopy(page)
        issue = next(i for i in issues if i['code'] == 'table_structure_unresolved')
        self.assertEqual(issue['table_index'], 0)
        rows = table['rows'] + [['含量均匀度', '标示量的70%', '标示量的130%']]
        corrected, resolved, _ = apply_resolutions([page], issues, [{
            'issue_key': issue['issue_key'], 'action': 'correct_table', 'reason': '逐格核对原页',
            'table': {'row_count': 4, 'column_count': 3,
                      'cells': [{'row': r, 'column': c, 'text': text}
                                for r, row in enumerate(rows) for c, text in enumerate(row)]}}])
        self.assertEqual(len(corrected[0]['tables']), 1)
        self.assertEqual(corrected[0]['tables'][0]['rows'], rows)
        self.assertIn(issue['issue_key'], resolved)
        self.assertEqual(page, original_page)

    def test_cross_page_tables_kept_independent_without_title_evidence(self):
        doc = fitz.open()
        gradient_page(doc, 'Gradient elution')
        gradient_page(doc, 'Different experiment')
        doc.save(self.path)
        doc.close()
        pages = extract_pdf_pages(str(self.path))
        self.assert_table(pages[0])
        self.assert_table(pages[1])
        self.assertNotEqual(pages[0]['tables'][0]['id'], pages[1]['tables'][0]['id'])

    def test_explicit_continuation_links_sources_and_repeated_headers(self):
        doc = fitz.open()
        gradient_page(doc, 'Gradient elution')
        gradient_page(doc, 'Gradient elution (continued)')
        doc.save(self.path)
        doc.close()
        pages = extract_pdf_pages(str(self.path))
        second = pages[1]['tables'][0]
        self.assertEqual(second['continuation_of'], pages[0]['tables'][0]['id'])
        self.assertEqual(second['repeated_header_rows'], [0, 1])
        self.assert_table(pages[1])

    def test_ctd_and_knowledge_keep_pdf_table_and_sources(self):
        from agent.agent_backend.utils.parser.ctd_paser import parse_pdf_to_markdown_json
        from agent.agent_backend.memory.rag.document import DocumentProcessor
        doc = fitz.open()
        gradient_page(doc, '3.2.P.5.1 Specification')
        doc.save(self.path)
        doc.close()
        payload = parse_pdf_to_markdown_json(str(self.path), [{'section_id': '3.2.p.5.1', 'section_name': 'Specification'}])
        self.assertEqual(payload['source_pages'][0]['tables'][0]['rows'][2], ['0.5', '50.0', '50.0'])
        self.assertIn('50.0', str(payload['sections']))
        processor = DocumentProcessor()
        document = processor.parse_to_document(str(self.path))
        chunks = processor.chunk_document(document)
        self.assertEqual(chunks[0].page_start, 1)
        self.assertEqual(chunks[0].metadata['tables'][0]['rows'][2], ['0.5', '50.0', '50.0'])
        self.assertIn('<=2.0', chunks[0].text)

    def test_bound_section_keeps_all_pages(self):
        from agent.agent_backend.utils.parser.ctd_paser import parse_bound_section_pdf_to_payload
        doc = fitz.open()
        gradient_page(doc)
        gradient_page(doc, 'Second page approval proof')
        doc.save(self.path)
        doc.close()
        payload = parse_bound_section_pdf_to_payload(str(self.path), section_id='3.2.p.5.1')
        self.assertIn('Second page approval proof', payload['sections'][0]['content'])
        self.assertEqual(payload['sections'][0]['raw_pages'], [1, 2])
        self.assertEqual(len(payload['sections'][0]['tables']), 2)

    def test_knowledge_saved_artifact_keeps_cells(self):
        from agent.agent_backend.memory.rag.pipeline import RAGPipeline
        from agent.agent_backend.memory.rag.document import DocumentProcessor
        self.make()
        pipeline = object.__new__(RAGPipeline)
        pipeline.processor = DocumentProcessor()
        pipeline._indexed_docs = set()
        pipeline.embedding = type('LocalEmbedding', (), {'embed': lambda _, text: [1.0]})()
        pipeline.store = type('LocalStore', (), {'add': lambda *args, **kwargs: None})()
        artifact = Path(self.tmp.name)/'saved.json'
        pipeline.index_file(str(self.path), doc_id='pdf-test', parsed_output_path=str(artifact))
        saved = json.loads(artifact.read_text())
        row = next(r for r in saved if r.get('tables'))
        self.assertEqual(row['page'], 1)
        self.assertEqual(row['tables'][0]['rows'][2], ['0.5', '50.0', '50.0'])
        self.assertEqual(row['parse_diagnostics']['status'], 'success')

    @unittest.skipUnless(os.getenv('T2_OCR_URL'), '设置 T2_OCR_URL 运行真实 OCR 验收')
    def test_real_chinese_license_without_sections(self):
        doc = fitz.open()
        page = doc.new_page(width=500, height=400)
        page.insert_text((60, 80), '药品生产许可证', fontname='china-s', fontsize=22)
        page.insert_text((60, 130), '许可证编号：20260001', fontname='china-s', fontsize=18)
        page.insert_text((60, 180), '企业名称：示例制药有限公司', fontname='china-s', fontsize=18)
        scan = fitz.open()
        target = scan.new_page(width=500, height=400)
        target.insert_image(target.rect, stream=page.get_pixmap(matrix=fitz.Matrix(3, 3)).tobytes('png'))
        scan.save(self.path)
        scan.close()
        doc.close()
        pages = extract_pdf_pages(str(self.path), ocr_client=OCRServiceClient(os.environ['T2_OCR_URL']), lang='chi_sim+eng')
        text = ''.join(pages[0]['text'].split())
        self.assertIn('药品生产许可证', text)
        self.assertIn('20260001', text)
        self.assertIn('示例制药有限公司', text)

    @unittest.skipUnless(os.getenv('T2_OCR_URL'), '设置 T2_OCR_URL 运行真实 OCR 验收')
    def test_real_ocr_scan_mixed_garbled_and_local(self):
        client = OCRServiceClient(os.environ['T2_OCR_URL'])
        for kwargs, calls in [({'scan': True}, 1), ({'mixed': True}, 1), ({'scan': True, 'garbled': True}, 1), ({'local': True}, 1)]:
            with self.subTest(kwargs=kwargs):
                self.make(**kwargs)
                pages = extract_pdf_pages(str(self.path), ocr_client=client, lang='eng')
                print('REAL_OCR', kwargs, pages[0]['parse_diagnostics'], flush=True)
                attempts = [a for p in pages for a in p.get('ocr_recovery', []) if a['status'] != 'not_attempted']
                self.assertLessEqual(len(attempts), 4)
                self.assertEqual(pages[0]['parse_diagnostics']['ocr_calls'], calls + len(attempts))
                self.assert_table(pages[-1])
                self.assertEqual(pages[-1]['status'], 'success')


if __name__ == '__main__':
    unittest.main()
