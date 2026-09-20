from __future__ import annotations

from typing import Any, Dict


class RuleTaskBuilder:
    """Build rule-fix tasks from feedback."""

    def build(self, feedback_record: Dict[str, Any], root_cause: Dict[str, Any]) -> Dict[str, Any]:
        """Build one rule-fix task payload."""
        return {"type": "rule_fix", "candidate_rule": self.extract_candidate_rule(feedback_record), "root_cause": root_cause}

    def extract_candidate_rule(self, feedback_record: Dict[str, Any]) -> Dict[str, Any]:
        """Extract candidate rule patch payload."""
        return {"section_id": feedback_record.get("section_id", ""), "feedback_text": feedback_record.get("feedback_text", "")}

    def persist_rule_ticket(self, ticket: Dict[str, Any]) -> str:
        """Return a stable ticket id."""
        return f"rule_ticket:{ticket.get('candidate_rule', {}).get('section_id', 'global')}"
