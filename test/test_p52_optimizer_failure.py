import unittest
from unittest.mock import Mock

from agent.agent_backend.agents.review.p52_feedback_optimizer_agent import P52FeedbackOptimizerAgent
from agent.agent_backend.services.p52_feedback_optimize_orchestrator import P52FeedbackOptimizeOrchestrator
from agent.agent_backend.llm.errors import LLMExecutionError


class P52OptimizerFailureTests(unittest.TestCase):
    def agent(self, parsed):
        agent = P52FeedbackOptimizerAgent.__new__(P52FeedbackOptimizerAgent)
        agent.llm = Mock()
        agent.llm.chat.return_value = 'PRIVATE_MODEL_OUTPUT'
        agent.llm.extract_json.return_value = parsed
        agent.prompts = Mock()
        agent.envelopes = Mock()
        agent.rule_index = {}
        agent._build_prompt_payload = Mock(return_value={})
        agent._fallback_result = Mock(return_value={'summary': 'default', 'patches': [{'patch_type': 'rule_patch'}]})
        return agent

    def test_invalid_output_never_becomes_patch(self):
        for output in (None, {}, {'summary': 'x', 'patches': 'invalid'},
                       {'summary': 'x', 'patches': [{}]}):
            with self.subTest(output=output), self.assertRaises(LLMExecutionError):
                self.agent(output).propose({'section_id': '3.2.p.5.2'})

    def test_valid_empty_patch_is_not_replaced_by_default(self):
        result = self.agent({'summary': '不需要修改', 'patches': []}).propose({})
        self.assertEqual(result['patches'], [])
        self.assertEqual(result['llm_execution']['raw_preview'], '')
        self.assertNotIn('PRIVATE_MODEL_OUTPUT', str(result))

    def test_orchestrator_preserves_empty_patch(self):
        flow = P52FeedbackOptimizeOrchestrator.__new__(P52FeedbackOptimizeOrchestrator)
        flow._build_candidate_patches = Mock(return_value=[{'fabricated': True}])
        result = flow._normalize_optimizer_patches(run_id='r', feedback_record={}, trace_snapshot={},
            classification={}, localizations=[], optimizer_patches=[])
        self.assertEqual(result, [])
        flow._build_candidate_patches.assert_not_called()

    def test_valid_candidate_keeps_supplied_overlay(self):
        patch = {'patch_type': 'reviewer_patch', 'target_agent': 'p52_reviewer',
                 'patch_content': '核对证据来源', 'overlay': {'prompt_suffixes': {'reviewer': ['核对证据来源']}}}
        result = self.agent({'summary': '候选建议', 'patches': [patch]}).propose({})
        self.assertEqual(result['patches'][0]['overlay'], patch['overlay'])
        self.assertEqual(result['patches'][0]['status'], 'candidate')

    def test_failure_stops_before_patch_persistence(self):
        flow = P52FeedbackOptimizeOrchestrator.__new__(P52FeedbackOptimizeOrchestrator)
        flow.service = Mock()
        flow.service.p52_feedback_optimizer_agent = self.agent(None)
        flow.classifier = Mock()
        flow.classifier.classify.return_value = {'error_types': []}
        flow.registry = Mock()
        with self.assertRaises(LLMExecutionError):
            flow.optimize(run_id='r', section_id='3.2.p.5.2', feedback_record={}, run_trace={}, run_context={})
        flow.registry.create_patches.assert_not_called()

    def test_public_service_returns_failure_not_http_unhandled_exception(self):
        from agent.agent_backend.services.pre_review_service import PreReviewService
        service = PreReviewService.__new__(PreReviewService)
        service._run_p52_feedback_workflow_stage = Mock(side_effect=LLMExecutionError(
            'model_invalid_output', stage='p52_feedback_optimizer'))
        ok, message, result = service.optimize_p52_feedback('r', '3.2.p.5.2')
        self.assertFalse(ok)
        self.assertEqual(result['execution_status'], 'failed')
        self.assertEqual(result['candidate_register_status'], 'skipped')
        self.assertEqual(result['error']['code'], 'model_invalid_output')
        self.assertEqual(result['candidate_patches'], [])
