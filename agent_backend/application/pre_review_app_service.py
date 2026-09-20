from __future__ import annotations

from typing import Any, Dict, List

from agent.agent_backend.services.pre_review_service import PreReviewService


class PreReviewAppService:
    """Application-level orchestration for pre-review project lifecycle."""

    def __init__(self) -> None:
        self.pre_review_service = PreReviewService()

    def create_project(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Create a pre-review project."""
        success, message, data = self.pre_review_service.create_project(
            project_name=str(payload.get("project_name", "") or ""),
            description=str(payload.get("description", "") or ""),
            owner=str(payload.get("owner", "") or ""),
        )
        return {"success": success, "message": message, "data": data}

    def upload_submission(self, project_id: str, files: List[Any]) -> Dict[str, Any]:
        """Upload one or more submission materials into a project."""
        results = []
        for file_obj in files or []:
            success, message, data = self.pre_review_service.upload_submission(project_id=project_id, file_obj=file_obj)
            results.append({"success": success, "message": message, "data": data})
        return {"results": results, "count": len(results)}

    def parse_submission(self, submission_id: str) -> Dict[str, Any]:
        """Parse a submission material into chapter/review units."""
        success, message, data = self.pre_review_service.parse_submission(doc_id=submission_id)
        return {"success": success, "message": message, "data": data}

    def run_pre_review(self, project_id: str, submission_id: str, run_config: Dict[str, Any]) -> Dict[str, Any]:
        """Run section-by-section pre-review for one submission."""
        success, message, data = self.pre_review_service.run_pre_review(
            project_id=project_id,
            source_doc_id=submission_id,
            run_config=run_config,
        )
        return {"success": success, "message": message, "data": data}

    def run_section_replay(
        self,
        project_id: str,
        submission_id: str,
        section_id: str,
        run_config: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Replay one specific section without rerunning the full submission."""
        success, message, data = self.pre_review_service.run_section_replay(
            project_id=project_id,
            source_doc_id=submission_id,
            section_id=section_id,
            run_config=run_config,
        )
        return {"success": success, "message": message, "data": data}

    def replay_feedback_optimize(self, run_id: str, section_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        success, message, data = self.pre_review_service.replay_feedback_optimize(
            run_id=run_id,
            section_id=section_id,
            feedback_payload=payload,
        )
        return {"success": success, "message": message, "data": data}

    def replay_meta_reflection(self, run_id: str, section_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        success, message, data = self.pre_review_service.replay_meta_reflection(
            run_id=run_id,
            section_id=section_id,
            feedback_payload=payload,
        )
        return {"success": success, "message": message, "data": data}

    def get_run_result(self, run_id: str) -> Dict[str, Any]:
        """Get overview and conclusions for one pre-review run."""
        success, message, data = self.pre_review_service.get_run_section_overview(run_id=run_id)
        return {"success": success, "message": message, "data": data}

    def get_section_traces(self, run_id: str, section_id: str) -> Dict[str, Any]:
        """Get detailed trace records for a section or a whole run."""
        data = self.pre_review_service.get_section_traces(run_id=run_id, section_id=section_id)
        return {"success": True, "message": "success", "data": data}

    def export_report(self, run_id: str) -> Dict[str, Any]:
        """Export one run into a report document."""
        success, message, data = self.pre_review_service.export_report_word(run_id=run_id)
        return {"success": success, "message": message, "data": data}
