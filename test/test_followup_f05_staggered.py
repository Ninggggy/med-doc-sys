"""F05 后续：真实合成 PDF、独立预期与坐标；所有新证据写入 /tmp。"""
import json
import tempfile
import unittest
from pathlib import Path

import fitz

from agent.agent_backend.utils.parser.drug_supplement_pdf_parser import parse_drug_supplement_pdf
from agent.agent_backend.services.filing_change_form_parser_service import FilingChangeFormParserService


class StaggeredFieldTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = Path('/tmp/followup-20260906/f05')
        root.mkdir(parents=True, exist_ok=True)
        cls.evidence = Path(tempfile.mkdtemp(prefix='cases-', dir=root))
        print(f'F05 case evidence: {cls.evidence}', flush=True)

    def make(self, entries, *, width=900, cells=(), pages=None):
        self.path = self.evidence / (self._testMethodName + '.pdf')
        with fitz.open() as doc:
            for page_entries in pages or [entries]:
                page = doc.new_page(width=width, height=600)
                for box in cells:
                    page.draw_rect(fitz.Rect(box))
                for x, y, text in page_entries:
                    page.insert_text((x, y), text, fontname='china-s', fontsize=12)
            doc.save(self.path)

    def parse(self, dpi=180, *, status='success'):
        result = parse_drug_supplement_pdf(self.path, dpi=dpi)
        self.path.with_suffix('.json').write_text(json.dumps({
            'diagnostics': result['parse_diagnostics'],
            'unassigned_regions': result['unassigned_regions'],
            'items': [i for i in result['items'] if i['raw_text'] or i['source_regions']],
        }, ensure_ascii=False, indent=2), encoding='utf-8')
        self.assertEqual(result['parse_diagnostics']['ocr_calls'], 0)
        self.assertEqual(result['parse_diagnostics']['status'], status)
        if status == 'success':
            self.assertEqual(result['unassigned_regions'], [])
        return result, {i['item_no']: i for i in result['items']}

    def check(self, item, expected, own, other):
        # 数值与中文间空格属于提取器排版；正文字符及其归属必须精确。
        self.assertEqual(''.join(item['normalized_text'].split()), expected)
        with fitz.open(self.path) as doc:
            for text in own:
                found = False
                for page_no, page in enumerate(doc, 1):
                    for box in page.search_for(text, flags=fitz.TEXTFLAGS_SEARCH | fitz.TEXT_INHIBIT_SPACES):
                        found = True
                        self.assertTrue(any(
                            r['page'] == page_no and
                            (fitz.Rect(r['bbox_pdf']) + (-.02, -.02, .02, .02)).contains(box)
                            for r in item['source_regions']), text)
                self.assertTrue(found, text)
            for text in other:
                self.assertNotIn(text, item['raw_text'])
                for page_no, page in enumerate(doc, 1):
                    for box in page.search_for(text, flags=fitz.TEXTFLAGS_SEARCH | fitz.TEXT_INHIBIT_SPACES):
                        self.assertFalse(any(
                            r['page'] == page_no and fitz.Rect(r['bbox_pdf']).contains((box.tl + box.br) / 2)
                            for r in item['source_regions']), text)
        for region in item['source_regions']:
            self.assertEqual(region['coordinate_unit'], 'pdf_point')
            for px, pt in zip(region['bbox_px'], region['bbox_pdf']):
                self.assertAlmostEqual(px * 72 / region['dpi'], pt, places=2)

    def original(self, framed):
        cells = [(20, 40, 430, 150), (430, 40, 850, 150),
                 (20, 150, 430, 190), (430, 150, 850, 190)] if framed else []
        self.make([(30, 60, '药品通用名称：甲乙'), (30, 110, '缓释片'),
                   (490, 80, '规格：3mg'), (490, 120, '每盒10片')], cells=cells)
        with fitz.open(self.path) as doc:
            doc[0].get_pixmap().save(self.path.with_suffix('.png'))
        _, items = self.parse()
        self.check(items[6], '甲乙缓释片', ['甲乙', '缓释片'], ['3mg', '每盒10片'])
        self.check(items[12], '3mg每盒10片', ['3mg', '每盒10片'], ['甲乙', '缓释片'])
        form = FilingChangeFormParserService().parse_form_file(str(self.path))
        self.assertEqual(form['content_status'], 'success')
        self.assertEqual(form['form_json']['item_6_generic_name']['value'], '甲乙缓释片')
        self.assertEqual(form['form_json']['item_12_specification']['value'], '3mg 每盒10片')

    def test_original_borderless(self):
        self.original(False)

    def test_original_framed(self):
        self.original(True)

    def layout(self, width, right, offset, swapped, delayed_left, framed, empty=None):
        drug, spec = (right, 28) if swapped else (28, right)
        left_y, right_y = (60 + offset, 60) if delayed_left else (60, 60 + offset)
        drug_y, spec_y = (right_y, left_y) if swapped else (left_y, right_y)
        entries = [(drug, drug_y, '药品通用名称：' + ('' if empty == 6 else '甲乙')),
                   (spec, spec_y, '规格：' + ('' if empty == 12 else '3mg'))]
        if empty != 6:
            entries += [(drug, 100 + offset, '缓释片'), (drug, 150 + offset, '薄膜衣')]
        if empty != 12:
            entries += [(spec, 112 + offset, '每盒10片'), (spec, 175 + offset, '避光保存')]
        cells = [(18, 35, right-20, 245), (right-20, 35, width-18, 245),
                 (18, 245, right-20, 280), (right-20, 245, width-18, 280)] if framed else []
        self.make(entries, width=width, cells=cells)
        _, items = self.parse(dpi=72 if offset == 8 else 216)
        drug_texts = ['甲乙', '缓释片', '薄膜衣'] if empty != 6 else []
        spec_texts = ['3mg', '每盒10片', '避光保存'] if empty != 12 else []
        self.check(items[6], ''.join(drug_texts), drug_texts or ['药品通用名称'], spec_texts)
        self.check(items[12], ''.join(spec_texts), spec_texts or ['规格'], drug_texts)

    def test_each_staggered_column_advances(self):
        self.make([(30, 60, '药品通用名称：甲乙'), (490, 80, '规格：3mg'),
                   (30, 110, '缓释片'), (490, 120, '每盒10片'),
                   (30, 150, '英文名称：Example'), (490, 165, '避光保存'),
                   (30, 190, 'Tablets')])
        _, items = self.parse()
        self.check(items[6], '甲乙缓释片', ['缓释片'], ['Example', '每盒10片'])
        self.check(items[7], 'ExampleTablets', ['Example', 'Tablets'], ['避光保存'])
        self.check(items[12], '3mg每盒10片避光保存', ['避光保存'], ['Example', 'Tablets'])

    def test_staggered_subfields(self):
        self.make([(30, 40, '药品注册申请人：'), (30, 75, '注册地址：甲市'),
                   (460, 95, '通讯地址：乙市'), (30, 130, '甲区甲路'),
                   (460, 145, '乙区乙路'), (30, 175, '三号'), (460, 190, '四号')])
        _, items = self.parse()
        self.assertEqual(items[30]['subfields']['注册地址'], '甲市甲区甲路三号')
        self.assertEqual(items[30]['subfields']['通讯地址'], '乙市乙区乙路四号')

    def test_staggered_parent_subfields(self):
        self.make([(30, 40, '药品注册申请人：'), (490, 60, '生产企业：'),
                   (30, 95, '中文名称：甲公司'), (490, 115, '中文名称：乙公司'),
                   (30, 150, '注册地址：甲市'), (490, 170, '生产地址：乙市'),
                   (30, 210, '甲路'), (490, 230, '乙路'),
                   (30, 270, '英文名称：Alpha Ltd'), (490, 290, '英文名称：Beta Ltd')])
        _, items = self.parse()
        self.assertEqual(items[30]['subfields']['注册地址'], '甲市甲路')
        self.assertEqual(items[31]['subfields']['生产地址'], '乙市乙路')
        self.assertEqual(items[30]['subfields']['英文名称'], 'Alpha Ltd')
        self.assertEqual(items[31]['subfields']['英文名称'], 'Beta Ltd')
        self.assertEqual(items[7]['normalized_text'], '')

    def test_single_column_indented_next_field(self):
        self.make([(30, 60, '药品通用名称：甲乙'), (30, 100, '缓释片'),
                   (490, 150, '规格：3mg'), (490, 185, '每盒10片'),
                   (30, 230, '商品名称：丙丁'), (30, 260, '品牌')])
        _, items = self.parse()
        self.check(items[6], '甲乙缓释片', ['缓释片'], ['3mg', '品牌'])
        self.check(items[12], '3mg每盒10片', ['每盒10片'], ['品牌'])
        self.check(items[10], '丙丁品牌', ['丙丁', '品牌'], ['每盒10片'])

    def test_full_width_heading_then_cross_page(self):
        self.make([], pages=[[
            (30, 60, '药品通用名称：甲乙'), (320, 80, '规格：3mg'),
            (30, 110, '缓释片'), (320, 125, '每盒10片'),
            (30, 180, '提出补充申请的理由：调整生产工艺并完善药品质量控制方法'),
            (30, 215, '通栏说明')], [(30, 60, '跨页补充说明')]])
        _, items = self.parse()
        self.assertEqual(items[22]['source_pages'], [1, 2])
        self.check(items[22], '调整生产工艺并完善药品质量控制方法通栏说明跨页补充说明',
                   ['通栏说明', '跨页补充说明'], ['甲乙', '3mg'])

    def test_single_parent_subfield_cross_page(self):
        self.make([], pages=[[(30, 60, '药品注册申请人：'), (30, 95, '注册地址：甲市')],
                            [(30, 60, '甲区甲路'), (30, 95, '三号')]])
        _, items = self.parse()
        self.assertEqual(items[30]['source_pages'], [1, 2])
        self.assertEqual(items[30]['subfields']['注册地址'], '甲市甲区甲路三号')

    def test_staggered_crossing_keeps_diagnostic(self):
        self.make([(30, 60, '药品通用名称：甲乙'), (300, 80, '规格：3mg'),
                   (30, 110, '缓释片'), (300, 125, '每盒10片'),
                   (260, 165, '跨界文字需要核对')])
        result, items = self.parse(status='partial')
        self.check(items[6], '甲乙缓释片', ['缓释片'], ['跨界文字需要核对'])
        self.check(items[12], '3mg每盒10片', ['每盒10片'], ['跨界文字需要核对'])
        issue = next(i for i in result['unassigned_regions'] if i['text'] == '跨界文字需要核对')
        self.assertEqual(issue['code'], 'field_region_crossing')
        self.assertEqual(issue['candidate_items'], [6, 12])
        self.assertTrue(issue['needs_review'])
        self.assertIn(issue, result['pages'][0]['errors'])
        with fitz.open(self.path) as doc:
            self.assertTrue((fitz.Rect(issue['bbox_pdf']) + (-.02, -.02, .02, .02)).contains(
                doc[0].search_for('跨界文字需要核对')[0]))


def layout_test(*args, **kwargs):
    def test(self):
        self.layout(*args, **kwargs)
    return test


# 每种布局单独计数，失败不会中断同一大循环中的其他组合。
for width, right in [(630, 280), (950, 690), (1200, 510)]:
    for offset in (8, 20, 55):
        for swapped in (False, True):
            for delayed_left in (False, True):
                for framed in (False, True):
                    name = f'test_matrix_w{width}_dy{offset}_swap{int(swapped)}_leftlate{int(delayed_left)}_frame{int(framed)}'
                    setattr(StaggeredFieldTests, name, layout_test(width, right, offset, swapped, delayed_left, framed))

for empty in (6, 12):
    for swapped in (False, True):
        for delayed_left in (False, True):
            for framed in (False, True):
                name = f'test_empty_{empty}_swap{int(swapped)}_leftlate{int(delayed_left)}_frame{int(framed)}'
                setattr(StaggeredFieldTests, name, layout_test(900, 490, 20, swapped, delayed_left, framed, empty=empty))


if __name__ == '__main__':
    unittest.main()
