from __future__ import annotations

from typing import Any, Dict


class RerankDatasetBuilder:
    """Build pairwise rerank samples from feedback."""

    def build_pairwise_sample(self, feedback_record: Dict[str, Any], run_trace: Dict[str, Any]) -> Dict[str, Any]:
        """Build one pairwise rerank sample."""
        return {"feedback": feedback_record, "trace": run_trace}

    def append_sample(self, sample: Dict[str, Any]) -> None:
        """Append sample into dataset sink. Skeleton implementation is no-op."""
        return None
