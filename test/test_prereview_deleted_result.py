"""Deleted registration projects must not expose runs/reports through direct IDs."""
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from agent.agent_backend.database.mysql.db_model import PreReviewProject, PreReviewRun
from agent.agent_backend.services.pre_review_service import PreReviewService


class DeletedResultTests(unittest.TestCase):
    def test_deleted_project_is_rejected_before_content_or_export(self):
        with tempfile.TemporaryDirectory(prefix='deleted-run-') as root:
            engine = create_engine('sqlite:///' + str(Path(root)/'db.sqlite'))
            self.addCleanup(engine.dispose)
            PreReviewProject.__table__.create(engine)
            PreReviewRun.__table__.create(engine)
            factory = sessionmaker(bind=engine)
            now = datetime.now()
            with factory() as session:
                session.add(PreReviewProject(project_id='deleted', project_name='synthetic', is_deleted=1, create_time=now, update_time=now))
                session.add(PreReviewRun(run_id='run', project_id='deleted', source_doc_id='doc', summary='{"review_complete":true}', create_time=now, finish_time=now))
                session.commit()
            service = object.__new__(PreReviewService)
            service.db_conn = SimpleNamespace(get_session=factory)
            def forbidden(**kwargs):
                self.fail('deleted project must be rejected before reading source content')
            service.get_submission_section_overview = forbidden
            for operation in (service.get_run_section_overview, service.export_report_word, service.export_review_conclusions_report):
                ok, message, result = operation('run')
                self.assertFalse(ok)
                self.assertIn('project not found', message)
                self.assertIsNone(result)


if __name__ == '__main__': unittest.main()
