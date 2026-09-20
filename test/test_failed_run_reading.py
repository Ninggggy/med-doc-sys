import json
from unittest.mock import Mock, patch
from flask import Flask
from test_audit_review_persistence import ReviewPersistenceAudit
from agent.agent_backend.database.mysql.db_model import FilingChangeReviewRun, FilingChangeProject, FilingChangeReviewReport


class FailedRunReadingTests(ReviewPersistenceAudit):
    def prepare_failure(self):
        self.test_context_failure_is_persisted_and_old_result_is_unchanged()
        with self.connection.get_session() as session:
            row = session.query(FilingChangeReviewRun).filter_by(status='failed').one()
            return row.run_id

    def test_partial_failure_is_readable_and_readonly(self):
        run_id = self.prepare_failure()
        ok, message, result = self.service.get_run_result(run_id)
        self.assertTrue(ok, message)
        self.assertEqual(result['execution_status'], 'failed')
        self.assertFalse(result['review_complete'])
        self.assertEqual(result['quality_standard_check']['result'], '需人工确认')
        self.assertIn('complete_report', result['incomplete_stages'])
        self.assertFalse(self.service.manual_confirm_run(run_id, {'confirmed':True})[0])
        self.assertFalse(self.service.generate_report(run_id)[0])

    def test_failure_without_partial_result_is_not_not_found(self):
        run_id = self.prepare_failure()
        with self.connection.get_session() as session:
            row = session.query(FilingChangeReviewRun).filter_by(run_id=run_id).one()
            row.result_json = '{}'; row.error_message = 'SECRET historical server response'; session.commit()
        ok, message, result = self.service.get_run_result(run_id)
        self.assertTrue(ok, message)
        self.assertFalse(result['review_complete'])
        self.assertEqual(result['completed_stages'], [])
        self.assertNotIn('SECRET', json.dumps(result))

    def test_deleted_project_still_cannot_read_failure(self):
        run_id = self.prepare_failure()
        with self.connection.get_session() as session:
            session.query(FilingChangeProject).filter_by(project_id='synthetic-budget').one().deleted=True
            session.commit()
        self.assertFalse(self.service.get_run_result(run_id)[0])

    def test_failed_run_cannot_preview_residual_complete_report(self):
        failed_id = self.prepare_failure()
        with self.connection.get_session() as session:
            report = session.query(FilingChangeReviewReport).one()
            previous_run = report.run_id
            report.run_id = failed_id
            report.report_content = 'STALE_COMPLETE_REPORT'
            report_id = report.report_id
            session.commit()
        for run_id in (failed_id, ''):
            with self.subTest(run_id=run_id):
                ok, message, result = self.service.get_project_latest_report('synthetic-budget', run_id)
                self.assertFalse(ok)
                self.assertIsNone(result)
                self.assertNotIn('STALE_COMPLETE_REPORT', message)
        self.assertFalse(self.service.export_report_word(report_id)[0])
        from agent.agent_backend.controller import filing_change_review_controller as controller
        app = Flask('failed-report-http')
        app.register_blueprint(controller.filing_change_review_bp)
        with patch.object(controller, 'get_service', return_value=self.service), app.test_client() as client:
            for query in ({}, {'run_id': failed_id}):
                response = client.get('/filing-change-review/projects/synthetic-budget/report', query_string=query)
                self.assertEqual(response.status_code, 404)
                self.assertNotIn('STALE_COMPLETE_REPORT', response.get_data(as_text=True))
        # 只限制未完成轮次，不删除或改写既有报告；恢复原归属后仍可读取。
        with self.connection.get_session() as session:
            report = session.query(FilingChangeReviewReport).one()
            report.run_id = previous_run
            session.commit()
        ok, _, result = self.service.get_project_latest_report('synthetic-budget', previous_run)
        self.assertTrue(ok)
        self.assertEqual(result['report_content'], 'STALE_COMPLETE_REPORT')
        with patch.object(controller, 'get_service', return_value=self.service), app.test_client() as client:
            response = client.get('/filing-change-review/projects/synthetic-budget/report', query_string={'run_id': previous_run})
            self.assertEqual(response.status_code, 200)
            self.assertIn('STALE_COMPLETE_REPORT', response.get_data(as_text=True))

    def test_completed_flag_cannot_override_explicit_incomplete_result(self):
        self.prepare_failure()
        with self.connection.get_session() as session:
            row = session.query(FilingChangeReviewRun).filter_by(status='completed').one()
            run_id = row.run_id
            stored = json.loads(row.result_json)
            stored['review_complete'] = False
            row.result_json = json.dumps(stored)
            session.commit()
        self.assertFalse(self.service.get_project_latest_report('synthetic-budget', run_id)[0])
