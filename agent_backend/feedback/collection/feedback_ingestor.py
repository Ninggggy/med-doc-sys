from __future__ import annotations

from typing import Any, Dict, List

from agent.agent_backend.feedback.collection.diff_extractor import DiffExtractor
from agent.agent_backend.feedback.collection.feedback_validator import FeedbackValidator


class FeedbackIngestor:
    """Normalize and enrich feedback payloads before routing."""

    def __init__(self) -> None:
        self.validator = FeedbackValidator()
        self.diff_extractor = DiffExtractor()

    def ingest(self, feedback_payload: Dict[str, Any]) -> Dict[str, Any]:
        """Validate, normalize, and attach run context."""
        self.validator.validate(feedback_payload)
        normalized = self.normalize_feedback(feedback_payload)
        with_context = self.attach_run_context(normalized)
        diff = self.diff_extractor.extract_diff(
            str(with_context.get("original_output", "") or ""),
            str(with_context.get("revised_output", "") or ""),
        )
        with_context["diff_result"] = diff
        return with_context

    def normalize_feedback(self, feedback_payload: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize minimal feedback payload into stable dict fields."""
        decision = str(feedback_payload.get("decision", "") or "").strip().lower()
        labels = [str(x).strip().lower() for x in list(feedback_payload.get("labels", []) or []) if str(x).strip()]
        issue_feedback = self._normalize_issue_feedback(feedback_payload.get("issue_feedback", []))
        missing_item_feedback = self._normalize_missing_item_feedback(feedback_payload.get("missing_item_feedback", {}))
        signal_summary = self._derive_signal_summary(issue_feedback, missing_item_feedback)
        return {
            "run_id": str(feedback_payload.get("run_id", "") or ""),
            "section_id": str(feedback_payload.get("section_id", "") or ""),
            "decision": decision,
            "feedback_type": str(feedback_payload.get("feedback_type", "") or "").strip().lower(),
            "labels": labels,
            "chain_mode": str(feedback_payload.get("chain_mode", "") or "").strip() or "feedback_optimize",
            "manual_modified": bool(feedback_payload.get("manual_modified", False)),
            "conclusion_feedback": str(feedback_payload.get("conclusion_feedback", "") or "").strip().lower(),
            "retrieval_feedback": str(feedback_payload.get("retrieval_feedback", "") or "").strip().lower(),
            "issue_feedback": issue_feedback,
            "paragraph_feedback": list(feedback_payload.get("paragraph_feedback", []) or []),
            "evidence_feedback": list(feedback_payload.get("evidence_feedback", []) or []),
            "missing_item_feedback": missing_item_feedback,
            "signal_summary": signal_summary,
            "knowledge_feedback": self._build_signal_text(signal_summary.get("fact_issues", []), signal_summary.get("missing_items", [])),
            "rule_feedback": self._build_signal_text(signal_summary.get("rule_issues", []), []),
            "reasoning_feedback": self._build_signal_text(
                signal_summary.get("reasoning_issues", []) + signal_summary.get("conclusion_issues", []),
                signal_summary.get("missing_items", []),
            ),
            "reference_example": feedback_payload.get("reference_example", {}) if isinstance(feedback_payload.get("reference_example", {}), dict) else {},
            "original_output": feedback_payload.get("original_output", {}),
            "revised_output": feedback_payload.get("revised_output", {}),
            "feedback_text": str(feedback_payload.get("feedback_text", "") or ""),
            "suggestion": str(feedback_payload.get("suggestion", "") or ""),
            "operator": str(feedback_payload.get("operator", "") or ""),
        }

    def attach_run_context(self, feedback_payload: Dict[str, Any]) -> Dict[str, Any]:
        """Attach minimal context fields for downstream processors."""
        payload = dict(feedback_payload)
        payload["context"] = {
            "run_id": payload.get("run_id", ""),
            "section_id": payload.get("section_id", ""),
            "conclusion_feedback": payload.get("conclusion_feedback", ""),
            "retrieval_feedback": payload.get("retrieval_feedback", ""),
            "knowledge_feedback": payload.get("knowledge_feedback", ""),
            "rule_feedback": payload.get("rule_feedback", ""),
            "reasoning_feedback": payload.get("reasoning_feedback", ""),
            "issue_feedback": payload.get("issue_feedback", []) if isinstance(payload.get("issue_feedback", []), list) else [],
            "missing_item_feedback": payload.get("missing_item_feedback", {}) if isinstance(payload.get("missing_item_feedback", {}), dict) else {},
            "signal_summary": payload.get("signal_summary", {}) if isinstance(payload.get("signal_summary", {}), dict) else {},
            "reference_example": payload.get("reference_example", {}) if isinstance(payload.get("reference_example", {}), dict) else {},
        }
        return payload

    def persist_feedback(self, feedback_record: Dict[str, Any]) -> str:
        """Return a stable feedback identifier. Persistence is delegated upstream."""
        return f"{feedback_record.get('run_id', 'run')}:{feedback_record.get('section_id', 'global')}"

    @staticmethod
    def _normalize_field_feedback(value: Any) -> Dict[str, str]:
        if not isinstance(value, dict):
            return {}
        out: Dict[str, str] = {}
        for key in ("rule", "fact", "evidence", "reasoning", "conclusion"):
            text = str(value.get(key, "") or "").strip().lower()
            if text:
                out[key] = text
        return out

    def _normalize_issue_feedback(self, values: Any) -> List[Dict[str, Any]]:
        if not isinstance(values, list):
            return []
        out: List[Dict[str, Any]] = []
        for item in values:
            if not isinstance(item, dict):
                continue
            normalized = dict(item)
            normalized["issue_key"] = str(item.get("issue_key", "") or "").strip()
            normalized["feedback_kind"] = str(item.get("feedback_kind", "") or "").strip().lower()
            normalized["verdict"] = str(item.get("verdict", "") or "").strip().lower()
            normalized["feedback_text"] = str(item.get("feedback_text", "") or "").strip()
            normalized["error_reason"] = str(item.get("error_reason", "") or "").strip()
            normalized["task_code"] = str(item.get("task_code", "") or "").strip()
            normalized["task_question"] = str(item.get("task_question", "") or "").strip()
            normalized["task_status"] = str(item.get("task_status", "") or "").strip().lower()
            normalized["rule_code"] = str(item.get("rule_code", "") or "").strip()
            normalized["rule_text"] = str(item.get("rule_text", "") or "").strip()
            normalized["basis"] = str(item.get("basis", "") or "").strip()
            normalized["material_fact"] = str(item.get("material_fact", "") or "").strip()
            normalized["evidence_support"] = str(item.get("evidence_support", "") or "").strip()
            normalized["comparison"] = str(item.get("comparison", "") or "").strip()
            normalized["judgment_reason"] = str(item.get("judgment_reason", "") or "").strip()
            normalized["issue"] = str(item.get("issue", "") or "").strip()
            normalized["problem"] = str(item.get("problem", "") or "").strip()
            normalized["advice"] = str(item.get("advice", "") or "").strip()
            normalized["location"] = str(item.get("location", "") or "").strip()
            normalized["violating_text"] = str(item.get("violating_text", "") or "").strip()
            normalized["field_feedback"] = self._normalize_field_feedback(item.get("field_feedback", {}))
            evidence_files = item.get("evidence_files", []) if isinstance(item.get("evidence_files", []), list) else []
            normalized["evidence_files"] = [str(x).strip() for x in evidence_files if str(x).strip()]
            out.append(normalized)
        return out

    @staticmethod
    def _normalize_missing_item_feedback(value: Any) -> Dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        return {
            "text": str(value.get("text", "") or "").strip(),
            "reason": str(value.get("reason", "") or "").strip(),
        }

    def _derive_signal_summary(
        self,
        issue_feedback: List[Dict[str, Any]],
        missing_item_feedback: Dict[str, Any],
    ) -> Dict[str, Any]:
        summary: Dict[str, Any] = {
            "issue_feedback_count": len(issue_feedback),
            "incorrect_item_count": 0,
            "field_error_counts": {
                "rule": 0,
                "fact": 0,
                "evidence": 0,
                "reasoning": 0,
                "conclusion": 0,
            },
            "incorrect_task_codes": [],
            "missing_items": [],
            "rule_issues": [],
            "fact_issues": [],
            "evidence_issues": [],
            "reasoning_issues": [],
            "conclusion_issues": [],
        }
        incorrect_task_codes = set()
        for item in issue_feedback:
            if not isinstance(item, dict):
                continue
            task_code = str(item.get("task_code", "") or item.get("issue_key", "") or "").strip()
            feedback_text = str(item.get("feedback_text", "") or item.get("error_reason", "") or "").strip()
            if str(item.get("verdict", "") or "").strip().lower() == "incorrect":
                summary["incorrect_item_count"] += 1
                if task_code:
                    incorrect_task_codes.add(task_code)
            field_feedback = item.get("field_feedback", {}) if isinstance(item.get("field_feedback", {}), dict) else {}
            for field_name, bucket_name in (
                ("rule", "rule_issues"),
                ("fact", "fact_issues"),
                ("evidence", "evidence_issues"),
                ("reasoning", "reasoning_issues"),
                ("conclusion", "conclusion_issues"),
            ):
                if str(field_feedback.get(field_name, "") or "").strip().lower() != "incorrect":
                    continue
                summary["field_error_counts"][field_name] += 1
                summary[bucket_name].append(
                    {
                        "task_code": task_code,
                        "task_question": str(item.get("task_question", "") or "").strip(),
                        "message": feedback_text or str(item.get("judgment_reason", "") or "").strip() or str(item.get("rule_text", "") or "").strip(),
                    }
                )
        if incorrect_task_codes:
            summary["incorrect_task_codes"] = sorted(incorrect_task_codes)
        missing_text = str(missing_item_feedback.get("text", "") or "").strip()
        missing_reason = str(missing_item_feedback.get("reason", "") or "").strip()
        if missing_text or missing_reason:
            summary["missing_items"] = [{"text": missing_text, "reason": missing_reason}]
        return summary

    @staticmethod
    def _build_signal_text(signal_items: List[Dict[str, Any]], missing_items: List[Dict[str, Any]]) -> str:
        segments: List[str] = []
        for item in signal_items[:5]:
            if not isinstance(item, dict):
                continue
            task_code = str(item.get("task_code", "") or "").strip()
            message = str(item.get("message", "") or "").strip()
            if task_code and message:
                segments.append(f"{task_code}: {message}")
            elif message:
                segments.append(message)
        for missing in missing_items[:2]:
            if not isinstance(missing, dict):
                continue
            text = str(missing.get("text", "") or "").strip()
            reason = str(missing.get("reason", "") or "").strip()
            if text and reason:
                segments.append(f"漏项: {text}；原因: {reason}")
            elif text:
                segments.append(f"漏项: {text}")
            elif reason:
                segments.append(f"漏项原因: {reason}")
        return " | ".join([segment for segment in segments if segment]).strip()
