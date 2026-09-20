"""注册流程中途上下文失败：已提交章节保留，不生成总体成功结论。"""
import tempfile
import json
from pathlib import Path
from datetime import datetime
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from agent.agent_backend.database.mysql.db_model import (PreReviewProject, PreReviewRun,
    PreReviewSectionConclusion, PreReviewSectionOutput, PreReviewSectionTrace)
from agent.agent_backend.services.pre_review_service import PreReviewService
from agent.agent_backend.llm.errors import LLMContextError


class PreReviewContextAudit(unittest.TestCase):
    def test_first_section_is_durable_second_exceeds_budget(self):
        with tempfile.TemporaryDirectory(prefix='context-pre-review-') as root:
            engine=create_engine('sqlite:///'+str(Path(root)/'test.sqlite'))
            self.addCleanup(engine.dispose)
            for model in (PreReviewProject,PreReviewRun,PreReviewSectionConclusion,PreReviewSectionOutput,PreReviewSectionTrace):
                model.__table__.create(engine)
            factory=sessionmaker(bind=engine,expire_on_commit=False)
            now=datetime(2026,9,17)
            with factory() as s:
                s.add(PreReviewProject(project_id='synthetic',project_name='合成上下文测试',status='created',create_time=now,update_time=now))
                s.commit()
            service=object.__new__(PreReviewService)
            service.db_conn=SimpleNamespace(get_session=factory)
            service._now=lambda:now
            service._apply_active_prompt_version=lambda config:config or {}
            service._resolve_pre_review_mode=lambda config:'multi_section'
            service._recover_project_running_state=lambda *args:None
            chunks=[{'section_id':'3.2.p.2.1'},{'section_id':'3.2.p.2.2'}]
            service._load_doc_chunks=lambda **kwargs:(True,'ok',chunks)
            service._order_review_units=lambda chunks:chunks
            service._new_run_id=lambda:'synthetic-run'
            service._next_version=lambda *args:1
            service._seed_historical_feedback_memory=lambda **kwargs:None
            service._seed_submission_structure_memory=lambda **kwargs:None
            service._run_post_review_agents=Mock()
            def review(**kw):
                sid=kw['chunk']['section_id']
                if sid.endswith('.2'):
                    raise LLMContextError(actual_chars=11,limit_chars=10)
                return {'success':True,'section_id':sid,'section_meta':{'section_name':'合成'},
                        'review_result':{'pre_review_conclusion':'合成已完成'},
                        'conclusion_row':PreReviewSectionConclusion(run_id=kw['run_id'],section_id=sid,section_name='合成',conclusion='合成已完成',create_time=now),
                        'trace_row':PreReviewSectionTrace(run_id=kw['run_id'],section_id=sid,trace_json='[]',create_time=now)}
            service.section_review_orchestrator=SimpleNamespace(review_single_chunk=review)
            events=[]
            ok,msg,result=service._run_pre_review_impl('synthetic','synthetic-doc',progress_callback=events.append)
            self.assertFalse(ok,msg)
            self.assertEqual(result['completed_section_ids'],['3.2.p.2.1'])
            self.assertEqual(result['incomplete_section_ids'],['3.2.p.2.2'])
            self.assertEqual(result['error']['stage'],'section_review')
            self.assertFalse(result['review_complete'])
            service._run_post_review_agents.assert_not_called()
            self.assertNotIn('run_done',[e['stage'] for e in events])
            with factory() as s:
                self.assertEqual(s.query(PreReviewProject).one().status,'failed')
                self.assertIsNotNone(s.query(PreReviewRun).one().finish_time)
                saved = json.loads(s.query(PreReviewRun).one().summary)
                self.assertFalse(saved['review_complete'])
                self.assertEqual(saved['completed_section_ids'], ['3.2.p.2.1'])
                self.assertEqual(saved['incomplete_section_ids'], ['3.2.p.2.2'])
                self.assertEqual(saved['error']['code'], 'context_limit_exceeded')
                self.assertEqual(s.query(PreReviewSectionConclusion).one().section_id,'3.2.p.2.1')
                self.assertEqual(s.query(PreReviewSectionOutput).count(),1)


if __name__=='__main__':unittest.main()
