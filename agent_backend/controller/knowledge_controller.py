import json
import os
import shutil
import tempfile
import time
import zipfile

from flask import Blueprint, request, Response, stream_with_context

from agent.agent_backend.services.knowledge_service import KnowledgeService
from agent.agent_backend.services.cde_guideline_sync_service import CDEGuidelineSyncService
from agent.agent_backend.utils.common_util import ResponseMessage


knowledge_bp = Blueprint("knowledge_controller", __name__, url_prefix="/knowledge")
_knowledge_service: KnowledgeService | None = None


def _decode_zip_member_name(raw_name: str) -> str:
    base_name = os.path.basename(str(raw_name or "")).strip()
    if not base_name:
        return ""
    # 兼容 Windows 常见的 zip 中文文件名编码：cp437 误解码自 gbk/gb18030。
    if any("\u2500" <= ch <= "\u257f" or "\u2580" <= ch <= "\u259f" for ch in base_name):
        for enc in ("gbk", "gb18030", "utf-8"):
            try:
                decoded = base_name.encode("cp437").decode(enc)
                if decoded:
                    return os.path.basename(decoded).strip()
            except Exception:
                continue
    return base_name


def get_knowledge_service() -> KnowledgeService:
    global _knowledge_service
    if _knowledge_service is None:
        _knowledge_service = KnowledgeService()
    return _knowledge_service


@knowledge_bp.post("/upload")
def upload_knowledge():
    files = [f for f in request.files.getlist("files") if getattr(f, "filename", "")]
    if not files and "file" in request.files:
        file_obj = request.files["file"]
        if getattr(file_obj, "filename", ""):
            files = [file_obj]
    if not files:
        return ResponseMessage(400, "file is required", None).to_json(), 400
    classification = request.form.get("classification", "other")
    affect_range = request.form.get("affect_range", "other")
    profession_classification = request.form.get("profession_classification", "other")
    registration_scope = request.form.get("registration_scope", "other")
    registration_path = request.form.get("registration_path", "")
    experience_type = request.form.get("experience_type", "other")
    service = get_knowledge_service()
    supported_exts = {".pdf", ".doc", ".docx", ".txt", ".md"}
    uploaded_items = []
    failed_items = []

    def _handle_single_file(single_file):
        ok, msg, data = service.upload_knowledge(
            single_file,
            classification,
            affect_range,
            profession_classification,
            registration_scope,
            registration_path,
            experience_type,
        )
        if ok and data:
            uploaded_items.append(data)
        else:
            failed_items.append({
                "file_name": getattr(single_file, "filename", ""),
                "message": msg,
            })

    for file_obj in files:
        file_name = str(getattr(file_obj, "filename", "") or "")
        suffix = os.path.splitext(file_name)[1].lower()
        if suffix != ".zip":
            _handle_single_file(file_obj)
            continue

        try:
            raw_bytes = file_obj.read()
            file_obj.stream.seek(0)
            with tempfile.TemporaryDirectory(prefix="kb_zip_") as temp_dir:
                archive_path = os.path.join(temp_dir, file_name or "archive.zip")
                with open(archive_path, "wb") as archive_file:
                    archive_file.write(raw_bytes)
                with zipfile.ZipFile(archive_path, "r") as archive:
                    members = [m for m in archive.infolist() if not m.is_dir()]
                    extracted_any = False
                    for member in members:
                        member_name = _decode_zip_member_name(member.filename)
                        ext = os.path.splitext(member_name)[1].lower()
                        if not member_name or ext not in supported_exts:
                            continue
                        extracted_any = True
                        extracted_path = os.path.join(temp_dir, member_name)
                        with archive.open(member, "r") as src, open(extracted_path, "wb") as dst:
                            shutil.copyfileobj(src, dst)
                        ok, msg, data = service.upload_local_knowledge(
                            file_path=extracted_path,
                            file_name=member_name,
                            classification=classification,
                            affect_range=affect_range,
                            profession_classification=profession_classification,
                            registration_scope=registration_scope,
                            registration_path=registration_path,
                            experience_type=experience_type,
                        )
                        if ok and data:
                            uploaded_items.append(data)
                        else:
                            failed_items.append({
                                "file_name": member_name,
                                "message": msg,
                            })
                    if not extracted_any:
                        failed_items.append({
                            "file_name": file_name,
                            "message": "zip contains no supported files",
                        })
        except Exception as exc:
            failed_items.append({
                "file_name": file_name,
                "message": f"zip upload failed: {exc}",
            })

    if not uploaded_items:
        message = failed_items[0]["message"] if failed_items else "upload failed"
        return ResponseMessage(400, message, {
            "items": [],
            "failed": failed_items,
            "success_count": 0,
            "fail_count": len(failed_items),
        }).to_json(), 400

    response_data = {
        "items": uploaded_items,
        "failed": failed_items,
        "success_count": len(uploaded_items),
        "fail_count": len(failed_items),
    }
    if len(uploaded_items) == 1:
        response_data.update(uploaded_items[0])
    message = "upload completed"
    if failed_items:
        message = f"upload completed with {len(failed_items)} failures"
    return ResponseMessage(200, message, response_data).to_json()


@knowledge_bp.post("/<doc_id>")
def update_knowledge(doc_id: str):
    payload = request.get_json(silent=True) or {}
    ok, msg = get_knowledge_service().update_knowledge(doc_id, payload)
    if not ok:
        return ResponseMessage(400, msg, None).to_json(), 400
    return ResponseMessage(200, msg, {"doc_id": doc_id}).to_json()


@knowledge_bp.post("/<doc_id>/delete")
def delete_knowledge(doc_id: str):
    ok, msg, data = get_knowledge_service().delete_knowledge_detailed(doc_id)
    if not ok:
        code = 404 if data['delete_state'] == 'not_found' else (503 if data['delete_state'] == 'unknown' else 409)
        return ResponseMessage(code, msg, data).to_json(), code
    return ResponseMessage(200, msg, data).to_json()


@knowledge_bp.post("/batch-delete")
def batch_delete_knowledge():
    payload = request.get_json(silent=True) or {}
    doc_ids = payload.get("doc_ids", [])
    ok, msg, data = get_knowledge_service().delete_knowledge_batch(doc_ids if isinstance(doc_ids, list) else [])
    if not ok:
        return ResponseMessage(400, msg, data).to_json(), 400
    return ResponseMessage(200, msg, data).to_json()


@knowledge_bp.post("/query")
def query_knowledge():
    payload = request.get_json(silent=True) or {}
    page = payload.get("page", 1)
    page_size = payload.get("page_size", 10)
    try:
        page = int(page)
        page_size = int(page_size)
    except Exception:
        return ResponseMessage(400, "page/page_size must be integer", None).to_json(), 400
    if page <= 0 or page_size <= 0:
        return ResponseMessage(400, "page/page_size must be > 0", None).to_json(), 400

    data = get_knowledge_service().query_knowledge(
        file_name=str(payload.get("file_name", "")),
        keyword=str(payload.get("keyword", "")),
        file_type=str(payload.get("file_type", "")),
        classification=str(payload.get("classification", "")),
        affect_range=str(payload.get("affect_range", "")),
        profession_classification=str(payload.get("profession_classification", "")),
        registration_scope=str(payload.get("registration_scope", "")),
        registration_path=str(payload.get("registration_path", "")),
        experience_type=str(payload.get("experience_type", "")),
        start_time=str(payload.get("start_time", "")),
        end_time=str(payload.get("end_time", "")),
        page=page,
        page_size=page_size,
    )
    return ResponseMessage(200, "success", data).to_json()


@knowledge_bp.post("/semantic-query")
def semantic_query():
    payload = request.get_json(silent=True) or {}
    query = str(payload.get("query", "")).strip()
    if not query:
        return ResponseMessage(400, "query is required", None).to_json(), 400
    try:
        top_k = int(payload.get("top_k", 10))
    except Exception:
        return ResponseMessage(400, "top_k must be integer", None).to_json(), 400
    if top_k <= 0:
        return ResponseMessage(400, "top_k must be > 0", None).to_json(), 400
    try:
        min_score = float(payload.get("min_score", 0.0))
    except Exception:
        return ResponseMessage(400, "min_score must be number", None).to_json(), 400
    classification = str(payload.get("classification", "")).strip()
    from agent.agent_backend.services.knowledge_source_validity import KnowledgeSourceValidationError
    try:
        data = get_knowledge_service().semantic_query(
            query,
            top_k,
            classification=classification,
            min_score=min_score,
        )
    except KnowledgeSourceValidationError as exc:
        return ResponseMessage(503, str(exc), {"reason_code": exc.code}).to_json(), 503
    return ResponseMessage(200, "success", data).to_json()


@knowledge_bp.post("/parse-progress")
def parse_progress():
    payload = request.get_json(silent=True) or {}
    doc_id = str(payload.get("doc_id", "")).strip()
    data = get_knowledge_service().get_parse_progress(doc_id=doc_id)
    return ResponseMessage(200, "success", data).to_json()


@knowledge_bp.post("/<doc_id>/parse")
def parse_knowledge(doc_id: str):
    ok, msg, data = get_knowledge_service().submit_parse(doc_id)
    if not ok:
        return ResponseMessage(400, msg, None).to_json(), 400
    return ResponseMessage(200, msg, data).to_json()


@knowledge_bp.post("/<doc_id>/reindex")
def reindex_knowledge(doc_id: str):
    ok, msg, data = get_knowledge_service().submit_reindex(doc_id)
    if not ok:
        return ResponseMessage(400, msg, None).to_json(), 400
    return ResponseMessage(200, msg, data).to_json()


@knowledge_bp.get("/parse-progress/stream")
def parse_progress_stream():
    doc_id = str(request.args.get("doc_id", "")).strip()
    try:
        max_seconds = int(request.args.get("max_seconds", "300"))
    except Exception:
        max_seconds = 300
    max_seconds = max(15, min(max_seconds, 3600))

    def _gen():
        last_text = ""
        started = time.time()
        while True:
            try:
                data = get_knowledge_service().get_parse_progress(doc_id=doc_id)
            except Exception as exc:
                error_payload = {
                    "doc_id": doc_id,
                    "status": "failed",
                    "message": str(exc),
                    "tasks": [],
                }
                yield f"event: error\ndata: {json.dumps(error_payload, ensure_ascii=False)}\n\n"
                break
            text = json.dumps(data, ensure_ascii=False)
            if text != last_text:
                last_text = text
                yield f"event: progress\ndata: {text}\n\n"
                if doc_id:
                    tasks = data.get("tasks", []) if isinstance(data, dict) else []
                    if tasks and isinstance(tasks[0], dict) and tasks[0].get("status") in {"completed", "failed"}:
                        break
            else:
                yield "event: ping\ndata: {}\n\n"
            if time.time() - started >= max_seconds:
                break
            time.sleep(1)

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return Response(stream_with_context(_gen()), mimetype="text/event-stream", headers=headers)


# CDE 指导原则同步服务
_cde_sync_service: CDEGuidelineSyncService | None = None


def get_cde_sync_service() -> CDEGuidelineSyncService:
    global _cde_sync_service
    if _cde_sync_service is None:
        _cde_sync_service = CDEGuidelineSyncService()
    return _cde_sync_service


@knowledge_bp.post("/cde-guidelines/fetch")
def fetch_cde_guidelines():
    """获取 CDE 指导原则列表（预览模式，不下载）"""
    try:
        payload = request.get_json(silent=True) or {}
        classify1 = str(payload.get("classify1", "all")).strip()
        classify2 = str(payload.get("classify2", "all")).strip()
        start_date = str(payload.get("start_date", "")).strip() or None
        end_date = str(payload.get("end_date", "")).strip() or None
        keyword = str(payload.get("keyword", "")).strip() or None

        service = get_cde_sync_service()
        result = service.fetch_only(
            classify1=classify1,
            classify2=classify2,
            start_date=start_date,
            end_date=end_date,
            keyword=keyword,
        )

        if not result.get("success"):
            return ResponseMessage(400, result.get("error", "fetch failed"), None).to_json(), 400
        return ResponseMessage(200, "success", result).to_json()
    except Exception as e:
        return ResponseMessage(500, str(e), None).to_json(), 500


@knowledge_bp.post("/cde-guidelines/compare")
def compare_cde_guidelines():
    """对比 CDE 列表与本地知识库"""
    payload = request.get_json(silent=True) or {}
    classify1 = str(payload.get("classify1", "all")).strip()
    classify2 = str(payload.get("classify2", "all")).strip()
    start_date = str(payload.get("start_date", "")).strip() or None
    end_date = str(payload.get("end_date", "")).strip() or None
    keyword = str(payload.get("keyword", "")).strip() or None

    service = get_cde_sync_service()
    result = service.fetch_only(
        classify1=classify1,
        classify2=classify2,
        start_date=start_date,
        end_date=end_date,
        keyword=keyword,
    )

    if not result.get("success"):
        return ResponseMessage(400, result.get("error", "compare failed"), None).to_json(), 400
    return ResponseMessage(200, "success", result).to_json()


@knowledge_bp.post("/cde-guidelines/sync")
def sync_cde_guidelines():
    """执行 CDE 指导原则同步"""
    payload = request.get_json(silent=True) or {}
    classify1 = str(payload.get("classify1", "all")).strip()
    classify2 = str(payload.get("classify2", "all")).strip()
    start_date = str(payload.get("start_date", "")).strip() or None
    end_date = str(payload.get("end_date", "")).strip() or None
    keyword = str(payload.get("keyword", "")).strip() or None
    auto_download = bool(payload.get("auto_download", True))
    dry_run = bool(payload.get("dry_run", False))

    service = get_cde_sync_service()
    result = service.sync_guidelines(
        classify1=classify1,
        classify2=classify2,
        start_date=start_date,
        end_date=end_date,
        keyword=keyword,
        auto_download=auto_download,
        dry_run=dry_run,
    )

    if not result.get("success"):
        return ResponseMessage(400, result.get("error", "sync failed"), None).to_json(), 400
    return ResponseMessage(200, "sync completed", result).to_json()


@knowledge_bp.get("/cde-guidelines/sync/stream")
def sync_cde_guidelines_stream():
    """执行 CDE 指导原则同步（SSE 流式返回进度）"""
    classify1 = str(request.args.get("classify1", "all")).strip()
    classify2 = str(request.args.get("classify2", "all")).strip()
    start_date = str(request.args.get("start_date", "")).strip() or None
    end_date = str(request.args.get("end_date", "")).strip() or None
    keyword = str(request.args.get("keyword", "")).strip() or None
    auto_download = request.args.get("auto_download", "true").lower() == "true"
    dry_run = request.args.get("dry_run", "false").lower() == "true"

    def _gen():
        progress_queue = []

        def progress_callback(data):
            progress_queue.append(data)

        try:
            service = CDEGuidelineSyncService()
            result = service.sync_guidelines(
                classify1=classify1,
                classify2=classify2,
                start_date=start_date,
                end_date=end_date,
                keyword=keyword,
                auto_download=auto_download,
                dry_run=dry_run,
                progress_callback=progress_callback,
            )

            # 发送最终结果
            yield f"event: complete\ndata: {json.dumps(result, ensure_ascii=False)}\n\n"

        except Exception as exc:
            error_payload = {
                "success": False,
                "error": str(exc),
            }
            yield f"event: error\ndata: {json.dumps(error_payload, ensure_ascii=False)}\n\n"

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return Response(stream_with_context(_gen()), mimetype="text/event-stream", headers=headers)


@knowledge_bp.get("/cde-guidelines/sync/history")
def get_cde_sync_history():
    """获取同步历史"""
    service = get_cde_sync_service()
    history = service.get_sync_history()
    return ResponseMessage(200, "success", {"history": history}).to_json()
