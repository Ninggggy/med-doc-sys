import json
import logging
import queue
import threading
import time
import uuid
from io import BytesIO

from flask import Blueprint, Response, request, send_file, stream_with_context

from agent.agent_backend.application.feedback_app_service import FeedbackAppService
from agent.agent_backend.services.pre_review_service import PreReviewService
from agent.agent_backend.services.runtime_task_store import RuntimeTaskStore
from agent.agent_backend.utils.common_util import ResponseMessage


pre_review_bp = Blueprint("pre_review_controller", __name__, url_prefix="/pre-review")
_service: PreReviewService | None = None
_feedback_app_service: FeedbackAppService | None = None
_runtime_task_store: RuntimeTaskStore | None = None
_TASK_DOMAIN = "pre_review"
_TASK_RETENTION_SECONDS = 2 * 60 * 60
_MAIN_REVIEW_TASK_TYPES = ("run", "section_replay", "module_replay")


class _InMemoryUploadFile:
    def __init__(self, filename: str, content: bytes):
        self.filename = str(filename or "")
        self.stream = BytesIO(content or b"")

    def read(self, *args, **kwargs):
        return self.stream.read(*args, **kwargs)


def get_pre_review_service() -> PreReviewService:
    global _service
    if _service is None:
        _service = PreReviewService()
    return _service


def get_feedback_app_service() -> FeedbackAppService:
    global _feedback_app_service
    if _feedback_app_service is None:
        _feedback_app_service = FeedbackAppService()
    return _feedback_app_service


def get_runtime_task_store() -> RuntimeTaskStore:
    global _runtime_task_store
    if _runtime_task_store is None:
        _runtime_task_store = RuntimeTaskStore()
    return _runtime_task_store


def _prune_pre_review_tasks() -> None:
    store = get_runtime_task_store()
    store.recover_stale_active_tasks(domain=_TASK_DOMAIN)
    store.prune_finished(
        domain=_TASK_DOMAIN,
        retention_seconds=_TASK_RETENTION_SECONDS,
    )


def _create_pre_review_task(
    project_id: str,
    source_doc_id: str,
    run_config: dict,
    task_type: str = "run",
    run_id: str = "",
    section_id: str = "",
    task_id: str = "",
    prune_tasks: bool = True,
) -> str:
    task_id = str(task_id or "").strip() or f"prt_{uuid.uuid4().hex[:16]}"
    if prune_tasks:
        _prune_pre_review_tasks()
    get_runtime_task_store().create_task(
        task_id=task_id,
        domain=_TASK_DOMAIN,
        project_id=project_id,
        run_id=run_id,
        source_doc_id=source_doc_id,
        section_id=section_id,
        task_type=task_type,
        status="pending",
        payload=run_config,
    )
    return task_id


def _discard_pre_review_task_creation(
    task_id: str,
    *,
    task_type: str,
    failure_message: str,
) -> None:
    """回滚未对外发布的任务；若删除失败，至少将其收敛为失败终态。"""
    normalized_task_id = str(task_id or "").strip()
    if not normalized_task_id:
        return
    store = get_runtime_task_store()
    try:
        store.delete_task(
            normalized_task_id,
            include_upload_task=str(task_type or "") == "upload_submission",
        )
        return
    except Exception:
        logging.exception("rollback pre-review task creation failed task_id=%s", normalized_task_id)

    try:
        if store.claim_task(normalized_task_id):
            store.finish_task(
                normalized_task_id,
                status="failed",
                message=failure_message,
                result={"ok": False, "message": failure_message},
                error_message=failure_message,
                final_log={"stage": "task_persist_failed", "message": failure_message},
                max_logs=500,
                assign_sequence=True,
            )
    except Exception:
        logging.exception("persist pre-review task failure state failed task_id=%s", normalized_task_id)


def _create_initialized_pre_review_task(
    *,
    project_id: str,
    source_doc_id: str,
    run_config: dict,
    initial_log: dict,
    task_type: str = "run",
    run_id: str = "",
    section_id: str = "",
    prune_tasks: bool = True,
) -> str:
    """创建任务并写入首条日志，任一步失败都执行补偿。"""
    task_id = f"prt_{uuid.uuid4().hex[:16]}"
    try:
        _create_pre_review_task(
            project_id=project_id,
            source_doc_id=source_doc_id,
            run_config=run_config,
            task_type=task_type,
            run_id=run_id,
            section_id=section_id,
            task_id=task_id,
            prune_tasks=prune_tasks,
        )
        if not _append_pre_review_task_log(task_id, initial_log):
            raise RuntimeError("预审任务首条日志未写入")
        return task_id
    except Exception as exc:
        failure_message = f"预审任务持久化失败: {exc}"
        logging.exception(
            "initialize pre-review task failed task_id=%s task_type=%s",
            task_id,
            task_type,
        )
        _discard_pre_review_task_creation(
            task_id,
            task_type=task_type,
            failure_message=failure_message,
        )
        return ""


def _append_pre_review_task_log(task_id: str, event_payload: dict) -> bool:
    now = time.time()
    item = dict(event_payload or {})
    item["time"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
    return get_runtime_task_store().append_log(
        task_id,
        item,
        max_logs=500,
        assign_sequence=True,
        now=now,
    )


def _update_pre_review_task(task_id: str, **fields) -> bool:
    store = get_runtime_task_store()
    status = str(fields.get("status", "") or "")
    if status in {"completed", "failed"}:
        result = fields.get("result")
        result_message = str((result or {}).get("message", "") or "") if isinstance(result, dict) else ""
        return store.finish_task(
            task_id,
            status=status,
            message=str(fields.get("message", "") or result_message),
            result=result,
            error_message=str(fields.get("error_message", "") or ""),
            final_log=fields.get("final_log") if isinstance(fields.get("final_log"), dict) else None,
            max_logs=500,
            assign_sequence=True,
        )
    return store.update_task(task_id, **fields)


def _get_pre_review_task_snapshot(task_id: str, cursor: int = 0) -> dict | None:
    _prune_pre_review_tasks()
    task = get_runtime_task_store().get_snapshot(task_id, domain=_TASK_DOMAIN, cursor=cursor)
    if task is None:
        return None
    return {
        "task_id": task_id,
        "project_id": task.get("project_id", ""),
        "run_id": task.get("run_id", ""),
        "source_doc_id": task.get("source_doc_id", ""),
        "section_id": task.get("section_id", ""),
        "task_type": task.get("task_type", "run"),
        "status": task.get("status", "pending"),
        "logs": task.get("logs", []),
        "cursor": task.get("cursor", 0),
        "next_cursor": task.get("next_cursor", 0),
        "done": bool(task.get("done", False)),
        "result": task.get("result"),
        "error_message": task.get("error_message", ""),
        "created_at": task.get("created_at", 0),
        "updated_at": task.get("updated_at", 0),
        "finished_at": task.get("finished_at", 0),
    }


def _execute_pre_review_task_worker(
    task_id: str,
    worker_callback,
    *,
    result_style: str = "service",
) -> None:
    store = get_runtime_task_store()
    def _failed_result(message: str) -> dict:
        return (
            {"success": False, "message": message, "data": None}
            if result_style == "application"
            else {"ok": False, "message": message, "data": None}
        )

    try:
        with store.heartbeat_context(task_id) as claimed:
            if not claimed:
                return
            try:
                worker_callback()
            except Exception as exc:
                logging.exception("pre-review background task failed task_id=%s", task_id)
                _update_pre_review_task(
                    task_id,
                    status="failed",
                    result=_failed_result(str(exc)),
                    error_message=str(exc),
                    final_log={"stage": "task_failed", "message": str(exc)},
                )
            finally:
                current = store.get_task(task_id, domain=_TASK_DOMAIN)
                if current is not None and str(current.get("status", "")) == "running":
                    message = "后台任务已结束，但未产生完成状态"
                    _update_pre_review_task(
                        task_id,
                        status="failed",
                        result=_failed_result(message),
                        error_message=message,
                        final_log={"stage": "task_failed", "message": message},
                    )
    except Exception as exc:
        # claim/heartbeat 本身异常时也要尽力将任务收敛为终态，
        # 避免进程仍存活时长时间遗留 pending/running 任务。
        logging.exception("pre-review task lease failed task_id=%s", task_id)
        message = f"后台任务执行状态异常: {exc}"
        try:
            current = store.get_task(task_id, domain=_TASK_DOMAIN)
            if current is None:
                return
            status = str(current.get("status", "") or "")
            if status == "pending" and not store.claim_task(task_id):
                return
            if status in {"pending", "running"}:
                _update_pre_review_task(
                    task_id,
                    status="failed",
                    result=_failed_result(message),
                    error_message=message,
                    final_log={"stage": "task_failed", "message": message},
                )
        except Exception:
            logging.exception("persist pre-review lease failure failed task_id=%s", task_id)


def _start_pre_review_worker_thread(
    task_id: str,
    worker: threading.Thread,
    *,
    result_style: str = "service",
) -> bool:
    try:
        worker.start()
        return True
    except Exception as exc:
        logging.exception("start pre-review background thread failed task_id=%s", task_id)
        store = get_runtime_task_store()
        try:
            if store.claim_task(task_id):
                message = f"后台任务启动失败: {exc}"
                failed_result = (
                    {"success": False, "message": message, "data": None}
                    if result_style == "application"
                    else {"ok": False, "message": message, "data": None}
                )
                store.finish_task(
                    task_id,
                    status="failed",
                    message=message,
                    result=failed_result,
                    error_message=message,
                    final_log={"stage": "task_start_failed", "message": message},
                    max_logs=500,
                    assign_sequence=True,
                )
        except Exception:
            # 任务表暂时不可写时仍需返回结构化启动失败；
            # 数据库恢复后由 stale recovery 将遗留任务收敛为终态。
            logging.exception("persist worker start failure failed task_id=%s", task_id)
        return False


def _run_long_operation_task_async(
    *,
    task_id: str,
    task_type: str,
    project_id: str,
    run_id: str,
    source_doc_id: str,
    section_id: str,
    operation,
    result_style: str,
) -> bool:
    """通过共享 RuntimeTask 接口在后台执行现有的同步流程。

    ``service`` 流程返回 ``(ok, message, data)``，持久化为
    ``{ok, message, data}``；``application`` 流程原本就返回
    ``{success, message, data}``，因此原样保存。这样只改变执行边界，
    不改变原有结果契约。
    """

    metadata = {
        "task_type": str(task_type or "long_operation"),
        "project_id": str(project_id or ""),
        "run_id": str(run_id or ""),
        "source_doc_id": str(source_doc_id or ""),
        "section_id": str(section_id or ""),
    }

    def _worker() -> None:
        try:
            if not _append_pre_review_task_log(
                task_id,
                {
                    "stage": "task_start",
                    "message": "后台任务已启动",
                    **metadata,
                },
            ):
                raise RuntimeError("后台任务已终止，无法写入启动日志")
            raw_result = operation()
            if result_style == "application":
                if not isinstance(raw_result, dict):
                    raise RuntimeError("application 流程返回了无效结果")
                persisted_result = dict(raw_result)
                succeeded = bool(persisted_result.get("success", False))
                message = str(persisted_result.get("message", "") or "")
            else:
                if not isinstance(raw_result, tuple) or len(raw_result) != 3:
                    raise RuntimeError("service 流程返回了无效结果")
                ok, message, data = raw_result
                succeeded = bool(ok)
                message = str(message or "")
                persisted_result = {
                    "ok": succeeded,
                    "message": message,
                    "data": data,
                }
            _update_pre_review_task(
                task_id,
                status="completed" if succeeded else "failed",
                result=persisted_result,
                error_message="" if succeeded else message,
                final_log={
                    "stage": "task_done" if succeeded else "task_failed",
                    "message": message,
                    **metadata,
                },
            )
        except Exception as exc:
            message = str(exc)
            failed_result = (
                {"success": False, "message": message, "data": None}
                if result_style == "application"
                else {"ok": False, "message": message, "data": None}
            )
            _update_pre_review_task(
                task_id,
                status="failed",
                result=failed_result,
                error_message=message,
                final_log={
                    "stage": "task_failed",
                    "message": message,
                    **metadata,
                },
            )

    worker = threading.Thread(
        target=lambda: _execute_pre_review_task_worker(
            task_id,
            _worker,
            result_style=result_style,
        ),
        name=f"pre-review-{str(task_type or 'long-operation')}-{task_id}",
        daemon=True,
    )
    return _start_pre_review_worker_thread(
        task_id,
        worker,
        result_style=result_style,
    )


def _submit_long_operation_task(
    *,
    task_type: str,
    project_id: str,
    run_id: str,
    source_doc_id: str,
    section_id: str,
    request_payload: dict,
    operation,
    result_style: str,
):
    request_data = request_payload if isinstance(request_payload, dict) else {}
    case_ids = request_data.get("case_ids", []) if isinstance(request_data.get("case_ids", []), list) else []
    request_digest = {
        "payload_keys": sorted(
            [
                str(key)
                for key in request_data.keys()
                if str(key) not in {"original_output", "revised_output", "version_config", "study_config"}
            ]
        )[:32],
        "case_count": len(case_ids),
        "label_count": len(request_data.get("labels", [])) if isinstance(request_data.get("labels", []), list) else 0,
        "issue_feedback_count": len(request_data.get("issue_feedback", [])) if isinstance(request_data.get("issue_feedback", []), list) else 0,
        "evidence_feedback_count": len(request_data.get("evidence_feedback", [])) if isinstance(request_data.get("evidence_feedback", []), list) else 0,
        "has_original_output": bool(request_data.get("original_output")),
        "has_revised_output": bool(request_data.get("revised_output")),
        "patch_id": str(request_data.get("patch_id", "") or "").strip()[:128],
    }
    try:
        # stale recovery 不持有项目锁，避免与其他项目的中断补偿交叉等待。
        _prune_pre_review_tasks()
    except Exception as exc:
        logging.exception("prune pre-review long-operation tasks failed run_id=%s", run_id)
        return ResponseMessage(500, f"后台任务状态恢复失败: {exc}", None).to_json(), 500

    service = get_pre_review_service()
    task_id = ""
    try:
        # 首次查询只用于定位锁；获锁后必须重新校验 run 与活动项目，
        # 并把 RuntimeTask 落库、首日志和 Thread.start 放在同一临界区。
        with service.project_task_creation_lock(project_id):
            metadata_ok, metadata_message, current_metadata = _get_run_task_metadata(run_id)
            if not metadata_ok:
                status = _task_metadata_failure_status(metadata_message)
                return ResponseMessage(status, metadata_message, None).to_json(), status
            current_project_id = str(current_metadata.get("project_id", "") or "").strip()
            if current_project_id != str(project_id or "").strip():
                return ResponseMessage(409, "运行记录的项目归属已变更，请刷新后重试", None).to_json(), 409
            project_id = current_project_id
            run_id = str(current_metadata.get("run_id", "") or run_id).strip()
            source_doc_id = str(current_metadata.get("source_doc_id", "") or "").strip()
            task_payload = {
                "task_type": str(task_type or "long_operation"),
                "project_id": project_id,
                "run_id": run_id,
                "source_doc_id": source_doc_id,
                "section_id": str(section_id or ""),
                # 进程重启后 RuntimeTask 无法恢复原 Python Worker，所以这里只保存
                # 有界的诊断摘要。可能包含整章输出或版本配置的原请求，仅在本进程
                # 生命周期内由 Worker 闭包持有，不写入任务表。
                "request_digest": request_digest,
                "result_style": str(result_style or "service"),
            }
            task_id = _create_initialized_pre_review_task(
                project_id=project_id,
                run_id=run_id,
                source_doc_id=source_doc_id,
                section_id=section_id,
                run_config=task_payload,
                task_type=task_type,
                prune_tasks=False,
                initial_log={
                    "stage": "task_created",
                    "message": "后台任务已创建",
                    "task_type": task_type,
                    "project_id": project_id,
                    "run_id": run_id,
                    "source_doc_id": source_doc_id,
                    "section_id": section_id,
                },
            )
            if not task_id:
                return ResponseMessage(500, "后台任务持久化失败", None).to_json(), 500
            worker_started = _run_long_operation_task_async(
                task_id=task_id,
                task_type=task_type,
                project_id=project_id,
                run_id=run_id,
                source_doc_id=source_doc_id,
                section_id=section_id,
                operation=operation,
                result_style=result_style,
            )
            if not worker_started:
                return ResponseMessage(
                    500,
                    "后台执行线程启动失败",
                    {"task_id": task_id},
                ).to_json(), 500
    except Exception as exc:
        logging.exception("create pre-review long-operation task failed run_id=%s", run_id)
        if task_id:
            _discard_pre_review_task_creation(
                task_id,
                task_type=task_type,
                failure_message=f"后台任务创建失败: {exc}",
            )
        return ResponseMessage(500, f"后台任务创建失败: {exc}", None).to_json(), 500
    return ResponseMessage(
        200,
        "accepted",
        {
            "task_id": task_id,
            "task_type": task_type,
            "project_id": project_id,
            "run_id": run_id,
            "source_doc_id": source_doc_id,
            "section_id": section_id,
            "status": "pending",
            "progress_url": f"/api/pre-review/runs/tasks/{task_id}/progress",
        },
    ).to_json(), 202


def _submit_project_review_task(
    *,
    task_type: str,
    project_id: str,
    source_doc_id: str,
    section_id: str,
    run_config: dict,
    created_message: str,
    persistence_error_message: str,
    worker_error_message: str,
    worker_start,
):
    try:
        _prune_pre_review_tasks()
    except Exception as exc:
        logging.exception("prune project review tasks failed project_id=%s", project_id)
        return ResponseMessage(500, f"后台任务状态恢复失败: {exc}", None).to_json(), 500

    service = get_pre_review_service()
    task_id = ""
    try:
        with service.project_task_creation_lock(project_id):
            ok, msg, _ = service.validate_project_task_target(
                project_id=project_id,
                source_doc_id=source_doc_id,
            )
            if not ok:
                status = 404 if "not found" in str(msg or "").lower() else 400
                return ResponseMessage(status, msg, None).to_json(), status
            task_id = _create_initialized_pre_review_task(
                project_id=project_id,
                source_doc_id=source_doc_id,
                section_id=section_id,
                run_config=run_config,
                task_type=task_type,
                prune_tasks=False,
                initial_log={
                    "stage": "task_created",
                    "message": created_message,
                    "project_id": project_id,
                    "source_doc_id": source_doc_id,
                    "section_id": section_id,
                },
            )
            if not task_id:
                return ResponseMessage(500, persistence_error_message, None).to_json(), 500
            if not worker_start(task_id):
                return ResponseMessage(
                    500,
                    worker_error_message,
                    {"task_id": task_id},
                ).to_json(), 500
    except Exception as exc:
        logging.exception(
            "create project review task failed project_id=%s task_type=%s",
            project_id,
            task_type,
        )
        if task_id:
            _discard_pre_review_task_creation(
                task_id,
                task_type=task_type,
                failure_message=f"后台任务创建失败: {exc}",
            )
        return ResponseMessage(500, f"后台任务创建失败: {exc}", None).to_json(), 500
    return ResponseMessage(
        200,
        "success",
        {
            "task_id": task_id,
            "project_id": project_id,
            "source_doc_id": source_doc_id,
            "section_id": section_id,
            "task_type": task_type,
            "status": "pending",
        },
    ).to_json()


def _submit_submission_upload_task(
    *,
    project_id: str,
    files_payload: list,
    material_category: str,
    section_id: str,
    upload_mode: str,
    parse_mode: str,
):
    task_payload = {
        "material_category": material_category,
        "section_id": section_id,
        "mode": upload_mode,
        "parse_mode": parse_mode,
    }
    try:
        _prune_pre_review_tasks()
    except Exception as exc:
        logging.exception("prune upload tasks failed project_id=%s", project_id)
        return ResponseMessage(500, f"后台任务状态恢复失败: {exc}", None).to_json(), 500

    service = get_pre_review_service()
    task_id = ""
    try:
        with service.project_task_creation_lock(project_id):
            ok, msg, _ = service.validate_project_task_target(project_id=project_id)
            if not ok:
                status = 404 if "not found" in str(msg or "").lower() else 400
                return ResponseMessage(status, msg, None).to_json(), status
            task_id = _create_initialized_pre_review_task(
                project_id=project_id,
                source_doc_id="",
                run_config=task_payload,
                task_type="upload_submission",
                section_id=section_id,
                prune_tasks=False,
                initial_log={
                    "stage": "task_created",
                    "message": "上传解析任务已创建",
                    "project_id": project_id,
                    "file_count": len(files_payload),
                },
            )
            if not task_id:
                return ResponseMessage(500, "upload task persistence failed", None).to_json(), 500
            try:
                upload_task_record = service.create_upload_task_record(
                    task_id=task_id,
                    project_id=project_id,
                    section_id=section_id,
                    file_count=len(files_payload),
                    payload=task_payload,
                    status="pending",
                    message="上传解析任务已创建",
                )
            except Exception:
                logging.exception("persist upload task record failed task_id=%s", task_id)
                upload_task_record = None
            if upload_task_record is None:
                _discard_pre_review_task_creation(
                    task_id,
                    task_type="upload_submission",
                    failure_message="upload task persistence failed",
                )
                return ResponseMessage(500, "upload task persistence failed", None).to_json(), 500
            worker_started = _run_submission_upload_task_async(
                task_id=task_id,
                project_id=project_id,
                files_payload=files_payload,
                material_category=material_category,
                section_id=section_id,
                upload_mode=upload_mode,
                parse_mode=parse_mode,
            )
            if not worker_started:
                return ResponseMessage(
                    500,
                    "upload task worker start failed",
                    {"task_id": task_id, "project_id": project_id, "status": "failed"},
                ).to_json(), 500
    except Exception as exc:
        logging.exception("create upload task failed project_id=%s", project_id)
        if task_id:
            _discard_pre_review_task_creation(
                task_id,
                task_type="upload_submission",
                failure_message=f"上传解析任务创建失败: {exc}",
            )
        return ResponseMessage(500, f"上传解析任务创建失败: {exc}", None).to_json(), 500
    return ResponseMessage(
        200,
        "upload task created",
        {
            "task_id": task_id,
            "project_id": project_id,
            "status": "pending",
            "file_count": len(files_payload),
        },
    ).to_json()


def _get_run_task_metadata(run_id: str):
    try:
        ok, msg, data = get_pre_review_service().get_run_task_metadata(run_id=run_id)
    except Exception as exc:
        logging.exception("get run task metadata failed run_id=%s", run_id)
        return False, f"查询运行记录失败: {exc}", {}
    if not ok or not isinstance(data, dict):
        return False, msg, {}
    return True, msg, {
        "project_id": str(data.get("project_id", "") or "").strip(),
        "run_id": str(data.get("run_id", "") or run_id).strip(),
        "source_doc_id": str(data.get("source_doc_id", "") or "").strip(),
    }


def _task_metadata_failure_status(message: str) -> int:
    normalized = str(message or "").lower()
    if "not found" in normalized:
        return 404
    if "failed" in normalized or "失败" in normalized:
        return 500
    return 400


def _build_feedback_optimize_payload(run_id: str, payload: dict) -> dict:
    section_id = str(payload.get("section_id", "") or "").strip()
    if not section_id and isinstance(payload.get("original_output", {}), dict):
        section_id = str(payload.get("original_output", {}).get("section_id", "") or "").strip()
    return {
        "run_id": run_id,
        "section_id": section_id,
        "decision": str(payload.get("decision", "") or ""),
        "feedback_type": str(payload.get("feedback_type", "") or ""),
        "chain_mode": str(payload.get("chain_mode", "") or "feedback_optimize"),
        "manual_modified": bool(payload.get("manual_modified", False)),
        "labels": payload.get("labels", []) if isinstance(payload.get("labels", []), list) else [],
        "issue_feedback": payload.get("issue_feedback", []) if isinstance(payload.get("issue_feedback", []), list) else [],
        "paragraph_feedback": payload.get("paragraph_feedback", []) if isinstance(payload.get("paragraph_feedback", []), list) else [],
        "evidence_feedback": payload.get("evidence_feedback", []) if isinstance(payload.get("evidence_feedback", []), list) else [],
        "missing_item_feedback": payload.get("missing_item_feedback", {}) if isinstance(payload.get("missing_item_feedback", {}), dict) else {},
        "retrieval_feedback": str(payload.get("retrieval_feedback", "") or ""),
        "conclusion_feedback": str(payload.get("conclusion_feedback", "") or ""),
        "reference_example": payload.get("reference_example", {}) if isinstance(payload.get("reference_example", {}), dict) else {},
        "original_output": payload.get("original_output", {}),
        "revised_output": payload.get("revised_output", {}),
        "feedback_text": str(payload.get("feedback_text", "") or ""),
        "suggestion": str(payload.get("suggestion", "") or ""),
        "operator": str(payload.get("operator", "") or ""),
    }


def _get_evaluation_task_metadata(payload: dict, case_ids: list):
    explicit_run_id = str(payload.get("run_id", "") or "").strip()
    inferred_run_ids = {
        str(case_id).split(":", 1)[0].strip()
        for case_id in case_ids
        if ":" in str(case_id) and str(case_id).split(":", 1)[0].strip()
    }
    if explicit_run_id and inferred_run_ids and inferred_run_ids != {explicit_run_id}:
        return False, "run_id 与 case_ids 中的运行记录不一致", {}
    if not explicit_run_id and len(inferred_run_ids) > 1:
        return False, "异步评测一次只能处理同一 run_id 的用例，请按运行记录分批提交", {}
    run_id = explicit_run_id or (next(iter(inferred_run_ids)) if inferred_run_ids else "")
    if not run_id:
        return False, "无法从 case_ids 确定 run_id，请在请求中显式提供 run_id", {}
    metadata = {
        "project_id": "",
        "run_id": run_id,
        "source_doc_id": "",
        "section_id": str(payload.get("section_id", "") or "").strip(),
    }
    ok, msg, run_metadata = _get_run_task_metadata(run_id)
    if not ok:
        return False, msg, {}
    metadata.update(run_metadata)
    if not metadata["project_id"]:
        return False, "运行记录未关联有效项目", {}
    if not metadata["section_id"] and len(case_ids) == 1 and ":" in str(case_ids[0]):
        metadata["section_id"] = str(case_ids[0]).split(":", 1)[1].strip()
    return True, "success", metadata


def _run_pre_review_task_async(task_id: str, project_id: str, source_doc_id: str, run_config: dict) -> bool:
    def _emit_progress(event_payload: dict) -> None:
        _append_pre_review_task_log(task_id, event_payload if isinstance(event_payload, dict) else {"message": str(event_payload or "")})

    def _worker() -> None:
        _append_pre_review_task_log(
            task_id,
            {
                "stage": "task_start",
                "message": "预审任务已启动",
                "project_id": project_id,
                "source_doc_id": source_doc_id,
            },
        )
        try:
            ok, msg, data = get_pre_review_service().run_pre_review(
                project_id,
                source_doc_id,
                run_config=run_config,
                progress_callback=_emit_progress,
            )
            status = "completed" if ok else "failed"
            _update_pre_review_task(
                task_id,
                status=status,
                result={
                    "ok": ok,
                    "message": msg,
                    "data": data,
                    "project_id": project_id,
                    "source_doc_id": source_doc_id,
                },
                error_message="" if ok else str(msg or ""),
                final_log={
                    "stage": "run_done" if ok else "run_failed",
                    "message": msg,
                    "project_id": project_id,
                    "source_doc_id": source_doc_id,
                },
            )
        except Exception as exc:
            _update_pre_review_task(
                task_id,
                status="failed",
                result={
                    "ok": False,
                    "message": str(exc),
                    "project_id": project_id,
                    "source_doc_id": source_doc_id,
                },
                error_message=str(exc),
                final_log={
                    "stage": "run_failed",
                    "message": str(exc),
                    "project_id": project_id,
                    "source_doc_id": source_doc_id,
                },
            )

    worker = threading.Thread(
        target=lambda: _execute_pre_review_task_worker(task_id, _worker),
        name=f"pre-review-poll-{task_id}",
        daemon=True,
    )
    return _start_pre_review_worker_thread(task_id, worker)


def _run_section_replay_task_async(
    task_id: str,
    project_id: str,
    source_doc_id: str,
    section_id: str,
    run_config: dict,
) -> bool:
    def _emit_progress(event_payload: dict) -> None:
        payload = event_payload if isinstance(event_payload, dict) else {"message": str(event_payload or "")}
        payload.setdefault("section_id", section_id)
        _append_pre_review_task_log(task_id, payload)

    def _worker() -> None:
        _append_pre_review_task_log(
            task_id,
            {
                "stage": "task_start",
                "message": "开始章节审评任务",
                "project_id": project_id,
                "source_doc_id": source_doc_id,
                "section_id": section_id,
            },
        )
        try:
            ok, msg, data = get_pre_review_service().run_section_replay(
                project_id=project_id,
                source_doc_id=source_doc_id,
                section_id=section_id,
                run_config=run_config,
                progress_callback=_emit_progress,
            )
            status = "completed" if ok else "failed"
            _update_pre_review_task(
                task_id,
                status=status,
                result={
                    "ok": ok,
                    "message": msg,
                    "data": data,
                    "project_id": project_id,
                    "source_doc_id": source_doc_id,
                    "section_id": section_id,
                },
                error_message="" if ok else str(msg or ""),
                final_log={
                    "stage": "run_done" if ok else "run_failed",
                    "message": msg,
                    "project_id": project_id,
                    "source_doc_id": source_doc_id,
                    "section_id": section_id,
                },
            )
        except Exception as exc:
            _update_pre_review_task(
                task_id,
                status="failed",
                result={
                    "ok": False,
                    "message": str(exc),
                    "project_id": project_id,
                    "source_doc_id": source_doc_id,
                    "section_id": section_id,
                },
                error_message=str(exc),
                final_log={
                    "stage": "run_failed",
                    "message": str(exc),
                    "project_id": project_id,
                    "source_doc_id": source_doc_id,
                    "section_id": section_id,
                },
            )

    worker = threading.Thread(
        target=lambda: _execute_pre_review_task_worker(task_id, _worker),
        name=f"pre-review-section-{task_id}",
        daemon=True,
    )
    return _start_pre_review_worker_thread(task_id, worker)


def _run_module_replay_task_async(
    task_id: str,
    project_id: str,
    source_doc_id: str,
    section_id: str,
    run_config: dict,
) -> bool:
    def _emit_progress(event_payload: dict) -> None:
        payload = event_payload if isinstance(event_payload, dict) else {"message": str(event_payload or "")}
        payload.setdefault("section_id", section_id)
        _append_pre_review_task_log(task_id, payload)

    def _worker() -> None:
        _append_pre_review_task_log(
            task_id,
            {
                "stage": "task_start",
                "message": "开始模块审评任务",
                "project_id": project_id,
                "source_doc_id": source_doc_id,
                "section_id": section_id,
            },
        )
        try:
            ok, msg, data = get_pre_review_service().run_module_replay(
                project_id=project_id,
                source_doc_id=source_doc_id,
                module_section_id=section_id,
                run_config=run_config,
                progress_callback=_emit_progress,
            )
            status = "completed" if ok else "failed"
            _update_pre_review_task(
                task_id,
                status=status,
                result={
                    "ok": ok,
                    "message": msg,
                    "data": data,
                    "project_id": project_id,
                    "source_doc_id": source_doc_id,
                    "section_id": section_id,
                },
                error_message="" if ok else str(msg or ""),
                final_log={
                    "stage": "run_done" if ok else "run_failed",
                    "message": msg,
                    "project_id": project_id,
                    "source_doc_id": source_doc_id,
                    "section_id": section_id,
                },
            )
        except Exception as exc:
            _update_pre_review_task(
                task_id,
                status="failed",
                result={
                    "ok": False,
                    "message": str(exc),
                    "project_id": project_id,
                    "source_doc_id": source_doc_id,
                    "section_id": section_id,
                },
                error_message=str(exc),
                final_log={
                    "stage": "run_failed",
                    "message": str(exc),
                    "project_id": project_id,
                    "source_doc_id": source_doc_id,
                    "section_id": section_id,
                },
            )

    worker = threading.Thread(
        target=lambda: _execute_pre_review_task_worker(task_id, _worker),
        name=f"pre-review-module-{task_id}",
        daemon=True,
    )
    return _start_pre_review_worker_thread(task_id, worker)


def _run_submission_upload_task_async(
    task_id: str,
    project_id: str,
    files_payload: list[dict],
    material_category: str,
    section_id: str,
    upload_mode: str,
    parse_mode: str,
) -> bool:
    def _worker() -> None:
        _append_pre_review_task_log(
            task_id,
            {
                "stage": "task_start",
                "message": "上传解析任务已启动",
                "project_id": project_id,
                "file_count": len(files_payload or []),
            },
        )
        success_items = []
        errors = []
        try:
            for index, item in enumerate(files_payload or [], start=1):
                file_name = str((item or {}).get("file_name", "") or "").strip()
                file_bytes = (item or {}).get("file_bytes", b"") or b""
                _append_pre_review_task_log(
                    task_id,
                    {
                        "stage": "upload_item_start",
                        "message": f"开始解析文件 {file_name or index}",
                        "project_id": project_id,
                        "file_name": file_name,
                        "file_index": index,
                        "file_count": len(files_payload or []),
                    },
                )
                upload_file = _InMemoryUploadFile(file_name, file_bytes)
                ok, msg, data = get_pre_review_service().upload_submission(
                    project_id=project_id,
                    file_obj=upload_file,
                    material_category=material_category,
                    section_id=section_id,
                    upload_mode=upload_mode,
                    parse_mode=parse_mode,
                )
                if not ok:
                    errors.append({"file_name": file_name, "message": msg})
                    _append_pre_review_task_log(
                        task_id,
                        {
                            "stage": "upload_item_failed",
                            "message": msg,
                            "project_id": project_id,
                            "file_name": file_name,
                            "file_index": index,
                            "file_count": len(files_payload or []),
                        },
                    )
                    continue
                if isinstance(data, dict) and isinstance(data.get("items"), list):
                    success_items.extend(data.get("items", []))
                elif data is not None:
                    success_items.append(data)
                _append_pre_review_task_log(
                    task_id,
                    {
                        "stage": "upload_item_done",
                        "message": msg or "文件解析完成",
                        "project_id": project_id,
                        "file_name": file_name,
                        "file_index": index,
                        "file_count": len(files_payload or []),
                    },
                )

            ok = bool(success_items)
            message = "上传解析完成" if ok and not errors else "上传解析部分完成" if ok else "上传解析失败"
            result_payload = {
                "ok": ok,
                "message": message,
                "data": {
                    "items": success_items,
                    "count": len(success_items),
                    "errors": errors,
                    "project_id": project_id,
                },
                "project_id": project_id,
            }
            _update_pre_review_task(
                task_id,
                status="completed" if ok else "failed",
                result=result_payload,
                error_message="" if ok else "; ".join([str(item.get("message", "") or "") for item in errors]) or message,
                final_log={
                    "stage": "upload_done" if ok else "upload_failed",
                    "message": message,
                    "project_id": project_id,
                    "success_count": len(success_items),
                    "error_count": len(errors),
                },
            )
        except Exception as exc:
            result_payload = {
                "ok": False,
                "message": str(exc),
                "project_id": project_id,
            }
            _update_pre_review_task(
                task_id,
                status="failed",
                result=result_payload,
                error_message=str(exc),
                final_log={
                    "stage": "upload_failed",
                    "message": str(exc),
                    "project_id": project_id,
                },
            )

    worker = threading.Thread(
        target=lambda: _execute_pre_review_task_worker(task_id, _worker),
        name=f"pre-review-upload-{task_id}",
        daemon=True,
    )
    return _start_pre_review_worker_thread(task_id, worker)


@pre_review_bp.post("/projects")
def create_project():
    payload = request.get_json(silent=True) or {}
    ok, msg, data = get_pre_review_service().create_project(
        project_name=payload.get("project_name", ""),
        description=payload.get("description", ""),
        owner=payload.get("owner", ""),
        registration_scope=payload.get("registration_scope", ""),
        registration_path=payload.get("registration_path", []) if isinstance(payload.get("registration_path", []), list) else [],
        registration_leaf=payload.get("registration_leaf", ""),
        registration_description=payload.get("registration_description", ""),
    )
    if not ok:
        return ResponseMessage(400, msg, None).to_json(), 400
    return ResponseMessage(200, msg, data).to_json()


@pre_review_bp.post("/projects/<project_id>/delete")
def delete_project(project_id: str):
    try:
        _prune_pre_review_tasks()
    except Exception as exc:
        logging.exception("recover runtime tasks before project deletion failed project_id=%s", project_id)
        return ResponseMessage(
            500,
            f"后台任务状态恢复失败，暂不能删除项目: {exc}",
            None,
        ).to_json(), 500
    ok, msg = get_pre_review_service().delete_project(project_id)
    if not ok:
        return ResponseMessage(400, msg, None).to_json(), 400
    return ResponseMessage(200, msg, {"project_id": project_id}).to_json()


@pre_review_bp.post("/projects/batch-delete")
def batch_delete_projects():
    payload = request.get_json(silent=True) or {}
    project_ids = payload.get("project_ids", []) if isinstance(payload.get("project_ids", []), list) else []
    try:
        _prune_pre_review_tasks()
    except Exception as exc:
        logging.exception("recover runtime tasks before batch project deletion failed")
        return ResponseMessage(
            500,
            f"后台任务状态恢复失败，暂不能批量删除项目: {exc}",
            None,
        ).to_json(), 500
    ok, msg, data = get_pre_review_service().batch_delete_projects(project_ids)
    status = 200 if ok else 207
    return ResponseMessage(status, msg, data).to_json(), status


@pre_review_bp.post("/projects/list")
def list_projects():
    payload = request.get_json(silent=True) or {}
    try:
        page = int(payload.get("page", 1))
        page_size = int(payload.get("page_size", 10))
    except Exception:
        return ResponseMessage(400, "page/page_size must be integer", None).to_json(), 400
    if page <= 0 or page_size <= 0:
        return ResponseMessage(400, "page/page_size must be > 0", None).to_json(), 400

    data = get_pre_review_service().list_projects(
        page=page,
        page_size=page_size,
        status=str(payload.get("status", "")),
        project_name=str(payload.get("project_name", "")),
        registration_scope=str(payload.get("registration_scope", "")),
        registration_leaf=str(payload.get("registration_leaf", "")),
    )
    return ResponseMessage(200, "success", data).to_json()


@pre_review_bp.post("/projects/<project_id>/detail")
def project_detail(project_id: str):
    ok, msg, data = get_pre_review_service().get_project_detail(project_id)
    if not ok:
        return ResponseMessage(404, msg, None).to_json(), 404
    return ResponseMessage(200, msg, data).to_json()


@pre_review_bp.post("/projects/<project_id>/submissions/upload")
def upload_submission(project_id: str):
    raw_files = request.files.getlist("files")
    if not raw_files and "file" in request.files:
        raw_files = [request.files["file"]]
    if not raw_files and "upload_file" in request.files:
        raw_files = [request.files["upload_file"]]
    if not raw_files and request.files:
        raw_files = list(request.files.values())
    files = [item for item in raw_files if getattr(item, "filename", "")]
    if not files:
        return (
            ResponseMessage(
                400,
                "file is required",
                {
                    "file_keys": list(request.files.keys()),
                    "form_keys": list(request.form.keys()),
                },
            ).to_json(),
            400,
        )
    material_category = str(request.form.get("material_category", "other") or "other")
    section_id = str(request.form.get("section_id", "") or "")
    upload_mode = str(request.form.get("mode", "") or "")
    parse_mode = str(request.form.get("parse_mode", "") or "")
    out_items = []
    errors = []
    for file_obj in files:
        ok, msg, data = get_pre_review_service().upload_submission(
            project_id,
            file_obj,
            material_category=material_category,
            section_id=section_id,
            upload_mode=upload_mode,
            parse_mode=parse_mode,
        )
        if not ok:
            errors.append(
                {
                    "file_name": str(getattr(file_obj, "filename", "") or ""),
                    "message": msg,
                }
            )
            continue
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            out_items.extend(data.get("items", []))
        elif data is not None:
            out_items.append(data)
    if not out_items:
        status = 404 if any("not found" in str(item.get("message", "")).lower() for item in errors) else 400
        message = "; ".join([str(item.get("message", "") or "") for item in errors]) or "upload failed"
        return ResponseMessage(status, message, {"errors": errors}).to_json(), status
    response_data = {"items": out_items, "count": len(out_items), "errors": errors}
    message = "submission uploaded" if not errors else "submission uploaded with partial failures"
    return ResponseMessage(200, message, response_data).to_json()


@pre_review_bp.post("/projects/<project_id>/submissions/async-upload")
def upload_submission_async(project_id: str):
    raw_files = request.files.getlist("files")
    if not raw_files and "file" in request.files:
        raw_files = [request.files["file"]]
    if not raw_files and "upload_file" in request.files:
        raw_files = [request.files["upload_file"]]
    if not raw_files and request.files:
        raw_files = list(request.files.values())
    files = [item for item in raw_files if getattr(item, "filename", "")]
    if not files:
        return (
            ResponseMessage(
                400,
                "file is required",
                {
                    "file_keys": list(request.files.keys()),
                    "form_keys": list(request.form.keys()),
                },
            ).to_json(),
            400,
        )
    material_category = str(request.form.get("material_category", "other") or "other")
    section_id = str(request.form.get("section_id", "") or "")
    upload_mode = str(request.form.get("mode", "") or "")
    parse_mode = str(request.form.get("parse_mode", "") or "")
    files_payload = [
        {
            "file_name": str(getattr(file_obj, "filename", "") or ""),
            "file_bytes": file_obj.read(),
        }
        for file_obj in files
    ]
    return _submit_submission_upload_task(
        project_id=project_id,
        files_payload=files_payload,
        material_category=material_category,
        section_id=section_id,
        upload_mode=upload_mode,
        parse_mode=parse_mode,
    )


@pre_review_bp.post("/projects/<project_id>/ctd-catalog")
def project_ctd_catalog(project_id: str):
    data = get_pre_review_service().get_ctd_section_catalog(project_id=project_id)
    return ResponseMessage(200, "success", data).to_json()


@pre_review_bp.post("/sections/catalog")
def global_section_catalog():
    data = get_pre_review_service().get_ctd_section_catalog(project_id="")
    return ResponseMessage(200, "success", data).to_json()


@pre_review_bp.post("/sections/catalog/refresh")
def refresh_global_section_catalog():
    data = get_pre_review_service().refresh_global_ctd_section_catalog()
    return ResponseMessage(200, "success", data).to_json()


@pre_review_bp.post("/sections/<section_id>/rules")
def global_section_rules(section_id: str):
    payload = request.get_json(silent=True) or {}
    scope_metadata = payload.get("scope_metadata", {}) if isinstance(payload.get("scope_metadata", {}), dict) else {}
    ok, msg, data = get_pre_review_service().get_section_rules(section_id=section_id, scope_metadata=scope_metadata)
    if not ok:
        return ResponseMessage(400, msg, None).to_json(), 400
    return ResponseMessage(200, msg, data).to_json()


@pre_review_bp.post("/sections/<section_id>/rules/save")
def save_global_section_rules(section_id: str):
    payload = request.get_json(silent=True) or {}
    rule_texts = payload.get("rule_texts", []) if isinstance(payload.get("rule_texts", []), list) else []
    scope_metadata = payload.get("scope_metadata", {}) if isinstance(payload.get("scope_metadata", {}), dict) else {}
    ok, msg, data = get_pre_review_service().save_section_rules(
        section_id=section_id,
        rule_texts=rule_texts,
        scope_metadata=scope_metadata,
    )
    if not ok:
        return ResponseMessage(400, msg, None).to_json(), 400
    return ResponseMessage(200, msg, data).to_json()


@pre_review_bp.post("/sections/<section_id>/seed-rules/save")
def save_global_seed_section_rules(section_id: str):
    payload = request.get_json(silent=True) or {}
    rule_texts = payload.get("rule_texts", []) if isinstance(payload.get("rule_texts", []), list) else []
    ok, msg, data = get_pre_review_service().save_seed_section_rules(
        section_id=section_id,
        rule_texts=rule_texts,
    )
    if not ok:
        status = 404 if "not found" in msg.lower() else 400
        return ResponseMessage(status, msg, None).to_json(), status
    return ResponseMessage(200, msg, data).to_json()


@pre_review_bp.post("/sections/<section_id>/seed-rules/delete")
def delete_global_seed_section_rules(section_id: str):
    ok, msg, data = get_pre_review_service().delete_seed_section_rules(section_id=section_id)
    if not ok:
        status = 404 if "not found" in msg.lower() else 400
        return ResponseMessage(status, msg, None).to_json(), status
    return ResponseMessage(200, msg, data).to_json()


@pre_review_bp.post("/sections/<section_id>/seed-rules/<rule_code>/delete")
def delete_global_seed_section_rule(section_id: str, rule_code: str):
    ok, msg, data = get_pre_review_service().delete_section_rule(section_id=section_id, rule_code=rule_code)
    if not ok:
        status = 404 if "not found" in msg.lower() else 400
        return ResponseMessage(status, msg, None).to_json(), status
    return ResponseMessage(200, msg, data).to_json()


@pre_review_bp.post("/sections/<section_id>/rules/<rule_code>/delete")
def delete_global_section_rule(section_id: str, rule_code: str):
    ok, msg, data = get_pre_review_service().delete_section_rule(section_id=section_id, rule_code=rule_code)
    if not ok:
        status = 404 if "not found" in msg.lower() else 400
        return ResponseMessage(status, msg, None).to_json(), status
    return ResponseMessage(200, msg, data).to_json()


@pre_review_bp.post("/sections/<section_id>/rules/batch-delete")
def batch_delete_global_section_rules(section_id: str):
    payload = request.get_json(silent=True) or {}
    rule_codes = payload.get("rule_codes", []) if isinstance(payload.get("rule_codes", []), list) else []
    ok, msg, data = get_pre_review_service().batch_delete_section_rules(section_id=section_id, rule_codes=rule_codes)
    if not ok:
        status = 404 if "not found" in msg.lower() else 400
        return ResponseMessage(status, msg, None).to_json(), status
    return ResponseMessage(200, msg, data).to_json()


@pre_review_bp.post("/sections/rules/delete-by-scope")
def delete_global_section_rules_by_scope():
    payload = request.get_json(silent=True) or {}
    scope_metadata = payload.get("scope_metadata", {}) if isinstance(payload.get("scope_metadata", {}), dict) else {}
    ok, msg, data = get_pre_review_service().delete_section_rules_by_scope(scope_metadata=scope_metadata)
    if not ok:
        status = 404 if "not found" in msg.lower() else 400
        return ResponseMessage(status, msg, None).to_json(), status
    return ResponseMessage(200, msg, data).to_json()


@pre_review_bp.post("/sections/rules/delete-by-scope/preview")
def preview_delete_global_section_rules_by_scope():
    payload = request.get_json(silent=True) or {}
    scope_metadata = payload.get("scope_metadata", {}) if isinstance(payload.get("scope_metadata", {}), dict) else {}
    ok, msg, data = get_pre_review_service().preview_delete_section_rules_by_scope(scope_metadata=scope_metadata)
    if not ok:
        status = 404 if "not found" in msg.lower() else 400
        return ResponseMessage(status, msg, None).to_json(), status
    return ResponseMessage(200, msg, data).to_json()


@pre_review_bp.post("/sections/rules/import-json")
def import_global_section_rules():
    payload = request.get_json(silent=True) or {}
    file_name = ""
    file_bytes = b""
    scope_metadata = {}
    upload_file = request.files.get("file")
    if upload_file is not None and getattr(upload_file, "filename", ""):
        file_name = str(upload_file.filename or "").strip()
        file_bytes = upload_file.read()
        scope_text = str(request.form.get("scope_metadata", "") or "").strip()
        if scope_text:
            try:
                decoded_scope = json.loads(scope_text)
                if isinstance(decoded_scope, dict):
                    scope_metadata = decoded_scope
            except Exception:
                scope_metadata = {}
    elif isinstance(payload.get("content"), str):
        file_name = str(payload.get("file_name", "") or "").strip()
        file_bytes = str(payload.get("content", "") or "").encode("utf-8")
        scope_metadata = payload.get("scope_metadata", {}) if isinstance(payload.get("scope_metadata", {}), dict) else {}
    else:
        return ResponseMessage(400, "json file is required", None).to_json(), 400
    ok, msg, data = get_pre_review_service().import_section_rules_json(
        file_bytes=file_bytes,
        file_name=file_name,
        scope_metadata=scope_metadata,
    )
    if not ok:
        return ResponseMessage(400, msg, None).to_json(), 400
    return ResponseMessage(200, msg, data).to_json()


@pre_review_bp.post("/projects/<project_id>/sections/<section_id>/rules")
def project_section_rules(project_id: str, section_id: str):
    ok, msg, data = get_pre_review_service().get_project_section_rules(project_id=project_id, section_id=section_id)
    if not ok:
        return ResponseMessage(400, msg, None).to_json(), 400
    return ResponseMessage(200, msg, data).to_json()


@pre_review_bp.post("/projects/<project_id>/sections/<section_id>/rules/save")
def save_project_section_rules(project_id: str, section_id: str):
    payload = request.get_json(silent=True) or {}
    rule_texts = payload.get("rule_texts", []) if isinstance(payload.get("rule_texts", []), list) else []
    ok, msg, data = get_pre_review_service().save_project_section_rules(
        project_id=project_id,
        section_id=section_id,
        rule_texts=rule_texts,
    )
    if not ok:
        status = 404 if "not found" in msg.lower() else 400
        return ResponseMessage(status, msg, None).to_json(), status
    return ResponseMessage(200, msg, data).to_json()


@pre_review_bp.post("/projects/<project_id>/sections/<section_id>/examples")
def project_section_examples(project_id: str, section_id: str):
    ok, msg, data = get_pre_review_service().get_project_section_examples(project_id=project_id, section_id=section_id)
    if not ok:
        return ResponseMessage(400, msg, None).to_json(), 400
    return ResponseMessage(200, msg, data).to_json()


@pre_review_bp.post("/projects/<project_id>/sections/<section_id>/prompt-rules")
def project_section_prompt_rules(project_id: str, section_id: str):
    ok, msg, data = get_pre_review_service().get_project_section_prompt_rules(project_id=project_id, section_id=section_id)
    if not ok:
        return ResponseMessage(400, msg, None).to_json(), 400
    return ResponseMessage(200, msg, data).to_json()


@pre_review_bp.post("/prompt-rules/seed/list")
def list_seed_prompt_rules():
    payload = request.get_json(silent=True) or {}
    ok, msg, data = get_pre_review_service().list_seed_prompt_rules(
        task_type=str(payload.get("task_type", "") or "").strip(),
    )
    if not ok:
        return ResponseMessage(400, msg, None).to_json(), 400
    return ResponseMessage(200, msg, data).to_json()


@pre_review_bp.post("/prompt-rules/seed/<task_type>/save")
def save_seed_prompt_rule(task_type: str):
    payload = request.get_json(silent=True) or {}
    ok, msg, data = get_pre_review_service().save_seed_prompt_rule(
        task_type=task_type,
        rule_payload=payload if isinstance(payload, dict) else {},
    )
    if not ok:
        return ResponseMessage(400, msg, None).to_json(), 400
    return ResponseMessage(200, msg, data).to_json()


@pre_review_bp.post("/prompt-rules/seed/<task_type>/<rule_code>/delete")
def delete_seed_prompt_rule(task_type: str, rule_code: str):
    ok, msg, data = get_pre_review_service().delete_seed_prompt_rule(
        task_type=task_type,
        rule_code=rule_code,
    )
    if not ok:
        status = 404 if "not found" in msg.lower() else 400
        return ResponseMessage(status, msg, None).to_json(), status
    return ResponseMessage(200, msg, data).to_json()


@pre_review_bp.post("/projects/<project_id>/sections/<section_id>/experience-memory")
def project_section_experience_memory(project_id: str, section_id: str):
    ok, msg, data = get_pre_review_service().get_project_section_experience_memory(project_id=project_id, section_id=section_id)
    if not ok:
        return ResponseMessage(400, msg, None).to_json(), 400
    return ResponseMessage(200, msg, data).to_json()


@pre_review_bp.post("/projects/<project_id>/submissions/list")
def list_submissions(project_id: str):
    payload = request.get_json(silent=True) or {}
    try:
        page = int(payload.get("page", 1))
        page_size = int(payload.get("page_size", 20))
    except Exception:
        return ResponseMessage(400, "page/page_size must be integer", None).to_json(), 400
    if page <= 0 or page_size <= 0:
        return ResponseMessage(400, "page/page_size must be > 0", None).to_json(), 400
    data = get_pre_review_service().list_submissions(project_id=project_id, page=page, page_size=page_size)
    return ResponseMessage(200, "success", data).to_json()


@pre_review_bp.post("/projects/<project_id>/submissions/<doc_id>/content")
def submission_content(project_id: str, doc_id: str):
    payload = request.get_json(silent=True) or {}
    compact_value = payload.get("compact", False)
    compact = compact_value is True or str(compact_value or "").strip().lower() in {"1", "true", "yes", "on"}
    ok, msg, data = get_pre_review_service().get_submission_content(
        project_id=project_id,
        doc_id=doc_id,
        compact=compact,
    )
    if not ok:
        normalized_message = str(msg or "").lower()
        status = (
            404
            if "not found" in normalized_message
            else 409
            if "not ready" in normalized_message or "reparse required" in normalized_message
            else 400
        )
        return ResponseMessage(status, msg, None).to_json(), status
    return ResponseMessage(200, msg, data).to_json()


@pre_review_bp.post("/projects/<project_id>/submissions/<doc_id>/content/save")
def save_submission_content(project_id: str, doc_id: str):
    payload = request.get_json(silent=True) or {}
    ok, msg, data = get_pre_review_service().save_submission_content(
        project_id=project_id,
        doc_id=doc_id,
        content=str(payload.get("content", "") or ""),
        section_id=str(payload.get("section_id", "") or ""),
    )
    if not ok:
        status = 404 if "not found" in msg.lower() else 400
        return ResponseMessage(status, msg, None).to_json(), status
    return ResponseMessage(200, msg, data).to_json()

@pre_review_bp.post("/projects/<project_id>/submissions/<doc_id>/sections")
def submission_sections(project_id: str, doc_id: str):
    payload = request.get_json(silent=True) or {}
    compact_value = payload.get("compact", False)
    compact = compact_value is True or str(compact_value or "").strip().lower() in {"1", "true", "yes", "on"}
    ok, msg, data = get_pre_review_service().get_submission_sections(
        project_id=project_id,
        doc_id=doc_id,
        compact=compact,
    )
    if not ok:
        normalized_message = str(msg or "").lower()
        status = (
            404
            if "not found" in normalized_message
            else 409
            if "not ready" in normalized_message or "reparse required" in normalized_message
            else 400
        )
        return ResponseMessage(status, msg, None).to_json(), status
    return ResponseMessage(200, msg, data).to_json()


@pre_review_bp.post("/projects/<project_id>/submissions/<doc_id>/section-diagnostics")
def submission_section_diagnostics(project_id: str, doc_id: str):
    ok, msg, data = get_pre_review_service().get_submission_section_diagnostics(project_id=project_id, doc_id=doc_id)
    if not ok:
        normalized_message = str(msg or "").lower()
        status = (
            404
            if "not found" in normalized_message
            else 409
            if "not ready" in normalized_message or "reparse required" in normalized_message
            else 400
        )
        return ResponseMessage(status, msg, None).to_json(), status
    return ResponseMessage(200, msg, data).to_json()


@pre_review_bp.post("/projects/<project_id>/submissions/<doc_id>/sections/<section_id>/replay")
def replay_submission_section(project_id: str, doc_id: str, section_id: str):
    payload = request.get_json(silent=True) or {}
    run_config = payload.get("run_config", {}) if isinstance(payload.get("run_config", {}), dict) else {}
    ok, msg, data = get_pre_review_service().run_section_replay(
        project_id=project_id,
        source_doc_id=doc_id,
        section_id=section_id,
        run_config=run_config,
    )
    if not ok:
        status = 404 if "not found" in msg.lower() else 400
        return ResponseMessage(status, msg, None).to_json(), status
    return ResponseMessage(200, msg, data).to_json()


@pre_review_bp.post("/projects/<project_id>/submissions/<doc_id>/sections/<section_id>/async-replay")
def replay_submission_section_async(project_id: str, doc_id: str, section_id: str):
    payload = request.get_json(silent=True) or {}
    run_config = payload.get("run_config", {}) if isinstance(payload.get("run_config", {}), dict) else {}
    return _submit_project_review_task(
        task_type="section_replay",
        project_id=project_id,
        source_doc_id=doc_id,
        section_id=section_id,
        run_config=run_config,
        created_message="章节审评任务已创建",
        persistence_error_message="section replay task persistence failed",
        worker_error_message="section replay worker start failed",
        worker_start=lambda task_id: _run_section_replay_task_async(
            task_id=task_id,
            project_id=project_id,
            source_doc_id=doc_id,
            section_id=section_id,
            run_config=run_config,
        ),
    )


@pre_review_bp.post("/projects/<project_id>/submissions/<doc_id>/sections/<section_id>/module-replay")
def replay_submission_module(project_id: str, doc_id: str, section_id: str):
    payload = request.get_json(silent=True) or {}
    run_config = payload.get("run_config", {}) if isinstance(payload.get("run_config", {}), dict) else {}
    ok, msg, data = get_pre_review_service().run_module_replay(
        project_id=project_id,
        source_doc_id=doc_id,
        module_section_id=section_id,
        run_config=run_config,
    )
    if not ok:
        status = 404 if "not found" in msg.lower() else 400
        return ResponseMessage(status, msg, None).to_json(), status
    return ResponseMessage(200, msg, data).to_json()


@pre_review_bp.post("/projects/<project_id>/submissions/<doc_id>/sections/<section_id>/async-module-replay")
def replay_submission_module_async(project_id: str, doc_id: str, section_id: str):
    payload = request.get_json(silent=True) or {}
    run_config = payload.get("run_config", {}) if isinstance(payload.get("run_config", {}), dict) else {}
    return _submit_project_review_task(
        task_type="module_replay",
        project_id=project_id,
        source_doc_id=doc_id,
        section_id=section_id,
        run_config=run_config,
        created_message="模块审评任务已创建",
        persistence_error_message="module replay task persistence failed",
        worker_error_message="module replay worker start failed",
        worker_start=lambda task_id: _run_module_replay_task_async(
            task_id=task_id,
            project_id=project_id,
            source_doc_id=doc_id,
            section_id=section_id,
            run_config=run_config,
        ),
    )


@pre_review_bp.get("/projects/<project_id>/submissions/<doc_id>/preview")
def submission_preview(project_id: str, doc_id: str):
    ok, msg, data = get_pre_review_service().get_submission_file_info(project_id=project_id, doc_id=doc_id)
    if not ok:
        status = 404 if "not found" in msg.lower() else 400
        return ResponseMessage(status, msg, None).to_json(), status
    return send_file(
        data["file_path"],
        as_attachment=False,
        download_name=data["file_name"],
        conditional=True,
    )


@pre_review_bp.get("/projects/<project_id>/submissions/<doc_id>/assets/<path:asset_path>")
def submission_asset_preview(project_id: str, doc_id: str, asset_path: str):
    ok, msg, data = get_pre_review_service().get_submission_asset_file_info(
        project_id=project_id,
        doc_id=doc_id,
        asset_path=asset_path,
    )
    if not ok:
        status = 404 if "not found" in msg.lower() else 400
        return ResponseMessage(status, msg, None).to_json(), status
    return send_file(
        data["asset_file_path"],
        as_attachment=False,
        download_name=data["asset_name"],
        conditional=True,
    )


@pre_review_bp.post("/runs")
def run_pre_review():
    payload = request.get_json(silent=True) or {}
    project_id = (
        str(payload.get("project_id", "")).strip()
        or str(payload.get("projectId", "")).strip()
    )
    source_doc_id = (
        str(payload.get("source_doc_id", "")).strip()
        or str(payload.get("sourceDocId", "")).strip()
        or str(payload.get("doc_id", "")).strip()
        or str(payload.get("docId", "")).strip()
    )
    if not project_id or not source_doc_id:
        logging.warning(
            "run_pre_review missing required fields. payload_keys=%s",
            list(payload.keys()),
        )
        return ResponseMessage(400, "project_id and source_doc_id are required", None).to_json(), 400

    run_config = payload.get("run_config", {}) if isinstance(payload.get("run_config", {}), dict) else {}
    ok, msg, data = get_pre_review_service().run_pre_review(project_id, source_doc_id, run_config=run_config)
    if not ok:
        logging.warning(
            "run_pre_review failed. project_id=%s source_doc_id=%s msg=%s",
            project_id,
            source_doc_id,
            msg,
        )
        status = 404 if "not found" in msg.lower() else 400
        return ResponseMessage(status, msg, None).to_json(), status
    return ResponseMessage(200, msg, data).to_json()


@pre_review_bp.post("/runs/history")
def run_history():
    payload = request.get_json(silent=True) or {}
    project_id = str(payload.get("project_id", ""))
    if not project_id:
        return ResponseMessage(400, "project_id is required", None).to_json(), 400
    data = get_pre_review_service().get_run_history(project_id)
    return ResponseMessage(200, "success", data).to_json()


@pre_review_bp.post("/dashboard")
def dashboard_summary():
    data = get_pre_review_service().get_dashboard_summary()
    return ResponseMessage(200, "success", data).to_json()


@pre_review_bp.post("/runs/<run_id>/sections")
def get_sections(run_id: str):
    payload = request.get_json(silent=True) or {}
    section_id = str(payload.get("section_id", ""))
    data = get_pre_review_service().get_section_conclusions(run_id, section_id)
    return ResponseMessage(200, "success", data).to_json()


@pre_review_bp.post("/runs/<run_id>/sections/overview")
def get_sections_overview(run_id: str):
    ok, msg, data = get_pre_review_service().get_run_section_overview(run_id=run_id)
    if not ok:
        status = 404 if "not found" in msg.lower() else 400
        return ResponseMessage(status, msg, None).to_json(), status
    return ResponseMessage(200, msg, data).to_json()


@pre_review_bp.post("/runs/<run_id>/traces")
def get_traces(run_id: str):
    payload = request.get_json(silent=True) or {}
    section_id = str(payload.get("section_id", ""))
    compact_value = payload.get("compact", False)
    compact = compact_value is True or str(compact_value or "").strip().lower() in {"1", "true", "yes", "on"}
    data = get_pre_review_service().get_section_traces(run_id, section_id, compact=compact)
    return ResponseMessage(200, "success", data).to_json()


@pre_review_bp.post("/runs/<run_id>/audits")
def get_execution_audits(run_id: str):
    payload = request.get_json(silent=True) or {}
    section_id = str(payload.get("section_id", "") or "")
    ok, msg, data = get_pre_review_service().get_execution_audits(run_id=run_id, section_id=section_id)
    if not ok:
        status = 404 if "not found" in msg.lower() else 400
        return ResponseMessage(status, msg, None).to_json(), status
    return ResponseMessage(200, msg, data).to_json()


@pre_review_bp.post("/runs/<run_id>/traces/<section_id>/artifact")
def get_trace_artifact(run_id: str, section_id: str):
    ok, msg, data = get_pre_review_service().get_section_trace_artifact(run_id=run_id, section_id=section_id)
    if not ok:
        status = 404 if "not found" in msg.lower() or "not exists" in msg.lower() else 400
        return ResponseMessage(status, msg, None).to_json(), status
    return ResponseMessage(200, msg, data).to_json()


@pre_review_bp.post("/runs/<run_id>/patches")
def get_patches(run_id: str):
    payload = request.get_json(silent=True) or {}
    section_id = str(payload.get("section_id", "") or "")
    ok, msg, data = get_pre_review_service().get_section_patch_candidates(run_id=run_id, section_id=section_id)
    if not ok:
        status = 404 if "not found" in msg.lower() else 400
        return ResponseMessage(status, msg, None).to_json(), status
    return ResponseMessage(200, msg, data).to_json()


@pre_review_bp.post("/runs/async-start")
def start_pre_review_async():
    payload = request.get_json(silent=True) or {}
    project_id = (
        str(payload.get("project_id", "")).strip()
        or str(payload.get("projectId", "")).strip()
    )
    source_doc_id = (
        str(payload.get("source_doc_id", "")).strip()
        or str(payload.get("sourceDocId", "")).strip()
        or str(payload.get("doc_id", "")).strip()
        or str(payload.get("docId", "")).strip()
    )
    if not project_id or not source_doc_id:
        return ResponseMessage(400, "project_id and source_doc_id are required", None).to_json(), 400
    run_config = payload.get("run_config", {}) if isinstance(payload.get("run_config", {}), dict) else {}
    return _submit_project_review_task(
        task_type="run",
        project_id=project_id,
        source_doc_id=source_doc_id,
        section_id="",
        run_config=run_config,
        created_message="预审轮询任务已创建",
        persistence_error_message="pre-review task persistence failed",
        worker_error_message="pre-review worker start failed",
        worker_start=lambda task_id: _run_pre_review_task_async(
            task_id=task_id,
            project_id=project_id,
            source_doc_id=source_doc_id,
            run_config=run_config,
        ),
    )


@pre_review_bp.get("/runs/tasks/<task_id>/progress")
def get_pre_review_task_progress(task_id: str):
    cursor_raw = str(request.args.get("cursor", "0") or "0").strip()
    try:
        cursor = int(cursor_raw)
    except Exception:
        cursor = 0
    snapshot = _get_pre_review_task_snapshot(task_id=task_id, cursor=cursor)
    if snapshot is None:
        snapshot = get_pre_review_service().get_upload_task_progress_snapshot(task_id=task_id, cursor=cursor)
    if snapshot is None:
        return ResponseMessage(404, "task not found", None).to_json(), 404
    return ResponseMessage(200, "success", snapshot).to_json()


@pre_review_bp.get("/projects/<project_id>/runs/tasks/latest")
def get_latest_project_review_task(project_id: str):
    include_terminal_raw = str(request.args.get("include_terminal", "1") or "1").strip().lower()
    include_terminal = include_terminal_raw not in {"0", "false", "no", "off"}
    try:
        _prune_pre_review_tasks()
        task = get_runtime_task_store().get_latest_project_task(
            domain=_TASK_DOMAIN,
            project_id=project_id,
            task_types=list(_MAIN_REVIEW_TASK_TYPES),
            include_terminal=include_terminal,
        )
    except Exception as exc:
        logging.exception("get latest project review task failed project_id=%s", project_id)
        return ResponseMessage(
            500,
            f"查询项目审评任务状态失败: {exc}",
            None,
        ).to_json(), 500
    if task is not None:
        task["progress_url"] = f"/api/pre-review/runs/tasks/{task['task_id']}/progress"
    return ResponseMessage(200, "success", task).to_json()


@pre_review_bp.get("/runs/stream")
def run_pre_review_stream():
    project_id = str(request.args.get("project_id", "")).strip()
    source_doc_id = str(request.args.get("source_doc_id", "")).strip()
    if not project_id or not source_doc_id:
        return ResponseMessage(400, "project_id and source_doc_id are required", None).to_json(), 400

    run_config_raw = str(request.args.get("run_config", "") or "").strip()
    try:
        run_config = json.loads(run_config_raw) if run_config_raw else {}
    except Exception:
        return ResponseMessage(400, "run_config must be valid JSON", None).to_json(), 400
    if not isinstance(run_config, dict):
        return ResponseMessage(400, "run_config must be a JSON object", None).to_json(), 400
    try:
        max_seconds = int(request.args.get("max_seconds", "900"))
    except Exception:
        max_seconds = 900
    max_seconds = max(30, min(max_seconds, 7200))

    event_queue: "queue.Queue[dict]" = queue.Queue()
    finished = threading.Event()

    def _emit_progress(event_payload: dict) -> None:
        event_queue.put({"event": "progress", "data": event_payload})

    def _worker() -> None:
        try:
            ok, msg, data = get_pre_review_service().run_pre_review(
                project_id,
                source_doc_id,
                run_config=run_config,
                progress_callback=_emit_progress,
            )
            event_queue.put(
                {
                    "event": "done" if ok else "error",
                    "data": {
                        "ok": ok,
                        "message": msg,
                        "data": data,
                        "project_id": project_id,
                        "source_doc_id": source_doc_id,
                    },
                }
            )
        except Exception as exc:
            event_queue.put(
                {
                    "event": "error",
                    "data": {
                        "ok": False,
                        "message": str(exc),
                        "project_id": project_id,
                        "source_doc_id": source_doc_id,
                    },
                }
            )
        finally:
            finished.set()

    @stream_with_context
    def _gen():
        worker = threading.Thread(target=_worker, name=f"pre-review-sse-{project_id}", daemon=True)
        worker.start()
        started = time.time()
        init_payload = {
            "stage": "stream_ready",
            "message": "sse stream ready",
            "project_id": project_id,
            "source_doc_id": source_doc_id,
        }
        yield f"event: progress\ndata: {json.dumps(init_payload, ensure_ascii=False)}\n\n"
        while True:
            try:
                item = event_queue.get(timeout=1.0)
                yield f"event: {item['event']}\ndata: {json.dumps(item['data'], ensure_ascii=False)}\n\n"
                if finished.is_set() and event_queue.empty():
                    break
            except queue.Empty:
                yield "event: ping\ndata: {}\n\n"
                if finished.is_set() and event_queue.empty():
                    break
                if time.time() - started >= max_seconds:
                    timeout_payload = {
                        "ok": False,
                        "message": "stream timeout",
                        "project_id": project_id,
                        "source_doc_id": source_doc_id,
                    }
                    yield f"event: error\ndata: {json.dumps(timeout_payload, ensure_ascii=False)}\n\n"
                    break

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return Response(_gen(), mimetype="text/event-stream", headers=headers)


@pre_review_bp.post("/runs/<run_id>/export")
def export_report(run_id: str):
    ok, msg, path = get_pre_review_service().export_report_word(run_id)
    if not ok:
        return ResponseMessage(400, msg, None).to_json(), 400
    return ResponseMessage(200, msg, {"run_id": run_id, "report_path": path}).to_json()


@pre_review_bp.post("/runs/<run_id>/review-conclusions/export")
def export_review_conclusions(run_id: str):
    ok, msg, data = get_pre_review_service().export_review_conclusions_report(run_id)
    if not ok:
        return ResponseMessage(400, msg, None).to_json(), 400
    return ResponseMessage(
        200,
        msg,
        {
            **(data or {}),
            "download_url": f"/api/pre-review/runs/{run_id}/review-conclusions/download",
        },
    ).to_json()


@pre_review_bp.get("/runs/<run_id>/review-conclusions/download")
def download_review_conclusions(run_id: str):
    ok, msg, data = get_pre_review_service().export_review_conclusions_report(run_id)
    if not ok or not isinstance(data, dict):
        status = 404 if "not found" in str(msg).lower() else 400
        return ResponseMessage(status, msg, None).to_json(), status
    return send_file(
        str(data.get("report_path", "") or ""),
        as_attachment=True,
        download_name=str(data.get("file_name", "") or f"{run_id}_review_conclusions.docx"),
        mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        conditional=True,
    )


@pre_review_bp.post("/runs/<run_id>/feedback")
def add_feedback(run_id: str):
    payload = request.get_json(silent=True) or {}
    section_id = str(payload.get("section_id", "") or "").strip()
    if not section_id and isinstance(payload.get("original_output", {}), dict):
        section_id = str(payload.get("original_output", {}).get("section_id", "") or "").strip()
    if not section_id:
        return ResponseMessage(400, "section_id is required for chapter-level feedback", None).to_json(), 400
    feedback_type = str(payload.get("feedback_type", "") or "").strip().lower()
    if not feedback_type:
        decision = str(payload.get("decision", "") or "").strip().lower()
        if decision in {"false_positive", "invalid", "rejected"}:
            feedback_type = "false_positive"
        elif decision in {"missed", "missing_risk"}:
            feedback_type = "missed"
        else:
            feedback_type = "valid"
    ok, msg, data = get_pre_review_service().add_feedback(
        run_id=run_id,
        section_id=section_id,
        feedback_type=feedback_type,
        feedback_text=payload.get("feedback_text", ""),
        suggestion=payload.get("suggestion", ""),
        operator=payload.get("operator", ""),
        feedback_meta={
            "chain_mode": str(payload.get("chain_mode", "") or "feedback_only"),
            "decision": str(payload.get("decision", "") or ""),
            "manual_modified": bool(payload.get("manual_modified", False)),
            "feedback_type": feedback_type,
            "labels": payload.get("labels", []) if isinstance(payload.get("labels", []), list) else [],
            "issue_feedback": payload.get("issue_feedback", []) if isinstance(payload.get("issue_feedback", []), list) else [],
            "evidence_feedback": payload.get("evidence_feedback", []) if isinstance(payload.get("evidence_feedback", []), list) else [],
            "missing_item_feedback": payload.get("missing_item_feedback", {}) if isinstance(payload.get("missing_item_feedback", {}), dict) else {},
            "retrieval_feedback": str(payload.get("retrieval_feedback", "") or ""),
            "conclusion_feedback": str(payload.get("conclusion_feedback", "") or ""),
            "reference_example": payload.get("reference_example", {}) if isinstance(payload.get("reference_example", {}), dict) else {},
            "original_output": payload.get("original_output", {}),
            "revised_output": payload.get("revised_output", {}),
            "suggestion": str(payload.get("suggestion", "") or ""),
            "persist_event": True,
        },
    )
    if not ok:
        return ResponseMessage(400, msg, None).to_json(), 400
    return ResponseMessage(200, msg, {"run_id": run_id, "stats": data}).to_json()


@pre_review_bp.post("/runs/<run_id>/feedback/optimize")
def optimize_feedback(run_id: str):
    payload = request.get_json(silent=True) or {}
    feedback_payload = _build_feedback_optimize_payload(run_id, payload)
    section_id = str(feedback_payload.get("section_id", "") or "").strip()
    if not section_id:
        return ResponseMessage(400, "section_id is required for chapter-level feedback optimize", None).to_json(), 400
    result = get_feedback_app_service().submit_feedback(feedback_payload)
    if not bool(result.get("success", False)):
        return ResponseMessage(400, str(result.get("message", "feedback optimize failed")), result.get("data")).to_json(), 400
    return ResponseMessage(200, str(result.get("message", "feedback optimize completed")), result.get("data")).to_json()


@pre_review_bp.post("/runs/<run_id>/feedback/optimize/async")
def optimize_feedback_async(run_id: str):
    payload = request.get_json(silent=True) or {}
    feedback_payload = _build_feedback_optimize_payload(run_id, payload)
    section_id = str(feedback_payload.get("section_id", "") or "").strip()
    if not section_id:
        return ResponseMessage(400, "section_id is required for chapter-level feedback optimize", None).to_json(), 400
    ok, msg, metadata = _get_run_task_metadata(run_id)
    if not ok:
        status = _task_metadata_failure_status(msg)
        return ResponseMessage(status, msg, None).to_json(), status
    return _submit_long_operation_task(
        task_type="feedback_optimize",
        project_id=metadata["project_id"],
        run_id=metadata["run_id"],
        source_doc_id=metadata["source_doc_id"],
        section_id=section_id,
        request_payload=feedback_payload,
        operation=lambda: get_feedback_app_service().submit_feedback(feedback_payload),
        result_style="application",
    )


@pre_review_bp.post("/runs/<run_id>/sections/<section_id>/p52-feedback/optimize")
def optimize_p52_feedback(run_id: str, section_id: str):
    payload = request.get_json(silent=True) or {}
    ok, msg, data = get_pre_review_service().optimize_p52_feedback(
        run_id=run_id,
        section_id=section_id,
        feedback_payload=payload if isinstance(payload, dict) else {},
    )
    if not ok:
        return ResponseMessage(400, msg, data).to_json(), 400
    return ResponseMessage(200, msg, data).to_json()


@pre_review_bp.post("/runs/<run_id>/sections/<section_id>/p52-feedback/optimize/async")
def optimize_p52_feedback_async(run_id: str, section_id: str):
    payload = request.get_json(silent=True) or {}
    ok, msg, metadata = _get_run_task_metadata(run_id)
    if not ok:
        status = _task_metadata_failure_status(msg)
        return ResponseMessage(status, msg, None).to_json(), status
    feedback_payload = payload if isinstance(payload, dict) else {}
    return _submit_long_operation_task(
        task_type="p52_feedback_optimize",
        project_id=metadata["project_id"],
        run_id=metadata["run_id"],
        source_doc_id=metadata["source_doc_id"],
        section_id=section_id,
        request_payload=feedback_payload,
        operation=lambda: get_pre_review_service().optimize_p52_feedback(
            run_id=run_id,
            section_id=section_id,
            feedback_payload=feedback_payload,
        ),
        result_style="service",
    )


@pre_review_bp.post("/runs/<run_id>/sections/<section_id>/p52-feedback/patches")
def list_p52_feedback_patches(run_id: str, section_id: str):
    payload = request.get_json(silent=True) or {}
    ok, msg, data = get_pre_review_service().list_p52_feedback_patches(
        run_id=run_id,
        section_id=section_id,
        status=str(payload.get("status", "") or "").strip(),
    )
    if not ok:
        return ResponseMessage(400, msg, data).to_json(), 400
    return ResponseMessage(200, msg, data).to_json()


@pre_review_bp.post("/runs/<run_id>/sections/<section_id>/p52-feedback/replay-verify")
def replay_verify_p52_feedback(run_id: str, section_id: str):
    payload = request.get_json(silent=True) or {}
    ok, msg, data = get_pre_review_service().replay_verify_p52_feedback(
        run_id=run_id,
        section_id=section_id,
        feedback_payload=payload if isinstance(payload, dict) else {},
    )
    if not ok:
        return ResponseMessage(400, msg, data).to_json(), 400
    return ResponseMessage(200, msg, data).to_json()


@pre_review_bp.post("/runs/<run_id>/sections/<section_id>/p52-feedback/replay-verify/async")
def replay_verify_p52_feedback_async(run_id: str, section_id: str):
    payload = request.get_json(silent=True) or {}
    ok, msg, metadata = _get_run_task_metadata(run_id)
    if not ok:
        status = _task_metadata_failure_status(msg)
        return ResponseMessage(status, msg, None).to_json(), status
    feedback_payload = payload if isinstance(payload, dict) else {}
    return _submit_long_operation_task(
        task_type="p52_feedback_verify",
        project_id=metadata["project_id"],
        run_id=metadata["run_id"],
        source_doc_id=metadata["source_doc_id"],
        section_id=section_id,
        request_payload=feedback_payload,
        operation=lambda: get_pre_review_service().replay_verify_p52_feedback(
            run_id=run_id,
            section_id=section_id,
            feedback_payload=feedback_payload,
        ),
        result_style="service",
    )


@pre_review_bp.post("/runs/<run_id>/sections/<section_id>/p52-feedback/meta-reflection")
def replay_p52_meta_reflection(run_id: str, section_id: str):
    payload = request.get_json(silent=True) or {}
    ok, msg, data = get_pre_review_service().replay_p52_meta_reflection(
        run_id=run_id,
        section_id=section_id,
        feedback_payload=payload if isinstance(payload, dict) else {},
    )
    if not ok:
        return ResponseMessage(400, msg, data).to_json(), 400
    return ResponseMessage(200, msg, data).to_json()


@pre_review_bp.post("/runs/<run_id>/sections/<section_id>/p52-feedback/meta-reflection/async")
def replay_p52_meta_reflection_async(run_id: str, section_id: str):
    payload = request.get_json(silent=True) or {}
    ok, msg, metadata = _get_run_task_metadata(run_id)
    if not ok:
        status = _task_metadata_failure_status(msg)
        return ResponseMessage(status, msg, None).to_json(), status
    feedback_payload = payload if isinstance(payload, dict) else {}
    return _submit_long_operation_task(
        task_type="p52_meta_reflection",
        project_id=metadata["project_id"],
        run_id=metadata["run_id"],
        source_doc_id=metadata["source_doc_id"],
        section_id=section_id,
        request_payload=feedback_payload,
        operation=lambda: get_pre_review_service().replay_p52_meta_reflection(
            run_id=run_id,
            section_id=section_id,
            feedback_payload=feedback_payload,
        ),
        result_style="service",
    )


@pre_review_bp.post("/runs/<run_id>/sections/<section_id>/p52-feedback/ablation")
def run_p52_ablation(run_id: str, section_id: str):
    payload = request.get_json(silent=True) or {}
    ok, msg, data = get_pre_review_service().run_p52_ablation_study(
        run_id=run_id,
        section_id=section_id,
        feedback_payload=payload if isinstance(payload, dict) else {},
    )
    if not ok:
        return ResponseMessage(400, msg, data).to_json(), 400
    return ResponseMessage(200, msg, data).to_json()


@pre_review_bp.post("/runs/<run_id>/sections/<section_id>/p52-feedback/patches/<patch_id>/approve")
def approve_p52_feedback_patch(run_id: str, section_id: str, patch_id: str):
    payload = request.get_json(silent=True) or {}
    ok, msg, data = get_pre_review_service().approve_p52_feedback_patch(
        run_id=run_id,
        section_id=section_id,
        patch_id=patch_id,
        payload=payload if isinstance(payload, dict) else {},
    )
    if not ok:
        return ResponseMessage(400, msg, data).to_json(), 400
    return ResponseMessage(200, msg, data).to_json()


@pre_review_bp.post("/runs/<run_id>/sections/<section_id>/p52-feedback/patches/<patch_id>/reject")
def reject_p52_feedback_patch(run_id: str, section_id: str, patch_id: str):
    payload = request.get_json(silent=True) or {}
    ok, msg, data = get_pre_review_service().reject_p52_feedback_patch(
        run_id=run_id,
        section_id=section_id,
        patch_id=patch_id,
        payload=payload if isinstance(payload, dict) else {},
    )
    if not ok:
        return ResponseMessage(400, msg, data).to_json(), 400
    return ResponseMessage(200, msg, data).to_json()


@pre_review_bp.post("/runs/<run_id>/feedback/<feedback_key>/evaluate")
def evaluate_feedback_optimize(run_id: str, feedback_key: str):
    payload = request.get_json(silent=True) or {}
    ok, msg, data = get_pre_review_service().evaluate_feedback_optimize(
        run_id=run_id,
        feedback_key=feedback_key,
        section_id=str(payload.get("section_id", "") or ""),
        evaluation_payload=payload,
    )
    if not ok:
        return ResponseMessage(400, msg, None).to_json(), 400
    return ResponseMessage(200, msg, data).to_json()


@pre_review_bp.post("/runs/<run_id>/sections/<section_id>/replay/feedback-optimize")
def replay_feedback_optimize(run_id: str, section_id: str):
    payload = request.get_json(silent=True) or {}
    ok, msg, data = get_pre_review_service().replay_feedback_optimize(
        run_id=run_id,
        section_id=section_id,
        feedback_payload=payload if isinstance(payload, dict) else {},
    )
    if not ok:
        return ResponseMessage(400, msg, data).to_json(), 400
    return ResponseMessage(200, msg, {"chapter_feedback_loop": data}).to_json()


@pre_review_bp.post("/runs/<run_id>/sections/<section_id>/replay/feedback-optimize/async")
def replay_feedback_optimize_async(run_id: str, section_id: str):
    payload = request.get_json(silent=True) or {}
    ok, msg, metadata = _get_run_task_metadata(run_id)
    if not ok:
        status = _task_metadata_failure_status(msg)
        return ResponseMessage(status, msg, None).to_json(), status
    feedback_payload = payload if isinstance(payload, dict) else {}
    return _submit_long_operation_task(
        task_type="feedback_replay_optimize",
        project_id=metadata["project_id"],
        run_id=metadata["run_id"],
        source_doc_id=metadata["source_doc_id"],
        section_id=section_id,
        request_payload=feedback_payload,
        operation=lambda: get_pre_review_service().replay_feedback_optimize(
            run_id=run_id,
            section_id=section_id,
            feedback_payload=feedback_payload,
        ),
        result_style="service",
    )


@pre_review_bp.post("/runs/<run_id>/sections/<section_id>/replay/meta-reflection")
def replay_meta_reflection(run_id: str, section_id: str):
    payload = request.get_json(silent=True) or {}
    ok, msg, data = get_pre_review_service().replay_meta_reflection(
        run_id=run_id,
        section_id=section_id,
        feedback_payload=payload if isinstance(payload, dict) else {},
    )
    if not ok:
        return ResponseMessage(400, msg, data).to_json(), 400
    return ResponseMessage(200, msg, {"chapter_feedback_loop": data}).to_json()


@pre_review_bp.post("/runs/<run_id>/sections/<section_id>/replay/meta-reflection/async")
def replay_meta_reflection_async(run_id: str, section_id: str):
    payload = request.get_json(silent=True) or {}
    ok, msg, metadata = _get_run_task_metadata(run_id)
    if not ok:
        status = _task_metadata_failure_status(msg)
        return ResponseMessage(status, msg, None).to_json(), status
    feedback_payload = payload if isinstance(payload, dict) else {}
    return _submit_long_operation_task(
        task_type="feedback_meta_reflection",
        project_id=metadata["project_id"],
        run_id=metadata["run_id"],
        source_doc_id=metadata["source_doc_id"],
        section_id=section_id,
        request_payload=feedback_payload,
        operation=lambda: get_pre_review_service().replay_meta_reflection(
            run_id=run_id,
            section_id=section_id,
            feedback_payload=feedback_payload,
        ),
        result_style="service",
    )


@pre_review_bp.post("/runs/<run_id>/feedback/stats")
def feedback_stats(run_id: str):
    payload = request.get_json(silent=True) or {}
    section_id = str(payload.get("section_id", ""))
    ok, msg, data = get_pre_review_service().get_feedback_stats(run_id=run_id, section_id=section_id)
    if not ok:
        return ResponseMessage(400, msg, None).to_json(), 400
    return ResponseMessage(200, msg, data).to_json()


@pre_review_bp.post("/feedback/evaluation/replay-cases")
def replay_cases():
    payload = request.get_json(silent=True) or {}
    case_ids = payload.get("case_ids", []) if isinstance(payload.get("case_ids", []), list) else []
    version_config = payload.get("version_config", {}) if isinstance(payload.get("version_config", {}), dict) else {}
    result = get_feedback_app_service().replay_cases(case_ids, version_config)
    if not bool(result.get("success", False)):
        return ResponseMessage(400, str(result.get("message", "replay failed")), result.get("data")).to_json(), 400
    return ResponseMessage(200, str(result.get("message", "replay executed")), result.get("data")).to_json()


@pre_review_bp.post("/feedback/evaluation/replay-cases/async")
def replay_cases_async():
    payload = request.get_json(silent=True) or {}
    case_ids = payload.get("case_ids", []) if isinstance(payload.get("case_ids", []), list) else []
    version_config = payload.get("version_config", {}) if isinstance(payload.get("version_config", {}), dict) else {}
    ok, msg, metadata = _get_evaluation_task_metadata(payload, case_ids)
    if not ok:
        status = _task_metadata_failure_status(msg)
        return ResponseMessage(status, msg, None).to_json(), status
    operation_payload = {
        **dict(payload),
        "case_ids": list(case_ids),
        "version_config": dict(version_config),
    }
    return _submit_long_operation_task(
        task_type="feedback_evaluation_replay",
        project_id=metadata["project_id"],
        run_id=metadata["run_id"],
        source_doc_id=metadata["source_doc_id"],
        section_id=metadata["section_id"],
        request_payload=operation_payload,
        operation=lambda: get_feedback_app_service().replay_cases(case_ids, version_config),
        result_style="application",
    )


@pre_review_bp.post("/feedback/evaluation/regression-cases")
def generate_regression_cases():
    payload = request.get_json(silent=True) or {}
    filters = payload if isinstance(payload, dict) else {}
    result = get_feedback_app_service().generate_regression_cases(filters)
    if not bool(result.get("success", False)):
        return ResponseMessage(400, str(result.get("message", "regression case generation failed")), result.get("data")).to_json(), 400
    return ResponseMessage(200, str(result.get("message", "regression cases generated")), result.get("data")).to_json()


@pre_review_bp.post("/feedback/evaluation/ablation")
def run_ablation_study():
    payload = request.get_json(silent=True) or {}
    case_ids = payload.get("case_ids", []) if isinstance(payload.get("case_ids", []), list) else []
    version_config = payload.get("version_config", {}) if isinstance(payload.get("version_config", {}), dict) else {}
    study_config = payload.get("study_config", {}) if isinstance(payload.get("study_config", {}), dict) else {}
    result = get_feedback_app_service().run_ablation_study(case_ids, version_config, study_config=study_config)
    if not bool(result.get("success", False)):
        return ResponseMessage(400, str(result.get("message", "ablation failed")), result.get("data")).to_json(), 400
    return ResponseMessage(200, str(result.get("message", "ablation study executed")), result.get("data")).to_json()


@pre_review_bp.post("/feedback/evaluation/ablation/async")
def run_ablation_study_async():
    payload = request.get_json(silent=True) or {}
    case_ids = payload.get("case_ids", []) if isinstance(payload.get("case_ids", []), list) else []
    version_config = payload.get("version_config", {}) if isinstance(payload.get("version_config", {}), dict) else {}
    study_config = payload.get("study_config", {}) if isinstance(payload.get("study_config", {}), dict) else {}
    ok, msg, metadata = _get_evaluation_task_metadata(payload, case_ids)
    if not ok:
        status = _task_metadata_failure_status(msg)
        return ResponseMessage(status, msg, None).to_json(), status
    operation_payload = {
        **dict(payload),
        "case_ids": list(case_ids),
        "version_config": dict(version_config),
        "study_config": dict(study_config),
    }
    return _submit_long_operation_task(
        task_type="feedback_evaluation_ablation",
        project_id=metadata["project_id"],
        run_id=metadata["run_id"],
        source_doc_id=metadata["source_doc_id"],
        section_id=metadata["section_id"],
        request_payload=operation_payload,
        operation=lambda: get_feedback_app_service().run_ablation_study(
            case_ids,
            version_config,
            study_config=study_config,
        ),
        result_style="application",
    )


@pre_review_bp.post("/prompt-versions/list")
def list_prompt_versions():
    result = get_feedback_app_service().list_prompt_versions()
    return ResponseMessage(200, str(result.get("message", "success")), result.get("data")).to_json()


@pre_review_bp.post("/prompt-versions/<version_id>/activate")
def activate_prompt_version(version_id: str):
    try:
        result = get_feedback_app_service().activate_prompt_version(version_id)
    except FileNotFoundError as exc:
        return ResponseMessage(404, str(exc), None).to_json(), 404
    return ResponseMessage(200, str(result.get("message", "prompt version activated")), result.get("data")).to_json()


@pre_review_bp.post("/prompt-versions/rollback")
def rollback_prompt_version():
    result = get_feedback_app_service().rollback_prompt_version()
    return ResponseMessage(200, str(result.get("message", "prompt version rolled back")), result.get("data")).to_json()
