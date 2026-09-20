from __future__ import annotations

import json
from typing import Any, Dict, List

from agent.agent_backend.llm.client import LLMClient
from agent.agent_backend.llm.review_output_validation import validate_review_output
from agent.agent_backend.prompts.prompt_envelope_builder import PromptEnvelopeBuilder
from agent.agent_backend.prompts.template_manager import PromptTemplateManager


class P52ReviewerAgent:
    """3.2.P.5.2 专用 reviewer。"""

    def __init__(self) -> None:
        self.llm = LLMClient()
        self.envelopes = PromptEnvelopeBuilder()
        self.prompts = PromptTemplateManager("pre_review_agent_prompt")

    @staticmethod
    def _safe_list(value: Any) -> List[str]:
        if not isinstance(value, list):
            return []
        out: List[str] = []
        seen = set()
        for item in value:
            text = str(item or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            out.append(text)
        return out

    @staticmethod
    def _compact_text(text: Any, max_len: int = 220) -> str:
        value = " ".join(str(text or "").strip().split())
        if len(value) <= max_len:
            return value
        return f"{value[:max_len].rstrip()}..."

    @staticmethod
    def _normalize_status(value: Any) -> str:
        text = str(value or "").strip().lower()
        mapping = {
            "supported": "supported",
            "support": "supported",
            "pass": "supported",
            "passed": "supported",
            "通过": "supported",
            "满足": "supported",
            "已满足": "supported",
            "unsupported": "unsupported",
            "fail": "unsupported",
            "failed": "unsupported",
            "不满足": "unsupported",
            "未通过": "unsupported",
            "不通过": "unsupported",
            "insufficient_information": "insufficient_information",
            "insufficient": "insufficient_information",
            "need_more_information": "insufficient_information",
            "uncertain": "insufficient_information",
            "信息不足": "insufficient_information",
            "证据不足": "insufficient_information",
            "待补充": "insufficient_information",
            "待补证": "insufficient_information",
            "暂无法判断": "insufficient_information",
        }
        return mapping.get(text, "")

    @staticmethod
    def _to_rule_item_title(task_code: str, task_question: str, basis: str, problem: str) -> str:
        code = str(task_code or "").strip()
        text = str(task_question or "").strip()
        if text:
            text = text.replace("当前章节是否", "").replace("是否", "").replace("？", "").strip()
        candidate = text or str(basis or "").strip() or str(problem or "").strip() or code
        if not candidate:
            candidate = "规则审评项"
        if code and code not in candidate:
            return f"{code} {candidate}".strip()
        return candidate

    @staticmethod
    def _compose_rule_evidence(
        *,
        rule_code: str,
        rule_text: str,
        requirement_point: str,
        evidence: str,
        location: str,
        issue: str,
        reason: str = "",
    ) -> str:
        rule_name = str(rule_text or "").strip()
        rule_label = ""
        if rule_code and rule_name:
            rule_label = rule_name if rule_name.startswith(rule_code) else f"{rule_code} {rule_name}"
        else:
            rule_label = rule_code or rule_name
        rule_basis = str(requirement_point or "").strip() or rule_label
        non_compliance = str(issue or "").strip() or str(evidence or "").strip()
        inferred_reason = str(reason or "").strip()
        source = str(location or "").strip() or "当前章节"
        parts = [
            f"规则依据：{rule_label or rule_basis}",
            f"规则适用条件：{rule_basis}",
            f"原文不符合点：{non_compliance}",
        ]
        if inferred_reason and inferred_reason != non_compliance:
            parts.append(f"推断原因：{inferred_reason}")
        parts.append(f"定位：{source}")
        return "；".join(parts)

    @staticmethod
    def _entity_label(value: Any) -> str:
        mapping = {
            "sample_preparation": "供试品制备",
            "reference_preparation": "对照溶液制备",
            "reference_standard": "对照品",
            "suitability_reference": "系统适用性/适用性支持",
            "column_name": "色谱柱",
            "mobile_phase": "流动相",
            "medium_name": "溶剂/介质",
            "linked_spec_item": "质量标准项目映射",
            "decision_rule": "结果判定表述",
            "method_type": "方法类型",
            "method_purpose": "方法目的",
            "pharmacopoeia_reference": "药典或通则引用",
            "sample_pretreatment": "供试液前处理",
            "test_scope": "检查范围",
            "culture_media": "培养基",
            "incubation_conditions": "培养条件",
            "enumeration_method": "计数方法",
            "calculation_formula": "计算公式",
            "system_suitability": "系统适用性",
            "wavelength": "检测波长",
            "temperature": "温度",
            "sampling_timepoints": "观察/操作时间",
        }
        text = str(value or "").strip()
        return mapping.get(text, text)

    def _build_llm_execution_meta(
        self,
        *,
        raw_text: Any,
        parsed_payload: Any,
    ) -> Dict[str, Any]:
        used_default_fallback = not isinstance(parsed_payload, dict)
        failure_reason = ""
        if used_default_fallback:
            failure_reason = "empty_response" if not str(raw_text or "").strip() else "json_parse_failed_or_non_object"
        return {
            "agent": "p52_reviewer",
            "used_default_fallback": used_default_fallback,
            "used_model_output": not used_default_fallback,
            "json_parse_ok": isinstance(parsed_payload, dict),
            "failure_reason": failure_reason,
            "raw_preview": "",
            "output_chars": len(str(raw_text or '')),
        }

    def _build_prompt_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        task_definition = payload.get("task_definition", {}) if isinstance(payload.get("task_definition", {}), dict) else {}
        section_review_profile = payload.get("section_review_profile", {}) if isinstance(payload.get("section_review_profile", {}), dict) else {}
        raw_tasks = payload.get("task_questions", []) if isinstance(payload.get("task_questions", []), list) else []
        task_questions: List[Dict[str, Any]] = []
        for item in raw_tasks[:8]:
            if not isinstance(item, dict):
                continue
            task_questions.append(
                {
                    "task_code": str(item.get("task_code", "") or "").strip(),
                    "task_question": str(item.get("task_question", "") or "").strip(),
                    "rule_name": str(item.get("rule_name", "") or item.get("rule_display_text", "") or "").strip(),
                    "problem_item": str(item.get("problem_item", "") or "").strip(),
                    "reasoning": str(item.get("reasoning", "") or "").strip(),
                    "revision_suggestion": str(item.get("revision_suggestion", "") or "").strip(),
                    "review_object": str(item.get("review_object", "") or "").strip(),
                    "judgment_basis": self._safe_list(item.get("judgment_basis", []))[:3],
                    "comparison_axes": self._safe_list(item.get("comparison_axes", []))[:4],
                    "missing_information": self._safe_list(item.get("missing_information", []))[:4],
                }
            )

        approved_materials: List[Dict[str, Any]] = []
        for item in payload.get("retrieved_materials", []) if isinstance(payload.get("retrieved_materials", []), list) else []:
            if not isinstance(item, dict):
                continue
            approved_materials.append(
                {
                    "evidence_id": str(item.get("evidence_id", "") or "").strip(),
                    "source_type": str(item.get("source_type", "") or "").strip(),
                    "title": str(item.get("title", "") or "").strip(),
                    "content": self._compact_text(item.get("content", ""), max_len=280),
                }
            )
        approved_materials = approved_materials[:12]

        bundles: Dict[str, Any] = {}
        raw_bundles = payload.get("evidence_bundles_by_task", {}) if isinstance(payload.get("evidence_bundles_by_task", {}), dict) else {}
        for task_code, item in list(raw_bundles.items())[:8]:
            if not isinstance(item, dict):
                continue
            bundles[str(task_code)] = {
                "required_rule_types": self._safe_list(item.get("required_rule_types", []))[:3],
                "supporting_evidence_ids": self._safe_list(item.get("supporting_evidence_ids", []))[:5],
                "reference_targets": self._safe_list(item.get("reference_targets", []))[:4],
            }

        return {
            "task_id": str(payload.get("task_id", "") or "").strip(),
            "section_id": str(payload.get("section_id", "") or "").strip(),
            "section_name": str(payload.get("section_name", "") or "").strip(),
            "raw_text": str(payload.get("raw_text", "") or "").strip(),
            "chapter_role": str(
                section_review_profile.get("chapter_role", "") or task_definition.get("chapter_role", "") or "3.2.P.5.2 分析方法审评"
            ).strip(),
            "core_review_question": str(
                section_review_profile.get("core_review_question", "")
                or task_definition.get("core_review_question", "")
                or "当前章节中的分析方法是否足以支撑对应质量标准项目，并且文本已经清楚到可执行、可复核。"
            ).strip(),
            "section_rules": self._safe_list(payload.get("section_rules", []))[:8],
            "task_questions": task_questions,
            "extracted_entities": payload.get("extracted_entities", {}) if isinstance(payload.get("extracted_entities", {}), dict) else {},
            "method_judgment": payload.get("method_judgment", {}) if isinstance(payload.get("method_judgment", {}), dict) else {},
            "approved_materials": approved_materials,
            "evidence_bundles_by_task": bundles,
        }

    def _build_medical_key_entities(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        extracted_entities = payload.get("extracted_entities", {}) if isinstance(payload.get("extracted_entities", {}), dict) else {}
        structured_sections = extracted_entities.get("structured_sections", {}) if isinstance(extracted_entities.get("structured_sections", {}), dict) else {}
        entities: List[Dict[str, Any]] = []
        for field_name in ["试液与试药", "仪器与用具", "仪器与设备", "试剂", "结果"]:
            value = str(structured_sections.get(field_name, "") or "").strip()
            if not value:
                continue
            entities.append(
                {
                    "entity_type": "section_block",
                    "entity_name": field_name,
                    "normalized_name": field_name,
                    "value": value,
                    "unit": "",
                    "context": value,
                    "evidence": value,
                    "source": "llm_entity_extraction",
                }
            )
        operation_methods = structured_sections.get("操作方法", {})
        if isinstance(operation_methods, dict):
            for method_name, content in operation_methods.items():
                method_name = str(method_name or "").strip()
                content = str(content or "").strip()
                if not method_name or not content:
                    continue
                entities.append(
                    {
                        "entity_type": "operation_method",
                        "entity_name": f"操作方法-{method_name}",
                        "normalized_name": method_name,
                        "value": content,
                        "unit": "",
                        "context": content,
                        "evidence": content,
                        "source": "llm_entity_extraction",
                    }
                )
        field_labels = {
            "method_type": "方法类型",
            "method_purpose": "方法目的",
            "sample_preparation": "供试品制备",
            "reference_preparation": "对照溶液制备",
            "reference_standard": "对照品",
            "pharmacopoeia_reference": "药典或通则引用",
            "sample_pretreatment": "供试液前处理",
            "diluent_or_neutralizer": "稀释液或中和体系",
            "test_scope": "检查范围",
            "culture_media": "培养基",
            "incubation_conditions": "培养条件",
            "enumeration_method": "计数方法",
            "suitability_reference": "适用性支持",
            "column_name": "色谱柱",
            "mobile_phase": "流动相",
            "medium_name": "溶剂/介质",
            "linked_spec_item": "质量标准项目映射",
            "linked_validation_target": "验证对象映射",
            "decision_rule": "结果判定表述",
        }
        for field_name, label in field_labels.items():
            row = extracted_entities.get(field_name, {}) if isinstance(extracted_entities.get(field_name, {}), dict) else {}
            value = str(row.get("value", "") or "").strip()
            evidence = str(row.get("evidence", "") or value).strip()
            if not value and not evidence:
                continue
            entities.append(
                {
                    "entity_type": "medical_entity",
                    "entity_name": label,
                    "normalized_name": field_name,
                    "value": value,
                    "unit": "",
                    "context": evidence,
                    "evidence": evidence,
                    "source": str(row.get("source", "") or "llm_entity_extraction").strip(),
                }
            )
        deduped: List[Dict[str, Any]] = []
        seen = set()
        for item in entities:
            dedupe_key = f"{item.get('entity_name', '')}::{item.get('value', '')}"
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            deduped.append(item)
        return deduped[:12]

    def _build_medical_key_data_points(self, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        extracted_entities = payload.get("extracted_entities", {}) if isinstance(payload.get("extracted_entities", {}), dict) else {}
        data_labels = {
            "medium_volume": "介质体积",
            "rotation_speed": "转速",
            "temperature": "温度",
            "sampling_timepoints": "观察/操作时间",
            "flow_rate": "流速",
            "wavelength": "检测波长",
            "calculation_formula": "计算公式",
            "incubation_conditions": "培养条件",
        }
        points: List[Dict[str, Any]] = []
        for field_name, label in data_labels.items():
            row = extracted_entities.get(field_name, {}) if isinstance(extracted_entities.get(field_name, {}), dict) else {}
            value = str(row.get("value", "") or "").strip()
            evidence = str(row.get("evidence", "") or value).strip()
            if not value and not evidence:
                continue
            points.append(
                {
                    "data_type": "medical_data_point",
                    "metric_name": label,
                    "value": value or evidence,
                    "unit": "",
                    "comparator": "",
                    "context": evidence,
                    "evidence": evidence,
                    "source": str(row.get("source", "") or "llm_entity_extraction").strip(),
                }
            )
        return points[:10]

    def _fallback_review(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        task_rows: List[Dict[str, Any]] = []
        reasoning_rows: List[Dict[str, Any]] = []
        question_rows: List[Dict[str, Any]] = []
        source_tasks = payload.get("task_questions", []) if isinstance(payload.get("task_questions", []), list) else []
        for index, item in enumerate(source_tasks[:8], start=1):
            if not isinstance(item, dict):
                continue
            task_code = str(item.get("task_code", "") or f"p52_task_{index}").strip() or f"p52_task_{index}"
            task_question = self._to_rule_item_title(
                task_code,
                str(item.get("task_question", "") or "").strip(),
                "；".join(self._safe_list(item.get("judgment_basis", []))[:2]),
                "",
            )
            basis = "；".join(self._safe_list(item.get("judgment_basis", []))[:2])
            reason = "模型结构化输出失败，本条仅保留为规则复核占位，不应直接作为最终风险结论。"
            advice = "请按本条规则补充“规则依据-原文事实-不符合点”三段式证据后重新审评。"
            task_rows.append(
                {
                    "task_code": task_code,
                    "status": "insufficient_information",
                    "task_question": task_question,
                    "problem": f"{task_question}：当前缺少可直接落库的结构化审评结论。",
                    "basis": basis,
                    "reason": reason,
                    "advice": advice,
                }
            )
            reasoning_rows.append(
                {
                    "task_code": task_code,
                    "rule_requirement": basis,
                    "material_fact": "",
                    "evidence_support": "",
                    "comparison": "模型本次未返回可直接使用的结构化推理结果。",
                    "judgment_reason": reason,
                }
            )
            question_rows.append(
                {
                    "issue": f"{task_question} 尚未形成可复核结论。",
                    "basis": basis,
                    "requested_action": advice,
                }
            )
        explicit_entities = [str(item.get("entity_name", "") or "").strip() for item in self._build_medical_key_entities(payload)[:6]]
        return {
            "section_id": str(payload.get("section_id", "") or "").strip(),
            "section_summary": "模型结构化输出异常，本次结果已降级为系统待重跑状态，未生成可发布的规则审评结论。",
            "supported_points": [],
            "unsupported_points": [],
            "missing_points": ["模型结构化输出异常（系统状态），请重跑后再进行业务审评。"],
            "risk_points": [],
            "pre_review_conclusion": "insufficient_information",
            "task_verdicts": [],
            "reasoning_chain_items": [],
            "rule_findings": [],
            "questions": [],
            "problem_basis_advice_items": [],
            "medical_key_entities": self._build_medical_key_entities(payload),
            "medical_key_data_points": self._build_medical_key_data_points(payload),
            "evidence_refs": [],
            "linked_rules": self._safe_list(payload.get("section_rules", []))[:8],
            "section_rules": self._safe_list(payload.get("section_rules", []))[:8],
            "fact_basis": {
                "explicit_in_text": explicit_entities,
                "inferred_from_evidence": [],
                "experience_warning": [],
                "not_stated_or_uncertain": ["模型本次未返回可直接使用的结构化推理结果。"],
            },
            "confidence": "low",
        }

    def _normalize_task_verdicts(self, value: Any) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for item in value if isinstance(value, list) else []:
            if not isinstance(item, dict):
                continue
            task_code = str(item.get("task_code", "") or "").strip()
            status = self._normalize_status(
                item.get("status", "") or item.get("task_status", "") or item.get("verdict", "")
            )
            if status not in {"supported", "unsupported", "insufficient_information"}:
                continue
            task_question = str(item.get("task_question", "") or "").strip()
            problem = str(item.get("problem", "") or item.get("issue", "") or "").strip()
            basis = str(item.get("basis", "") or item.get("rule_requirement", "") or item.get("rule_text", "") or "").strip()
            reason = str(item.get("reason", "") or item.get("judgment_reason", "") or "").strip()
            advice = str(item.get("advice", "") or item.get("suggested_fix", "") or item.get("requested_action", "") or "").strip()
            if not task_code:
                continue
            if not task_question:
                task_question = self._to_rule_item_title(task_code, "", basis, problem)
            rows.append(
                {
                    "task_code": task_code,
                    "status": status,
                    "task_question": task_question,
                    "problem": problem,
                    "basis": basis,
                    "reason": reason,
                    "advice": advice,
                }
            )
        deduped: List[Dict[str, Any]] = []
        seen = set()
        for row in rows:
            task_code = str(row.get("task_code", "") or "").strip()
            if not task_code or task_code in seen:
                continue
            seen.add(task_code)
            deduped.append(row)
        return deduped

    def _normalize_reasoning_chain_items(self, value: Any) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for item in value if isinstance(value, list) else []:
            if not isinstance(item, dict):
                continue
            task_code = str(item.get("task_code", "") or "").strip()
            rule_requirement = str(item.get("rule_requirement", "") or "").strip()
            material_fact = str(item.get("material_fact", "") or "").strip()
            evidence_support = str(item.get("evidence_support", "") or "").strip()
            comparison = str(item.get("comparison", "") or "").strip()
            judgment_reason = str(item.get("judgment_reason", "") or "").strip()
            if not task_code:
                continue
            rows.append(
                {
                    "task_code": task_code,
                    "rule_requirement": rule_requirement,
                    "material_fact": material_fact,
                    "evidence_support": evidence_support,
                    "comparison": comparison,
                    "judgment_reason": judgment_reason,
                }
            )
        deduped: List[Dict[str, Any]] = []
        seen = set()
        for row in rows:
            task_code = str(row.get("task_code", "") or "").strip()
            if task_code in seen:
                continue
            seen.add(task_code)
            deduped.append(row)
        return deduped

    def _normalize_rule_findings(self, value: Any) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for item in value if isinstance(value, list) else []:
            if not isinstance(item, dict):
                continue
            issue_type = str(item.get("issue_type", "") or "").strip().lower()
            if issue_type not in {"missing", "unsupported", "conflict", "uncertain"}:
                issue_type = "uncertain"
            rule_code = str(item.get("rule_code", "") or "").strip()
            rule_text = str(item.get("rule_text", "") or "").strip()
            requirement_point = str(item.get("requirement_point", "") or "").strip()
            location = str(item.get("location", "") or "").strip()
            issue = str(item.get("issue", "") or "").strip()
            evidence = str(item.get("evidence", "") or "").strip()
            composed_evidence = self._compose_rule_evidence(
                rule_code=rule_code,
                rule_text=rule_text,
                requirement_point=requirement_point,
                evidence=evidence,
                location=location,
                issue=issue,
                reason=str(item.get("reason", "") or item.get("judgment_reason", "") or "").strip(),
            )
            rows.append(
                {
                    "location": location,
                    "rule_code": rule_code,
                    "rule_text": rule_text,
                    "requirement_point": requirement_point,
                    "issue_type": issue_type,
                    "issue": issue,
                    "evidence": composed_evidence,
                    "suggested_fix": str(item.get("suggested_fix", "") or "").strip(),
                }
            )
        deduped: List[Dict[str, Any]] = []
        seen: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            rule_code = str(row.get("rule_code", "") or "").strip()
            issue = str(row.get("issue", "") or "").strip()
            dedupe_key = rule_code or issue
            if not dedupe_key:
                continue
            existing = seen.get(dedupe_key)
            if existing is None or len(str(row.get("evidence", "") or "")) > len(str(existing.get("evidence", "") or "")):
                seen[dedupe_key] = row
        deduped.extend(seen.values())
        return deduped

    def _normalize_questions(self, value: Any) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for item in value if isinstance(value, list) else []:
            if not isinstance(item, dict):
                continue
            issue = str(item.get("issue", "") or "").strip()
            basis = str(item.get("basis", "") or "").strip()
            requested_action = str(item.get("requested_action", "") or "").strip()
            if not issue or not basis or not requested_action:
                continue
            rows.append(
                {
                    "issue": issue,
                    "basis": basis,
                    "requested_action": requested_action,
                }
            )
        deduped: List[Dict[str, Any]] = []
        seen = set()
        for row in rows:
            dedupe_key = f"{row.get('issue', '')}::{row.get('basis', '')}"
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            deduped.append(row)
        return deduped

    def _normalize_problem_basis_advice_items(self, value: Any) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for item in value if isinstance(value, list) else []:
            if not isinstance(item, dict):
                continue
            rows.append(
                {
                    "status": str(item.get("status", "") or "").strip(),
                    "problem": str(item.get("problem", "") or "").strip(),
                    "basis": str(item.get("basis", "") or "").strip(),
                    "advice": str(item.get("advice", "") or "").strip(),
                    "evidence": str(item.get("evidence", "") or "").strip(),
                    "rule_code": str(item.get("rule_code", "") or "").strip(),
                    "rule_text": str(item.get("rule_text", "") or "").strip(),
                    "issue_type": str(item.get("issue_type", "") or "").strip(),
                }
            )
        return [row for row in rows if row.get("problem") and row.get("basis")]

    def _normalize_medical_key_entities(self, value: Any) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for item in value if isinstance(value, list) else []:
            if not isinstance(item, dict):
                continue
            entity_name = self._entity_label(item.get("entity_name", "") or item.get("normalized_name", ""))
            normalized_name = str(item.get("normalized_name", "") or "").strip()
            value_text = str(item.get("value", "") or "").strip()
            context = str(item.get("context", "") or value_text).strip()
            evidence = str(item.get("evidence", "") or context).strip()
            source = str(item.get("source", "") or "").strip()
            if not entity_name or not (value_text or evidence):
                continue
            if len(value_text) > 260 and normalized_name in {"sample_preparation", "reference_preparation", "mobile_phase", "medium_name", "linked_spec_item"}:
                continue
            rows.append(
                {
                    "entity_type": str(item.get("entity_type", "") or "medical_entity").strip(),
                    "entity_name": entity_name,
                    "normalized_name": normalized_name,
                    "value": value_text,
                    "unit": str(item.get("unit", "") or "").strip(),
                    "context": context,
                    "evidence": evidence,
                    "source": source or "llm_reviewer",
                }
            )
        deduped: List[Dict[str, Any]] = []
        seen = set()
        for row in rows:
            dedupe_key = f"{row.get('entity_name', '')}::{row.get('value', '')}"
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            deduped.append(row)
        return deduped[:12]

    def _normalize_medical_key_data_points(self, value: Any) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for item in value if isinstance(value, list) else []:
            if not isinstance(item, dict):
                continue
            metric_name = self._entity_label(item.get("metric_name", "") or item.get("normalized_name", ""))
            value_text = str(item.get("value", "") or "").strip()
            context = str(item.get("context", "") or value_text).strip()
            evidence = str(item.get("evidence", "") or context).strip()
            if not metric_name or not (value_text or evidence):
                continue
            if len(value_text) > 260:
                continue
            rows.append(
                {
                    "data_type": str(item.get("data_type", "") or "medical_data_point").strip(),
                    "metric_name": metric_name,
                    "value": value_text,
                    "unit": str(item.get("unit", "") or "").strip(),
                    "comparator": str(item.get("comparator", "") or "").strip(),
                    "context": context,
                    "evidence": evidence,
                    "source": str(item.get("source", "") or "llm_reviewer").strip(),
                }
            )
        deduped: List[Dict[str, Any]] = []
        seen = set()
        for row in rows:
            dedupe_key = f"{row.get('metric_name', '')}::{row.get('value', '')}"
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            deduped.append(row)
        return deduped[:10]

    def _build_task_verdicts_from_rule_findings(self, rule_findings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for item in rule_findings:
            if not isinstance(item, dict):
                continue
            task_code = str(item.get("rule_code", "") or "").strip()
            basis = str(item.get("rule_text", "") or item.get("requirement_point", "") or "").strip()
            issue = str(item.get("issue", "") or "").strip()
            evidence = str(item.get("evidence", "") or "").strip()
            advice = str(item.get("suggested_fix", "") or "").strip()
            issue_type = str(item.get("issue_type", "") or "").strip().lower()
            status = "unsupported" if issue_type in {"unsupported", "conflict"} else "insufficient_information"
            if not task_code or not issue:
                continue
            rows.append(
                {
                    "task_code": task_code,
                    "status": status,
                    "task_question": self._to_rule_item_title(task_code, "", basis, issue),
                    "problem": issue,
                    "basis": basis,
                    "reason": evidence or basis,
                    "advice": advice or "请补充与本条判断直接对应的章节事实、判定条件或映射关系。",
                }
            )
        return rows

    def _normalize_fact_basis(self, value: Any, payload: Dict[str, Any]) -> Dict[str, List[str]]:
        fact_basis = value if isinstance(value, dict) else {}
        explicit_entities = [str(item.get("entity_name", "") or "").strip() for item in self._build_medical_key_entities(payload)[:6]]
        return {
            "explicit_in_text": self._safe_list(fact_basis.get("explicit_in_text", []) or explicit_entities),
            "inferred_from_evidence": self._safe_list(fact_basis.get("inferred_from_evidence", [])),
            "experience_warning": self._safe_list(fact_basis.get("experience_warning", [])),
            "not_stated_or_uncertain": self._safe_list(fact_basis.get("not_stated_or_uncertain", [])),
        }

    def _build_problem_basis_advice_items_from_tasks(
        self,
        task_verdicts: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for item in task_verdicts:
            status = str(item.get("status", "") or "").strip()
            items.append(
                {
                    "status": "supported" if status == "supported" else "issue" if status == "unsupported" else "question",
                    "problem": str(item.get("problem", "") or "").strip(),
                    "basis": str(item.get("basis", "") or "").strip(),
                    "advice": str(item.get("advice", "") or "").strip(),
                    "evidence": str(item.get("reason", "") or "").strip(),
                    "rule_code": str(item.get("task_code", "") or "").strip(),
                    "rule_text": str(item.get("basis", "") or "").strip(),
                    "issue_type": "unsupported" if status == "unsupported" else "missing" if status == "insufficient_information" else "supported",
                }
            )
        return items

    def review(self, payload: Dict[str, Any], prompt_config: Dict[str, Any] | None = None) -> Dict[str, Any]:
        prompt_payload = self._build_prompt_payload(payload)
        prompt = self.prompts.render("p52_chapter_reviewer.j2", prompt_payload, prompt_config=prompt_config or {})
        default_data = self._fallback_review(payload)
        raw = self.llm.chat(
            messages=self.envelopes.build("reviewer", prompt),
            default=json.dumps(default_data, ensure_ascii=False),
        )
        parsed = self.llm.extract_json(raw)
        validate_review_output(parsed, 'p52_reviewer')
        llm_execution = self._build_llm_execution_meta(raw_text=raw, parsed_payload=parsed)
        data = dict(parsed)

        data["llm_execution"] = llm_execution
        data["section_id"] = str(data.get("section_id", "") or payload.get("section_id", "") or "").strip()
        for key in ["supported_points", "unsupported_points", "missing_points", "risk_points", "evidence_refs", "linked_rules", "section_rules"]:
            data[key] = self._safe_list(data.get(key, []))
        normalized_task_verdicts = self._normalize_task_verdicts(data.get("task_verdicts", []))
        data["task_verdicts"] = normalized_task_verdicts
        data["reasoning_chain_items"] = self._normalize_reasoning_chain_items(data.get("reasoning_chain_items", []))
        data["rule_findings"] = self._normalize_rule_findings(data.get("rule_findings", []))
        data["questions"] = self._normalize_questions(data.get("questions", []))
        data["problem_basis_advice_items"] = self._normalize_problem_basis_advice_items(data.get("problem_basis_advice_items", []))
        data["medical_key_entities"] = self._normalize_medical_key_entities(data.get("medical_key_entities", []))
        if not data["medical_key_entities"]:
            data["medical_key_entities"] = self._build_medical_key_entities(payload)
        data["medical_key_data_points"] = self._normalize_medical_key_data_points(data.get("medical_key_data_points", []))
        if not data["medical_key_data_points"]:
            data["medical_key_data_points"] = self._build_medical_key_data_points(payload)
        data["fact_basis"] = self._normalize_fact_basis(data.get("fact_basis", {}), payload)

        if not data["linked_rules"]:
            data["linked_rules"] = self._safe_list(payload.get("section_rules", []))[:8]
        if not data["section_rules"]:
            data["section_rules"] = self._safe_list(payload.get("section_rules", []))[:8]
        if not data["evidence_refs"]:
            data["evidence_refs"] = [
                str(item.get("title", "") or "").strip()
                for item in payload.get("retrieved_materials", [])
                if isinstance(item, dict) and str(item.get("title", "") or "").strip()
            ][:6]

        if not data["supported_points"]:
            data["supported_points"] = [
                str(item.get("problem", "") or "").strip()
                for item in data["task_verdicts"]
                if isinstance(item, dict) and str(item.get("status", "") or "").strip() == "supported"
            ][:6]
        if not data["unsupported_points"]:
            data["unsupported_points"] = [
                str(item.get("problem", "") or "").strip()
                for item in data["task_verdicts"]
                if isinstance(item, dict) and str(item.get("status", "") or "").strip() == "unsupported"
            ][:6]
        if not data["missing_points"]:
            data["missing_points"] = [
                str(item.get("problem", "") or "").strip()
                for item in data["task_verdicts"]
                if isinstance(item, dict) and str(item.get("status", "") or "").strip() == "insufficient_information"
            ][:6]
        if not data["risk_points"]:
            data["risk_points"] = list(data["unsupported_points"][:4] or data["missing_points"][:4])

        if not data["questions"]:
            data["questions"] = [
                {
                    "issue": str(item.get("problem", "") or "").strip(),
                    "basis": str(item.get("basis", "") or "").strip(),
                    "requested_action": str(item.get("advice", "") or "").strip(),
                }
                for item in data["task_verdicts"]
                if isinstance(item, dict) and str(item.get("status", "") or "").strip() == "insufficient_information"
            ][:6]

        if not data["task_verdicts"] and data["rule_findings"]:
            data["task_verdicts"] = self._build_task_verdicts_from_rule_findings(data["rule_findings"])

        if not data["rule_findings"]:
            data["rule_findings"] = [
                {
                    "location": "当前章节",
                    "rule_code": str(item.get("task_code", "") or "").strip(),
                    "rule_text": str(item.get("basis", "") or "").strip(),
                    "requirement_point": str(item.get("basis", "") or "").strip(),
                    "issue_type": "unsupported" if str(item.get("status", "") or "").strip() == "unsupported" else "missing",
                    "issue": str(item.get("problem", "") or "").strip(),
                    "evidence": str(item.get("reason", "") or "").strip(),
                    "suggested_fix": str(item.get("advice", "") or "").strip(),
                }
                for item in data["task_verdicts"]
                if isinstance(item, dict) and str(item.get("status", "") or "").strip() in {"unsupported", "insufficient_information"}
            ][:8]

        if not data["reasoning_chain_items"] and data["task_verdicts"]:
            data["reasoning_chain_items"] = [
                {
                    "task_code": str(item.get("task_code", "") or "").strip(),
                    "rule_requirement": str(item.get("basis", "") or "").strip(),
                    "material_fact": "",
                    "evidence_support": str(item.get("reason", "") or "").strip(),
                    "comparison": "已根据当前章节事实、补证和规则要求生成任务级判断。",
                    "judgment_reason": str(item.get("reason", "") or "").strip(),
                }
                for item in data["task_verdicts"][:8]
                if isinstance(item, dict)
            ]

        if not data["problem_basis_advice_items"]:
            data["problem_basis_advice_items"] = self._build_problem_basis_advice_items_from_tasks(data["task_verdicts"])

        supported_count = sum(1 for item in data["task_verdicts"] if str(item.get("status", "") or "").strip() == "supported")
        unsupported_count = sum(1 for item in data["task_verdicts"] if str(item.get("status", "") or "").strip() == "unsupported")
        insufficient_count = sum(1 for item in data["task_verdicts"] if str(item.get("status", "") or "").strip() == "insufficient_information")

        conclusion = str(data.get("pre_review_conclusion", "") or "").strip().lower()
        if conclusion not in {"supported", "unsupported", "insufficient_information"}:
            if unsupported_count > 0:
                conclusion = "unsupported"
            elif insufficient_count > 0:
                conclusion = "insufficient_information"
            else:
                conclusion = "supported" if supported_count > 0 else "insufficient_information"
        if conclusion == "supported" and (unsupported_count > 0 or insufficient_count > 0):
            conclusion = "unsupported" if unsupported_count > 0 else "insufficient_information"
        data["pre_review_conclusion"] = conclusion

        if not str(data.get("section_summary", "") or "").strip():
            section_name = str(payload.get("section_name", "") or data.get("section_id", "") or "当前章节").strip()
            if unsupported_count > 0:
                data["section_summary"] = f"{section_name} 已识别 {unsupported_count} 项明确缺口，当前方法文本尚不足以完整支撑对应标准项目。"
            elif insufficient_count > 0:
                data["section_summary"] = f"{section_name} 已识别 {insufficient_count} 项待补证判断，当前更偏向方法闭环或映射信息不足。"
            else:
                data["section_summary"] = f"{section_name} 当前方法描述与审评任务基本一致，未识别到明显的文本级缺口。"

        confidence = str(data.get("confidence", "medium") or "medium").strip().lower()
        if confidence not in {"low", "medium", "high"}:
            confidence = "medium"
        data["confidence"] = confidence
        return data
