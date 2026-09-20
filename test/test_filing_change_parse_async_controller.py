import json
import sys
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch


class _UnionableMeta(type):
    def __or__(cls, other):
        return object


class _StubServiceType(metaclass=_UnionableMeta):
    pass


class _StubStoreType(metaclass=_UnionableMeta):
    pass


class _DummyBlueprint:
    def __init__(self, *args, **kwargs):
        pass

    @staticmethod
    def _decorator(*args, **kwargs):
        return lambda callback: callback

    post = _decorator
    get = _decorator


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


class _FakeRequest:
    def __init__(self):
        self.json_payload = {}
        self.args = {}
        self.form = {}
        self.files = {}

    def get_json(self, silent=True):
        return self.json_payload


class _Upload:
    def __init__(self, filename="application.pdf", content=b"pdf bytes"):
        self.filename = filename
        self._content = content

    def read(self, size=-1):
        if not self._content:
            return b""
        if size is None or size < 0:
            value, self._content = self._content, b""
            return value
        value, self._content = self._content[:size], self._content[size:]
        return value


class _CreationLock:
    def __init__(self, service, project_id):
        self.service = service
        self.project_id = project_id

    def __enter__(self):
        self.service.locked_projects.add(self.project_id)
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.service.locked_projects.discard(self.project_id)


class _FakeService:
    def __init__(self):
        self.locked_projects = set()
        self.calls = []
        self.staged_files = set()
        self.cleaned_files = []

    def project_task_creation_lock(self, project_id):
        return _CreationLock(self, project_id)

    def parse_application_form(self, project_id):
        self.calls.append(("application", project_id))
        return True, "success", {"project_id": project_id}

    def parse_submission(self, project_id, doc_id):
        self.calls.append(("submission", project_id, doc_id))
        return True, "success", {"doc_id": doc_id, "parsed_chunks": 3}

    def batch_parse_submissions(self, project_id, payload):
        self.calls.append(("batch", project_id, payload))
        return True, "success", {"total": len(payload.get("doc_ids", []))}

    def parse_reference_material(self, doc_id):
        self.calls.append(("reference", doc_id))
        return True, "success", {"doc_id": doc_id, "parsed_chunks": 2}

    def stage_application_form_import(self, project_id, upload_file):
        self.assert_project_locked(project_id)
        if upload_file is None:
            return False, "file is required", None
        staging_file = "application-form-token.pdf"
        self.staged_files.add((project_id, staging_file))
        return True, "success", {
            "staging_file": staging_file,
            "original_file_name": upload_file.filename,
            "file_size": len(upload_file.read()),
            "file_type": "pdf",
        }

    def import_staged_application_form(self, project_id, staging_file, original_file_name):
        self.calls.append(("import", project_id, staging_file, original_file_name))
        if (project_id, staging_file) not in self.staged_files:
            return False, "staging missing", None
        return True, "success", {"project_id": project_id, "parse_status": "success"}

    def cleanup_application_form_staging(self, project_id, staging_file):
        self.staged_files.discard((project_id, staging_file))
        self.cleaned_files.append((project_id, staging_file))

    def assert_project_locked(self, project_id):
        if project_id not in self.locked_projects:
            raise AssertionError("project lock is required")


class _FakeStore:
    def __init__(self):
        self.tasks = {}
        self.append_result = True
        self.raise_after_create = False
        self.recovered_task_ids = []

    def recover_stale_active_tasks(self, **kwargs):
        return list(self.recovered_task_ids)

    def prune_finished(self, **kwargs):
        return 0

    def list_project_parse_tasks(self, project_id, domain):
        return [task for task in reversed(list(self.tasks.values())) if task.get('project_id') == project_id and task.get('domain') == domain]

    def create_task(self, **fields):
        task_id = fields["task_id"]
        self.tasks[task_id] = {
            **fields,
            "result": None,
            "logs": [],
            "error_message": "",
        }
        if self.raise_after_create:
            raise RuntimeError("injected create failure after persistence")
        return dict(self.tasks[task_id])

    def append_log(self, task_id, event_payload, **kwargs):
        if not self.append_result:
            return False
        task = self.tasks.get(task_id)
        if task is None or task.get("status") in {"completed", "failed"}:
            return False
        task["logs"].append(dict(event_payload))
        return True

    def claim_task(self, task_id, **kwargs):
        task = self.tasks.get(task_id)
        if task is None or task.get("status") != "pending":
            return False
        task["status"] = "running"
        return True

    @contextmanager
    def heartbeat_context(self, task_id, **kwargs):
        yield self.claim_task(task_id)

    def finish_task(self, task_id, **fields):
        task = self.tasks.get(task_id)
        if task is None or task.get("status") != "running":
            return False
        final_log = fields.pop("final_log", None)
        task.update(fields)
        if final_log:
            task["logs"].append(dict(final_log))
        return True

    def get_task(self, task_id, domain=""):
        return self.tasks.get(task_id)

    def get_snapshot(self, task_id, domain="", cursor=0):
        task = self.tasks.get(task_id)
        if task is None:
            return None
        data = dict(task)
        data.update({"cursor": int(cursor), "next_cursor": len(task["logs"]), "done": task["status"] in {"completed", "failed"}})
        return data


class _ImmediateThread:
    def __init__(self, target, **kwargs):
        self.target = target

    def start(self):
        self.target()


class _FailingThread(_ImmediateThread):
    def start(self):
        raise RuntimeError("injected thread start failure")


def _load_controller_module():
    flask_module = types.ModuleType("flask")
    flask_module.Blueprint = _DummyBlueprint
    flask_module.jsonify = lambda payload: payload
    flask_module.request = _FakeRequest()
    flask_module.send_file = lambda *args, **kwargs: None

    app_service_module = types.ModuleType(
        "agent.agent_backend.application.filing_change_review_app_service"
    )
    app_service_module.FilingChangeReviewAppService = _StubServiceType
    runtime_store_module = types.ModuleType(
        "agent.agent_backend.services.runtime_task_store"
    )
    runtime_store_module.RuntimeTaskStore = _StubStoreType
    common_util_module = types.ModuleType("agent.agent_backend.utils.common_util")
    common_util_module.ResponseMessage = _ResponseMessage

    controller_path = (
        Path(__file__).resolve().parents[1]
        / "agent_backend"
        / "controller"
        / "filing_change_review_controller.py"
    )
    module = types.ModuleType("_filing_change_parse_async_controller_under_test")
    module.__file__ = str(controller_path)
    stub_modules = {
        "flask": flask_module,
        "agent.agent_backend.application.filing_change_review_app_service": app_service_module,
        "agent.agent_backend.services.runtime_task_store": runtime_store_module,
        "agent.agent_backend.utils.common_util": common_util_module,
    }
    with patch.dict(sys.modules, stub_modules):
        source = controller_path.read_text(encoding="utf-8-sig")
        exec(compile("from __future__ import annotations\n" + source, str(controller_path), "exec"), module.__dict__)
    return module


class FilingChangeParseAsyncControllerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.controller = _load_controller_module()

    def setUp(self):
        self.service = _FakeService()
        self.store = _FakeStore()
        self.controller._service = self.service
        self.controller._runtime_task_store = self.store
        self.controller._task_time = lambda: "2026-08-18 12:00:00"
        self.controller.request.json_payload = {}
        self.controller.request.args = {}
        self.controller.request.files = {}

    @staticmethod
    def _body(response):
        value = response[0] if isinstance(response, tuple) else response
        return json.loads(value)

    def test_all_parse_start_endpoints_persist_explicit_task_types(self):
        cases = [
            (self.controller.parse_application_form_async, ("project-a",), "parse_application_form", "", {}),
            (self.controller.parse_submission_async, ("project-b", "doc-b"), "parse_submission", "doc-b", {}),
            (
                self.controller.batch_parse_submissions_async,
                ("project-c",),
                "parse_submissions_batch",
                "",
                {"doc_ids": ["doc-1", "doc-2"], "all": False},
            ),
            (self.controller.parse_reference_material_async, ("ref-d",), "parse_reference_material", "ref-d", {}),
        ]
        for endpoint, args, task_type, source_doc_id, request_payload in cases:
            with self.subTest(task_type=task_type):
                self.setUp()
                self.controller.request.json_payload = request_payload

                def _assert_started_while_project_lock_is_held(task_id, *worker_args):
                    task = self.store.tasks[task_id]
                    if task["project_id"]:
                        self.assertIn(task["project_id"], self.service.locked_projects)
                    return True

                with patch.object(self.controller, "_run_parse_task_async", side_effect=_assert_started_while_project_lock_is_held):
                    response = endpoint(*args)
                body = self._body(response)
                self.assertEqual(200, body["code"])
                task = self.store.tasks[body["data"]["task_id"]]
                self.assertEqual(task_type, task["task_type"])
                self.assertEqual(source_doc_id, task["source_doc_id"])
                self.assertEqual(request_payload, task["payload"])
                self.assertEqual([], self.service.calls, "HTTP 请求线程不应执行解析")

    def test_worker_persists_result_and_terminal_log_atomically(self):
        with patch.object(self.controller.threading, "Thread", _ImmediateThread):
            response = self.controller.parse_submission_async("project-1", "doc-1")
        body = self._body(response)
        task = self.store.tasks[body["data"]["task_id"]]
        self.assertEqual("completed", task["status"])
        self.assertEqual("parse_submission", task["result"]["task_type"])
        self.assertEqual("doc-1", task["result"]["source_doc_id"])
        self.assertEqual(3, task["result"]["data"]["parsed_chunks"])
        self.assertEqual("申报资料解析完成", task["logs"][-1]["message"])

    def test_duplicate_click_reuses_active_object_but_other_file_starts(self):
        with patch.object(self.controller, '_run_parse_task_async', return_value=True):
            first = self._body(self.controller.parse_submission_async('p', 'a'))['data']
            duplicate = self._body(self.controller.parse_submission_async('p', 'a'))['data']
            other = self._body(self.controller.parse_submission_async('p', 'b'))['data']
        self.assertEqual(first['task_id'], duplicate['task_id'])
        self.assertNotEqual(first['task_id'], other['task_id'])
        self.assertEqual(2, len(self.store.tasks))

    def test_overlapping_batch_is_rejected_and_disjoint_batch_is_allowed(self):
        with patch.object(self.controller, '_run_parse_task_async', return_value=True):
            self.controller.parse_submission_async('p', 'a')
            self.controller.request.json_payload = {'doc_ids': ['a', 'b']}
            conflict = self._body(self.controller.batch_parse_submissions_async('p'))
            self.assertEqual(409, conflict['code'])
            self.assertEqual(1, len(self.store.tasks))
            self.controller.request.json_payload = {'doc_ids': ['b', 'c']}
            first = self._body(self.controller.batch_parse_submissions_async('p'))
            duplicate = self._body(self.controller.batch_parse_submissions_async('p'))
        self.assertEqual(first['data']['task_id'], duplicate['data']['task_id'])
        self.assertEqual(2, len(self.store.tasks))

    def test_thread_constructor_failure_is_terminal_and_does_not_expose_paths(self):
        with patch.object(self.controller.threading, 'Thread', side_effect=RuntimeError('/private/server/path')):
            response = self._body(self.controller.parse_submission_async('p', 'a'))
        task = self.store.tasks[response['data']['task_id']]
        self.assertEqual('failed', task['status'])
        self.assertEqual('failed', task['result']['content_status'])
        self.assertNotIn('/private', task['error_message'])

    def test_partial_result_survives_refresh_and_retry_creates_new_attempt(self):
        self.service.parse_submission = lambda *_: (True, '部分成功', {'content_status': 'partial', 'content_available': True, 'parse_diagnostics': {'failed_pages': [2]}})
        with patch.object(self.controller.threading, 'Thread', _ImmediateThread):
            first = self._body(self.controller.parse_submission_async('p', 'a'))['data']
            second = self._body(self.controller.parse_submission_async('p', 'a'))['data']
        self.assertNotEqual(first['task_id'], second['task_id'])
        result = self.store.tasks[first['task_id']]['result']
        self.assertEqual('partial', result['content_status'])
        self.assertEqual([2], result['data']['parse_diagnostics']['failed_pages'])
        restored = self._body(self.controller.project_parse_tasks('p'))['data']
        self.assertEqual(2, len(restored))
        self.assertNotIn('payload', restored[0])

    def test_application_form_import_stages_then_parses_and_always_cleans(self):
        self.controller.request.files = {"file": _Upload()}
        def _assert_prune_outside_project_lock():
            self.assertEqual(set(), self.service.locked_projects)

        with patch.object(
            self.controller,
            "_prune_tasks",
            side_effect=_assert_prune_outside_project_lock,
        ), patch.object(self.controller.threading, "Thread", _ImmediateThread):
            response = self.controller.import_application_form_async("project-import")
        body = self._body(response)
        task = self.store.tasks[body["data"]["task_id"]]
        self.assertEqual("completed", task["status"])
        self.assertEqual("import_application_form", task["task_type"])
        self.assertEqual("import_application_form", task["result"]["task_type"])
        self.assertEqual({}, {item: True for item in self.service.staged_files})
        self.assertEqual(
            [("project-import", "application-form-token.pdf")],
            self.service.cleaned_files,
        )
        self.assertNotIn("bytes", json.dumps(task["payload"]))

    def test_application_form_import_cleans_staging_when_initial_log_fails(self):
        self.controller.request.files = {"file": _Upload()}
        self.store.append_result = False
        worker = Mock(return_value=True)
        with patch.object(self.controller, "_run_parse_task_async", worker):
            response = self.controller.import_application_form_async("project-log-fail")
        self.assertEqual(500, response[1])
        self.assertEqual(set(), self.service.staged_files)
        worker.assert_not_called()

    def test_application_form_import_cleans_staging_when_thread_start_fails(self):
        self.controller.request.files = {"file": _Upload()}
        with patch.object(self.controller.threading, "Thread", _FailingThread):
            response = self.controller.import_application_form_async("project-thread-fail")
        self.assertEqual(500, response[1])
        self.assertEqual(set(), self.service.staged_files)

    def test_initial_log_failure_is_compensated_before_worker_start(self):
        self.store.append_result = False
        worker = Mock(return_value=True)
        with patch.object(self.controller, "_run_parse_task_async", worker):
            response = self.controller.parse_submission_async("project-2", "doc-2")
        body = self._body(response)
        self.assertEqual(500, response[1])
        task = self.store.tasks[body["data"]["task_id"]]
        self.assertEqual("failed", task["status"])
        self.assertIn("初始日志", task["error_message"])
        worker.assert_not_called()

    def test_create_exception_after_persistence_is_compensated(self):
        self.store.raise_after_create = True
        worker = Mock(return_value=True)
        with patch.object(self.controller, "_run_parse_task_async", worker):
            response = self.controller.parse_application_form_async("project-create-fail")
        body = self._body(response)
        self.assertEqual(500, response[1])
        task = self.store.tasks[body["data"]["task_id"]]
        self.assertEqual("failed", task["status"])
        self.assertEqual("parse_application_form", task["result"]["task_type"])
        worker.assert_not_called()

    def test_thread_start_failure_is_persisted_as_failed_terminal_state(self):
        with patch.object(self.controller.threading, "Thread", _FailingThread):
            response = self.controller.parse_reference_material_async("ref-fail")
        body = self._body(response)
        self.assertEqual(500, response[1])
        task = self.store.tasks[body["data"]["task_id"]]
        self.assertEqual("failed", task["status"])
        self.assertIn("启动失败", task["error_message"])
        self.assertEqual("task_start_failed", task["logs"][-1]["stage"])
        self.assertEqual("parse_reference_material", task["result"]["task_type"])

    def test_progress_exposes_task_type_result_error_and_cursor(self):
        with patch.object(self.controller.threading, "Thread", _ImmediateThread):
            response = self.controller.parse_application_form_async("project-progress")
        task_id = self._body(response)["data"]["task_id"]
        self.controller.request.args = {"cursor": "1"}
        progress = self.controller.runtime_task_progress(task_id)
        data = progress["data"]
        self.assertEqual("parse_application_form", data["task_type"])
        self.assertTrue(data["done"])
        self.assertTrue(data["result"]["ok"])
        self.assertEqual(1, data["cursor"])
        self.assertIn("error_message", data)

    def test_prune_cleans_staging_left_by_interrupted_import_worker(self):
        task_id = "fcpt-stale-import"
        project_id = "project-stale-import"
        staging_file = "application-form-stale.pdf"
        self.service.staged_files.add((project_id, staging_file))
        self.store.tasks[task_id] = {
            "task_id": task_id,
            "domain": "filing_change_review",
            "task_type": "import_application_form",
            "project_id": project_id,
            "source_doc_id": staging_file,
            "status": "failed",
            "message": "interrupted",
            "payload": {"staging_file": staging_file, "original_file_name": "form.pdf"},
            "result": {"ok": False},
            "logs": [],
            "error_message": "interrupted",
        }
        self.store.recovered_task_ids = [task_id]

        self.controller._prune_tasks()

        self.assertEqual(set(), self.service.staged_files)
        self.assertEqual([(project_id, staging_file)], self.service.cleaned_files)


if __name__ == "__main__":
    unittest.main()
