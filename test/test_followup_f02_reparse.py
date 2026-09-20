"""F02：先断言真实 OCR 正确，再验证同件中断、保存刷新及恢复。"""
import json
import os
import tempfile
import threading
import unittest
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import fitz

from agent.test.test_f01_f02_form_replacement import service_at, _Upload, NAME, ENGLISH
from agent.agent_backend.config.settings import settings
from agent.agent_backend.services.filing_form_revision import merge_parse, save_revision


EVIDENCE = Path('/tmp/followup-20260906/f02')


def scanned_pdf(path):
    with fitz.open() as doc, fitz.open() as scan:
        page = doc.new_page()
        page.insert_text((35, 65), '药品通用名称：明确药品', fontname='china-s', fontsize=14)
        page = scan.new_page(width=300, height=60)
        page.insert_text((10, 35), 'EXAMPLE TABLETS', fontname='helv', fontsize=22)
        image = page.get_pixmap(matrix=fitz.Matrix(3, 3)).tobytes('png')
        page = doc.new_page()
        page.insert_text((35, 65), '英文名称：', fontname='china-s', fontsize=18)
        page.insert_image(fitz.Rect(200, 30, 500, 90), stream=image)
        doc.save(path)


class RealReparseTests(unittest.TestCase):
    def setUp(self):
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='f02-', dir=EVIDENCE)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.svc = service_at(self.root)
        self.addCleanup(self.svc.db_conn.engine.dispose)
        self.pid = self.svc.create_project({'project_name': 'F02同件重解析'})[2]['project_id']
        # 部署镜像为Python 3.10，TestCase.enterContext是3.11才提供的接口。
        for name, value in (('ocr_service_url', os.environ.get('T2_OCR_URL', 'http://127.0.0.1:18765')),
                            ('ocr_lang', 'eng')):
            context = patch.object(settings, name, value)
            context.start()
            self.addCleanup(context.stop)
        self.path = self.root / 'reparse.pdf'
        scanned_pdf(self.path)
        self.content = self.path.read_bytes()

    def read(self):
        fresh = service_at(self.root)
        try:
            return fresh.get_application_form(self.pid)[2]
        finally:
            fresh.db_conn.engine.dispose()

    def record(self, phase, state):
        (EVIDENCE / (os.getenv('F02_RUN_LABEL', 'after-fix') + '_' + self._testMethodName + '_' + phase + '.json')).write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')

    def exercise(self, url, code):
        self.assertTrue(self.svc.import_application_form(self.pid, _Upload(self.path.name, self.content))[0])
        before = self.read()
        self.record('before', before)
        self.assertEqual(before['parse_status'], 'success')
        self.assertEqual(before['form_json'][ENGLISH]['value'], 'EXAMPLE TABLETS')
        old_source = before['form_json'][ENGLISH]['value_sources']['value']
        self.assertEqual(old_source['recognition_status'], 'extracted')
        self.assertTrue(old_source['source_regions'])
        self.assertGreater(before['effective_source']['parse_diagnostics']['ocr_calls'], 0)
        stored = self.svc._project_path(self.pid) / 'application_form' / (before['original_file_id'] + '.pdf')
        with patch.object(settings, 'ocr_service_url', url), patch.object(settings, 'ocr_timeout_seconds', 1):
            self.assertTrue(self.svc.parse_application_form(self.pid)[0])
            failed = self.read()
            self.record('failed', failed)
            self.assertEqual(failed['parse_status'], 'partial')
            diagnostics = failed['effective_source']['parse_diagnostics']
            self.assertEqual(diagnostics['failed_pages'], [2])
            self.assertIn(code, [e['code'] for e in diagnostics['errors']])
            field = failed['form_json'][ENGLISH]
            self.assertEqual(field['value'], 'EXAMPLE TABLETS')
            source = field['value_sources']['value']
            self.assertEqual(source['recognition_status'], 'previous_result')
            self.assertEqual(source['previous_source'], old_source)
            self.assertEqual(source['reparse_diagnostics']['status'], 'partial')
            self.assertEqual(source['reparse_diagnostics']['failed_pages'], [2])
            self.assertTrue(source['reparse_diagnostics']['errors'][0]['bbox_pdf'])
            self.assertTrue(any(h['historical'] and h['value'] == 'EXAMPLE TABLETS'
                                and h['source_regions'] == old_source['source_regions'] for h in field['value_history']))
            self.assertEqual(failed['form_json'][NAME]['value'], '明确药品')
            self.assertEqual(failed['form_json'][NAME]['recognition_status'], 'extracted')
            self.assertTrue(self.svc.save_application_form(self.pid, {'form_json': failed['form_json']})[0])
            saved = self.read()
            self.record('saved', saved)
            self.assertEqual(saved['form_json'][ENGLISH], field)
            self.assertEqual(saved['effective_source']['parse_diagnostics'], diagnostics)
            self.assertEqual(saved['original_file_id'], before['original_file_id'])
            self.assertEqual(stored.read_bytes(), self.content)
            # 再次失败仍引用最早有效证据，不把此前结果嵌套成无限历史。
            self.assertTrue(self.svc.parse_application_form(self.pid)[0])
            self.assertEqual(self.read()['form_json'][ENGLISH]['value_sources']['value']['previous_source'], old_source)
        self.assertTrue(self.svc.parse_application_form(self.pid)[0])
        restored = self.read()
        self.record('restored', restored)
        self.assertEqual(restored['parse_status'], 'success')
        self.assertEqual(restored['form_json'][ENGLISH]['value'], 'EXAMPLE TABLETS')
        self.assertEqual(restored['form_json'][ENGLISH]['recognition_status'], 'extracted')
        self.assertNotIn('previous_source', restored['form_json'][ENGLISH]['value_sources']['value'])
        self.assertTrue(restored['form_json'][ENGLISH]['value_history'])
        self.assertEqual(restored['original_file_id'], before['original_file_id'])
        self.assertEqual(stored.read_bytes(), self.content)

    def test_connection_failure_and_recovery(self):
        # 与独立复核相同的不可连接本机端口，不干预正常 OCR。
        self.exercise('http://127.0.0.1:1', 'ocr_unreachable')

    def test_timeout_and_recovery(self):
        release = threading.Event()

        class SlowHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                release.wait(10)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), SlowHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            self.exercise('http://127.0.0.1:' + str(server.server_port), 'ocr_timeout')
        finally:
            release.set()
            server.shutdown()
            server.server_close()
            thread.join(2)

    def test_manual_value_and_clear_survive_real_ocr_failure_and_resolve(self):
        for index, manual in enumerate(('人工确认英文', '')):
            with self.subTest(manual=manual):
                self.assertTrue(self.svc.import_application_form(self.pid, _Upload(self.path.name, self.content))[0])
                before = self.read()
                self.record(f'{index}_before', before)
                self.assertEqual(before['parse_status'], 'success')
                self.assertEqual(before['form_json'][ENGLISH]['value'], 'EXAMPLE TABLETS')
                source = deepcopy(before['form_json'][ENGLISH]['value_sources']['value'])
                self.assertEqual(source['recognition_status'], 'extracted')
                self.assertTrue(source['source_regions'])
                self.assertGreater(before['effective_source']['parse_diagnostics']['ocr_calls'], 0)
                stored = self.svc._project_path(self.pid) / 'application_form' / (before['original_file_id'] + '.pdf')

                submitted = deepcopy(before['form_json'])
                submitted[ENGLISH]['value'] = manual
                self.assertTrue(self.svc.save_application_form(self.pid, {'form_json': submitted})[0])
                edited = self.read()
                self.record(f'{index}_edited', edited)
                manual_source = edited['form_json'][ENGLISH]['value_sources']['value']
                self.assertEqual(manual_source['recognition_status'], 'manual')
                self.assertEqual(manual_source['edited_from'], source)
                self.assertEqual(edited['form_json'][ENGLISH]['value'], manual)

                with patch.object(settings, 'ocr_service_url', 'http://127.0.0.1:1'), \
                        patch.object(settings, 'ocr_timeout_seconds', 1):
                    self.assertTrue(self.svc.parse_application_form(self.pid)[0])
                failed = self.read()
                self.record(f'{index}_failed', failed)
                field = failed['form_json'][ENGLISH]
                self.assertEqual(failed['parse_status'], 'partial')
                diagnostics = failed['effective_source']['parse_diagnostics']
                self.assertEqual(diagnostics['failed_pages'], [2])
                self.assertTrue(any(e['code'] == 'ocr_unreachable' and e['page'] == 2 and e['bbox_pdf']
                                    for e in diagnostics['errors']))
                self.assertEqual(field['value'], manual)
                self.assertTrue(field['manual_modified'])
                self.assertIn('value', field['manual_paths'])
                self.assertEqual(field['value_sources']['value'], manual_source)
                self.assertEqual(field['value_sources']['value']['edited_from'], source)

                self.assertTrue(self.svc.parse_application_form(self.pid)[0])
                recovered = self.read()
                self.record(f'{index}_recovered', recovered)
                field = recovered['form_json'][ENGLISH]
                self.assertEqual(recovered['parse_status'], 'success')
                self.assertEqual(field['value'], manual)
                self.assertEqual(field['value_sources']['value'], manual_source)
                candidate = field['candidates']['value']
                self.assertEqual(candidate['value'], 'EXAMPLE TABLETS')
                self.assertEqual(candidate['recognition_status'], 'extracted')
                self.assertEqual(candidate['source_file_id'], before['original_file_id'])
                self.assertEqual(candidate['source_regions'], source['source_regions'])

                self.assertTrue(self.svc.save_application_form(self.pid, {
                    'form_json': recovered['form_json'], 'resolutions': {ENGLISH: {'value': 'keep'}}})[0])
                kept = self.read()
                self.record(f'{index}_kept', kept)
                self.assertEqual(kept['form_json'][ENGLISH]['value'], manual)
                self.assertTrue(kept['form_json'][ENGLISH]['manual_modified'])
                self.assertEqual(kept['form_json'][ENGLISH]['value_sources']['value'], manual_source)
                self.assertNotIn('value', kept['form_json'][ENGLISH]['candidates'])
                self.assertEqual(kept['form_json'][ENGLISH]['resolution_history'][-1]['action'], 'keep')

                # 保留后重新解析会产生可再次核对的候选，采纳须经过真实保存。
                self.assertTrue(self.svc.parse_application_form(self.pid)[0])
                pending = self.read()
                candidate = pending['form_json'][ENGLISH]['candidates']['value']
                self.assertEqual(candidate['value'], 'EXAMPLE TABLETS')
                self.assertTrue(self.svc.save_application_form(self.pid, {
                    'form_json': pending['form_json'], 'resolutions': {ENGLISH: {'value': 'adopt'}}})[0])
                adopted = self.read()
                self.record(f'{index}_adopted', adopted)
                self.assertEqual(adopted['form_json'][ENGLISH]['value'], 'EXAMPLE TABLETS')
                self.assertFalse(adopted['form_json'][ENGLISH]['manual_modified'])
                self.assertNotIn('value', adopted['form_json'][ENGLISH]['manual_paths'])
                self.assertNotIn('value', adopted['form_json'][ENGLISH]['candidates'])
                self.assertEqual(adopted['form_json'][ENGLISH]['value_sources']['value'], candidate)
                self.assertEqual(adopted['form_json'][ENGLISH]['resolution_history'][-1]['action'], 'adopt')
                for state in (edited, failed, recovered, kept, pending, adopted):
                    self.assertEqual(state['original_file_id'], before['original_file_id'])
                    self.assertEqual(state['effective_source']['source_file_id'], before['original_file_id'])
                self.assertEqual(stored.read_bytes(), self.content)


def field(value, *, page=2, bbox=(210, 45, 420, 70), status=None, file_id='same'):
    return {'field_type': 'input', 'value': value, 'source_file_id': file_id,
            'source_file': 'same.pdf', 'parse_confidence': .9 if value else 0,
            'recognition_status': status or ('extracted' if value else 'unrecognized'),
            'source_regions': [{'page': page, 'bbox_pdf': list(bbox), 'coordinate_unit': 'pdf_point'}]}


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.diag = {'status': 'partial', 'failed_pages': [2], 'errors': [
            {'page': 2, 'stage': 'ocr', 'code': 'ocr_timeout', 'bbox_pdf': [200, 30, 500, 90], 'coordinate_unit': 'pdf_point'}]}

    def merged(self, old, fresh, mode='reparse', diagnostics=None):
        return merge_parse({'x': old}, {'x': fresh}, mode=mode,
                           diagnostics=self.diag if diagnostics is None else diagnostics)['x']

    def test_affected_region_retains_previous_evidence(self):
        old, fresh = field('旧正确值'), field('')
        old_copy, diag_copy = deepcopy(old), deepcopy(self.diag)
        merged = self.merged(old, fresh)
        self.assertEqual(merged['value'], '旧正确值')
        self.assertEqual(merged['recognition_status'], 'previous_result')
        self.assertEqual(old, old_copy)
        self.assertEqual(self.diag, diag_copy)

    def test_other_page_and_other_region_clear(self):
        for old in (field('旧值', page=1), field('旧值', bbox=(20, 200, 100, 240))):
            with self.subTest(old=old):
                merged = self.merged(old, field(''))
                self.assertEqual(merged['value'], '')
                self.assertEqual(merged['recognition_status'], 'unrecognized')

    def test_blank_conflict_and_new_value_are_not_hidden(self):
        for fresh in (field('', status='explicit_blank'), field('', status='conflict'), field('新正确值')):
            with self.subTest(fresh=fresh):
                merged = self.merged(field('旧值'), fresh)
                self.assertEqual(merged['value'], fresh['value'])
                self.assertEqual(merged['recognition_status'], fresh['recognition_status'])
                self.assertTrue(any(h['value'] == '旧值' and h['historical'] for h in merged['value_history']))

    def test_missing_scope_does_not_invent_success_or_previous_result(self):
        for diag in ({}, {'status': 'partial'}, {'status': 'success', 'failed_pages': []}):
            self.assertEqual(self.merged(field('旧值'), field(''), diagnostics=diag)['value'], '')

    def test_page_failure_without_region_and_unknown_source(self):
        diag = {'status': 'partial', 'failed_pages': [2], 'errors': []}
        self.assertEqual(self.merged(field('旧值'), field(''), diagnostics=diag)['value'], '旧值')
        old = field('旧值')
        old['source_regions'] = []
        self.assertEqual(self.merged(old, field(''), diagnostics=diag)['value'], '')

    def test_replace_and_supplement_keep_their_meaning(self):
        self.assertEqual(self.merged(field('旧值'), field(''), mode='replace')['value'], '')
        self.assertEqual(self.merged(field('旧值'), field('新值', file_id='new'))['value'], '新值')
        self.assertEqual(self.merged(field('旧值'), field('', file_id='new'))['value'], '')
        self.assertEqual(self.merged(field('旧值'), field(''), mode='supplement')['value'], '旧值')
        self.assertEqual(self.merged(field('旧值'), field('', status='explicit_blank'), mode='supplement')['value'], '')

    def test_supplement_empty_skeleton_keeps_entire_previous_evidence(self):
        previous = field('', status='explicit_blank')
        previous.update(field_type='group', source_text='片剂□ 胶囊剂□', selected_values=[],
                        selection_evidence=[{'raw_text': '片剂□', 'state': 'unselected', 'page': 2}],
                        option_fragments=['片剂□', '胶囊剂□'], source_pages=[2],
                        sub_fields={'validity_unit': '月'},
                        candidates={'value': {'value': '此前候选'}},
                        value_history=[{'value': '历史值', 'historical': True}])
        empty = {'field_type': 'group', 'value': '', 'selected_values': [], 'table_rows': [],
                 'source_text': '', 'source_regions': [], 'sub_fields': {'validity_unit': '月'}}
        merged = self.merged(previous, empty, mode='supplement')
        self.assertEqual(merged, previous)
        merged['selection_evidence'][0]['state'] = 'changed'
        self.assertEqual(previous['selection_evidence'][0]['state'], 'unselected')

    def test_supplement_empty_metadata_keeps_previous_but_accepts_real_update(self):
        previous = {'original_form_type': '备案表', 'source_pages': [1, 2],
                    'metadata': {'source': '原件'}, 'unchanged': '有值'}
        empty = {'original_form_type': '', 'source_pages': [], 'metadata': {}}
        self.assertEqual(merge_parse(previous, empty, mode='supplement'), previous)
        self.assertEqual(merge_parse(previous, empty, mode='replace'), empty)
        self.assertEqual(merge_parse(previous, empty, mode='reparse'), empty)
        self.assertEqual(merge_parse(previous, {'original_form_type': '补充申请表'}, mode='supplement')
                         ['original_form_type'], '补充申请表')
        merged = merge_parse(previous, empty, mode='supplement')
        merged['metadata']['source'] = '修改'
        self.assertEqual(previous['metadata']['source'], '原件')

    def test_real_t3_pdf_metadata_survives_ocr_enhancement(self):
        from agent.test.test_filing_form_t3 import T3ParserTests
        from agent.agent_backend.services import filing_form_revision
        with patch.object(settings, 'ocr_service_url', os.environ.get('T2_OCR_URL', 'http://127.0.0.1:18765')), \
                patch.object(settings, 'ocr_lang', 'eng'), \
                patch.object(filing_form_revision, 'merge_parse', wraps=merge_parse) as merges:
            # 原T3双位置PDF断言不修改；真实OCR增强确实进入共用合并入口。
            T3ParserTests().test_real_pdf_two_positions()
            self.assertTrue(merges.called)

    def test_manual_clear_and_candidate_resolution(self):
        old = field('')
        old.update(manual_modified=True, manual_paths=['value'])
        merged = self.merged(old, field('新候选'))
        self.assertEqual(merged['value'], '')
        self.assertTrue(merged['manual_modified'])
        self.assertEqual(merged['candidates']['value']['value'], '新候选')
        adopted = save_revision({'x': merged}, {'x': merged}, {'x': {'value': 'adopt'}})['x']
        self.assertEqual(adopted['value'], '新候选')
        self.assertFalse(adopted['manual_modified'])

    def test_failed_attempt_does_not_erase_pending_manual_candidate(self):
        old = field('人工值')
        old.update(manual_modified=True, manual_paths=['value'], candidates={'value': {
            'value': '此前候选', 'recognition_status': 'extracted', 'source_file_id': 'same',
            'source_file': 'same.pdf', 'source_regions': field('')['source_regions']}})
        merged = self.merged(old, field(''))
        self.assertEqual(merged['value'], '人工值')
        self.assertEqual(merged['candidates']['value']['value'], '此前候选')
        self.assertEqual(merged['candidates']['value']['recognition_status'], 'previous_result')

    def test_pending_blank_candidate_survives_interrupted_reparse(self):
        old = field('人工值')
        candidate = field('', status='explicit_blank')
        old.update(manual_modified=True, manual_paths=['value'], candidates={'value': candidate})
        merged = self.merged(old, field(''))
        self.assertEqual(merged['candidates']['value']['value'], '')
        self.assertEqual(merged['candidates']['value']['previous_source'], candidate)
        self.assertEqual(merged['candidates']['value']['recognition_status'], 'previous_result')

    def test_semantic_error_does_not_expand_failed_page(self):
        diag = {'status': 'partial', 'failed_pages': [2], 'errors': [
            {'page': 2, 'stage': 'field_region', 'code': 'field_region_crossing', 'bbox_pdf': [200, 30, 500, 90]}]}
        self.assertEqual(self.merged(field('旧值'), field(''), diagnostics=diag)['value'], '')

    def test_subfields_use_their_own_regions_and_mark_mixed_current_sources(self):
        old = field('')
        old.update(field_type='group', sub_fields={'a': '旧A', 'b': '旧B'}, value_sources={
            'sub_fields.a': field('旧A'), 'sub_fields.b': field('旧B', bbox=(20, 200, 100, 240))})
        fresh = field('')
        fresh.update(field_type='group', sub_fields={'a': '', 'b': '新B'}, value_sources={
            'sub_fields.a': field(''), 'sub_fields.b': field('新B', bbox=(20, 200, 100, 240))})
        merged = self.merged(old, fresh)
        self.assertEqual(merged['sub_fields'], {'a': '旧A', 'b': '新B'})
        self.assertEqual(merged['value_sources']['sub_fields.a']['recognition_status'], 'previous_result')
        self.assertEqual(merged['value_sources']['sub_fields.b']['recognition_status'], 'extracted')
        self.assertEqual(merged['recognition_status'], 'mixed_sources')
        fresh['sub_fields']['b'] = ''
        fresh['value_sources']['sub_fields.b'] = field('', bbox=(20, 200, 100, 240))
        self.assertEqual(self.merged(old, fresh)['sub_fields'], {'a': '旧A', 'b': ''})


if __name__ == '__main__':
    unittest.main()
