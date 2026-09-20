import unittest
from unittest.mock import Mock

from agent.agent_backend.llm.errors import LLMExecutionError
from agent.agent_backend.services.filing_change_llm_reasoning_service import FilingChangeLLMReasoningService


class FilingReasoningOutputTests(unittest.TestCase):
    def test_invalid_json_cannot_return_default_business_result(self):
        service = FilingChangeLLMReasoningService.__new__(FilingChangeLLMReasoningService)
        service.llm = Mock()
        service.llm.chat.return_value = 'PRIVATE_RESPONSE'
        for parsed in (None, [], 'invalid'):
            service.llm.extract_json.return_value = parsed
            with self.subTest(parsed=parsed), self.assertRaises(LLMExecutionError) as caught:
                service._call_json('instruction', {}, {'result': '通过'})
            self.assertEqual(caught.exception.code, 'model_invalid_json')
            self.assertNotIn('PRIVATE_RESPONSE', str(caught.exception))

    def test_valid_object_preserved_for_scene_validation(self):
        service = FilingChangeLLMReasoningService.__new__(FilingChangeLLMReasoningService)
        service.llm = Mock()
        service.llm.extract_json.return_value = {'explanation_order': [1, 0]}
        self.assertEqual(service.summarize_overall({'facts': ['a', 'b']}), {'explanation_order': [1, 0]})
