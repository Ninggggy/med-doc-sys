"""历史资料变化提示：不改历史结论，不将旧结果误作当前通过。"""
import json
import unittest
from unittest.mock import patch
from test_filing_change_review_service_regression import FilingChangeReviewServiceRegressionTest as Fixture, FilingChangeReviewRun


class ReviewInputStateTests(unittest.TestCase):
    setUp = Fixture.setUp
    tearDown = Fixture.tearDown
    _add_project = Fixture._add_project
    _add_run_and_result = Fixture._add_run_and_result

    def test_completed_run_retains_identity_and_report_state(self):
        from test_filing_change_review_service_regression import _RuleList, _ReviewOrchestrator, _ReportFileWriter
        self._add_project()
        identity = [{'kind': 'application_form', 'file_id': 'synthetic-current'}]
        self.service.rule_service = _RuleList()
        self.service.orchestrator = _ReviewOrchestrator()
        self.service.report_service = _ReportFileWriter(self.root_dir)
        with patch.object(self.service, 'get_parse_readiness', return_value={'ready': True, '_input_identity': identity}):
            ok, message, started = self.service.prepare_review_run('project-main')
            self.assertTrue(ok, message)
            ok, message, result = self.service.execute_review_run('project-main', started['run_id'])
            self.assertTrue(ok, message)
            self.assertNotIn('input_identity', result)
            report = self.service.get_project_latest_report('project-main', started['run_id'])[2]
            self.assertEqual(report['input_state']['status'], 'current')
        with self.connection.get_session() as session:
            saved = json.loads(session.query(FilingChangeReviewRun).first().result_json)
            self.assertEqual(saved['input_identity'], identity)
        with patch.object(self.service, 'get_parse_readiness', return_value={'ready': True, '_input_identity': []}):
            changed = self.service.get_project_latest_report('project-main', started['run_id'])[2]
            self.assertEqual(changed['input_state']['status'], 'stale')
            self.assertEqual(changed['report_content'], report['report_content'])

    def test_current_changed_unavailable_and_legacy_result(self):
        self._add_project()
        self._add_run_and_result('project-main')
        result = self.service.get_run_result('run-main')[2]
        self.assertEqual(result['input_state']['status'], 'unknown')
        identity = [{'kind': 'application_form', 'file_id': 'synthetic-1'}]
        with self.connection.get_session() as session:
            run = session.query(FilingChangeReviewRun).first()
            saved = {'run_id': 'run-main', 'input_identity': identity}
            run.result_json = json.dumps(saved)
            session.commit()
        for current, expected in [({'ready': True, '_input_identity': identity}, 'current'),
                                  ({'ready': True, '_input_identity': [{'file_id': 'synthetic-2'}]}, 'stale'),
                                  ({'ready': False, '_input_identity': identity}, 'stale')]:
            with patch.object(self.service, 'get_parse_readiness', return_value=current):
                ok, _, result = self.service.get_run_result('run-main')
                self.assertTrue(ok)
                self.assertEqual(result['input_state']['status'], expected)
                self.assertNotIn('input_identity', result)
                self.assertTrue(result['review_complete'])
        with patch.object(self.service, 'get_parse_readiness', side_effect=OSError('synthetic sensitive detail')):
            state = self.service.get_run_result('run-main')[2]['input_state']
            self.assertEqual(state['status'], 'unknown')
            self.assertNotIn('sensitive', str(state))
        with self.connection.get_session() as session:
            self.assertEqual(json.loads(session.query(FilingChangeReviewRun).first().result_json), saved)


if __name__ == '__main__':
    unittest.main()
