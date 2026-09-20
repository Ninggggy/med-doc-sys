import unittest
import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

from agent.agent_backend.llm.client import LLMClient
from agent.agent_backend.llm.errors import LLMExecutionError
from agent.agent_backend.agents.review.planner_reviewer_agent import PlannerReviewerAgent
from agent.agent_backend.agents.review.p52_reviewer_agent import P52ReviewerAgent
from agent.agent_backend.llm.review_output_validation import validate_plan_output, validate_review_output


class ChapterModelFailureTests(unittest.TestCase):
    def test_actual_review_normalizers_accept_explicit_insufficient_information(self):
        data = {'section_summary':'缺少实测数据，不能形成支持结论',
                'pre_review_conclusion':'insufficient_information', 'supported_points':[],
                'unsupported_points':[], 'missing_points':['缺少实测数据'], 'risk_points':[], 'task_verdicts':[]}
        for cls in (PlannerReviewerAgent, P52ReviewerAgent):
            with self.subTest(cls=cls.__name__):
                agent = object.__new__(cls)
                agent.llm = SimpleNamespace(chat=Mock(return_value=json.dumps(data)), extract_json=LLMClient.extract_json)
                agent.prompts = SimpleNamespace(render=lambda *a,**k:'synthetic')
                agent.envelopes = SimpleNamespace(build=lambda *a:[])
                result = agent.review({'section_id':'3.2.p.5.2','raw_text':'合成申报原文'})
                self.assertEqual(result['pre_review_conclusion'],'insufficient_information')
                self.assertFalse(result['llm_execution']['used_default_fallback'])
                self.assertEqual(result['llm_execution']['raw_preview'],'')

    def test_valid_review_and_missing_information_remain_distinct(self):
        value = {'section_summary':'核对已提供资料', 'pre_review_conclusion':'supported',
                 'supported_points':['有对应检验数据'], 'unsupported_points':[], 'missing_points':[],
                 'risk_points':[], 'task_verdicts':[]}
        validate_review_output(value, 'synthetic')
        missing = {**value, 'pre_review_conclusion':'insufficient_information',
                   'supported_points':[], 'missing_points':['缺少检验数据']}
        validate_review_output(missing, 'synthetic')
        for bad in ({**value, 'supported_points':[]}, {**value,'task_verdicts':[{}]},
                    {**value,'pre_review_conclusion':'invented'}, {**value,'missing_points':'none'}):
            with self.subTest(bad=bad), self.assertRaises(LLMExecutionError):
                validate_review_output(bad, 'synthetic')

    def test_plan_and_duplicate_task_validation(self):
        validate_plan_output({'query_list':['质量标准'], 'retrieval_plan':[], 'review_tasks':[]})
        verdict = {'task_code':'A','status':'supported','reason':'存在对应检验数据','basis':'对应质量标准'}
        value = {'section_summary':'核对已提供资料', 'pre_review_conclusion':'supported',
                 'supported_points':[], 'unsupported_points':[], 'missing_points':[],
                 'risk_points':[], 'task_verdicts':[verdict]}
        validate_review_output(value, 'synthetic')
        value['task_verdicts'].append(deepcopy(verdict))
        with self.assertRaises(LLMExecutionError): validate_review_output(value, 'synthetic')

    def test_invalid_output_cannot_turn_into_default_plan_or_conclusion(self):
        for cls, method in ((PlannerReviewerAgent,'plan'), (PlannerReviewerAgent,'review'), (P52ReviewerAgent,'review')):
            for raw in ('BROKEN SECRET', '{}', '[]', '{"pre_review_conclusion":"supported"}'):
                with self.subTest(cls=cls.__name__,method=method,raw=raw):
                    agent = object.__new__(cls)
                    agent.llm = SimpleNamespace(chat=Mock(return_value=raw),extract_json=LLMClient.extract_json)
                    agent.prompts = SimpleNamespace(render=lambda *a,**k:'synthetic')
                    agent.envelopes = SimpleNamespace(build=lambda *a:[])
                    for name in ('_build_planner_prompt_payload','_build_review_prompt_payload','_build_prompt_payload'):
                        setattr(agent,name,lambda *a:{})
                    for name in ('_fallback_plan','_fallback_review_v2','_fallback_review'):
                        setattr(agent,name,lambda *a:{})
                    with self.assertRaises(LLMExecutionError) as caught:
                        getattr(agent,method)({'section_id':'s'})
                    self.assertNotIn('SECRET',str(caught.exception))


if __name__ == '__main__': unittest.main()
