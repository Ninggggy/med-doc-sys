from __future__ import annotations

from typing import Any, Dict, Optional, TYPE_CHECKING

from agent.agent_backend.database.mysql.db_model import (
    PreReviewFeedbackAnalysisResult,
    PreReviewProject,
)
from agent.agent_backend.services.pre_review_agent_contracts import PreReviewAgentContractBuilder
from agent.agent_backend.utils.agent_logging import log_agent_flow

if TYPE_CHECKING:
    from agent.agent_backend.services.pre_review_service import PreReviewService


class FeedbackOptimizeOrchestrator:
    """Coordinate feedback optimization and non-persistent replay helpers."""

    def __init__(self, service: "PreReviewService") -> None:
        self.service = service

    def process_feedback_closed_loop(
        self,
        feedback_record: Dict[str, Any],
        run_trace: Dict[str, Any],
        run_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        return self.service._process_feedback_closed_loop_impl(
            feedback_record=feedback_record,
            run_trace=run_trace,
            run_context=run_context,
        )

    def replay_feedback_optimize(
        self,
        feedback_record: Dict[str, Any],
        run_trace: Dict[str, Any],
        run_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        return self._run_feedback_chain(
            feedback_record=feedback_record,
            run_trace=run_trace,
            run_context=run_context,
            include_meta_reflection=False,
        )

    def replay_meta_reflection(
        self,
        feedback_record: Dict[str, Any],
        run_trace: Dict[str, Any],
        run_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        return self._run_feedback_chain(
            feedback_record=feedback_record,
            run_trace=run_trace,
            run_context=run_context,
            include_meta_reflection=True,
        )

    def build_feedback_stage_audit_bundle(
        self,
        *,
        run_id: str,
        section_id: str,
        source_doc_id: str,
        feedback_key: str,
        analyzer_input: Dict[str, Any],
        analysis_result: Dict[str, Any],
        analyzer_prompt_config: Optional[Dict[str, Any]],
        section_rules: list,
        optimizer_input: Dict[str, Any],
        patch_result: Dict[str, Any],
        optimizer_prompt_config: Optional[Dict[str, Any]],
        meta_reflection_input: Optional[Dict[str, Any]] = None,
        meta_reflection: Optional[Dict[str, Any]] = None,
        meta_reflector_prompt_config: Optional[Dict[str, Any]] = None,
        meta_reflection_result: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        stage_digests = self._build_feedback_stage_digests(
            analyzer_input=analyzer_input,
            analysis_result=analysis_result,
            section_rules=section_rules,
            optimizer_input=optimizer_input,
            patch_result=patch_result,
            meta_reflection_input=meta_reflection_input,
            meta_reflection=meta_reflection,
            meta_reflection_result=meta_reflection_result,
        )
        stage_prompt_configs = {
            "feedback_analyzer": analyzer_prompt_config,
            "feedback_optimizer": optimizer_prompt_config,
            "meta_reflector": meta_reflector_prompt_config,
        }
        audit_rows = []
        io_contract = {}
        for stage, payload in stage_digests.items():
            io_contract[stage] = {
                "input": payload["input"],
                "output": payload["output"],
            }
            build_audit_row = (
                getattr(self.service, "execution_audit_service", None).build_execution_audit_row
                if getattr(self.service, "execution_audit_service", None) is not None
                else self.service._build_execution_audit_row
            )
            audit_rows.append(
                build_audit_row(
                    run_id=run_id,
                    section_id=section_id,
                    stage=stage,
                    agent_name=stage,
                    input_digest=payload["input"],
                    output_digest=payload["output"],
                    prompt_config=stage_prompt_configs.get(stage),
                    extra_version={
                        "feedback_key": feedback_key,
                        "source_doc_id": source_doc_id,
                    },
                )
            )
        return {
            "io_contract": io_contract,
            "audit_rows": audit_rows,
        }

    def _build_feedback_stage_digests(
        self,
        *,
        analyzer_input: Dict[str, Any],
        analysis_result: Dict[str, Any],
        section_rules: list,
        optimizer_input: Dict[str, Any],
        patch_result: Dict[str, Any],
        meta_reflection_input: Optional[Dict[str, Any]] = None,
        meta_reflection: Optional[Dict[str, Any]] = None,
        meta_reflection_result: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        preview = getattr(self.service, "_preview", None) or (lambda value, max_len=240: str(value or "")[:max_len])
        normalize = self.service._normalize_text_list
        stage_digests: Dict[str, Dict[str, Any]] = {
            "feedback_analyzer": {
                "input": {
                    "decision": analyzer_input.get("decision", ""),
                    "labels": analyzer_input.get("labels", []),
                    "feedback_signals": analyzer_input.get("feedback_signals", {}),
                    "issue_feedback_count": len(analyzer_input.get("issue_feedback", [])) if isinstance(analyzer_input.get("issue_feedback", []), list) else 0,
                    "missing_item_feedback": analyzer_input.get("missing_item_feedback", {}) if isinstance(analyzer_input.get("missing_item_feedback", {}), dict) else {},
                    "focus_points": analyzer_input.get("focus_points", []),
                    "section_rule_count": len(section_rules),
                    "retrieved_material_count": len(
                        (
                            analyzer_input.get("reference_inputs", {})
                            if isinstance(analyzer_input.get("reference_inputs", {}), dict)
                            else {}
                        ).get("retrieved_materials", [])
                    ),
                    "reference_example_title": str(
                        (
                            analyzer_input.get("reference_example", {})
                            if isinstance(analyzer_input.get("reference_example", {}), dict)
                            else {}
                        ).get("title", "")
                        or ""
                    ),
                },
                "output": {
                    "primary_error_type": str((analysis_result or {}).get("primary_error_type", "") or ""),
                    "error_types": (analysis_result or {}).get("error_types", []) if isinstance((analysis_result or {}).get("error_types", []), list) else [],
                    "root_cause": preview((analysis_result or {}).get("root_cause", ""), max_len=240),
                    "new_experience_count": len((analysis_result or {}).get("new_experience", [])) if isinstance((analysis_result or {}).get("new_experience", []), list) else 0,
                },
            },
            "feedback_optimizer": {
                "input": {
                    "primary_error_type": str((analysis_result or {}).get("primary_error_type", "") or ""),
                    "error_types": (analysis_result or {}).get("error_types", []) if isinstance((analysis_result or {}).get("error_types", []), list) else [],
                    "issue_feedback_count": len(optimizer_input.get("issue_feedback", [])) if isinstance(optimizer_input.get("issue_feedback", []), list) else 0,
                    "missing_item_feedback": optimizer_input.get("missing_item_feedback", {}) if isinstance(optimizer_input.get("missing_item_feedback", {}), dict) else {},
                    "focus_points": optimizer_input.get("focus_points", []),
                    "section_rule_count": len(section_rules),
                    "reference_example_count": len(optimizer_input.get("reference_examples", [])) if isinstance(optimizer_input.get("reference_examples", []), list) else 0,
                },
                "output": {
                    "patch_count": len((patch_result or {}).get("patches", [])) if isinstance((patch_result or {}).get("patches", []), list) else 0,
                    "patch_types": normalize(
                        [item.get("patch_type", "") for item in (patch_result or {}).get("patches", []) if isinstance(item, dict)]
                    ),
                    "target_agents": normalize(
                        [item.get("target_agent", "") for item in (patch_result or {}).get("patches", []) if isinstance(item, dict)]
                    ),
                },
            },
        }
        if isinstance(meta_reflection_input, dict):
            stage_digests["meta_reflector"] = {
                "input": {
                    "iteration": meta_reflection_input.get("iteration", 0),
                    "high_frequency_doc_ids": meta_reflection_input.get("high_frequency_doc_ids", []),
                    "reference_example_title": str(
                        (
                            meta_reflection_input.get("reference_example", {})
                            if isinstance(meta_reflection_input.get("reference_example", {}), dict)
                            else {}
                        ).get("title", "")
                        or ""
                    ),
                    "retrieved_material_count": len(meta_reflection_input.get("retrieval_materials", [])) if isinstance(meta_reflection_input.get("retrieval_materials", []), list) else 0,
                },
                "output": {
                    "bad_case": bool((meta_reflection or {}).get("bad_case", False)) if isinstance(meta_reflection, dict) else False,
                    "high_frequency_doc_ids": (meta_reflection or {}).get("high_frequency_doc_ids", []) if isinstance(meta_reflection, dict) and isinstance((meta_reflection or {}).get("high_frequency_doc_ids", []), list) else [],
                    "experience_count": len((meta_reflection or {}).get("distilled_experiences", [])) if isinstance(meta_reflection, dict) and isinstance((meta_reflection or {}).get("distilled_experiences", []), list) else 0,
                    "has_few_shot_example": bool((meta_reflection_result.get("few_shot_example", {}) if isinstance(meta_reflection_result, dict) else {})),
                },
            }
        return stage_digests

    def _run_feedback_chain(
        self,
        feedback_record: Dict[str, Any],
        run_trace: Dict[str, Any],
        run_context: Dict[str, Any],
        include_meta_reflection: bool,
    ) -> Dict[str, Any]:
        session = self.service.db_conn.get_session()
        try:
            run_id = str(feedback_record.get("run_id", "") or "")
            section_id = str(feedback_record.get("section_id", "") or "")
            context = self.service._load_feedback_section_context(
                session,
                run_id=run_id,
                section_id=section_id,
                run_context=run_context,
            )
            if not context:
                return {
                    "success": False,
                    "mode": "meta_reflection" if include_meta_reflection else "feedback_optimize",
                    "error_message": f"feedback context not found for run_id={run_id}, section_id={section_id}",
                }
            run = context.get("run")
            if run is None:
                return {
                    "success": False,
                    "mode": "meta_reflection" if include_meta_reflection else "feedback_optimize",
                    "error_message": f"run not found for run_id={run_id}",
                }
            section_item = context.get("section_item", {}) if isinstance(context.get("section_item", {}), dict) else {}
            standardized_output = context.get("standardized_output", {}) if isinstance(context.get("standardized_output", {}), dict) else {}
            trace_payload = context.get("trace_payload", {}) if isinstance(context.get("trace_payload", {}), dict) else {}
            historical_experience = context.get("historical_experience", []) if isinstance(context.get("historical_experience", []), list) else []
            section_rules = context.get("section_rules", []) if isinstance(context.get("section_rules", []), list) else []
            reference_examples = context.get("reference_examples", []) if isinstance(context.get("reference_examples", []), list) else []
            project = context.get("project")
            review_domain = str(context.get("review_domain", "") or "")
            product_type = str(context.get("product_type", "") or "")
            registration_class = str(context.get("registration_class", "") or "")
            source_doc_id = str(context.get("source_doc_id", "") or "")
            base_prompt_config = context.get("base_prompt_config", {}) if isinstance(context.get("base_prompt_config", {}), dict) else {}
            selected_reference_example = (
                feedback_record.get("reference_example", {})
                if isinstance(feedback_record.get("reference_example", {}), dict)
                else {}
            )
            if not selected_reference_example and reference_examples:
                selected_reference_example = reference_examples[0]
            retrieved_materials_for_reflection = (
                run_trace.get("retrieved_materials", [])
                if isinstance(run_trace.get("retrieved_materials", []), list)
                else trace_payload.get("retrieved_materials", [])
            )
            high_frequency_doc_ids = self.service._extract_high_frequency_doc_ids(retrieved_materials_for_reflection, limit=5)
            iteration = (
                session.query(PreReviewFeedbackAnalysisResult.id)
                .filter(
                    PreReviewFeedbackAnalysisResult.run_id == run_id,
                    PreReviewFeedbackAnalysisResult.section_id == section_id,
                )
                .scalar()
                or 0
            ) + 1
            feedback_key = str(feedback_record.get("feedback_key", "") or f"{run_id}:{section_id}:replay")
            feedback_signals = PreReviewAgentContractBuilder.feedback_signals(
                conclusion_feedback=str(feedback_record.get("conclusion_feedback", "") or ""),
                retrieval_feedback=str(feedback_record.get("retrieval_feedback", "") or ""),
                evidence_feedback=feedback_record.get("evidence_feedback", []) if isinstance(feedback_record.get("evidence_feedback", []), list) else [],
                knowledge_feedback=str(feedback_record.get("knowledge_feedback", "") or ""),
                rule_feedback=str(feedback_record.get("rule_feedback", "") or ""),
                reasoning_feedback=str(feedback_record.get("reasoning_feedback", "") or ""),
                issue_feedback=feedback_record.get("issue_feedback", []) if isinstance(feedback_record.get("issue_feedback", []), list) else [],
                missing_item_feedback=feedback_record.get("missing_item_feedback", {}) if isinstance(feedback_record.get("missing_item_feedback", {}), dict) else {},
                signal_summary=feedback_record.get("signal_summary", {}) if isinstance(feedback_record.get("signal_summary", {}), dict) else {},
            )
            analyzer_input = PreReviewAgentContractBuilder.feedback_analyzer_input(
                task_id=feedback_key,
                section_id=section_id,
                section_name=str(section_item.get("section_name", "") or ""),
                raw_text=str(section_item.get("content", "") or trace_payload.get("section_packet", {}).get("raw_text", "") or ""),
                focus_points=list(section_item.get("concern_points", []) or trace_payload.get("section_packet", {}).get("focus_points", []) or []),
                section_rules=section_rules,
                system_output=standardized_output,
                user_feedback_text=str(feedback_record.get("feedback_text", "") or ""),
                decision=str(feedback_record.get("decision", "") or ""),
                labels=list(feedback_record.get("labels", []) or []),
                feedback_signals=feedback_signals,
                issue_feedback=feedback_record.get("issue_feedback", []) if isinstance(feedback_record.get("issue_feedback", []), list) else [],
                missing_item_feedback=feedback_record.get("missing_item_feedback", {}) if isinstance(feedback_record.get("missing_item_feedback", {}), dict) else {},
                reference_inputs={
                    "retrieved_materials": trace_payload.get("retrieved_materials", []) if isinstance(trace_payload.get("retrieved_materials", []), list) else [],
                    "historical_experience": historical_experience,
                    "section_rules": section_rules,
                },
                reference_example=selected_reference_example,
                trace_payload=trace_payload,
                historical_experience=historical_experience,
            ).to_dict()
            analyzer_prompt_config = self.service._compose_runtime_prompt_config(
                project_id=run.project_id,
                task_type="feedback_analyzer",
                base_prompt_config=base_prompt_config,
                section_id=section_id,
                section_name=str(section_item.get("section_name", "") or ""),
                review_domain=review_domain,
                product_type=product_type,
                registration_class=registration_class,
            )
            analysis_result = self.service.feedback_agent.analyze_feedback(analyzer_input, prompt_config=analyzer_prompt_config)
            log_agent_flow(
                "feedback_analyzer",
                "done",
                run_id=run_id,
                section_id=section_id,
                primary_error_type=str((analysis_result or {}).get("primary_error_type", "") or ""),
                error_types=(analysis_result or {}).get("error_types", []) if isinstance((analysis_result or {}).get("error_types", []), list) else [],
            )
            optimizer_input = PreReviewAgentContractBuilder.feedback_optimizer_input(
                task_id=feedback_key,
                section_id=section_id,
                section_name=str(section_item.get("section_name", "") or ""),
                raw_text=analyzer_input["raw_text"],
                focus_points=analyzer_input["focus_points"],
                section_rules=section_rules,
                feedback_signals=feedback_signals,
                reference_examples=reference_examples,
                reference_example=selected_reference_example,
                issue_feedback=feedback_record.get("issue_feedback", []) if isinstance(feedback_record.get("issue_feedback", []), list) else [],
                missing_item_feedback=feedback_record.get("missing_item_feedback", {}) if isinstance(feedback_record.get("missing_item_feedback", {}), dict) else {},
                user_feedback_text=analyzer_input["user_feedback_text"],
                feedback_analysis_result=analysis_result,
                current_templates=self.service.runtime_context_service.build_template_snapshot(),
                current_prompt_rules=trace_payload.get("prompt_rules", {}) if isinstance(trace_payload.get("prompt_rules", {}), dict) else {},
                retrieved_materials=trace_payload.get("retrieved_materials", []) if isinstance(trace_payload.get("retrieved_materials", []), list) else [],
            ).to_dict()
            optimizer_prompt_config = self.service._compose_runtime_prompt_config(
                project_id=run.project_id,
                task_type="feedback_optimizer",
                base_prompt_config=base_prompt_config,
                section_id=section_id,
                section_name=str(section_item.get("section_name", "") or ""),
                review_domain=review_domain,
                product_type=product_type,
                registration_class=registration_class,
            )
            patch_result = self.service.feedback_agent.propose_patch(optimizer_input, prompt_config=optimizer_prompt_config)
            log_agent_flow(
                "feedback_optimizer",
                "done",
                run_id=run_id,
                section_id=section_id,
                patch_count=len((patch_result or {}).get("patches", [])) if isinstance((patch_result or {}).get("patches", []), list) else 0,
            )
            active_rules = {
                "feedback_analyzer": ((analyzer_prompt_config.get("prompt_bundle", {}) if isinstance(analyzer_prompt_config.get("prompt_bundle", {}), dict) else {}).get("active_rules", {}) or {}).get("feedback_analyzer", []),
                "feedback_optimizer": ((optimizer_prompt_config.get("prompt_bundle", {}) if isinstance(optimizer_prompt_config.get("prompt_bundle", {}), dict) else {}).get("active_rules", {}) or {}).get("feedback_optimizer", []),
            }
            meta_reflection = {}
            meta_reflection_input = None
            if include_meta_reflection:
                meta_reflection_input = PreReviewAgentContractBuilder.meta_reflection_input(
                    section_id=section_id,
                    section_name=str(section_item.get("section_name", "") or section_id).strip() or section_id,
                    source_doc_id=source_doc_id,
                    focus_points=analyzer_input.get("focus_points", []),
                    section_rules=section_rules,
                    retrieval_materials=retrieved_materials_for_reflection,
                    review_output=standardized_output,
                    reference_example=selected_reference_example,
                    feedback_text=str(feedback_record.get("feedback_text", "") or ""),
                    feedback_signals=feedback_signals,
                    analysis_result=analysis_result,
                    patch_result=patch_result,
                    iteration=iteration,
                    high_frequency_doc_ids=high_frequency_doc_ids,
                ).to_dict()
                meta_reflector_prompt_config = self.service._compose_runtime_prompt_config(
                    project_id=run.project_id,
                    task_type="meta_reflector",
                    base_prompt_config=base_prompt_config,
                    section_id=section_id,
                    section_name=str(section_item.get("section_name", "") or ""),
                    review_domain=review_domain,
                    product_type=product_type,
                    registration_class=registration_class,
                )
                meta_reflection = self.service.meta_reflection_agent.reflect(meta_reflection_input, prompt_config=meta_reflector_prompt_config)
                log_agent_flow(
                    "meta_reflector",
                    "done",
                    run_id=run_id,
                    section_id=section_id,
                    bad_case=bool((meta_reflection or {}).get("bad_case", False)),
                    experience_count=len((meta_reflection or {}).get("distilled_experiences", [])) if isinstance((meta_reflection or {}).get("distilled_experiences", []), list) else 0,
                )
                active_rules["meta_reflector"] = ((meta_reflector_prompt_config.get("prompt_bundle", {}) if isinstance(meta_reflector_prompt_config.get("prompt_bundle", {}), dict) else {}).get("active_rules", {}) or {}).get("meta_reflector", [])
            audit_bundle = self.build_feedback_stage_audit_bundle(
                run_id=run_id,
                section_id=section_id,
                source_doc_id=source_doc_id,
                feedback_key=feedback_key,
                analyzer_input=analyzer_input,
                analysis_result=analysis_result if isinstance(analysis_result, dict) else {},
                analyzer_prompt_config=analyzer_prompt_config,
                section_rules=section_rules,
                optimizer_input=optimizer_input,
                patch_result=patch_result if isinstance(patch_result, dict) else {},
                optimizer_prompt_config=optimizer_prompt_config,
                meta_reflection_input=meta_reflection_input if isinstance(meta_reflection_input, dict) else None,
                meta_reflection=meta_reflection if isinstance(meta_reflection, dict) else {},
                meta_reflector_prompt_config=meta_reflector_prompt_config if include_meta_reflection else None,
                meta_reflection_result={},
            )
            return {
                "success": True,
                "mode": "meta_reflection" if include_meta_reflection else "feedback_optimize",
                "feedback_key": feedback_key,
                "analysis_result": analysis_result,
                "patch_result": patch_result,
                "meta_reflection": meta_reflection,
                "reference_example": selected_reference_example,
                "active_rules": active_rules,
                "io_contract": audit_bundle.get("io_contract", {}),
            }
        except Exception as exc:
            return {
                "success": False,
                "mode": "meta_reflection" if include_meta_reflection else "feedback_optimize",
                "error_message": str(exc),
            }
        finally:
            session.close()
