from __future__ import annotations

from typing import Any, Dict


class RetrievalTaskBuilder:
    """Build retrieval-fix tasks from feedback."""

    def build(self, feedback_record: Dict[str, Any], root_cause: Dict[str, Any]) -> Dict[str, Any]:
        """Build one retrieval-fix task payload."""
        return {
            "type": "retrieval_fix",
            "missing_evidence_case": self.build_missing_evidence_case(feedback_record),
            "root_cause": root_cause,
        }

    def build_missing_evidence_case(self, feedback_record: Dict[str, Any]) -> Dict[str, Any]:
        """Build a missing-evidence case payload."""
        return {"run_id": feedback_record.get("run_id", ""), "section_id": feedback_record.get("section_id", "")}

    def persist_retrieval_ticket(self, ticket: Dict[str, Any]) -> str:
        """Return a stable ticket id."""
        return "retrieval_ticket"
