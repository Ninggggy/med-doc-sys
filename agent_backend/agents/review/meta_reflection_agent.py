from __future__ import annotations

import json
from typing import Any, Dict, List

from agent.agent_backend.llm.client import LLMClient
from agent.agent_backend.llm.review_output_validation import validate_reflection
from agent.agent_backend.prompts.prompt_envelope_builder import PromptEnvelopeBuilder
from agent.agent_backend.prompts.template_manager import PromptTemplateManager


class MetaReflectionAgent:
    """Distill reusable experience from one full section review-feedback loop."""

    def __init__(self) -> None:
        self.llm = LLMClient()
        self.envelopes = PromptEnvelopeBuilder()
        self.prompts = PromptTemplateManager("pre_review_agent_prompt")

    @staticmethod
    def _safe_string_list(value: Any, limit: int = 12) -> List[str]:
        if not isinstance(value, list):
            return []
        seen = set()
        out: List[str] = []
        for item in value:
            text = str(item or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            out.append(text)
            if len(out) >= limit:
                break
        return out

    def _fallback(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        section_id = str(payload.get("section_id", "") or "").strip()
        review_output = payload.get("review_output", {}) if isinstance(payload.get("review_output", {}), dict) else {}
        feedback_signals = payload.get("feedback_signals", {}) if isinstance(payload.get("feedback_signals", {}), dict) else {}
        reference_example = payload.get("reference_example", {}) if isinstance(payload.get("reference_example", {}), dict) else {}
        high_frequency_doc_ids = self._safe_string_list(payload.get("high_frequency_doc_ids", []), limit=8)
        feedback_text = str(payload.get("feedback_text", "") or "").strip()
        conclusion = str(review_output.get("pre_review_conclusion", "") or review_output.get("conclusion", "") or "").strip()
        summary = (
            f"章节 {section_id} 在本轮反馈优化后沉淀出一条可复用经验："
            f"后续审评应优先校验结论“{conclusion or '未明确'}”与反馈信号、章节规则和检索证据是否一致。"
        )
        example_output = (
            reference_example.get("expected_output")
            if isinstance(reference_example.get("expected_output"), dict)
            else review_output
        )
        bad_case = str(feedback_signals.get("conclusion_feedback", "") or "").lower() in {"incorrect", "partial"}
        bad_case = bad_case or str(feedback_signals.get("retrieval_feedback", "") or "").lower() in {"incorrect", "partial"}
        return {
            "section_id": section_id,
            "reflection_summary": summary,
            "bad_case": bad_case,
            "high_frequency_doc_ids": high_frequency_doc_ids,
            "distilled_experiences": [
                {
                    "experience_type": "reasoning_knowledge" if bad_case else "meta_reflection",
                    "knowledge_category": "reasoning" if bad_case else "generic",
                    "optimization_target": "reviewer",
                    "source_signal": str(feedback_signals.get("reasoning_feedback", "") or feedback_text or "").strip()[:160],
                    "content": feedback_text or summary,
                    "applicable_scope": section_id,
                }
            ],
            "few_shot_example": {
                "title": str(reference_example.get("title", "") or f"{section_id} 参考示例").strip(),
                "input_snapshot": {
                    "section_id": section_id,
                    "focus_points": payload.get("focus_points", []) if isinstance(payload.get("focus_points", []), list) else [],
                    "section_rules": payload.get("section_rules", []) if isinstance(payload.get("section_rules", []), list) else [],
                },
                "expected_output": example_output if isinstance(example_output, dict) else {},
            },
        }

    @staticmethod
    def _build_llm_execution_meta(raw_text: Any, parsed_payload: Any) -> Dict[str, Any]:
        used_default_fallback = not isinstance(parsed_payload, dict)
        return {
            "agent": "meta_reflector",
            "used_default_fallback": used_default_fallback,
            "used_model_output": not used_default_fallback,
            "json_parse_ok": isinstance(parsed_payload, dict),
            "failure_reason": "" if isinstance(parsed_payload, dict) else ("empty_response" if not str(raw_text or "").strip() else "json_parse_failed_or_non_object"),
            "raw_preview": "",
            "output_chars": len(str(raw_text or "")),
        }

    def reflect(self, payload: Dict[str, Any], prompt_config: Dict[str, Any] | None = None) -> Dict[str, Any]:
        safe_payload = dict(payload if isinstance(payload, dict) else {})
        safe_payload.setdefault("task_id", str(safe_payload.get("section_id", "") or "meta_reflection_task"))
        safe_payload.setdefault("section_id", "")
        safe_payload.setdefault("section_name", "")
        safe_payload.setdefault("review_output", safe_payload.get("review_output", {}) if isinstance(safe_payload.get("review_output", {}), dict) else {})
        safe_payload.setdefault("feedback_signals", safe_payload.get("feedback_signals", {}) if isinstance(safe_payload.get("feedback_signals", {}), dict) else {})
        safe_payload.setdefault("feedback_text", str(safe_payload.get("feedback_text", "") or ""))
        safe_payload.setdefault("analysis_result", safe_payload.get("analysis_result", {}) if isinstance(safe_payload.get("analysis_result", {}), dict) else {})
        safe_payload.setdefault("patch_result", safe_payload.get("patch_result", {}) if isinstance(safe_payload.get("patch_result", {}), dict) else {})
        safe_payload.setdefault("retrieval_materials", safe_payload.get("retrieval_materials", []) if isinstance(safe_payload.get("retrieval_materials", []), list) else [])
        safe_payload.setdefault("high_frequency_doc_ids", safe_payload.get("high_frequency_doc_ids", []) if isinstance(safe_payload.get("high_frequency_doc_ids", []), list) else [])
        safe_payload.setdefault("reference_example", safe_payload.get("reference_example", {}) if isinstance(safe_payload.get("reference_example", {}), dict) else {})
        safe_payload.setdefault("focus_points", safe_payload.get("focus_points", []) if isinstance(safe_payload.get("focus_points", []), list) else [])
        safe_payload.setdefault("section_rules", safe_payload.get("section_rules", []) if isinstance(safe_payload.get("section_rules", []), list) else [])
        default_data = self._fallback(safe_payload)
        prompt = self.prompts.render("meta_reflector.j2", safe_payload, prompt_config=prompt_config or {})
        raw = self.llm.chat(
            messages=self.envelopes.build("meta_reflector", prompt),
            default=json.dumps(default_data, ensure_ascii=False),
        )
        data = self.llm.extract_json(raw)
        validate_reflection(data)
        llm_execution = self._build_llm_execution_meta(raw_text=raw, parsed_payload=data)
        if not isinstance(data, dict):
            data = default_data
        data["section_id"] = str(data.get("section_id", default_data["section_id"]) or "").strip()
        data["reflection_summary"] = str(data.get("reflection_summary", default_data["reflection_summary"]) or "").strip()
        data["bad_case"] = bool(data.get("bad_case", default_data["bad_case"]))
        data["high_frequency_doc_ids"] = self._safe_string_list(
            data.get("high_frequency_doc_ids", default_data["high_frequency_doc_ids"]),
            limit=8,
        )
        distilled = []
        for item in data.get("distilled_experiences", default_data["distilled_experiences"]) or []:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content", "") or "").strip()
            if not content:
                continue
            distilled.append(
                {
                    "experience_type": str(item.get("experience_type", "") or "meta_reflection").strip() or "meta_reflection",
                    "knowledge_category": str(item.get("knowledge_category", "") or "").strip(),
                    "optimization_target": str(item.get("optimization_target", "") or "").strip(),
                    "source_signal": str(item.get("source_signal", "") or "").strip(),
                    "content": content,
                    "applicable_scope": str(item.get("applicable_scope", "") or data["section_id"]).strip() or data["section_id"],
                }
            )
        data["distilled_experiences"] = distilled
        few_shot_example = data.get("few_shot_example", {})
        if not isinstance(few_shot_example, dict):
            few_shot_example = {}
        input_snapshot = few_shot_example.get("input_snapshot", {})
        expected_output = few_shot_example.get("expected_output", {})
        data["few_shot_example"] = {
            "title": str(few_shot_example.get("title", "")).strip(),
            "input_snapshot": input_snapshot if isinstance(input_snapshot, dict) else default_data["few_shot_example"]["input_snapshot"],
            "expected_output": expected_output if isinstance(expected_output, dict) else default_data["few_shot_example"]["expected_output"],
        }
        data["llm_execution"] = llm_execution
        return data
