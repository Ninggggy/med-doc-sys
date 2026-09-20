from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


def normalize_text_list(values: Any) -> List[str]:
    if not isinstance(values, list):
        return []
    seen = set()
    out: List[str] = []
    for item in values:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def normalize_dict_list(values: Any) -> List[Dict[str, Any]]:
    if not isinstance(values, list):
        return []
    return [dict(item) for item in values if isinstance(item, dict)]


def normalize_metadata(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def normalize_dict_map(value: Any) -> Dict[str, Dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for key, item in value.items():
        map_key = str(key or "").strip()
        if not map_key or not isinstance(item, dict):
            continue
        out[map_key] = dict(item)
    return out


def normalize_field_feedback(value: Any) -> Dict[str, str]:
    if not isinstance(value, dict):
        return {}
    out: Dict[str, str] = {}
    for key in ("rule", "fact", "evidence", "reasoning", "conclusion"):
        text = str(value.get(key, "") or "").strip().lower()
        if text:
            out[key] = text
    return out


def normalize_issue_feedback_list(values: Any) -> List[Dict[str, Any]]:
    if not isinstance(values, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in values:
        if not isinstance(item, dict):
            continue
        normalized = dict(item)
        normalized["issue_key"] = str(item.get("issue_key", "") or "").strip()
        normalized["feedback_kind"] = str(item.get("feedback_kind", "") or "").strip().lower()
        normalized["verdict"] = str(item.get("verdict", "") or "").strip().lower()
        normalized["feedback_text"] = str(item.get("feedback_text", "") or "").strip()
        normalized["error_reason"] = str(item.get("error_reason", "") or "").strip()
        normalized["task_code"] = str(item.get("task_code", "") or "").strip()
        normalized["task_question"] = str(item.get("task_question", "") or "").strip()
        normalized["task_status"] = str(item.get("task_status", "") or "").strip().lower()
        normalized["rule_code"] = str(item.get("rule_code", "") or "").strip()
        normalized["rule_text"] = str(item.get("rule_text", "") or "").strip()
        normalized["requirement_point"] = str(item.get("requirement_point", "") or "").strip()
        normalized["basis"] = str(item.get("basis", "") or "").strip()
        normalized["material_fact"] = str(item.get("material_fact", "") or "").strip()
        normalized["evidence_support"] = str(item.get("evidence_support", "") or "").strip()
        normalized["comparison"] = str(item.get("comparison", "") or "").strip()
        normalized["judgment_reason"] = str(item.get("judgment_reason", "") or "").strip()
        normalized["issue"] = str(item.get("issue", "") or "").strip()
        normalized["problem"] = str(item.get("problem", "") or "").strip()
        normalized["advice"] = str(item.get("advice", "") or "").strip()
        normalized["location"] = str(item.get("location", "") or "").strip()
        normalized["violating_text"] = str(item.get("violating_text", "") or "").strip()
        normalized["field_feedback"] = normalize_field_feedback(item.get("field_feedback", {}))
        normalized["evidence_files"] = normalize_text_list(item.get("evidence_files", []))
        out.append(normalized)
    return out


def normalize_signal_summary(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    field_error_counts = value.get("field_error_counts", {}) if isinstance(value.get("field_error_counts", {}), dict) else {}
    return {
        "issue_feedback_count": int(value.get("issue_feedback_count", 0) or 0),
        "incorrect_item_count": int(value.get("incorrect_item_count", 0) or 0),
        "field_error_counts": {
            "rule": int(field_error_counts.get("rule", 0) or 0),
            "fact": int(field_error_counts.get("fact", 0) or 0),
            "evidence": int(field_error_counts.get("evidence", 0) or 0),
            "reasoning": int(field_error_counts.get("reasoning", 0) or 0),
            "conclusion": int(field_error_counts.get("conclusion", 0) or 0),
        },
        "incorrect_task_codes": normalize_text_list(value.get("incorrect_task_codes", [])),
        "missing_items": normalize_dict_list(value.get("missing_items", [])),
        "rule_issues": normalize_dict_list(value.get("rule_issues", [])),
        "fact_issues": normalize_dict_list(value.get("fact_issues", [])),
        "evidence_issues": normalize_dict_list(value.get("evidence_issues", [])),
        "reasoning_issues": normalize_dict_list(value.get("reasoning_issues", [])),
        "conclusion_issues": normalize_dict_list(value.get("conclusion_issues", [])),
    }


@dataclass
class PlannerTaskInput:
    task_id: str
    application_id: str
    section_id: str
    section_name: str
    registration_class: str
    review_domain: str
    product_type: str
    raw_text: str
    focus_points: List[str] = field(default_factory=list)
    section_rules: List[str] = field(default_factory=list)
    review_checkpoints: List[str] = field(default_factory=list)
    knowledge_priority: List[str] = field(default_factory=list)
    task_definition: Dict[str, Any] = field(default_factory=dict)
    section_review_profile: Dict[str, Any] = field(default_factory=dict)
    compliance_targets: List[str] = field(default_factory=list)
    issue_hypotheses: List[str] = field(default_factory=list)
    evidence_requirements: List[str] = field(default_factory=list)
    output_requirements: List[str] = field(default_factory=list)
    medical_entity_requirements: List[str] = field(default_factory=list)
    medical_data_requirements: List[str] = field(default_factory=list)
    reference_examples: List[Dict[str, Any]] = field(default_factory=list)
    historical_experience: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "application_id": self.application_id,
            "section_id": self.section_id,
            "section_name": self.section_name,
            "registration_class": self.registration_class,
            "review_domain": self.review_domain,
            "product_type": self.product_type,
            "raw_text": self.raw_text,
            "focus_points": list(self.focus_points),
            "section_rules": list(self.section_rules),
            "review_checkpoints": list(self.review_checkpoints),
            "knowledge_priority": list(self.knowledge_priority),
            "task_definition": dict(self.task_definition),
            "section_review_profile": dict(self.section_review_profile),
            "compliance_targets": list(self.compliance_targets),
            "issue_hypotheses": list(self.issue_hypotheses),
            "evidence_requirements": list(self.evidence_requirements),
            "output_requirements": list(self.output_requirements),
            "medical_entity_requirements": list(self.medical_entity_requirements),
            "medical_data_requirements": list(self.medical_data_requirements),
            "reference_examples": list(self.reference_examples),
            "historical_experience": list(self.historical_experience),
            "metadata": dict(self.metadata),
        }


@dataclass
class ReviewerTaskInput(PlannerTaskInput):
    retrieved_materials: List[Dict[str, Any]] = field(default_factory=list)
    review_tasks: List[Dict[str, Any]] = field(default_factory=list)
    task_questions: List[Dict[str, Any]] = field(default_factory=list)
    coverage_by_task: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    missing_evidence_by_task: List[Dict[str, Any]] = field(default_factory=list)
    retrieval_blueprint: Dict[str, Any] = field(default_factory=dict)
    classified_materials: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    evidence_bundles_by_task: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    entity_extraction_focus: List[str] = field(default_factory=list)
    data_extraction_focus: List[str] = field(default_factory=list)
    extracted_entities: Dict[str, Any] = field(default_factory=dict)
    method_judgment: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        payload = super().to_dict()
        payload["retrieved_materials"] = list(self.retrieved_materials)
        payload["review_tasks"] = list(self.review_tasks)
        payload["task_questions"] = list(self.task_questions)
        payload["coverage_by_task"] = dict(self.coverage_by_task)
        payload["missing_evidence_by_task"] = list(self.missing_evidence_by_task)
        payload["retrieval_blueprint"] = dict(self.retrieval_blueprint)
        payload["classified_materials"] = dict(self.classified_materials)
        payload["evidence_bundles_by_task"] = dict(self.evidence_bundles_by_task)
        payload["entity_extraction_focus"] = list(self.entity_extraction_focus)
        payload["data_extraction_focus"] = list(self.data_extraction_focus)
        payload["extracted_entities"] = dict(self.extracted_entities)
        payload["method_judgment"] = dict(self.method_judgment)
        return payload


@dataclass
class RetrievalEvaluatorTaskInput(PlannerTaskInput):
    retrieved_materials: List[Dict[str, Any]] = field(default_factory=list)
    historical_bad_retrievals: List[Dict[str, Any]] = field(default_factory=list)
    review_tasks: List[Dict[str, Any]] = field(default_factory=list)
    retrieval_blueprint: Dict[str, Any] = field(default_factory=dict)
    entity_extraction_focus: List[str] = field(default_factory=list)
    data_extraction_focus: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        payload = super().to_dict()
        payload["retrieved_materials"] = list(self.retrieved_materials)
        payload["historical_bad_retrievals"] = list(self.historical_bad_retrievals)
        payload["review_tasks"] = list(self.review_tasks)
        payload["retrieval_blueprint"] = dict(self.retrieval_blueprint)
        payload["entity_extraction_focus"] = list(self.entity_extraction_focus)
        payload["data_extraction_focus"] = list(self.data_extraction_focus)
        return payload


@dataclass
class TaskQuestionTaskInput(PlannerTaskInput):
    review_tasks: List[Dict[str, Any]] = field(default_factory=list)
    retrieved_materials: List[Dict[str, Any]] = field(default_factory=list)
    rejected_materials: List[Dict[str, Any]] = field(default_factory=list)
    retrieval_evaluation_result: Dict[str, Any] = field(default_factory=dict)
    retrieval_blueprint: Dict[str, Any] = field(default_factory=dict)
    entity_extraction_focus: List[str] = field(default_factory=list)
    data_extraction_focus: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        payload = super().to_dict()
        payload["review_tasks"] = list(self.review_tasks)
        payload["retrieved_materials"] = list(self.retrieved_materials)
        payload["rejected_materials"] = list(self.rejected_materials)
        payload["retrieval_evaluation_result"] = dict(self.retrieval_evaluation_result)
        payload["retrieval_blueprint"] = dict(self.retrieval_blueprint)
        payload["entity_extraction_focus"] = list(self.entity_extraction_focus)
        payload["data_extraction_focus"] = list(self.data_extraction_focus)
        return payload


@dataclass
class FeedbackSignals:
    conclusion_feedback: str = ""
    retrieval_feedback: str = ""
    evidence_feedback: List[Dict[str, Any]] = field(default_factory=list)
    knowledge_feedback: str = ""
    rule_feedback: str = ""
    reasoning_feedback: str = ""
    issue_feedback: List[Dict[str, Any]] = field(default_factory=list)
    missing_item_feedback: Dict[str, Any] = field(default_factory=dict)
    signal_summary: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "conclusion_feedback": self.conclusion_feedback,
            "retrieval_feedback": self.retrieval_feedback,
            "evidence_feedback": list(self.evidence_feedback),
            "knowledge_feedback": self.knowledge_feedback,
            "rule_feedback": self.rule_feedback,
            "reasoning_feedback": self.reasoning_feedback,
            "issue_feedback": list(self.issue_feedback),
            "missing_item_feedback": dict(self.missing_item_feedback),
            "signal_summary": dict(self.signal_summary),
        }


@dataclass
class FeedbackAnalyzerTaskInput:
    task_id: str
    section_id: str
    raw_text: str
    section_name: str = ""
    focus_points: List[str] = field(default_factory=list)
    section_rules: List[str] = field(default_factory=list)
    system_output: Dict[str, Any] = field(default_factory=dict)
    user_feedback_text: str = ""
    decision: str = ""
    labels: List[str] = field(default_factory=list)
    feedback_signals: FeedbackSignals = field(default_factory=FeedbackSignals)
    issue_feedback: List[Dict[str, Any]] = field(default_factory=list)
    missing_item_feedback: Dict[str, Any] = field(default_factory=dict)
    reference_example: Dict[str, Any] = field(default_factory=dict)
    reference_inputs: Dict[str, Any] = field(default_factory=dict)
    trace_payload: Dict[str, Any] = field(default_factory=dict)
    historical_experience: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "section_id": self.section_id,
            "section_name": self.section_name,
            "raw_text": self.raw_text,
            "focus_points": list(self.focus_points),
            "section_rules": list(self.section_rules),
            "system_output": dict(self.system_output),
            "user_feedback_text": self.user_feedback_text,
            "feedback_text": self.user_feedback_text,
            "decision": self.decision,
            "labels": list(self.labels),
            "feedback_signals": self.feedback_signals.to_dict(),
            "issue_feedback": list(self.issue_feedback),
            "missing_item_feedback": dict(self.missing_item_feedback),
            "reference_example": dict(self.reference_example),
            "reference_inputs": dict(self.reference_inputs),
            "trace_payload": dict(self.trace_payload),
            "historical_experience": list(self.historical_experience),
        }


@dataclass
class FeedbackOptimizerTaskInput:
    task_id: str
    section_id: str
    raw_text: str
    section_name: str = ""
    focus_points: List[str] = field(default_factory=list)
    section_rules: List[str] = field(default_factory=list)
    reference_examples: List[Dict[str, Any]] = field(default_factory=list)
    reference_example: Dict[str, Any] = field(default_factory=dict)
    feedback_signals: FeedbackSignals = field(default_factory=FeedbackSignals)
    issue_feedback: List[Dict[str, Any]] = field(default_factory=list)
    missing_item_feedback: Dict[str, Any] = field(default_factory=dict)
    user_feedback_text: str = ""
    feedback_analysis_result: Dict[str, Any] = field(default_factory=dict)
    current_templates: Dict[str, Any] = field(default_factory=dict)
    current_prompt_rules: Dict[str, Any] = field(default_factory=dict)
    retrieved_materials: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "section_id": self.section_id,
            "section_name": self.section_name,
            "raw_text": self.raw_text,
            "focus_points": list(self.focus_points),
            "section_rules": list(self.section_rules),
            "reference_examples": list(self.reference_examples),
            "reference_example": dict(self.reference_example),
            "feedback_signals": self.feedback_signals.to_dict(),
            "issue_feedback": list(self.issue_feedback),
            "missing_item_feedback": dict(self.missing_item_feedback),
            "user_feedback_text": self.user_feedback_text,
            "feedback_analysis_result": dict(self.feedback_analysis_result),
            "current_templates": dict(self.current_templates),
            "current_prompt_rules": dict(self.current_prompt_rules),
            "retrieved_materials": list(self.retrieved_materials),
        }


@dataclass
class MetaReflectionTaskInput:
    section_id: str
    section_name: str
    source_doc_id: str
    focus_points: List[str] = field(default_factory=list)
    section_rules: List[str] = field(default_factory=list)
    retrieval_materials: List[Dict[str, Any]] = field(default_factory=list)
    review_output: Dict[str, Any] = field(default_factory=dict)
    reference_example: Dict[str, Any] = field(default_factory=dict)
    feedback_text: str = ""
    feedback_signals: FeedbackSignals = field(default_factory=FeedbackSignals)
    analysis_result: Dict[str, Any] = field(default_factory=dict)
    patch_result: Dict[str, Any] = field(default_factory=dict)
    iteration: int = 0
    high_frequency_doc_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "section_id": self.section_id,
            "section_name": self.section_name,
            "source_doc_id": self.source_doc_id,
            "focus_points": list(self.focus_points),
            "section_rules": list(self.section_rules),
            "retrieval_materials": list(self.retrieval_materials),
            "review_output": dict(self.review_output),
            "reference_example": dict(self.reference_example),
            "feedback_text": self.feedback_text,
            "feedback_signals": self.feedback_signals.to_dict(),
            "analysis_result": dict(self.analysis_result),
            "patch_result": dict(self.patch_result),
            "iteration": self.iteration,
            "high_frequency_doc_ids": list(self.high_frequency_doc_ids),
        }


class PreReviewAgentContractBuilder:
    """Centralize all agent IO contracts to avoid field drift across service layers."""

    @staticmethod
    def planner_input(
        *,
        task_id: str,
        application_id: str,
        section_id: str,
        section_name: str,
        registration_class: str,
        review_domain: str,
        product_type: str,
        raw_text: str,
        focus_points: Any,
        section_rules: Any,
        review_checkpoints: Any = None,
        knowledge_priority: Any = None,
        task_definition: Any = None,
        section_review_profile: Any = None,
        compliance_targets: Any = None,
        issue_hypotheses: Any = None,
        evidence_requirements: Any = None,
        output_requirements: Any = None,
        medical_entity_requirements: Any = None,
        medical_data_requirements: Any = None,
        reference_examples: Any = None,
        historical_experience: Any = None,
        metadata: Any = None,
    ) -> PlannerTaskInput:
        return PlannerTaskInput(
            task_id=str(task_id or "").strip(),
            application_id=str(application_id or "").strip(),
            section_id=str(section_id or "").strip(),
            section_name=str(section_name or "").strip(),
            registration_class=str(registration_class or "").strip(),
            review_domain=str(review_domain or "").strip(),
            product_type=str(product_type or "").strip(),
            raw_text=str(raw_text or "").strip(),
            focus_points=normalize_text_list(focus_points),
            section_rules=normalize_text_list(section_rules),
            review_checkpoints=normalize_text_list(review_checkpoints),
            knowledge_priority=normalize_text_list(knowledge_priority),
            task_definition=normalize_metadata(task_definition),
            section_review_profile=normalize_metadata(section_review_profile),
            compliance_targets=normalize_text_list(compliance_targets),
            issue_hypotheses=normalize_text_list(issue_hypotheses),
            evidence_requirements=normalize_text_list(evidence_requirements),
            output_requirements=normalize_text_list(output_requirements),
            medical_entity_requirements=normalize_text_list(medical_entity_requirements),
            medical_data_requirements=normalize_text_list(medical_data_requirements),
            reference_examples=normalize_dict_list(reference_examples),
            historical_experience=normalize_dict_list(historical_experience),
            metadata=normalize_metadata(metadata),
        )

    @staticmethod
    def reviewer_input(
        planner_input: PlannerTaskInput,
        *,
        raw_text: str,
        retrieved_materials: Any,
        review_tasks: Any = None,
        task_questions: Any = None,
        coverage_by_task: Any = None,
        missing_evidence_by_task: Any = None,
        retrieval_blueprint: Any = None,
        classified_materials: Any = None,
        evidence_bundles_by_task: Any = None,
        entity_extraction_focus: Any = None,
        data_extraction_focus: Any = None,
        extracted_entities: Any = None,
        method_judgment: Any = None,
        reference_examples: Any = None,
        historical_experience: Any = None,
        section_rules: Any = None,
    ) -> ReviewerTaskInput:
        return ReviewerTaskInput(
            task_id=planner_input.task_id,
            application_id=planner_input.application_id,
            section_id=planner_input.section_id,
            section_name=planner_input.section_name,
            registration_class=planner_input.registration_class,
            review_domain=planner_input.review_domain,
            product_type=planner_input.product_type,
            raw_text=str(raw_text or "").strip(),
            focus_points=list(planner_input.focus_points),
            section_rules=normalize_text_list(section_rules if section_rules is not None else planner_input.section_rules),
            review_checkpoints=list(planner_input.review_checkpoints),
            knowledge_priority=list(planner_input.knowledge_priority),
            task_definition=dict(planner_input.task_definition),
            section_review_profile=dict(planner_input.section_review_profile),
            compliance_targets=list(planner_input.compliance_targets),
            issue_hypotheses=list(planner_input.issue_hypotheses),
            evidence_requirements=list(planner_input.evidence_requirements),
            output_requirements=list(planner_input.output_requirements),
            medical_entity_requirements=list(planner_input.medical_entity_requirements),
            medical_data_requirements=list(planner_input.medical_data_requirements),
            reference_examples=normalize_dict_list(reference_examples if reference_examples is not None else planner_input.reference_examples),
            historical_experience=normalize_dict_list(historical_experience if historical_experience is not None else planner_input.historical_experience),
            metadata=dict(planner_input.metadata),
            retrieved_materials=normalize_dict_list(retrieved_materials),
            review_tasks=normalize_dict_list(review_tasks),
            task_questions=normalize_dict_list(task_questions),
            coverage_by_task=normalize_dict_map(coverage_by_task),
            missing_evidence_by_task=normalize_dict_list(missing_evidence_by_task),
            retrieval_blueprint=normalize_metadata(retrieval_blueprint),
            classified_materials={
                str(key or "").strip(): normalize_dict_list(item)
                for key, item in (classified_materials.items() if isinstance(classified_materials, dict) else [])
                if str(key or "").strip()
            },
            evidence_bundles_by_task=normalize_dict_map(evidence_bundles_by_task),
            entity_extraction_focus=normalize_text_list(entity_extraction_focus),
            data_extraction_focus=normalize_text_list(data_extraction_focus),
            extracted_entities=normalize_metadata(extracted_entities),
            method_judgment=normalize_metadata(method_judgment),
        )

    @staticmethod
    def retrieval_evaluator_input(
        planner_input: PlannerTaskInput,
        *,
        raw_text: str,
        retrieved_materials: Any,
        historical_bad_retrievals: Any,
        review_tasks: Any = None,
        retrieval_blueprint: Any = None,
        entity_extraction_focus: Any = None,
        data_extraction_focus: Any = None,
        reference_examples: Any = None,
        historical_experience: Any = None,
        section_rules: Any = None,
    ) -> RetrievalEvaluatorTaskInput:
        return RetrievalEvaluatorTaskInput(
            task_id=planner_input.task_id,
            application_id=planner_input.application_id,
            section_id=planner_input.section_id,
            section_name=planner_input.section_name,
            registration_class=planner_input.registration_class,
            review_domain=planner_input.review_domain,
            product_type=planner_input.product_type,
            raw_text=str(raw_text or "").strip(),
            focus_points=list(planner_input.focus_points),
            section_rules=normalize_text_list(section_rules if section_rules is not None else planner_input.section_rules),
            review_checkpoints=list(planner_input.review_checkpoints),
            knowledge_priority=list(planner_input.knowledge_priority),
            task_definition=dict(planner_input.task_definition),
            section_review_profile=dict(planner_input.section_review_profile),
            compliance_targets=list(planner_input.compliance_targets),
            issue_hypotheses=list(planner_input.issue_hypotheses),
            evidence_requirements=list(planner_input.evidence_requirements),
            output_requirements=list(planner_input.output_requirements),
            medical_entity_requirements=list(planner_input.medical_entity_requirements),
            medical_data_requirements=list(planner_input.medical_data_requirements),
            reference_examples=normalize_dict_list(reference_examples if reference_examples is not None else planner_input.reference_examples),
            historical_experience=normalize_dict_list(historical_experience if historical_experience is not None else planner_input.historical_experience),
            metadata=dict(planner_input.metadata),
            retrieved_materials=normalize_dict_list(retrieved_materials),
            historical_bad_retrievals=normalize_dict_list(historical_bad_retrievals),
            review_tasks=normalize_dict_list(review_tasks),
            retrieval_blueprint=normalize_metadata(retrieval_blueprint),
            entity_extraction_focus=normalize_text_list(entity_extraction_focus),
            data_extraction_focus=normalize_text_list(data_extraction_focus),
        )

    @staticmethod
    def task_question_input(
        planner_input: PlannerTaskInput,
        *,
        raw_text: str,
        review_tasks: Any,
        retrieved_materials: Any,
        rejected_materials: Any,
        retrieval_evaluation_result: Any,
        retrieval_blueprint: Any = None,
        entity_extraction_focus: Any = None,
        data_extraction_focus: Any = None,
        reference_examples: Any = None,
        historical_experience: Any = None,
        section_rules: Any = None,
    ) -> TaskQuestionTaskInput:
        return TaskQuestionTaskInput(
            task_id=planner_input.task_id,
            application_id=planner_input.application_id,
            section_id=planner_input.section_id,
            section_name=planner_input.section_name,
            registration_class=planner_input.registration_class,
            review_domain=planner_input.review_domain,
            product_type=planner_input.product_type,
            raw_text=str(raw_text or "").strip(),
            focus_points=list(planner_input.focus_points),
            section_rules=normalize_text_list(section_rules if section_rules is not None else planner_input.section_rules),
            review_checkpoints=list(planner_input.review_checkpoints),
            knowledge_priority=list(planner_input.knowledge_priority),
            task_definition=dict(planner_input.task_definition),
            section_review_profile=dict(planner_input.section_review_profile),
            compliance_targets=list(planner_input.compliance_targets),
            issue_hypotheses=list(planner_input.issue_hypotheses),
            evidence_requirements=list(planner_input.evidence_requirements),
            output_requirements=list(planner_input.output_requirements),
            medical_entity_requirements=list(planner_input.medical_entity_requirements),
            medical_data_requirements=list(planner_input.medical_data_requirements),
            reference_examples=normalize_dict_list(reference_examples if reference_examples is not None else planner_input.reference_examples),
            historical_experience=normalize_dict_list(historical_experience if historical_experience is not None else planner_input.historical_experience),
            metadata=dict(planner_input.metadata),
            review_tasks=normalize_dict_list(review_tasks),
            retrieved_materials=normalize_dict_list(retrieved_materials),
            rejected_materials=normalize_dict_list(rejected_materials),
            retrieval_evaluation_result=normalize_metadata(retrieval_evaluation_result),
            retrieval_blueprint=normalize_metadata(retrieval_blueprint),
            entity_extraction_focus=normalize_text_list(entity_extraction_focus),
            data_extraction_focus=normalize_text_list(data_extraction_focus),
        )

    @staticmethod
    def feedback_signals(
        *,
        conclusion_feedback: str = "",
        retrieval_feedback: str = "",
        evidence_feedback: Any = None,
        knowledge_feedback: str = "",
        rule_feedback: str = "",
        reasoning_feedback: str = "",
        issue_feedback: Any = None,
        missing_item_feedback: Any = None,
        signal_summary: Any = None,
    ) -> FeedbackSignals:
        return FeedbackSignals(
            conclusion_feedback=str(conclusion_feedback or "").strip(),
            retrieval_feedback=str(retrieval_feedback or "").strip(),
            evidence_feedback=normalize_dict_list(evidence_feedback),
            knowledge_feedback=str(knowledge_feedback or "").strip(),
            rule_feedback=str(rule_feedback or "").strip(),
            reasoning_feedback=str(reasoning_feedback or "").strip(),
            issue_feedback=normalize_issue_feedback_list(issue_feedback),
            missing_item_feedback=normalize_metadata(missing_item_feedback),
            signal_summary=normalize_signal_summary(signal_summary),
        )

    @staticmethod
    def feedback_analyzer_input(
        *,
        task_id: str,
        section_id: str,
        section_name: str = "",
        raw_text: str,
        focus_points: Any,
        section_rules: Any,
        system_output: Any,
        user_feedback_text: str,
        decision: str,
        labels: Any,
        feedback_signals: FeedbackSignals,
        issue_feedback: Any = None,
        missing_item_feedback: Any = None,
        reference_example: Any = None,
        reference_inputs: Any = None,
        trace_payload: Any = None,
        historical_experience: Any = None,
    ) -> FeedbackAnalyzerTaskInput:
        return FeedbackAnalyzerTaskInput(
            task_id=str(task_id or "").strip(),
            section_id=str(section_id or "").strip(),
            section_name=str(section_name or "").strip(),
            raw_text=str(raw_text or "").strip(),
            focus_points=normalize_text_list(focus_points),
            section_rules=normalize_text_list(section_rules),
            system_output=normalize_metadata(system_output),
            user_feedback_text=str(user_feedback_text or "").strip(),
            decision=str(decision or "").strip(),
            labels=normalize_text_list(labels),
            feedback_signals=feedback_signals,
            issue_feedback=normalize_issue_feedback_list(issue_feedback),
            missing_item_feedback=normalize_metadata(missing_item_feedback),
            reference_example=normalize_metadata(reference_example),
            reference_inputs=normalize_metadata(reference_inputs),
            trace_payload=normalize_metadata(trace_payload),
            historical_experience=normalize_dict_list(historical_experience),
        )

    @staticmethod
    def feedback_optimizer_input(
        *,
        task_id: str,
        section_id: str,
        section_name: str = "",
        raw_text: str,
        focus_points: Any,
        section_rules: Any,
        reference_examples: Any,
        reference_example: Any,
        feedback_signals: FeedbackSignals,
        issue_feedback: Any = None,
        missing_item_feedback: Any = None,
        user_feedback_text: str = "",
        feedback_analysis_result: Any = None,
        current_templates: Any = None,
        current_prompt_rules: Any = None,
        retrieved_materials: Any = None,
    ) -> FeedbackOptimizerTaskInput:
        return FeedbackOptimizerTaskInput(
            task_id=str(task_id or "").strip(),
            section_id=str(section_id or "").strip(),
            section_name=str(section_name or "").strip(),
            raw_text=str(raw_text or "").strip(),
            focus_points=normalize_text_list(focus_points),
            section_rules=normalize_text_list(section_rules),
            reference_examples=normalize_dict_list(reference_examples),
            reference_example=normalize_metadata(reference_example),
            feedback_signals=feedback_signals,
            issue_feedback=normalize_issue_feedback_list(issue_feedback),
            missing_item_feedback=normalize_metadata(missing_item_feedback),
            user_feedback_text=str(user_feedback_text or "").strip(),
            feedback_analysis_result=normalize_metadata(feedback_analysis_result),
            current_templates=normalize_metadata(current_templates),
            current_prompt_rules=normalize_metadata(current_prompt_rules),
            retrieved_materials=normalize_dict_list(retrieved_materials),
        )

    @staticmethod
    def meta_reflection_input(
        *,
        section_id: str,
        section_name: str,
        source_doc_id: str,
        focus_points: Any,
        section_rules: Any,
        retrieval_materials: Any,
        review_output: Any,
        reference_example: Any,
        feedback_text: str,
        feedback_signals: FeedbackSignals,
        analysis_result: Any,
        patch_result: Any,
        iteration: int,
        high_frequency_doc_ids: Any,
    ) -> MetaReflectionTaskInput:
        return MetaReflectionTaskInput(
            section_id=str(section_id or "").strip(),
            section_name=str(section_name or "").strip(),
            source_doc_id=str(source_doc_id or "").strip(),
            focus_points=normalize_text_list(focus_points),
            section_rules=normalize_text_list(section_rules),
            retrieval_materials=normalize_dict_list(retrieval_materials),
            review_output=normalize_metadata(review_output),
            reference_example=normalize_metadata(reference_example),
            feedback_text=str(feedback_text or "").strip(),
            feedback_signals=feedback_signals,
            analysis_result=normalize_metadata(analysis_result),
            patch_result=normalize_metadata(patch_result),
            iteration=int(iteration or 0),
            high_frequency_doc_ids=normalize_text_list(high_frequency_doc_ids),
        )
