from __future__ import annotations

from typing import Any, Dict, List


class FeedbackValidator:
    """Validate minimal feedback payload shape before ingestion."""

    def validate(self, feedback_payload: Dict[str, Any]) -> None:
        """Validate the full feedback payload and raise on required-field errors."""
        if not str(feedback_payload.get("run_id", "") or "").strip():
            raise ValueError("run_id is required")
        self.validate_labels(list(feedback_payload.get("labels", []) or []))
        self.validate_revised_output(feedback_payload.get("revised_output", {}))

    def validate_labels(self, labels: List[str]) -> None:
        """Validate optional feedback labels."""
        if not isinstance(labels, list):
            raise ValueError("labels must be a list")

    def validate_revised_output(self, revised_output: Dict[str, Any] | str) -> None:
        """Validate revised output payload type."""
        if not isinstance(revised_output, (dict, str)):
            raise ValueError("revised_output must be dict or string")
