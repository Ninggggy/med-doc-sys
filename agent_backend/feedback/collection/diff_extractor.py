from __future__ import annotations

from typing import Any, Dict


class DiffExtractor:
    """Build lightweight diffs between original and revised outputs."""

    def extract_diff(self, original_output: str, revised_output: str) -> Dict[str, Any]:
        """Extract a text-level diff summary."""
        return {
            "original_preview": str(original_output or "")[:300],
            "revised_preview": str(revised_output or "")[:300],
            "changed": str(original_output or "") != str(revised_output or ""),
        }

    def extract_structured_diff(self, original: Dict[str, Any], revised: Dict[str, Any]) -> Dict[str, Any]:
        """Extract a structured diff summary for dict payloads."""
        original_keys = set((original or {}).keys())
        revised_keys = set((revised or {}).keys())
        return {
            "added_keys": sorted(list(revised_keys - original_keys)),
            "removed_keys": sorted(list(original_keys - revised_keys)),
            "shared_keys": sorted(list(original_keys & revised_keys)),
        }

    def classify_diff(self, diff_result: Dict[str, Any]) -> list[str]:
        """Classify diff result into simple labels."""
        labels = []
        if diff_result.get("changed"):
            labels.append("content_changed")
        if diff_result.get("added_keys") or diff_result.get("removed_keys"):
            labels.append("structure_changed")
        return labels
