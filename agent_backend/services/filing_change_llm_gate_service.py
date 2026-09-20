from typing import Any, Dict, List


class FilingChangeLLMGateService:
    """LLM 调用门控：默认确定性规则优先，仅在必要场景触发。"""

    def should_call_llm(self, scene: str, context: Dict[str, Any]) -> Dict[str, Any]:
        scene_key = str(scene or "").strip()
        reason = ""
        allow = False
        if scene_key in {"category_suggestion", "overall_summary", "correction_notice", "review_report"}:
            allow = True
            reason = "需要生成自然语言综合建议"
        if scene_key == "material_classification" and bool(context.get("low_confidence", False)):
            allow = True
            reason = "资料分类置信度低"
        if scene_key == "field_interpretation" and bool(context.get("low_confidence", False)):
            allow = True
            reason = "字段抽取置信度低"
        if scene_key == "rule_conflict" and bool(context.get("has_conflict", False)):
            allow = True
            reason = "规则冲突需要解释"
        return {"scene": scene_key, "allow": allow, "reason": reason or "确定性工具可完成"}

    def build_call_record(self, scene: str, decision: Dict[str, Any], input_summary: str, output_summary: str = "") -> Dict[str, Any]:
        return {
            "scene": str(scene or ""),
            "decision": decision,
            "input_summary": str(input_summary or "")[:1000],
            "output_summary": str(output_summary or "")[:2000],
            "prompt_version": "v1",
            "accepted_by_reviewer": None,
        }

