from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List

from agent.agent_backend.llm.client import LLMClient
from agent.agent_backend.llm.review_output_validation import validate_feedback_analysis, validate_feedback_patch
from agent.agent_backend.prompts.prompt_envelope_builder import PromptEnvelopeBuilder
from agent.agent_backend.prompts.template_manager import PromptTemplateManager


class FeedbackAgent:
    """Unified runtime agent for feedback analysis and patch proposal."""

    TAXONOMY = {
        "query_miss",
        "retrieval_scope_error",
        "retrieval_ranking_error",
        "historical_experience_missing",
        "knowledge_understanding_error",
        "rule_mapping_error",
        "section_fact_extraction_error",
        "focus_point_miss",
        "task_question_error",
        "reasoning_chain_error",
        "evidence_interpretation_error",
        "over_inference",
        "under_identification",
        "wrong_severity",
        "wording_not_actionable",
        "missing_regulatory_basis",
        "unhelpful_question_to_applicant",
    }
    PATCH_TYPES = {"understanding_patch", "query_patch", "reasoning_patch", "wording_patch"}
    TARGET_AGENTS = {"planner", "retrieval_evaluator", "task_question", "reviewer", "feedback_analyzer", "feedback_optimizer"}

    def __init__(self) -> None:
        self.llm = LLMClient()
        self.envelopes = PromptEnvelopeBuilder()
        self.prompts = PromptTemplateManager("pre_review_agent_prompt")

    def describe(self) -> Dict[str, Any]:
        return {
            "name": "feedback_agent",
            "modes": ["analyze_feedback", "propose_patch"],
            "inputs": [
                "task_id",
                "section_id",
                "section_name",
                "raw_text",
                "focus_points",
                "section_rules",
                "system_output",
                "feedback_signals",
                "issue_feedback",
                "missing_item_feedback",
                "reference_example",
                "reference_examples",
                "reference_inputs",
                "trace_payload",
                "historical_experience",
                "feedback_analysis_result",
                "current_templates",
                "current_prompt_rules",
                "retrieved_materials",
            ],
            "outputs": [
                "analysis_result",
                "patch_result",
                "llm_execution",
                "contract_diagnostics",
                "error_message",
            ],
        }

    @staticmethod
    def _safe_list(value: Any) -> List[str]:
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
        return out

    @staticmethod
    def _field_present(value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str):
            return bool(str(value or "").strip())
        if isinstance(value, (list, dict, tuple, set)):
            return bool(value)
        return True

    @classmethod
    def _build_contract_diagnostics(
        cls,
        payload: Dict[str, Any],
        *,
        required_fields: List[str],
        important_fields: List[str],
    ) -> Dict[str, Any]:
        field_presence: Dict[str, bool] = {}
        missing_required_fields: List[str] = []
        missing_important_fields: List[str] = []
        all_fields = list(required_fields) + [item for item in important_fields if item not in required_fields]
        for field_name in all_fields:
            present = cls._field_present(payload.get(field_name))
            field_presence[field_name] = present
            if field_name in required_fields and not present:
                missing_required_fields.append(field_name)
            if field_name in important_fields and not present:
                missing_important_fields.append(field_name)
        return {
            "required_fields": list(required_fields),
            "important_fields": list(important_fields),
            "field_presence": field_presence,
            "missing_required_fields": missing_required_fields,
            "missing_important_fields": missing_important_fields,
        }

    @staticmethod
    def _compact_text(text: Any, max_len: int = 160) -> str:
        value = str(text or "").strip()
        if not value:
            return ""
        return value if len(value) <= max_len else value[: max_len - 3].rstrip() + "..."

    def _build_llm_execution_meta(
        self,
        *,
        agent_name: str,
        raw_text: Any,
        parsed_payload: Any,
        contract_diagnostics: Dict[str, Any] | None = None,
        skipped_reason: str = "",
        error_message: str = "",
    ) -> Dict[str, Any]:
        contract = contract_diagnostics if isinstance(contract_diagnostics, dict) else {}
        missing_required_fields = contract.get("missing_required_fields", []) if isinstance(contract.get("missing_required_fields", []), list) else []
        missing_important_fields = contract.get("missing_important_fields", []) if isinstance(contract.get("missing_important_fields", []), list) else []
        used_default_fallback = bool(skipped_reason) or not isinstance(parsed_payload, dict)
        failure_reason = str(skipped_reason or "").strip()
        if not failure_reason and used_default_fallback:
            failure_reason = "empty_response" if not str(raw_text or "").strip() else "json_parse_failed_or_non_object"
        return {
            "agent": agent_name,
            "used_default_fallback": used_default_fallback,
            "used_model_output": not used_default_fallback,
            "json_parse_ok": isinstance(parsed_payload, dict),
            "failure_reason": failure_reason,
            "missing_required_fields": missing_required_fields,
            "missing_important_fields": missing_important_fields,
            "error_message": str(error_message or "").strip(),
            "raw_preview": "",
            "output_chars": len(str(raw_text or "")),
        }

    @staticmethod
    def _build_suffix_from_patches(patches: List[Dict[str, Any]], target_agent: str) -> str:
        relevant = [item for item in patches if isinstance(item, dict) and str(item.get("target_agent", "") or "").strip() == target_agent]
        if not relevant:
            return ""
        lines = ["动态补丁规则:"]
        for index, patch in enumerate(relevant, start=1):
            trigger = str(patch.get("trigger_condition", "") or "").strip()
            content = str(patch.get("patch_content", "") or "").strip()
            if not content:
                continue
            line = f"{index}. {content}"
            if trigger:
                line += f" 触发条件: {trigger}"
            lines.append(line)
        return "\n".join(lines).strip()

    @staticmethod
    def _normalize_target_agent(value: Any) -> str:
        text = str(value or "").strip()
        if text == "pre_review":
            return "reviewer"
        return text

    def _sanitize_error_types(self, value: Any) -> List[str]:
        return [item for item in self._safe_list(value) if item in self.TAXONOMY]

    @staticmethod
    def _ensure_prompt_context_defaults(payload: Dict[str, Any]) -> Dict[str, Any]:
        base = dict(payload or {})
        defaults = {
            "task_id": "",
            "section_id": "",
            "section_name": "",
            "decision": "",
            "feedback_text": "",
            "labels": [],
            "feedback_signals": {},
            "issue_feedback": [],
            "missing_item_feedback": {},
            "focus_points": [],
            "section_rules": [],
            "system_output": {},
            "reference_example": {},
            "reference_examples": [],
            "reference_inputs": {},
            "trace_payload": {},
            "historical_experience": [],
            "feedback_analysis_result": {},
            "current_templates": {},
            "current_prompt_rules": {},
            "retrieved_materials": [],
        }
        for key, default_value in defaults.items():
            if key not in base or base.get(key) is None:
                base[key] = default_value
        return base

    def _summarize_issue_feedback(self, values: Any, limit: int = 6) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        if not isinstance(values, list):
            return out
        for item in values:
            if not isinstance(item, dict):
                continue
            field_feedback = item.get("field_feedback", {}) if isinstance(item.get("field_feedback", {}), dict) else {}
            errors = [field_name for field_name, verdict in field_feedback.items() if str(verdict or "").strip().lower() == "incorrect"]
            out.append(
                {
                    "task_code": str(item.get("task_code", "") or item.get("issue_key", "") or "").strip(),
                    "task_question": self._compact_text(str(item.get("task_question", "") or ""), max_len=120),
                    "verdict": str(item.get("verdict", "") or "").strip().lower(),
                    "task_status": str(item.get("task_status", "") or "").strip().lower(),
                    "incorrect_fields": errors,
                    "feedback_text": self._compact_text(str(item.get("feedback_text", "") or ""), max_len=140),
                    "error_reason": self._compact_text(str(item.get("error_reason", "") or ""), max_len=140),
                    "rule_text": self._compact_text(str(item.get("rule_text", "") or item.get("basis", "") or ""), max_len=120),
                    "evidence_support": self._compact_text(str(item.get("evidence_support", "") or ""), max_len=120),
                }
            )
            if len(out) >= limit:
                break
        return out

    def _summarize_signal_summary(self, value: Any) -> Dict[str, Any]:
        summary = value if isinstance(value, dict) else {}
        field_error_counts = summary.get("field_error_counts", {}) if isinstance(summary.get("field_error_counts", {}), dict) else {}
        return {
            "incorrect_item_count": int(summary.get("incorrect_item_count", 0) or 0),
            "incorrect_task_codes": self._safe_list(summary.get("incorrect_task_codes", []))[:8],
            "field_error_counts": {
                "rule": int(field_error_counts.get("rule", 0) or 0),
                "fact": int(field_error_counts.get("fact", 0) or 0),
                "evidence": int(field_error_counts.get("evidence", 0) or 0),
                "reasoning": int(field_error_counts.get("reasoning", 0) or 0),
                "conclusion": int(field_error_counts.get("conclusion", 0) or 0),
            },
            "missing_items": self._safe_list(summary.get("missing_items", []))[:6],
            "rule_issues": self._safe_list(summary.get("rule_issues", []))[:4],
            "fact_issues": self._safe_list(summary.get("fact_issues", []))[:4],
            "evidence_issues": self._safe_list(summary.get("evidence_issues", []))[:4],
            "reasoning_issues": self._safe_list(summary.get("reasoning_issues", []))[:4],
            "conclusion_issues": self._safe_list(summary.get("conclusion_issues", []))[:4],
        }

    def _summarize_system_output(self, value: Any) -> Dict[str, Any]:
        data = value if isinstance(value, dict) else {}
        task_verdicts = data.get("task_verdicts", []) if isinstance(data.get("task_verdicts", []), list) else []
        summarized_task_verdicts: List[Dict[str, str]] = []
        for item in task_verdicts[:6]:
            if not isinstance(item, dict):
                continue
            summarized_task_verdicts.append(
                {
                    "task_code": str(item.get("task_code", "") or "").strip(),
                    "status": str(item.get("status", "") or "").strip().lower(),
                    "problem": self._compact_text(str(item.get("problem", "") or ""), max_len=120),
                    "basis": self._compact_text(str(item.get("basis", "") or ""), max_len=100),
                }
            )
        return {
            "pre_review_conclusion": str(data.get("pre_review_conclusion", "") or "").strip().lower(),
            "task_verdicts": summarized_task_verdicts,
            "missing_points": self._safe_list(data.get("missing_points", []))[:4],
            "unsupported_points": self._safe_list(data.get("unsupported_points", []))[:4],
            "risk_points": self._safe_list(data.get("risk_points", []))[:4],
        }

    def _summarize_retrieved_materials(self, values: Any, limit: int = 6) -> List[Dict[str, str]]:
        out: List[Dict[str, str]] = []
        if not isinstance(values, list):
            return out
        for item in values:
            if not isinstance(item, dict):
                continue
            out.append(
                {
                    "evidence_id": str(item.get("evidence_id", "") or "").strip(),
                    "source_type": str(item.get("source_type", "") or "").strip(),
                    "title": self._compact_text(str(item.get("title", "") or item.get("file_name", "") or ""), max_len=72),
                    "content": self._compact_text(str(item.get("content", "") or ""), max_len=120),
                }
            )
            if len(out) >= limit:
                break
        return out

    def _summarize_reference_examples(self, values: Any, limit: int = 2) -> List[Dict[str, str]]:
        out: List[Dict[str, str]] = []
        if not isinstance(values, list):
            return out
        for item in values:
            if not isinstance(item, dict):
                continue
            out.append(
                {
                    "title": self._compact_text(str(item.get("title", "") or item.get("reference_title", "") or item.get("task_question", "") or ""), max_len=80),
                    "example": self._compact_text(str(item.get("content", "") or item.get("reference_content", "") or item.get("revised_output", "") or ""), max_len=140),
                }
            )
            if len(out) >= limit:
                break
        return out

    def _summarize_experience(self, values: Any, limit: int = 4) -> List[str]:
        out: List[str] = []
        if not isinstance(values, list):
            return out
        for item in values:
            if isinstance(item, dict):
                text = str(item.get("content", "") or item.get("summary", "") or item.get("experience", "") or "").strip()
            else:
                text = str(item or "").strip()
            compact = self._compact_text(text, max_len=140)
            if compact and compact not in out:
                out.append(compact)
            if len(out) >= limit:
                break
        return out

    def _build_analysis_prompt_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        feedback_signals = payload.get("feedback_signals", {}) if isinstance(payload.get("feedback_signals", {}), dict) else {}
        reference_inputs = payload.get("reference_inputs", {}) if isinstance(payload.get("reference_inputs", {}), dict) else {}
        return {
            "task_id": str(payload.get("task_id", "") or "").strip(),
            "section_id": str(payload.get("section_id", "") or "").strip(),
            "section_name": str(payload.get("section_name", "") or "").strip(),
            "decision": str(payload.get("decision", "") or "").strip().lower(),
            "feedback_text": self._compact_text(str(payload.get("feedback_text", "") or ""), max_len=160),
            "labels": self._safe_list(payload.get("labels", []))[:8],
            "signal_summary": self._summarize_signal_summary(feedback_signals.get("signal_summary", {})),
            "issue_feedback": self._summarize_issue_feedback(payload.get("issue_feedback", []), limit=6),
            "missing_item_feedback": {
                "text": self._compact_text(str(payload.get("missing_item_feedback", {}).get("text", "") or ""), max_len=140)
                if isinstance(payload.get("missing_item_feedback", {}), dict)
                else "",
                "reason": self._compact_text(str(payload.get("missing_item_feedback", {}).get("reason", "") or ""), max_len=160)
                if isinstance(payload.get("missing_item_feedback", {}), dict)
                else "",
            },
            "system_output": self._summarize_system_output(payload.get("system_output", {})),
            "section_rules": self._safe_list(payload.get("section_rules", []))[:6],
            "focus_points": self._safe_list(payload.get("focus_points", []))[:6],
            "retrieved_materials": self._summarize_retrieved_materials(reference_inputs.get("retrieved_materials", []), limit=6),
            "historical_experience": self._summarize_experience(payload.get("historical_experience", []), limit=4),
        }

    def _build_patch_prompt_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        feedback_signals = payload.get("feedback_signals", {}) if isinstance(payload.get("feedback_signals", {}), dict) else {}
        analysis_result = payload.get("feedback_analysis_result", {}) if isinstance(payload.get("feedback_analysis_result", {}), dict) else {}
        return {
            "task_id": str(payload.get("task_id", "") or "").strip(),
            "section_id": str(payload.get("section_id", "") or "").strip(),
            "section_name": str(payload.get("section_name", "") or "").strip(),
            "feedback_analysis_result": {
                "primary_error_type": str(analysis_result.get("primary_error_type", "") or "").strip(),
                "error_types": self._safe_list(analysis_result.get("error_types", []))[:6],
                "root_cause": self._compact_text(str(analysis_result.get("root_cause", "") or ""), max_len=180),
                "attention_points_next_time": self._safe_list(analysis_result.get("attention_points_next_time", []))[:6],
                "retrieval_missed": self._safe_list(analysis_result.get("retrieval_missed", []))[:6],
            },
            "signal_summary": self._summarize_signal_summary(feedback_signals.get("signal_summary", {})),
            "issue_feedback": self._summarize_issue_feedback(payload.get("issue_feedback", []), limit=6),
            "missing_item_feedback": {
                "text": self._compact_text(str(payload.get("missing_item_feedback", {}).get("text", "") or ""), max_len=140)
                if isinstance(payload.get("missing_item_feedback", {}), dict)
                else "",
                "reason": self._compact_text(str(payload.get("missing_item_feedback", {}).get("reason", "") or ""), max_len=160)
                if isinstance(payload.get("missing_item_feedback", {}), dict)
                else "",
            },
            "section_rules": self._safe_list(payload.get("section_rules", []))[:6],
            "focus_points": self._safe_list(payload.get("focus_points", []))[:6],
            "reference_examples": self._summarize_reference_examples(payload.get("reference_examples", []), limit=2),
            "retrieved_materials": self._summarize_retrieved_materials(payload.get("retrieved_materials", []), limit=6),
            "current_prompt_rules": payload.get("current_prompt_rules", {}) if isinstance(payload.get("current_prompt_rules", {}), dict) else {},
        }

    def _sanitize_experiences(self, experiences: Any, section_id: str, fallback_content: str) -> List[Dict[str, str]]:
        sanitized: List[Dict[str, str]] = []
        if isinstance(experiences, list):
            for item in experiences:
                if not isinstance(item, dict):
                    continue
                exp_type = str(item.get("experience_type", "") or "").strip()
                content = str(item.get("content", "") or "").strip()
                scope = str(item.get("applicable_scope", "") or "").strip() or section_id
                if exp_type and content:
                    sanitized.append(
                        {
                            "experience_type": exp_type,
                            "content": content,
                            "applicable_scope": scope,
                        }
                    )
        if sanitized:
            return sanitized
        if not fallback_content:
            return []
        return [
            {
                "experience_type": "review_rule",
                "content": fallback_content,
                "applicable_scope": section_id,
            }
        ]

    def _fallback_experience_content(self, primary_error: str, root_cause: str, section_id: str) -> str:
        if primary_error == "knowledge_understanding_error":
            return f"章节 {section_id} 在进入检索前，必须先识别审评对象、关键实体、关键数据和任务边界，不能把章节主题直接等同于审评任务。"
        if primary_error == "query_miss":
            return f"章节 {section_id} 生成检索 query 时，必须围绕章节主题、药品名称和关注点生成 3 到 8 条短 query，禁止只用章节号或路径片段检索。"
        if primary_error == "retrieval_scope_error":
            return f"章节 {section_id} 检索时，必须覆盖指导原则、ICH、法律法规、药典数据、历史经验五类来源，并记录缺失来源。"
        if primary_error == "rule_mapping_error":
            return f"章节 {section_id} 必须先把章节规则转成显式审评任务，再进行规则与事实对比，不能只把规则原文拼接进结论。"
        if primary_error == "task_question_error":
            return f"章节 {section_id} 在 reviewer 判断前，必须先提出显式 task_question，说明要判断什么、依据什么判断、还缺什么信息。"
        if primary_error == "reasoning_chain_error":
            return f"章节 {section_id} 输出结论时，必须显式给出规则要求、原文事实、证据支持、比较分析和判断原因，禁止直接跳到结论。"
        if primary_error == "missing_regulatory_basis":
            return f"章节 {section_id} 输出问题或结论时，必须绑定法规或指导原则依据；没有依据时只能标记为信息不足。"
        if primary_error == "wording_not_actionable":
            return f"章节 {section_id} 的 questions 必须包含 issue、basis 和 requested_action，requested_action 要写成申请人可执行动作。"
        if primary_error == "focus_point_miss":
            return f"章节 {section_id} 审评输出必须逐条覆盖 focus_points，不能遗漏已给定的章节关注点。"
        return root_cause or f"章节 {section_id} 需要补充一条可复用的审评规则，避免同类反馈重复出现。"

    def _infer_patch_type(self, error_types: List[str]) -> str:
        if any(item in {"knowledge_understanding_error", "section_fact_extraction_error", "focus_point_miss"} for item in error_types):
            return "understanding_patch"
        if any(item in {"query_miss", "retrieval_scope_error", "retrieval_ranking_error", "historical_experience_missing"} for item in error_types):
            return "query_patch"
        if any(item in {"wording_not_actionable", "missing_regulatory_basis", "unhelpful_question_to_applicant"} for item in error_types):
            return "wording_patch"
        return "reasoning_patch"

    @staticmethod
    def _infer_target_layer(error_types: List[str]) -> str:
        if any(item in {"query_miss", "retrieval_scope_error", "retrieval_ranking_error", "historical_experience_missing"} for item in error_types):
            return "retrieval"
        if any(item in {"rule_mapping_error", "missing_regulatory_basis"} for item in error_types):
            return "rule"
        if any(item in {"knowledge_understanding_error", "section_fact_extraction_error"} for item in error_types):
            return "fact"
        if any(item in {"focus_point_miss", "task_question_error"} for item in error_types):
            return "task"
        if any(item in {"reasoning_chain_error", "evidence_interpretation_error", "over_inference", "under_identification", "wrong_severity"} for item in error_types):
            return "reasoning"
        if any(item in {"wording_not_actionable", "unhelpful_question_to_applicant"} for item in error_types):
            return "wording"
        return "general"

    @staticmethod
    def _infer_primary_knowledge_category(error_types: List[str]) -> str:
        if any(item in {"query_miss", "retrieval_scope_error", "retrieval_ranking_error", "historical_experience_missing"} for item in error_types):
            return "retrieval"
        if any(item in {"rule_mapping_error", "missing_regulatory_basis"} for item in error_types):
            return "rule"
        if any(item in {"knowledge_understanding_error", "section_fact_extraction_error"} for item in error_types):
            return "fact"
        if any(item in {"focus_point_miss", "task_question_error"} for item in error_types):
            return "task"
        if any(item in {"under_identification", "wrong_severity"} for item in error_types):
            return "risk"
        if any(item in {"reasoning_chain_error", "evidence_interpretation_error", "over_inference"} for item in error_types):
            return "reasoning"
        if any(item in {"wording_not_actionable", "unhelpful_question_to_applicant"} for item in error_types):
            return "wording"
        return "generic"

    @staticmethod
    def _infer_patch_scope(patch_type: str, target_agent: str) -> str:
        normalized_patch = str(patch_type or "").strip()
        normalized_target = str(target_agent or "").strip()
        if normalized_patch == "query_patch":
            return "section_retrieval"
        if normalized_patch == "understanding_patch":
            return "section_fact_extraction" if normalized_target == "planner" else "section_general"
        if normalized_patch == "wording_patch":
            return "review_output_style"
        if normalized_target == "task_question":
            return "section_task_design"
        if normalized_target == "reviewer":
            return "section_reasoning"
        return "section_general"

    def _allowed_target_agents(self, patch_type: str) -> set[str]:
        if patch_type == "understanding_patch":
            return {"planner"}
        if patch_type == "query_patch":
            return {"planner", "retrieval_evaluator"}
        if patch_type in {"reasoning_patch", "wording_patch"}:
            return {"task_question", "reviewer"} if patch_type == "reasoning_patch" else {"reviewer"}
        return set(self.TARGET_AGENTS)

    def _fallback_analysis(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        decision = str(payload.get("decision", "") or "").lower()
        labels = {str(x).strip().lower() for x in payload.get("labels", []) or []}
        issue_feedback = payload.get("issue_feedback", []) if isinstance(payload.get("issue_feedback", []), list) else []
        missing_item_feedback = payload.get("missing_item_feedback", {}) if isinstance(payload.get("missing_item_feedback", {}), dict) else {}
        signal_summary = (
            (
                payload.get("feedback_signals", {})
                if isinstance(payload.get("feedback_signals", {}), dict)
                else {}
            ).get("signal_summary", {})
            if isinstance(
                (
                    payload.get("feedback_signals", {})
                    if isinstance(payload.get("feedback_signals", {}), dict)
                    else {}
                ).get("signal_summary", {}),
                dict,
            )
            else {}
        )
        field_error_counts = signal_summary.get("field_error_counts", {}) if isinstance(signal_summary.get("field_error_counts", {}), dict) else {}
        reference_inputs = payload.get("reference_inputs", {}) if isinstance(payload.get("reference_inputs", {}), dict) else {}
        retrieved_materials = reference_inputs.get("retrieved_materials", []) if isinstance(reference_inputs.get("retrieved_materials", []), list) else []
        polarity = "positive"
        error_types: List[str] = []
        if int(field_error_counts.get("rule", 0) or 0) > 0:
            polarity = "negative"
            error_types = ["rule_mapping_error"]
        elif int(field_error_counts.get("fact", 0) or 0) > 0:
            polarity = "negative"
            error_types = ["section_fact_extraction_error"]
        elif int(field_error_counts.get("reasoning", 0) or 0) > 0 or int(field_error_counts.get("conclusion", 0) or 0) > 0:
            polarity = "negative"
            error_types = ["reasoning_chain_error"]
        elif int(field_error_counts.get("evidence", 0) or 0) > 0:
            polarity = "negative"
            error_types = ["query_miss" if not retrieved_materials else "evidence_interpretation_error"]
        elif str(missing_item_feedback.get("text", "") or "").strip() or str(missing_item_feedback.get("reason", "") or "").strip():
            polarity = "partial"
            error_types = ["focus_point_miss" if self._safe_list(payload.get("focus_points", [])) else "under_identification"]
        if decision in {"false_positive", "rejected"}:
            polarity = "negative"
            error_types = ["over_inference"]
        elif decision in {"missed", "missing_risk"}:
            polarity = "negative"
            error_types = ["under_identification"]
            if not retrieved_materials:
                error_types = ["query_miss"]
        elif decision:
            polarity = "partial"
            error_types = ["focus_point_miss"]
        if "retrieval_miss" in labels:
            error_types = ["query_miss"]
        elif "wrong_reference" in labels:
            error_types = ["missing_regulatory_basis"]
        elif "style_issue" in labels:
            error_types = ["wording_not_actionable"]
        elif "reasoning_error" in labels:
            error_types = ["evidence_interpretation_error"]
        elif any(isinstance(item, dict) and str(item.get("feedback_kind", "") or "").strip().lower() == "missing_item" for item in issue_feedback):
            polarity = "partial"
            error_types = ["under_identification"]
        return {
            "section_id": str(payload.get("section_id", "") or ""),
            "feedback_polarity": polarity,
            "error_types": error_types,
            "primary_error_type": error_types[0] if error_types else "",
            "target_layer": self._infer_target_layer(error_types),
            "knowledge_category": self._infer_primary_knowledge_category(error_types),
            "root_cause": str(payload.get("feedback_text", "") or "需要根据专家反馈修正章节预审策略。"),
            "attention_points_next_time": self._safe_list(payload.get("focus_points", []))[:5],
            "retrieval_missed": [],
            "new_experience": [],
            "uncertainty_note": "",
        }

    def analyze_feedback(self, payload: Dict[str, Any], prompt_config: Dict[str, Any] | None = None) -> Dict[str, Any]:
        normalized_payload = self._ensure_prompt_context_defaults(payload)
        default_data = self._fallback_analysis(normalized_payload)
        contract_diagnostics = self._build_contract_diagnostics(
            normalized_payload,
            required_fields=["task_id", "section_id"],
            important_fields=[
                "raw_text",
                "system_output",
                "feedback_signals",
                "issue_feedback",
                "missing_item_feedback",
                "section_rules",
                "trace_payload",
                "historical_experience",
                "reference_inputs",
            ],
        )
        if contract_diagnostics["missing_required_fields"]:
            error_message = f"feedback_analyzer missing required fields: {', '.join(contract_diagnostics['missing_required_fields'])}"
            default_data["llm_execution"] = self._build_llm_execution_meta(
                agent_name="feedback_analyzer",
                raw_text="",
                parsed_payload=None,
                contract_diagnostics=contract_diagnostics,
                skipped_reason="missing_required_fields",
                error_message=error_message,
            )
            default_data["contract_diagnostics"] = contract_diagnostics
            default_data["error_message"] = error_message
            return default_data
        prompt_payload = self._build_analysis_prompt_payload(normalized_payload)
        prompt = self.prompts.render("feedback_analyzer.j2", prompt_payload, prompt_config=prompt_config or {})
        raw = self.llm.chat(
            messages=self.envelopes.build("feedback_analyzer", prompt),
            default=json.dumps(default_data, ensure_ascii=False),
        )
        parsed = self.llm.extract_json(raw)
        validate_feedback_analysis(parsed, self.TAXONOMY)
        llm_execution = self._build_llm_execution_meta(
            agent_name="feedback_analyzer",
            raw_text=raw,
            parsed_payload=parsed,
            contract_diagnostics=contract_diagnostics,
        )
        data = parsed if isinstance(parsed, dict) else default_data
        error_parts: List[str] = []
        if llm_execution.get("used_default_fallback", False):
            error_parts.append(f"feedback_analyzer fallback: {str(llm_execution.get('failure_reason', '') or '').strip()}")
        if contract_diagnostics["missing_important_fields"]:
            error_parts.append(f"missing important fields: {', '.join(contract_diagnostics['missing_important_fields'])}")
        data["section_id"] = str(data.get("section_id", normalized_payload.get("section_id", "")) or "")
        polarity = str(data.get("feedback_polarity", default_data["feedback_polarity"]) or "").strip()
        if polarity not in {"positive", "negative", "partial"}:
            polarity = default_data["feedback_polarity"]
        data["feedback_polarity"] = polarity
        data["error_types"] = self._sanitize_error_types(data.get("error_types", []))
        primary_error_type = str(data.get("primary_error_type", data["error_types"][0] if data["error_types"] else "") or "").strip()
        if primary_error_type and primary_error_type not in self.TAXONOMY:
            primary_error_type = data["error_types"][0] if data["error_types"] else ""
        data["primary_error_type"] = primary_error_type
        data["target_layer"] = str(data.get("target_layer", "") or self._infer_target_layer(data["error_types"]) or "").strip()
        data["knowledge_category"] = str(data.get("knowledge_category", "") or self._infer_primary_knowledge_category(data["error_types"]) or "").strip()
        data["attention_points_next_time"] = self._safe_list(data.get("attention_points_next_time", []))
        data["retrieval_missed"] = self._safe_list(data.get("retrieval_missed", []))
        data["new_experience"] = self._sanitize_experiences(
            data.get("new_experience", []),
            section_id=data["section_id"],
            fallback_content="",
        )
        data["uncertainty_note"] = str(data.get("uncertainty_note", "") or "").strip()
        data["root_cause"] = str(data.get("root_cause", default_data["root_cause"]) or "").strip()
        data["llm_execution"] = llm_execution
        data["contract_diagnostics"] = contract_diagnostics
        data["error_message"] = " | ".join([part for part in error_parts if part]).strip()
        return data

    def _fallback_patch(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        section_id = str(payload.get("section_id", "") or "")
        analysis = payload.get("feedback_analysis_result", {}) if isinstance(payload.get("feedback_analysis_result", {}), dict) else {}
        error_types = self._safe_list(analysis.get("error_types", []))
        patch_type = self._infer_patch_type(error_types)
        if patch_type == "understanding_patch":
            target_agent = "planner"
        elif patch_type == "query_patch":
            target_agent = "retrieval_evaluator" if "retrieval_" in " ".join(error_types) else "planner"
        elif any(item in {"task_question_error", "rule_mapping_error", "reasoning_chain_error"} for item in error_types):
            target_agent = "task_question"
        else:
            target_agent = "reviewer"
        patch_content = str(analysis.get("root_cause", "") or payload.get("user_feedback_text", "") or "需要增加一条最小规则补丁。").strip()
        patches = [
            {
                "patch_id": f"patch_{uuid.uuid4().hex[:12]}",
                "patch_type": patch_type,
                "target_agent": target_agent,
                "target_layer": self._infer_target_layer(error_types),
                "knowledge_category": self._infer_primary_knowledge_category(error_types),
                "target_scope": section_id,
                "patch_scope": self._infer_patch_scope(patch_type, target_agent),
                "trigger_condition": f"section_id == '{section_id}'",
                "patch_content": patch_content,
                "status": "candidate",
            }
        ]
        planner_suffix = self._build_suffix_from_patches(patches, "planner")
        retrieval_suffix = self._build_suffix_from_patches(patches, "retrieval_evaluator")
        task_question_suffix = self._build_suffix_from_patches(patches, "task_question")
        review_suffix = self._build_suffix_from_patches(patches, "reviewer")
        return {
            "section_id": section_id,
            "target_layer": self._infer_target_layer(error_types),
            "knowledge_category": self._infer_primary_knowledge_category(error_types),
            "patches": patches,
            "candidate_templates": {
                "planner_prompt_candidate": f"{str(payload.get('current_templates', {}).get('planner_prompt', '') or '').strip()}\n\n[DynamicPromptPatch]\n{planner_suffix}".strip() if planner_suffix else str(payload.get("current_templates", {}).get("planner_prompt", "") or ""),
                "retrieval_evaluator_prompt_candidate": f"{str(payload.get('current_templates', {}).get('retrieval_evaluator_prompt', '') or '').strip()}\n\n[DynamicPromptPatch]\n{retrieval_suffix}".strip() if retrieval_suffix else str(payload.get("current_templates", {}).get("retrieval_evaluator_prompt", "") or ""),
                "task_question_prompt_candidate": f"{str(payload.get('current_templates', {}).get('task_question_prompt', '') or '').strip()}\n\n[DynamicPromptPatch]\n{task_question_suffix}".strip() if task_question_suffix else str(payload.get("current_templates", {}).get("task_question_prompt", "") or ""),
                "pre_review_prompt_candidate": f"{str(payload.get('current_templates', {}).get('pre_review_prompt', '') or '').strip()}\n\n[DynamicPromptPatch]\n{review_suffix}".strip() if review_suffix else str(payload.get("current_templates", {}).get("pre_review_prompt", "") or ""),
                "feedback_analyzer_prompt_candidate": str(payload.get("current_templates", {}).get("feedback_analyzer_prompt", "") or ""),
                "feedback_optimizer_prompt_candidate": str(payload.get("current_templates", {}).get("feedback_optimizer_prompt", "") or ""),
            },
            "applicable_conditions": [section_id],
        }

    def propose_patch(self, payload: Dict[str, Any], prompt_config: Dict[str, Any] | None = None) -> Dict[str, Any]:
        normalized_payload = self._ensure_prompt_context_defaults(payload)
        analysis = normalized_payload.get("feedback_analysis_result", {}) if isinstance(normalized_payload.get("feedback_analysis_result", {}), dict) else {}
        default_data = self._fallback_patch(normalized_payload)
        contract_diagnostics = self._build_contract_diagnostics(
            normalized_payload,
            required_fields=["task_id", "section_id", "feedback_analysis_result"],
            important_fields=[
                "raw_text",
                "feedback_signals",
                "current_templates",
                "current_prompt_rules",
                "retrieved_materials",
                "reference_examples",
                "reference_example",
            ],
        )
        if contract_diagnostics["missing_required_fields"]:
            error_message = f"feedback_optimizer missing required fields: {', '.join(contract_diagnostics['missing_required_fields'])}"
            default_data["llm_execution"] = self._build_llm_execution_meta(
                agent_name="feedback_optimizer",
                raw_text="",
                parsed_payload=None,
                contract_diagnostics=contract_diagnostics,
                skipped_reason="missing_required_fields",
                error_message=error_message,
            )
            default_data["contract_diagnostics"] = contract_diagnostics
            default_data["error_message"] = error_message
            return default_data
        prompt_payload = self._build_patch_prompt_payload(normalized_payload)
        prompt = self.prompts.render("feedback_optimizer.j2", prompt_payload, prompt_config=prompt_config or {})
        raw = self.llm.chat(
            messages=self.envelopes.build("feedback_optimizer", prompt),
            default=json.dumps(default_data, ensure_ascii=False),
        )
        parsed = self.llm.extract_json(raw)
        validate_feedback_patch(parsed, self.PATCH_TYPES, self._allowed_target_agents, self._normalize_target_agent)
        llm_execution = self._build_llm_execution_meta(
            agent_name="feedback_optimizer",
            raw_text=raw,
            parsed_payload=parsed,
            contract_diagnostics=contract_diagnostics,
        )
        data = parsed if isinstance(parsed, dict) else default_data
        error_parts: List[str] = []
        if llm_execution.get("used_default_fallback", False):
            error_parts.append(f"feedback_optimizer fallback: {str(llm_execution.get('failure_reason', '') or '').strip()}")
        if contract_diagnostics["missing_important_fields"]:
            error_parts.append(f"missing important fields: {', '.join(contract_diagnostics['missing_important_fields'])}")
        patches = []
        if isinstance(data.get("patches", []), list):
            for item in data.get("patches", []):
                if not isinstance(item, dict):
                    continue
                patch_type = str(item.get("patch_type", "") or "").strip()
                target_agent = self._normalize_target_agent(item.get("target_agent", ""))
                patch_content = str(item.get("patch_content", "") or "").strip()
                if patch_type not in self.PATCH_TYPES or not patch_content:
                    continue
                allowed_targets = self._allowed_target_agents(patch_type)
                if target_agent not in allowed_targets:
                    target_agent = next(iter(sorted(allowed_targets))) if allowed_targets else "reviewer"
                patches.append(
                    {
                        "patch_id": str(item.get("patch_id", "") or f"patch_{uuid.uuid4().hex[:12]}"),
                        "patch_type": patch_type,
                        "target_agent": target_agent,
                        "target_layer": str(item.get("target_layer", "") or self._infer_target_layer(self._safe_list(analysis.get("error_types", [])))).strip(),
                        "knowledge_category": str(item.get("knowledge_category", "") or self._infer_primary_knowledge_category(self._safe_list(analysis.get("error_types", [])))).strip(),
                        "target_scope": str(item.get("target_scope", data.get("section_id", "")) or data.get("section_id", "")),
                        "patch_scope": str(item.get("patch_scope", "") or self._infer_patch_scope(patch_type, target_agent)).strip(),
                        "trigger_condition": str(item.get("trigger_condition", "") or ""),
                        "patch_content": patch_content,
                        "status": str(item.get("status", "candidate") or "candidate"),
                    }
                )
        data["patches"] = patches
        analysis_error_types = self._safe_list(analysis.get("error_types", []))
        data["target_layer"] = str(data.get("target_layer", "") or self._infer_target_layer(analysis_error_types)).strip()
        data["knowledge_category"] = str(data.get("knowledge_category", "") or self._infer_primary_knowledge_category(analysis_error_types)).strip()
        planner_suffix = self._build_suffix_from_patches(patches, "planner")
        retrieval_suffix = self._build_suffix_from_patches(patches, "retrieval_evaluator")
        task_question_suffix = self._build_suffix_from_patches(patches, "task_question")
        review_suffix = self._build_suffix_from_patches(patches, "reviewer")
        candidates = data.get("candidate_templates", {})
        if not isinstance(candidates, dict):
            candidates = default_data["candidate_templates"]
        planner_base = str(candidates.get("planner_prompt_candidate", "") or "").strip() or str(normalized_payload.get("current_templates", {}).get("planner_prompt", "") or "").strip()
        retrieval_base = str(candidates.get("retrieval_evaluator_prompt_candidate", "") or "").strip() or str(normalized_payload.get("current_templates", {}).get("retrieval_evaluator_prompt", "") or "").strip()
        task_question_base = str(candidates.get("task_question_prompt_candidate", "") or "").strip() or str(normalized_payload.get("current_templates", {}).get("task_question_prompt", "") or "").strip()
        review_base = str(candidates.get("pre_review_prompt_candidate", "") or "").strip() or str(normalized_payload.get("current_templates", {}).get("pre_review_prompt", "") or "").strip()
        data["candidate_templates"] = {
            "planner_prompt_candidate": planner_base if "[DynamicPromptPatch]" in planner_base or not planner_suffix else f"{planner_base}\n\n[DynamicPromptPatch]\n{planner_suffix}".strip(),
            "retrieval_evaluator_prompt_candidate": retrieval_base if "[DynamicPromptPatch]" in retrieval_base or not retrieval_suffix else f"{retrieval_base}\n\n[DynamicPromptPatch]\n{retrieval_suffix}".strip(),
            "task_question_prompt_candidate": task_question_base if "[DynamicPromptPatch]" in task_question_base or not task_question_suffix else f"{task_question_base}\n\n[DynamicPromptPatch]\n{task_question_suffix}".strip(),
            "pre_review_prompt_candidate": review_base if "[DynamicPromptPatch]" in review_base or not review_suffix else f"{review_base}\n\n[DynamicPromptPatch]\n{review_suffix}".strip(),
            "feedback_analyzer_prompt_candidate": str(candidates.get("feedback_analyzer_prompt_candidate", "") or normalized_payload.get("current_templates", {}).get("feedback_analyzer_prompt", "") or "").strip(),
            "feedback_optimizer_prompt_candidate": str(candidates.get("feedback_optimizer_prompt_candidate", "") or normalized_payload.get("current_templates", {}).get("feedback_optimizer_prompt", "") or "").strip(),
        }
        data["applicable_conditions"] = self._safe_list(data.get("applicable_conditions", []))
        data["section_id"] = str(data.get("section_id", normalized_payload.get("section_id", "")) or "")
        data["llm_execution"] = llm_execution
        data["contract_diagnostics"] = contract_diagnostics
        data["error_message"] = " | ".join([part for part in error_parts if part]).strip()
        return data




