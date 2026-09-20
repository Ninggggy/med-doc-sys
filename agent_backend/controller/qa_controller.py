from flask import Blueprint, request

from agent.agent_backend.services.knowledge_service import KnowledgeService
from agent.agent_backend.utils.common_util import ResponseMessage


qa_bp = Blueprint("qa_controller", __name__, url_prefix="/qa")
_knowledge_service: KnowledgeService | None = None


def get_knowledge_service() -> KnowledgeService:
    global _knowledge_service
    if _knowledge_service is None:
        _knowledge_service = KnowledgeService()
    return _knowledge_service


@qa_bp.post("/health")
def health():
    return ResponseMessage(200, "ok", {"ok": True}).to_json()


@qa_bp.post("/ask")
def ask():
    payload = request.get_json(silent=True) or {}
    query = str(payload.get("query", "")).strip()
    if not query:
        return ResponseMessage(400, "query is required", None).to_json(), 400

    try:
        top_k = int(payload.get("top_k", 5))
    except Exception:
        return ResponseMessage(400, "top_k must be integer", None).to_json(), 400
    if top_k <= 0:
        return ResponseMessage(400, "top_k must be > 0", None).to_json(), 400
    try:
        min_score = float(payload.get("min_score", 0.0))
    except Exception:
        return ResponseMessage(400, "min_score must be number", None).to_json(), 400

    classification = str(payload.get("classification", "")).strip()
    result = get_knowledge_service().semantic_query(
        query,
        top_k=top_k,
        classification=classification,
        min_score=min_score,
    )
    hits = result.get("list", []) if isinstance(result, dict) else []
    answer = "\n".join([f"- {x.get('content', '')}" for x in hits if isinstance(x, dict) and x.get("content")]).strip()
    if not answer:
        answer = "No matched knowledge found."

    return ResponseMessage(
        200,
        "success",
        {"query": query, "answer": answer, "hits": hits, "hit_count": len(hits), "grouped_docs": result.get("grouped_docs", []) if isinstance(result, dict) else []},
    ).to_json()
