import unittest
from unittest.mock import Mock

from agent.agent_backend.agents.review.run_review_workflow import RunReviewWorkflow
from agent.agent_backend.llm.errors import LLMExecutionError


class RunWorkflowPartialTests(unittest.TestCase):
    def workflow(self):
        return RunReviewWorkflow(
            consistency_agent=Mock(check=Mock(return_value={'summary':'checked','issues':[]})),
            qa_agent=Mock(review_section=Mock(return_value={'qa_status':'pass','qa_issues':[]}),
                          review_run=Mock(return_value={'qa_status':'pass','qa_issues':[]})),
            lead_reviewer_agent=Mock(summarize=Mock(return_value={'summary':'complete','key_questions':[]})))

    def test_failure_keeps_only_completed_nodes_and_stops_dependents(self):
        for graph_mode in (False, True):
            with self.subTest(graph_mode=graph_mode):
                workflow = self.workflow()
                if graph_mode and workflow.graph is None:
                    self.skipTest('LangGraph未安装，不能冒称图执行验证通过')
                if not graph_mode: workflow.graph = None
                workflow.qa_agent.review_run.side_effect = LLMExecutionError('model_timeout')
                with self.assertRaises(LLMExecutionError) as caught:
                    workflow.run({}, [{'section_id':'s','section_summary':{'summary':'saved'}}])
                error = caught.exception
                self.assertEqual(error.completed_stages, ['consistency','qa_sections'])
                self.assertEqual(error.incomplete_stages, ['qa_run','lead'])
                self.assertEqual(error.partial_result['consistency_result']['summary'],'checked')
                self.assertEqual(len(error.partial_result['qa_checks']),1)
                self.assertNotIn('lead_result',error.partial_result)
                self.assertNotIn('run_qa_result',error.partial_result)
                workflow.lead_reviewer_agent.summarize.assert_not_called()

    def test_mid_section_qa_retains_completed_check_without_finishing_stage(self):
        workflow = self.workflow(); workflow.graph = None
        workflow.qa_agent.review_section.side_effect = [
            {'qa_status':'pass','qa_issues':[]}, LLMExecutionError('model_invalid_output')]
        with self.assertRaises(LLMExecutionError) as caught:
            workflow.run({}, [{'section_id':'a'}, {'section_id':'b'}])
        self.assertEqual(caught.exception.completed_stages,['consistency'])
        self.assertEqual(len(caught.exception.partial_result['qa_checks']),1)
        workflow.qa_agent.review_run.assert_not_called()


if __name__ == '__main__': unittest.main()
