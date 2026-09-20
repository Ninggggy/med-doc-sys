from __future__ import annotations

from typing import Dict, List


class FeedbackRouter:
    """Route attributed feedback into optimization channels."""

    RETRIEVAL_SUBCATEGORIES = {
        "query_miss",
        "retrieval_scope_error",
        "retrieval_ranking_error",
        "historical_experience_missing",
    }
    RULE_SUBCATEGORIES = {
        "rule_mapping_error",
        "missing_regulatory_basis",
    }
    KNOWLEDGE_AND_REASONING_SUBCATEGORIES = {
        "knowledge_understanding_error",
        "section_fact_extraction_error",
        "focus_point_miss",
        "task_question_error",
        "reasoning_chain_error",
        "evidence_interpretation_error",
        "over_inference",
        "under_identification",
        "wrong_severity",
    }
    WORDING_SUBCATEGORIES = {
        "wording_not_actionable",
        "unhelpful_question_to_applicant",
    }

    def route_profile(self, feedback_record: Dict[str, object], root_cause: Dict[str, object]) -> Dict[str, object]:
        """Return structured routing metadata for one feedback record."""
        category = str(root_cause.get("root_category", "") or "")
        sub_category = str(root_cause.get("sub_category", "") or "")
        decision = str(feedback_record.get("decision", "") or "")
        if sub_category in self.RETRIEVAL_SUBCATEGORIES or category == "rag_issue":
            return {
                "routes": ["retrieval_fix", "rerank_dataset", "regression_case"],
                "target_layer": "retrieval",
                "primary_route": "retrieval_fix",
                "knowledge_category": "retrieval",
                "optimization_target": "planner" if sub_category == "query_miss" else "retrieval_evaluator",
                "patch_scope": "section_retrieval",
            }
        if sub_category in self.RULE_SUBCATEGORIES or category == "rule_issue":
            return {
                "routes": ["rule_fix", "prompt_fix", "regression_case"],
                "target_layer": "rule",
                "primary_route": "rule_fix",
                "knowledge_category": "rule",
                "optimization_target": "task_question" if sub_category == "rule_mapping_error" else "reviewer",
                "patch_scope": "section_rule_mapping",
            }
        if sub_category in self.WORDING_SUBCATEGORIES or category == "prompt_issue":
            return {
                "routes": ["prompt_fix", "preference_dataset", "regression_case"],
                "target_layer": "wording",
                "primary_route": "prompt_fix",
                "knowledge_category": "wording",
                "optimization_target": "reviewer",
                "patch_scope": "review_output_style",
            }
        if sub_category in self.KNOWLEDGE_AND_REASONING_SUBCATEGORIES or category == "agent_issue":
            target_layer = self._target_layer_from_sub_category(sub_category)
            knowledge_category = self._knowledge_category_from_sub_category(sub_category)
            optimization_target = self._optimization_target_from_sub_category(sub_category)
            return {
                "routes": ["workflow_fix", "prompt_fix", "preference_dataset", "regression_case"] if decision != "valid" else ["regression_case"],
                "target_layer": target_layer,
                "primary_route": "workflow_fix" if decision != "valid" else "regression_case",
                "knowledge_category": knowledge_category,
                "optimization_target": optimization_target,
                "patch_scope": self._patch_scope_from_layer(target_layer),
            }
        if category == "workflow_issue":
            return {
                "routes": ["workflow_fix", "regression_case"],
                "target_layer": "workflow",
                "primary_route": "workflow_fix",
                "knowledge_category": "workflow",
                "optimization_target": "reviewer",
                "patch_scope": "workflow_policy",
            }
        return {
            "routes": ["regression_case"],
            "target_layer": "general",
            "primary_route": "regression_case",
            "knowledge_category": "generic",
            "optimization_target": "reviewer",
            "patch_scope": "section_general",
        }

    def route(self, feedback_record: Dict[str, object], root_cause: Dict[str, object]) -> List[str]:
        """Return downstream routes for one feedback record."""
        profile = self.route_profile(feedback_record, root_cause)
        return list(profile.get("routes", []) or ["regression_case"])

    @staticmethod
    def _target_layer_from_sub_category(sub_category: str) -> str:
        normalized = str(sub_category or "").strip()
        if normalized in {"knowledge_understanding_error", "section_fact_extraction_error"}:
            return "fact"
        if normalized in {"focus_point_miss", "task_question_error"}:
            return "task"
        if normalized in {"reasoning_chain_error", "evidence_interpretation_error", "over_inference", "under_identification", "wrong_severity"}:
            return "reasoning"
        return "general"

    @staticmethod
    def _knowledge_category_from_sub_category(sub_category: str) -> str:
        normalized = str(sub_category or "").strip()
        if normalized in {"knowledge_understanding_error", "section_fact_extraction_error"}:
            return "fact"
        if normalized in {"focus_point_miss", "task_question_error"}:
            return "task"
        if normalized in {"reasoning_chain_error", "evidence_interpretation_error", "over_inference"}:
            return "reasoning"
        if normalized in {"under_identification", "wrong_severity"}:
            return "risk"
        return "generic"

    @staticmethod
    def _optimization_target_from_sub_category(sub_category: str) -> str:
        normalized = str(sub_category or "").strip()
        if normalized in {"knowledge_understanding_error", "section_fact_extraction_error"}:
            return "planner"
        if normalized in {"focus_point_miss", "task_question_error"}:
            return "task_question"
        if normalized in {"reasoning_chain_error", "evidence_interpretation_error", "over_inference", "under_identification", "wrong_severity"}:
            return "reviewer"
        return "reviewer"

    @staticmethod
    def _patch_scope_from_layer(target_layer: str) -> str:
        normalized = str(target_layer or "").strip()
        mapping = {
            "retrieval": "section_retrieval",
            "rule": "section_rule_mapping",
            "fact": "section_fact_extraction",
            "task": "section_task_design",
            "reasoning": "section_reasoning",
            "wording": "review_output_style",
            "workflow": "workflow_policy",
        }
        return mapping.get(normalized, "section_general")
