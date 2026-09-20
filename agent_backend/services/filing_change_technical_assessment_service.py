from typing import Any, Dict, List


class FilingChangeTechnicalAssessmentService:
    """TechnicalAssessmentAgent：变更识别、类别建议、质量与稳定性分析。"""

    def __init__(self, quality_service: Any, stability_service: Any) -> None:
        self.quality_service = quality_service
        self.stability_service = stability_service

    def run(
        self,
        form_json: Dict[str, Any],
        submissions: List[Dict[str, Any]],
        rules: List[Dict[str, Any]],
        project_id: str = "",
    ) -> Dict[str, Any]:
        matters = ((form_json.get("item_5_application_matter_category", {}) or {}).get("selected_values", [])) if isinstance(form_json, dict) else []
        has_17 = "1.7" in [str(x) for x in matters]
        def execute(call):
            try:
                value = call()
                return value if isinstance(value, dict) and value else {'result': '证据不足', 'reason': '模块返回空结果'}
            except Exception as exc:
                return {'execution_status': 'failed', 'execution_error': str(exc), 'result': '执行失败'}
        quality = execute(lambda: self.quality_service.run(submissions or []))
        stability = execute(lambda: self.stability_service.run({'form_json': form_json}, submissions or [], project_id=project_id))
        matched_rules = []
        suggested = '备案类' if has_17 else '需人工确认'
        reason = '申请表选择1.7变更有效期和贮藏条件事项；事项类别不代表技术资料满足要求。' if has_17 else '申请表未明确识别1.7事项，需核对事项类别及依据。'

        tech_risks = []
        tech_risks.extend(stability.get("missing_data", []) if isinstance(stability, dict) else [])
        tech_risks.extend(stability.get("risk_points", []) if isinstance(stability, dict) else [])
        tech_risks.extend([item.get("review_conclusion", "") for item in matched_rules if str(item.get("review_conclusion", "")).strip()])
        tech_risks = [str(x) for x in tech_risks if str(x or "").strip()]

        return {
            "change_identification": {
                "identified_change": "延长药品有效期" if has_17 else "需人工确认",
                "associated_changes": [],
                "need_manual_review": not has_17 or suggested in {"需人工确认", "需补正后再审"},
            },
            "change_category_suggestion": {
                "suggested_category": suggested,
                "reason": reason,
                "confidence": 0.78 if suggested == "备案类" else 0.62,
                "evidence": [{"source": {"doc_id": "application_form", "field": "item_5_application_matter_category", "subfield": "selected_values"}, "raw_value": matters}],
                "need_manual_review": suggested in {"需人工确认", "需补正后再审"},
            },
            "quality_standard_check": quality,
            "stability_trend_analysis": stability,
            "technical_risk_points": list(dict.fromkeys(tech_risks)),
            "matched_rules": matched_rules,
        }
