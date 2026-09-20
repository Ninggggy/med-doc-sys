"""人工修订的已有页可重新映射字段，不能再次OCR或改原始页。"""
import unittest
from copy import deepcopy
from unittest.mock import patch
from agent.agent_backend.utils.parser.drug_supplement_pdf_parser import build_pdf_form_from_pages
from agent.agent_backend.services.filing_change_form_parser_service import FilingChangeFormParserService


class FormReextractTests(unittest.TestCase):
    def test_unlabelled_data_table_with_known_parent_reaches_field_source(self):
        from agent.agent_backend.services.filing_parse_resolution import manual_table
        for number,title,key in ((16,'处方','item_16_prescription'),(21,'补充申请的内容','item_21_change_content')):
            with self.subTest(item=number):
                table=manual_table({'row_count':2,'column_count':2,'cells':[
                    {'row':0,'column':0,'text':'项目'},{'row':0,'column':1,'text':'数值'},
                    {'row':1,'column':0,'text':'合成甲'},{'row':1,'column':1,'text':'3 mg'}]},
                    [10,50,290,120],1,'按原页逐格核对')
                word={'text':f'{number}. {title}：','bbox':[10,10,180,30],'source':'native'}
                pages=[{'page':1,'page_bbox':[0,0,300,400],'status':'partial','words':[word],
                    'lines':[word],'text':word['text'],'raw_text':word['text'],'tables':[table],'errors':[]}]
                original=deepcopy(pages)
                result=build_pdf_form_from_pages(pages)
                item=next(i for i in result['items'] if i['item_no']==number)
                self.assertEqual(result['tables'][0]['nearest_item_no'],number)
                self.assertIn(table['markdown'],item['raw_text'])
                field=FilingChangeFormParserService().form_from_pdf_result(result)['form_json'][key]
                self.assertIn('3 mg',field['source_text'])
                if number==21:self.assertIn('3 mg',field['value'])
                self.assertTrue(any(region.get('bbox_pdf')==table['bbox_pdf']
                                    and region.get('source')=='manual_revision' for region in field['source_regions']))
                self.assertEqual(pages,original)
                self.assertEqual(build_pdf_form_from_pages(pages),result)
                # 没有可靠父标题时不能凭数据值创造字段归属。
                detached=deepcopy(pages);detached[0].update(words=[],lines=[],text='',raw_text='')
                unassigned=build_pdf_form_from_pages(detached)
                self.assertIsNone(unassigned['tables'][0]['nearest_item_no'])
                self.assertTrue(all('3 mg' not in i['raw_text'] for i in unassigned['items']))

    def test_manual_last_page_does_not_assign_review_footer_to_research_party(self):
        from agent.agent_backend.services.filing_parse_resolution import apply_resolutions
        from agent.agent_backend.services.filing_parse_readiness import source_issues
        text = ('生产企业：\n中文名称：合成生产企业\n英文名称：\n'
                '统一社会信用代码/组织机构代码：SYNTHETIC-PRODUCER\n'
                '31. 委托研究机构：\n审查机关\n经审查，本表填写符合形式审查要求。\n'
                '审查机关：\n审查人签名：\n日期：年 月 日\n'
                '申请表已适配浏览器，不需要调整兼容。\n打印预览 打印 自查表查看')
        word = {'text': '旧识别', 'bbox': [10, 10, 200, 80], 'source': 'ocr'}
        chunks = [{'page': 4, 'page_bbox': [0, 0, 300, 400], 'status': 'partial',
                   'words': [word], 'lines': [word], 'text': word['text'], 'raw_text': word['text'],
                   'tables': [], 'errors': [{'code': 'ocr_quality', 'bbox_pdf': [0, 0, 300, 400]}]}]
        original = deepcopy(chunks)
        issues = source_issues({'content_status': 'partial'}, 'application_form', chunks)
        effective, _, _ = apply_resolutions(chunks, issues, [{
            'issue_key': issues[0]['issue_key'], 'action': 'correct_text',
            'reason': '核对末页主体与审查机关及打印说明边界', 'text': text}])
        parsed = build_pdf_form_from_pages(effective)
        research = next(i for i in parsed['items'] if i['item_no'] == 32)
        producer = next(i for i in parsed['items'] if i['item_no'] == 31)
        self.assertEqual(research['raw_text'], '')
        self.assertIn('SYNTHETIC-PRODUCER', producer['raw_text'])
        self.assertEqual(effective[0]['raw_text'], text)
        self.assertEqual(chunks, original)
        # 结束主体区域不等于截断余下文件；后续明确字段仍正常提取。
        extended = deepcopy(effective)
        extended_text = text + '\n33. 其他特别申明事项：合成后续声明'
        extended[0]['raw_text'] = extended_text
        extended[0]['text'] = extended_text
        for line in extended[0]['lines']:
            line['text'] = extended_text
        for word in extended[0]['words']:
            word['text'] = extended_text
        following = build_pdf_form_from_pages(extended)
        self.assertEqual(next(i for i in following['items'] if i['item_no'] == 33)['raw_text'], '合成后续声明')

    def test_manual_party_subfield_label_does_not_start_drug_english_field(self):
        from agent.agent_backend.utils.parser.drug_supplement_pdf_parser import _page_form_lines
        text = ('30. 药品注册申请人：\n中文名称：合成申请人\n英文名称：\n'
                '统一社会信用代码/组织机构代码：SYNTHETIC-CODE\n法定代表人：合成人 职位：负责人\n'
                '联系人：合成联系人\n手机：10000000000\n31. 制剂生产企业：\n中文名称：另一合成企业')
        page = {'page': 3, 'page_bbox': [0, 0, 300, 400], 'status': 'partial', 'errors': [],
                'words': [], 'tables': [], 'raw_text': text,
                'lines': [{'text': text, 'bbox': [0,0,300,300], 'source': 'manual_revision',
                           'position_kind': 'review_region_not_glyph'}]}
        lines = _page_form_lines(page, 180)
        self.assertEqual([l.manual_target_item_no for l in lines], [30, 31])
        fields = FilingChangeFormParserService().form_from_pdf_result(build_pdf_form_from_pages([page]))['form_json']
        party = fields['item_30_applicant_info']['sub_fields']
        self.assertEqual(party['credit_or_org_code'], 'SYNTHETIC-CODE')
        self.assertEqual(party['legal_representative'], '合成人')
        self.assertEqual(party['contact'], '合成联系人')
        self.assertEqual(party['mobile'], '10000000000')
        self.assertEqual(fields['item_7_english_or_latin_name']['value'], '')

    def test_history_placeholder_does_not_delete_unusual_identifiers_or_text(self):
        service = FilingChangeFormParserService()
        header = '| 受理号 | 批件号 | 批准内容 | 备注 |\n| --- | --- | --- | --- |'
        self.assertTrue(service._looks_like_history_placeholder(header))
        self.assertTrue(service._looks_like_history_placeholder('受理号 批件号 批准内容 备注'))
        for row in ('| SYNTHETIC-29 | / | 合成批准内容 | / |', '| / | / | 不适用 | / |',
                    '另附核对说明', '受理号待确认'):
            with self.subTest(row=row):
                self.assertFalse(service._looks_like_history_placeholder(header + '\n' + row))
        self.assertFalse(service._looks_like_history_placeholder('受理号 批件号 批准内容 备注\n尚未取得批件'))

    def test_history_table_manual_revision_is_not_only_a_preview(self):
        from agent.agent_backend.services.filing_parse_resolution import apply_resolutions
        from agent.agent_backend.services.filing_parse_readiness import source_issues
        word = {'text': '29. 历次申请情况：', 'bbox': [10, 10, 180, 30], 'source': 'native'}
        chunks = [{'page': 3, 'page_bbox': [0, 0, 300, 400], 'status': 'partial', 'words': [word],
                   'lines': [word], 'text': word['text'], 'raw_text': word['text'],
                   'tables': [{'id': 'h', 'page': 3, 'bbox_pdf': [10, 50, 290, 120], 'markdown': '|旧表|', 'cells': []}],
                   'errors': [{'code': 'ocr_quality', 'bbox_pdf': [0, 0, 300, 200]}]}]
        rows = [['受理号', '批件号', '批准内容', '备注'], ['SYNTHETIC-29', '/', '合成批准内容', '/']]
        issues = source_issues({'content_status': 'partial'}, 'application_form', chunks)
        effective, _, _ = apply_resolutions(chunks, issues, [{
            'issue_key': issues[0]['issue_key'], 'action': 'correct_table', 'reason': '按合成原页核对申请记录表',
            'outside_text': '28. 本次申请为：首次申请\n29. 历次申请情况：\n30. 药品注册申请人：',
            'outside_text_verified': True, 'table': {'row_count': 2, 'column_count': 4, 'cells': [
                {'row': r, 'column': c, 'text': value} for r, row in enumerate(rows) for c, value in enumerate(row)]}}])
        fields = FilingChangeFormParserService().form_from_pdf_result(build_pdf_form_from_pages(effective))['form_json']
        self.assertIn('SYNTHETIC-29', fields['item_29_other_related_info']['value'])
        self.assertIn('合成批准内容', fields['item_29_other_related_info']['value'])
        self.assertNotIn('SYNTHETIC-29', fields['item_28_change_related_items']['value'])
        duplicate = deepcopy(effective)
        duplicate[0]['tables'][0]['rows'][0][3] = '受理号'
        duplicate[0]['tables'][0]['cells'][3]['text'] = '受理号'
        with self.assertRaisesRegex(ValueError, '重复表头'):
            build_pdf_form_from_pages(duplicate)
        detached = deepcopy(effective)
        for line in detached[0]['lines']:
            if line.get('position_kind') == 'review_region_not_glyph':
                line['bbox'] = [0, 0, 300, 40]
        unrelated = build_pdf_form_from_pages(detached)
        self.assertNotEqual(unrelated['tables'][0]['nearest_item_no'], 29)

    def test_manual_page_continuation_requires_explicit_field_without_invented_heading(self):
        from agent.agent_backend.services.filing_parse_resolution import apply_resolutions
        from agent.agent_backend.services.filing_parse_readiness import source_issues
        word = {'text': '旧识别', 'bbox': [10, 10, 200, 80], 'source': 'ocr'}
        chunks = [{'page': 3, 'page_bbox': [0, 0, 300, 400], 'status': 'partial', 'words': [word],
                   'lines': [word], 'text': word['text'], 'raw_text': word['text'], 'tables': [],
                   'errors': [{'code': 'ocr_quality', 'bbox_pdf': [0, 0, 300, 200]}]}]
        issues = source_issues({'content_status': 'partial'}, 'application_form', chunks)
        text = '原申请受理号：SYNTHETIC-23\n24. 专利情况：无\n28. 本次申请为：首次申请'
        item = {'issue_key': issues[0]['issue_key'], 'action': 'correct_text', 'reason': '已核对前页23项标题和本页续文',
                'text': text, 'continuation_item_no': 23}
        effective, _, records = apply_resolutions(chunks, issues, [item])
        parsed = build_pdf_form_from_pages(effective)
        self.assertEqual(next(i for i in parsed['items'] if i['item_no'] == 23)['raw_text'], '原申请受理号：SYNTHETIC-23')
        self.assertEqual(effective[0]['raw_text'], text)
        self.assertEqual(records[0]['continuation_item_no'], 23)
        replayed, _, _ = apply_resolutions(chunks, issues, records)
        self.assertEqual(effective, replayed)
        for overrides in ({'continuation_item_no': True}, {'continuation_item_no': 999},
                          {'text': '24. 专利情况：无'}, {'action': 'confirm'}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError):
                apply_resolutions(chunks, issues, [{**item, **overrides}])
        first_page = deepcopy(chunks)
        first_page[0]['page'] = 1
        with self.assertRaises(ValueError):
            apply_resolutions(first_page, issues, [item])
        external = deepcopy(issues)
        external[0]['source_kind'] = 'submission'
        with self.assertRaises(ValueError):
            apply_resolutions(chunks, external, [item])
        unassigned, _, _ = apply_resolutions(chunks, issues, [{k: v for k, v in item.items() if k != 'continuation_item_no'}])
        with self.assertRaisesRegex(ValueError, '前置文字'):
            build_pdf_form_from_pages(unassigned)
        mixed = deepcopy(chunks)
        mixed[0]['tables'] = [{'id': 'history', 'page': 3, 'bbox_pdf': [10, 100, 290, 160],
                              'markdown': '|历史表|', 'cells': []}]
        mixed_issues = source_issues({'content_status': 'partial'}, 'application_form', mixed)
        mixed_item = {**item, 'issue_key': mixed_issues[0]['issue_key'], 'action': 'correct_table',
                      'outside_text': text, 'outside_text_verified': True,
                      'table': {'row_count': 2, 'column_count': 2, 'cells': [
                          {'row': 0, 'column': 0, 'text': '受理号'}, {'row': 0, 'column': 1, 'text': '批件号'},
                          {'row': 1, 'column': 0, 'text': 'SYNTHETIC'}, {'row': 1, 'column': 1, 'text': '/'}]}}
        mixed_effective, _, _ = apply_resolutions(mixed, mixed_issues, [mixed_item])
        parsed_mixed = build_pdf_form_from_pages(mixed_effective)
        self.assertEqual(next(i for i in parsed_mixed['items'] if i['item_no'] == 23)['raw_text'], '原申请受理号：SYNTHETIC-23')
        self.assertIn(text, mixed_effective[0]['raw_text'])

    def test_mixed_material_table_revision_reaches_material_field(self):
        from agent.agent_backend.services.filing_parse_resolution import apply_resolutions
        from agent.agent_backend.services.filing_parse_readiness import source_issues
        words = [{'text': '17. 原/辅料/包材来源：', 'bbox': [10, 50, 180, 70], 'source': 'native'}]
        chunks = [{'page': 1, 'page_bbox': [0, 0, 300, 400], 'status': 'partial',
                   'words': words, 'lines': words, 'text': '旧正文', 'raw_text': '旧正文',
                   'tables': [{'id': 't', 'page': 1, 'bbox_pdf': [10, 100, 290, 160],
                               'nearest_item_no': 17, 'markdown': '|旧表|', 'cells': []}],
                   'errors': [{'code': 'ocr_quality', 'bbox_pdf': [0, 0, 300, 200]}]}]
        rows = [['原/辅料/包材名称', '登记号', '受理号', '生产企业名称'], ['合成辅料', '无', '/', '合成企业']]
        issues = source_issues({'content_status': 'partial'}, 'application_form', chunks)
        effective, _, _ = apply_resolutions(chunks, issues, [{
            'issue_key': issues[0]['issue_key'], 'action': 'correct_table', 'reason': '合成原页逐字段核对',
            'outside_text': '6. 药品通用名称：合成甲片\n17. 原/辅料/包材来源：\n18. 中药材标准：',
            'outside_text_verified': True,
            'table': {'row_count': 2, 'column_count': 4, 'cells': [
                {'row': r, 'column': c, 'text': t} for r, row in enumerate(rows) for c, t in enumerate(row)]}}])
        fields = FilingChangeFormParserService().form_from_pdf_result(build_pdf_form_from_pages(effective))['form_json']
        self.assertEqual(fields['item_17_material_source']['table_rows'], [
            {'material_name': '合成辅料', 'register_no': '无', 'accept_no': '/', 'manufacturer': '合成企业'}])
        duplicate = deepcopy(effective)
        duplicate[0]['tables'][0]['rows'][0][2] = '登记号'
        duplicate[0]['tables'][0]['cells'][2]['text'] = '登记号'
        with self.assertRaisesRegex(ValueError, '表头重复或归属不唯一'):
            build_pdf_form_from_pages(duplicate)
        detached = deepcopy(effective)
        for line in detached[0]['lines']:
            if line.get('position_kind') == 'review_region_not_glyph':
                line['bbox'] = [0, 0, 300, 80]
        unrelated = build_pdf_form_from_pages(detached)
        self.assertNotEqual(unrelated['tables'][0]['nearest_item_no'], 17)

    def test_plain_text_revision_multifield_preserves_boundaries(self):
        from agent.agent_backend.services.filing_parse_resolution import apply_resolutions
        from agent.agent_backend.services.filing_parse_readiness import source_issues
        word = {'text': '旧正文', 'bbox': [10, 10, 180, 70], 'source': 'native'}
        chunks = [{'page': 1, 'page_bbox': [0, 0, 300, 400], 'status': 'partial',
                   'words': [word], 'lines': [word], 'text': '旧正文', 'raw_text': '旧正文', 'tables': [],
                   'errors': [{'code': 'ocr_quality', 'bbox_pdf': [0, 0, 210, 100]}]}]
        original = deepcopy(chunks)
        issues = source_issues({'content_status': 'partial'}, 'application_form', chunks)
        text = '药品通用名称：合成甲片\n规格：3mg\n每盒10片'
        effective, _, _ = apply_resolutions(chunks, issues, [{
            'issue_key': issues[0]['issue_key'], 'action': 'correct_text', 'reason': '合成原页逐字段核对', 'text': text}])
        parsed = build_pdf_form_from_pages(effective)
        self.assertEqual(next(i for i in parsed['items'] if i['item_no'] == 12)['raw_text'], '3mg\n每盒10片')
        fields = FilingChangeFormParserService().form_from_pdf_result(parsed)['form_json']
        self.assertEqual(fields['item_6_generic_name']['value'], '合成甲片')
        # 现有规格展示字段统一将换行转空格，来源原文必须仍保留完整换行。
        self.assertEqual(fields['item_12_specification']['value'], '3mg 每盒10片')
        self.assertIn(text, effective[0]['raw_text'])
        self.assertEqual(chunks, original)

    def test_mixed_revision_multiple_outside_fields_keep_separate_values(self):
        from agent.agent_backend.services.filing_parse_resolution import apply_resolutions
        from agent.agent_backend.services.filing_parse_readiness import source_issues
        words = [{'text': '药品通用名称：旧名称', 'bbox': [10, 10, 180, 30], 'source': 'native'},
                 {'text': '规格：8mg', 'bbox': [10, 50, 180, 70], 'source': 'native'}]
        chunks = [{'page': 1, 'page_bbox': [0, 0, 300, 400], 'status': 'partial', 'words': words, 'lines': words,
                   'text': '旧正文', 'raw_text': '旧正文',
                   'tables': [{'id': 't', 'page': 1, 'bbox_pdf': [10, 100, 200, 160], 'markdown': '|旧表|'}],
                   'errors': [{'code': 'ocr_quality', 'bbox_pdf': [0, 0, 210, 170]}]}]
        issues = source_issues({'content_status': 'partial'}, 'application_form', chunks)
        effective, _, _ = apply_resolutions(chunks, issues, [{
            'issue_key': issues[0]['issue_key'], 'action': 'correct_table', 'reason': '原页逐字段核对',
            'outside_text': '药品通用名称：合成甲片\n规格：3mg', 'outside_text_verified': True,
            'table': {'row_count': 1, 'column_count': 2, 'cells': [
                {'row': 0, 'column': 0, 'text': '英文名称'}, {'row': 0, 'column': 1, 'text': 'Synthetic'}]}}])
        parsed = build_pdf_form_from_pages(effective)
        fields = FilingChangeFormParserService().form_from_pdf_result(parsed)['form_json']
        self.assertEqual(fields['item_6_generic_name']['value'], '合成甲片')
        self.assertEqual(fields['item_12_specification']['value'], '3mg')
        self.assertEqual(fields['item_7_english_or_latin_name']['value'], 'Synthetic')
        self.assertTrue(all(region.get('position_kind') == 'review_region_not_glyph'
                            for item in parsed['items'] if item['item_no'] in (6, 12)
                            for region in item['source_regions']))

    def test_manual_multifield_region_rejects_ambiguous_labels_and_keeps_continuations(self):
        from agent.agent_backend.utils.parser.drug_supplement_pdf_parser import _page_form_lines
        bounds = [10, 20, 200, 160]
        def page(text):
            return {'page': 1, 'words': [], 'tables': [], 'lines': [{
                'text': text, 'bbox': bounds, 'source': 'manual_revision', 'position_kind': 'review_region_not_glyph'}]}
        valid = page('药品通用名称：合成甲片\n规格：3mg\n每盒10片')
        original = deepcopy(valid)
        lines = _page_form_lines(valid, 180)
        self.assertEqual([(l.manual_target_item_no, l.text) for l in lines], [(6, '合成甲片'), (12, '3mg\n每盒10片')])
        self.assertEqual(valid, original)
        for text in ('药品通用名称：甲\n规格：3mg\n通用名称：乙',
                     '无法归属的前言\n药品通用名称：甲\n规格：3mg'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                _page_form_lines(page(text), 180)
        # 只有一个明确字段时沿用原行为，普通声明正文不因此被猜成字段。
        ordinary = _page_form_lines(page('声明\n保证资料完整'), 180)
        self.assertEqual(len(ordinary), 1)
        self.assertEqual(ordinary[0].manual_target_item_no, 0)

    def test_explicit_drug_english_and_latin_labels_reach_field_without_guessing_prose(self):
        from agent.agent_backend.utils.parser.form_field_semantics import heading
        for label in ('药品英文名称', '药品拉丁名称', '英文名称', '拉丁名称'):
            line = {'text': label + '：Synthetic 2', 'bbox': [30, 200, 350, 220], 'source': 'native'}
            pages = [{'page': 1, 'page_bbox': [0, 0, 600, 400], 'lines': [line], 'words': [line],
                      'tables': [], 'raw_text': line['text'], 'status': 'success', 'errors': []}]
            with self.subTest(label=label):
                fields = FilingChangeFormParserService().form_from_pdf_result(build_pdf_form_from_pages(pages))
                self.assertEqual(fields['form_json']['item_7_english_or_latin_name']['value'], 'Synthetic 2')
        self.assertIsNone(heading('本段讨论药品英文名称：Synthetic 2'))
        self.assertIsNone(heading('药品英文名称变更情况'))

    def test_rebuild_does_not_multiply_identical_field_evidence(self):
        lines = [
            {'text': '药品通用名称：合成甲', 'bbox': [30, 40, 190, 60], 'source': 'native'},
            {'text': '规格：3mg', 'bbox': [300, 40, 390, 60], 'source': 'native'},
            {'text': '跨界文字需核对', 'bbox': [260, 80, 390, 100], 'source': 'native'},
        ]
        pages = [{'page': 1, 'page_bbox': [0, 0, 600, 400], 'lines': lines, 'words': lines,
                  'tables': [], 'raw_text': '\n'.join(l['text'] for l in lines), 'status': 'partial',
                  'errors': [{'code': 'ocr_quality', 'bbox_pdf': [20, 30, 200, 65]}]}]
        first = build_pdf_form_from_pages(pages)
        self.assertTrue(first['unassigned_regions'])
        original = deepcopy(first['pages'])
        second = build_pdf_form_from_pages(first['pages'])
        third = build_pdf_form_from_pages(second['pages'])
        self.assertEqual(first['pages'], original)
        self.assertEqual(len(second['pages'][0]['errors']), len(original[0]['errors']))
        self.assertEqual(third['pages'][0]['errors'], original[0]['errors'])
        # 内容相同但候选字段不同的旧证据不能被宽松合并或删除。
        distinct = deepcopy(original)
        distinct[0]['errors'].append({**first['unassigned_regions'][0], 'candidate_items': [7, 12]})
        rebuilt = build_pdf_form_from_pages(distinct)
        self.assertEqual(rebuilt['pages'][0]['errors'], distinct[0]['errors'])

    def test_rebuild_from_pages_without_ocr_and_without_mutation(self):
        lines = [
            {'text': '6. 药品通用名称', 'bbox': [20, 40, 160, 60], 'source': 'native'},
            {'text': '合成核对药品', 'bbox': [20, 65, 190, 85], 'source': 'manual_revision'},
        ]
        pages = [{'page': 1, 'page_bbox': [0, 0, 300, 400], 'lines': lines,
                  'words': [], 'tables': [], 'raw_text': '\n'.join(l['text'] for l in lines),
                  'status': 'partial', 'errors': [{'code': 'ocr_quality'}]}]
        original = deepcopy(pages)
        service = FilingChangeFormParserService()
        with patch.object(service, '_refine_pdf_items', side_effect=AssertionError('must not OCR')), \
             patch.object(service, '_apply_pdf_region_ocr_enhancements', side_effect=AssertionError('must not OCR')):
            parsed = build_pdf_form_from_pages(pages, source_file='synthetic.pdf')
            result = service.form_from_pdf_result(parsed)
        self.assertEqual(pages, original)
        self.assertEqual(result['form_json']['item_6_generic_name']['value'], '合成核对药品')
        self.assertEqual(parsed['pages'][0]['status'], 'partial')
        self.assertEqual(result['raw_text'], original[0]['raw_text'])


if __name__ == '__main__':
    unittest.main()
