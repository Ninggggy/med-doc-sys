from __future__ import annotations

from typing import Any, Dict


class PreferenceDatasetBuilder:
    """Build preference-learning samples from feedback."""

    def build_preference_sample(self, original_output: Dict[str, Any], revised_output: Dict[str, Any], feedback_record: Dict[str, Any]) -> Dict[str, Any]:
        """Build one preference-learning sample."""
        return {
            "original": original_output,
            "revised": revised_output,
            "feedback": feedback_record,
            "reference_example": feedback_record.get("reference_example", {}) if isinstance(feedback_record.get("reference_example", {}), dict) else {},
        }

    def append_sample(self, sample: Dict[str, Any]) -> None:
        """Append sample into dataset sink. Skeleton implementation is no-op."""
        return None
