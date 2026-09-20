import unittest
import tempfile
from unittest.mock import patch
from datetime import datetime, timezone

from agent.agent_backend.database.mysql.db_model import (
    PreReviewExecutionAudit,
    PreReviewExperienceMemory,
    PreReviewFeedback,
    PreReviewFeedbackAnalysisResult,
    PreReviewPatchRegistry,
    PreReviewProject,
    PreReviewPromptRule,
    PreReviewRun,
    PreReviewSectionConclusion,
    PreReviewSectionExample,
    PreReviewSectionOutput,
    PreReviewSectionRule,
    PreReviewSectionTrace,
    PreReviewUploadTask,
    RuntimeTask,
)
from agent.agent_backend.services.pre_review_service import (
    GLOBAL_SECTION_RULE_PROJECT_ID,
    PreReviewService,
)
from agent.agent_backend.services.runtime_task_store import RuntimeTaskStore
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


class _Connection:
    def __init__(self, session_factory, engine=None):
        self._session_factory = session_factory
        self.engine = engine

    def get_session(self):
        return self._session_factory()


class PreReviewProjectCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        isolated_files = tempfile.TemporaryDirectory(prefix='pre-review-cleanup-')
        self.addCleanup(isolated_files.cleanup)
        location = patch('agent.agent_backend.services.pre_review_service.SUBMISSION_UPLOAD_DIR', isolated_files.name + '/uploads')
        location.start()
        self.addCleanup(location.stop)
        self.engine = create_engine("sqlite:///:memory:")
        tables = [
            PreReviewProject,
            PreReviewRun,
            PreReviewExecutionAudit,
            PreReviewSectionConclusion,
            PreReviewSectionTrace,
            PreReviewFeedback,
            PreReviewSectionOutput,
            PreReviewFeedbackAnalysisResult,
            PreReviewPatchRegistry,
            PreReviewPromptRule,
            PreReviewSectionRule,
            PreReviewSectionExample,
            PreReviewExperienceMemory,
            PreReviewUploadTask,
            RuntimeTask,
        ]
        for model in tables:
            model.__table__.create(bind=self.engine, checkfirst=True)
        self.Session = sessionmaker(bind=self.engine)
        self.now = datetime(2026, 8, 10, 12, 0, 0, tzinfo=timezone.utc)
        self.project_id = "project_cleanup_target"
        self.other_project_id = "project_cleanup_other"
        self.run_id = "run_cleanup_target"
        self.other_run_id = "run_cleanup_other"
        self._seed_rows()

        self.service = object.__new__(PreReviewService)
        self.connection = _Connection(self.Session, self.engine)
        self.service.db_conn = self.connection
        self.service._now = lambda: self.now
        self.service._purge_project_submission_files = lambda session, project_id: None

    def tearDown(self) -> None:
        self.engine.dispose()

    def _seed_rows(self) -> None:
        session = self.Session()
        try:
            session.add_all(
                [
                    PreReviewProject(
                        project_id=self.project_id,
                        project_name="cleanup target",
                        status="created",
                        create_time=self.now,
                        update_time=self.now,
                        is_deleted=False,
                    ),
                    PreReviewProject(
                        project_id=self.other_project_id,
                        project_name="cleanup other",
                        status="created",
                        create_time=self.now,
                        update_time=self.now,
                        is_deleted=False,
                    ),
                    PreReviewProject(
                        project_id=GLOBAL_SECTION_RULE_PROJECT_ID,
                        project_name="global rules",
                        status="completed",
                        create_time=self.now,
                        update_time=self.now,
                        is_deleted=False,
                    ),
                ]
            )
            session.add_all(
                [
                    PreReviewRun(
                        run_id=self.run_id,
                        project_id=self.project_id,
                        version_no=1,
                        source_doc_id="doc_target",
                        create_time=self.now,
                    ),
                    PreReviewRun(
                        run_id=self.other_run_id,
                        project_id=self.other_project_id,
                        version_no=1,
                        source_doc_id="doc_other",
                        create_time=self.now,
                    ),
                ]
            )
            session.add_all(
                [
                    self._audit("audit_target", self.run_id),
                    self._audit("audit_other", self.other_run_id),
                    self._rule("rule_target", self.project_id),
                    self._rule("rule_other", self.other_project_id),
                    self._rule("rule_global", GLOBAL_SECTION_RULE_PROJECT_ID),
                    self._example("example_target", self.project_id, self.run_id),
                    self._example("example_other", self.other_project_id, self.other_run_id),
                    self._example("example_global", GLOBAL_SECTION_RULE_PROJECT_ID, None),
                    self._task("task_target", "pre_review", self.project_id),
                    self._task("task_other", "pre_review", self.other_project_id),
                    self._task("task_other_domain", "filing_change", self.project_id),
                ]
            )
            session.commit()
        finally:
            session.close()

    def _audit(self, audit_id: str, run_id: str) -> PreReviewExecutionAudit:
        return PreReviewExecutionAudit(
            audit_id=audit_id,
            run_id=run_id,
            section_id="3.2.s.1",
            stage="review",
            agent_name="reviewer",
            attempt_no=1,
            status="completed",
            input_digest_json="{}",
            output_digest_json="{}",
            version_snapshot_json="{}",
            create_time=self.now,
        )

    def _rule(self, rule_id: str, project_id: str) -> PreReviewSectionRule:
        return PreReviewSectionRule(
            rule_id=rule_id,
            project_id=project_id,
            section_id="3.2.s.1",
            rule_code=rule_id,
            rule_text="rule text",
            source_type="manual",
            is_active=True,
            create_time=self.now,
            update_time=self.now,
        )

    def _example(self, example_id: str, project_id: str, run_id: str) -> PreReviewSectionExample:
        return PreReviewSectionExample(
            example_id=example_id,
            project_id=project_id,
            run_id=run_id,
            section_id="3.2.s.1",
            example_type="reference",
            content="example content",
            is_active=True,
            create_time=self.now,
            update_time=self.now,
        )

    @staticmethod
    def _task(task_id: str, domain: str, project_id: str) -> RuntimeTask:
        return RuntimeTask(
            task_id=task_id,
            domain=domain,
            task_type="run",
            project_id=project_id,
            status="success",
            created_at=1.0,
            updated_at=1.0,
        )

    def test_delete_project_removes_project_owned_runtime_rows_only(self) -> None:
        ok, message = self.service.delete_project(self.project_id)

        self.assertTrue(ok, message)
        session = self.Session()
        try:
            project = session.query(PreReviewProject).filter_by(project_id=self.project_id).one()
            self.assertTrue(project.is_deleted)
            self.assertEqual(project.status, "archived")
            self.assertIsNone(session.query(PreReviewRun).filter_by(run_id=self.run_id).one_or_none())
            self.assertIsNone(
                session.query(PreReviewExecutionAudit).filter_by(audit_id="audit_target").one_or_none()
            )
            self.assertIsNone(session.query(PreReviewSectionRule).filter_by(rule_id="rule_target").one_or_none())
            self.assertIsNone(
                session.query(PreReviewSectionExample).filter_by(example_id="example_target").one_or_none()
            )
            self.assertIsNone(session.query(RuntimeTask).filter_by(task_id="task_target").one_or_none())

            self.assertIsNotNone(session.query(PreReviewRun).filter_by(run_id=self.other_run_id).one_or_none())
            self.assertIsNotNone(
                session.query(PreReviewExecutionAudit).filter_by(audit_id="audit_other").one_or_none()
            )
            self.assertIsNotNone(session.query(PreReviewSectionRule).filter_by(rule_id="rule_other").one_or_none())
            self.assertIsNotNone(
                session.query(PreReviewSectionExample).filter_by(example_id="example_other").one_or_none()
            )
            self.assertIsNotNone(session.query(PreReviewSectionRule).filter_by(rule_id="rule_global").one_or_none())
            self.assertIsNotNone(
                session.query(PreReviewSectionExample).filter_by(example_id="example_global").one_or_none()
            )
            self.assertIsNotNone(session.query(RuntimeTask).filter_by(task_id="task_other").one_or_none())
            self.assertIsNotNone(
                session.query(RuntimeTask).filter_by(task_id="task_other_domain").one_or_none()
            )
        finally:
            session.close()

    def test_global_rule_project_cannot_be_deleted(self) -> None:
        ok, message = self.service.delete_project(GLOBAL_SECTION_RULE_PROJECT_ID)

        self.assertFalse(ok)
        self.assertEqual(message, "system project cannot be deleted")
        session = self.Session()
        try:
            project = (
                session.query(PreReviewProject)
                .filter_by(project_id=GLOBAL_SECTION_RULE_PROJECT_ID)
                .one()
            )
            self.assertFalse(project.is_deleted)
            self.assertIsNotNone(session.query(PreReviewSectionRule).filter_by(rule_id="rule_global").one_or_none())
            self.assertIsNotNone(
                session.query(PreReviewSectionExample).filter_by(example_id="example_global").one_or_none()
            )
        finally:
            session.close()

    def test_delete_project_refuses_active_runtime_task(self) -> None:
        session = self.Session()
        try:
            task = session.query(RuntimeTask).filter_by(task_id="task_target").one()
            task.status = "running"
            task.worker_id = "worker-active"
            task.heartbeat_at = 995.0
            task.updated_at = 995.0
            session.commit()
        finally:
            session.close()

        ok, message = self.service.delete_project(self.project_id)

        self.assertFalse(ok)
        self.assertIn("后台任务运行中", message)
        session = self.Session()
        try:
            project = session.query(PreReviewProject).filter_by(project_id=self.project_id).one()
            self.assertFalse(project.is_deleted)
            self.assertIsNotNone(session.query(PreReviewRun).filter_by(run_id=self.run_id).one_or_none())
            self.assertIsNotNone(session.query(RuntimeTask).filter_by(task_id="task_target").one_or_none())
        finally:
            session.close()

    def test_stale_runtime_task_recovers_to_failed_then_project_can_be_deleted(self) -> None:
        session = self.Session()
        try:
            task = session.query(RuntimeTask).filter_by(task_id="task_target").one()
            task.status = "pending"
            task.worker_id = "worker-gone"
            task.heartbeat_at = None
            task.updated_at = 100.0
            session.commit()
        finally:
            session.close()

        store = RuntimeTaskStore(connection=self.connection, ensure_schema=False)
        recovered = store.recover_stale_active_tasks(
            domain="pre_review",
            stale_after_seconds=120.0,
            now=1000.0,
        )
        self.assertEqual(["task_target"], recovered)
        recovered_task = store.get_task("task_target", domain="pre_review")
        self.assertEqual("failed", recovered_task["status"])
        self.assertTrue(recovered_task["result"]["interrupted"])

        ok, message = self.service.delete_project(self.project_id)

        self.assertTrue(ok, message)
        session = self.Session()
        try:
            project = session.query(PreReviewProject).filter_by(project_id=self.project_id).one()
            self.assertTrue(project.is_deleted)
            self.assertIsNone(session.query(RuntimeTask).filter_by(task_id="task_target").one_or_none())
        finally:
            session.close()

    def test_get_run_task_metadata_returns_only_active_project_identifiers(self) -> None:
        ok, message, metadata = self.service.get_run_task_metadata(self.run_id)

        self.assertTrue(ok, message)
        self.assertEqual(
            {
                "run_id": self.run_id,
                "project_id": self.project_id,
                "source_doc_id": "doc_target",
            },
            metadata,
        )

        session = self.Session()
        try:
            project = session.query(PreReviewProject).filter_by(project_id=self.project_id).one()
            project.is_deleted = True
            session.commit()
        finally:
            session.close()

        ok, message, metadata = self.service.get_run_task_metadata(self.run_id)
        self.assertFalse(ok)
        self.assertEqual("run not found", message)
        self.assertIsNone(metadata)

    def test_delete_project_rolls_back_all_cleanup_when_transaction_fails(self) -> None:
        purge_runtime_records = self.service._purge_project_runtime_records

        def fail_after_cleanup(session, project_id):
            purge_runtime_records(session, project_id)
            raise RuntimeError("forced cleanup failure")

        self.service._purge_project_runtime_records = fail_after_cleanup

        ok, message = self.service.delete_project(self.project_id)

        self.assertFalse(ok)
        self.assertIn("forced cleanup failure", message)
        session = self.Session()
        try:
            project = session.query(PreReviewProject).filter_by(project_id=self.project_id).one()
            self.assertFalse(project.is_deleted)
            self.assertIsNotNone(session.query(PreReviewRun).filter_by(run_id=self.run_id).one_or_none())
            self.assertIsNotNone(
                session.query(PreReviewExecutionAudit).filter_by(audit_id="audit_target").one_or_none()
            )
            self.assertIsNotNone(session.query(PreReviewSectionRule).filter_by(rule_id="rule_target").one_or_none())
            self.assertIsNotNone(
                session.query(PreReviewSectionExample).filter_by(example_id="example_target").one_or_none()
            )
            self.assertIsNotNone(session.query(RuntimeTask).filter_by(task_id="task_target").one_or_none())
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main()
