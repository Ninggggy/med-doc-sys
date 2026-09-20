from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from agent.agent_backend.config.settings import settings
from agent.agent_backend.feedback.evaluation.ablation_experiment_runner import AblationExperimentRunner
from agent.agent_backend.feedback.evaluation.metrics_calculator import MetricsCalculator
from agent.agent_backend.feedback.evaluation.replay_runner import ReplayRunner
from agent.agent_backend.feedback.pipelines.feedback_closed_loop_pipeline import FeedbackClosedLoopPipeline
from agent.agent_backend.feedback.optimization.prompt_version_registry import PromptVersionRegistry
from agent.agent_backend.services.pre_review_service import PreReviewService
from agent.agent_backend.utils.file_util import ensure_dir_exists


class FeedbackAppService:
    """Application-level orchestration for feedback ingestion and replay."""

    def __init__(self) -> None:
        self.pre_review_service = PreReviewService()
        self.feedback_pipeline = FeedbackClosedLoopPipeline(pre_review_service=self.pre_review_service)
        self.replay_runner = ReplayRunner()
        self.ablation_runner = AblationExperimentRunner()
        self.metrics_calculator = MetricsCalculator()
        self.prompt_registry = PromptVersionRegistry()

    def submit_feedback(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Submit one expert feedback payload into the closed loop."""
        return self.feedback_pipeline.run(payload)

    def get_feedback_stats(self, filters: Dict[str, Any]) -> Dict[str, Any]:
        """Get aggregated feedback metrics for a run/section."""
        success, message, data = self.pre_review_service.get_feedback_stats(
            run_id=str(filters.get("run_id", "") or ""),
            section_id=str(filters.get("section_id", "") or ""),
        )
        return {"success": success, "message": message, "data": data}

    def replay_cases(self, case_ids: List[str], version_config: Dict[str, Any]) -> Dict[str, Any]:
        """Replay evaluation cases and calculate structured review/retrieval metrics."""
        results = self.replay_runner.run_batch(case_ids, version_config)
        review_metrics = self.metrics_calculator.calc_review_metrics(results)
        retrieval_metrics = self.metrics_calculator.calc_retrieval_metrics(results)
        problem_detection = (
            dict(review_metrics.get("problem_detection", {}))
            if isinstance(review_metrics.get("problem_detection", {}), dict)
            else {}
        )
        return {
            "success": True,
            "message": "replay executed",
            "data": {
                "results": results,
                "metrics": {
                    "review": review_metrics,
                    "retrieval": retrieval_metrics,
                    "problem_detection": problem_detection,
                    "overall_score": 0.7 * float(review_metrics.get("composite_score", 0.0) or 0.0)
                    + 0.3 * float(retrieval_metrics.get("composite_score", 0.0) or 0.0),
                },
            },
        }

    def run_ablation_study(
        self,
        case_ids: List[str],
        version_config: Dict[str, Any],
        study_config: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        result = self.ablation_runner.run(
            case_ids=case_ids,
            base_version_config=version_config,
            study_config=study_config if isinstance(study_config, dict) else {},
        )
        return {
            "success": True,
            "message": "ablation study executed",
            "data": result,
        }

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

    def generate_regression_cases(self, filters: Dict[str, Any]) -> Dict[str, Any]:
        """Generate lightweight regression cases from run/section filters."""
        run_id = str(filters.get("run_id", "") or "")
        section_id = str(filters.get("section_id", "") or "")
        traces = self.pre_review_service.get_section_traces(run_id=run_id, section_id=section_id) if run_id else []
        conclusions = self.pre_review_service.get_section_conclusions(run_id=run_id, section_id=section_id) if run_id else []
        overview_ok, _, overview = self.pre_review_service.get_run_section_overview(run_id=run_id) if run_id else (False, "", None)
        overview = overview if overview_ok and isinstance(overview, dict) else {}
        inherited_run_config = dict(overview.get("run_config", {}) or {})
        if not inherited_run_config:
            strategy = str(overview.get("strategy", "") or "")
            workflow_mode = str(overview.get("workflow_mode", "") or "")
            if strategy:
                inherited_run_config["strategy"] = strategy
            if workflow_mode:
                inherited_run_config["workflow_mode"] = workflow_mode
        cases = []
        conclusion_by_section = {str(item.get("section_id", "")): item for item in conclusions}
        case_dir = Path(settings.feedback_asset_dir) / "cases"
        ensure_dir_exists(str(case_dir))
        for trace in traces or []:
            if not isinstance(trace, dict):
                continue
            sid = str(trace.get("section_id", "") or "")
            case_data = {
                "case_id": f"{run_id}:{sid}",
                "run_id": run_id,
                "section_id": sid,
                "project_id": str(overview.get("project_id", "") or ""),
                "source_doc_id": str(overview.get("source_doc_id", "") or ""),
                "run_config": dict(filters.get("run_config", {}) or inherited_run_config),
                "trace": trace,
                "expected": conclusion_by_section.get(sid, {}),
            }
            case_path = case_dir / f"{case_data['case_id'].replace(':', '__')}.json"
            case_path.write_text(json.dumps(case_data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            case_data["case_path"] = str(case_path)
            cases.append(case_data)
        return {
            "success": True,
            "message": "regression cases generated",
            "data": {"filters": dict(filters), "cases": cases},
        }

    def list_prompt_versions(self) -> Dict[str, Any]:
        return {"success": True, "message": "success", "data": self.prompt_registry.list_versions()}

    def activate_prompt_version(self, version_id: str) -> Dict[str, Any]:
        record = self.prompt_registry.activate_version(version_id)
        return {"success": True, "message": "prompt version activated", "data": record}

    def rollback_prompt_version(self) -> Dict[str, Any]:
        record = self.prompt_registry.rollback_active()
        return {"success": True, "message": "prompt version rolled back", "data": record}
