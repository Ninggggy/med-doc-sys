import logging
import threading
import uuid
from contextlib import nullcontext
from datetime import datetime
from typing import Any, Callable, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

from flask import Blueprint, jsonify, request, send_file

from agent.agent_backend.application.filing_change_review_app_service import FilingChangeReviewAppService
from agent.agent_backend.services.runtime_task_store import RuntimeTaskStore
from agent.agent_backend.services.filing_parse_outcome import failure, parse_task_id, log_failure
from agent.agent_backend.utils.common_util import ResponseMessage


filing_change_review_bp = Blueprint("filing_change_review_controller", __name__, url_prefix="/filing-change-review")
_service: FilingChangeReviewAppService | None = None
_runtime_task_store: RuntimeTaskStore | None = None
_TASK_DOMAIN = "filing_change_review"
_TASK_RETENTION_SECONDS = 2 * 60 * 60


def _busy_parse_response(project_id):
    active = next((t for t in get_runtime_task_store().list_project_parse_tasks(project_id, _TASK_DOMAIN)
                   if t['status'] in ('pending', 'running')), {})
    return ResponseMessage(409, '项目正在处理文件，请等待当前任务结束后重试本次提交。',
                           {k: active[k] for k in ('task_id','task_type','project_id','status') if k in active}).to_json(), 409


def _try_creation_lock(lock):
    # 旧适配器可能提供 nullcontext；真实业务锁始终保留跨进程互斥。
    return lock.try_lock() if hasattr(lock, 'try_lock') else lock


def _active_upload_response(project_id, upload_file):
    app_service = get_service()
    service = getattr(app_service, 'service', app_service)
    compare = getattr(service, 'matches_active_application_upload', None)
    for task in get_runtime_task_store().list_project_parse_tasks(project_id, _TASK_DOMAIN):
        if callable(compare) and compare(project_id, upload_file, task):
            return ResponseMessage(200, '同一文件已有导入任务，已返回现有任务。',
                {**{k:task[k] for k in ('task_id','task_type','project_id','source_doc_id','status')}, 'duplicate':True}).to_json()
    return _busy_parse_response(project_id)


def get_service() -> FilingChangeReviewAppService:
    global _service
    if _service is None:
        _service = FilingChangeReviewAppService()
    return _service


def get_runtime_task_store() -> RuntimeTaskStore:
    global _runtime_task_store
    if _runtime_task_store is None:
        _runtime_task_store = RuntimeTaskStore()
    return _runtime_task_store


def _mark_review_run_interrupted(project_id: str, run_id: str, message: str) -> None:
    app_service = get_service()
    targets = [app_service, getattr(app_service, "service", None)]
    for target in targets:
        method = getattr(target, "mark_review_run_interrupted", None)
        if not callable(method):
            continue
        try:
            method(project_id=project_id, run_id=run_id, message=message)
        except Exception:
            # runtime_task 已经保存失败态，业务状态补偿失败需留日志排查。
            logging.exception("mark filing review run interrupted failed run_id=%s", run_id)
        return


def _prune_tasks() -> None:
    store = get_runtime_task_store()
    recovered_task_ids = store.recover_stale_active_tasks(domain=_TASK_DOMAIN)
    for task_id in recovered_task_ids:
        task = store.get_task(task_id, domain=_TASK_DOMAIN)
        if task is None:
            continue
        task_type = str(task.get("task_type", "") or "")
        if task_type == "import_application_form":
            payload = task.get("payload", {}) if isinstance(task.get("payload", {}), dict) else {}
            project_id = str(task.get("project_id", "") or "")
            staging_file = str(payload.get("staging_file", "") or "")
            _safe_task_cleanup(
                task_id,
                lambda project_id=project_id, staging_file=staging_file: get_service().cleanup_application_form_staging(
                    project_id,
                    staging_file,
                ),
            )
        if task_type != "review":
            continue
        _mark_review_run_interrupted(
            str(task.get("project_id", "") or ""),
            str(task.get("run_id", "") or ""),
            str(task.get("message", "") or ""),
        )
    store.prune_finished(
        domain=_TASK_DOMAIN,
        retention_seconds=_TASK_RETENTION_SECONDS,
    )


def _append_task_log(task_id: str, stage: str, message: str) -> bool:
    return get_runtime_task_store().append_log(
        task_id,
        {
            "stage": stage,
            "message": message,
            "time": _task_time(),
        },
        max_logs=300,
        assign_sequence=False,
    )


def _task_time() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")


def _finish_parse_task_after_error(task_id: str, message: str, stage: str) -> None:
    """将已落库但未能正常启动/执行的解析任务收敛到失败终态。

    RuntimeTaskStore.finish_task 只允许 running -> 终态，因此先尝试领取 pending
    任务；如果 Worker 已领取，则直接尝试收尾。终态与最后一条日志
    在同一次数据库事务中写入。
    """
    store = get_runtime_task_store()
    try:
        task = store.get_task(task_id, domain=_TASK_DOMAIN) or {}
    except Exception as exc:
        task = {}
        log_failure(exc, '', '', 'read_task', task_id)
    result = {
        "ok": False,
        "message": message,
        "task_type": str(task.get("task_type", "") or ""),
        "project_id": str(task.get("project_id", "") or ""),
        "source_doc_id": str(task.get("source_doc_id", "") or ""),
        "content_status": "failed",
        "content_available": False,
        "data": None,
    }
    previous = task.get('result') or {}
    if previous.get('content_available'):
        result.update(content_status=previous.get('content_status'), content_available=True, data=previous.get('data'))
    try:
        store.claim_task(task_id)
    except Exception as exc:
        log_failure(exc, task.get('project_id'), task.get('source_doc_id'), 'claim_task', task_id)
    try:
        store.finish_task(
            task_id,
            status="failed",
            message=message,
            result=result,
            error_message=message,
            final_log={"stage": stage, "message": message, "time": _task_time()},
            max_logs=300,
            assign_sequence=False,
        )
    except Exception as exc:
        log_failure(exc, task.get('project_id'), task.get('source_doc_id'), 'finish_task', task_id)


def _safe_task_cleanup(task_id: str, cleanup: Optional[Callable[[], None]]) -> None:
    if not callable(cleanup):
        return
    try:
        cleanup()
    except Exception as exc:
        log_failure(exc, '', '', 'cleanup', task_id)


def _run_parse_task_async(
    task_id: str,
    task_type: str,
    project_id: str,
    source_doc_id: str,
    operation: Callable[[], Tuple[bool, str, Any]],
    start_message: str,
    success_message: str,
    cleanup: Optional[Callable[[], None]] = None,
) -> bool:
    def _worker() -> None:
        store = get_runtime_task_store()
        task_token = parse_task_id.set(task_id)
        try:
            with store.heartbeat_context(task_id) as claimed:
                if not claimed:
                    return
                if not _append_task_log(task_id, "parse", start_message):
                    raise RuntimeError("任务执行日志未能写入")
                try:
                    ok, msg, data = operation()
                except Exception as exc:
                    log_failure(exc, project_id, source_doc_id, task_type)
                    data = failure(exc)
                    ok, msg = False, data['message']
                content_status = (data or {}).get('content_status', 'success' if ok else 'failed')
                final_message = (data or {}).get('message') or (success_message if ok else f"解析失败: {msg}")
                result = {
                    "ok": bool(ok),
                    "message": str(msg or ""),
                    "task_type": task_type,
                    "project_id": project_id,
                    "source_doc_id": source_doc_id,
                    "data": data,
                    "content_status": content_status,
                    "content_available": bool((data or {}).get('content_available', ok)),
                }
                store.finish_task(
                    task_id,
                    status="completed" if ok else "failed",
                    message=str(msg or final_message),
                    result=result,
                    error_message="" if ok else str(msg or final_message),
                    final_log={"stage": "parse", "message": final_message, "time": _task_time()},
                    max_logs=300,
                    assign_sequence=False,
                )
        except Exception as exc:
            message = "解析任务执行器异常，请联系管理员检查后重试。"
            log_failure(exc, project_id, source_doc_id, 'worker_failed', task_id)
            _finish_parse_task_after_error(task_id, message, "worker_failed")
        finally:
            parse_task_id.reset(task_token)
            _safe_task_cleanup(task_id, cleanup)

    try:
        worker = threading.Thread(target=_worker, name=f"filing-parse-{task_id}", daemon=True)
        worker.start()
        return True
    except Exception as exc:
        message = "解析任务启动失败，请稍后重试或联系管理员。"
        log_failure(exc, project_id, source_doc_id, 'task_start_failed', task_id)
        _finish_parse_task_after_error(task_id, message, "task_start_failed")
        _safe_task_cleanup(task_id, cleanup)
        return False


def _start_parse_task(
    *,
    task_type: str,
    project_id: str = "",
    source_doc_id: str = "",
    payload: Optional[Dict[str, Any]] = None,
    operation: Callable[[], Tuple[bool, str, Any]],
    created_message: str,
    start_message: str,
    success_message: str,
    cleanup: Optional[Callable[[], None]] = None,
    prune_tasks: bool = True,
):
    if prune_tasks:
        try:
            _prune_tasks()
        except Exception as exc:
            message = "备案解析任务清理失败，请联系管理员检查后重试。"
            log_failure(exc, project_id, source_doc_id, 'prune_tasks')
            _safe_task_cleanup("", cleanup)
            return ResponseMessage(500, message, None).to_json(), 500

    task_id = f"fcpt_{uuid.uuid4().hex[:16]}"
    app_service = get_service()
    # 项目解析任务的“落库 + 启动”与项目删除串行，避免删除后又留下孤儿任务。
    creation_lock = app_service.project_task_creation_lock(project_id) if project_id else nullcontext()
    with _try_creation_lock(creation_lock) as acquired:
        if acquired is False:
            _safe_task_cleanup(task_id, cleanup)
            return _busy_parse_response(project_id)
        persistence_stage = 'task_persist_failed'
        try:
            store = get_runtime_task_store()
            if task_type == 'parse_submissions_batch':
                service = getattr(app_service, 'service', app_service)
                resolver = getattr(service, 'submission_task_targets', None)
                if callable(resolver):
                    # 同一payload亦由operation闭包使用，执行不再扩张文件范围。
                    payload.update(doc_ids=resolver(project_id, payload), all=False, targets_resolved=True)
            if project_id:
                for active in store.list_project_parse_tasks(project_id, _TASK_DOMAIN):
                    if active['status'] not in ('pending', 'running'):
                        continue
                    same_form = task_type in ('import_application_form', 'parse_application_form') and active['task_type'] in ('import_application_form', 'parse_application_form')
                    same_doc = active['task_type'] == task_type and active.get('source_doc_id', '') == source_doc_id
                    submission_overlap = task_type in ('parse_submission', 'parse_submissions_batch') and active['task_type'] in ('parse_submission', 'parse_submissions_batch')
                    if submission_overlap:
                        requested = set((payload or {}).get('doc_ids') or ([source_doc_id] if source_doc_id else []))
                        existing = set((active.get('payload') or {}).get('doc_ids') or ([active.get('source_doc_id')] if active.get('source_doc_id') else []))
                        if (payload or {}).get('all'):
                            requested = set()
                        if (active.get('payload') or {}).get('all'):
                            existing = set()
                        submission_overlap = not requested or not existing or bool(requested & existing)
                        same_doc = False
                        if submission_overlap and (task_type != active['task_type'] or requested != existing):
                            _safe_task_cleanup(task_id, cleanup)
                            return ResponseMessage(409, '部分文件已有解析任务正在运行，请等待结束后重试本次操作。', {'task_id': active['task_id']}).to_json(), 409
                    if same_form or same_doc or submission_overlap:
                        _safe_task_cleanup(task_id, cleanup)
                        if same_form and active['task_type'] != task_type:
                            return _busy_parse_response(project_id)
                        return ResponseMessage(200, '已有解析任务正在运行', {k: active[k] for k in ('task_id', 'task_type', 'project_id', 'source_doc_id', 'status')}).to_json()
            task_payload = dict(payload or {})
            source_reader = getattr(getattr(app_service, 'service', app_service), 'application_form_task_source', None)
            if task_type == 'parse_application_form' and callable(source_reader):
                task_payload['application_form_source'] = source_reader(project_id)
            store.create_task(
                task_id=task_id,
                domain=_TASK_DOMAIN,
                project_id=project_id,
                source_doc_id=source_doc_id,
                task_type=task_type,
                status="pending",
                message=created_message,
                payload=task_payload,
            )
            persistence_stage = 'task_initial_log_failed'
            if not _append_task_log(task_id, "prepare", created_message):
                raise RuntimeError("任务初始日志未能写入")
        except Exception as exc:
            message = '解析任务初始日志保存失败，请稍后重试或联系管理员。' if persistence_stage == 'task_initial_log_failed' else '解析任务保存失败，请稍后重试或联系管理员。'
            log_failure(exc, project_id, source_doc_id, persistence_stage, task_id)
            _finish_parse_task_after_error(task_id, message, persistence_stage)
            _safe_task_cleanup(task_id, cleanup)
            return ResponseMessage(500, message, {"task_id": task_id, "status": "failed"}).to_json(), 500

        if not _run_parse_task_async(
            task_id,
            task_type,
            project_id,
            source_doc_id,
            operation,
            start_message,
            success_message,
            cleanup,
        ):
            return (
                ResponseMessage(
                    500,
                    "filing parse worker start failed",
                    {"task_id": task_id, "task_type": task_type, "status": "failed"},
                ).to_json(),
                500,
            )
    return ResponseMessage(
        200,
        "success",
        {
            "task_id": task_id,
            "task_type": task_type,
            "project_id": project_id,
            "source_doc_id": source_doc_id,
            "status": "pending",
        },
    ).to_json()


def _run_task_async(task_id: str, project_id: str, run_id: str) -> bool:
    def _worker() -> None:
        store = get_runtime_task_store()
        with store.heartbeat_context(task_id) as claimed:
            if not claimed:
                return
            _append_task_log(task_id, "run", "开始执行备案变更审评")
            try:
                ok, msg, result = get_service().execute_review_run(project_id, run_id)
            except Exception as exc:
                ok, msg = False, str(exc)
                result = None
            store.finish_task(
                task_id,
                status="completed" if ok else "failed",
                message=msg,
                error_message="" if ok else str(msg or ""),
                result=result,
                final_log={
                    "stage": "run",
                    "message": "审评完成" if ok else f"审评失败: {msg}",
                    "time": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S"),
                },
                max_logs=300,
                assign_sequence=False,
            )

    worker = threading.Thread(target=_worker, name=f"filing-change-review-{task_id}", daemon=True)
    try:
        worker.start()
        return True
    except Exception as exc:
        message = f"备案变更审评任务启动失败: {exc}"
        store = get_runtime_task_store()
        if store.claim_task(task_id):
            store.finish_task(
                task_id,
                status="failed",
                message=message,
                error_message=message,
                final_log={
                    "stage": "task_start_failed",
                    "message": message,
                    "time": datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S"),
                },
                max_logs=300,
                assign_sequence=False,
            )
        _mark_review_run_interrupted(project_id, run_id, message)
        return False


@filing_change_review_bp.post("/projects")
def create_project():
    payload = request.get_json(silent=True) or {}
    ok, msg, data = get_service().create_project(payload)
    if not ok:
        return ResponseMessage(400, msg, data).to_json(), 400
    return ResponseMessage(200, msg, data).to_json()


@filing_change_review_bp.post("/projects/list")
def list_projects():
    payload = request.get_json(silent=True) or {}
    data = get_service().list_projects(payload)
    return ResponseMessage(200, "success", data).to_json()


@filing_change_review_bp.post("/projects/<project_id>/delete")
def delete_project(project_id: str):
    ok, msg = get_service().delete_project(project_id)
    if not ok:
        return ResponseMessage(400, msg, None).to_json(), 400
    return ResponseMessage(200, msg, None).to_json()


@filing_change_review_bp.get("/projects/<project_id>/detail")
def project_detail(project_id: str):
    ok, msg, data = get_service().get_project_detail(project_id)
    if not ok:
        return ResponseMessage(404, msg, data).to_json(), 404
    return ResponseMessage(200, msg, data).to_json()


@filing_change_review_bp.post("/projects/<project_id>/application-form/save")
def save_application_form(project_id: str):
    payload = request.get_json(silent=True) or {}
    ok, msg, data = get_service().save_application_form(project_id, payload)
    if not ok:
        return ResponseMessage(400, msg, data).to_json(), 400
    return ResponseMessage(200, msg, data).to_json()


@filing_change_review_bp.get("/projects/<project_id>/application-form")
def get_application_form(project_id: str):
    ok, msg, data = get_service().get_application_form(project_id)
    if not ok:
        return ResponseMessage(400, msg, data).to_json(), 400
    return ResponseMessage(200, msg, data).to_json()


def _import_application_form_response(project_id: str):
    upload_file = request.files.get("file")
    ok, msg, data = get_service().import_application_form(project_id, upload_file)
    if not ok:
        return ResponseMessage(400, msg, data).to_json(), 400
    return ResponseMessage(200, msg, data).to_json()


@filing_change_review_bp.post("/projects/<project_id>/application-form/import")
def import_application_form(project_id: str):
    return _import_application_form_response(project_id)


@filing_change_review_bp.post("/projects/<project_id>/application-form/import-word")
def import_application_form_word(project_id: str):
    """保留历史路由，兼容尚未升级的前端。"""
    return _import_application_form_response(project_id)


@filing_change_review_bp.post("/projects/<project_id>/application-form/import/start")
@filing_change_review_bp.post("/projects/<project_id>/application-form/import-async")
def import_application_form_async(project_id: str):
    """multipart 请求只负责安全落盘，Word/PDF/OCR 解析由持久化后台任务执行。"""
    upload_file = request.files.get("file")
    app_service = get_service()
    try:
        # 过期任务清理必须在项目锁之外，避免与其他项目的中断补偿交叉等待。
        _prune_tasks()
    except Exception as exc:
        message = f"备案解析任务清理失败: {exc}"
        logging.exception("prune filing import tasks failed project_id=%s", project_id)
        return ResponseMessage(500, message, None).to_json(), 500
    # staging、任务落库和 Worker 启动必须与项目删除串行。
    with _try_creation_lock(app_service.project_task_creation_lock(project_id)) as acquired:
        if acquired is False:
            return _active_upload_response(project_id, upload_file)
        active = get_runtime_task_store().list_project_parse_tasks(project_id, _TASK_DOMAIN)
        if any(t['status'] in ('pending', 'running') and t['task_type'] in ('import_application_form','parse_application_form') for t in active):
            return _active_upload_response(project_id, upload_file)
        ok, msg, staged = app_service.stage_application_form_import(project_id, upload_file)
        if not ok or not isinstance(staged, dict):
            return ResponseMessage(400, msg, staged).to_json(), 400
        staging_file = str(staged.get("staging_file", "") or "")
        original_file_name = str(staged.get("original_file_name", "") or "")
        cleanup = lambda: app_service.cleanup_application_form_staging(project_id, staging_file)
        return _start_parse_task(
            task_type="import_application_form",
            project_id=project_id,
            source_doc_id=staging_file,
            payload={
                "staging_file": staging_file,
                "original_file_name": original_file_name,
                "file_size": int(staged.get("file_size", 0) or 0),
                "file_type": str(staged.get("file_type", "") or ""),
            },
            operation=lambda: app_service.import_staged_application_form(
                project_id,
                staging_file,
                original_file_name,
            ),
            created_message="申请表导入任务已创建，等待执行",
            start_message="开始导入并解析申请表",
            success_message="申请表导入和解析完成",
            cleanup=cleanup,
            prune_tasks=False,
        )


@filing_change_review_bp.post("/projects/<project_id>/application-form/parse")
def parse_application_form(project_id: str):
    ok, msg, data = get_service().parse_application_form(project_id)
    if not ok:
        return ResponseMessage(400, msg, data).to_json(), 400
    return ResponseMessage(200, msg, data).to_json()


@filing_change_review_bp.post("/projects/<project_id>/application-form/parse/start")
@filing_change_review_bp.post("/projects/<project_id>/application-form/parse-async")
def parse_application_form_async(project_id: str):
    return _start_parse_task(
        task_type="parse_application_form",
        project_id=project_id,
        operation=lambda: get_service().parse_application_form(project_id),
        created_message="申请表解析任务已创建，等待执行",
        start_message="开始解析申请表",
        success_message="申请表解析完成",
    )


@filing_change_review_bp.post("/projects/<project_id>/submissions/upload")
def upload_submissions(project_id: str):
    files = [f for f in request.files.getlist("files") if getattr(f, "filename", "")]
    material_category = request.form.get("material_category", "other")
    material_sub_category = request.form.get("material_sub_category", "")
    ok, msg, data = get_service().upload_submission_files(project_id, files, material_category, material_sub_category)
    if not ok:
        return ResponseMessage(400, msg, data).to_json(), 400
    return ResponseMessage(200, msg, data).to_json()


@filing_change_review_bp.post("/projects/<project_id>/submissions/list")
def list_submissions(project_id: str):
    payload = request.get_json(silent=True) or {}
    data = get_service().list_submissions(project_id, payload)
    return ResponseMessage(200, "success", data).to_json()


@filing_change_review_bp.post("/projects/<project_id>/submissions/<doc_id>/delete")
def delete_submission(project_id: str, doc_id: str):
    ok, msg = get_service().delete_submission(project_id, doc_id)
    if not ok:
        return ResponseMessage(400, msg, None).to_json(), 400
    return ResponseMessage(200, msg, None).to_json()


@filing_change_review_bp.post("/projects/<project_id>/submissions/<doc_id>/parse")
def parse_submission(project_id: str, doc_id: str):
    ok, msg, data = get_service().parse_submission(project_id, doc_id)
    if not ok:
        return ResponseMessage(400, msg, data).to_json(), 400
    return ResponseMessage(200, msg, data).to_json()


@filing_change_review_bp.post("/projects/<project_id>/submissions/<doc_id>/parse/start")
@filing_change_review_bp.post("/projects/<project_id>/submissions/<doc_id>/parse-async")
def parse_submission_async(project_id: str, doc_id: str):
    return _start_parse_task(
        task_type="parse_submission",
        project_id=project_id,
        source_doc_id=doc_id,
        operation=lambda: get_service().parse_submission(project_id, doc_id),
        created_message="申报资料解析任务已创建，等待执行",
        start_message="开始解析申报资料",
        success_message="申报资料解析完成",
    )


@filing_change_review_bp.post("/projects/<project_id>/submissions/parse-batch")
def batch_parse_submissions(project_id: str):
    payload = request.get_json(silent=True) or {}
    ok, msg, data = get_service().batch_parse_submissions(project_id, payload)
    if not ok:
        return ResponseMessage(400, msg, data).to_json(), 400
    return ResponseMessage(200, msg, data).to_json()


@filing_change_review_bp.post("/projects/<project_id>/submissions/parse-batch/start")
@filing_change_review_bp.post("/projects/<project_id>/submissions/parse-batch-async")
def batch_parse_submissions_async(project_id: str):
    payload = request.get_json(silent=True) or {}
    raw_doc_ids = payload.get("doc_ids", [])
    if not isinstance(raw_doc_ids, (list, tuple)):
        raw_doc_ids = []
    task_payload = {
        "doc_ids": [str(item) for item in raw_doc_ids if str(item).strip()],
        "all": bool(payload.get("all", False)),
    }
    return _start_parse_task(
        task_type="parse_submissions_batch",
        project_id=project_id,
        payload=task_payload,
        operation=lambda: get_service().batch_parse_submissions(project_id, task_payload),
        created_message="批量申报资料解析任务已创建，等待执行",
        start_message="开始批量解析申报资料",
        success_message="批量申报资料解析完成",
    )


@filing_change_review_bp.get("/projects/<project_id>/submissions/<doc_id>/parsed-markdown")
def get_submission_parsed_markdown(project_id: str, doc_id: str):
    ok, msg, data = get_service().get_submission_parsed_markdown(project_id, doc_id)
    if not ok:
        return ResponseMessage(404, msg, data).to_json(), 404
    return ResponseMessage(200, msg, data).to_json()


@filing_change_review_bp.get("/projects/<project_id>/submissions/<doc_id>/original")
def get_submission_original_file(project_id: str, doc_id: str):
    ok, msg, data = get_service().get_submission_original_file(project_id, doc_id)
    if not ok:
        return ResponseMessage(404, msg, None).to_json(), 404
    stream = data['stream']
    try:
        response = send_file(stream, mimetype='application/octet-stream', as_attachment=True,
                             download_name=data['file_name'], conditional=False, max_age=0)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.call_on_close(stream.close)
        return response
    except Exception:
        stream.close()
        raise


@filing_change_review_bp.post("/projects/<project_id>/submissions/<doc_id>/numeric-confirmations")
def confirm_submission_numeric_cells(project_id: str, doc_id: str):
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return ResponseMessage(400, '请求必须为JSON对象', None).to_json(), 400
    ok, msg, data = get_service().confirm_submission_numeric_cells(project_id, doc_id, payload)
    status = 200 if ok else 404 if msg == 'submission not found' else 409 if (data or {}).get('code', '').endswith('conflict') else 400
    return ResponseMessage(status, msg, data).to_json(), status


@filing_change_review_bp.post("/projects/<project_id>/submissions/<doc_id>/metadata")
def update_submission_metadata(project_id: str, doc_id: str):
    payload = request.get_json(silent=True) or {}
    ok, msg = get_service().update_submission_metadata(project_id, doc_id, payload)
    if not ok:
        return ResponseMessage(400, msg, None).to_json(), 400
    return ResponseMessage(200, msg, None).to_json()


@filing_change_review_bp.post("/projects/<project_id>/submissions/category/not-applicable")
def set_submission_category_not_applicable(project_id: str):
    payload = request.get_json(silent=True) or {}
    ok, msg = get_service().set_category_not_applicable(project_id, payload)
    if not ok:
        return ResponseMessage(400, msg, None).to_json(), 400
    return ResponseMessage(200, msg, None).to_json()


@filing_change_review_bp.get("/projects/<project_id>/submissions/catalog")
def get_submission_catalog(project_id: str):
    data = get_service().get_submission_catalog(project_id)
    return ResponseMessage(200, "success", data).to_json()


@filing_change_review_bp.post("/projects/<project_id>/submissions/completeness-check")
def submission_completeness_check(project_id: str):
    data = get_service().check_submission_completeness(project_id)
    return ResponseMessage(200, "success", data).to_json()


@filing_change_review_bp.post("/projects/<project_id>/submissions/compare")
def compare_submissions(project_id: str):
    payload = request.get_json(silent=True) or {}
    ok, msg, data = get_service().compare_submission_materials(project_id, payload)
    if not ok:
        return ResponseMessage(400, msg, data).to_json(), 400
    return ResponseMessage(200, msg, data).to_json()


@filing_change_review_bp.post("/reference-materials/upload")
def upload_reference_materials():
    files = [f for f in request.files.getlist("files") if getattr(f, "filename", "")]
    material_type = request.form.get("material_type", "other")
    task_type = request.form.get("task_type", "extend_validity_period")
    payload = {
        "drug_category": request.form.get("drug_category", "通用资料"),
        "applicable_change_item": request.form.get("applicable_change_item", "延长药品有效期"),
        "applicable_registration_classification": request.form.get("applicable_registration_classification", ""),
        "publisher": request.form.get("publisher", ""),
        "version": request.form.get("version", ""),
        "enabled": request.form.get("enabled", "true") not in {"false", "0", "False"},
        "remark": request.form.get("remark", ""),
    }
    ok, msg, data = get_service().upload_reference_materials(files, material_type, task_type, payload)
    if not ok:
        return ResponseMessage(400, msg, data).to_json(), 400
    return ResponseMessage(200, msg, data).to_json()


@filing_change_review_bp.post("/reference-materials/list")
def list_reference_materials():
    payload = request.get_json(silent=True) or {}
    data = get_service().list_reference_materials(payload)
    return ResponseMessage(200, "success", data).to_json()


@filing_change_review_bp.post("/reference-materials/<doc_id>/delete")
def delete_reference_material(doc_id: str):
    ok, msg = get_service().delete_reference_material(doc_id)
    if not ok:
        return ResponseMessage(400, msg, None).to_json(), 400
    return ResponseMessage(200, msg, None).to_json()


@filing_change_review_bp.post("/reference-materials/<doc_id>/parse")
def parse_reference_material(doc_id: str):
    ok, msg, data = get_service().parse_reference_material(doc_id)
    if not ok:
        return ResponseMessage(400, msg, data).to_json(), 400
    return ResponseMessage(200, msg, data).to_json()


@filing_change_review_bp.post("/reference-materials/<doc_id>/parse/start")
@filing_change_review_bp.post("/reference-materials/<doc_id>/parse-async")
def parse_reference_material_async(doc_id: str):
    return _start_parse_task(
        task_type="parse_reference_material",
        source_doc_id=doc_id,
        operation=lambda: get_service().parse_reference_material(doc_id),
        created_message="参考资料解析任务已创建，等待执行",
        start_message="开始解析参考资料",
        success_message="参考资料解析完成",
    )


@filing_change_review_bp.post("/reference-materials/<doc_id>/metadata")
def update_reference_material_metadata(doc_id: str):
    payload = request.get_json(silent=True) or {}
    ok, msg = get_service().update_reference_material_metadata(doc_id, payload)
    if not ok:
        return ResponseMessage(400, msg, None).to_json(), 400
    return ResponseMessage(200, msg, None).to_json()


@filing_change_review_bp.get("/reference-materials/<doc_id>/parsed-markdown")
def get_reference_parsed_markdown(doc_id: str):
    ok, msg, data = get_service().get_reference_material_parsed_markdown(doc_id)
    if not ok:
        return ResponseMessage(404, msg, data).to_json(), 404
    return ResponseMessage(200, msg, data).to_json()


@filing_change_review_bp.get("/reference-materials/taxonomy")
def get_reference_taxonomy():
    data = get_service().get_reference_taxonomy()
    return ResponseMessage(200, "success", data).to_json()


@filing_change_review_bp.post("/rules")
def create_rule():
    payload = request.get_json(silent=True) or {}
    ok, msg, data = get_service().create_rule(payload)
    if not ok:
        return ResponseMessage(400, msg, data).to_json(), 400
    return ResponseMessage(200, msg, data).to_json()


@filing_change_review_bp.post("/rules/list")
def list_rules():
    payload = request.get_json(silent=True) or {}
    data = get_service().list_rules(payload)
    return ResponseMessage(200, "success", data).to_json()


@filing_change_review_bp.post("/rules/<rule_id>")
def update_rule(rule_id: str):
    payload = request.get_json(silent=True) or {}
    ok, msg, data = get_service().update_rule(rule_id, payload)
    if not ok:
        return ResponseMessage(400, msg, data).to_json(), 400
    return ResponseMessage(200, msg, data).to_json()


@filing_change_review_bp.post("/rules/<rule_id>/delete")
def delete_rule(rule_id: str):
    ok, msg = get_service().delete_rule(rule_id)
    if not ok:
        return ResponseMessage(400, msg, None).to_json(), 400
    return ResponseMessage(200, msg, None).to_json()


@filing_change_review_bp.post("/rules/import")
def import_rules():
    upload_file = request.files.get("file")
    ok, msg, data = get_service().import_rules(upload_file)
    if not ok:
        return ResponseMessage(400, msg, data).to_json(), 400
    return ResponseMessage(200, msg, data).to_json()


@filing_change_review_bp.get("/projects/<project_id>/review/readiness")
def get_parse_readiness(project_id: str):
    result = get_service().get_parse_readiness(project_id)
    public = {key: value for key, value in result.items() if not key.startswith('_')}
    status = 200 if result.get('project_exists') else 404
    return ResponseMessage(status, 'success' if status == 200 else 'project not found', public).to_json(), status, {'Content-Type': 'application/json'}


@filing_change_review_bp.route("/projects/<project_id>/parse-review/<kind>/<doc_id>", methods=['GET', 'POST'])
def parse_review(project_id, kind, doc_id):
    if request.method == 'GET':
        ok, message, data = get_service().get_parse_review(project_id, kind, doc_id)
    else:
        ok, message, data = get_service().save_parse_review(project_id, kind, doc_id, request.get_json(silent=True))
    status = 200 if ok else (409 if (data or {}).get('code') == 'parse_revision_conflict' else 400)
    return ResponseMessage(status, message, data).to_json(), status, {'Content-Type': 'application/json'}


@filing_change_review_bp.post("/projects/<project_id>/parse-review/<kind>/<doc_id>/page/<int:page_no>")
def parse_review_page(project_id, kind, doc_id, page_no):
    ok, message, data = get_service().get_parse_review_page(
        project_id, kind, doc_id, page_no, request.get_json(silent=True))
    status = 200 if ok else (409 if (data or {}).get('code') == 'parse_revision_conflict' else 400)
    return ResponseMessage(status, message, data).to_json(), status, {
        'Content-Type': 'application/json', 'Cache-Control': 'no-store'}


@filing_change_review_bp.post("/projects/<project_id>/review/start")
def start_review(project_id: str):
    try:
        # 任务过期清理不应在持有项目锁时执行，避免多项目锁顺序交叉。
        _prune_tasks()
    except Exception as exc:
        message = f"备案变更异步任务清理失败: {exc}"
        logging.exception("prune filing review tasks failed project_id=%s", project_id)
        return ResponseMessage(500, message, None).to_json(), 500

    task_id = f"fcrt_{uuid.uuid4().hex[:16]}"
    app_service = get_service()
    with app_service.project_task_creation_lock(project_id):
        ok, msg, data = app_service.prepare_review_run(project_id)
        if not ok:
            status = 409 if isinstance(data, dict) and data.get('code') == 'parse_review_required' else 400
            return ResponseMessage(status, msg, data).to_json(), status, {'Content-Type': 'application/json'}
        run_id = str((data or {}).get("run_id", "")).strip()
        try:
            get_runtime_task_store().create_task(
                task_id=task_id,
                domain=_TASK_DOMAIN,
                project_id=project_id,
                run_id=run_id,
                task_type="review",
                status="pending",
                message="任务已创建",
            )
            _append_task_log(task_id, "prepare", "任务已创建，等待执行")
        except Exception as exc:
            message = f"备案变更审评任务持久化失败: {exc}"
            logging.exception("persist filing review task failed run_id=%s", run_id)
            try:
                store = get_runtime_task_store()
                if store.claim_task(task_id):
                    store.finish_task(
                        task_id,
                        status="failed",
                        message=message,
                        error_message=message,
                        final_log={"stage": "task_persist_failed", "message": message},
                        max_logs=300,
                        assign_sequence=False,
                    )
            except Exception:
                logging.exception("persist filing review task failure state failed run_id=%s", run_id)
            _mark_review_run_interrupted(project_id, run_id, message)
            return ResponseMessage(500, message, {"run_id": run_id}).to_json(), 500
        if not _run_task_async(task_id, project_id, run_id):
            return (
                ResponseMessage(
                    500,
                    "filing review worker start failed",
                    {"task_id": task_id, "run_id": run_id, "status": "failed"},
                ).to_json(),
                500,
            )
        return ResponseMessage(200, "success", {"task_id": task_id, "run_id": run_id}).to_json()


@filing_change_review_bp.post("/projects/<project_id>/ai-review/start")
def start_ai_review(project_id: str):
    return start_review(project_id)


def _runtime_task_progress_response(task_id: str):
    _prune_tasks()
    cursor = request.args.get("cursor", 0)
    item = get_runtime_task_store().get_snapshot(task_id, domain=_TASK_DOMAIN, cursor=cursor)
    if item is None:
        return jsonify({"code": 404, "message": "task not found", "data": None}), 404
    data = {
        "task_id": task_id,
        "project_id": item.get("project_id", ""),
        "run_id": item.get("run_id", ""),
        "source_doc_id": item.get("source_doc_id", ""),
        "task_type": item.get("task_type", "run"),
        "status": item.get("status", "pending"),
        "message": item.get("message", ""),
        "error_message": item.get("error_message", ""),
        "result": item.get("result"),
        "logs": item.get("logs", []),
        "cursor": item.get("cursor", 0),
        "next_cursor": item.get("next_cursor", 0),
        "done": item.get("done", False),
        "updated_at": item.get("updated_at", 0),
    }
    return jsonify({"code": 200, "message": "success", "data": data})


@filing_change_review_bp.get("/tasks/<task_id>/progress")
def runtime_task_progress(task_id: str):
    """备案业务统一任务进度接口，同时服务审评任务和各类解析任务。"""
    return _runtime_task_progress_response(task_id)


@filing_change_review_bp.get('/projects/<project_id>/parse-tasks')
def project_parse_tasks(project_id: str):
    _prune_tasks()
    service = getattr(get_service(), 'service', get_service())
    reader = getattr(service, '_project_parse_attempt_tasks', None)
    tasks = reader(project_id) if callable(reader) else get_runtime_task_store().list_project_parse_tasks(project_id, _TASK_DOMAIN)
    # staging 路径仅供内部清理，不能作为用户可见来源。
    return ResponseMessage(200, 'success', [{k: v for k, v in task.items() if k != 'payload'} for task in tasks]).to_json()


@filing_change_review_bp.get("/review/tasks/<task_id>/progress")
def review_task_progress(task_id: str):
    return _runtime_task_progress_response(task_id)


@filing_change_review_bp.get("/ai-review/tasks/<task_id>/progress")
def ai_review_task_progress(task_id: str):
    return review_task_progress(task_id)


@filing_change_review_bp.post("/projects/<project_id>/review/history")
def review_history(project_id: str):
    data = get_service().get_review_history(project_id)
    return ResponseMessage(200, "success", data).to_json()


@filing_change_review_bp.get("/runs/<run_id>/result")
def get_run_result(run_id: str):
    ok, msg, data = get_service().get_run_result(run_id)
    if not ok:
        return ResponseMessage(404, msg, data).to_json(), 404
    return ResponseMessage(200, msg, data).to_json()


@filing_change_review_bp.get("/runs/<run_id>/evidence")
def get_run_evidence(run_id: str):
    ok, msg, data = get_service().get_run_evidence(run_id)
    if not ok:
        return ResponseMessage(404, msg, data).to_json(), 404
    return ResponseMessage(200, msg, data).to_json()


@filing_change_review_bp.post("/runs/<run_id>/correction-notice/generate")
def generate_correction_notice(run_id: str):
    ok, msg, data = get_service().generate_correction_notice(run_id)
    if not ok:
        return ResponseMessage(404, msg, data).to_json(), 404
    return ResponseMessage(200, msg, data).to_json()


@filing_change_review_bp.post("/runs/<run_id>/report/generate")
def generate_report(run_id: str):
    ok, msg, data = get_service().generate_report(run_id)
    if not ok:
        return ResponseMessage(404, msg, data).to_json(), 404
    return ResponseMessage(200, msg, data).to_json()


@filing_change_review_bp.post("/runs/<run_id>/manual-confirm")
def manual_confirm_run(run_id: str):
    payload = request.get_json(silent=True) or {}
    ok, msg, data = get_service().manual_confirm_run(run_id, payload)
    if not ok:
        return ResponseMessage(404, msg, data).to_json(), 404
    return ResponseMessage(200, msg, data).to_json()


@filing_change_review_bp.get("/projects/<project_id>/report")
def get_project_report(project_id: str):
    ok, msg, data = get_service().get_project_latest_report(project_id, run_id=str(request.args.get("run_id", "")).strip())
    if not ok:
        return ResponseMessage(404, msg, data).to_json(), 404
    return ResponseMessage(200, msg, data).to_json()


@filing_change_review_bp.post("/reports/<report_id>/export-word")
def export_report_word(report_id: str):
    ok, msg, data = get_service().export_report_word(report_id)
    if not ok:
        return ResponseMessage(404, msg, data).to_json(), 404
    path = str((data or {}).get("file_path", "")).strip()
    return send_file(path, as_attachment=True)


@filing_change_review_bp.get("/reports/<report_id>/export-word")
def export_report_word_get(report_id: str):
    return export_report_word(report_id)
