import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from agent.agent_backend.database.mysql.db_model import PreReviewProject, PreReviewUploadTask
from agent.agent_backend.services import runtime_task_store as runtime_task_store_module
from agent.agent_backend.services.runtime_task_store import RuntimeTaskStore


class _SqliteConnection:
    def __init__(self, database_url: str):
        self.engine = create_engine(database_url)
        self.SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=self.engine)

    def get_session(self):
        return self.SessionLocal()


class RuntimeTaskStoreTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="runtime-task-store-")
        database_path = Path(self._tmpdir.name) / "runtime_tasks.db"
        self.database_url = f"sqlite:///{database_path.as_posix()}"
        self.connections = []

    def tearDown(self):
        for connection in self.connections:
            connection.engine.dispose()
        self._tmpdir.cleanup()

    def _new_store(self) -> RuntimeTaskStore:
        connection = _SqliteConnection(self.database_url)
        self.connections.append(connection)
        return RuntimeTaskStore(connection=connection)

    def test_state_survives_new_store_and_database_connection(self):
        first_store = self._new_store()
        first_store.create_task(
            task_id="prt_persist_001",
            domain="pre_review",
            project_id="project-persist-001",
            source_doc_id="document-persist-001",
            task_type="run",
            payload={"mode": "full"},
            now=100.0,
        )
        self.assertTrue(first_store.claim_task("prt_persist_001", now=100.5))
        first_store.append_log(
            "prt_persist_001",
            {"stage": "task_created", "message": "task created", "time": "2026-08-10 08:00:00"},
            max_logs=500,
            assign_sequence=True,
            now=101.0,
        )
        first_store.finish_task(
            "prt_persist_001",
            status="completed",
            result={"ok": True, "data": {"run_id": "run-persist-001"}},
            final_log={"stage": "run_done", "message": "done"},
            now=102.0,
        )
        self.connections[-1].engine.dispose()

        # 新建 store 和数据库连接，模拟原 Worker 内存清空或进程重启。
        second_store = self._new_store()
        snapshot = second_store.get_snapshot("prt_persist_001", domain="pre_review", cursor=0)

        self.assertIsNotNone(snapshot)
        self.assertEqual("completed", snapshot["status"])
        self.assertTrue(snapshot["done"])
        self.assertEqual({"ok": True, "data": {"run_id": "run-persist-001"}}, snapshot["result"])
        self.assertEqual("project-persist-001", snapshot["project_id"])
        self.assertEqual("document-persist-001", snapshot["source_doc_id"])
        self.assertEqual(2, snapshot["next_cursor"])
        self.assertEqual(0, snapshot["logs"][0]["seq"])
        self.assertEqual("run_done", snapshot["logs"][-1]["stage"])

    def test_log_cursor_and_retention_match_existing_poll_contract(self):
        store = self._new_store()
        store.create_task(
            task_id="prt_cursor_001",
            domain="pre_review",
            project_id="project-cursor-001",
            now=200.0,
        )
        for index in range(3):
            store.append_log(
                "prt_cursor_001",
                {"stage": f"stage-{index}", "time": f"2026-08-10 08:00:0{index}"},
                max_logs=2,
                assign_sequence=True,
                now=201.0 + index,
            )

        full_snapshot = store.get_snapshot("prt_cursor_001", domain="pre_review", cursor=0)
        incremental_snapshot = store.get_snapshot("prt_cursor_001", domain="pre_review", cursor=2)

        self.assertEqual([1, 2], [item["seq"] for item in full_snapshot["logs"]])
        self.assertEqual(3, full_snapshot["next_cursor"])
        self.assertEqual([2], [item["seq"] for item in incremental_snapshot["logs"]])
        self.assertEqual(3, incremental_snapshot["next_cursor"])

    def test_concurrent_store_instances_do_not_lose_logs(self):
        first_store = self._new_store()
        second_store = self._new_store()
        first_store.create_task(
            task_id="prt_concurrent_001",
            domain="pre_review",
            project_id="project-concurrent-001",
            now=250.0,
        )

        def _append(index: int):
            store = first_store if index % 2 == 0 else second_store
            return store.append_log(
                "prt_concurrent_001",
                {"stage": "progress", "index": index},
                max_logs=500,
                assign_sequence=True,
                now=251.0 + index,
            )

        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(_append, range(80)))

        snapshot = self._new_store().get_snapshot("prt_concurrent_001", domain="pre_review", cursor=0)
        sequences = [item["seq"] for item in snapshot["logs"]]
        self.assertTrue(all(results))
        self.assertEqual(80, len(snapshot["logs"]))
        self.assertEqual(list(range(80)), sorted(sequences))

    def test_project_cleanup_is_scoped_by_domain(self):
        store = self._new_store()
        store.create_task(
            task_id="fcrt_cleanup_001",
            domain="filing_change_review",
            project_id="shared-project-001",
            now=300.0,
        )
        store.create_task(
            task_id="prt_cleanup_001",
            domain="pre_review",
            project_id="shared-project-001",
            now=301.0,
        )

        deleted_count = store.delete_by_project("shared-project-001", domain="filing_change_review")

        self.assertEqual(1, deleted_count)
        self.assertIsNone(store.get_task("fcrt_cleanup_001", domain="filing_change_review"))
        self.assertIsNotNone(store.get_task("prt_cleanup_001", domain="pre_review"))

    def test_prune_only_removes_expired_finished_tasks(self):
        store = self._new_store()
        store.create_task(
            task_id="prt_expired_001",
            domain="pre_review",
            project_id="project-prune-001",
            now=400.0,
        )
        store.update_task("prt_expired_001", status="completed", now=401.0)
        store.create_task(
            task_id="prt_running_001",
            domain="pre_review",
            project_id="project-prune-001",
            status="running",
            now=400.0,
        )
        store.create_task(
            task_id="fcrt_expired_001",
            domain="filing_change_review",
            project_id="project-prune-001",
            now=400.0,
        )
        store.update_task("fcrt_expired_001", status="failed", now=401.0)

        deleted_count = store.prune_finished(domain="pre_review", retention_seconds=10, now=500.0)

        self.assertEqual(1, deleted_count)
        self.assertIsNone(store.get_task("prt_expired_001", domain="pre_review"))
        self.assertIsNotNone(store.get_task("prt_running_001", domain="pre_review"))
        self.assertIsNotNone(store.get_task("fcrt_expired_001", domain="filing_change_review"))

    def test_stale_running_task_is_reported_as_interrupted_failure(self):
        store = self._new_store()
        store.create_task(
            task_id="prt_interrupted_001",
            domain="pre_review",
            project_id="project-interrupted-001",
            worker_id="worker-before-restart",
            now=500.0,
        )
        self.assertTrue(
            store.claim_task(
                "prt_interrupted_001",
                worker_id="worker-before-restart",
                now=501.0,
            )
        )
        self.assertTrue(
            store.heartbeat(
                "prt_interrupted_001",
                worker_id="worker-before-restart",
                now=509.0,
            )
        )
        self.assertEqual(
            [],
            store.recover_stale_active_tasks(
                domain="pre_review",
                stale_after_seconds=10,
                now=518.0,
            ),
        )

        recovered = store.recover_stale_active_tasks(
            domain="pre_review",
            stale_after_seconds=10,
            now=520.0,
        )
        snapshot = self._new_store().get_snapshot("prt_interrupted_001", domain="pre_review")

        self.assertEqual(["prt_interrupted_001"], recovered)
        self.assertEqual("failed", snapshot["status"])
        self.assertTrue(snapshot["done"])
        self.assertTrue(snapshot["result"]["interrupted"])
        self.assertEqual("task_interrupted", snapshot["logs"][-1]["stage"])
        self.assertFalse(
            store.finish_task(
                "prt_interrupted_001",
                status="completed",
                result={"ok": True},
                worker_id="worker-before-restart",
                now=521.0,
            )
        )
        self.assertFalse(store.update_task("prt_interrupted_001", status="completed", now=522.0))
        self.assertFalse(
            store.append_log(
                "prt_interrupted_001",
                {"stage": "late_completion"},
                max_logs=500,
                assign_sequence=True,
                now=523.0,
            )
        )
        self.assertEqual("failed", store.get_task("prt_interrupted_001")["status"])

    def test_filing_parse_interruption_and_retry_are_separate_persistent_attempts(self):
        store = self._new_store()
        store.create_task(task_id='parse-old', domain='filing_change_review', project_id='p', source_doc_id='a', task_type='parse_submission', now=100)
        store.claim_task('parse-old', now=101)
        store.recover_stale_active_tasks(domain='filing_change_review', stale_after_seconds=10, now=120)
        store.create_task(task_id='parse-retry', domain='filing_change_review', project_id='p', source_doc_id='a', task_type='parse_submission', now=121)
        store.claim_task('parse-retry', now=122)
        store.finish_task('parse-retry', status='completed', result={'content_status': 'success', 'content_available': True}, now=123)
        restored = self._new_store().list_project_parse_tasks('p', 'filing_change_review')
        self.assertEqual(['parse-retry', 'parse-old'], [x['task_id'] for x in restored])
        self.assertEqual('success', restored[0]['result']['content_status'])
        self.assertEqual('failed', restored[1]['result']['content_status'])
        self.assertFalse(restored[1]['result']['content_available'])
        self.assertEqual([], store.list_project_parse_tasks('other', 'filing_change_review'))

    def test_interrupted_batch_keeps_finished_files_and_reports_unfinished_files(self):
        store = self._new_store()
        store.create_task(task_id='batch', domain='filing_change_review', project_id='p', task_type='parse_submissions_batch', now=100)
        store.claim_task('batch', now=101)
        store.update_task('batch', now=102, result={'data': {'total': 2, 'success': [{'doc_id': 'a', 'content_status': 'success'}], 'failed': [], 'pending': ['b']}})
        store.recover_stale_active_tasks(domain='filing_change_review', stale_after_seconds=10, now=120)
        restored = self._new_store().get_task('batch')
        self.assertEqual('failed', restored['status'])
        self.assertEqual('partial', restored['result']['content_status'])
        self.assertEqual('a', restored['result']['data']['success'][0]['doc_id'])
        self.assertEqual('b', restored['result']['data']['failed'][0]['doc_id'])
        self.assertEqual('task_interrupted', restored['result']['data']['failed'][0]['code'])

    def test_latest_project_task_is_lightweight_active_first_and_type_scoped(self):
        first_store = self._new_store()
        first_store.create_task(
            task_id="prt_terminal_run",
            domain="pre_review",
            project_id="project-latest-001",
            run_id="run-terminal",
            source_doc_id="doc-terminal",
            task_type="run",
            payload={"large_body": "x" * 100000},
            now=200.0,
        )
        self.assertTrue(first_store.claim_task("prt_terminal_run", now=201.0))
        self.assertTrue(
            first_store.finish_task(
                "prt_terminal_run",
                status="completed",
                result={"large_result": "y" * 100000},
                now=202.0,
            )
        )
        first_store.create_task(
            task_id="prt_active_section",
            domain="pre_review",
            project_id="project-latest-001",
            source_doc_id="doc-active",
            section_id="3.2.p.2.3",
            task_type="section_replay",
            now=100.0,
        )
        for task_id, task_type, timestamp in [
            ("prt_new_feedback", "feedback_optimize", 500.0),
            ("prt_new_p52", "p52_feedback_verify", 501.0),
            ("prt_new_upload", "upload_submission", 502.0),
        ]:
            first_store.create_task(
                task_id=task_id,
                domain="pre_review",
                project_id="project-latest-001",
                task_type=task_type,
                now=timestamp,
            )
        first_store.create_task(
            task_id="prt_other_project",
            domain="pre_review",
            project_id="project-other",
            task_type="module_replay",
            now=600.0,
        )
        first_store.create_task(
            task_id="fcrt_same_project",
            domain="filing_change_review",
            project_id="project-latest-001",
            task_type="run",
            now=601.0,
        )

        second_store = self._new_store()
        latest = second_store.get_latest_project_task(
            domain="pre_review",
            project_id="project-latest-001",
            task_types=["run", "section_replay", "module_replay"],
        )

        self.assertEqual("prt_active_section", latest["task_id"])
        self.assertEqual("section_replay", latest["task_type"])
        self.assertEqual("3.2.p.2.3", latest["section_id"])
        self.assertEqual("pending", latest["status"])
        self.assertNotIn("payload", latest)
        self.assertNotIn("result", latest)
        self.assertNotIn("logs", latest)

        self.assertTrue(second_store.claim_task("prt_active_section", now=700.0))
        self.assertTrue(
            second_store.finish_task(
                "prt_active_section",
                status="completed",
                now=701.0,
            )
        )
        fallback = self._new_store().get_latest_project_task(
            domain="pre_review",
            project_id="project-latest-001",
            task_types=["run", "section_replay", "module_replay"],
        )
        self.assertEqual("prt_active_section", fallback["task_id"])
        self.assertEqual("completed", fallback["status"])
        self.assertTrue(fallback["done"])

    def test_latest_project_task_returns_recovered_stale_terminal_only_when_requested(self):
        first_store = self._new_store()
        first_store.create_task(
            task_id="prt_stale_application",
            domain="pre_review",
            project_id="project-stale-latest",
            task_type="module_replay",
            payload={"result_style": "application"},
            worker_id="worker-before-restart",
            now=100.0,
        )

        recovered = self._new_store().recover_stale_active_tasks(
            domain="pre_review",
            stale_after_seconds=10.0,
            now=200.0,
        )
        self.assertEqual(["prt_stale_application"], recovered)

        latest_store = self._new_store()
        latest = latest_store.get_latest_project_task(
            domain="pre_review",
            project_id="project-stale-latest",
            task_types=["run", "section_replay", "module_replay"],
            include_terminal=True,
        )
        self.assertEqual("failed", latest["status"])
        self.assertTrue(latest["done"])
        self.assertIsNone(
            latest_store.get_latest_project_task(
                domain="pre_review",
                project_id="project-stale-latest",
                task_types=["run", "section_replay", "module_replay"],
                include_terminal=False,
            )
        )
        snapshot = latest_store.get_snapshot(
            "prt_stale_application",
            domain="pre_review",
        )
        self.assertEqual(
            {
                "success": False,
                "message": "任务执行进程已中断，请重新发起任务",
                "data": None,
            },
            snapshot["result"],
        )

        self.assertEqual(
            1,
            latest_store.prune_finished(
                domain="pre_review",
                retention_seconds=10,
                now=500.0,
            ),
        )
        self.assertIsNone(
            self._new_store().get_latest_project_task(
                domain="pre_review",
                project_id="project-stale-latest",
                task_types=["run", "section_replay", "module_replay"],
            )
        )

    def test_process_instance_id_changes_after_forked_pid(self):
        current_pid = runtime_task_store_module.os.getpid()
        current_instance_id = runtime_task_store_module.get_process_instance_id()
        with patch.object(runtime_task_store_module.os, "getpid", return_value=current_pid + 100000):
            forked_instance_id = runtime_task_store_module.get_process_instance_id()
            repeated_instance_id = runtime_task_store_module.get_process_instance_id()

        restored_instance_id = runtime_task_store_module.get_process_instance_id()
        self.assertNotEqual(current_instance_id, forked_instance_id)
        self.assertEqual(forked_instance_id, repeated_instance_id)
        self.assertIn(f":{current_pid + 100000}:", forked_instance_id)
        self.assertNotEqual(forked_instance_id, restored_instance_id)
        self.assertIn(f":{current_pid}:", restored_instance_id)

    def test_existing_runtime_table_is_upgraded_with_lease_columns(self):
        self._new_store()
        connection = self.connections[-1]
        with connection.engine.begin() as db_connection:
            db_connection.execute(text("DROP INDEX ix_runtime_task_worker_id"))
            db_connection.execute(text("DROP INDEX ix_runtime_task_heartbeat_at"))
            db_connection.execute(text("ALTER TABLE runtime_task DROP COLUMN worker_id"))
            db_connection.execute(text("ALTER TABLE runtime_task DROP COLUMN heartbeat_at"))
        connection.engine.dispose()

        upgraded_store = self._new_store()
        column_names = {item["name"] for item in inspect(upgraded_store.db.engine).get_columns("runtime_task")}
        index_names = {item["name"] for item in inspect(upgraded_store.db.engine).get_indexes("runtime_task")}
        self.assertIn("worker_id", column_names)
        self.assertIn("heartbeat_at", column_names)
        self.assertIn("ix_runtime_task_worker_id", index_names)
        self.assertIn("ix_runtime_task_heartbeat_at", index_names)

    def test_upload_task_status_is_mirrored_in_same_store_transaction(self):
        store = self._new_store()
        connection = self.connections[-1]
        PreReviewProject.__table__.create(bind=connection.engine, checkfirst=True)
        PreReviewUploadTask.__table__.create(bind=connection.engine, checkfirst=True)
        session = connection.get_session()
        try:
            now = datetime.fromtimestamp(700.0)
            session.add(
                PreReviewProject(
                    project_id="project-upload-001",
                    project_name="upload persistence project",
                    status="created",
                    progress=0.0,
                    create_time=now,
                    update_time=now,
                    is_deleted=False,
                )
            )
            session.add(
                PreReviewUploadTask(
                    task_id="prt_upload_001",
                    project_id="project-upload-001",
                    section_id="section-001",
                    task_type="upload_submission",
                    status="pending",
                    file_count=1,
                    message="created",
                    result_json="",
                    error_message="",
                    payload_json="{}",
                    create_time=now,
                    update_time=now,
                    finish_time=None,
                )
            )
            session.commit()
        finally:
            session.close()

        store.create_task(
            task_id="prt_upload_001",
            domain="pre_review",
            project_id="project-upload-001",
            section_id="section-001",
            task_type="upload_submission",
            now=700.0,
        )
        self.assertTrue(store.claim_task("prt_upload_001", now=701.0))
        self.assertTrue(
            store.finish_task(
                "prt_upload_001",
                status="completed",
                message="upload completed",
                result={"ok": True},
                final_log={"stage": "upload_done", "message": "upload completed"},
                now=702.0,
            )
        )

        session = connection.get_session()
        try:
            upload_row = session.query(PreReviewUploadTask).filter_by(task_id="prt_upload_001").one()
            self.assertEqual("completed", upload_row.status)
            self.assertEqual("upload completed", upload_row.message)
            self.assertEqual('{"ok": true}', upload_row.result_json)
            self.assertIsNotNone(upload_row.finish_time)
        finally:
            session.close()

    def test_delete_task_can_compensate_upload_task_pair(self):
        store = self._new_store()
        connection = self.connections[-1]
        PreReviewProject.__table__.create(bind=connection.engine, checkfirst=True)
        PreReviewUploadTask.__table__.create(bind=connection.engine, checkfirst=True)
        now = datetime.fromtimestamp(800.0)
        session = connection.get_session()
        try:
            session.add(
                PreReviewProject(
                    project_id="project-upload-cleanup-001",
                    project_name="upload cleanup project",
                    status="created",
                    progress=0.0,
                    create_time=now,
                    update_time=now,
                    is_deleted=False,
                )
            )
            session.add(
                PreReviewUploadTask(
                    task_id="prt_upload_cleanup_001",
                    project_id="project-upload-cleanup-001",
                    section_id="section-cleanup-001",
                    task_type="upload_submission",
                    status="pending",
                    file_count=1,
                    message="created",
                    result_json="",
                    error_message="",
                    payload_json="{}",
                    create_time=now,
                    update_time=now,
                    finish_time=None,
                )
            )
            session.commit()
        finally:
            session.close()

        store.create_task(
            task_id="prt_upload_cleanup_001",
            domain="pre_review",
            project_id="project-upload-cleanup-001",
            section_id="section-cleanup-001",
            task_type="upload_submission",
            now=800.0,
        )

        self.assertEqual(
            1,
            store.delete_task("prt_upload_cleanup_001", include_upload_task=True),
        )
        self.assertIsNone(store.get_task("prt_upload_cleanup_001", domain="pre_review"))
        session = connection.get_session()
        try:
            self.assertIsNone(
                session.query(PreReviewUploadTask)
                .filter_by(task_id="prt_upload_cleanup_001")
                .one_or_none()
            )
        finally:
            session.close()


if __name__ == "__main__":
    unittest.main()
