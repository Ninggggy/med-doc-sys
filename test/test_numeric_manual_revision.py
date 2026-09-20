import copy
import tempfile
import unittest
from agent.agent_backend.services.filing_numeric_revision import numeric_cells, apply_confirmations
from test_numeric_uncertainty_persistence import parsed_page
import test_filing_change_review_service_regression as fixture
from pathlib import Path


class ManualNumericRevisionTests(unittest.TestCase):
    def test_report_keeps_manual_confirmation_even_when_value_is_within_limit(self):
        from agent.agent_backend.services.filing_change_review_service import FilingChangeReviewService
        from agent.agent_backend.services.filing_change_stability_service import FilingChangeStabilityService
        from agent.agent_backend.services.filing_change_report_service import FilingChangeReportService
        chunks = [parsed_page()]
        item = {**numeric_cells(chunks)[0], 'value': '-1.25', 'reason': '人工核对原页负号'}
        service = object.__new__(FilingChangeReviewService)
        extracted = service._extract_structured_payload('稳定性资料.pdf', apply_confirmations(chunks, [item]))
        analysis = FilingChangeStabilityService().run({}, [{'file_name': '稳定性资料.pdf',
            'parse_status': 'partial', 'extracted_json': extracted}])
        report = object.__new__(FilingChangeReportService)._build_markdown('p', 'r', {'stability_trend_analysis': analysis}, 'now')
        self.assertIn('人工数值确认', report)
        self.assertIn('人工核对原页负号', report)
        self.assertIn('manual_confirmed', report)
        self.assertIn('1.25', report)
        self.assertIn('-1.25', report)
        # 实际导出后重读Markdown与Word，不能仅证明字符串拼接通过。
        from docx import Document
        with tempfile.TemporaryDirectory(prefix='numeric-report-') as folder:
            generated = FilingChangeReportService(Path(folder)).generate_report('p', 'r',
                {'stability_trend_analysis': analysis})
            saved = Path(generated['markdown_path']).read_text()
            word = '\n'.join(p.text for p in Document(generated['word_path']).paragraphs)
            for text in (saved, word):
                self.assertIn('人工核对原页负号', text)
                self.assertIn('manual_confirmed', text)
                self.assertIn('-1.25', text)

    def test_only_confirmed_copy_enters_new_limit_analysis(self):
        from agent.agent_backend.services.filing_change_review_service import FilingChangeReviewService
        from agent.agent_backend.services.filing_change_stability_service import FilingChangeStabilityService
        chunks = [parsed_page()]
        item = {**numeric_cells(chunks)[0], 'value': '-1.25', 'reason': '对照原页'}
        service = object.__new__(FilingChangeReviewService)
        def analyze(source):
            extracted = service._extract_structured_payload('稳定性资料.pdf', source)
            return FilingChangeStabilityService().run({}, [{'file_name': '稳定性资料.pdf',
                'parse_status': 'partial', 'extracted_json': extracted}])['limit_check']
        old = analyze(chunks)
        new = analyze(apply_confirmations(chunks, [item]))
        self.assertEqual(old['undecidable_count'], 1)
        self.assertEqual(new['undecidable_count'], 0)
        self.assertEqual(new['out_of_spec_count'], 1)
        self.assertEqual(analyze(chunks), old)

    def test_confirmation_is_copy_only_and_keeps_ocr_evidence(self):
        chunks = [parsed_page()]
        original = copy.deepcopy(chunks)
        selected = numeric_cells(chunks)[0]
        item = {**selected, 'value': '-1.25', 'reason': '已对照原页负号'}
        corrected = apply_confirmations(chunks, [item])
        self.assertEqual(chunks, original)
        ci, ti, si = map(int, selected['key'].split(':'))
        cell = corrected[ci]['tables'][ti]['cells'][si]
        self.assertEqual(cell['text'], '-1.25')
        self.assertEqual(cell['numeric_status'], 'manual_confirmed')
        self.assertEqual(cell['numeric_verification'][0]['ocr_status'], 'numeric_uncertain')
        self.assertEqual(corrected[ci]['errors'], original[ci]['errors'])
        self.assertIn('-1.25', corrected[ci]['tables'][ti]['markdown'])

    def test_stale_duplicate_unknown_or_unreasoned_correction_rejected(self):
        chunks = [parsed_page()]
        item = {**numeric_cells(chunks)[0], 'value': '-1.25', 'reason': '核对原页'}
        for corrections in ([{**item, 'key': '-1:0:0'}], [item, item],
                            [{**item, 'original_text': 'stale'}], [{**item, 'reason': ''}],
                            [{**item, 'value': ''}], {'key': item['key']}):
            with self.subTest(corrections=corrections), self.assertRaises(ValueError):
                apply_confirmations(chunks, corrections)


class NumericConfirmationPersistenceTests(unittest.TestCase):
    setUp = fixture.FilingChangeReviewServiceRegressionTest.setUp
    tearDown = fixture.FilingChangeReviewServiceRegressionTest.tearDown
    _add_project = fixture.FilingChangeReviewServiceRegressionTest._add_project

    def test_original_download_is_exact_and_rejects_foreign_deleted_or_outside_path(self):
        from flask import Flask
        from unittest.mock import patch
        from agent.agent_backend.controller import filing_change_review_controller as controller
        from agent.agent_backend.database.mysql.db_model import FilingChangeProject, FilingChangeSubmissionFile
        self.prepare()
        app = Flask('numeric-original')
        app.register_blueprint(controller.filing_change_review_bp)
        with patch.object(controller, 'get_service', return_value=self.service), app.test_client() as client:
            url = f'/filing-change-review/projects/{self.pid}/submissions/{self.doc}/original'
            response = client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.data, b'synthetic')
            self.assertIn('attachment', response.headers['Content-Disposition'])
            self.assertIn('no-store', response.headers['Cache-Control'])
            response.close()
            self.assertEqual(client.get(url.replace(self.pid, 'foreign')).status_code, 404)
            with self.service.db_conn.get_session() as session:
                row = session.query(FilingChangeSubmissionFile).filter_by(doc_id=self.doc).first()
                original_path = row.storage_path
                row.storage_path = str(self.root_dir / 'outside.txt')
                Path(row.storage_path).write_bytes(b'SHOULD_NOT_DOWNLOAD')
                session.commit()
            self.assertEqual(client.get(url).status_code, 404)
            with self.service.db_conn.get_session() as session:
                session.query(FilingChangeSubmissionFile).filter_by(doc_id=self.doc).first().storage_path = original_path
                session.query(FilingChangeProject).filter_by(project_id=self.pid).first().deleted = True
                session.commit()
            self.assertEqual(client.get(url).status_code, 404)

    def prepare(self):
        self.pid = 'numeric-confirmation'
        self._add_project(self.pid)
        self.assertTrue(self.service.upload_submission_files(self.pid, [fixture._Upload('稳定性资料.txt', b'synthetic')], '5')[0])
        self.doc = self.service.list_submissions(self.pid, {})['list'][0]['doc_id']
        self.service.material_service = fixture._MaterialParser([parsed_page()])
        self.assertTrue(self.service.parse_submission(self.pid, self.doc)[0])
        view = self.service.get_submission_parsed_markdown(self.pid, self.doc)[2]['numeric_review']
        self.payload = {'source_attempt': view['source_attempt'], 'expected_revision': view['revision'],
                        'items': [{**view['cells'][0], 'value': '-1.25', 'reason': '核对原页'}]}

    def test_save_snapshot_conflicts_and_reparse_invalidates_old_confirmation(self):
        self.prepare()
        path = self.service._project_path(self.pid) / 'parsed' / f'{self.doc}.json'
        original = path.read_bytes()
        self.assertTrue(self.service.confirm_submission_numeric_cells(self.pid, self.doc, self.payload)[0])
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.service.get_submission_parsed_markdown(self.pid, self.doc)[2]['numeric_review']['revision'], 1)
        self.assertFalse(self.service.confirm_submission_numeric_cells(self.pid, self.doc, self.payload)[0])
        new = self.service._load_parsed_submission_map(self.pid, [self.doc])[self.doc]
        self.assertIn('-1.25', new[0]['tables'][0]['markdown'])
        self.assertTrue(self.service.parse_submission(self.pid, self.doc)[0])
        view = self.service.get_submission_parsed_markdown(self.pid, self.doc)[2]['numeric_review']
        self.assertTrue(view['stale'])
        self.assertEqual(view['items'], [])
        current = self.service._load_parsed_submission_map(self.pid, [self.doc])[self.doc]
        self.assertEqual(current[0]['tables'][0]['rows'][1][2], '1.25')

    def test_http_rejects_foreign_project_and_revision_conflict(self):
        from flask import Flask
        from unittest.mock import patch
        from agent.agent_backend.controller import filing_change_review_controller as controller
        self.prepare()
        app = Flask('numeric-confirmation')
        app.register_blueprint(controller.filing_change_review_bp)
        with patch.object(controller, 'get_service', return_value=self.service), app.test_client() as client:
            url = f'/filing-change-review/projects/{self.pid}/submissions/{self.doc}/numeric-confirmations'
            self.assertEqual(client.post(url.replace(self.pid, 'foreign'), json=self.payload).status_code, 404)
            self.assertEqual(client.post(url, json=self.payload).status_code, 200)
            self.assertEqual(client.post(url, json=self.payload).status_code, 409)
