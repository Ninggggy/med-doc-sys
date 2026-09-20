from flask import Blueprint, request

from agent.agent_backend.agentic_rl.model_evaluation import evaluate_model
from agent.agent_backend.agentic_rl.reward_functions import feedback_metrics
from agent.agent_backend.utils.common_util import ResponseMessage


rl_bp = Blueprint("rl_controller", __name__, url_prefix="/rl")


@rl_bp.post("/health")
def health():
    return ResponseMessage(200, "ok", {"ok": True}).to_json()


@rl_bp.post("/feedback-metrics")
def calc_feedback_metrics():
    payload = request.get_json(silent=True) or {}
    feedback_types = payload.get("feedback_types", [])
    if not isinstance(feedback_types, list):
        return ResponseMessage(400, "feedback_types must be list[str]", None).to_json(), 400
    metrics = feedback_metrics([str(x) for x in feedback_types])
    return ResponseMessage(200, "success", metrics).to_json()


@rl_bp.post("/evaluate")
def evaluate_predictions():
    payload = request.get_json(silent=True) or {}
    predictions = payload.get("predictions", [])
    references = payload.get("references", [])
    if not isinstance(predictions, list) or not isinstance(references, list):
        return ResponseMessage(400, "predictions/references must be list[str]", None).to_json(), 400
    result = evaluate_model(
        predictions=[str(x) for x in predictions],
        references=[str(x) for x in references],
    )
    return ResponseMessage(200, "success", result).to_json()
