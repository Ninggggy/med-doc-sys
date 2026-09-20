from __future__ import annotations

from typing import Any, Dict, List


class BlindReviewBuilder:
    """Build blinded review packets for human evaluation."""

    def build_blind_review_packets(self, case_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Build human-evaluation packets from case results."""
        return [{"packet_id": idx + 1, "payload": item} for idx, item in enumerate(case_results or [])]
