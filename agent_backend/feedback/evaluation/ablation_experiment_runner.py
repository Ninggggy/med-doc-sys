from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional

from agent.agent_backend.feedback.evaluation.metrics_calculator import MetricsCalculator
from agent.agent_backend.feedback.evaluation.replay_runner import ReplayRunner


class AblationExperimentRunner:
    """Run replay-based ablation studies on pre-review feature flags."""

    DEFAULT_DIMENSIONS = [
        {
            "variant_id": "no_prompt_rules",
            "label": "No Prompt Rules",
            "description": "Disable prompt rule bundle and prompt patches.",
            "feature_flags": {"use_prompt_rules": False},
        },
        {
            "variant_id": "no_section_rules",
            "label": "No Section Rules",
            "description": "Disable section-level business rules in planner/reviewer inputs.",
            "feature_flags": {"use_section_rules": False},
        },
        {
            "variant_id": "no_reference_examples",
            "label": "No Reference Examples",
            "description": "Disable section reference and few-shot examples.",
            "feature_flags": {"use_reference_examples": False},
        },
        {
            "variant_id": "no_experience_memory",
            "label": "No Experience Memory",
            "description": "Disable long-term experience memory injection.",
            "feature_flags": {"use_experience_memory": False},
        },
        {
            "variant_id": "no_historical_bad_retrievals",
            "label": "No Bad Retrieval Memory",
            "description": "Disable historical bad retrieval memory in retrieval evaluation.",
            "feature_flags": {"use_historical_bad_retrievals": False},
        },
        {
            "variant_id": "no_retrieval_evaluator",
            "label": "No Retrieval Evaluator",
            "description": "Bypass retrieval evaluator and approve all retrieved materials.",
            "feature_flags": {"use_retrieval_evaluator": False},
        },
        {
            "variant_id": "no_memory_bundle",
            "label": "No Memory Bundle",
            "description": "Disable examples, experience, and bad retrieval memory together.",
            "feature_flags": {
                "use_reference_examples": False,
                "use_experience_memory": False,
                "use_historical_bad_retrievals": False,
            },
        },
        {
            "variant_id": "minimal_core",
            "label": "Minimal Core",
            "description": "Keep only planner/retrieval/reviewer core loop.",
            "feature_flags": {
                "use_prompt_rules": False,
                "use_section_rules": False,
                "use_reference_examples": False,
                "use_experience_memory": False,
                "use_historical_bad_retrievals": False,
                "use_retrieval_evaluator": False,
            },
        },
    ]

    def __init__(
        self,
        replay_runner: ReplayRunner | None = None,
        metrics_calculator: MetricsCalculator | None = None,
    ) -> None:
        self.replay_runner = replay_runner if replay_runner is not None else ReplayRunner()
        self.metrics_calculator = metrics_calculator if metrics_calculator is not None else MetricsCalculator()

    @staticmethod
    def _normalize_version_config(version_config: Dict[str, Any]) -> Dict[str, Any]:
        config = deepcopy(version_config if isinstance(version_config, dict) else {})
        run_config = config.get("run_config", {}) if isinstance(config.get("run_config", {}), dict) else {}
        config["run_config"] = run_config
        return config

    def _with_feature_flags(self, version_config: Dict[str, Any], feature_flags: Dict[str, Any]) -> Dict[str, Any]:
        config = self._normalize_version_config(version_config)
        run_config = dict(config.get("run_config", {}) or {})
        merged_flags = self.metrics_calculator.build_feature_flags(run_config.get("feature_flags", {}))
        merged_flags.update(self.metrics_calculator.normalize_feature_overrides(feature_flags))
        run_config["feature_flags"] = merged_flags
        config["run_config"] = run_config
        return config

    def build_variants(
        self,
        base_version_config: Dict[str, Any],
        study_config: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        config = study_config if isinstance(study_config, dict) else {}
        custom_variants = config.get("variants", []) if isinstance(config.get("variants", []), list) else []
        variants: List[Dict[str, Any]] = [
            {
                "variant_id": "baseline",
                "label": "Baseline",
                "description": "Baseline configuration with all enabled defaults.",
                "feature_flags": self.metrics_calculator.build_feature_flags(
                    self._normalize_version_config(base_version_config).get("run_config", {}).get("feature_flags", {})
                ),
                "version_config": self._with_feature_flags(base_version_config, {}),
            }
        ]
        source_variants = custom_variants if custom_variants else self.DEFAULT_DIMENSIONS
        for item in source_variants:
            if not isinstance(item, dict):
                continue
            feature_flags = item.get("feature_flags", {}) if isinstance(item.get("feature_flags", {}), dict) else {}
            variants.append(
                {
                    "variant_id": str(item.get("variant_id", "") or f"variant_{len(variants)}"),
                    "label": str(item.get("label", "") or item.get("variant_id", "") or f"Variant {len(variants)}"),
                    "description": str(item.get("description", "") or ""),
                    "feature_flags": self.metrics_calculator.build_feature_flags(feature_flags),
                    "version_config": self._with_feature_flags(base_version_config, feature_flags),
                }
            )
        return variants

    def run(
        self,
        case_ids: List[str],
        base_version_config: Dict[str, Any],
        study_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        variants = self.build_variants(base_version_config, study_config=study_config)
        include_results = bool((study_config or {}).get("include_results", False))
        variant_records: List[Dict[str, Any]] = []
        baseline_results: List[Dict[str, Any]] = []
        for index, variant in enumerate(variants):
            results = self.replay_runner.run_batch(case_ids, variant["version_config"])
            review_metrics = self.metrics_calculator.calc_review_metrics(results)
            retrieval_metrics = self.metrics_calculator.calc_retrieval_metrics(results)
            record = {
                "variant_id": variant["variant_id"],
                "label": variant["label"],
                "description": variant["description"],
                "feature_flags": variant["feature_flags"],
                "metrics": {
                    "review": review_metrics,
                    "retrieval": retrieval_metrics,
                    "overall_score": 0.7 * float(review_metrics.get("composite_score", 0.0) or 0.0)
                    + 0.3 * float(retrieval_metrics.get("composite_score", 0.0) or 0.0),
                },
            }
            if index == 0:
                baseline_results = results
            else:
                record["delta_vs_baseline"] = self.metrics_calculator.calc_version_gain(baseline_results, results)
            if include_results:
                record["results"] = results
            variant_records.append(record)
        ranking = sorted(
            [
                {
                    "variant_id": item["variant_id"],
                    "label": item["label"],
                    "overall_score": float((item.get("metrics", {}) if isinstance(item.get("metrics", {}), dict) else {}).get("overall_score", 0.0) or 0.0),
                }
                for item in variant_records
            ],
            key=lambda item: item["overall_score"],
            reverse=True,
        )
        best_variant = ranking[0] if ranking else {}
        return {
            "case_ids": list(case_ids or []),
            "baseline_variant_id": "baseline",
            "variants": variant_records,
            "ranking": ranking,
            "summary": {
                "variant_count": len(variant_records),
                "case_count": len(case_ids or []),
                "best_variant_id": str(best_variant.get("variant_id", "") or ""),
                "best_variant_label": str(best_variant.get("label", "") or ""),
                "best_overall_score": float(best_variant.get("overall_score", 0.0) or 0.0),
            },
        }
