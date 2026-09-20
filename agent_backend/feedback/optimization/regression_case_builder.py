from __future__ import annotations

from typing import Any, Dict


class RegressionCaseBuilder:
    """Build regression replay cases from feedback."""

    def build_case(self, feedback_record: Dict[str, Any], run_trace: Dict[str, Any]) -> Dict[str, Any]:
        """Build one regression-case payload."""
        run_id = str(feedback_record.get("run_id", "") or "")
        section_id = str(feedback_record.get("section_id", "") or "global")
        return {
            "case_id": f"{run_id}:{section_id}",
            "run_id": run_id,
            "section_id": section_id,
            "project_id": str(feedback_record.get("project_id", "") or ""),
            "source_doc_id": str(feedback_record.get("source_doc_id", "") or ""),
            "run_config": feedback_record.get("run_config", {}) if isinstance(feedback_record.get("run_config", {}), dict) else {},
            "feedback": feedback_record,
            "trace": run_trace,
            "reference_example": feedback_record.get("reference_example", {}) if isinstance(feedback_record.get("reference_example", {}), dict) else {},
            "expected_output": (
                feedback_record.get("reference_example", {}).get("expected_output", {})
                if isinstance(feedback_record.get("reference_example", {}), dict)
                else {}
            ),
        }

    def append_case(self, case_data: Dict[str, Any]) -> None:
        """Append case into regression corpus. Skeleton implementation is no-op."""
        return None
