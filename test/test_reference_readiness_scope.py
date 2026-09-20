import json
import threading
import unittest
from unittest.mock import patch
import test_filing_change_review_service_regression as fixture


class ReferenceScopeTests(unittest.TestCase):
    setUp = fixture.FilingChangeReviewServiceRegressionTest.setUp
    tearDown = fixture.FilingChangeReviewServiceRegressionTest.tearDown
    _add_project = fixture.FilingChangeReviewServiceRegressionTest._add_project

    def test_actual_word_report_does_not_claim_unused_references(self):
        from agent.agent_backend.services.filing_change_report_service import FilingChangeReportService
        from docx import Document
        result = FilingChangeReportService(self.root_dir).generate_report('synthetic', 'run',
            {'reference_usage': {'status': 'not_used'}, 'evidence_refs': []})
        phrase = '本轮未自动采用参考资料正文'
        self.assertIn(phrase, result['markdown'])
        document = Document(result['word_path'])
        self.assertTrue(any(phrase in paragraph.text for paragraph in document.paragraphs))

    def test_unused_reference_does_not_change_project_inputs_and_is_editable(self):
        self._add_project()
        before = self.service.get_parse_readiness('project-main')
        ok, message, uploaded = self.service.upload_reference_materials(
            [fixture._Upload('reference.txt', b'original reference')], 'guideline', 'extend_validity_period', {})
        self.assertTrue(ok, message)
        doc_id = uploaded['created'][0]['doc_id']
        after = self.service.get_parse_readiness('project-main')
        self.assertEqual(before['_input_identity'], after['_input_identity'])
        self.assertEqual(before['blocking_issues'], after['blocking_issues'])
        self.assertEqual(after['reference_usage']['status'], 'not_used')
        manifest = self.service._load_reference_manifest()
        manifest['doc_meta'][doc_id]['latest_attempt'] = {'content_status': 'partial', 'attempted_at': 'test'}
        self.service._save_reference_manifest(manifest)
        with self.connection.get_session() as session:
            session.query(fixture.FilingChangeReferenceMaterial).first().parse_status = 'partial'
            session.commit()
        chunks = [{'page': 1, 'status': 'partial', 'text': 'old', 'raw_text': 'old',
                   'words': [{'text': 'old', 'bbox': [2, 2, 8, 8]}], 'tables': [],
                   'errors': [{'code': 'ocr_coverage', 'bbox_pdf': [0, 0, 10, 10]}]}]
        path = self.root_dir / 'reference_materials' / 'parsed' / f'{doc_id}.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(chunks), encoding='utf8')
        context = self.service.get_parse_review('project-main', 'reference', doc_id)[2]
        ok, message, _ = self.service.save_parse_review('project-main', 'reference', doc_id,
            {'source_identity': context['source_identity'], 'expected_revision': 0,
             'items': [{'issue_key': context['issues'][0]['issue_key'], 'action': 'correct_text',
                        'text': 'corrected reference', 'reason': '核对原件'}]})
        self.assertTrue(ok, message)
        view = self.service.get_reference_material_parsed_markdown(doc_id)[2]
        self.assertIn('corrected reference', view['markdown'])
        self.assertEqual(json.loads(path.read_text()), chunks)

    def test_running_project_does_not_hold_unused_reference_lock(self):
        self._add_project()
        entered, release, acquired = threading.Event(), threading.Event(), threading.Event()
        results = []
        def slow(*args):
            entered.set()
            release.wait(5)
            return True, 'success', {}
        def reference_work():
            with self.service._reference_manifest_lock():
                acquired.set()
        identity = {'ready': True, '_input_identity': [{'source': 'synthetic'}]}
        with patch.object(self.service, 'get_parse_readiness', return_value=identity), patch.object(self.service, '_run_review_once', side_effect=slow):
            ok, message, started = self.service.prepare_review_run('project-main')
            self.assertTrue(ok, message)
            worker = threading.Thread(target=lambda: results.append(self.service.execute_review_run('project-main', started['run_id'])))
            other = threading.Thread(target=reference_work)
            try:
                worker.start()
                self.assertTrue(entered.wait(2))
                other.start()
                self.assertTrue(acquired.wait(1), '无关参考资料被长审评全局锁阻塞')
            finally:
                release.set()
                worker.join(5)
                if other.ident is not None:
                    other.join(5)
            self.assertTrue(results[0][0])


if __name__ == '__main__':
    unittest.main()
