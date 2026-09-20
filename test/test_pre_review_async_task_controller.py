import json
import sys
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


class _UnionableMeta(type):
    def __or__(cls, other):
        return object


class _StubService(metaclass=_UnionableMeta):
    pass


class _StubRuntimeTaskStore(metaclass=_UnionableMeta):
    pass


class _DummyBlueprint:
    def __init__(self, *args, **kwargs):
        pass

    @staticmethod
    def _decorator(*args, **kwargs):
        return lambda callback: callback

    post = _decorator
    get = _decorator


class _DummyResponse:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs


class _ResponseMessage:
    def __init__(self, code, message, data=None):
        self.code = code
        self.message = message
        self.data = data

    def to_json(self):
        return json.dumps(
            {"code": self.code, "message": self.message, "data": self.data},
            ensure_ascii=False,
        )


class _FileMap(dict):
    def getlist(self, key):
        value = self.get(key, [])
        return value if isinstance(value, list) else [value]


class _FakeRequest:
    def __init__(self):
        self.json_payload = {}
        self.form = {}
        self.files = _FileMap()
        self.args = {}

    def get_json(self, silent=True):
        return self.json_payload


class _UploadFile:
    filename = "submission.pdf"

    @staticmethod
    def read():
        return b"submission bytes"


class _InlineThread:
    def __init__(self, *, target, name="", daemon=False):
        self.target = target
        self.name = name
        self.daemon = daemon

    def start(self):
        self.target()


class _FailStartThread(_InlineThread):
    def start(self):
        raise RuntimeError("thread start failed")


class _FailConstructThread:
    def __init__(self, *args, **kwargs):
        raise RuntimeError("thread construction failed")


class _FakeStore:
    def __init__(self, upload_rows):
        self.tasks = {}
        self.upload_rows = upload_rows
        self.raise_after_create = False
        self.append_result = True
        self.delete_calls = []
        self.recover_calls = 0
        self.recover_error = None
        self.latest_task = None
        self.latest_calls = []

    def recover_stale_active_tasks(self, **kwargs):
        self.recover_calls += 1
        if self.recover_error is not None:
            raise self.recover_error
        return []

    def prune_finished(self, **kwargs):
        return 0

    def create_task(self, **fields):
        task_id = fields["task_id"]
        self.tasks[task_id] = dict(fields)
        if self.raise_after_create:
            raise RuntimeError("create failed after persistence")
        return dict(fields)

    def append_log(self, task_id, event_payload, **kwargs):
        if not self.append_result:
            return False
        self.tasks[task_id].setdefault("logs", []).append(dict(event_payload))
        return True

    def delete_task(self, task_id, *, include_upload_task=False):
        self.delete_calls.append((task_id, include_upload_task))
        deleted = int(task_id in self.tasks)
        self.tasks.pop(task_id, None)
        if include_upload_task:
            self.upload_rows.pop(task_id, None)
        return deleted

    def claim_task(self, task_id):
        if task_id not in self.tasks:
            return False
        self.tasks[task_id]["status"] = "running"
        return True

    def finish_task(self, task_id, **fields):
        if task_id not in self.tasks:
            return False
        self.tasks[task_id].update(fields)
        return True

    def get_task(self, task_id, domain=""):
        task = self.tasks.get(task_id)
        if task is None:
            return None
        if domain and task.get("domain") != domain:
            return None
        return dict(task)

    def get_latest_project_task(self, **kwargs):
        self.latest_calls.append(dict(kwargs))
        return dict(self.latest_task) if isinstance(self.latest_task, dict) else None


class _FakePreReviewService:
    def __init__(self, upload_rows):
        self.upload_rows = upload_rows
        self.create_upload_calls = 0
        self.persist_upload_then_return_none = False
        self.store = None
        self.deleted_projects = set()
        self._project_locks = {}

    def project_task_creation_lock(self, project_id):
        return self._project_locks.setdefault(project_id, threading.RLock())

    def validate_project_task_target(self, project_id, source_doc_id=""):
        if project_id in self.deleted_projects:
            return False, "project not found", None
        return True, "success", {
            "project_id": project_id,
            "source_doc_id": source_doc_id,
        }

    def create_upload_task_record(self, **fields):
        self.create_upload_calls += 1
        task_id = fields["task_id"]
        self.upload_rows[task_id] = dict(fields)
        if self.persist_upload_then_return_none:
            return None
        return dict(fields)

    def delete_project(self, project_id):
        with self.project_task_creation_lock(project_id):
            if project_id in self.deleted_projects:
                return False, "project not found"
            active = [
                task
                for task in (self.store.tasks.values() if self.store is not None else [])
                if task.get("project_id") == project_id
                and task.get("status") in {"pending", "running"}
            ]
            if active:
                return False, "项目仍有后台任务运行中，请等待任务结束后再删除"
            self.deleted_projects.add(project_id)
            if self.store is not None:
                for task_id, task in list(self.store.tasks.items()):
                    if task.get("project_id") == project_id:
                        self.store.tasks.pop(task_id, None)
            return True, "project deleted"


class _FakeFeedbackAppService:
    pass


def _load_controller_module():
    flask_module = types.ModuleType("flask")
    flask_module.Blueprint = _DummyBlueprint
    flask_module.Response = _DummyResponse
    flask_module.request = _FakeRequest()
    flask_module.send_file = lambda *args, **kwargs: None
    flask_module.stream_with_context = lambda callback: callback

    feedback_module = types.ModuleType(
        "agent.agent_backend.application.feedback_app_service"
    )
    feedback_module.FeedbackAppService = _StubService
    pre_review_module = types.ModuleType(
        "agent.agent_backend.services.pre_review_service"
    )
    pre_review_module.PreReviewService = _StubService
    runtime_store_module = types.ModuleType(
        "agent.agent_backend.services.runtime_task_store"
    )
    runtime_store_module.RuntimeTaskStore = _StubRuntimeTaskStore
    common_util_module = types.ModuleType("agent.agent_backend.utils.common_util")
    common_util_module.ResponseMessage = _ResponseMessage

    controller_path = (
        Path(__file__).resolve().parents[1]
        / "agent_backend"
        / "controller"
        / "pre_review_controller.py"
    )
    module = types.ModuleType("_pre_review_async_task_controller_under_test")
    module.__file__ = str(controller_path)
    stub_modules = {
        "flask": flask_module,
        "agent.agent_backend.application.feedback_app_service": feedback_module,
        "agent.agent_backend.services.pre_review_service": pre_review_module,
        "agent.agent_backend.services.runtime_task_store": runtime_store_module,
        "agent.agent_backend.utils.common_util": common_util_module,
    }
    with patch.dict(sys.modules, stub_modules):
        source = controller_path.read_text(encoding="utf-8-sig")
        code = compile(
            "from __future__ import annotations\n" + source,
            str(controller_path),
            "exec",
        )
        exec(code, module.__dict__)
    return module


class PreReviewAsyncTaskControllerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.controller = _load_controller_module()

    def setUp(self):
        self.upload_rows = {}
        self.store = _FakeStore(self.upload_rows)
        self.service = _FakePreReviewService(self.upload_rows)
        self.service.store = self.store
        self.feedback_service = _FakeFeedbackAppService()
        self.controller._runtime_task_store = self.store
        self.controller._service = self.service
        self.controller._feedback_app_service = self.feedback_service
        self.controller.request.json_payload = {}
        self.controller.request.form = {}
        self.controller.request.files = _FileMap()
        self.controller.request.args = {}

    def _assert_structured_500(self, response):
        body, status = response
        payload = json.loads(body)
        self.assertEqual(500, status)
        self.assertEqual(500, payload["code"])
        self.assertIsInstance(payload["message"], str)

    @staticmethod
    def _parse_response(response):
        if isinstance(response, tuple):
            body, status = response
        else:
            body, status = response, 200
        return json.loads(body), status

    def test_four_async_endpoints_compensate_when_initial_log_raises(self):
        cases = [
            (
                "run",
                self.controller.start_pre_review_async,
                (),
                "_run_pre_review_task_async",
                {"project_id": "project-run-001", "source_doc_id": "doc-run-001"},
            ),
            (
                "section",
                self.controller.replay_submission_section_async,
                ("project-section-001", "doc-section-001", "section-001"),
                "_run_section_replay_task_async",
                {"run_config": {}},
            ),
            (
                "module",
                self.controller.replay_submission_module_async,
                ("project-module-001", "doc-module-001", "module-001"),
                "_run_module_replay_task_async",
                {"run_config": {}},
            ),
            (
                "upload",
                self.controller.upload_submission_async,
                ("project-upload-001",),
                "_run_submission_upload_task_async",
                {},
            ),
        ]
        for case_name, endpoint, args, worker_name, payload in cases:
            with self.subTest(case=case_name):
                self.setUp()
                self.controller.request.json_payload = payload
                if case_name == "upload":
                    self.controller.request.files = _FileMap(
                        {"files": [_UploadFile()]}
                    )
                worker = Mock(return_value=True)
                with patch.object(
                    self.controller,
                    "_append_pre_review_task_log",
                    side_effect=RuntimeError("initial log failed"),
                ), patch.object(self.controller, worker_name, worker):
                    response = endpoint(*args)

                self._assert_structured_500(response)
                worker.assert_not_called()
                self.assertEqual({}, self.store.tasks)
                self.assertEqual({}, self.upload_rows)
                if case_name == "upload":
                    self.assertEqual(0, self.service.create_upload_calls)

    def test_false_initial_log_result_is_compensated_before_worker_start(self):
        self.controller.request.json_payload = {
            "project_id": "project-log-false-001",
            "source_doc_id": "doc-log-false-001",
        }
        worker = Mock(return_value=True)
        with patch.object(
            self.controller,
            "_append_pre_review_task_log",
            return_value=False,
        ), patch.object(self.controller, "_run_pre_review_task_async", worker):
            response = self.controller.start_pre_review_async()

        self._assert_structured_500(response)
        worker.assert_not_called()
        self.assertEqual({}, self.store.tasks)

    def test_create_exception_after_persistence_is_compensated(self):
        self.store.raise_after_create = True
        self.controller.request.json_payload = {
            "project_id": "project-create-failure-001",
            "source_doc_id": "doc-create-failure-001",
        }
        worker = Mock(return_value=True)
        with patch.object(self.controller, "_run_pre_review_task_async", worker):
            response = self.controller.start_pre_review_async()

        self._assert_structured_500(response)
        worker.assert_not_called()
        self.assertEqual({}, self.store.tasks)

    def test_upload_record_partial_persistence_cleans_both_tables(self):
        self.service.persist_upload_then_return_none = True
        self.controller.request.files = _FileMap({"files": [_UploadFile()]})
        worker = Mock(return_value=True)
        with patch.object(
            self.controller,
            "_run_submission_upload_task_async",
            worker,
        ):
            response = self.controller.upload_submission_async(
                "project-upload-partial-001"
            )

        self._assert_structured_500(response)
        worker.assert_not_called()
        self.assertEqual(1, self.service.create_upload_calls)
        self.assertEqual({}, self.store.tasks)
        self.assertEqual({}, self.upload_rows)
        self.assertTrue(self.store.delete_calls[-1][1])

    def test_feedback_and_evaluation_async_routes_persist_metadata_and_original_results(self):
        run_metadata = {
            "run_id": "run-async-001",
            "project_id": "project-async-001",
            "source_doc_id": "doc-async-001",
        }
        self.service.get_run_task_metadata = Mock(
            return_value=(True, "success", run_metadata)
        )
        application_result = {
            "success": True,
            "message": "application completed",
            "data": {"kind": "application", "value": 1},
        }
        service_result = (
            True,
            "service completed",
            {"kind": "service", "value": 2},
        )
        self.feedback_service.submit_feedback = Mock(return_value=application_result)
        self.feedback_service.replay_cases = Mock(return_value=application_result)
        self.feedback_service.run_ablation_study = Mock(return_value=application_result)
        self.service.replay_feedback_optimize = Mock(return_value=service_result)
        self.service.replay_meta_reflection = Mock(return_value=service_result)
        self.service.optimize_p52_feedback = Mock(return_value=service_result)
        self.service.replay_verify_p52_feedback = Mock(return_value=service_result)
        self.service.replay_p52_meta_reflection = Mock(return_value=service_result)

        cases = [
            (
                "feedback_optimize",
                self.controller.optimize_feedback_async,
                ("run-async-001",),
                {
                    "section_id": "section-feedback",
                    "feedback_text": "fix it",
                    "original_output": {"raw_text": "SENSITIVE_ORIGINAL_BODY"},
                    "revised_output": {"raw_text": "SENSITIVE_REVISED_BODY"},
                },
                "section-feedback",
                application_result,
                self.feedback_service.submit_feedback,
            ),
            (
                "feedback_replay_optimize",
                self.controller.replay_feedback_optimize_async,
                ("run-async-001", "section-replay"),
                {"feedback_text": "replay"},
                "section-replay",
                {"ok": True, "message": service_result[1], "data": service_result[2]},
                self.service.replay_feedback_optimize,
            ),
            (
                "feedback_meta_reflection",
                self.controller.replay_meta_reflection_async,
                ("run-async-001", "section-meta"),
                {"feedback_text": "reflect"},
                "section-meta",
                {"ok": True, "message": service_result[1], "data": service_result[2]},
                self.service.replay_meta_reflection,
            ),
            (
                "p52_feedback_optimize",
                self.controller.optimize_p52_feedback_async,
                ("run-async-001", "3.2.p.5.2.1"),
                {"feedback_text": "p52 optimize"},
                "3.2.p.5.2.1",
                {"ok": True, "message": service_result[1], "data": service_result[2]},
                self.service.optimize_p52_feedback,
            ),
            (
                "p52_feedback_verify",
                self.controller.replay_verify_p52_feedback_async,
                ("run-async-001", "3.2.p.5.2.2"),
                {"patch_id": "patch-1"},
                "3.2.p.5.2.2",
                {"ok": True, "message": service_result[1], "data": service_result[2]},
                self.service.replay_verify_p52_feedback,
            ),
            (
                "p52_meta_reflection",
                self.controller.replay_p52_meta_reflection_async,
                ("run-async-001", "3.2.p.5.2.3"),
                {"feedback_text": "p52 reflect"},
                "3.2.p.5.2.3",
                {"ok": True, "message": service_result[1], "data": service_result[2]},
                self.service.replay_p52_meta_reflection,
            ),
            (
                "feedback_evaluation_replay",
                self.controller.replay_cases_async,
                (),
                {
                    "case_ids": ["run-async-001:section-evaluation"],
                    "version_config": {"version": "candidate"},
                },
                "section-evaluation",
                application_result,
                self.feedback_service.replay_cases,
            ),
            (
                "feedback_evaluation_ablation",
                self.controller.run_ablation_study_async,
                (),
                {
                    "case_ids": ["run-async-001:section-ablation"],
                    "version_config": {"version": "baseline"},
                    "study_config": {"include_results": False},
                },
                "section-ablation",
                application_result,
                self.feedback_service.run_ablation_study,
            ),
        ]

        for task_type, endpoint, args, payload, section_id, expected_result, operation_mock in cases:
            with self.subTest(task_type=task_type):
                before_calls = operation_mock.call_count
                self.controller.request.json_payload = payload
                with patch.object(
                    self.controller.threading,
                    "Thread",
                    _InlineThread,
                ), patch.object(
                    self.controller,
                    "_execute_pre_review_task_worker",
                    side_effect=lambda task_id, callback, **kwargs: callback(),
                ):
                    response = endpoint(*args)

                response_payload, status = self._parse_response(response)
                self.assertEqual(202, status)
                self.assertEqual(200, response_payload["code"])
                response_data = response_payload["data"]
                task_id = response_data["task_id"]
                task = self.store.tasks[task_id]
                self.assertEqual(task_type, task["task_type"])
                self.assertEqual("project-async-001", task["project_id"])
                self.assertEqual("run-async-001", task["run_id"])
                self.assertEqual("doc-async-001", task["source_doc_id"])
                self.assertEqual(section_id, task["section_id"])
                self.assertEqual("completed", task["status"])
                self.assertEqual(expected_result, task["result"])
                self.assertEqual(before_calls + 1, operation_mock.call_count)
                self.assertEqual(
                    f"/api/pre-review/runs/tasks/{task_id}/progress",
                    response_data["progress_url"],
                )
                task_payload = task["payload"]
                self.assertEqual("run-async-001", task_payload["run_id"])
                self.assertEqual(section_id, task_payload["section_id"])
                self.assertNotIn("request_payload", task_payload)
                serialized_task_payload = json.dumps(task_payload, ensure_ascii=False)
                self.assertNotIn("SENSITIVE_ORIGINAL_BODY", serialized_task_payload)
                self.assertNotIn("SENSITIVE_REVISED_BODY", serialized_task_payload)
                self.assertNotIn("version_config", serialized_task_payload)

    def test_new_async_route_compensates_initial_log_failure_before_worker_start(self):
        self.service.get_run_task_metadata = Mock(
            return_value=(
                True,
                "success",
                {
                    "run_id": "run-log-fail",
                    "project_id": "project-log-fail",
                    "source_doc_id": "doc-log-fail",
                },
            )
        )
        self.service.replay_verify_p52_feedback = Mock()
        self.controller.request.json_payload = {"patch_id": "patch-log-fail"}
        worker = Mock(return_value=True)
        with patch.object(
            self.controller,
            "_append_pre_review_task_log",
            side_effect=RuntimeError("initial log failed"),
        ), patch.object(self.controller, "_run_long_operation_task_async", worker):
            response = self.controller.replay_verify_p52_feedback_async(
                "run-log-fail",
                "3.2.p.5.2.1",
            )

        self._assert_structured_500(response)
        worker.assert_not_called()
        self.service.replay_verify_p52_feedback.assert_not_called()
        self.assertEqual({}, self.store.tasks)

    def test_new_async_route_marks_task_failed_when_thread_start_fails(self):
        self.service.get_run_task_metadata = Mock(
            return_value=(
                True,
                "success",
                {
                    "run_id": "run-start-fail",
                    "project_id": "project-start-fail",
                    "source_doc_id": "doc-start-fail",
                },
            )
        )
        self.service.optimize_p52_feedback = Mock()
        self.controller.request.json_payload = {"feedback_text": "start fail"}
        with patch.object(self.controller.threading, "Thread", _FailStartThread):
            response = self.controller.optimize_p52_feedback_async(
                "run-start-fail",
                "3.2.p.5.2.1",
            )

        self._assert_structured_500(response)
        self.service.optimize_p52_feedback.assert_not_called()
        self.assertEqual(1, len(self.store.tasks))
        task = next(iter(self.store.tasks.values()))
        self.assertEqual("failed", task["status"])
        self.assertFalse(task["result"]["ok"])

    def test_background_service_exception_is_persisted_as_failed_result(self):
        self.service.get_run_task_metadata = Mock(
            return_value=(
                True,
                "success",
                {
                    "run_id": "run-worker-fail",
                    "project_id": "project-worker-fail",
                    "source_doc_id": "doc-worker-fail",
                },
            )
        )
        self.service.replay_meta_reflection = Mock(
            side_effect=RuntimeError("model service unavailable")
        )
        self.controller.request.json_payload = {"feedback_text": "reflect"}
        with patch.object(
            self.controller.threading,
            "Thread",
            _InlineThread,
        ), patch.object(
            self.controller,
            "_execute_pre_review_task_worker",
            side_effect=lambda task_id, callback, **kwargs: callback(),
        ):
            response = self.controller.replay_meta_reflection_async(
                "run-worker-fail",
                "section-worker-fail",
            )

        _, status = self._parse_response(response)
        self.assertEqual(202, status)
        task = next(iter(self.store.tasks.values()))
        self.assertEqual("failed", task["status"])
        self.assertEqual(
            {
                "ok": False,
                "message": "model service unavailable",
                "data": None,
            },
            task["result"],
        )

    def test_application_thread_start_failure_keeps_application_result_shape(self):
        self.service.get_run_task_metadata = Mock(
            return_value=(
                True,
                "success",
                {
                    "run_id": "run-app-start-fail",
                    "project_id": "project-app-start-fail",
                    "source_doc_id": "doc-app-start-fail",
                },
            )
        )
        self.feedback_service.submit_feedback = Mock()
        self.controller.request.json_payload = {
            "section_id": "section-app-start-fail",
            "feedback_text": "start fail",
        }
        with patch.object(self.controller.threading, "Thread", _FailStartThread):
            response = self.controller.optimize_feedback_async("run-app-start-fail")

        self._assert_structured_500(response)
        task = next(iter(self.store.tasks.values()))
        self.assertEqual("failed", task["status"])
        self.assertEqual(
            {
                "success": False,
                "message": "后台任务启动失败: thread start failed",
                "data": None,
            },
            task["result"],
        )

    def test_thread_start_failure_remains_structured_when_task_store_is_unavailable(self):
        self.service.get_run_task_metadata = Mock(
            return_value=(
                True,
                "success",
                {
                    "run_id": "run-store-fail",
                    "project_id": "project-store-fail",
                    "source_doc_id": "doc-store-fail",
                },
            )
        )
        self.service.optimize_p52_feedback = Mock()
        self.store.claim_task = Mock(side_effect=RuntimeError("database unavailable"))
        self.controller.request.json_payload = {"feedback_text": "start fail"}

        with patch.object(self.controller.threading, "Thread", _FailStartThread):
            response = self.controller.optimize_p52_feedback_async(
                "run-store-fail",
                "3.2.p.5.2.1",
            )

        self._assert_structured_500(response)
        self.service.optimize_p52_feedback.assert_not_called()

    def test_worker_does_not_call_model_when_start_log_cannot_be_written(self):
        self.service.get_run_task_metadata = Mock(
            return_value=(
                True,
                "success",
                {
                    "run_id": "run-log-stopped",
                    "project_id": "project-log-stopped",
                    "source_doc_id": "doc-log-stopped",
                },
            )
        )
        self.service.optimize_p52_feedback = Mock()
        self.controller.request.json_payload = {"feedback_text": "must not execute"}
        append_calls = {"count": 0}

        def append_log(task_id, payload):
            append_calls["count"] += 1
            if append_calls["count"] == 1:
                return self.store.append_log(task_id, payload)
            return False

        def execute_claimed(task_id, callback, **kwargs):
            self.assertTrue(self.store.claim_task(task_id))
            callback()

        with patch.object(
            self.controller,
            "_append_pre_review_task_log",
            side_effect=append_log,
        ), patch.object(
            self.controller.threading,
            "Thread",
            _InlineThread,
        ), patch.object(
            self.controller,
            "_execute_pre_review_task_worker",
            side_effect=execute_claimed,
        ):
            response = self.controller.optimize_p52_feedback_async(
                "run-log-stopped",
                "3.2.p.5.2.1",
            )

        _, status = self._parse_response(response)
        self.assertEqual(202, status)
        self.service.optimize_p52_feedback.assert_not_called()
        task = next(iter(self.store.tasks.values()))
        self.assertEqual("failed", task["status"])
        self.assertIn("无法写入启动日志", task["result"]["message"])

    def test_thread_constructor_failure_discards_unpublished_tasks_for_all_helpers(self):
        cases = ["main", "long_operation", "upload"]
        for case_name in cases:
            with self.subTest(case=case_name):
                self.setUp()
                if case_name == "main":
                    self.controller.request.json_payload = {
                        "project_id": "project-constructor-main",
                        "source_doc_id": "doc-constructor-main",
                    }
                    endpoint = self.controller.start_pre_review_async
                    args = ()
                elif case_name == "long_operation":
                    self.service.get_run_task_metadata = Mock(
                        return_value=(
                            True,
                            "success",
                            {
                                "run_id": "run-constructor-long",
                                "project_id": "project-constructor-long",
                                "source_doc_id": "doc-constructor-long",
                            },
                        )
                    )
                    self.service.optimize_p52_feedback = Mock()
                    self.controller.request.json_payload = {"feedback_text": "constructor fail"}
                    endpoint = self.controller.optimize_p52_feedback_async
                    args = ("run-constructor-long", "3.2.p.5.2.1")
                else:
                    self.controller.request.files = _FileMap(
                        {"files": [_UploadFile()]}
                    )
                    endpoint = self.controller.upload_submission_async
                    args = ("project-constructor-upload",)

                with patch.object(
                    self.controller.threading,
                    "Thread",
                    _FailConstructThread,
                ):
                    response = endpoint(*args)

                self._assert_structured_500(response)
                self.assertEqual({}, self.store.tasks)
                self.assertEqual({}, self.upload_rows)

    def test_run_metadata_lookup_exception_is_a_structured_500(self):
        self.service.get_run_task_metadata = Mock(
            side_effect=RuntimeError("database unavailable")
        )
        self.controller.request.json_payload = {"feedback_text": "lookup fail"}

        response = self.controller.optimize_p52_feedback_async(
            "run-metadata-fail",
            "3.2.p.5.2.1",
        )

        self._assert_structured_500(response)
        self.assertEqual({}, self.store.tasks)

    def test_evaluation_async_rejects_missing_or_mixed_run_metadata(self):
        self.feedback_service.replay_cases = Mock()

        cases = [
            ({"case_ids": ["custom-case-without-run"]}, "无法从 case_ids 确定 run_id"),
            (
                {"case_ids": ["run-a:section-1", "run-b:section-2"]},
                "一次只能处理同一 run_id",
            ),
            (
                {"run_id": "run-a", "case_ids": ["run-b:section-2"]},
                "run_id 与 case_ids",
            ),
        ]
        for payload, message_fragment in cases:
            with self.subTest(payload=payload):
                self.controller.request.json_payload = payload
                response_payload, status = self._parse_response(
                    self.controller.replay_cases_async()
                )
                self.assertEqual(400, status)
                self.assertIn(message_fragment, response_payload["message"])

        self.feedback_service.replay_cases.assert_not_called()

    def test_delete_project_prunes_stale_tasks_before_service_delete(self):
        call_order = []

        def recover(**kwargs):
            call_order.append("recover")
            return []

        def delete(project_id):
            call_order.append("delete")
            return True, "project deleted"

        self.store.recover_stale_active_tasks = recover
        self.service.delete_project = Mock(side_effect=delete)
        response_payload, status = self._parse_response(
            self.controller.delete_project("project-delete")
        )

        self.assertEqual(200, status)
        self.assertEqual(200, response_payload["code"])
        self.assertEqual(["recover", "delete"], call_order)

    def test_delete_project_returns_structured_500_when_stale_recovery_fails(self):
        self.store.recover_error = RuntimeError("runtime task database unavailable")
        self.service.delete_project = Mock()

        response = self.controller.delete_project("project-delete")

        self._assert_structured_500(response)
        self.service.delete_project.assert_not_called()

    def test_delete_waits_while_runtime_task_creation_holds_project_lock(self):
        project_id = "project-race-create"
        source_doc_id = "doc-race-create"
        self.controller.request.json_payload = {
            "project_id": project_id,
            "source_doc_id": source_doc_id,
        }
        create_entered = threading.Event()
        release_create = threading.Event()
        delete_finished = threading.Event()
        responses = {}
        original_create = self.store.create_task

        def blocking_create(**fields):
            create_entered.set()
            self.assertTrue(release_create.wait(2.0))
            return original_create(**fields)

        def start_request():
            responses["start"] = self.controller.start_pre_review_async()

        def delete_request():
            responses["delete"] = self.controller.delete_project(project_id)
            delete_finished.set()

        with patch.object(self.store, "create_task", side_effect=blocking_create), patch.object(
            self.controller,
            "_run_pre_review_task_async",
            return_value=True,
        ):
            start_thread = threading.Thread(target=start_request)
            start_thread.start()
            self.assertTrue(create_entered.wait(1.0))
            delete_thread = threading.Thread(target=delete_request)
            delete_thread.start()
            self.assertFalse(delete_finished.wait(0.1))
            release_create.set()
            start_thread.join(2.0)
            delete_thread.join(2.0)

        self.assertFalse(start_thread.is_alive())
        self.assertFalse(delete_thread.is_alive())
        _, start_status = self._parse_response(responses["start"])
        delete_payload, delete_status = self._parse_response(responses["delete"])
        self.assertEqual(200, start_status)
        self.assertEqual(400, delete_status)
        self.assertIn("后台任务运行中", delete_payload["message"])
        self.assertNotIn(project_id, self.service.deleted_projects)
        self.assertEqual(1, len(self.store.tasks))

    def test_delete_waits_through_worker_start_and_deleted_project_cannot_restart(self):
        project_id = "project-race-thread"
        source_doc_id = "doc-race-thread"
        self.controller.request.json_payload = {
            "project_id": project_id,
            "source_doc_id": source_doc_id,
        }
        worker_entered = threading.Event()
        release_worker = threading.Event()
        delete_finished = threading.Event()
        responses = {}

        def blocking_worker(**kwargs):
            worker_entered.set()
            self.assertTrue(release_worker.wait(2.0))
            return True

        def start_request():
            responses["start"] = self.controller.start_pre_review_async()

        def delete_request():
            responses["delete"] = self.controller.delete_project(project_id)
            delete_finished.set()

        with patch.object(
            self.controller,
            "_run_pre_review_task_async",
            side_effect=blocking_worker,
        ):
            start_thread = threading.Thread(target=start_request)
            start_thread.start()
            self.assertTrue(worker_entered.wait(1.0))
            delete_thread = threading.Thread(target=delete_request)
            delete_thread.start()
            self.assertFalse(delete_finished.wait(0.1))
            release_worker.set()
            start_thread.join(2.0)
            delete_thread.join(2.0)

        _, start_status = self._parse_response(responses["start"])
        _, delete_status = self._parse_response(responses["delete"])
        self.assertEqual(200, start_status)
        self.assertEqual(400, delete_status)
        task = next(iter(self.store.tasks.values()))
        task["status"] = "failed"

        _, final_delete_status = self._parse_response(
            self.controller.delete_project(project_id)
        )
        self.assertEqual(200, final_delete_status)
        self.assertIn(project_id, self.service.deleted_projects)
        self.assertEqual({}, self.store.tasks)

        self.controller.request.json_payload = {
            "project_id": project_id,
            "source_doc_id": source_doc_id,
        }
        with patch.object(
            self.controller,
            "_run_pre_review_task_async",
            return_value=True,
        ):
            restart_payload, restart_status = self._parse_response(
                self.controller.start_pre_review_async()
            )
        self.assertEqual(404, restart_status)
        self.assertEqual("project not found", restart_payload["message"])
        self.assertEqual({}, self.store.tasks)

    def test_batch_delete_prunes_stale_tasks_and_propagates_recovery_failure(self):
        self.controller.request.json_payload = {"project_ids": ["project-delete"]}
        self.service.batch_delete_projects = Mock(
            return_value=(
                True,
                "success",
                {"deleted": ["project-delete"], "failed": []},
            )
        )
        response_payload, status = self._parse_response(
            self.controller.batch_delete_projects()
        )
        self.assertEqual(200, status)
        self.assertEqual(1, self.store.recover_calls)
        self.service.batch_delete_projects.assert_called_once()

        self.setUp()
        self.controller.request.json_payload = {"project_ids": ["project-delete"]}
        self.store.recover_error = RuntimeError("runtime task database unavailable")
        self.service.batch_delete_projects = Mock()
        response = self.controller.batch_delete_projects()
        self._assert_structured_500(response)
        self.service.batch_delete_projects.assert_not_called()

    def test_latest_project_review_task_is_recovered_and_strictly_type_scoped(self):
        self.store.latest_task = {
            "task_id": "prt-latest-main",
            "task_type": "section_replay",
            "project_id": "project-latest-main",
            "run_id": "run-latest-main",
            "source_doc_id": "doc-latest-main",
            "section_id": "3.2.p.2.3",
            "status": "running",
            "done": False,
        }
        self.controller.request.args = {"include_terminal": "0"}

        response_payload, status = self._parse_response(
            self.controller.get_latest_project_review_task("project-latest-main")
        )

        self.assertEqual(200, status)
        self.assertEqual(1, self.store.recover_calls)
        self.assertEqual(
            {
                "domain": "pre_review",
                "project_id": "project-latest-main",
                "task_types": ["run", "section_replay", "module_replay"],
                "include_terminal": False,
            },
            self.store.latest_calls[-1],
        )
        data = response_payload["data"]
        self.assertEqual("prt-latest-main", data["task_id"])
        self.assertEqual(
            "/api/pre-review/runs/tasks/prt-latest-main/progress",
            data["progress_url"],
        )

    def test_latest_project_review_task_returns_null_or_structured_500(self):
        response_payload, status = self._parse_response(
            self.controller.get_latest_project_review_task("project-no-task")
        )
        self.assertEqual(200, status)
        self.assertIsNone(response_payload["data"])

        self.setUp()
        self.store.recover_error = RuntimeError("runtime task database unavailable")
        response = self.controller.get_latest_project_review_task("project-error")
        self._assert_structured_500(response)
        self.assertEqual([], self.store.latest_calls)

    def test_read_endpoints_forward_compact_flag(self):
        self.controller.request.json_payload = {"compact": True}
        section_reader = Mock(return_value=(True, "success", {"sections": []}))
        trace_reader = Mock(return_value=[])
        self.service.get_submission_sections = section_reader
        self.service.get_section_traces = trace_reader

        self.controller.submission_sections("project-1", "doc-1")
        self.controller.get_traces("run-1")

        section_reader.assert_called_once_with(
            project_id="project-1",
            doc_id="doc-1",
            compact=True,
        )
        trace_reader.assert_called_once_with("run-1", "", compact=True)


if __name__ == "__main__":
    unittest.main()
