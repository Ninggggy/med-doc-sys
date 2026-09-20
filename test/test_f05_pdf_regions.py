"""F05：以真实 PDF 中独立写定的内容及位置检验并列字段归属。"""
import tempfile
import unittest
from pathlib import Path

import fitz

from agent.agent_backend.utils.parser.drug_supplement_pdf_parser import parse_drug_supplement_pdf
from agent.agent_backend.services.filing_change_form_parser_service import FilingChangeFormParserService


class PDFFieldRegionTests(unittest.TestCase):
    def test_parallel_subfields_accept_existing_value_parser_delimiters(self):
        self.make_pdf([(40,40,'30. 药品注册申请人：'),
                       (40,90,'电子信箱,'),(430,90,'手机;'),
                       (40,120,'alpha@example.invalid'),(430,120,'123456')])
        item=self.parse()[30]
        self.assertEqual(item['subfields']['电子信箱'],'alpha@example.invalid')
        self.assertEqual(item['subfields']['手机'],'123456')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='f05-regions-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'form.pdf'

    def make_pdf(self, entries, width=900, cells=()):
        with fitz.open() as doc:
            page = doc.new_page(width=width, height=600)
            for box in cells:
                page.draw_rect(fitz.Rect(box))
            for x, y, text in entries:
                page.insert_text((x, y), text, fontname='china-s', fontsize=12)
            doc.save(self.path)

    def parse(self, dpi=180):
        result = parse_drug_supplement_pdf(self.path, dpi=dpi)
        self.assertEqual(result['parse_diagnostics']['ocr_calls'], 0)
        self.assertEqual(result['parse_diagnostics']['status'], 'success')
        return {i['item_no']: i for i in result['items']}

    def assert_regions(self, item, own, other, dpi=180):
        regions = item['source_regions']
        self.assertTrue(regions)
        self.assertEqual(item['source_pages'], [1])
        with fitz.open(self.path) as doc:
            page = doc[0]
            for text in own:
                # 与PDF实际字符匹配，不让MuPDF自动补空格破坏独立定位依据。
                boxes = page.search_for(text, flags=fitz.TEXTFLAGS_SEARCH | fitz.TEXT_INHIBIT_SPACES)
                self.assertTrue(boxes, text)
                for box in boxes:
                    self.assertTrue(any((fitz.Rect(r['bbox_pdf']) + (-.02, -.02, .02, .02)).contains(box) for r in regions), text)
            for text in other:
                for box in page.search_for(text, flags=fitz.TEXTFLAGS_SEARCH | fitz.TEXT_INHIBIT_SPACES):
                    self.assertFalse(any(fitz.Rect(r['bbox_pdf']).contains((box.tl + box.br) / 2) for r in regions), text)
        for region in regions:
            self.assertEqual(region['coordinate_unit'], 'pdf_point')
            for px, point in zip(region['bbox_px'], region['bbox_pdf']):
                self.assertAlmostEqual(px * 72 / dpi, point, places=2)

    def test_original_audit_input_and_form_entry(self):
        # 与 audit_pdf_misc.py 的 F05 输入逐字、逐位置一致。
        self.make_pdf([(30, 60, '药品通用名称：'), (180, 60, '盐酸测试'),
                       (180, 85, '缓释片'), (500, 60, '规格：'), (620, 60, '1%')])
        items = self.parse()
        self.assertEqual(items[6]['normalized_text'], '盐酸测试缓释片')
        self.assertEqual(items[12]['normalized_text'], '1%')
        self.assert_regions(items[6], ['盐酸测试', '缓释片'], ['规格', '1%'])
        self.assert_regions(items[12], ['1%'], ['盐酸测试', '缓释片'])
        form = FilingChangeFormParserService().parse_form_file(str(self.path))['form_json']
        self.assertEqual(form['item_6_generic_name']['value'], '盐酸测试缓释片')
        self.assertEqual(form['item_12_specification']['value'], '1%')

    def test_swapped_variable_columns_multiline_and_dpi(self):
        for width, right in [(630, 280), (950, 690), (1200, 510)]:
            for swapped in (False, True):
                for dpi in (72, 216):
                    with self.subTest(width=width, right=right, swapped=swapped, dpi=dpi):
                        drug, spec = (right, 28) if swapped else (28, right)
                        self.make_pdf([(drug, 60, '药品通用名称：盐酸甲'),
                                       (drug, 83, '乙丙'), (drug, 107, '缓释片'),
                                       (spec, 60, '规格：2mg'), (spec, 83, '每瓶20片')], width)
                        items = self.parse(dpi)
                        self.assertEqual(items[6]['normalized_text'], '盐酸甲乙丙缓释片')
                        self.assertEqual(items[12]['normalized_text'], '2mg 每瓶20片')
                        self.assert_regions(items[6], ['盐酸甲', '乙丙', '缓释片'], ['2mg', '每瓶20片'], dpi)

    def test_empty_neighbor_in_both_directions(self):
        for drug_x, spec_x in [(25, 470), (470, 25)]:
            with self.subTest(drug_x=drug_x):
                self.make_pdf([(drug_x, 60, '药品通用名称：甲乙'), (drug_x, 85, '片'),
                               (spec_x, 60, '规格：')])
                items = self.parse()
                self.assertEqual(items[6]['normalized_text'], '甲乙片')
                self.assertEqual(items[12]['normalized_text'], '')
                self.assert_regions(items[12], ['规格：'], ['甲乙', '片'])

    def test_multiline_parallel_subfields(self):
        self.make_pdf([(30, 50, '药品注册申请人：'), (490, 50, '生产企业：'),
                       (30, 80, '中文名称：甲公司'), (490, 80, '中文名称：乙公司'),
                       (30, 105, '注册地址：甲市'), (490, 105, '生产地址：乙市'),
                       (30, 130, '甲区甲路'), (490, 130, '乙区乙路'),
                       (30, 155, '三号'), (490, 155, '四号'),
                       (30, 180, '英文名称：Alpha Ltd'), (490, 180, '英文名称：Beta Ltd')])
        items = self.parse()
        self.assertEqual(items[30]['subfields']['注册地址'], '甲市甲区甲路三号')
        self.assertEqual(items[31]['subfields']['生产地址'], '乙市乙区乙路四号')
        self.assertEqual(items[30]['subfields']['英文名称'], 'Alpha Ltd')
        self.assertEqual(items[31]['subfields']['英文名称'], 'Beta Ltd')
        self.assertEqual(items[7]['normalized_text'], '')
        self.assert_regions(items[30], ['甲公司', '甲区甲路'], ['乙公司', '乙区乙路'])

    def test_parallel_subfields_inside_one_item(self):
        self.make_pdf([(30, 50, '药品注册申请人：'),
                       (30, 80, '注册地址：甲市'), (460, 80, '通讯地址：乙市'),
                       (30, 105, '甲区甲路'), (460, 105, '乙区乙路'),
                       (30, 130, '三号'), (460, 130, '四号')])
        item = self.parse()[30]
        self.assertEqual(item['subfields']['注册地址'], '甲市甲区甲路三号')
        self.assertEqual(item['subfields']['通讯地址'], '乙市乙区乙路四号')

    def test_cells_override_centered_title_positions(self):
        self.make_pdf([(45, 65, '药品通用名称：甲乙'), (48, 90, '缓释片'),
                       (590, 65, '规格：'), (440, 90, '3mg'), (440, 115, '每盒10片')],
                      cells=[(20, 40, 410, 150), (410, 40, 850, 150),
                             (20, 150, 410, 185), (410, 150, 850, 185)])
        items = self.parse()
        self.assertEqual(items[6]['normalized_text'], '甲乙缓释片')
        self.assertEqual(items[12]['normalized_text'], '3mg 每盒10片')
        self.assert_regions(items[6], ['甲乙', '缓释片'], ['3mg', '每盒10片'])

    def test_each_column_advances_independently(self):
        self.make_pdf([(30, 60, '药品通用名称：甲乙'), (490, 60, '规格：3mg'),
                       (30, 85, '缓释片'), (490, 85, '每瓶20片'),
                       (30, 110, '英文名称：Example'), (490, 110, '每箱10瓶'),
                       (30, 135, 'Tablets')])
        items = self.parse()
        self.assertEqual(items[6]['normalized_text'], '甲乙缓释片')
        self.assertEqual(items[7]['normalized_text'], 'Example Tablets')
        self.assertEqual(items[12]['normalized_text'], '3mg 每瓶20片每箱10瓶')
        self.assert_regions(items[12], ['每箱10瓶'], ['Example', 'Tablets'])

    def test_three_columns_and_section_boundary(self):
        self.make_pdf([(30, 60, '药品通用名称：甲乙'), (340, 60, '规格：3mg'),
                       (650, 60, '商品名称：丙丁'), (30, 85, '缓释片'),
                       (340, 85, '每盒10片'), (650, 85, '品牌'),
                       (30, 130, '补充内容'), (30, 160, '提出补充申请的理由：工艺优化')])
        items = self.parse()
        self.assertEqual(items[6]['normalized_text'], '甲乙缓释片')
        self.assertEqual(items[10]['normalized_text'], '丙丁品牌')
        self.assertEqual(items[12]['normalized_text'], '3mg 每盒10片')
        self.assertEqual(items[22]['normalized_text'], '工艺优化')

    def test_repeated_item_regions_do_not_enclose_neighbor(self):
        self.make_pdf([(30, 60, '药品通用名称：甲乙'), (490, 60, '规格：3mg'),
                       (30, 85, '缓释片'), (490, 85, '每盒10片'),
                       (30, 140, '规格：4mg'), (490, 140, '药品通用名称：丙丁'),
                       (30, 165, '每瓶20片'), (490, 165, '胶囊')])
        items = self.parse()
        self.assertEqual(items[6]['normalized_text'], '甲乙缓释片丙丁胶囊')
        self.assertEqual(len(items[6]['source_regions']), 2)
        self.assert_regions(items[6], ['甲乙', '缓释片', '丙丁', '胶囊'], ['3mg', '4mg', '每盒10片', '每瓶20片'])

    def test_value_only_fragments_follow_real_cells(self):
        # 两组选项各自占实际单元格；前置/后置标记均不得带上字段标签。
        for first, second in [('√片剂', '○胶囊剂'), ('片剂√', '胶囊剂○')]:
            with self.subTest(first=first):
                self.make_pdf([(35, 65, '剂型：'), (230, 65, first), (550, 65, second)],
                              cells=[(20, 40, 200, 100), (200, 40, 500, 100), (500, 40, 850, 100),
                                     (20, 100, 200, 150), (200, 100, 500, 150), (500, 100, 850, 150)])
                item = self.parse()[11]
                self.assertEqual(item['option_fragments'], [first, second])
                self.assertNotIn('剂型', item['raw_text'])

    def test_raw_body_retains_original_punctuation(self):
        self.make_pdf([(30, 60, '专利情况：本申请声明：构成他人专利侵权。'),
                       (30, 100, '本次申请为：非首次申请。')])
        items = self.parse()
        self.assertEqual(items[24]['raw_text'], '本申请声明：构成他人专利侵权。')
        self.assertEqual(items[28]['raw_text'], '非首次申请。')
        self.assertEqual(items[24]['option_fragments'], ['本申请声明：构成他人专利侵权。'])

    def test_merged_parent_heading_keeps_child_rows(self):
        self.make_pdf([(35, 60, '药品注册申请人：'),
                       (35, 95, '注册地址：甲市'), (445, 95, '通讯地址：乙市'),
                       (35, 120, '甲区甲路'), (445, 120, '乙区乙路'),
                       (35, 175, '中文名称：甲公司')],
                      cells=[(20, 40, 850, 75), (20, 75, 430, 150), (430, 75, 850, 150),
                             (20, 150, 850, 190)])
        item = self.parse()[30]
        self.assertEqual(item['subfields']['注册地址'], '甲市甲区甲路')
        self.assertEqual(item['subfields']['通讯地址'], '乙市乙区乙路')
        self.assertEqual(item['subfields']['中文名称'], '甲公司')

    def test_parallel_fields_inside_shared_merged_cell(self):
        self.make_pdf([(35, 65, '药品通用名称：甲乙'), (460, 65, '规格：3mg'),
                       (35, 90, '缓释片'), (460, 90, '每盒10片')],
                      cells=[(20, 40, 850, 120), (20, 120, 430, 155), (430, 120, 850, 155),
                             (20, 155, 430, 190), (430, 155, 850, 190)])
        items = self.parse()
        self.assertEqual(items[6]['normalized_text'], '甲乙缓释片')
        self.assertEqual(items[12]['normalized_text'], '3mg 每盒10片')
        self.assert_regions(items[6], ['甲乙', '缓释片'], ['3mg', '每盒10片'])

    def test_borderless_wrapped_option_keeps_its_column(self):
        from agent.test.test_four_semantic_table_defects import pdf_file
        for left, right in [('☑6.8 变更有效期和\n贮藏条件', '□6.9 增加规格'),
                            ('□6.9 增加规格', '☑6.8 变更有效期和\n贮藏条件'),
                            ('6.8 变更有效期和☑\n贮藏条件', '6.9 增加规格□')]:
            with self.subTest(left=left):
                pdf_file(self.path, [['申请事项分类', left, right]])
                item = self.parse()[5]
                self.assertEqual(item['option_fragments'], [left, right])
                self.assertNotIn('申请事项分类', item['raw_text'])
                field = FilingChangeFormParserService().parse_form_file(str(self.path))['form_json']['item_5_application_matter_category']
                self.assertEqual(field['selected_values'], ['1.7'])

    def assert_unassigned(self, result, text, page_no, code):
        self.assertEqual(result['parse_diagnostics']['status'], 'partial')
        self.assertEqual(result['parse_diagnostics']['ocr_calls'], 0)
        issue = next(i for i in result['unassigned_regions'] if i['text'] == text)
        self.assertEqual(issue['code'], code)
        self.assertEqual(issue['page'], page_no)
        self.assertTrue(issue['needs_review'])
        self.assertIn(issue, result['pages'][page_no-1]['errors'])
        self.assertEqual(result['pages'][page_no-1]['status'], 'partial')
        self.assertTrue(any('核对' in warning for warning in result['warnings']))
        with fitz.open(self.path) as doc:
            box = doc[page_no-1].search_for(text)[0]
            self.assertTrue((fitz.Rect(issue['bbox_pdf']) + (-.02, -.02, .02, .02)).contains(box))
        for item in result['items']:
            self.assertNotIn(text, item['raw_text'])
            self.assertFalse(any(text in fragment for fragment in item['option_fragments']))

    def test_crossing_column_boundary_is_unassigned(self):
        self.make_pdf([(30, 60, '药品通用名称：甲乙片'), (300, 60, '规格：3mg'),
                       (260, 90, '跨界文字需要核对')])
        result = parse_drug_supplement_pdf(self.path)
        self.assert_unassigned(result, '跨界文字需要核对', 1, 'field_region_crossing')
        self.assertEqual(result['items'][5]['normalized_text'], '甲乙片')
        self.assertEqual(result['items'][11]['normalized_text'], '3mg')
        self.assertEqual(result['unassigned_regions'][0]['candidate_items'], [6, 12])
        parsed = FilingChangeFormParserService().parse_form_file(str(self.path))
        self.assertEqual(parsed['content_status'], 'partial')
        self.assertEqual(parsed['form_json']['item_6_generic_name']['value'], '甲乙片')
        self.assertEqual(parsed['form_json']['item_12_specification']['value'], '3mg')
        self.assertEqual(parsed['pdf_parse_result']['unassigned_regions'][0]['text'], '跨界文字需要核对')

    def test_parallel_columns_require_headings_again_on_next_page(self):
        with fitz.open() as doc:
            page = doc.new_page(width=900, height=600)
            page.insert_text((30, 60), '药品通用名称：甲乙片', fontname='china-s', fontsize=12)
            page.insert_text((480, 60), '规格：3mg', fontname='china-s', fontsize=12)
            page = doc.new_page(width=900, height=600)
            page.insert_text((30, 60), '次页无标题续文', fontname='china-s', fontsize=12)
            page.insert_text((480, 60), '另一列续文', fontname='china-s', fontsize=12)
            doc.save(self.path)
        result = parse_drug_supplement_pdf(self.path)
        self.assert_unassigned(result, '次页无标题续文', 2, 'field_region_unassigned')
        self.assert_unassigned(result, '另一列续文', 2, 'field_region_unassigned')
        self.assertEqual(result['items'][5]['source_pages'], [1])
        self.assertEqual(result['items'][11]['source_pages'], [1])

    def test_single_column_can_continue_on_next_page(self):
        with fitz.open() as doc:
            for text in ['药品通用名称：甲乙', '缓释片']:
                doc.new_page().insert_text((30, 60), text, fontname='china-s', fontsize=12)
            doc.save(self.path)
        result = parse_drug_supplement_pdf(self.path)
        self.assertEqual(result['items'][5]['normalized_text'], '甲乙缓释片')
        self.assertEqual(result['items'][5]['source_pages'], [1, 2])
        self.assertEqual(result['unassigned_regions'], [])
        self.assertEqual(result['parse_diagnostics']['status'], 'success')


if __name__ == '__main__':
    unittest.main()
