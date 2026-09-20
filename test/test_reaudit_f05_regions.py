"""再次复核 F05：独立输入事实、真实 PDF/服务/SQLite，证据仅写入 /tmp。"""
import json
import tempfile
import unittest
from pathlib import Path

import fitz

from agent.agent_backend.utils.parser.drug_supplement_pdf_parser import parse_drug_supplement_pdf
from agent.test.test_f01_f02_form_replacement import service_at, _Upload


FIELDS = {6: 'item_6_generic_name', 7: 'item_7_english_or_latin_name',
          10: 'item_10_trade_name', 12: 'item_12_specification'}


def compact(text):
    return ''.join(text.split())


class ReauditF05Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path('/tmp/reaudit-fixes-20260906/f05')
        root.mkdir(parents=True, exist_ok=True)
        cls.evidence_dir = Path(tempfile.mkdtemp(prefix='cases-', dir=root))
        print(f'F05 evidence: {cls.evidence_dir}', flush=True)

    def setUp(self):
        self.root = self.evidence_dir / self._testMethodName
        self.root.mkdir()
        self.path = self.root / 'input.pdf'

    def make(self, entries, *, width=900, font=12, cells=(), pages=None):
        # owner 由测试输入指定；重复文字分别按插入位置建立归属断言。
        self.locations = []
        with fitz.open() as doc:
            for page_no, part in enumerate(pages or [entries], 1):
                page = doc.new_page(width=width, height=600)
                for box in cells:
                    page.draw_rect(fitz.Rect(box))
                for x, y, text, owner in part:
                    page.insert_text((x, y), text, fontname='china-s', fontsize=font)
                    if owner is None:
                        continue
                    value = text.split('：', 1)[-1]
                    if not value:
                        continue
                    boxes = [b for b in page.search_for(value, flags=fitz.TEXTFLAGS_SEARCH | fitz.TEXT_INHIBIT_SPACES)
                             if b.x0 >= x-.02 and y-font*1.5 <= b.y0 <= y]
                    self.assertTrue(boxes, (text, x, y))
                    box = min(boxes, key=lambda b: abs(b.y1-y-font*.2))
                    self.locations.append((owner, page_no, list(box), value))
            doc.save(self.path)

    def parse(self, *, status='success', dpi=180):
        result = parse_drug_supplement_pdf(self.path, dpi=dpi)
        (self.root / 'parsed.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        self.assertEqual(result['parse_diagnostics']['ocr_calls'], 0)
        self.assertEqual(result['parse_diagnostics']['status'], status)
        if status == 'success':
            self.assertEqual(result['unassigned_regions'], [])
        return result, {i['item_no']: i for i in result['items']}

    def regions(self, number, regions):
        self.assertTrue(regions)
        for owner, page, box, text in self.locations:
            same_page = [r for r in regions if r['page'] == page]
            if owner == number:
                self.assertTrue(any((fitz.Rect(r['bbox_pdf']) + (-.03, -.03, .03, .03)).contains(fitz.Rect(box))
                                    for r in same_page), (number, text, box, regions))
            else:
                self.assertFalse(any(fitz.Rect(r['bbox_pdf']).intersects(fitz.Rect(box))
                                     for r in same_page), (number, 'neighbor', text, box, regions))
        for r in regions:
            self.assertEqual(r['coordinate_unit'], 'pdf_point')
            for px, pt in zip(r['bbox_px'], r['bbox_pdf']):
                self.assertAlmostEqual(px * 72 / r['dpi'], pt, places=2)

    def values(self, items, expected):
        for number, text in expected.items():
            with self.subTest(item=number):
                self.assertEqual(compact(items[number]['normalized_text']), compact(text))
                self.regions(number, items[number]['source_regions'])

    def original(self, mode):
        first, body, x = ('药品通用名称：', '甲乙缓释片', 180) if mode == 'A' else (
            '药品通用名称：甲乙', '缓释片', 200)
        self.make([(30, 60, first, 6), (x, 110, body, 6),
                   (490, 80, '规格：3mg', 12), (490, 120, '每盒10片', 12)])
        with fitz.open(self.path) as doc:
            doc[0].get_pixmap().save(self.root / 'input.png')

    def test_original_A_delayed_first_value(self):
        self.original('A')
        _, items = self.parse()
        self.values(items, {6: '甲乙缓释片', 12: '3mg 每盒10片'})

    def test_original_B_indented_continuation(self):
        self.original('B')
        _, items = self.parse()
        self.values(items, {6: '甲乙缓释片', 12: '3mg 每盒10片'})

    def persisted(self, expected, *, status='success'):
        base = self.root / 'service'
        base.mkdir()
        writer = service_at(base)
        self.addCleanup(writer.db_conn.engine.dispose)
        ok, msg, project = writer.create_project({'project_name': 'F05真实持久化来源核对'})
        self.assertTrue(ok, msg)
        pid = project['project_id']
        imported = writer.import_application_form(pid, _Upload(self.path.name, self.path.read_bytes()))
        writer.db_conn.engine.dispose()
        reader = service_at(base)
        self.addCleanup(reader.db_conn.engine.dispose)
        saved = reader.get_application_form(pid)
        reparsed = reader.parse_application_form(pid)
        reader.db_conn.engine.dispose()
        fresh = service_at(base)
        self.addCleanup(fresh.db_conn.engine.dispose)
        refreshed = fresh.get_application_form(pid)
        (self.root / 'persistence.json').write_text(json.dumps({
            'import': imported, 'new_connection_read': saved, 'reparse': reparsed,
            'new_connection_after_reparse': refreshed}, ensure_ascii=False, indent=2), encoding='utf-8')
        for operation in (imported, saved, reparsed, refreshed):
            self.assertTrue(operation[0], operation[1])
        file_id = saved[2]['original_file_id']
        self.assertNotEqual(saved[2]['form_json']['_parse_attempt_id'], refreshed[2]['form_json']['_parse_attempt_id'])
        for stage, state in [('saved', saved[2]), ('reparsed', refreshed[2])]:
            with self.subTest(stage=stage):
                self.assertEqual(state['parse_status'], status)
                self.assertEqual(state['original_file_id'], file_id)
                self.assertEqual(state['effective_source']['source_file_id'], file_id)
                self.assertEqual(state['effective_source']['source_file_name'], self.path.name)
                self.assertEqual(state['effective_source']['parse_diagnostics']['ocr_calls'], 0)
                stored = fresh._project_path(pid) / 'application_form' / (file_id + '.pdf')
                self.assertEqual(stored.read_bytes(), self.path.read_bytes())
                for number, expected_value in expected.items():
                    with self.subTest(item=number):
                        field = state['form_json'][FIELDS[number]]
                        source = field['value_sources']['value']
                        self.assertEqual(compact(field['value']), compact(expected_value))
                        self.assertEqual(source['value'], field['value'])
                        for evidence in (field, source):
                            self.assertEqual(evidence['source_file_id'], file_id)
                            self.assertEqual(evidence['source_file'], self.path.name)
                            self.regions(number, evidence['source_regions'])
                        self.assertEqual(source['recognition_status'], 'extracted')
                        self.assertEqual(field['source_text'], source['source_text'])
                        for owner, _, _, text in self.locations:
                            if owner == number:
                                self.assertIn(compact(text), compact(source['source_text']))
                            elif all(text != t for o, _, _, t in self.locations if o == number):
                                self.assertNotIn(text, source['source_text'])

    def test_service_A_new_connections_and_reparse(self):
        self.original('A')
        self.persisted({6: '甲乙缓释片', 12: '3mg 每盒10片'})

    def test_service_B_new_connections_and_reparse(self):
        self.original('B')
        self.persisted({6: '甲乙缓释片', 12: '3mg 每盒10片'})

    def layout(self, mode, width, right, font, indent, swapped, left_late, framed):
        drug, spec = (right, 30) if swapped else (30, right)
        drug_y, spec_y = (60, 60 + font*2) if swapped == left_late else (60 + font*2, 60)
        dx = drug + indent
        entries = [(drug, drug_y, '药品通用名称：' + ('甲乙' if mode == 'B' else ''), 6),
                   (dx, 160, '缓释片' if mode == 'B' else '甲乙缓释片', 6),
                   (dx + font, 205, '薄膜衣', 6), (spec, spec_y, '规格：3mg', 12),
                   (spec+font*2, 180, '每盒10片', 12)]
        cells = [(20, 35, right-10, 250), (right-10, 35, width-20, 250),
                 (20, 250, right-10, 285), (right-10, 250, width-20, 285)] if framed else []
        self.make(entries, width=width, font=font, cells=cells)
        _, items = self.parse(dpi=72 if font == 9 else 216)
        self.values(items, {6: '甲乙缓释片薄膜衣', 12: '3mg 每盒10片'})

    def test_value_before_neighbor_heading_control(self):
        self.make([(30, 60, '药品通用名称：', None), (180, 70, '甲乙', 6),
                   (180, 110, '缓释片', 6), (490, 80, '规格：3mg', 12), (490, 120, '每盒10片', 12)])
        _, items = self.parse()
        self.values(items, {6: '甲乙缓释片', 12: '3mg 每盒10片'})

    def test_both_labels_separate_from_delayed_values(self):
        self.make([(30, 60, '药品通用名称：', None), (180, 140, '甲乙缓释片', 6),
                   (490, 80, '规格：', None), (620, 125, '3mg', 12), (620, 170, '每盒10片', 12)])
        _, items = self.parse()
        self.values(items, {6: '甲乙缓释片', 12: '3mg 每盒10片'})

    def test_next_single_column_field_stops_old_field(self):
        self.make([(30, 60, '药品通用名称：', None), (180, 110, '甲乙缓释片', 6),
                   (30, 160, '规格：', None), (180, 205, '3mg', 12), (180, 250, '每盒10片', 12)])
        _, items = self.parse()
        self.values(items, {6: '甲乙缓释片', 12: '3mg 每盒10片'})

    def test_indented_next_single_column_field_stops_at_following_heading(self):
        self.make([(30, 60, '药品通用名称：甲乙', 6), (180, 110, '缓释片', 6),
                   (490, 160, '规格：3mg', 12), (530, 205, '每盒10片', 12),
                   (30, 260, '商品名称：丙丁', 10), (180, 300, '品牌', 10)])
        _, items = self.parse()
        self.values(items, {6: '甲乙缓释片', 12: '3mg 每盒10片', 10: '丙丁品牌'})

    def test_full_width_heading_resets_columns(self):
        self.make([(30, 60, '药品通用名称：', None), (180, 110, '甲乙缓释片', 6),
                   (360, 80, '规格：3mg', 12), (390, 125, '每盒10片', 12),
                   (30, 190, '提出补充申请的理由：完善生产工艺以及药品质量控制方法', 22),
                   (180, 235, '通栏补充说明', 22)])
        _, items = self.parse()
        self.values(items, {6: '甲乙缓释片', 12: '3mg 每盒10片',
                            22: '完善生产工艺以及药品质量控制方法通栏补充说明'})

    def test_same_text_different_positions(self):
        # 两栏出现相同文字；按内容去重/按药品词猜归属都不能通过。
        self.make([(30, 60, '药品通用名称：甲乙', 6), (200, 110, '缓释片', 6),
                   (490, 80, '规格：3mg', 12), (620, 120, '缓释片', 12)])
        _, items = self.parse()
        self.values(items, {6: '甲乙缓释片', 12: '3mg 缓释片'})
        self.persisted({6: '甲乙缓释片', 12: '3mg 缓释片'})

    def test_same_body_moved_across_column_changes_owner(self):
        self.make([(30, 60, '药品通用名称：甲乙', 6), (620, 110, '缓释片', 12),
                   (490, 80, '规格：3mg', 12), (620, 140, '每盒10片', 12)])
        _, items = self.parse()
        self.values(items, {6: '甲乙', 12: '3mg 缓释片每盒10片'})

    def test_indented_subfields_in_single_parent(self):
        self.make([(30, 35, '药品注册申请人：', None), (30, 75, '注册地址：', None),
                   (180, 125, '甲市甲路', 30), (490, 95, '通讯地址：', None),
                   (620, 155, '乙市乙路', 30), (180, 180, '三号', 30), (620, 210, '四号', 30)])
        _, items = self.parse()
        self.assertEqual(items[30]['subfields'].get('注册地址'), '甲市甲路三号')
        self.assertEqual(items[30]['subfields'].get('通讯地址'), '乙市乙路四号')

    def test_indented_subfields_in_staggered_parents(self):
        self.make([(30, 35, '药品注册申请人：', None), (490, 55, '生产企业：', None),
                   (30, 95, '中文名称：甲公司', 30), (490, 115, '中文名称：乙公司', 31),
                   (30, 150, '注册地址：', None), (490, 170, '生产地址：', None),
                   (200, 205, '甲市甲路', 30), (650, 235, '乙市乙路', 31),
                   (200, 260, '三号', 30), (650, 285, '四号', 31)])
        _, items = self.parse()
        self.assertEqual(items[30]['subfields'].get('注册地址'), '甲市甲路三号')
        self.assertEqual(items[31]['subfields'].get('生产地址'), '乙市乙路四号')
        self.regions(30, items[30]['source_regions'])
        self.regions(31, items[31]['source_regions'])

    def test_single_column_delayed_value_crosses_page(self):
        self.make([], pages=[[(30, 60, '药品通用名称：甲乙', 6)],
                            [(200, 60, '缓释片', 6), (30, 140, '规格：3mg', 12)]])
        _, items = self.parse()
        self.values(items, {6: '甲乙缓释片', 12: '3mg'})
        self.assertEqual(items[6]['source_pages'], [1, 2])

    def test_single_parent_subfield_crosses_page(self):
        self.make([], pages=[[(30, 60, '药品注册申请人：', None), (30, 100, '注册地址：甲市', 30)],
                            [(200, 60, '甲路三号', 30)]])
        _, items = self.parse()
        self.assertEqual(items[30]['subfields'].get('注册地址'), '甲市甲路三号')
        self.regions(30, items[30]['source_regions'])

    def test_columns_reestablished_on_next_page(self):
        self.make([], pages=[[(30, 60, '药品通用名称：', None), (180, 110, '甲乙缓释片', 6),
                             (490, 80, '规格：3mg', 12), (620, 125, '每盒10片', 12)],
                            [(30, 60, '药品通用名称：', None), (200, 130, '薄膜衣', 6),
                             (490, 90, '规格：', None), (620, 140, '避光保存', 12)]])
        _, items = self.parse()
        self.values(items, {6: '甲乙缓释片薄膜衣', 12: '3mg 每盒10片避光保存'})

    def test_columns_without_next_page_labels_are_uncertain(self):
        self.make([], pages=[[(30, 60, '药品通用名称：', None), (180, 110, '甲乙缓释片', 6),
                             (490, 80, '规格：3mg', 12), (620, 125, '每盒10片', 12)],
                            [(200, 60, '未确认的跨页续文', 'uncertain'),
                             (620, 90, '另一栏续文', 'uncertain')]])
        result, items = self.parse(status='partial')
        self.values(items, {6: '甲乙缓释片', 12: '3mg 每盒10片'})
        self.assertEqual({i['page'] for i in result['unassigned_regions']}, {2})
        self.assertEqual(len(result['unassigned_regions']), 2)

    def test_crossing_after_indented_value_is_uncertain(self):
        self.make([(30, 60, '药品通用名称：', None), (180, 110, '甲乙缓释片', 6),
                   (490, 80, '规格：3mg', 12), (620, 120, '每盒10片', 12),
                   (450, 165, '无法确定的跨栏文字', 'uncertain')])
        result, items = self.parse(status='partial')
        self.values(items, {6: '甲乙缓释片', 12: '3mg 每盒10片'})
        issue = result['unassigned_regions'][0]
        self.assertEqual(issue['code'], 'field_region_crossing')
        self.assertEqual(issue['candidate_items'], [6, 12])
        self.assertTrue(issue['needs_review'])
        self.assertIn(issue, result['pages'][0]['errors'])
        self.persisted({6: '甲乙缓释片', 12: '3mg 每盒10片'}, status='partial')

    def test_only_crossing_body_cannot_be_borrowed_by_new_heading(self):
        self.make([(30, 60, '药品通用名称：甲乙', 6), (490, 80, '规格：3mg', 12),
                   (450, 110, '无法确定的跨栏文字', 'uncertain'), (620, 140, '每盒10片', 12)])
        result, items = self.parse(status='partial')
        self.values(items, {6: '甲乙', 12: '3mg 每盒10片'})
        self.assertEqual(result['unassigned_regions'][0]['text'], '无法确定的跨栏文字')
        self.persisted({6: '甲乙', 12: '3mg 每盒10片'}, status='partial')

    def test_earlier_wide_body_is_not_the_later_column_boundary(self):
        text = '甲乙丙丁戊己庚辛壬癸甲乙丙丁戊己庚辛壬癸甲乙丙丁戊己庚辛壬癸'
        self.make([(30, 60, '药品通用名称：', None), (180, 95, text, 6),
                   (490, 140, '规格：3mg', 12), (200, 180, '缓释片', 6),
                   (620, 215, '每盒10片', 12)])
        _, items = self.parse()
        self.values(items, {6: text+'缓释片', 12: '3mg 每盒10片'})

    def empty_neighbor(self, empty, swapped):
        drug, spec = (490, 30) if swapped else (30, 490)
        self.make([(drug, 60, '药品通用名称：', None), (spec, 80, '规格：', None)] +
                  ([(drug+160, 130, '甲乙缓释片', 6)] if empty == 12 else
                   [(spec+160, 130, '3mg', 12), (spec+160, 175, '每盒10片', 12)]))
        _, items = self.parse()
        self.values(items, {6: '甲乙缓释片' if empty == 12 else '',
                            12: '3mg 每盒10片' if empty == 6 else ''})


def case_test(*args):
    def test(self):
        self.layout(*args)
    return test


for mode in ('A', 'B'):
    for width, right, font in [(720, 350, 9), (900, 490, 12), (1250, 620, 16)]:
        for indent in (font*10, font*14):
            for swapped in (False, True):
                for left_late in (False, True):
                    for framed in (False, True):
                        name = f'test_layout_{mode}_w{width}_font{font}_indent{indent}_swap{int(swapped)}_late{int(left_late)}_frame{int(framed)}'
                        setattr(ReauditF05Tests, name, case_test(mode, width, right, font, indent, swapped, left_late, framed))

for empty in (6, 12):
    for swapped in (False, True):
        def empty_test(self, empty=empty, swapped=swapped):
            self.empty_neighbor(empty, swapped)
        setattr(ReauditF05Tests, f'test_delayed_empty_{empty}_swap{int(swapped)}', empty_test)


if __name__ == '__main__':
    unittest.main()
