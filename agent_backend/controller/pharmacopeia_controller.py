from flask import Blueprint, request

from agent.agent_backend.services.pharmacopeia_service import PharmacopeiaService
from agent.agent_backend.utils.common_util import ResponseMessage


pharmacopeia_bp = Blueprint("pharmacopeia_controller", __name__, url_prefix="/pharmacopeia")
_pharmacopeia_service: PharmacopeiaService | None = None


def get_pharmacopeia_service() -> PharmacopeiaService:
    global _pharmacopeia_service
    if _pharmacopeia_service is None:
        _pharmacopeia_service = PharmacopeiaService()
    return _pharmacopeia_service


@pharmacopeia_bp.post("/list")
def list_pharmacopeia_entries():
    payload = request.get_json(silent=True) or {}
    data = get_pharmacopeia_service().list_entries(
        keyword=str(payload.get("keyword", "") or ""),
        affect_range=str(payload.get("affect_range", "") or ""),
        page=payload.get("page", 1),
        page_size=payload.get("page_size", 20),
    )
    return ResponseMessage(200, "success", data).to_json()


@pharmacopeia_bp.post("")
def create_pharmacopeia_entry():
    payload = request.get_json(silent=True) or {}
    ok, msg, data = get_pharmacopeia_service().create_entry(payload)
    if not ok:
        return ResponseMessage(400, msg, data).to_json(), 400
    return ResponseMessage(200, msg, data).to_json()


@pharmacopeia_bp.post("/<entry_id>")
def update_pharmacopeia_entry(entry_id: str):
    payload = request.get_json(silent=True) or {}
    ok, msg, data = get_pharmacopeia_service().update_entry(entry_id, payload)
    if not ok:
        return ResponseMessage(400, msg, data).to_json(), 400
    return ResponseMessage(200, msg, data).to_json()


@pharmacopeia_bp.post("/<entry_id>/delete")
def delete_pharmacopeia_entry(entry_id: str):
    ok, msg = get_pharmacopeia_service().delete_entry(entry_id)
    if not ok:
        return ResponseMessage(400, msg, None).to_json(), 400
    return ResponseMessage(200, msg, {"entry_id": entry_id}).to_json()


@pharmacopeia_bp.post("/import-json")
def import_pharmacopeia_json():
    file_obj = request.files.get("file")
    affect_range = str(request.form.get("affect_range", "") or "").strip()
    if affect_range not in {"中药", "化学药"}:
        return ResponseMessage(400, "affect_range must be 中药 or 化学药", None).to_json(), 400
    ok, msg, data = get_pharmacopeia_service().import_json_file(file_obj=file_obj, affect_range=affect_range)
    if not ok:
        return ResponseMessage(400, msg, data).to_json(), 400
    return ResponseMessage(200, msg, data).to_json()
