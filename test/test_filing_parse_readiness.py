import unittest
from copy import deepcopy
from agent.agent_backend.services.filing_parse_readiness import source_issues, readiness_result


class ReadinessTests(unittest.TestCase):
    def test_old_content_does_not_override_failed_new_attempt(self):
        source = {'doc_id': 'd1', 'parse_status': 'success', 'raw_text': 'previous valid content',
                  'latest_attempt': {'content_status': 'failed', 'task_id': 'new'}}
        issues = source_issues(source, 'submission')
        self.assertFalse(readiness_result(issues)['ready'])
        self.assertEqual(issues[0]['source_attempt']['task_id'], 'new')

    def test_pending_unknown_and_missing_are_blocked(self):
        for status in ('pending', 'running', 'not_parsed', 'unexpected', ''):
            self.assertTrue(source_issues({'parse_status': status}, 'application_form'))

    def test_partial_without_details_cannot_be_considered_complete(self):
        self.assertEqual(source_issues({'parse_status': 'partial'}, 'submission')[0]['code'], 'parse_incomplete')

    def test_page_region_and_attempt_evidence_are_preserved(self):
        source = {'doc_id': 'd1', 'parse_status': 'partial', 'latest_attempt': {'attempted_at': 't1'}}
        chunks = [{'page': 3, 'errors': [{'code': 'ocr_empty', 'bbox_pdf': [1, 2, 3, 4]}]}]
        original = deepcopy(chunks)
        issue = source_issues(source, 'submission', chunks)[0]
        self.assertEqual(issue['page'], 3)
        self.assertEqual(issue['bbox_pdf'], [1, 2, 3, 4])
        self.assertEqual(issue['source_attempt']['attempted_at'], 't1')
        self.assertEqual(chunks, original)

    def test_success_label_cannot_hide_unresolved_table(self):
        chunks = [{'page': 1, 'tables': [{'needs_review': True, 'bbox_pdf': [0, 0, 10, 10]}]}]
        self.assertTrue(source_issues({'parse_status': 'success'}, 'submission', chunks))

    def test_clean_source_is_ready(self):
        self.assertTrue(readiness_result(source_issues({'parse_status': 'success'}, 'submission'))['ready'])

    def test_page_error_does_not_hide_other_global_error(self):
        source = {'parse_status': 'partial', 'parse_diagnostics': {'errors': [
            {'page': 2, 'code': 'ocr_empty', 'reason': 'no text'}]}}
        chunks = [{'page': 1, 'errors': [{'code': 'ocr_quality', 'reason': 'low confidence'}]}]
        issues = source_issues(source, 'submission', chunks)
        self.assertEqual({i['page'] for i in issues}, {1, 2})
        # 确认第1页不能同时消除第2页的未识别问题。
        self.assertFalse(readiness_result([i for i in issues if i['page'] != 1])['ready'])

    def test_global_copy_of_same_page_error_is_not_duplicated(self):
        error = {'code': 'ocr_quality', 'reason': 'low confidence', 'bbox_pdf': [1, 2, 3, 4]}
        source = {'parse_status': 'partial', 'parse_diagnostics': {
            'errors': [{**error, 'page': 1, 'message': 'low confidence'}]}}
        issues = source_issues(source, 'submission', [{'page': 1, 'errors': [error]}])
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]['issue_key'], 'chunk:0:error:0')

    def test_missing_page_stays_blocked_beside_existing_error(self):
        source = {'parse_status': 'partial', 'parse_diagnostics': {'missing_pages': [3], 'failed_pages': [1, 3]}}
        chunks = [{'page': 1, 'errors': [{'code': 'ocr_quality'}]}]
        issues = source_issues(source, 'submission', chunks)
        self.assertEqual({i['page'] for i in issues}, {1, 3})
        self.assertTrue(any(i['code'] == 'page_incomplete' for i in issues if i['page'] == 3))


if __name__ == '__main__':
    unittest.main()
