from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from agent.agent_backend.config.settings import settings
from agent.agent_backend.feedback.attribution.feedback_router import FeedbackRouter
from agent.agent_backend.feedback.attribution.root_cause_classifier import RootCauseClassifier
from agent.agent_backend.feedback.attribution.severity_scorer import SeverityScorer
from agent.agent_backend.feedback.collection.feedback_ingestor import FeedbackIngestor
from agent.agent_backend.feedback.evaluation.metrics_calculator import MetricsCalculator
from agent.agent_backend.feedback.evaluation.replay_runner import ReplayRunner
from agent.agent_backend.feedback.optimization.preference_dataset_builder import PreferenceDatasetBuilder
from agent.agent_backend.feedback.optimization.prompt_evaluator_agent import PromptEvaluatorAgent
from agent.agent_backend.feedback.optimization.prompt_task_builder import PromptTaskBuilder
from agent.agent_backend.feedback.optimization.prompt_version_registry import PromptVersionRegistry
from agent.agent_backend.feedback.optimization.regression_case_builder import RegressionCaseBuilder
from agent.agent_backend.feedback.optimization.rerank_dataset_builder import RerankDatasetBuilder
from agent.agent_backend.feedback.optimization.retrieval_task_builder import RetrievalTaskBuilder
from agent.agent_backend.feedback.optimization.rule_task_builder import RuleTaskBuilder
from agent.agent_backend.feedback.optimization.workflow_optimizer_agent import WorkflowOptimizerAgent
from agent.agent_backend.utils.file_util import ensure_dir_exists


class FeedbackClosedLoopPipeline:
    """Synchronous closed-loop feedback pipeline for ingestion, attribution, and routing."""

    def __init__(self, pre_review_service=None) -> None:
        self.pre_review_service = pre_review_service
        self.ingestor = FeedbackIngestor()
        self.classifier = RootCauseClassifier()
        self.severity_scorer = SeverityScorer()
        self.router = FeedbackRouter()
        self.replay_runner = ReplayRunner()
        self.metrics_calculator = MetricsCalculator()
        self.rule_task_builder = RuleTaskBuilder()
        self.prompt_task_builder = PromptTaskBuilder()
        self.prompt_evaluator = PromptEvaluatorAgent()
        self.retrieval_task_builder = RetrievalTaskBuilder()
        self.workflow_optimizer = WorkflowOptimizerAgent()
        self.rerank_dataset_builder = RerankDatasetBuilder()
        self.preference_dataset_builder = PreferenceDatasetBuilder()
        self.regression_case_builder = RegressionCaseBuilder()
        self.prompt_registry = PromptVersionRegistry()

    def run(self, feedback_payload: Dict[str, Any]) -> Dict[str, Any]:
        """Run the full closed loop for one feedback payload."""
        feedback_record = self.ingest_feedback(feedback_payload)
        run_trace = self.load_run_trace(feedback_record)
        run_context = self.load_run_context(feedback_record)
        if not isinstance(feedback_record.get("run_config", {}), dict) or not feedback_record.get("run_config", {}):
            feedback_record["run_config"] = dict(run_context.get("run_config", {}) or {})
        root_cause = self.classify_root_cause(feedback_record, run_trace)
        route_profile = self.route_feedback_profile(feedback_record, root_cause)
        routes = list(route_profile.get("routes", []) or [])
        assets = self.build_optimization_assets(feedback_record, root_cause, routes, run_trace, run_context)
        asset_bundle = self.persist_assets(feedback_record, root_cause, routes, assets, run_trace, run_context)
        replay_eval: Dict[str, Any] = {}
        replay_error = ""
        try:
            replay_eval = self.evaluate_candidate_version(feedback_record, assets)
        except Exception as exc:
            replay_error = str(exc)
        self.trigger_regression_update(feedback_record, root_cause, run_trace)
        prompt_version_record: Dict[str, Any] = {}
        candidate_register_error = ""
        try:
            prompt_version_record = self.register_prompt_candidate(feedback_record, assets, replay_eval)
        except Exception as exc:
            candidate_register_error = str(exc)
        replay_status = "failed" if replay_error else self._resolve_replay_status(replay_eval)
        if candidate_register_error:
            candidate_register_status = "failed"
        else:
            candidate_register_status = "completed" if prompt_version_record else "skipped"
        chapter_feedback_loop = {}
        if self.pre_review_service is not None:
            feedback_type = self.resolve_feedback_type(feedback_record)
            self.pre_review_service.add_feedback(
                run_id=str(feedback_record.get("run_id", "") or ""),
                section_id=str(feedback_record.get("section_id", "") or ""),
                feedback_type=feedback_type,
                feedback_text=str(feedback_record.get("feedback_text", "") or ""),
                suggestion=str(feedback_record.get("suggestion", "") or ""),
                operator=str(feedback_record.get("operator", "") or ""),
                feedback_meta={
                    "chain_mode": str(feedback_record.get("chain_mode", "") or "feedback_optimize"),
                    "decision": str(feedback_record.get("decision", "") or ""),
                    "manual_modified": bool(feedback_record.get("manual_modified", False)),
                    "feedback_type": feedback_type,
                    "labels": feedback_record.get("labels", []) if isinstance(feedback_record.get("labels", []), list) else [],
                    "evidence_feedback": feedback_record.get("evidence_feedback", []) if isinstance(feedback_record.get("evidence_feedback", []), list) else [],
                    "retrieval_feedback": str(feedback_record.get("retrieval_feedback", "") or ""),
                    "conclusion_feedback": str(feedback_record.get("conclusion_feedback", "") or ""),
                    "reference_example": feedback_record.get("reference_example", {}) if isinstance(feedback_record.get("reference_example", {}), dict) else {},
                    "original_output": feedback_record.get("original_output", {}),
                    "revised_output": feedback_record.get("revised_output", {}),
                    "suggestion": str(feedback_record.get("suggestion", "") or ""),
                },
            )
            chapter_feedback_loop = self.pre_review_service.process_feedback_closed_loop(
                feedback_record=feedback_record,
                run_trace=run_trace,
                run_context=run_context,
            )
            loop_success = bool((chapter_feedback_loop if isinstance(chapter_feedback_loop, dict) else {}).get("success", False))
            final_status = {
                "feedback_optimize_status": "completed" if loop_success else "failed",
                "candidate_register_status": candidate_register_status if loop_success else "skipped",
                "replay_status": replay_status if loop_success else "skipped",
                "error_message": self._join_errors(
                    ([] if loop_success else [str((chapter_feedback_loop or {}).get("error_message", "") or "feedback closed loop failed")])
                    + ([replay_error] if replay_error else [])
                    + ([candidate_register_error] if candidate_register_error else [])
                ),
            }
            self.pre_review_service.record_feedback_pipeline_event(
                run_id=str(feedback_record.get("run_id", "") or ""),
                section_id=str(feedback_record.get("section_id", "") or ""),
                payload={
                    "feedback_key": str((chapter_feedback_loop or {}).get("feedback_key", "") or ""),
                    "analysis_kind": "feedback_optimize_pipeline",
                    "feedback_type": feedback_type,
                    "feedback_meta": {
                        "chain_mode": str(feedback_record.get("chain_mode", "") or "feedback_optimize"),
                        "decision": str(feedback_record.get("decision", "") or ""),
                        "manual_modified": bool(feedback_record.get("manual_modified", False)),
                        "feedback_type": feedback_type,
                        "labels": feedback_record.get("labels", []) if isinstance(feedback_record.get("labels", []), list) else [],
                        "evidence_feedback": feedback_record.get("evidence_feedback", []) if isinstance(feedback_record.get("evidence_feedback", []), list) else [],
                        "retrieval_feedback": str(feedback_record.get("retrieval_feedback", "") or ""),
                        "conclusion_feedback": str(feedback_record.get("conclusion_feedback", "") or ""),
                        **final_status,
                    },
                    **final_status,
                },
            )
            chapter_feedback_loop = {
                **(chapter_feedback_loop if isinstance(chapter_feedback_loop, dict) else {}),
                **final_status,
            }
        return {
            "success": True,
            "message": "feedback closed loop completed",
            "data": {
                "feedback_record": feedback_record,
                "run_trace": run_trace,
                "root_cause": root_cause,
                "severity": self.severity_scorer.score(feedback_record, root_cause),
                "routes": routes,
                "route_profile": route_profile,
                "assets": assets,
                "asset_bundle": asset_bundle,
                "replay_evaluation": replay_eval,
                "prompt_version_record": prompt_version_record,
                "chapter_feedback_loop": chapter_feedback_loop,
            },
        }

    def ingest_feedback(self, feedback_payload: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize one raw feedback payload."""
        return self.ingestor.ingest(feedback_payload)

    def classify_root_cause(self, feedback_record: Dict[str, Any], run_trace: Dict[str, Any]) -> Dict[str, Any]:
        """Classify root cause using lightweight rules."""
        return self.classifier.classify(feedback_record, run_trace=run_trace)

    def route_feedback(self, feedback_record: Dict[str, Any], root_cause: Dict[str, Any]) -> list[str]:
        """Route one attributed feedback record to optimization channels."""
        return self.router.route(feedback_record, root_cause)

    def route_feedback_profile(self, feedback_record: Dict[str, Any], root_cause: Dict[str, Any]) -> Dict[str, Any]:
        """Return structured routing metadata for one attributed feedback record."""
        return self.router.route_profile(feedback_record, root_cause)

    def build_optimization_assets(
        self,
        feedback_record: Dict[str, Any],
        root_cause: Dict[str, Any],
        routes: list[str],
        run_trace: Dict[str, Any],
        run_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Build task/dataset assets for configured routes."""
        assets: Dict[str, Any] = {}
        asset_input = dict(feedback_record)
        asset_input["project_id"] = str(run_context.get("project_id", "") or "")
        asset_input["source_doc_id"] = str(run_context.get("source_doc_id", "") or "")
        asset_input["run_config"] = run_context.get("run_config", {}) if isinstance(run_context.get("run_config", {}), dict) else {}
        if "rule_fix" in routes:
            assets["rule_fix"] = self.rule_task_builder.build(asset_input, root_cause)
        if "prompt_fix" in routes:
            assets["prompt_fix"] = self.prompt_task_builder.build(asset_input, root_cause)
        if "retrieval_fix" in routes:
            assets["retrieval_fix"] = self.retrieval_task_builder.build(asset_input, root_cause)
        if "workflow_fix" in routes:
            assets["workflow_fix"] = self.workflow_optimizer.propose(asset_input, root_cause, run_trace)
        if "rerank_dataset" in routes:
            assets["rerank_dataset"] = self.rerank_dataset_builder.build_pairwise_sample(asset_input, run_trace)
        if "preference_dataset" in routes:
            assets["preference_dataset"] = self.preference_dataset_builder.build_preference_sample(
                run_trace.get("agent", {}),
                asset_input.get("revised_output", {}) if isinstance(asset_input.get("revised_output", {}), dict) else {},
                asset_input,
            )
        if "regression_case" in routes:
            assets["regression_case"] = self.regression_case_builder.build_case(asset_input, run_trace)
        return assets

    def trigger_regression_update(self, feedback_record: Dict[str, Any], root_cause: Dict[str, Any], run_trace: Dict[str, Any]) -> None:
        """Placeholder hook for regression-case refresh."""
        return None

    def load_run_trace(self, feedback_record: Dict[str, Any]) -> Dict[str, Any]:
        """Load the most relevant section trace for one feedback record."""
        if self.pre_review_service is None:
            return {}
        traces = self.pre_review_service.get_section_traces(
            run_id=str(feedback_record.get("run_id", "") or ""),
            section_id=str(feedback_record.get("section_id", "") or ""),
        )
        if isinstance(traces, list) and traces:
            return traces[-1] if isinstance(traces[-1], dict) else {}
        return {}

    def load_run_context(self, feedback_record: Dict[str, Any]) -> Dict[str, Any]:
        """Load minimal run context used for replay-case generation."""
        if self.pre_review_service is None:
            return {}
        run_id = str(feedback_record.get("run_id", "") or "")
        if not run_id:
            return {}
        success, _, data = self.pre_review_service.get_run_section_overview(run_id=run_id)
        if not success or not isinstance(data, dict):
            return {}
        run_config = dict(data.get("run_config", {}) or {}) if isinstance(data.get("run_config", {}), dict) else {}
        if not run_config:
            strategy = str(data.get("strategy", "") or "")
            workflow_mode = str(data.get("workflow_mode", "") or "")
            domain = str(data.get("domain", "") or "")
            branch = str(data.get("branch", "") or "")
            feedback_loop_mode = str(data.get("feedback_loop_mode", "") or "")
            enable_feedback_optimize = data.get("enable_feedback_optimize", None)
            prompt_config = data.get("prompt_config", {}) if isinstance(data.get("prompt_config", {}), dict) else {}
            if strategy:
                run_config["strategy"] = strategy
            if workflow_mode:
                run_config["workflow_mode"] = workflow_mode
            if domain:
                run_config["domain"] = domain
            if branch:
                run_config["branch"] = branch
            if feedback_loop_mode:
                run_config["feedback_loop_mode"] = feedback_loop_mode
            if isinstance(enable_feedback_optimize, bool):
                run_config["enable_feedback_optimize"] = enable_feedback_optimize
            if prompt_config:
                run_config["prompt_config"] = prompt_config
        return {
            "run_id": run_id,
            "project_id": str(data.get("project_id", "") or ""),
            "source_doc_id": str(data.get("source_doc_id", "") or ""),
            "run_config": run_config,
        }

    def persist_assets(
        self,
        feedback_record: Dict[str, Any],
        root_cause: Dict[str, Any],
        routes: list[str],
        assets: Dict[str, Any],
        run_trace: Dict[str, Any],
        run_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Persist feedback optimization assets under data/feedback_assets."""
        run_id = str(feedback_record.get("run_id", "") or "unknown_run")
        section_id = str(feedback_record.get("section_id", "") or "global")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        base_dir = Path(settings.feedback_asset_dir) / run_id / section_id
        ensure_dir_exists(str(base_dir))

        bundle_path = base_dir / f"bundle_{timestamp}.json"
        persisted = {"bundle_path": str(bundle_path), "asset_files": {}}
        prompt_fix = assets.get("prompt_fix", {})
        if isinstance(prompt_fix, dict):
            prompt_bundle = prompt_fix.get("bundle", {})
            version_id = str(prompt_fix.get("version_id", "") or "")
            if isinstance(prompt_bundle, dict) and version_id:
                prompt_dir = Path(settings.feedback_asset_dir) / "prompt_versions"
                ensure_dir_exists(str(prompt_dir))
                prompt_path = prompt_dir / f"{version_id}.json"
                prompt_bundle = dict(prompt_bundle)
                prompt_bundle["prompt_bundle_path"] = str(prompt_path)
                prompt_path.write_text(json.dumps(prompt_bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                persisted["asset_files"]["prompt_bundle"] = str(prompt_path)
                version_config = prompt_fix.get("version_config", {}) if isinstance(prompt_fix.get("version_config", {}), dict) else {}
                run_config = version_config.get("run_config", {}) if isinstance(version_config.get("run_config", {}), dict) else {}
                prompt_config = run_config.get("prompt_config", {}) if isinstance(run_config.get("prompt_config", {}), dict) else {}
                prompt_config["prompt_bundle_path"] = str(prompt_path)
                prompt_config["prompt_version_id"] = version_id
                run_config["prompt_config"] = prompt_config
                version_config["run_config"] = run_config
                prompt_fix["version_config"] = version_config
        bundle = {
            "feedback_record": feedback_record,
            "run_context": run_context,
            "run_trace": run_trace,
            "root_cause": root_cause,
            "routes": list(routes),
            "assets": assets,
        }
        bundle_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        regression_case = assets.get("regression_case", {})
        if isinstance(regression_case, dict) and regression_case.get("case_id"):
            case_dir = Path(settings.feedback_asset_dir) / "cases"
            ensure_dir_exists(str(case_dir))
            safe_case_id = str(regression_case.get("case_id", "")).replace(":", "__")
            case_path = case_dir / f"{safe_case_id}.json"
            case_path.write_text(json.dumps(regression_case, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            persisted["asset_files"]["regression_case"] = str(case_path)
        return persisted

    def evaluate_candidate_version(self, feedback_record: Dict[str, Any], assets: Dict[str, Any]) -> Dict[str, Any]:
        """Run minimal baseline-vs-candidate replay for prompt bundles when a regression case exists."""
        prompt_fix = assets.get("prompt_fix", {})
        regression_case = assets.get("regression_case", {})
        if not isinstance(prompt_fix, dict) or not isinstance(regression_case, dict):
            return {}
        case_id = str(regression_case.get("case_id", "") or "")
        if not case_id:
            return {}
        baseline = {
            "run_config": dict(feedback_record.get("run_config", {}) or {}),
        }
        candidate = prompt_fix.get("version_config", {}) if isinstance(prompt_fix.get("version_config", {}), dict) else {}
        if not candidate:
            return {}
        return self.prompt_evaluator.evaluate(case_id, baseline, candidate)

    def register_prompt_candidate(
        self,
        feedback_record: Dict[str, Any],
        assets: Dict[str, Any],
        replay_evaluation: Dict[str, Any],
    ) -> Dict[str, Any]:
        prompt_fix = assets.get("prompt_fix", {})
        if not isinstance(prompt_fix, dict):
            return {}
        version_id = str(prompt_fix.get("version_id", "") or "")
        version_config = prompt_fix.get("version_config", {}) if isinstance(prompt_fix.get("version_config", {}), dict) else {}
        run_config = version_config.get("run_config", {}) if isinstance(version_config.get("run_config", {}), dict) else {}
        prompt_config = run_config.get("prompt_config", {}) if isinstance(run_config.get("prompt_config", {}), dict) else {}
        bundle_path = str(prompt_config.get("prompt_bundle_path", "") or "")
        if not version_id or not bundle_path:
            return {}
        return self.prompt_registry.register_candidate(
            version_id=version_id,
            bundle_path=bundle_path,
            source_feedback=feedback_record,
            diagnosis=prompt_fix.get("diagnosis", {}) if isinstance(prompt_fix.get("diagnosis", {}), dict) else {},
            replay_evaluation=replay_evaluation,
            target_templates=prompt_fix.get("target_templates", []) if isinstance(prompt_fix.get("target_templates", []), list) else [],
        )

    @staticmethod
    def resolve_feedback_type(feedback_record: Dict[str, Any]) -> str:
        """Map user decision and labels to service-layer feedback_type."""
        feedback_type = str(feedback_record.get("feedback_type", "") or "").lower().strip()
        if feedback_type in {"valid", "false_positive", "missed"}:
            return feedback_type
        decision = str(feedback_record.get("decision", "") or "").lower()
        labels = {str(x).lower() for x in feedback_record.get("labels", []) or []}
        if decision in {"false_positive", "rejected"}:
            return "false_positive"
        if decision in {"missed", "missing_risk"} or "missing_risk" in labels or "retrieval_miss" in labels:
            return "missed"
        return "valid"

    @staticmethod
    def _resolve_replay_status(replay_evaluation: Dict[str, Any]) -> str:
        if not isinstance(replay_evaluation, dict) or not replay_evaluation:
            return "skipped"
        recommendation = str(replay_evaluation.get("recommendation", "") or "").strip().lower()
        if recommendation == "candidate_preferred":
            return "passed"
        if recommendation:
            return "failed"
        return "skipped"

    @staticmethod
    def _join_errors(errors: list[str]) -> str:
        parts = [str(item or "").strip() for item in errors if str(item or "").strip()]
        return " | ".join(parts)
