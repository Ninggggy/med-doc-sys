from __future__ import annotations

from typing import Any, Dict


class SeverityScorer:
    """Assign coarse severity scores to feedback items."""

    def score(self, feedback_record: Dict[str, Any], root_cause: Dict[str, Any]) -> Dict[str, Any]:
        """Score a feedback item by labels and root category."""
        high = self.is_high_risk(feedback_record)
        return {
            "severity": "high" if high else "medium",
            "score": 0.9 if high else 0.5,
            "root_category": root_cause.get("root_category", ""),
        }

    def is_high_risk(self, feedback_record: Dict[str, Any]) -> bool:
        """Return whether a feedback record should be treated as high risk."""
        text = " ".join(
            [
                str(feedback_record.get("feedback_text", "") or ""),
                " ".join([str(x) for x in feedback_record.get("labels", []) or []]),
            ]
        ).lower()
        return any(token in text for token in ["critical", "严重", "high", "missing_risk"])
