import unittest
from copy import deepcopy
from agent.agent_backend.services.filing_parse_readiness import source_issues
from agent.agent_backend.services.filing_numeric_revision import resolved_numeric_issues


class NumericReadinessTests(unittest.TestCase):
    def fixture(self):
        check = {'status': 'numeric_uncertain', 'primary': '1.2', 'secondary': '12', 'bbox_pdf': [1, 1, 5, 5]}
        table = {'page': 1, 'bbox_pdf': [0, 0, 10, 10], 'rows': [['1.2']], 'markdown': '|1.2|',
                 'needs_review': True, 'review_reasons': ['numeric_uncertain'],
                 'cells': [{'row': 0, 'column': 0, 'text': '1.2', 'numeric_status': 'numeric_uncertain',
                            'numeric_verification': [deepcopy(check)]}]}
        chunks = [{'page': 1, 'text': '|1.2|', 'tables': [table], 'errors': [
            {'code': 'numeric_uncertain', 'numeric_verification': [check], 'bbox_pdf': [0, 0, 10, 10]}]}]
        meta = {'parse_status': 'partial', 'parse_attempts': [{'attempted_at': 'a1', 'content_status': 'partial'}],
                'numeric_revision': {'source_attempt': 'a1', 'revision': 1, 'items': [
                    {'key': '0:0:0', 'original_text': '1.2', 'value': '1.2', 'reason': 'checked original'}]}}
        return chunks, meta

    def test_only_matching_numeric_evidence_resolves_without_mutation(self):
        chunks, meta = self.fixture()
        original = deepcopy(chunks)
        issues = source_issues(meta, 'submission', chunks)
        self.assertEqual(resolved_numeric_issues(chunks, issues, meta), {i['issue_key'] for i in issues})
        self.assertEqual(chunks, original)

    def test_text_and_unknown_structure_remain_blocking(self):
        chunks, meta = self.fixture()
        chunks[0]['errors'].append({'code': 'ocr_coverage', 'bbox_pdf': [0, 0, 10, 10]})
        chunks[0]['tables'][0]['review_reasons'].append('cell_assignment')
        issues = source_issues(meta, 'submission', chunks)
        resolved = resolved_numeric_issues(chunks, issues, meta)
        self.assertEqual(resolved, {'chunk:0:error:0'})
        self.assertEqual(len([i for i in issues if i['issue_key'] not in resolved]), 2)

    def test_stale_attempt_and_changed_original_cannot_resolve(self):
        for stale in (True, False):
            chunks, meta = self.fixture()
            if stale:
                meta['numeric_revision']['source_attempt'] = 'old'
            else:
                meta['numeric_revision']['items'][0]['original_text'] = 'changed'
            self.assertEqual(resolved_numeric_issues(chunks, source_issues(meta, 'submission', chunks), meta), set())


if __name__ == '__main__':
    unittest.main()
