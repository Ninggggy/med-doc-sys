from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Set

from agent.agent_backend.llm.client import LLMClient
from agent.agent_backend.llm.errors import LLMExecutionError
from agent.agent_backend.prompts.prompt_envelope_builder import PromptEnvelopeBuilder
from agent.agent_backend.prompts.template_manager import PromptTemplateManager


class RetrievalEvaluatorAgent:
    """Filter retrieval hits before they enter task-questioning and review."""

    ENTITY_REGEXES = [
        r"(?:^|\n)\s*(?:中文名|通用名|药品名称)\s*[:：]\s*([^\n\r]{2,120})",
        r"(?:^|\n)\s*(?:英文名|英文通用名)\s*[:：]\s*([^\n\r]{2,160})",
        r"(?:^|\n)\s*(?:中文化学名)\s*[:：]\s*([^\n\r]{2,240})",
        r"(?:^|\n)\s*(?:英文化学名)\s*[:：]\s*([^\n\r]{2,240})",
        r"(?:^|\n)\s*CAS(?:号| No\.?)?\s*[:：]\s*([A-Za-z0-9\-]{4,40})",
    ]

    INTENT_BLACKLIST = {
        "review",
        "section",
        "rule",
        "task",
        "章节",
        "规则",
        "任务",
    }

    def __init__(self) -> None:
        self.llm = LLMClient()
        self.envelopes = PromptEnvelopeBuilder()
        self.prompts = PromptTemplateManager("pre_review_agent_prompt")

    @staticmethod
    def _safe_list(value: Any) -> List[Any]:
        return value if isinstance(value, list) else []

    @staticmethod
    def _safe_text_list(value: Any) -> List[str]:
        out: List[str] = []
        seen: Set[str] = set()
        for item in value if isinstance(value, list) else []:
            text = str(item or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            out.append(text)
        return out

    @staticmethod
    def _compact_text(text: Any, max_len: int = 160) -> str:
        value = re.sub(r"\s+", " ", str(text or "").strip())
        if not value:
            return ""
        return value if len(value) <= max_len else value[: max_len - 3].rstrip() + "..."

    def _build_llm_execution_meta(
        self,
        *,
        agent_name: str,
        raw_text: Any,
        parsed_payload: Any,
    ) -> Dict[str, Any]:
        used_default_fallback = not isinstance(parsed_payload, dict)
        failure_reason = ""
        if used_default_fallback:
            failure_reason = "empty_response" if not str(raw_text or "").strip() else "json_parse_failed_or_non_object"
        return {
            "agent": agent_name,
            "used_default_fallback": used_default_fallback,
            "used_model_output": not used_default_fallback,
            "json_parse_ok": isinstance(parsed_payload, dict),
            "failure_reason": failure_reason,
            "raw_preview": "",
            "output_chars": len(str(raw_text or '')),
        }

    @staticmethod
    def _material_evidence_id(item: Dict[str, Any]) -> str:
        evidence_id = str(item.get("evidence_id", "") or "").strip()
        if evidence_id:
            return evidence_id
        doc_id = str(item.get("doc_id", "") or "").strip()
        chunk_id = str(item.get("chunk_id", "") or "").strip()
        if doc_id or chunk_id:
            return f"{doc_id}:{chunk_id}"
        return ""

    @classmethod
    def _extract_target_terms(cls, raw_text: str) -> List[str]:
        text = str(raw_text or "")
        out: List[str] = []
        seen: Set[str] = set()
        for pattern in cls.ENTITY_REGEXES:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if not match:
                continue
            candidate = str(match.group(1) or "").strip(" ;,:；，。")
            if not candidate:
                continue
            normalized = candidate.lower()
            if normalized in seen:
                continue
            seen.add(normalized)
            out.append(candidate)
        return out

    @classmethod
    def _extract_intent_keywords(cls, section_name: str, focus_points: List[str], section_rules: List[str]) -> List[str]:
        raw_terms: List[str] = [str(section_name or "").strip()]
        raw_terms.extend([str(item or "").strip() for item in focus_points[:8]])
        raw_terms.extend([str(item or "").strip() for item in section_rules[:8]])
        out: List[str] = []
        seen: Set[str] = set()
        for term in raw_terms:
            tokens = re.findall(r"[A-Za-z][A-Za-z0-9\-]{1,24}|[\u4e00-\u9fff]{2,12}", term)
            for token in tokens:
                normalized = str(token or "").strip()
                if not normalized:
                    continue
                if normalized.lower() in cls.INTENT_BLACKLIST or normalized in seen:
                    continue
                seen.add(normalized)
                out.append(normalized)
        return out[:24]

    @staticmethod
    def _collect_negative_evidence_ids(history: List[Dict[str, Any]]) -> Set[str]:
        out: Set[str] = set()
        for item in history:
            if not isinstance(item, dict):
                continue
            verdict = str(item.get("verdict", "") or item.get("label", "") or "").strip().lower()
            if verdict not in {"incorrect", "partial", "rejected"}:
                continue
            evidence_id = str(item.get("evidence_id", "") or "").strip()
            if evidence_id:
                out.add(evidence_id)
                continue
            doc_id = str(item.get("doc_id", "") or "").strip()
            chunk_id = str(item.get("chunk_id", "") or "").strip()
            if doc_id or chunk_id:
                out.add(f"{doc_id}:{chunk_id}")
        return out

    @staticmethod
    def _match_count(text: str, keywords: List[str]) -> int:
        haystack = str(text or "").lower()
        count = 0
        for keyword in keywords:
            token = str(keyword or "").strip().lower()
            if token and token in haystack:
                count += 1
        return count

    @staticmethod
    def _normalize_rejection_type(value: str) -> str:
        candidate = str(value or "").strip().lower()
        allowed = {
            "entity_mismatch",
            "intent_mismatch",
            "historical_negative",
            "low_signal",
            "duplicate",
            "other",
        }
        return candidate if candidate in allowed else "other"

    @staticmethod
    def _normalize_score(value: Any) -> float:
        try:
            score = float(value or 0.0)
        except Exception:
            return 0.0
        if score <= 0:
            return 0.0
        if score <= 1.0:
            return min(score, 1.0)
        return min(score / 5.0, 1.0)

    @staticmethod
    def _is_pharmacopeia_source(source_type: str) -> bool:
        text = str(source_type or "").strip().lower()
        return "药典" in text or "pharmacopeia" in text

    @staticmethod
    def _is_history_source(source_type: str) -> bool:
        text = str(source_type or "").strip().lower()
        return "history" in text or "meta_reflection" in text or "经验" in text

    @staticmethod
    def _task_requires_standard_evidence(task: Dict[str, Any]) -> bool:
        text = " ".join(
            [
                str(task.get("task_question", "") or ""),
                str(task.get("review_object", "") or ""),
                " ".join(str(item or "") for item in (task.get("rule_targets", []) or [])),
                " ".join(str(item or "") for item in (task.get("comparison_axes", []) or [])),
            ]
        )
        keywords = [
            "药典",
            "命名",
            "中文名",
            "英文名",
            "通用名",
            "化学结构",
            "分子式",
            "分子量",
            "立体结构",
            "参比制剂",
            "批准",
            "标准",
        ]
        return any(keyword in text for keyword in keywords)

    @classmethod
    def _classify_material_role(cls, source_type: str) -> str:
        text = str(source_type or "").strip().lower()
        if cls._is_history_source(text):
            return "experience"
        if cls._is_pharmacopeia_source(text) or any(
            keyword in text
            for keyword in ["ich", "guideline", "rule", "regulation", "法规", "指导原则", "法律", "标准", "要求"]
        ):
            return "rule"
        if any(
            keyword in text
            for keyword in ["submission", "source_doc", "section", "raw_text", "material", "申报", "原文", "章节"]
        ):
            return "fact"
        return "reference_info"

    @classmethod
    def _compute_comprehensive_score(
        cls,
        *,
        score: Any,
        target_match: int,
        intent_match: int,
        source_type: str,
    ) -> float:
        normalized_score = cls._normalize_score(score)
        signal_bonus = min(target_match, 2) * 0.12 + min(intent_match, 2) * 0.08
        material_role = cls._classify_material_role(source_type)
        role_bonus = 0.05 if material_role in {"rule", "fact"} else 0.0
        return round(min(normalized_score + signal_bonus + role_bonus, 1.0), 3)

    @classmethod
    def _build_classified_materials(cls, approved_materials: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        buckets: Dict[str, List[Dict[str, Any]]] = {
            "rule_materials": [],
            "reference_materials": [],
            "reference_info_materials": [],
            "fact_materials": [],
            "experience_materials": [],
        }
        for item in approved_materials:
            if not isinstance(item, dict):
                continue
            role = cls._classify_material_role(str(item.get("source_type", "") or ""))
            bucket_key = {
                "rule": "rule_materials",
                "reference_info": "reference_info_materials",
                "fact": "fact_materials",
                "experience": "experience_materials",
            }.get(role, "reference_info_materials")
            entry = {
                "evidence_id": str(item.get("evidence_id", "") or "").strip(),
                "doc_id": str(item.get("doc_id", "") or "").strip(),
                "chunk_id": str(item.get("chunk_id", "") or "").strip(),
                "source_type": str(item.get("source_type", "") or "").strip(),
                "title": str(item.get("title", "") or item.get("file_name", "") or "").strip(),
                "material_role": role,
                "retrieval_type": role,
            }
            buckets[bucket_key].append(entry)
            if role == "reference_info":
                buckets["reference_materials"].append(dict(entry))
        return buckets

    @staticmethod
    def _summarize_experience(values: Any, limit: int = 4) -> List[str]:
        out: List[str] = []
        seen: Set[str] = set()
        if not isinstance(values, list):
            return out
        for item in values:
            if isinstance(item, dict):
                text = str(item.get("content", "") or item.get("summary", "") or item.get("experience", "") or "").strip()
            else:
                text = str(item or "").strip()
            compact = RetrievalEvaluatorAgent._compact_text(text, max_len=120)
            if not compact or compact in seen:
                continue
            seen.add(compact)
            out.append(compact)
            if len(out) >= limit:
                break
        return out

    def _build_prompt_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        task_definition = payload.get("task_definition", {}) if isinstance(payload.get("task_definition", {}), dict) else {}
        section_review_profile = payload.get("section_review_profile", {}) if isinstance(payload.get("section_review_profile", {}), dict) else {}
        review_tasks: List[Dict[str, Any]] = []
        source_tasks = payload.get("review_tasks", []) if isinstance(payload.get("review_tasks", []), list) else []
        for item in source_tasks[:8]:
            if not isinstance(item, dict):
                continue
            review_tasks.append(
                {
                    "task_code": str(item.get("task_code", "") or "").strip(),
                    "task_question": str(item.get("task_question", "") or "").strip(),
                    "review_object": str(item.get("review_object", "") or "").strip(),
                    "required_rule_types": self._safe_text_list(item.get("required_rule_types", []))[:3],
                    "reference_targets": self._safe_text_list(item.get("reference_targets", []))[:4],
                    "comparison_axes": self._safe_text_list(item.get("comparison_axes", []))[:4],
                    "expected_evidence": self._safe_text_list(item.get("expected_evidence", []))[:4],
                    "completion_criteria": self._safe_text_list(item.get("completion_criteria", []))[:4],
                }
            )
        retrieved_materials: List[Dict[str, Any]] = []
        source_materials = payload.get("retrieved_materials", []) if isinstance(payload.get("retrieved_materials", []), list) else []
        for item in source_materials[:14]:
            if not isinstance(item, dict):
                continue
            retrieved_materials.append(
                {
                    "evidence_id": self._material_evidence_id(item),
                    "source_type": str(item.get("source_type", "") or "").strip(),
                    "title": self._compact_text(str(item.get("title", "") or item.get("file_name", "") or ""), max_len=72),
                    "content": self._compact_text(str(item.get("content", "") or ""), max_len=180),
                    "score": self._normalize_score(item.get("score", item.get("vector_score", item.get("comprehensive_score", 0.0)))),
                }
            )
        negative_history: List[Dict[str, str]] = []
        source_history = payload.get("historical_bad_retrievals", []) if isinstance(payload.get("historical_bad_retrievals", []), list) else []
        for item in source_history[:8]:
            if not isinstance(item, dict):
                continue
            negative_history.append(
                {
                    "evidence_id": str(item.get("evidence_id", "") or "").strip(),
                    "verdict": str(item.get("verdict", "") or item.get("label", "") or "").strip(),
                    "reason": self._compact_text(str(item.get("reason", "") or item.get("feedback_text", "") or ""), max_len=120),
                }
            )
        return {
            "task_id": str(payload.get("task_id", "") or "").strip(),
            "section_id": str(payload.get("section_id", "") or "").strip(),
            "section_name": str(payload.get("section_name", "") or "").strip(),
            "raw_text": str(payload.get("raw_text", "") or ""),
            "section_review_profile": {
                "chapter_role": str(section_review_profile.get("chapter_role", "") or "").strip(),
                "core_review_question": str(section_review_profile.get("core_review_question", "") or "").strip(),
                "must_answer_questions": self._safe_text_list(section_review_profile.get("must_answer_questions", []))[:6],
                "reviewer_mindset": self._safe_text_list(section_review_profile.get("reviewer_mindset", []))[:4],
                "evidence_judgment_rules": self._safe_text_list(section_review_profile.get("evidence_judgment_rules", []))[:4],
            },
            "task_definition": {
                "chapter_role": str(task_definition.get("chapter_role", "") or "").strip(),
                "core_review_question": str(task_definition.get("core_review_question", "") or task_definition.get("review_goal", "") or "").strip(),
                "must_answer_questions": self._safe_text_list(task_definition.get("must_answer_questions", []))[:6],
                "evidence_judgment_rules": self._safe_text_list(task_definition.get("evidence_judgment_rules", []))[:4],
            },
            "review_tasks": review_tasks,
            "retrieval_blueprint": payload.get("retrieval_blueprint", {}) if isinstance(payload.get("retrieval_blueprint", {}), dict) else {},
            "evidence_requirements": self._safe_text_list(payload.get("evidence_requirements", []))[:6],
            "retrieved_materials": retrieved_materials,
            "historical_bad_retrievals": negative_history,
            "historical_lessons": self._summarize_experience(payload.get("historical_experience", []), limit=4),
        }

    def describe(self) -> Dict[str, Any]:
        return {
            "name": "retrieval_evaluator",
            "inputs": [
                "task_id",
                "section_id",
                "section_name",
                "raw_text",
                "focus_points",
                "section_rules",
                "retrieved_materials",
                "historical_bad_retrievals",
                "historical_experience",
            ],
            "outputs": [
                "approved_evidence_ids",
                "rejected_evidence_ids",
                "approved_materials",
                "rejected_materials",
                "classified_materials",
                "coverage_by_task",
                "evidence_bundles_by_task",
                "missing_evidence_by_task",
                "judgment_ready",
                "source_breakdown",
                "rejection_breakdown",
                "confidence",
            ],
        }

    def _fallback_evaluate(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        section_id = str(payload.get("section_id", "") or "").strip()
        section_name = str(payload.get("section_name", "") or "").strip()
        raw_text = str(payload.get("raw_text", "") or "").strip()
        focus_points = self._safe_text_list(payload.get("focus_points", []))
        section_rules = self._safe_text_list(payload.get("section_rules", []))
        retrieved_materials = [
            dict(item)
            for item in self._safe_list(payload.get("retrieved_materials", []))
            if isinstance(item, dict)
        ]
        historical_bad_retrievals = [
            dict(item)
            for item in self._safe_list(payload.get("historical_bad_retrievals", []))
            if isinstance(item, dict)
        ]

        target_terms = self._extract_target_terms(raw_text)
        intent_keywords = self._extract_intent_keywords(section_name, focus_points, section_rules)
        negative_ids = self._collect_negative_evidence_ids(historical_bad_retrievals)

        approved_materials: List[Dict[str, Any]] = []
        rejected_materials: List[Dict[str, Any]] = []
        source_breakdown: Dict[str, int] = {}
        rejection_breakdown: Dict[str, int] = {}
        seen_evidence_ids: Set[str] = set()

        for item in retrieved_materials:
            evidence_id = self._material_evidence_id(item)
            if not evidence_id:
                continue

            source_type = str(item.get("source_type", "") or "").strip()
            score = float(item.get("score", 0.0) or 0.0)
            content = " ".join(
                [
                    str(item.get("title", "") or ""),
                    str(item.get("file_name", "") or ""),
                    str(item.get("content", "") or ""),
                    str(item.get("content_preview", "") or ""),
                    str(item.get("context_snippet", "") or ""),
                ]
            ).strip()
            target_match = self._match_count(content, target_terms) if target_terms else 0
            intent_match = self._match_count(content, intent_keywords)
            material_role = self._classify_material_role(source_type)
            comprehensive_score = self._compute_comprehensive_score(
                score=score,
                target_match=target_match,
                intent_match=intent_match,
                source_type=source_type,
            )

            reject_type = ""
            reject_reason = ""
            if evidence_id in seen_evidence_ids:
                reject_type = "duplicate"
                reject_reason = "duplicate evidence id"
            elif evidence_id in negative_ids:
                reject_type = "historical_negative"
                reject_reason = "rejected by historical negative feedback"
            elif self._is_pharmacopeia_source(source_type) and target_terms and target_match <= 0:
                reject_type = "entity_mismatch"
                reject_reason = "pharmacopeia evidence does not match the section target entity"
            elif comprehensive_score < 0.5:
                reject_type = "low_signal"
                reject_reason = f"comprehensive_score={comprehensive_score:.3f} < 0.500, filtered before downstream reasoning"

            if reject_type:
                reject_type = self._normalize_rejection_type(reject_type)
                rejection_breakdown[reject_type] = int(rejection_breakdown.get(reject_type, 0)) + 1
                rejected_materials.append(
                    {
                        "evidence_id": evidence_id,
                        "doc_id": str(item.get("doc_id", "") or "").strip(),
                        "chunk_id": str(item.get("chunk_id", "") or "").strip(),
                        "source_type": source_type,
                        "material_role": material_role,
                        "retrieval_type": material_role,
                        "comprehensive_score": comprehensive_score,
                        "reject_type": reject_type,
                        "reject_reason": reject_reason,
                    }
                )
                seen_evidence_ids.add(evidence_id)
                continue

            keep_reason_parts: List[str] = []
            if target_match > 0:
                keep_reason_parts.append("matched section target entity")
            if intent_match > 0:
                keep_reason_parts.append("matched section review intent")
            if material_role == "rule":
                keep_reason_parts.append("classified as normative rule evidence")
            elif material_role == "reference_info":
                keep_reason_parts.append("classified as related reference information")
            elif material_role == "fact":
                keep_reason_parts.append("classified as section fact evidence")
            elif material_role == "experience":
                keep_reason_parts.append("classified as historical experience evidence")
            if not keep_reason_parts:
                keep_reason_parts.append("retained as the strongest remaining candidate")

            approved_materials.append(
                {
                    "evidence_id": evidence_id,
                    "doc_id": str(item.get("doc_id", "") or "").strip(),
                    "chunk_id": str(item.get("chunk_id", "") or "").strip(),
                    "source_type": source_type,
                    "material_role": material_role,
                    "retrieval_type": material_role,
                    "comprehensive_score": comprehensive_score,
                    "keep_reason": "; ".join(keep_reason_parts),
                    "relevance": "high" if comprehensive_score >= 0.8 else ("medium" if comprehensive_score >= 0.65 else "low"),
                }
            )
            seen_evidence_ids.add(evidence_id)
            bucket = source_type or "unknown"
            source_breakdown[bucket] = int(source_breakdown.get(bucket, 0)) + 1

        review_tasks = [
            dict(item)
            for item in payload.get("review_tasks", [])
            if isinstance(payload.get("review_tasks", []), list) and isinstance(item, dict)
        ]
        approved_ids = [
            item["evidence_id"]
            for item in approved_materials
            if str(item.get("evidence_id", "") or "").strip()
        ]
        classified_materials = self._build_classified_materials(approved_materials)
        coverage_by_task: Dict[str, Dict[str, Any]] = {}
        evidence_bundles_by_task: Dict[str, Dict[str, Any]] = {}
        missing_evidence_by_task: List[Dict[str, Any]] = []

        material_lookup = {
            str(item.get("evidence_id", "") or "").strip(): item
            for item in approved_materials
            if isinstance(item, dict) and str(item.get("evidence_id", "") or "").strip()
        }

        for index, task in enumerate(review_tasks, start=1):
            task_code = str(task.get("task_code", "") or f"task_{index}").strip() or f"task_{index}"
            expected_evidence = self._safe_text_list(task.get("expected_evidence", []))
            task_text = " ".join(
                [
                    str(task.get("task_question", "") or ""),
                    str(task.get("review_object", "") or ""),
                    " ".join(str(item or "") for item in task.get("comparison_axes", []) if item),
                ]
            )
            task_keywords = self._extract_intent_keywords(task_text, [], [])[:8]
            task_specific_subset: List[str] = []
            task_standard_subset: List[str] = []
            task_rule_subset: List[str] = []
            task_reference_subset: List[str] = []
            task_fact_subset: List[str] = []
            task_experience_subset: List[str] = []

            for evidence_id, approved in material_lookup.items():
                source_type = str(approved.get("source_type", "") or "").strip()
                material_role = str(approved.get("material_role", "") or "").strip() or self._classify_material_role(source_type)
                material = next((row for row in retrieved_materials if self._material_evidence_id(row) == evidence_id), {})
                content = " ".join(
                    [
                        str(material.get("title", "") or ""),
                        str(material.get("content", "") or ""),
                        str(material.get("content_preview", "") or ""),
                        str(material.get("context_snippet", "") or ""),
                    ]
                )
                if not task_keywords or self._match_count(content, task_keywords) > 0:
                    task_specific_subset.append(evidence_id)
                    if material_role == "rule":
                        task_rule_subset.append(evidence_id)
                    elif material_role == "fact":
                        task_fact_subset.append(evidence_id)
                    elif material_role == "experience":
                        task_experience_subset.append(evidence_id)
                    else:
                        task_reference_subset.append(evidence_id)
                if material_role in {"rule", "reference_info"}:
                    task_standard_subset.append(evidence_id)

            approved_subset = task_specific_subset[: min(3, len(task_specific_subset))] or approved_ids[: min(3, len(approved_ids))]
            missing_information: List[str] = []
            requires_standard_evidence = self._task_requires_standard_evidence(task)
            if not approved_subset:
                missing_information.extend(expected_evidence[:2] or ["missing direct supporting evidence for this task"])
            if requires_standard_evidence and not task_standard_subset:
                missing_information.append("missing normative rule or reference information evidence")

            coverage_status = "covered"
            if missing_information and approved_subset:
                coverage_status = "partial"
            elif not approved_subset:
                coverage_status = "missing"

            coverage_by_task[task_code] = {
                "task_code": task_code,
                "coverage_status": coverage_status,
                "approved_evidence_ids": approved_subset,
                "rule_evidence_ids": task_rule_subset[:3],
                "reference_evidence_ids": task_reference_subset[:3],
                "fact_evidence_ids": task_fact_subset[:3],
                "experience_evidence_ids": task_experience_subset[:3],
                "expected_evidence": expected_evidence[:4],
            }
            evidence_bundles_by_task[task_code] = {
                "task_code": task_code,
                "required_rule_types": self._safe_text_list(task.get("required_rule_types", [])),
                "reference_targets": self._safe_text_list(task.get("reference_targets", [])),
                "supporting_evidence_ids": approved_subset,
                "rule_evidence_ids": task_rule_subset[:3],
                "reference_evidence_ids": task_reference_subset[:3],
                "fact_evidence_ids": task_fact_subset[:3],
                "experience_evidence_ids": task_experience_subset[:3],
            }
            if missing_information:
                missing_evidence_by_task.append(
                    {
                        "task_code": task_code,
                        "missing_information": missing_information,
                    }
                )

        return {
            "section_id": section_id,
            "evaluation_summary": f"approved {len(approved_materials)} materials and rejected {len(rejected_materials)} materials",
            "approved_evidence_ids": [item["evidence_id"] for item in approved_materials if str(item.get("evidence_id", "") or "").strip()],
            "rejected_evidence_ids": [item["evidence_id"] for item in rejected_materials if str(item.get("evidence_id", "") or "").strip()],
            "approved_materials": approved_materials,
            "rejected_materials": rejected_materials,
            "classified_materials": classified_materials,
            "coverage_by_task": coverage_by_task,
            "evidence_bundles_by_task": evidence_bundles_by_task,
            "missing_evidence_by_task": missing_evidence_by_task,
            "judgment_ready": bool(approved_materials) and not any(
                str(item.get("coverage_status", "") or "") == "missing" for item in coverage_by_task.values()
            ),
            "source_breakdown": source_breakdown,
            "rejection_breakdown": rejection_breakdown,
            "confidence": "medium",
        }

    def evaluate(self, payload: Dict[str, Any], prompt_config: Dict[str, Any] | None = None) -> Dict[str, Any]:
        prompt_payload = self._build_prompt_payload(payload)
        prompt = self.prompts.render(
            "retrieval_evaluator.j2",
            prompt_payload,
            prompt_config=prompt_config or {},
        )
        default_data = self._fallback_evaluate(payload)
        raw = self.llm.chat(
            messages=self.envelopes.build("retrieval_evaluator", prompt),
            default=json.dumps(default_data, ensure_ascii=False),
        )
        parsed = self.llm.extract_json(raw)
        if not isinstance(parsed, dict):
            raise LLMExecutionError('model_invalid_json', stage='retrieval_evaluator')
        if (not isinstance(parsed.get('evaluation_summary'), str) or not parsed['evaluation_summary'].strip()
                or any(not isinstance(parsed.get(key), list) or any(not isinstance(v, str) for v in parsed[key])
                       for key in ('approved_evidence_ids', 'rejected_evidence_ids'))
                or any(not isinstance(parsed.get(key), list) or any(not isinstance(v, dict)
                           or not isinstance(v.get('evidence_id'), str) or not v['evidence_id'].strip() for v in parsed[key])
                       for key in ('approved_materials', 'rejected_materials'))):
            raise LLMExecutionError('model_invalid_output', stage='retrieval_evaluator')
        llm_execution = self._build_llm_execution_meta(
            agent_name="retrieval_evaluator",
            raw_text=raw,
            parsed_payload=parsed,
        )
        data = parsed
        data["llm_execution"] = llm_execution
        data.setdefault("section_id", str(payload.get("section_id", "") or "").strip())
        data["approved_evidence_ids"] = self._safe_text_list(data.get("approved_evidence_ids", []))
        data["rejected_evidence_ids"] = self._safe_text_list(data.get("rejected_evidence_ids", []))
        data["approved_materials"] = [
            dict(item) for item in self._safe_list(data.get("approved_materials", [])) if isinstance(item, dict)
        ]
        data["rejected_materials"] = [
            dict(item) for item in self._safe_list(data.get("rejected_materials", [])) if isinstance(item, dict)
        ]
        for item in data["approved_materials"]:
            item["material_role"] = str(
                item.get("material_role", "") or self._classify_material_role(str(item.get("source_type", "") or ""))
            ).strip()
            item["retrieval_type"] = str(item.get("retrieval_type", "") or item["material_role"]).strip()
            item["comprehensive_score"] = float(item.get("comprehensive_score", 0.0) or 0.0)
        for item in data["rejected_materials"]:
            item["material_role"] = str(
                item.get("material_role", "") or self._classify_material_role(str(item.get("source_type", "") or ""))
            ).strip()
            item["retrieval_type"] = str(item.get("retrieval_type", "") or item["material_role"]).strip()
            item["comprehensive_score"] = float(item.get("comprehensive_score", 0.0) or 0.0)
        data["source_breakdown"] = (
            dict(data.get("source_breakdown", {}))
            if isinstance(data.get("source_breakdown", {}), dict)
            else {}
        )
        data["classified_materials"] = (
            dict(data.get("classified_materials", {}))
            if isinstance(data.get("classified_materials", {}), dict)
            else {}
        )
        if "reference_info_materials" not in data["classified_materials"] and "reference_materials" in data["classified_materials"]:
            data["classified_materials"]["reference_info_materials"] = list(data["classified_materials"].get("reference_materials", []))
        if "reference_materials" not in data["classified_materials"] and "reference_info_materials" in data["classified_materials"]:
            data["classified_materials"]["reference_materials"] = list(data["classified_materials"].get("reference_info_materials", []))
        data["coverage_by_task"] = (
            dict(data.get("coverage_by_task", {}))
            if isinstance(data.get("coverage_by_task", {}), dict)
            else {}
        )
        data["evidence_bundles_by_task"] = (
            dict(data.get("evidence_bundles_by_task", {}))
            if isinstance(data.get("evidence_bundles_by_task", {}), dict)
            else {}
        )
        data["missing_evidence_by_task"] = [
            dict(item)
            for item in data.get("missing_evidence_by_task", [])
            if isinstance(data.get("missing_evidence_by_task", []), list) and isinstance(item, dict)
        ]
        data["judgment_ready"] = bool(data.get("judgment_ready", False))
        data["rejection_breakdown"] = (
            dict(data.get("rejection_breakdown", {}))
            if isinstance(data.get("rejection_breakdown", {}), dict)
            else {}
        )
        confidence = str(data.get("confidence", "medium") or "medium").strip().lower()
        data["confidence"] = confidence if confidence in {"low", "medium", "high"} else "medium"
        data["evaluation_summary"] = self._compact_text(
            data.get("evaluation_summary", default_data.get("evaluation_summary", "")),
            max_len=240,
        )
        # 成功评价后明确不采纳任何依据也是有效结果，不能改用兜底重新引入被拒绝资料。
        return data
