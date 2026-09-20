from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from sqlalchemy import desc

from agent.agent_backend.database.mysql.db_model import (
    PreReviewExperienceMemory,
    PreReviewFeedback,
    PreReviewFeedbackAnalysisResult,
    PreReviewRun,
    PreReviewSectionExample,
)

if TYPE_CHECKING:
    from agent.agent_backend.services.pre_review_service import PreReviewService


class MemoryGovernanceService:
    """
    Govern short-term/long-term and shared/private memory usage.

    This service does not introduce new business fields. It only centralizes
    admission, ranking, and write rules for existing memory assets.
    """

    GOVERNANCE_VERSION = "memory_governance_v1"
    ALLOWED_EXPERIENCE_TYPES = {
        "review_rule",
        "query_rule",
        "risk_pattern",
        "wording_rule",
        "meta_reflection",
        "rule_knowledge",
        "fact_knowledge",
        "retrieval_knowledge",
        "task_knowledge",
        "reasoning_knowledge",
    }
    NEGATIVE_EVIDENCE_VERDICTS = {"incorrect", "partial", "rejected"}
    EXAMPLE_TYPES_FOR_PROMPT = {"reference", "few_shot"}
    EXPERIENCE_SCOPE_PRIORITY = {
        "section": 300,
        "project": 200,
        "product_type": 100,
        "global": 50,
    }
    EXAMPLE_TYPE_PRIORITY = {
        "few_shot": 300,
        "reference": 200,
        "evaluation_case": 100,
    }
    FEEDBACK_MEMORY_POLICY = {
        "valid": {"memory_type": "episodic", "memory_scope": "short_term_private"},
        "false_positive": {"memory_type": "working", "memory_scope": "short_term_private"},
        "missed": {"memory_type": "semantic", "memory_scope": "short_term_shared"},
    }

    def __init__(self, service: "PreReviewService") -> None:
        self.service = service

    def seed_project_feedback_memory(self, project_id: str, limit: int = 200) -> None:
        project_key = str(project_id or "").strip()
        if not project_key:
            return
        session = self.service.db_conn.get_session()
        try:
            rows = (
                session.query(PreReviewFeedback, PreReviewRun)
                .join(PreReviewRun, PreReviewFeedback.run_id == PreReviewRun.run_id)
                .filter(PreReviewRun.project_id == project_key)
                .order_by(desc(PreReviewFeedback.id))
                .limit(max(1, int(limit or 200)))
                .all()
            )
            for feedback_row, run_row in rows:
                feedback_type = str(getattr(feedback_row, "feedback_type", "") or "").strip().lower()
                policy = self.FEEDBACK_MEMORY_POLICY.get(feedback_type, self.FEEDBACK_MEMORY_POLICY["valid"])
                feedback_text = str(getattr(feedback_row, "feedback_text", "") or "").strip()
                suggestion = str(getattr(feedback_row, "suggestion", "") or "").strip()
                note = f"{feedback_type} | {feedback_text} | {suggestion}".strip(" |")
                if not note:
                    continue
                self.service.memory_tool.remember(
                    key=f"fb:{getattr(run_row, 'run_id', '')}:{getattr(feedback_row, 'id', '')}",
                    value=note,
                    memory_type=policy["memory_type"],
                    metadata={
                        "source": "historical_feedback",
                        "memory_scope": "short_term_shared",
                        "governance_version": self.GOVERNANCE_VERSION,
                        "project_id": project_key,
                        "run_id": str(getattr(run_row, "run_id", "") or "").strip(),
                        "section_id": str(getattr(feedback_row, "section_id", "") or "").strip(),
                        "operator": str(getattr(feedback_row, "operator", "") or "").strip(),
                        "feedback_type": feedback_type,
                    },
                )
        finally:
            session.close()

    def remember_submission_outline(
        self,
        *,
        project_id: str,
        source_doc_id: str,
        review_units: List[Dict[str, Any]],
    ) -> None:
        for unit in review_units or []:
            if not isinstance(unit, dict):
                continue
            section_id = str(unit.get("section_id") or unit.get("chunk_id") or "").strip()
            if not section_id:
                continue
            skeleton = {
                "section_id": section_id,
                "section_code": str(unit.get("section_code", "") or "").strip(),
                "section_name": str(unit.get("section_name", "") or "").strip(),
                "parent_code": str(unit.get("parent_code", "") or "").strip(),
                "unit_type": str(unit.get("unit_type", "") or "").strip(),
                "unit_order": unit.get("unit_order"),
                "title_path": [str(x).strip() for x in (unit.get("title_path") or []) if str(x).strip()],
                "page_start": unit.get("page_start"),
                "page_end": unit.get("page_end"),
            }
            self.service.memory_tool.remember(
                key=f"outline:{source_doc_id}:{section_id}",
                value=json.dumps(skeleton, ensure_ascii=False),
                memory_type="semantic",
                metadata={
                    "source": "submission_outline",
                    "memory_scope": "short_term_shared",
                    "governance_version": self.GOVERNANCE_VERSION,
                    "project_id": project_id,
                    "doc_id": source_doc_id,
                    "section_id": section_id,
                },
            )

    def remember_recent_review_conclusion(
        self,
        *,
        project_id: str,
        source_doc_id: str,
        section_id: str,
        conclusion: str,
    ) -> None:
        text = str(conclusion or "").strip()
        if not text:
            return
        self.service.memory_tool.remember(
            key=f"recent:{source_doc_id}:{section_id}",
            value=text,
            memory_type="working",
            metadata={
                "source": "recent_finding",
                "memory_scope": "short_term_private",
                "governance_version": self.GOVERNANCE_VERSION,
                "project_id": project_id,
                "doc_id": source_doc_id,
                "section_id": section_id,
            },
        )

    def remember_feedback_event(
        self,
        *,
        run_id: str,
        section_id: str,
        feedback_type: str,
        feedback_text: str,
        suggestion: str,
        operator: str,
    ) -> None:
        note = f"{feedback_type} | {feedback_text or ''} | {suggestion or ''}".strip(" |")
        if not note:
            return
        normalized_type = str(feedback_type or "").strip().lower()
        policy = self.FEEDBACK_MEMORY_POLICY.get(normalized_type, self.FEEDBACK_MEMORY_POLICY["valid"])
        self.service.memory_tool.remember(
            key=f"feedback:{run_id}:{section_id or 'global'}:{uuid.uuid4().hex[:8]}",
            value=note,
            memory_type=policy["memory_type"],
            metadata={
                "source": "online_feedback",
                "memory_scope": policy["memory_scope"],
                "governance_version": self.GOVERNANCE_VERSION,
                "run_id": run_id,
                "section_id": section_id or "",
                "operator": operator or "",
                "feedback_type": normalized_type,
            },
        )

    def load_historical_experience(
        self,
        session,
        *,
        project_id: str,
        section_id: str,
        product_type: str,
        limit: int = 12,
    ) -> List[Dict[str, Any]]:
        rows = (
            session.query(PreReviewExperienceMemory)
            .filter(PreReviewExperienceMemory.status == "active")
            .order_by(PreReviewExperienceMemory.id.desc())
            .all()
        )
        candidates: List[tuple[float, Dict[str, Any]]] = []
        section_key = str(section_id or "").strip()
        project_key = str(project_id or "").strip()
        product_key = str(product_type or "").strip()
        for row in rows:
            if not self._is_experience_admissible(row, section_key=section_key, project_key=project_key, product_key=product_key):
                continue
            candidates.append((self._score_experience(row), self._normalize_experience(row, section_key=section_key)))
        candidates.sort(key=lambda item: item[0], reverse=True)
        return [item for _, item in candidates[: max(1, int(limit or 12))]]

    def load_historical_bad_retrievals(
        self,
        session,
        *,
        project_id: str,
        section_id: str,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        project_key = str(project_id or "").strip()
        section_key = str(section_id or "").strip()
        if not project_key or not section_key:
            return []
        rows = (
            session.query(PreReviewFeedbackAnalysisResult)
            .join(PreReviewRun, PreReviewFeedbackAnalysisResult.run_id == PreReviewRun.run_id)
            .filter(
                PreReviewRun.project_id == project_key,
                PreReviewFeedbackAnalysisResult.section_id == section_key,
            )
            .order_by(PreReviewFeedbackAnalysisResult.id.desc())
            .all()
        )
        out: List[Dict[str, Any]] = []
        seen = set()
        for row in rows:
            try:
                payload = json.loads(getattr(row, "analysis_json", "") or "{}")
            except Exception:
                payload = {}
            if not isinstance(payload, dict):
                continue
            meta = self.service._extract_feedback_meta(payload)
            retrieval_feedback = str(meta.get("retrieval_feedback", "") or "").strip().lower()
            primary_error_type = str(payload.get("primary_error_type", "") or "").strip()
            evidence_feedback = meta.get("evidence_feedback", []) if isinstance(meta.get("evidence_feedback", []), list) else []
            explicit_negative_added = False
            for item in evidence_feedback:
                normalized = self._normalize_bad_retrieval_item(item, row=row, primary_error_type=primary_error_type, retrieval_feedback=retrieval_feedback)
                if not normalized:
                    continue
                evidence_id = str(normalized.get("evidence_id", "") or "").strip()
                if evidence_id and evidence_id in seen:
                    continue
                if evidence_id:
                    seen.add(evidence_id)
                explicit_negative_added = True
                out.append(normalized)
                if len(out) >= limit:
                    return out
            if explicit_negative_added or retrieval_feedback not in {"incorrect", "partial"}:
                continue
            summary_key = f"summary:{getattr(row, 'feedback_key', '')}"
            if summary_key in seen:
                continue
            seen.add(summary_key)
            out.append(
                {
                    "feedback_key": str(getattr(row, "feedback_key", "") or "").strip(),
                    "run_id": str(getattr(row, "run_id", "") or "").strip(),
                    "section_id": str(getattr(row, "section_id", "") or "").strip(),
                    "evidence_id": "",
                    "doc_id": "",
                    "chunk_id": "",
                    "verdict": retrieval_feedback,
                    "reason": primary_error_type or retrieval_feedback or "historical_negative",
                    "retrieval_feedback": retrieval_feedback,
                    "memory_scope": "long_term_private",
                }
            )
            if len(out) >= limit:
                return out
        return out

    def load_section_reference_examples(
        self,
        session,
        *,
        project_id: str,
        section_id: str,
        limit: int = 3,
    ) -> List[Dict[str, Any]]:
        project_key = str(project_id or "").strip()
        section_key = str(section_id or "").strip()
        if not project_key or not section_key:
            return []
        rows = (
            session.query(PreReviewSectionExample)
            .filter(
                PreReviewSectionExample.project_id == project_key,
                PreReviewSectionExample.section_id == section_key,
                PreReviewSectionExample.is_active == 1,
                PreReviewSectionExample.example_type.in_(list(self.EXAMPLE_TYPES_FOR_PROMPT)),
            )
            .order_by(PreReviewSectionExample.update_time.desc(), PreReviewSectionExample.id.desc())
            .all()
        )
        ranked: List[tuple[float, Dict[str, Any]]] = []
        for row in rows:
            if not self._is_example_admissible(row):
                continue
            ranked.append((self._score_example(row), self.service._normalize_example_record(row)))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [item for _, item in ranked[: max(1, int(limit or 3))]]

    def _is_experience_admissible(self, row: Any, *, section_key: str, project_key: str, product_key: str) -> bool:
        content = str(getattr(row, "content", "") or "").strip()
        if not content:
            return False
        experience_type = str(getattr(row, "experience_type", "") or "").strip() or "review_rule"
        if experience_type not in self.ALLOWED_EXPERIENCE_TYPES:
            return False
        scope_type = str(getattr(row, "scope_type", "") or "").strip()
        scope_key = str(getattr(row, "scope_key", "") or "").strip()
        if scope_type == "section":
            return scope_key == section_key
        if scope_type == "project":
            return scope_key == project_key
        if scope_type == "product_type":
            return scope_key == product_key
        if scope_type == "global":
            return True
        return False

    def _score_experience(self, row: Any) -> float:
        scope_type = str(getattr(row, "scope_type", "") or "").strip()
        usage_count = int(getattr(row, "usage_count", 0) or 0)
        success_count = int(getattr(row, "success_count", 0) or 0)
        success_ratio = (success_count / usage_count) if usage_count > 0 else 0.0
        row_id = int(getattr(row, "id", 0) or 0)
        return (
            float(self.EXPERIENCE_SCOPE_PRIORITY.get(scope_type, 0))
            + min(float(usage_count), 20.0)
            + success_ratio * 20.0
            + min(row_id / 1000000.0, 10.0)
        )

    def _normalize_experience(self, row: Any, *, section_key: str) -> Dict[str, Any]:
        payload = {}
        try:
            payload = json.loads(getattr(row, "payload_json", "") or "{}")
        except Exception:
            payload = {}
        scope_type = str(getattr(row, "scope_type", "") or "").strip()
        scope_key = str(getattr(row, "scope_key", "") or "").strip()
        payload_dict = payload if isinstance(payload, dict) else {}
        return {
            "experience_id": str(getattr(row, "experience_id", "") or "").strip(),
            "experience_type": str(getattr(row, "experience_type", "") or "").strip(),
            "content": str(getattr(row, "content", "") or "").strip(),
            "applicable_sections": [section_key] if scope_type == "section" else [],
            "scope_type": scope_type,
            "scope_key": scope_key,
            "trigger_conditions": self.service._parse_json_list(getattr(row, "trigger_conditions", "") or ""),
            "knowledge_category": str(payload_dict.get("knowledge_category", "") or "").strip(),
            "optimization_target": str(payload_dict.get("optimization_target", "") or "").strip(),
            "source_error_type": str(payload_dict.get("source_error_type", "") or "").strip(),
            "payload": payload_dict,
        }

    def _normalize_bad_retrieval_item(
        self,
        item: Any,
        *,
        row: Any,
        primary_error_type: str,
        retrieval_feedback: str,
    ) -> Dict[str, Any]:
        if not isinstance(item, dict):
            return {}
        verdict = str(item.get("verdict", "") or item.get("label", "") or "").strip().lower()
        if verdict not in self.NEGATIVE_EVIDENCE_VERDICTS:
            return {}
        doc_id = str(item.get("doc_id", "") or "").strip()
        chunk_id = str(item.get("chunk_id", "") or "").strip()
        evidence_id = str(item.get("evidence_id", "") or "").strip() or f"{doc_id}:{chunk_id}"
        if not evidence_id.strip():
            return {}
        return {
            "feedback_key": str(getattr(row, "feedback_key", "") or "").strip(),
            "run_id": str(getattr(row, "run_id", "") or "").strip(),
            "section_id": str(getattr(row, "section_id", "") or "").strip(),
            "evidence_id": evidence_id,
            "doc_id": doc_id,
            "chunk_id": chunk_id,
            "verdict": verdict,
            "reason": str(item.get("reason", "") or primary_error_type or retrieval_feedback or "historical_negative").strip(),
            "retrieval_feedback": retrieval_feedback,
            "memory_scope": "long_term_private",
        }

    def _is_example_admissible(self, row: Any) -> bool:
        content = str(getattr(row, "content", "") or "").strip()
        if not content:
            return False
        example_type = str(getattr(row, "example_type", "") or "").strip()
        if example_type not in self.EXAMPLE_TYPES_FOR_PROMPT:
            return False
        payload = {}
        input_payload = {}
        output_payload = {}
        try:
            payload = json.loads(getattr(row, "payload_json", "") or "{}")
        except Exception:
            payload = {}
        try:
            input_payload = json.loads(getattr(row, "input_json", "") or "{}")
        except Exception:
            input_payload = {}
        try:
            output_payload = json.loads(getattr(row, "output_json", "") or "{}")
        except Exception:
            output_payload = {}
        if bool((payload if isinstance(payload, dict) else {}).get("disabled_for_prompt", False)):
            return False
        if example_type == "few_shot":
            has_io = bool(input_payload) or bool(output_payload)
            if not has_io:
                return False
        return True

    def _score_example(self, row: Any) -> float:
        example_type = str(getattr(row, "example_type", "") or "").strip()
        row_id = int(getattr(row, "id", 0) or 0)
        source_feedback_key = str(getattr(row, "source_feedback_key", "") or "").strip()
        return (
            float(self.EXAMPLE_TYPE_PRIORITY.get(example_type, 0))
            + (20.0 if source_feedback_key else 0.0)
            + min(row_id / 1000000.0, 10.0)
        )
