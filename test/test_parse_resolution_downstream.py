import unittest
from copy import deepcopy
from unittest.mock import Mock
from agent.agent_backend.services.filing_parse_readiness import effective_parse_ready
from agent.agent_backend.services.filing_change_extraction_service import collect_facts, content_completeness
from agent.agent_backend.services.filing_change_review_orchestrator import FilingChangeReviewOrchestrator


class ResolutionDownstreamTests(unittest.TestCase):
    def source(self):
        return {'parse_status': 'partial', 'latest_attempt': {'content_status': 'partial'},
                'parse_resolution': {'revision': 1, 'manually_reviewed': True, 'unresolved_count': 0, 'original_status': 'partial'},
                'extracted_json': {'facts': [{'field': 'generic_name', 'status': 'available', 'source': {}}]}}

    def test_resolved_partial_is_current_without_rewriting_source(self):
        row = self.source()
        original = deepcopy(row)
        self.assertTrue(effective_parse_ready(row))
        self.assertEqual(content_completeness([row]), ([], []))
        fact = collect_facts([row])[0]
        self.assertEqual(fact['availability'], 'current')
        self.assertEqual(fact['parse_resolution']['original_status'], 'partial')
        self.assertEqual(row, original)

    def test_failed_new_attempt_or_unresolved_count_never_uses_old_confirmation(self):
        for state, remaining in [('failed', 0), ('pending', 0), ('running', 0), ('partial', 1)]:
            row = self.source()
            row['latest_attempt']['content_status'] = state
            row['parse_resolution']['unresolved_count'] = remaining
            self.assertFalse(effective_parse_ready(row))
            self.assertTrue(content_completeness([row])[1])
            self.assertEqual(collect_facts([row])[0]['availability'], 'previous_or_partial')

    def test_orchestrator_distinguishes_manual_resolution_from_original_partial(self):
        orchestrator = object.__new__(FilingChangeReviewOrchestrator)
        orchestrator.form_parser = Mock()
        orchestrator.form_parser.normalize_form_json.return_value = {}
        orchestrator.extraction = Mock()
        orchestrator.extraction.run.return_value = {'submissions': []}
        orchestrator.technical = Mock()
        orchestrator.technical.run.return_value = {}
        orchestrator.rule_review = Mock()
        orchestrator.evidence = Mock()
        orchestrator.evidence.collect.return_value = {}
        orchestrator.llm_gate = Mock()
        orchestrator.llm_gate.should_call_llm.return_value = {'allow': False}
        orchestrator.llm_gate.build_call_record.return_value = {}
        orchestrator.llm_reasoning = Mock()
        for resolved in (True, False):
            form = self.source()
            form['parse_resolution']['manually_reviewed'] = resolved
            review = {'formal_review': {'result': '通过', 'parse_issues': []}}
            orchestrator.rule_review.run.return_value = review
            result = orchestrator.run('p', form, [], [], [], {}, {})
            self.assertEqual(bool(review['formal_review']['parse_issues']), not resolved)
            self.assertEqual(review['formal_review']['parse_resolution']['application_form']['manually_reviewed'], resolved)
            self.assertIn('人工核对已完成' if resolved else '人工核对尚未完成', result['review_report_markdown'])
            self.assertIn('原始解析状态：partial', result['review_report_markdown'])
        orchestrator.llm_reasoning.summarize_overall.assert_not_called()


if __name__ == '__main__':
    unittest.main()
