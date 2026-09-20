"""F01/F02：输入事实固定；真实解析、服务保存、新连接读取和原文件校验。"""
import tempfile
import unittest
import os
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from agent.test.test_four_semantic_table_defects import word_file, pdf_file
from agent.test.t123_audit_20260905.audit_crash import service_at
from agent.test.test_filing_change_review_service_regression import _Upload
from agent.agent_backend.services.filing_change_form_parser_service import FilingChangeFormParserService
from agent.agent_backend.services.filing_parse_outcome import ParseFailure
from agent.agent_backend.services.filing_form_revision import merge_parse


NAME = 'item_6_generic_name'
ENGLISH = 'item_7_english_or_latin_name'
VALIDITY = 'item_15_validity_period'
EMPTY = [['药品通用名称', ''], ['英文名称', ''], ['化学名称', ''], ['规格', ''], ['药品有效期', '']]


class ReplacementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='f01-f02-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        if os.getenv('T2_OCR_URL'):
            from agent.agent_backend.config.settings import settings
            self.enterContext(patch.object(settings, 'ocr_service_url', os.environ['T2_OCR_URL']))
        self.service = service_at(self.root)
        self.addCleanup(self.service.db_conn.engine.dispose)
        self.pid = self.service.create_project({'project_name': '原件替换回归'})[2]['project_id']

    def upload(self, name, rows):
        path = self.root / name
        (pdf_file if path.suffix == '.pdf' else word_file)(path, rows)
        return self.service.import_application_form(self.pid, _Upload(name, path.read_bytes()))

    def read(self):
        fresh = service_at(self.root)
        try:
            return fresh.get_application_form(self.pid)[2]
        finally:
            fresh.db_conn.engine.dispose()

    def assert_original(self, state, name):
        path = self.service._project_path(self.pid) / 'application_form' / (state['original_file_id'] + Path(name).suffix)
        self.assertEqual(path.read_bytes(), (self.root / name).read_bytes())
        self.assertEqual(state['effective_source']['source_file_name'], name)

    def test_blank_word_and_pdf_keep_readable_effective_original(self):
        self.assertTrue(self.upload('有效.docx', [['药品通用名称', '明确原药名']])[0])
        first = self.read()
        for ext in ('docx', 'pdf'):
            with self.subTest(ext=ext):
                ok, _, attempt = self.upload('空模板.' + ext, EMPTY)
                self.assertFalse(ok)
                self.assertEqual(attempt['code'], 'empty_form')
                current = self.read()
                self.assertEqual(current['original_file_id'], first['original_file_id'])
                self.assertEqual(current['form_json'], first['form_json'])
                self.assertEqual(current['parse_status'], 'success')
                self.assertEqual(current['latest_attempt']['content_status'], 'failed')
                self.assertEqual(current['latest_attempt']['parse_diagnostics']['status'], 'failed')
                self.assert_original(current, '有效.docx')
                self.assertTrue(self.service.parse_application_form(self.pid)[0])
                self.assertEqual(self.read()['form_json'][NAME]['value'], '明确原药名')
                # 同一原件重解析必须沿用有效文件关联。
                first = self.read()

    def test_template_prompts_units_are_not_filled_content(self):
        for ext in ('docx', 'pdf'):
            for prompt in ('请填写药品名称', '（请填写）', '____', '月'):
                with self.subTest(ext=ext, prompt=prompt):
                    ok, _, _ = self.upload('提示.' + ext, [['药品通用名称', prompt], ['药品有效期', '月']])
                    self.assertFalse(ok)
        self.assertTrue(self.upload('不完整.docx', [['药品通用名称', '真实药名'], ['英文名称', '']])[0])
        self.assertEqual(self.read()['form_json'][ENGLISH]['recognition_status'], 'explicit_blank')

    def test_empty_group_labels_are_not_filled_content(self):
        self.upload('有效.docx', [['药品通用名称', '真实药名']])
        original = self.read()
        for ext in ('docx', 'pdf'):
            with self.subTest(ext=ext):
                ok, _, attempt = self.upload('子标签.'+ext, EMPTY + [
                    ['药品有效期', '原有效期', ''], ['药品有效期', '拟延长后有效期', ''],
                    ['药品有效期', '贮藏条件', ''], ['包装', '包装规格', '']])
                self.assertFalse(ok)
                self.assertEqual(attempt['code'], 'empty_form')
                self.assertEqual(self.read()['original_file_id'], original['original_file_id'])
                self.assert_original(self.read(), '有效.docx')

    def test_cross_format_replacement_uses_new_name_and_blank(self):
        for old_ext, new_ext in [('docx', 'pdf'), ('pdf', 'docx')]:
            with self.subTest(old=old_ext, new=new_ext):
                old_name = '原Word药品甲' if old_ext == 'docx' else '原PDF药品甲'
                new_name = '新PDF药品乙' if new_ext == 'pdf' else '新Word药品乙'
                self.assertTrue(self.upload('old.'+old_ext, [['药品通用名称', old_name], ['英文名称', 'Old Name']])[0])
                self.assertTrue(self.upload('new.'+new_ext, [['药品通用名称', new_name], ['英文名称', '']])[0])
                current = self.read()
                self.assertEqual(current['form_json'][NAME]['value'], new_name)
                self.assertEqual(current['form_json'][ENGLISH]['value'], '')
                self.assertEqual(current['form_json'][ENGLISH]['recognition_status'], 'explicit_blank')
                self.assertEqual(current['form_json'][NAME]['value_sources']['value']['value'], new_name)
                self.assertEqual(current['form_json'][NAME]['value_sources']['value']['source_file_id'], current['original_file_id'])
                self.assertEqual(current['form_json'][NAME]['source_file'], 'new.'+new_ext)
                self.assert_original(current, 'new.'+new_ext)
                self.assertTrue(self.service.parse_application_form(self.pid)[0])
                self.assertEqual(self.read()['form_json'][NAME]['value'], new_name)

    def test_conflicting_new_validity_never_refills_old_values(self):
        for ext in ('docx', 'pdf'):
            with self.subTest(ext=ext):
                self.assertTrue(self.upload('old.docx', [['药品通用名称', '原药名'], ['药品有效期', '原有效期18个月，拟延长后有效期24个月']])[0])
                self.assertTrue(self.upload('conflict.'+ext, [['药品通用名称', '新药名'], ['药品有效期', '原有效期18个月，拟延长后有效期36个月'], ['补充申请的内容', '药品有效期由18个月延长至24个月']])[0])
                field = self.read()['form_json'][VALIDITY]
                self.assertTrue(field['semantic_issues'])
                for path in ('original_validity_period', 'proposed_validity_period'):
                    self.assertEqual(field['sub_fields'][path], '')
                    self.assertEqual(field['value_sources']['sub_fields.'+path]['recognition_status'], 'conflict')

    def test_manual_value_and_clear_keep_own_source_and_blank_candidate(self):
        for manual in ('人工药名', ''):
            with self.subTest(manual=manual):
                self.assertTrue(self.upload('old.docx', [['药品通用名称', '原Word药品甲'], ['英文名称', 'Old Name']])[0])
                edited = deepcopy(self.read()['form_json'])
                edited[NAME]['value'] = manual
                edited[ENGLISH]['value'] = '人工英文'
                self.assertTrue(self.service.save_application_form(self.pid, {'form_json': edited})[0])
                self.assertTrue(self.upload('new.pdf', [['药品通用名称', '新PDF药品乙'], ['英文名称', '']])[0])
                current = self.read()
                field = current['form_json'][NAME]
                self.assertEqual(field['value'], manual)
                self.assertTrue(field['manual_modified'])
                self.assertEqual(field['value_sources']['value']['recognition_status'], 'manual')
                self.assertEqual(field['value_sources']['value']['value'], manual)
                candidate = field['candidates']['value']
                self.assertEqual(candidate['value'], '新PDF药品乙')
                self.assertEqual(candidate['source_file'], 'new.pdf')
                self.assertEqual(candidate['source_file_id'], current['original_file_id'])
                self.assertTrue(candidate['source_regions'])
                blank = current['form_json'][ENGLISH]['candidates']['value']
                self.assertEqual(blank['value'], '')
                self.assertEqual(blank['recognition_status'], 'explicit_blank')
                self.assertTrue(self.service.save_application_form(self.pid, {'form_json': current['form_json'], 'resolutions': {NAME: {'value': 'keep'}, ENGLISH: {'value': 'adopt'}}})[0])
                self.assertEqual(self.read()['form_json'][ENGLISH]['value'], '')
                self.assertFalse(self.read()['form_json'][ENGLISH]['manual_modified'])
                self.assertTrue(self.service.parse_application_form(self.pid)[0])
                current = self.read()
                self.assertEqual(current['form_json'][NAME]['value'], manual)
                self.assertTrue(self.service.save_application_form(self.pid, {'form_json': current['form_json'], 'resolutions': {NAME: {'value': 'adopt'}}})[0])
                self.assertEqual(self.read()['form_json'][NAME]['value'], '新PDF药品乙')
                self.assertFalse(self.read()['form_json'][NAME]['manual_modified'])

    def test_unrecognized_new_field_is_separate_from_explicit_blank(self):
        self.upload('old.docx', [['药品通用名称', '原药'], ['英文名称', 'Old Name']])
        self.upload('new.docx', [['药品通用名称', '新药']])
        field = self.read()['form_json'][ENGLISH]
        self.assertEqual(field['value'], '')
        self.assertEqual(field['recognition_status'], 'unrecognized')
        self.assertEqual(field['value_sources']['value']['source_file'], 'new.docx')

    def test_supplement_only_can_keep_stronger_same_source_value(self):
        old = {'x': {'field_type': 'input', 'value': '清晰', 'parse_confidence': .9, 'source_file': 'same.pdf'}}
        new = {'x': {'field_type': 'input', 'value': '弱候选', 'parse_confidence': .5, 'source_file': 'same.pdf'}}
        self.assertEqual(merge_parse(old, new, mode='supplement')['x']['value'], '清晰')
        self.assertEqual(merge_parse(old, new, mode='replace')['x']['value'], '弱候选')
        self.assertEqual(merge_parse(old, new, mode='reparse')['x']['value'], '弱候选')

    def test_option_evidence_and_same_named_replacement_keep_file_identity(self):
        for ext in ('docx', 'pdf'):
            self.upload('同名.'+ext, [['药品通用名称', '旧药'], ['申请事项分类', '6.8 变更有效期和贮藏条件☑', '6.9 增加规格□']])
            old = self.read()
            self.upload('同名.'+ext, [['药品通用名称', '新药'], ['申请事项分类', '6.8 变更有效期和贮藏条件□', '6.9 增加规格☑']])
            current = self.read()
            self.assertNotEqual(current['original_file_id'], old['original_file_id'])
            field = current['form_json']['item_5_application_matter_category']
            self.assertEqual(field['selected_values'], ['1.8'])
            for entry in field['original_matter'] + field['selection_evidence']:
                self.assertEqual(entry['source_file'], '同名.'+ext)
                self.assertEqual(entry['source_file_id'], current['original_file_id'])
            self.assertTrue(self.service.parse_application_form(self.pid)[0])
            self.assertEqual(self.read()['original_file_id'], current['original_file_id'])

    def test_manual_validity_and_manual_clear_keep_conflict_candidate(self):
        for ext in ('docx', 'pdf'):
            self.upload('old.docx', [['药品通用名称', '原药'], ['药品有效期', '原有效期18个月，拟延长后有效期24个月']])
            form = deepcopy(self.read()['form_json'])
            form[VALIDITY]['sub_fields'].update(original_validity_period='人工确认20个月', proposed_validity_period='')
            self.assertTrue(self.service.save_application_form(self.pid, {'form_json': form})[0])
            self.upload('new.'+ext, [['药品通用名称', '新药'], ['药品有效期', '原有效期18个月，拟延长后有效期36个月'], ['补充申请的内容', '药品有效期由18个月延长至24个月']])
            field = self.read()['form_json'][VALIDITY]
            self.assertEqual(field['sub_fields']['original_validity_period'], '人工确认20个月')
            self.assertEqual(field['sub_fields']['proposed_validity_period'], '')
            self.assertTrue(field['manual_modified'])
            candidate = field['candidates']['sub_fields.original_validity_period']
            self.assertEqual(candidate['value'], '')
            self.assertEqual(candidate['recognition_status'], 'conflict')
            self.assertEqual(candidate['source_file'], 'new.'+ext)
            self.assertTrue(candidate['source_regions'])

    def test_blank_attempt_reaches_real_review_without_relabeling_old_values(self):
        from agent.agent_backend.services.filing_change_review_orchestrator import FilingChangeReviewOrchestrator
        from agent.agent_backend.services.filing_change_extraction_service import FilingChangeExtractionService
        from agent.agent_backend.services.filing_change_quality_check_service import FilingChangeQualityCheckService
        from agent.agent_backend.services.filing_change_stability_service import FilingChangeStabilityService
        self.upload('有效.docx', [['药品通用名称', '明确原药名']])
        self.assertFalse(self.upload('空白.pdf', EMPTY)[0])
        current = self.read()
        extracted = FilingChangeExtractionService().run(current, [], {})
        drug_facts = [fact for fact in extracted['facts'] if fact['field'] == 'drug_name']
        self.assertEqual([fact['raw_value'] for fact in drug_facts], ['明确原药名'])
        self.assertEqual(drug_facts[0]['source']['detail']['value']['source_file'], '有效.docx')
        self.assertEqual(extracted['application_form'][NAME]['value_sources']['value']['source_file'], '有效.docx')
        reviewer = FilingChangeReviewOrchestrator(self.service.form_parser, FilingChangeQualityCheckService(), FilingChangeStabilityService())
        # 只隔离远程模型生成，真实规则、提取、异常汇总和来源取数仍执行。
        with patch.object(reviewer.llm_gate, 'should_call_llm', return_value={'allow': False, 'reason': '测试不调用外部模型'}):
            result = reviewer.run(self.pid, current, [], [], [], {}, {'missing_items': []})
        issues = result['formal_review']['parse_issues']
        self.assertEqual(issues[0]['status'], 'failed')
        self.assertEqual(issues[0]['latest_attempt']['source_file_name'], '空白.pdf')
        self.assertEqual(result['formal_review']['missing_materials'], [])
        self.assertTrue(result['overall_conclusion']['need_manual_review'])

    def test_ambiguous_pdf_region_survives_save_read_and_reparse(self):
        import fitz
        path = self.root / '跨界.pdf'
        with fitz.open() as doc:
            page = doc.new_page(width=900, height=600)
            for x, y, text in [(30, 60, '药品通用名称：甲乙片'), (300, 60, '规格：3mg'), (260, 90, '跨界文字需要核对')]:
                page.insert_text((x, y), text, fontname='china-s', fontsize=12)
            doc.save(path)
        self.assertTrue(self.service.import_application_form(self.pid, _Upload(path.name, path.read_bytes()))[0])
        current = self.read()
        self.assertEqual(current['parse_status'], 'partial')
        issues = current['effective_source']['parse_diagnostics']['errors']
        issue = next(i for i in issues if i['code'] == 'field_region_crossing')
        self.assertEqual(issue['text'], '跨界文字需要核对')
        self.assertEqual(issue['candidate_items'], [6, 12])
        self.assertTrue(issue['bbox_pdf'])
        self.assertEqual(current['form_json'][NAME]['value'], '甲乙片')
        self.assertEqual(current['form_json']['item_12_specification']['value'], '3mg')
        self.assertTrue(self.service.save_application_form(self.pid, {'form_json': current['form_json']})[0])
        self.assertEqual(self.read()['effective_source']['parse_diagnostics']['errors'], issues)
        self.assertTrue(self.service.parse_application_form(self.pid)[0])
        self.assertEqual(self.read()['effective_source']['parse_diagnostics']['errors'], issues)


if __name__ == '__main__':
    unittest.main()
