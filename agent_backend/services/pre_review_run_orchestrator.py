from __future__ import annotations

from typing import Any, Dict, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from agent.agent_backend.services.pre_review_service import PreReviewService


class PreReviewRunOrchestrator:
    """Coordinate a full pre-review run while keeping section orchestration separate."""

    def __init__(self, service: "PreReviewService") -> None:
        self.service = service

    def run_pre_review(
        self,
        project_id: str,
        source_doc_id: str,
        run_config: Optional[Dict[str, Any]] = None,
        progress_callback=None,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        return self.service._run_pre_review_impl(
            project_id=project_id,
            source_doc_id=source_doc_id,
            run_config=run_config,
            progress_callback=progress_callback,
        )
