import contextlib
import io
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from agent.agent_backend.llm.providers.openai_compatible import OpenAICompatibleTextModel
from agent.agent_backend.agents.review.consistency_agent import ConsistencyAgent
from agent.test.test_audit_llm_safety import load_client


class ModelFailurePropagationTests(unittest.TestCase):
    def provider(self, content=None, error=None):
        model = object.__new__(OpenAICompatibleTextModel)
        model.model = 'synthetic-model'
        model.base_url = 'http://localhost/v1'
        model.api_key = 'synthetic-key'
        create = Mock(side_effect=error) if error else Mock(return_value=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))]))
        model.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        return model

    def test_transport_failures_cannot_become_default_result(self):
        errors = [(TimeoutError('SECRET body'), 'model_timeout'), (ConnectionError('SECRET'), 'model_connection_failed')]
        for status, code in [(401,'model_auth_failed'), (403,'model_auth_failed'), (429,'model_rate_limited'), (500,'model_service_error')]:
            error = RuntimeError('SECRET response'); error.status_code = status
            errors.append((error, code))
        for error, code in errors:
            with self.subTest(code=code):
                output = io.StringIO()
                with contextlib.redirect_stdout(output), self.assertRaises(Exception) as caught:
                    self.provider(error=error).chat([{'role':'user','content':'SECRET submission'}], default='{"issues":[]}')
                self.assertEqual(caught.exception.code, code)
                self.assertNotIn('SECRET', str(caught.exception))
                self.assertNotIn('SECRET', output.getvalue())

    def test_empty_output_is_not_default_success(self):
        for text in ('', None, '   '):
            with self.subTest(text=text), self.assertRaises(Exception) as caught:
                self.provider(content=text).chat([], default='{"issues":[]}')
            self.assertEqual(caught.exception.code, 'model_empty_output')

    def checker(self, content):
        Client = load_client(); client = object.__new__(Client)
        client._chat_model = self.provider(content=content)
        client._active_chat_model_name = 'synthetic'; client.chat_context_max_chars = 200000
        checker = object.__new__(ConsistencyAgent)
        checker.llm = client
        checker.prompts = SimpleNamespace(render=lambda *args, **kwargs: 'synthetic')
        return checker

    def test_invalid_consistency_output_cannot_be_no_issues(self):
        for text in ('invalid SECRET', '{}', '{"summary":"ok"}', '{"summary":"ok","issues":"none"}', '{"summary":"ok","issues":[7]}'):
            with self.subTest(text=text), self.assertRaises(Exception) as caught:
                self.checker(text).check([{'section_id':'synthetic'}])
            self.assertIn(caught.exception.code, ('model_invalid_json','model_invalid_output'))
            self.assertNotIn('SECRET', str(caught.exception))

    def test_valid_empty_issue_list_remains_success(self):
        result = self.checker('{"summary":"已检查未发现矛盾","issues":[]}').check([{'section_id':'synthetic'}])
        self.assertEqual(result['issues'], [])
        self.assertEqual(result['summary'], '已检查未发现矛盾')

    def test_qa_invalid_response_and_prompt_failure_are_not_pass(self):
        from agent.agent_backend.agents.review.qa_agent import QAAgent
        for text in ('invalid', '{}', '{"qa_status":"pass"}', '{"qa_status":"fail","qa_issues":[]}', '{"qa_status":"pass","qa_issues":[{}]}'):
            checker = self.checker(text)
            agent = object.__new__(QAAgent); agent.llm = checker.llm; agent.prompts = checker.prompts
            with self.subTest(text=text), self.assertRaises(Exception) as caught:
                agent._llm_review({})
            self.assertIn(caught.exception.code, ('model_invalid_json', 'model_invalid_output'))
        agent.prompts = SimpleNamespace(render=Mock(side_effect=RuntimeError('SECRET template')))
        with self.assertRaises(Exception) as caught:
            agent._llm_review({})
        self.assertEqual(caught.exception.code, 'model_configuration_invalid')
        self.assertNotIn('SECRET', str(caught.exception))

    def test_lead_invalid_response_cannot_form_empty_summary(self):
        from agent.agent_backend.agents.review.lead_reviewer_agent import LeadReviewerAgent
        for text in ('invalid', '{}', '{"overall_conclusion":"ok","summary":"ok"}'):
            checker = self.checker(text)
            agent = object.__new__(LeadReviewerAgent); agent.llm = checker.llm; agent.prompts = checker.prompts
            with self.subTest(text=text), self.assertRaises(Exception) as caught:
                agent.summarize({}, [], {}, {})
            self.assertIn(caught.exception.code, ('model_invalid_json', 'model_invalid_output'))
