import unittest
from unittest.mock import Mock
from agent.agent_backend.agents.review.feedback_agent import FeedbackAgent
from agent.agent_backend.agents.review.meta_reflection_agent import MetaReflectionAgent
from agent.agent_backend.llm.errors import LLMExecutionError


class FeedbackOutputTests(unittest.TestCase):
    def agent(self, cls, output):
        obj = cls.__new__(cls)
        obj.llm = Mock()
        obj.llm.chat.return_value = 'PRIVATE_FEEDBACK_OUTPUT'
        obj.llm.extract_json.return_value = output
        obj.prompts = Mock()
        obj.envelopes = Mock()
        return obj

    def payload(self):
        return {'task_id': 'task', 'section_id': '3.2.p.2', 'feedback_analysis_result': {'root_cause': '需要核对'}}

    def test_invalid_output_is_typed_failure_at_all_three_nodes(self):
        for cls, method in ((FeedbackAgent, 'analyze_feedback'), (FeedbackAgent, 'propose_patch'), (MetaReflectionAgent, 'reflect')):
            for output in (None, {}, []):
                with self.subTest(method=method, output=output), self.assertRaises(LLMExecutionError):
                    getattr(self.agent(cls, output), method)(self.payload())

    def test_valid_empty_patch_does_not_create_patch(self):
        output = {'patches': [], 'candidate_templates': {}, 'applicable_conditions': []}
        result = self.agent(FeedbackAgent, output).propose_patch(self.payload())
        self.assertEqual(result['patches'], [])
        self.assertNotIn('PRIVATE_FEEDBACK_OUTPUT', str(result))

    def test_nested_invalid_fields_are_typed_failures(self):
        for output in ({'patches': [{'patch_type': []}], 'candidate_templates': {}, 'applicable_conditions': []},
                       {'patches': [], 'candidate_templates': [], 'applicable_conditions': []}):
            with self.subTest(output=output), self.assertRaises(LLMExecutionError):
                self.agent(FeedbackAgent, output).propose_patch(self.payload())

    def test_supported_feedback_experience_is_retained(self):
        output = {'feedback_polarity': 'positive', 'root_cause': '证据关联可复核', 'error_types': [],
                  'attention_points_next_time': [], 'retrieval_missed': [], 'new_experience': [
                      {'experience_type': 'review_rule', 'content': '逐项核对证据来源', 'applicable_scope': '3.2.p.2'}]}
        result = self.agent(FeedbackAgent, output).analyze_feedback(self.payload())
        self.assertEqual(result['new_experience'][0]['content'], '逐项核对证据来源')

    def test_valid_empty_experience_does_not_create_experience(self):
        output = {'feedback_polarity': 'negative', 'root_cause': '缺乏可复用依据', 'error_types': [],
                  'attention_points_next_time': [], 'retrieval_missed': [], 'new_experience': []}
        result = self.agent(FeedbackAgent, output).analyze_feedback(self.payload())
        self.assertEqual(result['new_experience'], [])
        self.assertNotIn('PRIVATE_FEEDBACK_OUTPUT', str(result))

    def test_valid_empty_reflection_experience_preserved(self):
        output = {'reflection_summary': '无足够依据形成经验', 'bad_case': False,
                  'high_frequency_doc_ids': [], 'distilled_experiences': [],
                  'few_shot_example': {'title': '', 'input_snapshot': {}, 'expected_output': {}}}
        result = self.agent(MetaReflectionAgent, output).reflect(self.payload())
        self.assertEqual(result['distilled_experiences'], [])
        self.assertEqual(result['few_shot_example']['title'], '')
        self.assertNotIn('PRIVATE_FEEDBACK_OUTPUT', str(result))

    def test_empty_few_shot_not_saved_as_reference(self):
        from agent.agent_backend.services.pre_review_service import PreReviewService
        service = PreReviewService.__new__(PreReviewService)
        service._upsert_section_example = Mock(return_value={'saved': True})
        result = service._persist_meta_reflection_outputs(Mock(), project_id='p', run_id='r', source_doc_id='d',
            section_id='3.2.p.2', section_name='测试', feedback_key='f', reference_example={}, review_output={},
            trace_payload={}, iteration=1, meta_reflection={'distilled_experiences': [],
                'few_shot_example': {'title': '', 'input_snapshot': {}, 'expected_output': {}}})
        self.assertEqual(result['few_shot_example'], {})
        self.assertEqual([call.kwargs['example_type'] for call in service._upsert_section_example.call_args_list], ['evaluation_case'])

    def test_service_failure_boundary_rolls_back_and_redacts(self):
        from agent.agent_backend.services.pre_review_service import PreReviewService
        for error in (LLMExecutionError('model_invalid_output', stage='meta_reflector'), RuntimeError('PRIVATE_EXCEPTION')):
            service = PreReviewService.__new__(PreReviewService)
            service.db_conn = Mock()
            service._load_feedback_section_context = Mock(side_effect=error)
            result = service._process_feedback_closed_loop_impl({'run_id': 'r', 'section_id': '3.2.p.2'}, {}, {})
            self.assertFalse(result['success'])
            self.assertEqual(result['candidate_register_status'], 'skipped')
            self.assertNotIn('PRIVATE_EXCEPTION', str(result))
            self.assertIn('code', result['error'])
            service.db_conn.get_session.return_value.rollback.assert_called_once()
            service.db_conn.get_session.return_value.close.assert_called_once()
