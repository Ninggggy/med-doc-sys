from __future__ import annotations

from typing import Any, Dict

from agent.agent_backend.feedback.evaluation.metrics_calculator import MetricsCalculator
from agent.agent_backend.feedback.evaluation.replay_runner import ReplayRunner


class PromptEvaluatorAgent:
    """Evaluate candidate prompt versions on replay cases."""

    def __init__(self) -> None:
        self.replay_runner = ReplayRunner()
        self.metrics_calculator = MetricsCalculator()

    def describe(self) -> Dict[str, Any]:
        return {
            "name": "prompt_evaluator",
            "description": "Compare baseline and candidate prompt versions on replay cases and generate release recommendation.",
        }

    def evaluate(
        self,
        case_id: str,
        baseline_version: Dict[str, Any],
        candidate_version: Dict[str, Any],
    ) -> Dict[str, Any]:
        compare = self.replay_runner.compare_versions([case_id], baseline_version, candidate_version)
        old_results = compare.get("old_results", []) if isinstance(compare.get("old_results", []), list) else []
        new_results = compare.get("new_results", []) if isinstance(compare.get("new_results", []), list) else []
        comparison = self.metrics_calculator.calc_version_gain(old_results, new_results)
        baseline_metrics = comparison.get("baseline_metrics", {})
        candidate_metrics = comparison.get("candidate_metrics", {})
        recommendation = str(
            (comparison.get("summary", {}) if isinstance(comparison.get("summary", {}), dict) else {}).get(
                "recommendation",
                "manual_review_required",
            )
        )
        return {
            "case_id": case_id,
            "baseline_version": baseline_version,
            "candidate_version": candidate_version,
            "compare": compare,
            "baseline_metrics": baseline_metrics,
            "candidate_metrics": candidate_metrics,
            "comparison": comparison,
            "recommendation": recommendation,
        }
