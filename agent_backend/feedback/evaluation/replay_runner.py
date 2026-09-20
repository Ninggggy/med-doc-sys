from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from agent.agent_backend.agents.review.section_review_executor import SectionReviewExecutor
from agent.agent_backend.application.pre_review_app_service import PreReviewAppService
from agent.agent_backend.config.settings import settings


class ReplayRunner:
    """Run replay cases against a version configuration."""

    def __init__(self) -> None:
        self.pre_review_app_service = PreReviewAppService()
        self.section_review_executor = SectionReviewExecutor()
        self.case_dir = Path(settings.feedback_asset_dir) / "cases"

    def _case_path(self, case_id: str) -> Path:
        """Resolve persisted case file path from case id."""
        safe_case_id = str(case_id or "").replace(":", "__")
        return self.case_dir / f"{safe_case_id}.json"

    def load_case(self, case_id: str) -> Dict[str, Any]:
        """Load a persisted replay case."""
        path = self._case_path(case_id)
        if not path.exists():
            raise FileNotFoundError(f"replay case not found: {case_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    def run_case(self, case_id: str, version_config: Dict[str, Any]) -> Dict[str, Any]:
        """Replay a single case by invoking pre-review application service."""
        case_data = self.load_case(case_id)
        project_id = str(case_data.get("project_id", "") or "")
        submission_id = str(case_data.get("source_doc_id", "") or "")
        section_id = str(case_data.get("section_id", "") or "")
        if not project_id or not submission_id:
            return {
                "case_id": case_id,
                "status": "invalid_case",
                "message": "missing project_id or source_doc_id",
                "case_data": case_data,
            }
        run_config = dict(case_data.get("run_config", {}) or {})
        run_config.update(dict(version_config.get("run_config", {}) or {}))
        if section_id and section_id.lower() != "global":
            replay_result = self.section_review_executor.execute(
                section_payload={
                    "project_id": project_id,
                    "source_doc_id": submission_id,
                    "section_id": section_id,
                    "case_id": case_id,
                },
                run_config=run_config,
            )
            replay_mode = "section"
        else:
            replay_result = self.pre_review_app_service.run_pre_review(project_id, submission_id, run_config)
            replay_mode = "full_submission"
        return {
            "case_id": case_id,
            "version_config": version_config,
            "status": "replayed",
            "replay_mode": replay_mode,
            "source_case": case_data,
            "replay_result": replay_result,
        }

    def run_batch(self, case_ids: List[str], version_config: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Replay a batch of cases."""
        return [self.run_case(case_id, version_config) for case_id in case_ids]

    def compare_versions(self, case_ids: List[str], old_version: Dict[str, Any], new_version: Dict[str, Any]) -> Dict[str, Any]:
        """Compare replay outputs across versions."""
        old_results = self.run_batch(case_ids, old_version)
        new_results = self.run_batch(case_ids, new_version)
        return {"case_ids": list(case_ids), "old_results": old_results, "new_results": new_results}
