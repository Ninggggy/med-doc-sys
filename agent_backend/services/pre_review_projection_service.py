from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from agent.agent_backend.services.pre_review_service import PreReviewService


class PreReviewProjectionService:
    """Centralize trace and frontend-facing result projections."""

    def __init__(self, service: "PreReviewService") -> None:
        self.service = service

    def _normalize_rule_display_text(self, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        # 已经是“编码 + 规则名”则直接返回
        if re.match(r"^[A-Z]+-\d{3}\s+.+$", text):
            return text
        # 仅有规则编码时，补全规则名称
        if re.match(r"^[A-Z]+-\d{3}$", text):
            try:
                display = str(
                    (
                        self.service.p52_rule_review_orchestrator.rule_engine.get_rule_display_text(text)
                        if getattr(self.service, "p52_rule_review_orchestrator", None) is not None
                        and getattr(self.service.p52_rule_review_orchestrator, "rule_engine", None) is not None
                        else ""
                    )
                    or ""
                ).strip()
            except Exception:
                display = ""
            return display or text
        return text

    def _normalize_rule_text_list(self, values: Any) -> List[str]:
        out: List[str] = []
        seen = set()
        for item in values if isinstance(values, list) else []:
            text = self._normalize_rule_display_text(item)
            if not text or text in seen:
                continue
            seen.add(text)
            out.append(text)
        return out

    @staticmethod
    def _fallback_problem_advice(problem: str) -> str:
        problem_text = str(problem or "").strip()
        if not problem_text:
            return ""
        return f"请补充或修订与“{problem_text}”直接对应的申报资料、法规依据、研究结果或结论说明。"

    def _normalize_problem_basis_advice_items(self, values: Any) -> List[Dict[str, str]]:
        if not isinstance(values, list):
            return []
        out: List[Dict[str, str]] = []
        seen = set()
        for item in values:
            if not isinstance(item, dict):
                continue
            row = {
                "status": str(item.get("status", "") or "").strip().lower() or "issue",
                "problem": str(item.get("problem", "") or item.get("issue", "") or "").strip(),
                "basis": str(item.get("basis", "") or item.get("evidence", "") or "").strip(),
                "advice": str(item.get("advice", "") or item.get("suggested_fix", "") or item.get("requested_action", "") or "").strip(),
                "evidence": str(item.get("evidence", "") or "").strip(),
                "rule_code": str(item.get("rule_code", "") or "").strip(),
                "rule_text": str(item.get("rule_text", "") or "").strip(),
                "issue_type": str(item.get("issue_type", "") or "").strip(),
                "section_id": str(item.get("section_id", "") or "").strip(),
                "section_name": str(item.get("section_name", "") or "").strip(),
            }
            if not row["problem"] and not row["basis"] and not row["advice"]:
                continue
            if not row["advice"]:
                row["advice"] = self._fallback_problem_advice(row["problem"])
            key = "|".join(
                [
                    row["status"],
                    row["problem"],
                    row["basis"],
                    row["advice"],
                    row["rule_code"],
                    row["section_id"],
                ]
            )
            if key in seen:
                continue
            seen.add(key)
            out.append(row)
        return out

    def _build_problem_basis_advice_items(
        self,
        *,
        section_id: str,
        section_name: str,
        conclusion: str,
        rule_findings: List[Dict[str, Any]],
        questions: List[Dict[str, Any]],
        supported_points: List[str],
        unsupported_points: List[str],
        missing_points: List[str],
        linked_rules: List[str],
        section_rules: List[str],
        evidence_refs: List[str],
    ) -> List[Dict[str, str]]:
        items: List[Dict[str, Any]] = []
        for item in rule_findings:
            if not isinstance(item, dict):
                continue
            problem = str(item.get("issue", "") or item.get("requirement_point", "") or item.get("rule_text", "") or "").strip()
            if not problem:
                continue
            basis_parts = []
            rule_code = str(item.get("rule_code", "") or "").strip()
            rule_text = str(item.get("rule_text", "") or "").strip()
            requirement_point = str(item.get("requirement_point", "") or "").strip()
            evidence = str(item.get("evidence", "") or "").strip()
            violating_text = str(item.get("violating_text", "") or "").strip()
            location = str(item.get("location", "") or "").strip()
            if rule_code or rule_text:
                basis_parts.append(f"规则依据：{':'.join([x for x in [rule_code, rule_text] if x])}")
            if requirement_point:
                basis_parts.append(f"要求点：{requirement_point}")
            if evidence:
                basis_parts.append(f"证据：{evidence}")
            if violating_text:
                basis_parts.append(f"原文摘录：{violating_text}")
            elif location:
                basis_parts.append(f"位置：{location}")
            items.append(
                {
                    "status": "issue",
                    "problem": problem,
                    "basis": "；".join([part for part in basis_parts if part]),
                    "advice": str(item.get("suggested_fix", "") or "").strip() or self._fallback_problem_advice(problem),
                    "evidence": evidence,
                    "rule_code": rule_code,
                    "rule_text": rule_text,
                    "issue_type": str(item.get("issue_type", "") or "").strip(),
                    "section_id": section_id,
                    "section_name": section_name,
                }
            )
        for item in questions:
            if not isinstance(item, dict):
                continue
            problem = str(item.get("issue", "") or "").strip()
            basis = str(item.get("basis", "") or "").strip()
            advice = str(item.get("requested_action", "") or "").strip()
            if not problem:
                continue
            items.append(
                {
                    "status": "question",
                    "problem": problem,
                    "basis": basis,
                    "advice": advice or self._fallback_problem_advice(problem),
                    "evidence": basis,
                    "section_id": section_id,
                    "section_name": section_name,
                }
            )
        if not items and conclusion == "supported":
            for point in supported_points[:5]:
                text = str(point or "").strip()
                if not text:
                    continue
                basis_text = text
                if linked_rules:
                    basis_text = f"{text}；关联规则：{linked_rules[0]}"
                elif section_rules:
                    basis_text = f"{text}；关联规则：{section_rules[0]}"
                if evidence_refs:
                    basis_text = f"{basis_text}；证据：{evidence_refs[0]}"
                items.append(
                    {
                        "status": "supported",
                        "problem": "未发现与关键要求不符的问题",
                        "basis": basis_text,
                        "advice": "保持当前申报方式，并持续确保结论与证据链一一对应。",
                        "evidence": evidence_refs[0] if evidence_refs else "",
                        "section_id": section_id,
                        "section_name": section_name,
                    }
                )
        if not items:
            fallback_points = unsupported_points[:3] + missing_points[:3]
            for point in fallback_points:
                text = str(point or "").strip()
                if not text:
                    continue
                basis_text = linked_rules[0] if linked_rules else (section_rules[0] if section_rules else "")
                items.append(
                    {
                        "status": "issue",
                        "problem": text,
                        "basis": basis_text,
                        "advice": self._fallback_problem_advice(text),
                        "section_id": section_id,
                        "section_name": section_name,
                    }
                )
        for row in items:
            if str(row.get("status", "") or "").strip().lower() != "supported":
                continue
            problem = str(row.get("problem", "") or "").strip()
            if supported_points and (not problem or "未发现" in problem or "æœªå‘çŽ°" in problem):
                problem = str(supported_points[0] or "").strip()
                row["problem"] = problem
            basis_parts = [f"已形成对“{problem or '当前判断项'}”的直接支撑"]
            linked_rule = linked_rules[0] if linked_rules else (section_rules[0] if section_rules else "")
            evidence_ref = evidence_refs[0] if evidence_refs else ""
            if linked_rule:
                basis_parts.append(f"关联规则：{linked_rule}")
            if evidence_ref:
                basis_parts.append(f"证据：{evidence_ref}")
            row["basis"] = "；".join(basis_parts)
            row["advice"] = "保持当前事实表述、规则依据和证据链的一致性，避免后续版本出现信息漂移。"
        return self._normalize_problem_basis_advice_items(items)[:8]

    @staticmethod
    def _normalize_task_verdicts(values: Any) -> List[Dict[str, str]]:
        if not isinstance(values, list):
            return []
        out: List[Dict[str, str]] = []
        seen = set()
        for item in values:
            if not isinstance(item, dict):
                continue
            row = {
                "task_code": str(item.get("task_code", "") or "").strip(),
                "status": str(item.get("status", "") or "").strip().lower(),
                "task_question": str(item.get("task_question", "") or "").strip(),
                "problem": str(item.get("problem", "") or item.get("issue", "") or "").strip(),
                "basis": str(item.get("basis", "") or "").strip(),
                "reason": str(item.get("reason", "") or "").strip(),
                "advice": str(item.get("advice", "") or item.get("suggested_fix", "") or "").strip(),
            }
            if not any([row["task_question"], row["problem"], row["basis"], row["reason"], row["advice"]]):
                continue
            key = "|".join(
                [
                    row["task_code"],
                    row["status"],
                    row["task_question"],
                    row["problem"],
                    row["basis"],
                    row["reason"],
                ]
            )
            if key in seen:
                continue
            seen.add(key)
            out.append(row)
        return out[:12]

    @staticmethod
    def _normalize_reasoning_chain_items(values: Any) -> List[Dict[str, str]]:
        if not isinstance(values, list):
            return []
        out: List[Dict[str, str]] = []
        seen = set()
        for item in values:
            if not isinstance(item, dict):
                continue
            row = {
                "task_code": str(item.get("task_code", "") or "").strip(),
                "rule_requirement": str(item.get("rule_requirement", "") or "").strip(),
                "material_fact": str(item.get("material_fact", "") or "").strip(),
                "evidence_support": str(item.get("evidence_support", "") or "").strip(),
                "evidence_excerpt": str(item.get("evidence_excerpt", "") or "").strip(),
                "comparison": str(item.get("comparison", "") or "").strip(),
                "judgment_reason": str(item.get("judgment_reason", "") or item.get("reason", "") or "").strip(),
            }
            if not any(
                [
                    row["rule_requirement"],
                    row["material_fact"],
                    row["evidence_support"],
                    row["evidence_excerpt"],
                    row["comparison"],
                    row["judgment_reason"],
                ]
            ):
                continue
            key = "|".join(
                [
                    row["task_code"],
                    row["rule_requirement"],
                    row["material_fact"],
                    row["evidence_support"],
                    row["evidence_excerpt"],
                    row["comparison"],
                    row["judgment_reason"],
                ]
            )
            if key in seen:
                continue
            seen.add(key)
            out.append(row)
        return out[:12]

    @staticmethod
    def _normalize_matching_text(value: Any) -> str:
        text = str(value or "").strip().lower()
        if not text:
            return ""
        return re.sub(r"[\s\u3000；;，,。、“”\"'（）()【】\[\]：:、\\/_-]+", "", text)

    @classmethod
    def _must_answer_match_score(cls, question: str, task_row: Dict[str, Any]) -> float:
        normalized_question = cls._normalize_matching_text(question)
        if not normalized_question:
            return 0.0
        best_score = 0.0
        question_chars = set(normalized_question)
        for field in ("task_question", "problem", "basis", "rule_requirement", "material_fact"):
            candidate = cls._normalize_matching_text(task_row.get(field, ""))
            if not candidate:
                continue
            if normalized_question in candidate or candidate in normalized_question:
                best_score = max(best_score, 1.0 if min(len(normalized_question), len(candidate)) >= 6 else 0.85)
            if question_chars:
                overlap = len(question_chars & set(candidate))
                best_score = max(best_score, overlap / len(question_chars))
        return best_score

    def _build_must_answer_coverage(
        self,
        *,
        section_review_profile: Dict[str, Any],
        task_definition: Dict[str, Any],
        task_verdicts: List[Dict[str, str]],
        reasoning_chain_items: List[Dict[str, str]],
    ) -> List[Dict[str, Any]]:
        raw_questions = (
            section_review_profile.get("must_answer_questions")
            if isinstance(section_review_profile.get("must_answer_questions"), list) and section_review_profile.get("must_answer_questions")
            else task_definition.get("must_answer_questions")
        )
        must_answer_questions = self.service._normalize_text_list(raw_questions or [])
        if not must_answer_questions:
            return []

        reasoning_map = {
            str(item.get("task_code", "") or "").strip(): item
            for item in reasoning_chain_items
            if isinstance(item, dict) and str(item.get("task_code", "") or "").strip()
        }
        task_rows: List[Dict[str, Any]] = []
        for item in task_verdicts:
            if not isinstance(item, dict):
                continue
            task_code = str(item.get("task_code", "") or "").strip()
            reasoning = reasoning_map.get(task_code, {})
            task_rows.append(
                {
                    "task_code": task_code,
                    "status": str(item.get("status", "") or "").strip().lower(),
                    "task_question": str(item.get("task_question", "") or "").strip(),
                    "problem": str(item.get("problem", "") or item.get("issue", "") or "").strip(),
                    "basis": str(item.get("basis", "") or "").strip(),
                    "rule_requirement": str(reasoning.get("rule_requirement", "") or "").strip(),
                    "material_fact": str(reasoning.get("material_fact", "") or "").strip(),
                    "judgment_reason": str(item.get("reason", "") or reasoning.get("judgment_reason", "") or "").strip(),
                }
            )

        coverage_rows: List[Dict[str, Any]] = []
        for question in must_answer_questions:
            matched_rows: List[Dict[str, Any]] = []
            for task_row in task_rows:
                score = self._must_answer_match_score(question, task_row)
                if score >= 0.42:
                    matched = dict(task_row)
                    matched["match_score"] = round(score, 4)
                    matched_rows.append(matched)

            verdict_status = "unknown"
            coverage_status = "uncovered"
            reason = "当前尚未找到与该必答问题直接对应的判断项。"
            if matched_rows:
                matched_rows.sort(key=lambda item: float(item.get("match_score", 0.0)), reverse=True)
                statuses = {
                    str(item.get("status", "") or "").strip().lower()
                    for item in matched_rows
                    if str(item.get("status", "") or "").strip()
                }
                if statuses and statuses == {"supported"}:
                    verdict_status = "supported"
                    coverage_status = "covered"
                    reason = "已存在直接对应的判断项，且当前均已形成支持性判断。"
                elif "supported" in statuses:
                    verdict_status = "mixed"
                    coverage_status = "partial"
                    reason = "已存在对应判断项，但结论仍有分歧或部分证据链尚未闭合。"
                else:
                    verdict_status = "risky"
                    coverage_status = "partial"
                    reason = "已存在对应判断项，但目前仍停留在未通过或证据不足状态。"

            coverage_rows.append(
                {
                    "question": question,
                    "coverage_status": coverage_status,
                    "verdict_status": verdict_status,
                    "matched_task_codes": [
                        str(item.get("task_code", "") or "").strip()
                        for item in matched_rows
                        if str(item.get("task_code", "") or "").strip()
                    ],
                    "matched_task_questions": [
                        str(item.get("task_question", "") or "").strip()
                        for item in matched_rows
                        if str(item.get("task_question", "") or "").strip()
                    ],
                    "reason": reason,
                }
            )
        return coverage_rows

    def _build_reviewer_outline(
        self,
        *,
        section_review_profile: Dict[str, Any],
        task_definition: Dict[str, Any],
        must_answer_coverage: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        chapter_role = str(section_review_profile.get("chapter_role", "") or task_definition.get("chapter_role", "") or "").strip()
        core_review_question = str(
            section_review_profile.get("core_review_question", "")
            or task_definition.get("core_review_question", "")
            or task_definition.get("review_goal", "")
            or ""
        ).strip()
        must_answer_questions = self.service._normalize_text_list(
            section_review_profile.get("must_answer_questions", []) or task_definition.get("must_answer_questions", [])
        )
        covered_count = sum(1 for item in must_answer_coverage if str(item.get("coverage_status", "") or "").strip() == "covered")
        partial_count = sum(1 for item in must_answer_coverage if str(item.get("coverage_status", "") or "").strip() == "partial")
        uncovered_count = sum(1 for item in must_answer_coverage if str(item.get("coverage_status", "") or "").strip() == "uncovered")
        total = len(must_answer_coverage) or len(must_answer_questions)
        summary = ""
        if total:
            summary = (
                f"本章围绕“{core_review_question or '当前核心审评问题'}”共设置 {total} 个必答问题，"
                f"已形成直接支撑 {covered_count} 个，仍需补强 {partial_count + uncovered_count} 个。"
            )
        return {
            "chapter_role": chapter_role,
            "core_review_question": core_review_question,
            "must_answer_questions": must_answer_questions,
            "covered_count": covered_count,
            "partial_count": partial_count,
            "uncovered_count": uncovered_count,
            "coverage_ratio": round(covered_count / total, 4) if total else 0.0,
            "summary": summary,
        }

    @staticmethod
    def _normalize_llm_execution(value: Any) -> Dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        agent = str(value.get("agent", "") or "").strip()
        raw_preview = str(value.get("raw_preview", "") or "").strip()
        failure_reason = str(value.get("failure_reason", "") or "").strip()
        if not any(
            [
                agent,
                raw_preview,
                failure_reason,
                value.get("used_default_fallback", False),
                value.get("used_model_output", False),
                value.get("json_parse_ok", False),
            ]
        ):
            return {}
        return {
            "agent": agent,
            "used_default_fallback": bool(value.get("used_default_fallback", False)),
            "used_model_output": bool(value.get("used_model_output", False)),
            "json_parse_ok": bool(value.get("json_parse_ok", False)),
            "failure_reason": failure_reason,
            "raw_preview": raw_preview,
        }

    @staticmethod
    def _normalize_medical_key_entities(values: Any) -> List[Dict[str, str]]:
        if not isinstance(values, list):
            return []
        out: List[Dict[str, str]] = []
        seen = set()
        for item in values:
            if not isinstance(item, dict):
                continue
            row = {
                "entity_type": str(item.get("entity_type", "") or "").strip(),
                "entity_name": str(item.get("entity_name", "") or item.get("name", "") or "").strip(),
                "normalized_name": str(item.get("normalized_name", "") or item.get("canonical_name", "") or item.get("entity_name", "") or item.get("name", "") or "").strip(),
                "value": str(item.get("value", "") or "").strip(),
                "unit": str(item.get("unit", "") or "").strip(),
                "context": str(item.get("context", "") or "").strip(),
                "evidence": str(item.get("evidence", "") or "").strip(),
                "source": str(item.get("source", "") or "").strip(),
            }
            if not row["entity_name"] and not row["normalized_name"]:
                continue
            key = "|".join([row["entity_type"], row["entity_name"], row["normalized_name"], row["value"], row["unit"], row["source"]])
            if key in seen:
                continue
            seen.add(key)
            out.append(row)
        return out[:12]

    @staticmethod
    def _normalize_medical_key_data_points(values: Any) -> List[Dict[str, str]]:
        if not isinstance(values, list):
            return []
        out: List[Dict[str, str]] = []
        seen = set()
        for item in values:
            if not isinstance(item, dict):
                continue
            row = {
                "data_type": str(item.get("data_type", "") or "").strip(),
                "metric_name": str(item.get("metric_name", "") or item.get("name", "") or "").strip(),
                "value": str(item.get("value", "") or "").strip(),
                "unit": str(item.get("unit", "") or "").strip(),
                "comparator": str(item.get("comparator", "") or "").strip(),
                "context": str(item.get("context", "") or "").strip(),
                "evidence": str(item.get("evidence", "") or "").strip(),
                "source": str(item.get("source", "") or "").strip(),
            }
            if not row["metric_name"] and not row["value"]:
                continue
            key = "|".join([row["data_type"], row["metric_name"], row["value"], row["unit"], row["comparator"], row["source"]])
            if key in seen:
                continue
            seen.add(key)
            out.append(row)
        return out[:12]

    def build_section_trace_payload(
        self,
        *,
        workflow_mode: str,
        section_id: str,
        section_name: str,
        chunk: Dict[str, Any],
        text: str,
        paragraph_blocks: List[Dict[str, Any]],
        focus_points: List[str],
        section_rules: List[str],
        section_review_packet,
        coordination_payload: Dict[str, Any],
        historical_experience: List[Dict[str, Any]],
        section_summary: Dict[str, Any],
        findings: List[Dict[str, Any]],
        score: float,
        registration_class: str,
        review_domain: str,
        product_type: str,
        planner_result: Dict[str, Any],
        retrieved_materials: List[Dict[str, Any]],
        approved_materials: List[Dict[str, Any]],
        historical_bad_retrievals: List[Dict[str, Any]],
        retrieval_evaluation_result: Dict[str, Any],
        rejected_materials: List[Dict[str, Any]],
        task_question_result: Dict[str, Any],
        review_result: Dict[str, Any],
        planner_prompt_config: Optional[Dict[str, Any]],
        retrieval_evaluator_prompt_config: Optional[Dict[str, Any]],
        task_question_prompt_config: Optional[Dict[str, Any]],
        reviewer_prompt_config: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        planner_rules = (
            ((planner_prompt_config.get("prompt_bundle", {}) if isinstance(planner_prompt_config, dict) else {}).get("active_rules", {}) or {}).get("planner", [])
        )
        retrieval_evaluator_rules = (
            ((retrieval_evaluator_prompt_config.get("prompt_bundle", {}) if isinstance(retrieval_evaluator_prompt_config, dict) else {}).get("active_rules", {}) or {}).get("retrieval_evaluator", [])
        )
        task_question_rules = (
            ((task_question_prompt_config.get("prompt_bundle", {}) if isinstance(task_question_prompt_config, dict) else {}).get("active_rules", {}) or {}).get("task_question", [])
        )
        reviewer_rules = (
            ((reviewer_prompt_config.get("prompt_bundle", {}) if isinstance(reviewer_prompt_config, dict) else {}).get("active_rules", {}) or {}).get("reviewer", [])
        )
        return {
            "trace_schema": "chapter_review_v1",
            "section_packet": {
                "section_id": section_id,
                "section_code": str(chunk.get("section_code", "") or section_id),
                "section_title": section_name,
                "page_start": chunk.get("page_start"),
                "page_end": chunk.get("page_end"),
                "title_path": chunk.get("title_path", []),
                "paragraph_blocks": paragraph_blocks,
                "raw_text": text,
                "focus_points": self.service._normalize_text_list(focus_points),
                "section_rules": self.service._normalize_text_list(section_rules),
            },
            "section_review_packet": section_review_packet.to_dict(),
            "coordination": coordination_payload,
            "memory": {
                "hit_count": len(historical_experience),
                "context_preview": self.service._preview(json.dumps(historical_experience[:3], ensure_ascii=False), max_len=600),
                "hits_by_type": {"历史经验": len(historical_experience)},
            },
            "summary_agent": section_summary,
            "agent": {
                "strategy": "planner_review_single_v2" if str(workflow_mode or "") == "single_section_pre_review_v2" else "planner_review_v1",
                "roles": ["planner", "retrieval_evaluator", "task_question", "pre_review"],
                "findings": findings,
                "findings_count": len(findings),
                "score": score,
            },
            "planner_result": planner_result,
            "raw_retrieved_materials": retrieved_materials,
            "retrieved_materials": approved_materials,
            "retrieval_evaluation": {
                "historical_bad_retrievals": historical_bad_retrievals,
                "result": retrieval_evaluation_result,
                "rejected_count": len(rejected_materials),
            },
            "task_question": task_question_result,
            "standardized_output": review_result,
            "prompt_rules": {
                "planner": planner_rules,
                "retrieval_evaluator": retrieval_evaluator_rules,
                "task_question": task_question_rules,
                "reviewer": reviewer_rules,
            },
            "trace": {
                "registration_class": registration_class,
                "review_domain": review_domain,
                "product_type": product_type,
            },
        }

    def normalize_section_review_output(
        self,
        output_payload: Dict[str, Any],
        *,
        section_id: str = "",
        section_name: str = "",
        fallback_conclusion: str = "",
        fallback_risk_level: str = "",
        fallback_linked_rules: Optional[List[Any]] = None,
        fallback_focus_points: Optional[List[Any]] = None,
        fallback_section_rules: Optional[List[Any]] = None,
    ) -> Dict[str, Any]:
        payload = dict(output_payload or {}) if isinstance(output_payload, dict) else {}
        fact_basis = payload.get("fact_basis", {}) if isinstance(payload.get("fact_basis", {}), dict) else {}
        confidence = payload.get("confidence", {}) if isinstance(payload.get("confidence", {}), dict) else payload.get("confidence")
        rule_findings = payload.get("rule_findings", []) if isinstance(payload.get("rule_findings", []), list) else []
        normalized_questions = [
            {
                "issue": str(item.get("issue", "") or "").strip(),
                "basis": str(item.get("basis", "") or "").strip(),
                "requested_action": str(item.get("requested_action", "") or "").strip(),
            }
            for item in payload.get("questions", [])
            if isinstance(item, dict) and str(item.get("issue", "") or "").strip()
        ] if isinstance(payload.get("questions", []), list) else []
        normalized_rule_findings = [
            {
                "location": str(item.get("location", "") or "").strip(),
                "rule_code": str(item.get("rule_code", "") or "").strip(),
                "rule_text": self._normalize_rule_display_text(
                    str(item.get("rule_text", "") or "").strip() or str(item.get("rule_code", "") or "").strip()
                ),
                "requirement_point": str(item.get("requirement_point", "") or item.get("rule_clause", "") or "").strip(),
                "issue_type": str(item.get("issue_type", "") or "").strip(),
                "issue": str(item.get("issue", "") or "").strip(),
                "evidence": str(item.get("evidence", "") or "").strip(),
                "violating_text": str(item.get("violating_text", "") or item.get("source_excerpt", "") or "").strip(),
                "suggested_fix": str(item.get("suggested_fix", "") or item.get("requested_action", "") or "").strip(),
            }
            for item in rule_findings
            if isinstance(item, dict)
        ]
        linked_rules = self._normalize_rule_text_list(payload.get("linked_rules", []) or fallback_linked_rules or [])
        section_rules = self._normalize_rule_text_list(payload.get("section_rules", []) or fallback_section_rules or [])
        supported_points = self.service._normalize_text_list(payload.get("supported_points", []))
        unsupported_points = self.service._normalize_text_list(payload.get("unsupported_points", []))
        missing_points = self.service._normalize_text_list(payload.get("missing_points", []))
        evidence_refs = self.service._normalize_text_list(payload.get("evidence_refs", []))
        normalized_task_verdicts = self._normalize_task_verdicts(payload.get("task_verdicts", []))
        normalized_reasoning_chain_items = self._normalize_reasoning_chain_items(payload.get("reasoning_chain_items", []))
        normalized_problem_basis_advice_items = self._normalize_problem_basis_advice_items(payload.get("problem_basis_advice_items", []))
        if not normalized_problem_basis_advice_items:
            normalized_problem_basis_advice_items = self._build_problem_basis_advice_items(
                section_id=str(payload.get("section_id", "") or section_id or "").strip(),
                section_name=str(payload.get("section_name", "") or section_name or "").strip(),
                conclusion=str(payload.get("pre_review_conclusion", "") or payload.get("conclusion", "") or fallback_conclusion or "").strip(),
                rule_findings=normalized_rule_findings,
                questions=normalized_questions,
                supported_points=supported_points,
                unsupported_points=unsupported_points,
                missing_points=missing_points,
                linked_rules=linked_rules,
                section_rules=section_rules,
                evidence_refs=evidence_refs,
            )
        normalized_medical_key_entities = self._normalize_medical_key_entities(payload.get("medical_key_entities", []))
        normalized_medical_key_data_points = self._normalize_medical_key_data_points(payload.get("medical_key_data_points", []))
        normalized_llm_execution = self._normalize_llm_execution(payload.get("llm_execution", {}))
        export_evidence_snippets = [
            {
                "source": str(item.get("source", "") or "").strip(),
                "snippet": str(item.get("snippet", "") or "").strip(),
            }
            for item in payload.get("export_evidence_snippets", [])
            if isinstance(item, dict) and (str(item.get("source", "") or "").strip() or str(item.get("snippet", "") or "").strip())
        ] if isinstance(payload.get("export_evidence_snippets", []), list) else []
        section_review_profile = payload.get("section_review_profile", {})
        if not isinstance(section_review_profile, dict):
            section_review_profile = {}
        task_definition = dict(payload.get("task_definition", {})) if isinstance(payload.get("task_definition", {}), dict) else {}
        if not section_review_profile and isinstance(payload.get("task_definition", {}), dict):
            nested_profile = payload.get("task_definition", {}).get("section_review_profile", {})
            section_review_profile = dict(nested_profile) if isinstance(nested_profile, dict) else {}
        must_answer_coverage = self._build_must_answer_coverage(
            section_review_profile=section_review_profile,
            task_definition=task_definition,
            task_verdicts=normalized_task_verdicts,
            reasoning_chain_items=normalized_reasoning_chain_items,
        )
        reviewer_outline = self._build_reviewer_outline(
            section_review_profile=section_review_profile,
            task_definition=task_definition,
            must_answer_coverage=must_answer_coverage,
        )
        return {
            "display_template": "problem_basis_advice_v1",
            "section_id": str(payload.get("section_id", "") or section_id or "").strip(),
            "section_name": str(payload.get("section_name", "") or section_name or "").strip(),
            "section_summary": str(payload.get("section_summary", "") or payload.get("summary", "") or "").strip(),
            "pre_review_conclusion": str(payload.get("pre_review_conclusion", "") or payload.get("conclusion", "") or fallback_conclusion or "").strip(),
            "risk_level": str(payload.get("risk_level", "") or fallback_risk_level or "").strip(),
            "supported_points": supported_points,
            "unsupported_points": unsupported_points,
            "missing_points": missing_points,
            "risk_points": self.service._normalize_text_list(payload.get("risk_points", [])),
            "questions": normalized_questions,
            "evidence_refs": evidence_refs,
            "linked_rules": linked_rules,
            "focus_points": self.service._normalize_text_list(payload.get("focus_points", []) or payload.get("concern_points", []) or fallback_focus_points or []),
            "section_rules": section_rules,
            "task_definition": task_definition,
            "section_review_profile": dict(section_review_profile),
            "reviewer_outline": reviewer_outline,
            "must_answer_coverage": must_answer_coverage,
            "compliance_targets": self.service._normalize_text_list(payload.get("compliance_targets", [])),
            "issue_hypotheses": self.service._normalize_text_list(payload.get("issue_hypotheses", [])),
            "evidence_requirements": self.service._normalize_text_list(payload.get("evidence_requirements", [])),
            "output_requirements": self.service._normalize_text_list(payload.get("output_requirements", [])),
            "medical_entity_requirements": self.service._normalize_text_list(payload.get("medical_entity_requirements", [])),
            "medical_data_requirements": self.service._normalize_text_list(payload.get("medical_data_requirements", [])),
            "task_verdicts": normalized_task_verdicts,
            "reasoning_chain_items": normalized_reasoning_chain_items,
            "rule_findings": normalized_rule_findings,
            "problem_basis_advice_items": normalized_problem_basis_advice_items,
            "medical_key_entities": normalized_medical_key_entities,
            "medical_key_data_points": normalized_medical_key_data_points,
            "export_evidence_snippets": export_evidence_snippets,
            "llm_execution": normalized_llm_execution,
            "fact_basis": {
                "explicit_in_text": self.service._normalize_text_list(fact_basis.get("explicit_in_text", [])),
                "inferred_from_evidence": self.service._normalize_text_list(
                    fact_basis.get("inferred_from_evidence", []) or fact_basis.get("supported_by_retrieval", [])
                ),
                "experience_warning": self.service._normalize_text_list(
                    fact_basis.get("experience_warning", []) or fact_basis.get("based_on_experience_warning", [])
                ),
                "not_stated_or_uncertain": self.service._normalize_text_list(fact_basis.get("not_stated_or_uncertain", [])),
            },
            "confidence": confidence if isinstance(confidence, dict) else {"label": str(confidence or "").strip()},
        }
