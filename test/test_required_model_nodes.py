import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from agent.agent_backend.llm.client import LLMClient
from agent.agent_backend.llm.errors import LLMExecutionError
from agent.agent_backend.agents.review.section_summarizer_agent import SectionSummarizerAgent
from agent.agent_backend.agents.review.task_question_agent import TaskQuestionAgent
from agent.agent_backend.agents.review.retrieval_evaluator_agent import RetrievalEvaluatorAgent


class RequiredModelNodeTests(unittest.TestCase):
    def agent(self, cls, output):
        agent = object.__new__(cls)
        agent.llm = SimpleNamespace(chat=Mock(return_value=output), extract_json=LLMClient.extract_json)
        agent.prompts = SimpleNamespace(render=lambda *args, **kwargs:'synthetic')
        agent.envelopes = SimpleNamespace(build=lambda *args:[])
        return agent

    def test_summary_invalid_json_or_fields_cannot_be_default_facts(self):
        for output in ('broken SECRET', '{}', '{"structured_summary":"hello"}',
                       '{"structured_summary":"ok","key_facts":[{}],"missing_items":[],"draft_risks":[]}'):
            with self.subTest(output=output):
                with self.assertRaises(LLMExecutionError) as caught:
                    self.agent(SectionSummarizerAgent, output).summarize('s','section','synthetic body')
                self.assertEqual(caught.exception.stage, 'section_summarizer')
                self.assertNotIn('SECRET', str(caught.exception))

    def test_valid_summary_and_no_content_are_distinct(self):
        expected = {'structured_summary':'已检查', 'key_facts':[], 'missing_items':[], 'draft_risks':[]}
        agent = self.agent(SectionSummarizerAgent, json.dumps(expected))
        self.assertEqual(agent.summarize('s','section','body')['structured_summary'],'已检查')
        agent.llm.chat.reset_mock()
        self.assertEqual(agent.summarize('s','section','')['missing_items'], ['章节无可用正文'])
        agent.llm.chat.assert_not_called()

    def test_task_and_retrieval_invalid_json_or_missing_core_fields_fail(self):
        for cls, method, stage in [(TaskQuestionAgent,'build_questions','task_question'),
                                   (RetrievalEvaluatorAgent,'evaluate','retrieval_evaluator')]:
            for output in ('broken SECRET', '{}', '[]'):
                with self.subTest(stage=stage,output=output):
                    with self.assertRaises(LLMExecutionError) as caught:
                        agent = self.agent(cls, output)
                        agent._build_prompt_payload = lambda *args:{}
                        agent._build_fallback_questions = lambda *args:{}
                        agent._fallback_evaluate = lambda *args:{}
                        getattr(agent, method)({'section_id':'s'})
                    self.assertEqual(caught.exception.stage, stage)
                    self.assertNotIn('SECRET', str(caught.exception))

    def test_valid_empty_task_and_retrieval_results_are_not_model_failure(self):
        questions = self.agent(TaskQuestionAgent, json.dumps({'judgment_ready':False, 'task_questions':[],
            'reasoning_plan':[], 'missing_information':['缺少正文']}))
        questions._build_prompt_payload = lambda *args:{}
        questions._build_fallback_questions = lambda *args:{'task_questions':[], 'reasoning_plan':[]}
        result = questions.build_questions({'section_id':'s'})
        self.assertFalse(result['judgment_ready'])
        self.assertEqual(result['llm_execution']['raw_preview'], '')
        evaluator = self.agent(RetrievalEvaluatorAgent, json.dumps({'evaluation_summary':'没有候选依据',
            'approved_evidence_ids':[], 'rejected_evidence_ids':['rejected-source'], 'approved_materials':[], 'rejected_materials':[]}))
        evaluator._build_prompt_payload = lambda *args:{}
        evaluator._fallback_evaluate = lambda *args:{'approved_evidence_ids':['rejected-source']}
        result = evaluator.evaluate({'section_id':'s'})
        self.assertEqual(result['approved_evidence_ids'], [])
        self.assertEqual(result['rejected_evidence_ids'], ['rejected-source'])
        self.assertFalse(result['llm_execution']['used_default_fallback'])


if __name__ == '__main__':
    unittest.main()
