"""独立复核 F01：本次填写证据，真实原件与新连接持久化断言。"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.test.test_f01_f02_form_replacement import service_at, _Upload, NAME
from agent.test.test_four_semantic_table_defects import word_file, pdf_file
from agent.agent_backend.services.filing_change_form_parser_service import FilingChangeFormParserService
from agent.agent_backend.services.filing_parse_outcome import form_outcome


EMPTY_OPTIONS = [['药品通用名称', ''], ['是否为OTC', '处方药□', '非处方药□'],
                 ['剂型', '片剂□', '胶囊剂□']]


class CurrentContentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='followup-f01-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        from agent.agent_backend.config.settings import settings
        if os.environ.get('T2_OCR_URL'):
            self.enterContext(patch.object(settings, 'ocr_service_url', os.environ['T2_OCR_URL']))
        self.service = service_at(self.root)
        self.addCleanup(self.service.db_conn.engine.dispose)
        self.pid = self.service.create_project({'project_name': '本次填写证据'})[2]['project_id']

    def upload(self, name, rows):
        p = self.root / name
        (pdf_file if p.suffix == '.pdf' else word_file)(p, rows)
        return self.service.import_application_form(self.pid, _Upload(name, p.read_bytes()))

    def read(self):
        service = service_at(self.root)
        try:
            return service.get_application_form(self.pid)[2]
        finally:
            service.db_conn.engine.dispose()

    def assert_empty_replacement(self, extension, rows):
        self.assertTrue(self.upload('此前.docx', [['药品通用名称', '此前有效药品']])[0])
        before = self.read()
        original = self.service._project_path(self.pid) / 'application_form' / (before['original_file_id'] + '.docx')
        content = original.read_bytes()
        ok, _, attempt = self.upload('空模板.'+extension, rows)
        self.assertFalse(ok)
        self.assertEqual(attempt['code'], 'empty_form')
        for _ in range(2):
            after = self.read()
            self.assertEqual(after['original_file_id'], before['original_file_id'])
            self.assertEqual(after['form_json'], before['form_json'])
            self.assertEqual(after['effective_source'], before['effective_source'])
            self.assertEqual(after['latest_attempt']['content_status'], 'failed')
            self.assertEqual(after['latest_attempt']['code'], 'empty_form')
            self.assertEqual(original.read_bytes(), content)
            self.assertEqual(FilingChangeFormParserService().parse_form_file(str(original))['form_json'][NAME]['value'], '此前有效药品')
        self.assertTrue(self.service.parse_application_form(self.pid)[0])
        after = self.read()
        self.assertEqual(after['original_file_id'], before['original_file_id'])
        self.assertEqual(after['form_json'][NAME]['value'], '此前有效药品')
        self.assertEqual(original.read_bytes(), content)

    def test_unchecked_word_cannot_replace(self):
        self.assert_empty_replacement('docx', EMPTY_OPTIONS)

    def test_unchecked_pdf_cannot_replace(self):
        self.assert_empty_replacement('pdf', EMPTY_OPTIONS)

    def test_header_only_word_cannot_replace(self):
        self.assert_empty_replacement('docx', [['药品通用名称', ''], ['原辅包来源', ''], ['物料名称', '登记号', '受理号', '供应商']])

    def test_header_only_pdf_cannot_replace(self):
        self.assert_empty_replacement('pdf', [['药品通用名称', ''], ['原辅包来源', ''], ['物料名称', '登记号', '受理号', '供应商']])

    def test_one_real_name_with_unchecked_neighbors_is_usable(self):
        for extension in ('docx', 'pdf'):
            with self.subTest(extension=extension):
                self.assertTrue(self.upload('部分填写.'+extension, [['药品通用名称', '真实填写']] + EMPTY_OPTIONS[1:])[0])
                state = self.read()
                self.assertEqual(state['form_json'][NAME]['value'], '真实填写')
                for key in ('item_3_otc_type', 'item_11_dosage_form'):
                    field = state['form_json'][key]
                    self.assertEqual(field['recognition_status'], 'explicit_blank')
                    self.assertTrue(all(e['selected'] is False for e in field['selection_evidence']))

    def test_only_explicit_selection_is_usable(self):
        for extension in ('docx', 'pdf'):
            for label, value, key in [('剂型', '片剂☑', 'item_11_dosage_form'), ('是否为OTC', '☑非处方药', 'item_3_otc_type')]:
                with self.subTest(extension=extension, label=label):
                    self.assertTrue(self.upload('只有选项.'+extension, [[label, value]])[0])
                    field = self.read()['form_json'][key]
                    self.assertEqual(field['recognition_status'], 'extracted')
                    self.assertTrue(any(e['selected'] is True for e in field['selection_evidence']))

    def test_unmapped_selected_matter_is_filled_but_needs_review(self):
        for extension in ('docx', 'pdf'):
            with self.subTest(extension=extension):
                self.assertTrue(self.upload('未映射事项.'+extension, [['申请事项分类', '9.9 特殊申请事项☑']])[0])
                state = self.read()
                self.assertEqual(state['parse_status'], 'partial')
                self.assertTrue(state['form_json']['item_5_application_matter_category']['semantic_issues'])

    def test_filled_unrecognized_content_is_not_empty_template(self):
        for extension in ('docx', 'pdf'):
            with self.subTest(extension=extension):
                ok, _, result = self.upload('未识别填写.'+extension, [['是否为OTC', '目前状态待补充确认']])
                self.assertTrue(ok)
                state = self.read()
                self.assertEqual(state['parse_status'], 'partial')
                self.assertEqual(state['effective_source']['parse_diagnostics']['form_content_status'], 'unrecognized_content')
                self.assertIn('目前状态待补充确认', state['raw_text'])

    def test_option_ambiguity_is_filled_not_template(self):
        for extension in ('docx', 'pdf'):
            with self.subTest(extension=extension):
                ok, _, _ = self.upload('歧义填写.'+extension, [['是否为OTC', '处方药☑非处方药']])
                self.assertTrue(ok)
                self.assertEqual(self.read()['parse_status'], 'partial')

    def test_old_or_default_values_cannot_prove_this_attempt(self):
        parsed = {'raw_text': '是否为OTC | 处方药□ | 非处方药□',
                  'form_json': FilingChangeFormParserService()._build_full_form('')}
        # 本次schema中的默认单位不构成原文填写；调用只提供新解析，没有旧记录。
        from agent.agent_backend.services.filing_parse_outcome import ParseFailure
        with self.assertRaises(ParseFailure) as error:
            form_outcome(parsed)
        self.assertEqual(error.exception.code, 'empty_form')

    def test_free_text_without_recognized_fields_retains_partial_content(self):
        for extension in ('docx', 'pdf'):
            with self.subTest(extension=extension):
                self.assertTrue(self.upload('自由填写.'+extension, [['其他自填栏目', '这是本次真实填写的补充内容']])[0])
                state = self.read()
                self.assertEqual(state['parse_status'], 'partial')
                self.assertEqual(state['effective_source']['parse_diagnostics']['form_content_status'], 'unrecognized_content')
                self.assertIn('这是本次真实填写的补充内容', state['raw_text'])
