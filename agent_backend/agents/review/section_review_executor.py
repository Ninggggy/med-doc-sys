from __future__ import annotations

from typing import Any, Dict

from agent.agent_backend.application.pre_review_app_service import PreReviewAppService


class SectionReviewExecutor:
    """Execute one section-level replay/review task."""

    def __init__(self) -> None:
        self.pre_review_app_service = PreReviewAppService()

    def build_initial_state(self, section_payload: Dict[str, Any], run_config: Dict[str, Any]) -> Dict[str, Any]:
        """Build normalized section replay input state."""
        return {
            "project_id": str(section_payload.get("project_id", "") or ""),
            "source_doc_id": str(
                section_payload.get("source_doc_id", "")
                or section_payload.get("submission_id", "")
                or ""
            ),
            "section_id": str(section_payload.get("section_id", "") or ""),
            "run_config": dict(run_config or {}),
            "source_case": dict(section_payload or {}),
        }

    def persist_section_result(self, state: Dict[str, Any]) -> None:
        """Persistence hook reserved for future replay result snapshots."""
        return None

    def execute(self, section_payload: Dict[str, Any], run_config: Dict[str, Any]) -> Dict[str, Any]:
        """Run replay for one target section."""
        state = self.build_initial_state(section_payload=section_payload, run_config=run_config)
        project_id = state["project_id"]
        source_doc_id = state["source_doc_id"]
        section_id = state["section_id"]
        if not project_id or not source_doc_id or not section_id:
            return {
                "success": False,
                "message": "missing project_id, source_doc_id, or section_id",
                "data": state,
            }
        replay_result = self.pre_review_app_service.run_section_replay(
            project_id=project_id,
            submission_id=source_doc_id,
            section_id=section_id,
            run_config=state["run_config"],
        )
        self.persist_section_result(
            {
                "section_payload": dict(section_payload or {}),
                "run_config": dict(run_config or {}),
                "result": replay_result,
            }
        )
        return replay_result
