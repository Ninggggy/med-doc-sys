import json
import unittest
from pathlib import Path
from datetime import datetime
from copy import deepcopy
import test_filing_change_review_service_regression as fixture
import test_numeric_parse_readiness as numeric_fixture
from agent.agent_backend.services.filing_parse_resolution import apply_compatible_resolutions
from agent.agent_backend.services.filing_parse_readiness import source_issues


class CombinedRevisionTests(unittest.TestCase):
    setUp = fixture.FilingChangeReviewServiceRegressionTest.setUp
    tearDown = fixture.FilingChangeReviewServiceRegressionTest.tearDown
    _add_project = fixture.FilingChangeReviewServiceRegressionTest._add_project
    _add_submission = fixture.FilingChangeReviewServiceRegressionTest._add_submission

    def test_real_stamp_conflict_confirmation_does_not_dismiss_coverage(self):
        # 固定的合成印章真实OCR输出；不是测试人员原件，也不在本例重新调用OCR。
        source = Path(__file__).parent / 'pdf_integrity_20260919' / 'deskew-production' / 'stamp_outside_table.json'
        chunks = json.loads(source.read_text())
        self._add_project()
        self._add_submission('project-main', 'stamp', created_at=datetime.now(), parse_status='partial')
        manifest = self.service._load_submission_manifest('project-main')
        manifest['doc_meta']['stamp'] = {'parse_status':'partial', 'parse_attempts':[
            {'attempted_at':'synthetic-stamp-attempt', 'content_status':'partial'}]}
        self.service._save_submission_manifest('project-main', manifest)
        path = self.service._project_path('project-main') / 'parsed' / 'stamp.json'
        path.write_text(json.dumps(chunks))
        original = path.read_bytes()
        from agent.agent_backend.services.filing_numeric_revision import numeric_cells
        cells = numeric_cells(chunks)
        self.assertEqual(len(cells),1)
        self.assertEqual(cells[0]['original_text'],'1.25')
        self.assertEqual(cells[0]['candidates'][0]['secondary'],'-1.25')
        before = self.service.get_parse_readiness('project-main')
        self.assertTrue(any(i.get('doc_id')=='stamp' and i['code']=='numeric_uncertain' for i in before['blocking_issues']))
        ok, message, _ = self.service.confirm_submission_numeric_cells('project-main','stamp',{
            'source_attempt':'synthetic-stamp-attempt','expected_revision':0,
            'items':[{'key':cells[0]['key'],'original_text':'1.25','value':'-1.25',
                      'reason':'逐字核对合成原图中负号，不按置信度自动择值'}]})
        self.assertTrue(ok,message)
        effective = self.service._load_parsed_submission_map('project-main',['stamp'])['stamp']
        self.assertEqual(effective[0]['tables'][0]['rows'][3][0],'-1.25')
        after = self.service.get_parse_readiness('project-main')
        remaining = [i for i in after['blocking_issues'] if i.get('doc_id')=='stamp']
        self.assertFalse(any(i['code']=='numeric_uncertain' for i in remaining))
        self.assertTrue(any(i['code']=='ocr_coverage' for i in remaining))
        self.assertFalse(after['ready'])
        self.assertEqual(path.read_bytes(),original)

        # 完整表格修订前先撤销独立数值确认，避免两种覆盖互相覆盖。
        self.assertTrue(self.service.confirm_submission_numeric_cells('project-main', 'stamp', {
            'source_attempt': 'synthetic-stamp-attempt', 'expected_revision': 1, 'items': []})[0])
        context = self.service.get_parse_review('project-main', 'submission', 'stamp')[2]
        coverage = next(i for i in context['issues'] if i['code'] == 'ocr_coverage')
        cells = [{k: cell[k] for k in ('row', 'column', 'rowspan', 'colspan', 'text')}
                 for cell in chunks[0]['tables'][0]['cells']]
        next(c for c in cells if c['row'] == 3 and c['column'] == 0)['text'] = '-1.25'
        ok, message, _ = self.service.save_parse_review('project-main', 'submission', 'stamp', {
            'source_identity': context['source_identity'], 'expected_revision': context['revision'],
            'items': [{'issue_key': coverage['issue_key'], 'action': 'correct_table',
                       'reason': '逐格核对合成原表全部内容，包括负号和表外印章文字',
                       'table': {'row_count': 4, 'column_count': 3, 'cells': cells},
                       'outside_text': 'Gradient elution\n* Range: 0.5-2.0 mg/mL; 95%; note 1.\nCOPY',
                       'outside_text_verified': True}]})
        self.assertTrue(ok, message)
        after = self.service.get_parse_readiness('project-main')
        self.assertFalse([i for i in after['blocking_issues'] if i.get('doc_id') == 'stamp'])
        updated = self.service._load_parsed_submission_map('project-main', ['stamp'])['stamp']
        self.assertEqual(updated[0]['tables'][0]['rows'][3][0], '-1.25')
        self.assertIn('COPY', updated[0]['text'])
        self.assertEqual(path.read_bytes(), original)

    def test_numeric_display_execution_and_structure_conflict(self):
        self._add_project()
        self._add_submission('project-main', 'doc', created_at=datetime.now(), parse_status='partial')
        chunks, meta = numeric_fixture.NumericReadinessTests().fixture()
        chunks[0]['words'] = [{'text': '1.2', 'bbox': [1, 1, 5, 5]}]
        numeric = meta.pop('numeric_revision')['items']
        numeric[0]['value'] = '12'
        manifest = self.service._load_submission_manifest('project-main')
        manifest['doc_meta']['doc'] = meta
        self.service._save_submission_manifest('project-main', manifest)
        path = self.service._project_path('project-main') / 'parsed' / 'doc.json'
        path.write_text(json.dumps(chunks), encoding='utf8')
        payload = {'source_attempt': 'a1', 'expected_revision': 0, 'items': numeric}
        ok, message, _ = self.service.confirm_submission_numeric_cells('project-main', 'doc', payload)
        self.assertTrue(ok, message)
        display = self.service.get_submission_parsed_markdown('project-main', 'doc')[2]
        execution = self.service._load_parsed_submission_map('project-main', ['doc'])['doc']
        self.assertEqual(display['parsed_chunks'], execution)
        self.assertEqual(execution[0]['tables'][0]['rows'], [['12']])
        context = self.service.get_parse_review('project-main', 'submission', 'doc')[2]
        self.assertEqual(context['effective_chunks'], execution)
        issue = next(i for i in context['issues'] if i['code'] == 'table_review_required')
        generic = {'source_identity': context['source_identity'], 'expected_revision': 0,
                   'items': [{'issue_key': issue['issue_key'], 'action': 'correct_table', 'reason': '原表核对',
                              'table': {'row_count': 1, 'column_count': 1,
                                        'cells': [{'row': 0, 'column': 0, 'text': '99'}]}}]}
        ok, message, _ = self.service.save_parse_review('project-main', 'submission', 'doc', generic)
        self.assertFalse(ok)
        self.assertIn('不能相互覆盖', message)
        self.assertTrue(self.service.confirm_submission_numeric_cells('project-main', 'doc',
            {**payload, 'expected_revision': 1, 'items': []})[0])
        ok, message, _ = self.service.save_parse_review('project-main', 'submission', 'doc', generic)
        self.assertTrue(ok, message)
        ok, message, _ = self.service.confirm_submission_numeric_cells('project-main', 'doc',
            {**payload, 'expected_revision': 2})
        self.assertFalse(ok)
        self.assertIn('不能相互覆盖', message)
        self.assertEqual(json.loads(path.read_text()), chunks)
        # 模拟旧版本留下的重叠修订，读取必须可恢复且就绪检查明确阻塞。
        manifest = self.service._load_submission_manifest('project-main')
        manifest['doc_meta']['doc']['numeric_revision']['items'] = numeric
        self.service._save_submission_manifest('project-main', manifest)
        readiness = self.service.get_parse_readiness('project-main')
        self.assertTrue(any(i['code'] == 'parse_revision_conflict' for i in readiness['blocking_issues']))
        display = self.service.get_submission_parsed_markdown('project-main', 'doc')[2]
        self.assertIn('原始解析', display['revision_warning'])
        self.assertEqual(display['parsed_chunks'], chunks)
        self.assertTrue(self.service.confirm_submission_numeric_cells('project-main', 'doc',
            {**payload, 'expected_revision': 2, 'items': []})[0])

    def test_disjoint_text_correction_keeps_numeric_value(self):
        chunks, meta = numeric_fixture.NumericReadinessTests().fixture()
        chunks[0]['words'] = [{'text': 'old', 'bbox': [20, 20, 30, 30]}]
        chunks[0]['errors'].append({'code': 'ocr_coverage', 'bbox_pdf': [19, 19, 31, 31]})
        original = deepcopy(chunks)
        numeric = meta['numeric_revision']['items']
        numeric[0]['value'] = '12'
        corrected, _, _ = apply_compatible_resolutions(chunks, source_issues(meta, 'submission', chunks),
            [{'issue_key': 'chunk:0:error:1', 'action': 'correct_text', 'text': 'new', 'reason': 'checked'}], numeric)
        self.assertEqual(corrected[0]['tables'][0]['rows'], [['12']])
        self.assertIn('12', corrected[0]['text'])
        self.assertIn('new', corrected[0]['text'])
        self.assertEqual(chunks, original)


if __name__ == '__main__':
    unittest.main()
