from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from sqlalchemy import and_

from agent.agent_backend.database.mysql.db_model import (
    PreReviewProject,
    PreReviewRun,
    PreReviewSectionOutput,
    PreReviewSectionTrace,
)
from agent.agent_backend.infrastructure.repositories.pre_review_repository import PreReviewRepository
from agent.agent_backend.feedback.evaluation.metrics_calculator import MetricsCalculator
from agent.agent_backend.services.section_review_profile_registry import SectionReviewProfileRegistry

if TYPE_CHECKING:
    from agent.agent_backend.services.pre_review_service import PreReviewService


@dataclass
class SectionReviewRuntimeContext:
    registration_class: str = ""
    review_domain: str = ""
    product_type: str = ""
    focus_points: List[str] = field(default_factory=list)
    section_rules: List[str] = field(default_factory=list)
    task_definition: Dict[str, Any] = field(default_factory=dict)
    compliance_targets: List[str] = field(default_factory=list)
    issue_hypotheses: List[str] = field(default_factory=list)
    evidence_requirements: List[str] = field(default_factory=list)
    output_requirements: List[str] = field(default_factory=list)
    medical_entity_requirements: List[str] = field(default_factory=list)
    medical_data_requirements: List[str] = field(default_factory=list)
    historical_experience: List[Dict[str, Any]] = field(default_factory=list)
    historical_bad_retrievals: List[Dict[str, Any]] = field(default_factory=list)
    reference_examples: List[Dict[str, Any]] = field(default_factory=list)
    section_review_profile: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "registration_class": self.registration_class,
            "review_domain": self.review_domain,
            "product_type": self.product_type,
            "focus_points": list(self.focus_points),
            "section_rules": list(self.section_rules),
            "task_definition": dict(self.task_definition),
            "compliance_targets": list(self.compliance_targets),
            "issue_hypotheses": list(self.issue_hypotheses),
            "evidence_requirements": list(self.evidence_requirements),
            "output_requirements": list(self.output_requirements),
            "medical_entity_requirements": list(self.medical_entity_requirements),
            "medical_data_requirements": list(self.medical_data_requirements),
            "historical_experience": list(self.historical_experience),
            "historical_bad_retrievals": list(self.historical_bad_retrievals),
            "reference_examples": list(self.reference_examples),
            "section_review_profile": dict(self.section_review_profile),
        }


@dataclass
class FeedbackSectionRuntimeContext:
    run: Any = None
    project: Any = None
    section_item: Dict[str, Any] = field(default_factory=dict)
    standardized_output: Dict[str, Any] = field(default_factory=dict)
    trace_payload: Dict[str, Any] = field(default_factory=dict)
    historical_experience: List[Dict[str, Any]] = field(default_factory=list)
    section_rules: List[str] = field(default_factory=list)
    task_definition: Dict[str, Any] = field(default_factory=dict)
    compliance_targets: List[str] = field(default_factory=list)
    issue_hypotheses: List[str] = field(default_factory=list)
    evidence_requirements: List[str] = field(default_factory=list)
    output_requirements: List[str] = field(default_factory=list)
    medical_entity_requirements: List[str] = field(default_factory=list)
    medical_data_requirements: List[str] = field(default_factory=list)
    reference_examples: List[Dict[str, Any]] = field(default_factory=list)
    registration_class: str = ""
    review_domain: str = ""
    product_type: str = ""
    source_doc_id: str = ""
    base_prompt_config: Dict[str, Any] = field(default_factory=dict)
    section_review_profile: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "run": self.run,
            "project": self.project,
            "section_item": dict(self.section_item),
            "standardized_output": dict(self.standardized_output),
            "trace_payload": dict(self.trace_payload),
            "historical_experience": list(self.historical_experience),
            "section_rules": list(self.section_rules),
            "task_definition": dict(self.task_definition),
            "compliance_targets": list(self.compliance_targets),
            "issue_hypotheses": list(self.issue_hypotheses),
            "evidence_requirements": list(self.evidence_requirements),
            "output_requirements": list(self.output_requirements),
            "medical_entity_requirements": list(self.medical_entity_requirements),
            "medical_data_requirements": list(self.medical_data_requirements),
            "reference_examples": list(self.reference_examples),
            "registration_class": self.registration_class,
            "review_domain": self.review_domain,
            "product_type": self.product_type,
            "source_doc_id": self.source_doc_id,
            "base_prompt_config": dict(self.base_prompt_config),
            "section_review_profile": dict(self.section_review_profile),
        }


class PreReviewRuntimeContextService:
    """Centralize runtime context assembly for section review and feedback loops."""

    def __init__(self, service: "PreReviewService") -> None:
        self.service = service
        self.section_review_profile_registry = getattr(service, "section_review_profile_registry", SectionReviewProfileRegistry())

    def _normalize_text_list(self, values: Any) -> List[str]:
        normalizer = getattr(self.service, "_normalize_text_list", None)
        if callable(normalizer):
            return normalizer(values)
        out: List[str] = []
        seen = set()
        for item in values or []:
            text = str(item or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            out.append(text)
        return out

    @staticmethod
    def resolve_feature_flags(
        *,
        run_config: Optional[Dict[str, Any]] = None,
        base_prompt_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, bool]:
        merged = MetricsCalculator.build_feature_flags()
        sources = []
        if isinstance(run_config, dict):
            sources.append(run_config.get("feature_flags", {}))
        if isinstance(base_prompt_config, dict):
            sources.append(base_prompt_config.get("feature_flags", {}))
        for source in sources:
            if not isinstance(source, dict):
                continue
            merged.update(MetricsCalculator.normalize_feature_overrides(source))
        return merged

    def resolve_registration_class(
        self,
        project: Optional[PreReviewProject],
        run_config: Optional[Dict[str, Any]] = None,
    ) -> str:
        config = run_config if isinstance(run_config, dict) else {}
        explicit_value = str(config.get("registration_class", "") or "").strip()
        if explicit_value:
            return self.service._compact_text(explicit_value, max_len=64)
        if project is None:
            return ""
        return self.service._infer_registration_class(project)

    def compose_prompt_config(
        self,
        *,
        project_id: str,
        task_type: str,
        base_prompt_config: Optional[Dict[str, Any]] = None,
        section_id: str = "",
        section_name: str = "",
        review_domain: str = "",
        product_type: str = "",
        registration_class: str = "",
    ) -> Dict[str, Any]:
        feature_flags = self.resolve_feature_flags(base_prompt_config=base_prompt_config)
        if not feature_flags.get("use_prompt_rules", True):
            prompt_config = dict(base_prompt_config or {})
            prompt_config["feature_flags"] = feature_flags
            prompt_config.pop("prompt_bundle", None)
            prompt_config.pop("prompt_bundle_path", None)
            prompt_config.pop("prompt_version_id", None)
            return prompt_config
        session = self.service.db_conn.get_session()
        try:
            prompt_config = self.service.prompt_rule_service.compose_prompt_config(
                session=session,
                project_id=project_id,
                base_prompt_config=base_prompt_config or {},
                task_type=task_type,
                section_id=section_id,
                section_name=section_name,
                review_domain=review_domain,
                product_type=product_type,
                registration_class=registration_class,
            )
            prompt_config["feature_flags"] = feature_flags
            return prompt_config
        finally:
            session.close()

    def build_template_snapshot(self) -> Dict[str, str]:
        return {
            "planner_prompt": self.service._load_current_template_text("chapter_planner.j2"),
            "retrieval_evaluator_prompt": self.service._load_current_template_text("retrieval_evaluator.j2"),
            "task_question_prompt": self.service._load_current_template_text("task_question.j2"),
            "pre_review_prompt": self.service._load_current_template_text("chapter_reviewer.j2"),
            "feedback_analyzer_prompt": self.service._load_current_template_text("feedback_analyzer.j2"),
            "feedback_optimizer_prompt": self.service._load_current_template_text("feedback_optimizer.j2"),
        }

    def build_section_review_context(
        self,
        *,
        project: PreReviewProject,
        project_id: str,
        section_id: str,
        section_name: str,
        chunk: Optional[Dict[str, Any]] = None,
        run_config: Optional[Dict[str, Any]] = None,
    ) -> SectionReviewRuntimeContext:
        chunk_payload = chunk if isinstance(chunk, dict) else {}
        feature_flags = self.resolve_feature_flags(run_config=run_config)
        registration_class = self.resolve_registration_class(project, run_config=run_config)
        review_domain = self.service._infer_review_domain(project)
        product_type = self.service._infer_product_type(project, section_name=section_name)
        section_review_profile = self.section_review_profile_registry.resolve(
            section_id=section_id,
            section_name=section_name,
        )
        focus_points = self.service._resolve_chunk_focus_points(
            project_id=project_id,
            section_id=section_id,
            chunk=chunk_payload,
            run_config=run_config if isinstance(run_config, dict) else {},
        )
        focus_points = self._normalize_text_list(
            focus_points
            + list(section_review_profile.get("core_review_principles", []))
            + list(section_review_profile.get("must_answer_questions", []))
        )
        section_rules = self.service._resolve_chunk_section_rules(
            project_id=project_id,
            section_id=section_id,
            chunk=chunk_payload,
        ) if feature_flags.get("use_section_rules", True) else []
        session = self.service.db_conn.get_session()
        try:
            historical_experience = self.service._load_historical_experience(
                session=session,
                project_id=project_id,
                section_id=section_id,
                product_type=product_type,
            ) if feature_flags.get("use_experience_memory", True) else []
            historical_bad_retrievals = self.service._load_historical_bad_retrievals(
                session=session,
                project_id=project_id,
                section_id=section_id,
            ) if feature_flags.get("use_historical_bad_retrievals", True) else []
            reference_examples = self.service._load_section_reference_examples(
                session=session,
                project_id=project_id,
                section_id=section_id,
                limit=3,
            ) if feature_flags.get("use_reference_examples", True) else []
        finally:
            session.close()
        return SectionReviewRuntimeContext(
            registration_class=registration_class,
            review_domain=review_domain,
            product_type=product_type,
            focus_points=focus_points,
            section_rules=section_rules,
            historical_experience=historical_experience,
            historical_bad_retrievals=historical_bad_retrievals,
            reference_examples=reference_examples,
            section_review_profile=section_review_profile,
        )

    def load_feedback_context(
        self,
        session,
        *,
        run_id: str,
        section_id: str,
        run_context: Optional[Dict[str, Any]] = None,
    ) -> FeedbackSectionRuntimeContext:
        run = session.query(PreReviewRun).filter(PreReviewRun.run_id == run_id).first()
        if run is None:
            return FeedbackSectionRuntimeContext()
        ok, _, section_payload = self.service.get_submission_sections(project_id=run.project_id, doc_id=run.source_doc_id)
        sections = section_payload.get("sections", []) if ok and isinstance(section_payload, dict) else []
        section_item: Dict[str, Any] = {}
        for item in sections:
            if not isinstance(item, dict):
                continue
            if str(item.get("section_id", "") or "").strip() == section_id:
                section_item = item
                break
        output_row = (
            session.query(PreReviewSectionOutput)
            .filter(
                and_(
                    PreReviewSectionOutput.run_id == run_id,
                    PreReviewSectionOutput.section_id == section_id,
                )
            )
            .order_by(PreReviewSectionOutput.id.desc())
            .first()
        )
        standardized_output: Dict[str, Any] = {}
        if output_row is not None:
            try:
                standardized_output = json.loads(output_row.output_json or "{}")
            except Exception:
                standardized_output = {}
        trace_row = (
            session.query(PreReviewSectionTrace)
            .filter(
                and_(
                    PreReviewSectionTrace.run_id == run_id,
                    PreReviewSectionTrace.section_id == section_id,
                )
            )
            .order_by(PreReviewSectionTrace.id.desc())
            .first()
        )
        trace_payload: Dict[str, Any] = {}
        if trace_row is not None:
            trace_payload = PreReviewRepository.load_trace_payload(trace_row.trace_json)
        io_contract = trace_payload.get("io_contract", {}) if isinstance(trace_payload, dict) and isinstance(trace_payload.get("io_contract", {}), dict) else {}
        planner_stage = io_contract.get("planner", {}) if isinstance(io_contract.get("planner", {}), dict) else {}
        planner_input = planner_stage.get("input", {}) if isinstance(planner_stage.get("input", {}), dict) else {}
        if not isinstance(planner_input, dict):
            planner_input = {}
        project = session.query(PreReviewProject).filter(PreReviewProject.project_id == run.project_id).first()
        review_domain = self.service._infer_review_domain(project)
        product_type = self.service._infer_product_type(
            project,
            section_name=str(section_item.get("section_name", "") or ""),
        )
        historical_experience = self.service._load_historical_experience(
            session=session,
            project_id=str(getattr(run, "project_id", "") or ""),
            section_id=section_id,
            product_type=product_type,
        )
        section_rules = self.service._load_project_section_rule_map(session, run.project_id).get(section_id, [])
        reference_examples = self.service._load_section_reference_examples(
            session=session,
            project_id=run.project_id,
            section_id=section_id,
            limit=6,
        )
        section_review_profile = planner_input.get("section_review_profile", {}) if isinstance(planner_input.get("section_review_profile", {}), dict) else self.section_review_profile_registry.resolve(
            section_id=section_id,
            section_name=str(section_item.get("section_name", "") or ""),
        )
        run_ctx = run_context if isinstance(run_context, dict) else {}
        base_prompt_config = run_ctx.get("prompt_config", {}) if isinstance(run_ctx.get("prompt_config", {}), dict) else {}
        return FeedbackSectionRuntimeContext(
            run=run,
            project=project,
            section_item=section_item if isinstance(section_item, dict) else {},
            standardized_output=standardized_output if isinstance(standardized_output, dict) else {},
            trace_payload=trace_payload if isinstance(trace_payload, dict) else {},
            historical_experience=historical_experience,
            section_rules=section_rules,
            task_definition=planner_input.get("task_definition", {}) if isinstance(planner_input.get("task_definition", {}), dict) else {},
            compliance_targets=self.service._normalize_text_list(planner_input.get("compliance_targets", [])),
            issue_hypotheses=self.service._normalize_text_list(planner_input.get("issue_hypotheses", [])),
            evidence_requirements=self.service._normalize_text_list(planner_input.get("evidence_requirements", [])),
            output_requirements=self.service._normalize_text_list(planner_input.get("output_requirements", [])),
            medical_entity_requirements=self.service._normalize_text_list(planner_input.get("medical_entity_requirements", [])),
            medical_data_requirements=self.service._normalize_text_list(planner_input.get("medical_data_requirements", [])),
            reference_examples=reference_examples,
            registration_class=self.resolve_registration_class(project, run_config=run_ctx.get("run_config", {})),
            review_domain=review_domain,
            product_type=product_type,
            source_doc_id=str(getattr(run, "source_doc_id", "") or "").strip(),
            base_prompt_config=base_prompt_config,
            section_review_profile=section_review_profile,
        )
