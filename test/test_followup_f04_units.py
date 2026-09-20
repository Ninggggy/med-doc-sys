"""F04 实际尺度：从绘制坐标独立计算预期，真实入口；原生页禁止 OCR。"""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import fitz

from agent.test.test_pdf_page_extraction import gradient_page
from agent.test.test_f04_pdf_coordinates import ROWS, CELLS, NOTE
from agent.agent_backend.utils.parser.pdf_page_extractor import extract_pdf_pages
from agent.agent_backend.utils.parser.drug_supplement_pdf_parser import OCRServiceClient


ROOT = Path(os.getenv('F04_EVIDENCE_DIR', '/tmp/followup-20260906/f04/default'))
ANGLES = (0, 90, 180, 270)
# 单位、旋转、原点、裁剪、位置构成笛卡尔积；所有表格完整可见。
GEOMETRIES = (
    ((20, 30, 480, 490), None),
    ((20, 15, 480, 450), None),
    (None, None),
    ((20, 30, 480, 490), (10, 15, 510, 615)),
    ((20, 30, 480, 490), (-10, -15, 490, 585)),
    (None, (10, 15, 510, 615)),
    (None, (-10, -15, 490, 585)),
)


class F04UnitsTests(unittest.TestCase):
    def setUp(self):
        ROOT.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix=self._testMethodName + '-', dir=ROOT))
        self.path = self.root / 'input.pdf'

    def make(self, unit=2, angle=0, crop=(20, 30, 480, 490), media=None,
             shift=(0, 0), mode='native'):
        with fitz.open() as doc, fitz.open() as src:
            if mode == 'native':
                page = gradient_page(doc, '3.2.P.5.1 Specification')
                if shift != (0, 0):
                    stream = (f'q 1 0 0 1 {shift[0]} {-shift[1]} cm\n'.encode()
                              + page.read_contents() + b'\nQ')
                    first = page.get_contents()[0]
                    doc.update_stream(first, stream)
                    page.set_contents(first)
            else:
                original = gradient_page(src)
                if mode == 'mixed':
                    native = gradient_page(doc, '3.2.P.5.1 Specification')
                    doc.xref_set_key(native.xref, 'UserUnit', str(unit))
                    native.set_rotation(angle)
                page = doc.new_page(width=500, height=500)
                region = original.rect
                if mode == 'local':
                    page.insert_text((40, 40), '3.2.P.5.1 Specification', fontsize=14)
                    region = fitz.Rect(35, 75, 465, 260)
                page.insert_image(region, stream=original.get_pixmap(
                    matrix=fitz.Matrix(3, 3), clip=region).tobytes('png'))
            if media:
                doc.xref_set_key(page.xref, 'MediaBox', str(list(media)).replace(',', ''))
                page = doc.reload_page(page)
            if crop:
                page.set_cropbox(fitz.Rect(crop))
            doc.xref_set_key(page.xref, 'UserUnit', str(unit))
            page.set_rotation(angle)
            doc.save(self.path)
        self.unit, self.angle, self.crop = unit, angle, crop
        self.media, self.shift = media or (0, 0, 500, 500), shift
        self.original = self.path.read_bytes()

    def expected_box(self, box, *, crop=True):
        # PDF 绘制基于原始 500 高页面；改变 MediaBox 不移动内容。
        left = self.crop[0] if crop and self.crop else self.media[0]
        top = (self.crop[1] if crop and self.crop else 0) - (self.media[3] - 500)
        return [(v + self.shift[i % 2] - (left, top)[i % 2]) * self.unit
                for i, v in enumerate(box)]

    def assert_box(self, actual, expected, delta=.03):
        self.assertEqual(len(actual), 4)
        for a, e in zip(actual, expected):
            self.assertAlmostEqual(a, e, delta=delta)

    def assert_table(self, page, *, crop=True, delta=.03, native=True):
        self.assertEqual(page['status'], 'success', page.get('errors'))
        self.assertEqual(page['source_rotation'], self.angle)
        self.assertEqual(len(page['tables']), 1)
        table = page['tables'][0]
        self.assertEqual(table['rows'], ROWS)
        self.assertEqual(table['header_rows'], [0, 1])
        self.assertFalse(table['needs_review'])
        self.assertEqual(table['coordinate_unit'], 'pdf_point')
        self.assertEqual(table['page'], page['page'])
        self.assert_box(table['bbox_pdf'], self.expected_box([40, 80, 460, 220], crop=crop), delta)
        self.assertEqual(len(table['cells']), len(CELLS))
        for r, c, rowspan, colspan, box in CELLS:
            cell = next(v for v in table['cells'] if (v['row'], v['column']) == (r, c))
            self.assertEqual((cell['rowspan'], cell['colspan'], cell['text']),
                             (rowspan, colspan, ROWS[r][c]))
            self.assert_box(cell['bbox_pdf'], self.expected_box(box, crop=crop), delta)
            if r >= 2:
                self.assertTrue(any(w['text'] == ROWS[r][c] and
                                    fitz.Rect(w['bbox']) in fitz.Rect(cell['bbox_pdf'])
                                    for w in page['words']), (r, c, cell))
        self.assertEqual([n['text'] for n in table['notes']], [NOTE])
        self.assertGreater(table['notes'][0]['bbox'][1], table['bbox_pdf'][3])
        self.assertEqual(page['text'].count(table['markdown']), 1)
        self.assertEqual(page['text'].count(NOTE), 1)
        if native:
            self.assertEqual(page['ocr_calls'], 0)
            self.assertEqual(table['source'], 'native')
        self.assertEqual(self.path.read_bytes(), self.original)

    def save(self, name, data):
        (self.root / (name + '.json')).write_text(json.dumps(data, ensure_ascii=False, indent=2))

    def check_geometry(self, unit, angle, crop, media, shift):
        self.make(unit, angle, crop, media, shift)
        with patch.object(OCRServiceClient, 'image_to_data', side_effect=AssertionError('原生页禁止 OCR')) as ocr:
            page = extract_pdf_pages(str(self.path))[0]
        self.save('result', page)
        ocr.assert_not_called()
        self.assert_table(page)
        width = crop[2]-crop[0] if crop else self.media[2]-self.media[0]
        height = crop[3]-crop[1] if crop else self.media[3]-self.media[1]
        self.assert_box(page['page_bbox'], [0, 0, width*unit, height*unit])

    def check_original(self, angle):
        source = Path(__file__).parent / 'f01_f07_independent_review_20260905/evidence' / f'userunit_{angle}.pdf'
        self.path.write_bytes(source.read_bytes())
        self.unit, self.angle, self.crop = 2, angle, (20, 30, 480, 490)
        self.media, self.shift, self.original = (0, 0, 500, 500), (0, 0), self.path.read_bytes()
        with patch.object(OCRServiceClient, 'image_to_data', side_effect=AssertionError('原件原生页禁止 OCR')) as ocr:
            page = extract_pdf_pages(str(self.path))[0]
        self.save('result', page)
        ocr.assert_not_called()
        self.assert_table(page)
        self.assertEqual(source.read_bytes(), self.original)

    def check_entries(self, angle, entry):
        self.make(angle=angle)
        with patch.object(OCRServiceClient, 'image_to_data', side_effect=AssertionError('入口原生页禁止 OCR')) as ocr:
            if entry == 'filing':
                from agent.test.test_filing_change_review_service_regression import _TestConnection, _Upload
                from agent.agent_backend.services.filing_change_review_service import FilingChangeReviewService
                from agent.agent_backend.services.filing_change_material_service import FilingChangeMaterialService
                def service():
                    result = object.__new__(FilingChangeReviewService)
                    result.root_dir = self.root / 'filing'
                    result.root_dir.mkdir(exist_ok=True)
                    result.db_conn = _TestConnection(self.root / 'filing.sqlite')
                    self.addCleanup(result.db_conn.engine.dispose)
                    result.material_service = FilingChangeMaterialService()
                    return result
                writer = service()
                ok, message, project = writer.create_project({'project_name': 'F04实际尺度'})
                self.assertTrue(ok, message)
                pid = project['project_id']
                ok, message, uploaded = writer.upload_submission_files(pid, [_Upload('gradient.pdf', self.original)], '5')
                self.assertTrue(ok, message)
                doc_id = uploaded['created'][0]['doc_id']
                ok, message, parsed = writer.parse_submission(pid, doc_id)
                self.assertTrue(ok, message)
                self.assertEqual(parsed['content_status'], 'success')
                ok, message, saved = service().get_submission_parsed_markdown(pid, doc_id)
                self.assertTrue(ok, message)
                self.save('readback', saved)
                self.assert_table(saved['parsed_chunks'][0])
                self.assertIn('| -1.25 | <=2.0 | 98.0 |', saved['markdown'])
            elif entry == 'ctd':
                from agent.agent_backend.utils.parser.ctd_paser import parse_pdf_to_markdown_json, parse_bound_section_pdf_to_payload
                payload = parse_pdf_to_markdown_json(str(self.path), [{'section_id': '3.2.p.5.1', 'section_name': 'Specification'}])
                self.save('ctd', payload)
                self.assert_table(payload['source_pages'][0])
                bound = parse_bound_section_pdf_to_payload(str(self.path), section_id='3.2.p.5.1')
                self.save('bound', bound)
                section = bound['sections'][0]
                self.assertEqual(section['raw_pages'], [1])
                self.assertEqual(section['tables'][0]['rows'], ROWS)
                self.assert_box(section['tables'][0]['bbox_pdf'], [40, 100, 880, 380])
                self.assertIn('| -1.25 | <=2.0 | 98.0 |', section['content'])
            else:
                from agent.agent_backend.memory.rag.document import DocumentProcessor
                processor = DocumentProcessor()
                document = processor.parse_to_document(str(self.path))
                self.save('knowledge', document.metadata['source_pages'])
                self.assert_table(document.metadata['source_pages'][0])
                chunks = processor.chunk_document(document)
                self.assertEqual(len(chunks), 1)
                chunk = chunks[0]
                self.assertEqual((chunk.page_start, chunk.page_end), (1, 1))
                self.assertEqual(chunk.metadata['tables'][0]['rows'], ROWS)
                self.assert_box(chunk.metadata['tables'][0]['bbox_pdf'], [40, 100, 880, 380])
                self.assertEqual(chunk.metadata['source_rotation'], angle)
                self.assertIn('| -1.25 | <=2.0 | 98.0 |', chunk.text)
                self.assertIn(NOTE, chunk.text)
        ocr.assert_not_called()

    def check_clipped(self, unit, angle, hidden):
        crop = (20, 230, 480, 490) if hidden else (45, 75, 480, 490)
        self.make(unit=unit, angle=angle, crop=crop)
        with patch.object(OCRServiceClient, 'image_to_data', side_effect=AssertionError('裁剪原生页禁止 OCR')) as ocr:
            page = extract_pdf_pages(str(self.path))[0]
        self.save('clipped', page)
        ocr.assert_not_called()
        self.assertEqual(page['status'], 'success')
        if hidden:
            self.assertEqual(page['tables'], [])
            self.assertEqual(page['text'], NOTE)
        else:
            table = page['tables'][0]
            self.assertEqual(table['rows'], ROWS)
            self.assert_box(table['bbox_pdf'], [0, 5*unit, 415*unit, 145*unit])
            for cell in table['cells']:
                self.assertTrue(fitz.Rect(cell['bbox_pdf']) in fitz.Rect(page['page_bbox']))
        self.assertEqual(self.path.read_bytes(), self.original)

    def check_ocr(self, unit, angle, mode, dpi=216):
        crop = (20, 30, 480, 490) if mode == 'local' else None
        self.make(unit=unit, angle=angle, mode=mode, crop=crop)
        client = OCRServiceClient(os.environ['T2_OCR_URL'])
        with patch.object(client, 'image_to_data', wraps=client.image_to_data) as ocr:
            pages = extract_pdf_pages(str(self.path), ocr_client=client, lang='eng', dpi=dpi)
        self.save('ocr', pages)
        attempts = [a for a in pages[-1].get('ocr_recovery', []) if a['status'] != 'not_attempted']
        self.assertLessEqual(len(attempts), 4)
        self.assertEqual(ocr.call_count, 1 + len(attempts))
        self.assert_table(pages[-1], delta=1, native=False)
        self.assertEqual(pages[-1]['ocr_calls'], ocr.call_count)
        self.assertEqual(pages[-1]['path'], ['native', 'region_ocr'] if mode == 'local' else ['page_ocr'])
        self.assertEqual(pages[-1]['parse_diagnostics']['ocr_calls'], ocr.call_count)
        if mode == 'mixed':
            self.assert_table(pages[0], crop=False)

    def check_invalid_geometry(self, key, value, angle):
        with fitz.open() as doc:
            page = gradient_page(doc)
            doc.xref_set_key(page.xref, key, value)
            page.set_rotation(angle)
            doc.save(self.path)
        original = self.path.read_bytes()
        with patch.object(OCRServiceClient, 'image_to_data', side_effect=AssertionError('非法尺度禁止 OCR')) as ocr:
            result = extract_pdf_pages(str(self.path))[0]
        self.save('invalid', result)
        ocr.assert_not_called()
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['parse_diagnostics']['status'], 'failed')
        self.assertEqual(result['tables'], [])
        self.assertEqual(result['ocr_calls'], 0)
        self.assertTrue(any(e['stage'] == 'page_geometry' and e.get('code') == 'page_geometry' and e.get('reason')
                            for e in result['errors']), result['errors'])
        self.assertEqual(self.path.read_bytes(), original)

    @unittest.skipUnless(os.getenv('T2_OCR_URL'), '设置 T2_OCR_URL 运行真实 OCR')
    def test_coverage_still_rejects_missing_numeric_glyph(self):
        self.make(unit=2, mode='scan', crop=None)
        client = OCRServiceClient(os.environ['T2_OCR_URL'])
        recognize = client.image_to_data
        removed = []
        def missing_number(*args, **kwargs):
            data = recognize(*args, **kwargs)
            removed.extend(text for text in data['text'] if text == '98.0')
            data['text'] = ['' if text == '98.0' else text for text in data['text']]
            return data
        with patch.object(client, 'image_to_data', side_effect=missing_number) as ocr:
            page = extract_pdf_pages(str(self.path), ocr_client=client, lang='eng')[0]
        self.save('missing-number', page)
        self.assertTrue(removed, '故障注入必须实际删除目标数值，不能以空操作通过')
        attempts = [a for a in page.get('ocr_recovery', []) if a['status'] != 'not_attempted']
        self.assertGreaterEqual(len(attempts), 1)
        self.assertLessEqual(len(attempts), 4)
        self.assertEqual(ocr.call_count, 1 + len(attempts))
        self.assertEqual(page['status'], 'partial')
        failures = [e for e in page['errors'] if e['stage'] == 'ocr_coverage']
        self.assertTrue(failures, page['errors'])
        # 缺失组件必须实际覆盖98.0所在格的字形区域，不能仅依赖别处的线框残留报错。
        self.assertTrue(any(fitz.Rect(box).intersects(fitz.Rect(680, 380, 740, 415))
                            for e in failures for box in e['uncovered_components']))
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_invalid_actual_scale_and_source_origin_raise(self):
        from types import SimpleNamespace
        from agent.agent_backend.utils.parser.pdf_page_extractor import plumber_to_page_matrix
        self.make()
        with fitz.open(self.path) as doc:
            page = doc[0]
            for bounds in ((0, 0, 0, 920), (0, 0, float('inf'), 920),
                           (0, 0, float('nan'), 920), (0, 0, 460, 920)):
                with self.subTest(actual_bounds=bounds):
                    fake = SimpleNamespace(rect=fitz.Rect(bounds), mediabox=page.mediabox,
                                           cropbox=page.cropbox, parent=doc, xref=page.xref)
                    with self.assertRaises(ValueError):
                        plumber_to_page_matrix(SimpleNamespace(mediabox=(0, 0, 500, 500)), fake, 0)
            with self.assertRaises(ValueError):
                plumber_to_page_matrix(SimpleNamespace(mediabox=(float('nan'), 0, 500, 500)), page, 0)

    def test_invalid_pdf_service_attempt_preserves_old_original(self):
        from agent.test.t123_audit_20260905.audit_crash import service_at
        from agent.test.test_four_semantic_table_defects import word_file
        from agent.test.test_filing_change_review_service_regression import _Upload
        service = service_at(self.root)
        self.addCleanup(service.db_conn.engine.dispose)
        ok, message, project = service.create_project({'project_name': 'F04非法页面尺度保护'})
        self.assertTrue(ok, message)
        pid = project['project_id']
        valid = self.root / 'valid.docx'
        word_file(valid, [['药品通用名称', '此前有效药品']])
        ok, message, _ = service.import_application_form(pid, _Upload(valid.name, valid.read_bytes()))
        self.assertTrue(ok, message)
        before = service.get_application_form(pid)[2]
        original = service._project_path(pid) / 'application_form' / (before['original_file_id'] + '.docx')
        old_bytes = original.read_bytes()
        self.make(unit=-2)
        with patch.object(OCRServiceClient, 'image_to_data', side_effect=AssertionError('非法尺度禁止 OCR')) as ocr:
            ok, message, attempt = service.import_application_form(pid, _Upload(self.path.name, self.original))
        ocr.assert_not_called()
        self.save('failed-attempt', {'ok': ok, 'message': message, 'attempt': attempt})
        self.assertFalse(ok)
        self.assertEqual(attempt['code'], 'page_geometry')
        self.assertIn('页面尺度', message)
        fresh = service_at(self.root)
        self.addCleanup(fresh.db_conn.engine.dispose)
        ok, message, saved = fresh.get_application_form(pid)
        self.assertTrue(ok, message)
        self.save('preserved-original', saved)
        self.assertEqual(saved['original_file_id'], before['original_file_id'])
        self.assertEqual(saved['form_json'], before['form_json'])
        self.assertEqual(saved['parse_status'], 'success')
        self.assertEqual(saved['latest_attempt']['code'], 'page_geometry')
        self.assertEqual(original.read_bytes(), old_bytes)
        self.assertEqual(self.path.read_bytes(), self.original)

    def check_partial_media_intersection(self, unit, angle):
        self.make(unit=unit, angle=angle, crop=None)
        path = self.root / 'partial-media.pdf'
        with fitz.open(self.path) as doc:
            doc.xref_set_key(doc[0].xref, 'CropBox', '[-20 30 480 490]')
            doc.save(path)
        self.path, self.crop, self.original = path, (0, 10, 480, 470), path.read_bytes()
        with patch.object(OCRServiceClient, 'image_to_data', side_effect=AssertionError('部分交集原生页禁止 OCR')) as ocr:
            page = extract_pdf_pages(str(path))[0]
        self.save('partial-media', page)
        ocr.assert_not_called()
        self.assert_table(page)
        self.assert_box(page['page_bbox'], [0, 0, 480*unit, 460*unit])

    def test_invalid_and_valid_pages_are_isolated(self):
        self.make(unit=2, crop=None)
        path = self.root / 'invalid-and-valid.pdf'
        with fitz.open(self.path) as doc:
            invalid = gradient_page(doc)
            doc.xref_set_key(invalid.xref, 'UserUnit', '-2')
            doc.select([1, 0])
            doc.save(path)
        self.path, self.original = path, path.read_bytes()
        with patch.object(OCRServiceClient, 'image_to_data', side_effect=AssertionError('异常页不重试 OCR')) as ocr:
            pages = extract_pdf_pages(str(path))
        self.save('isolated-pages', pages)
        ocr.assert_not_called()
        self.assertEqual([p['status'] for p in pages], ['failed', 'success'])
        self.assertEqual(pages[0]['errors'][0]['code'], 'page_geometry')
        self.assertEqual(pages[0]['parse_diagnostics']['status'], 'partial')
        self.assert_table(pages[1])


def geometry_test(unit, angle, crop, media, shift):
    return lambda self: self.check_geometry(unit, angle, crop, media, shift)


for _unit in (.5, 1, 1.25, 2):
    for _angle in ANGLES:
        for _index, (_crop, _media) in enumerate(GEOMETRIES):
            for _position, _shift in enumerate(((-10, 0), (0, 40), (10, 100))):
                setattr(F04UnitsTests, f'test_geometry_u{str(_unit).replace(".", "_")}_r{_angle}_g{_index}_p{_position}',
                        geometry_test(_unit, _angle, _crop, _media, _shift))

for _angle in ANGLES:
    setattr(F04UnitsTests, f'test_original_userunit2_r{_angle}',
            lambda self, angle=_angle: self.check_original(angle))
    for _entry in ('filing', 'ctd', 'knowledge'):
        setattr(F04UnitsTests, f'test_entry_{_entry}_r{_angle}',
                lambda self, angle=_angle, entry=_entry: self.check_entries(angle, entry))

for _unit in (.5, 2):
    for _angle in ANGLES:
        for _hidden in (False, True):
            setattr(F04UnitsTests, f'test_clip_u{_unit}_r{_angle}_hidden{_hidden}',
                    lambda self, unit=_unit, angle=_angle, hidden=_hidden: self.check_clipped(unit, angle, hidden))

for _unit in (1, 2):
    for _angle in ANGLES:
        for _mode in ('scan', 'local', 'mixed'):
            setattr(F04UnitsTests, f'test_ocr_u{_unit}_r{_angle}_{_mode}',
                    unittest.skipUnless(os.getenv('T2_OCR_URL'), '设置 T2_OCR_URL 运行真实 OCR')(
                        lambda self, unit=_unit, angle=_angle, mode=_mode: self.check_ocr(unit, angle, mode)))

for _dpi in (144, 288):
    for _mode in ('scan', 'local', 'mixed'):
        setattr(F04UnitsTests, f'test_ocr_render_dpi{_dpi}_{_mode}',
                unittest.skipUnless(os.getenv('T2_OCR_URL'), '设置 T2_OCR_URL 运行真实 OCR')(
                    lambda self, dpi=_dpi, mode=_mode: self.check_ocr(2, 0, mode, dpi=dpi)))

for _unit, _dpi in ((.5, 432), (1.25, 216)):
    for _angle in ANGLES:
        for _mode in ('scan', 'local'):
            setattr(F04UnitsTests, f'test_ocr_fractional_u{_unit}_r{_angle}_{_mode}',
                    unittest.skipUnless(os.getenv('T2_OCR_URL'), '设置 T2_OCR_URL 运行真实 OCR')(
                        lambda self, unit=_unit, angle=_angle, mode=_mode, dpi=_dpi: self.check_ocr(unit, angle, mode, dpi=dpi)))

for _unit in (.5, 1, 1.25, 2):
    for _angle in ANGLES:
        setattr(F04UnitsTests, f'test_partial_media_u{_unit}_r{_angle}',
                lambda self, unit=_unit, angle=_angle: self.check_partial_media_intersection(unit, angle))

for _name, _key, _value in (
        ('zero_unit', 'UserUnit', '0'), ('negative_unit', 'UserUnit', '-2'),
        ('nonnumeric_unit', 'UserUnit', '/NaN'), ('string_unit', 'UserUnit', '(2)'),
        ('overflow_unit', 'UserUnit', '99999999999999999999999999999999999999999999999999'),
        ('disjoint_crop', 'CropBox', '[600 600 900 900]'),
        ('empty_crop', 'CropBox', '[10 10 10 10]')):
    for _angle in ANGLES:
        setattr(F04UnitsTests, f'test_invalid_{_name}_r{_angle}',
                lambda self, key=_key, value=_value, angle=_angle: self.check_invalid_geometry(key, value, angle))


if __name__ == '__main__':
    unittest.main()
