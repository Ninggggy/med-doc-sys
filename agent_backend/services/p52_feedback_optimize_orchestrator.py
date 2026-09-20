from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List

from agent.agent_backend.services.p52_feedback_classifier import P52FeedbackClassifier
from agent.agent_backend.services.p52_patch_registry_service import P52PatchRegistryService
from agent.agent_backend.services.p52_replay_verifier import P52ReplayVerifier


class P52FeedbackOptimizeOrchestrator:
    """编排 3.2.P.5.2 专用反馈优化链路。"""

    _TARGET_MAP = {
        "method_profile_error": {
            "patch_type": "profile_patch",
            "target_agent": "p52_method_profile",
            "target_file": "agent_backend/services/p52_rule_engine_service.py",
        },
        "entity_extraction_error": {
            "patch_type": "entity_patch",
            "target_agent": "p52_entity_extractor",
            "target_file": "agent_backend/services/p52_rule_review_orchestrator.py",
        },
        "quality_standard_mapping_error": {
            "patch_type": "entity_patch",
            "target_agent": "p52_quality_standard_mapper",
            "target_file": "agent_backend/services/p52_rule_review_orchestrator.py",
        },
        "rule_false_positive": {
            "patch_type": "rule_patch",
            "target_agent": "p52_rule_engine",
            "target_file": "agent_backend/data/rule/p52_method_review/rules.json",
        },
        "rule_false_negative": {
            "patch_type": "rule_patch",
            "target_agent": "p52_rule_engine",
            "target_file": "agent_backend/data/rule/p52_method_review/rules.json",
        },
        "retrieval_scope_error": {
            "patch_type": "retrieval_patch",
            "target_agent": "p52_retriever",
            "target_file": "agent_backend/services/p52_review_toolkit.py",
        },
        "retrieval_ranking_error": {
            "patch_type": "retrieval_patch",
            "target_agent": "p52_retriever",
            "target_file": "agent_backend/services/p52_rule_review_orchestrator.py",
        },
        "reviewer_reasoning_error": {
            "patch_type": "reviewer_patch",
            "target_agent": "p52_reviewer",
            "target_file": "agent_backend/prompts/pre_review_agent_prompt/p52_chapter_reviewer.j2",
        },
        "result_merge_error": {
            "patch_type": "merge_patch",
            "target_agent": "p52_result_merger",
            "target_file": "agent_backend/services/p52_rule_review_orchestrator.py",
        },
        "frontend_projection_error": {
            "patch_type": "frontend_patch",
            "target_agent": "p52_frontend_projection",
            "target_file": "agent_fronted/src/components/pre-review/PreReviewResultDialog.vue",
        },
    }

    def __init__(self, service: Any) -> None:
        self.service = service
        self.classifier = P52FeedbackClassifier()
        self.registry = P52PatchRegistryService(service.db_conn)
        self.replay_verifier = P52ReplayVerifier(service)

    def optimize(
        self,
        *,
        run_id: str,
        section_id: str,
        feedback_record: Dict[str, Any],
        run_trace: Dict[str, Any],
        run_context: Dict[str, Any],
        workflow_id: str = "",
    ) -> Dict[str, Any]:
        self.service._log_p52_workflow_event(
            "p52_feedback_optimize",
            "classify_start",
            workflow_id=workflow_id,
            run_id=run_id,
            section_id=section_id,
        )
        trace_snapshot = self._extract_trace_snapshot(run_trace)
        classification = self.classifier.classify(feedback_record, trace_snapshot)
        self.service._log_p52_workflow_event(
            "p52_feedback_optimize",
            "classify_completed",
            workflow_id=workflow_id,
            run_id=run_id,
            section_id=section_id,
            primary_error_type=str(classification.get("primary_error_type", "") or "").strip(),
            error_type_count=len(classification.get("error_types", []) if isinstance(classification.get("error_types", []), list) else []),
        )
        localizations = self._localize_targets(classification)
        optimizer_result = self.service.p52_feedback_optimizer_agent.propose(
            {
                "run_id": run_id,
                "section_id": section_id,
                "section_name": str((run_trace.get("section_name", "") if isinstance(run_trace, dict) else "") or "").strip(),
                "feedback_record": feedback_record,
                "trace_snapshot": trace_snapshot,
                "classification": classification,
                "localizations": localizations,
                "run_context": run_context,
            }
        )
        optimizer_patches = optimizer_result.get("patches", []) if isinstance(optimizer_result.get("patches", []), list) else []
        candidate_patches = self._normalize_optimizer_patches(
            run_id=run_id,
            feedback_record=feedback_record,
            trace_snapshot=trace_snapshot,
            classification=classification,
            localizations=localizations,
            optimizer_patches=optimizer_patches,
        )
        self.service._log_p52_workflow_event(
            "p52_feedback_optimize",
            "candidate_patch_built",
            workflow_id=workflow_id,
            run_id=run_id,
            section_id=section_id,
            candidate_patch_count=len(candidate_patches),
            optimizer_used_model=not bool((optimizer_result.get("llm_execution", {}) if isinstance(optimizer_result.get("llm_execution", {}), dict) else {}).get("used_default_fallback", False)),
        )
        feedback_key = str(feedback_record.get("feedback_key", "") or f"p52_feedback:{run_id}:{section_id}:{uuid.uuid4().hex[:8]}")
        persisted_patches = self.registry.create_patches(
            run_id=run_id,
            section_id=section_id,
            feedback_key=feedback_key,
            patches=candidate_patches,
        )
        self.service._log_p52_workflow_event(
            "p52_feedback_optimize",
            "patch_registry_completed",
            workflow_id=workflow_id,
            run_id=run_id,
            section_id=section_id,
            persisted_patch_count=len(persisted_patches),
            feedback_key=feedback_key,
        )
        verification_plan = self.replay_verifier.plan(
            run_id=run_id,
            section_id=section_id,
            run_trace=run_trace,
            run_context=run_context,
            feedback_record=feedback_record,
            candidate_patches=persisted_patches,
        )
        self.service._log_p52_workflow_event(
            "p52_feedback_optimize",
            "verification_plan_completed",
            workflow_id=workflow_id,
            run_id=run_id,
            section_id=section_id,
            verification_goal_count=len(verification_plan.get("verification_goals", []) if isinstance(verification_plan.get("verification_goals", []), list) else []),
        )
        return {
            "success": True,
            "workflow_mode": "p52_feedback_optimize_v1",
            "workflow_id": workflow_id,
            "feedback_key": feedback_key,
            "analysis_result": {
                "feedback_record": feedback_record,
                "trace_snapshot": self._build_trace_snapshot_summary(trace_snapshot),
                "error_classification": classification,
                "target_localization": localizations,
                "run_context": run_context,
                "optimizer_result": optimizer_result,
            },
            "candidate_patches": persisted_patches,
            "verification_plan": verification_plan,
        }

    def replay_verify(
        self,
        *,
        run_id: str,
        section_id: str,
        feedback_record: Dict[str, Any],
        run_trace: Dict[str, Any],
        run_context: Dict[str, Any],
        patch_rows: List[Dict[str, Any]],
        workflow_id: str = "",
    ) -> Dict[str, Any]:
        self.service._log_p52_workflow_event(
            "p52_feedback_verify",
            "verification_run_start",
            workflow_id=workflow_id,
            run_id=run_id,
            section_id=section_id,
            selected_patch_count=len(patch_rows),
        )
        verification_result = self.replay_verifier.verify(
            run_id=run_id,
            section_id=section_id,
            run_trace=run_trace,
            run_context=run_context,
            feedback_record=feedback_record,
            candidate_patches=patch_rows,
        )
        self.service._log_p52_workflow_event(
            "p52_feedback_verify",
            "verification_run_completed",
            workflow_id=workflow_id,
            run_id=run_id,
            section_id=section_id,
            overall_verdict=str(verification_result.get("overall_verdict", "") or "").strip(),
        )
        return {
            "success": True,
            "workflow_mode": "p52_feedback_replay_verify_v1",
            "workflow_id": workflow_id,
            "verification_result": verification_result,
            "patches": patch_rows,
        }

    def list_patches(self, *, run_id: str, section_id: str, status: str = "") -> List[Dict[str, Any]]:
        return self.registry.list_patches(run_id=run_id, section_id=section_id, status=status)

    def approve_patch(
        self,
        *,
        run_id: str,
        section_id: str,
        patch_id: str,
        operator: str = "",
        comment: str = "",
    ) -> Dict[str, Any] | None:
        return self.registry.update_patch_status(
            run_id=run_id,
            section_id=section_id,
            patch_id=patch_id,
            status="approved",
            operator=operator,
            comment=comment,
        )

    def reject_patch(
        self,
        *,
        run_id: str,
        section_id: str,
        patch_id: str,
        operator: str = "",
        comment: str = "",
    ) -> Dict[str, Any] | None:
        return self.registry.update_patch_status(
            run_id=run_id,
            section_id=section_id,
            patch_id=patch_id,
            status="rejected",
            operator=operator,
            comment=comment,
        )

    @staticmethod
    def _extract_trace_snapshot(run_trace: Dict[str, Any]) -> Dict[str, Any]:
        trace_block = run_trace.get("trace", {}) if isinstance(run_trace.get("trace", {}), dict) else {}
        if trace_block:
            return trace_block
        return run_trace if isinstance(run_trace, dict) else {}

    def _localize_targets(self, classification: Dict[str, Any]) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        for error_type in classification.get("error_types", []) or []:
            mapping = self._TARGET_MAP.get(str(error_type).strip(), {})
            if not mapping:
                continue
            items.append(
                {
                    "error_type": str(error_type).strip(),
                    "patch_type": str(mapping.get("patch_type", "") or "").strip(),
                    "target_agent": str(mapping.get("target_agent", "") or "").strip(),
                    "target_file": str(mapping.get("target_file", "") or "").strip(),
                }
            )
        return items

    def _build_candidate_patches(
        self,
        *,
        run_id: str,
        feedback_record: Dict[str, Any],
        trace_snapshot: Dict[str, Any],
        classification: Dict[str, Any],
        localizations: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        candidate_rule_ids = classification.get("candidate_rule_ids", []) if isinstance(classification.get("candidate_rule_ids", []), list) else []
        method_profiles = classification.get("method_profiles", []) if isinstance(classification.get("method_profiles", []), list) else []
        feedback_text = str(feedback_record.get("feedback_text", "") or "").strip()
        review_result = trace_snapshot.get("review_result", {}) if isinstance(trace_snapshot.get("review_result", {}), dict) else {}
        section_summary = str(review_result.get("section_summary", "") or "").strip()
        patches: List[Dict[str, Any]] = []
        for index, item in enumerate(localizations, start=1):
            error_type = str(item.get("error_type", "") or "").strip()
            target_file = str(item.get("target_file", "") or "").strip()
            patch_payload = {
                "patch_id": f"p52_patch_{uuid.uuid4().hex[:12]}",
                "patch_type": str(item.get("patch_type", "") or "rule_patch"),
                "target_agent": str(item.get("target_agent", "") or "p52_feedback_optimize"),
                "target_scope": section_id,
                "trigger_condition": f"section_id startswith '3.2.p.5.2' and error_type == '{error_type}'",
                "patch_content": self._build_patch_content(
                    error_type=error_type,
                    feedback_text=feedback_text,
                    method_profiles=method_profiles,
                    candidate_rule_ids=candidate_rule_ids,
                    section_summary=section_summary,
                ),
                "status": "candidate",
                "version": index,
                "payload": {
                    "target_file": target_file,
                    "target_key": self._build_target_key(error_type=error_type, rule_ids=candidate_rule_ids, method_profiles=method_profiles),
                    "reason": feedback_text or "基于 P52 反馈优化链路自动生成候选 patch。",
                    "source_feedback_text": feedback_text,
                    "method_profiles": method_profiles,
                    "candidate_rule_ids": candidate_rule_ids,
                    "section_rules": candidate_rule_ids,
                    "trace_summary": section_summary,
                    "overlay": self._build_fallback_overlay(
                        patch_type=str(item.get("patch_type", "") or "rule_patch"),
                        target_agent=str(item.get("target_agent", "") or "p52_feedback_optimize"),
                        classification=classification,
                    ),
                    "run_id": run_id,
                },
            }
            patches.append(patch_payload)
        return patches

    def _normalize_optimizer_patches(
        self,
        *,
        run_id: str,
        feedback_record: Dict[str, Any],
        trace_snapshot: Dict[str, Any],
        classification: Dict[str, Any],
        localizations: List[Dict[str, Any]],
        optimizer_patches: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        if not optimizer_patches:
            return []
        localize_by_type = {
            str(item.get("patch_type", "") or "").strip(): item
            for item in localizations
            if isinstance(item, dict) and str(item.get("patch_type", "") or "").strip()
        }
        feedback_text = str(feedback_record.get("feedback_text", "") or "").strip()
        review_result = trace_snapshot.get("review_result", {}) if isinstance(trace_snapshot.get("review_result", {}), dict) else {}
        section_summary = str(review_result.get("section_summary", "") or "").strip()
        normalized: List[Dict[str, Any]] = []
        for index, item in enumerate(optimizer_patches, start=1):
            if not isinstance(item, dict):
                continue
            patch_type = str(item.get("patch_type", "") or "").strip()
            target_meta = localize_by_type.get(patch_type, {})
            target_agent = str(item.get("target_agent", "") or target_meta.get("target_agent", "") or "").strip()
            if not patch_type or not target_agent:
                continue
            target_file = str(item.get("target_file", "") or target_meta.get("target_file", "") or "").strip()
            payload_block = {
                "target_file": target_file,
                "target_key": str(item.get("target_key", "") or target_meta.get("error_type", "") or patch_type).strip(),
                "reason": str(item.get("patch_content", "") or "").strip() or feedback_text or "P52 反馈优化候选 patch。",
                "source_feedback_text": feedback_text,
                "method_profiles": item.get("method_profiles", []) if isinstance(item.get("method_profiles", []), list) else classification.get("method_profiles", []),
                "candidate_rule_ids": item.get("candidate_rule_ids", []) if isinstance(item.get("candidate_rule_ids", []), list) else classification.get("candidate_rule_ids", []),
                "section_rules": item.get("section_rules", []) if isinstance(item.get("section_rules", []), list) else classification.get("candidate_rule_ids", []),
                "trace_summary": section_summary,
                "overlay": item.get("overlay", {}) if isinstance(item.get("overlay", {}), dict) else {},
                "verification_focus": item.get("verification_focus", []) if isinstance(item.get("verification_focus", []), list) else [],
                "rule_change": item.get("rule_change", {}) if isinstance(item.get("rule_change", {}), dict) else {},
                "run_id": run_id,
            }
            normalized.append(
                {
                    "patch_id": str(item.get("patch_id", "") or f"p52_patch_{uuid.uuid4().hex[:12]}"),
                    "patch_type": patch_type,
                    "target_agent": target_agent,
                    "target_scope": str(item.get("target_scope", "") or "").strip(),
                    "trigger_condition": str(item.get("trigger_condition", "") or f"section_id startswith '3.2.p.5.2'").strip(),
                    "patch_content": str(item.get("patch_content", "") or "").strip() or self._build_patch_content(
                        error_type=str(target_meta.get("error_type", "") or classification.get("primary_error_type", "") or "").strip(),
                        feedback_text=feedback_text,
                        method_profiles=payload_block["method_profiles"] if isinstance(payload_block["method_profiles"], list) else [],
                        candidate_rule_ids=payload_block["candidate_rule_ids"] if isinstance(payload_block["candidate_rule_ids"], list) else [],
                        section_summary=section_summary,
                    ),
                    "status": str(item.get("status", "") or "candidate").strip() or "candidate",
                    "version": index,
                    "target_file": target_file,
                    "target_key": str(payload_block.get("target_key", "") or "").strip(),
                    "method_profiles": payload_block["method_profiles"],
                    "candidate_rule_ids": payload_block["candidate_rule_ids"],
                    "section_rules": payload_block["section_rules"],
                    "verification_focus": payload_block["verification_focus"],
                    "rule_change": payload_block["rule_change"],
                    "payload": payload_block,
                }
            )
        return normalized

    @staticmethod
    def _build_patch_content(
        *,
        error_type: str,
        feedback_text: str,
        method_profiles: List[str],
        candidate_rule_ids: List[str],
        section_summary: str,
    ) -> str:
        lines = [
            f"错误类型: {error_type}",
            f"方法域: {'、'.join(method_profiles) if method_profiles else '待确认'}",
            f"候选规则: {'、'.join(candidate_rule_ids) if candidate_rule_ids else '无'}",
            f"反馈摘要: {feedback_text or '无'}",
            f"当前结论摘要: {section_summary or '无'}",
        ]
        return "\n".join(lines).strip()

    @staticmethod
    def _build_fallback_overlay(
        *,
        patch_type: str,
        target_agent: str,
        classification: Dict[str, Any],
    ) -> Dict[str, Any]:
        method_profiles = classification.get("method_profiles", []) if isinstance(classification.get("method_profiles", []), list) else []
        candidate_rule_ids = classification.get("candidate_rule_ids", []) if isinstance(classification.get("candidate_rule_ids", []), list) else []
        overlay = {
            "force_method_profiles": [],
            "exclude_method_profiles": [],
            "include_rule_ids": [],
            "exclude_rule_ids": [],
            "retrieval_keyword_hints": [],
            "retrieval_section_patterns": [],
            "prompt_suffixes": {},
            "entity_hints": {},
            "rule_keyword_hints": {},
        }
        if patch_type == "profile_patch":
            overlay["force_method_profiles"] = method_profiles[:3]
            overlay["prompt_suffixes"] = {
                "method_judgment": [
                    "本轮反馈提示方法域识别存在偏差。请优先根据章节主题、方法术语和 5.1 映射校正方法类型。"
                ]
            }
        elif patch_type == "entity_patch":
            overlay["entity_hints"] = {
                "focus_fields": ["mobile_phase", "column_name", "decision_rule", "linked_spec_item"],
                "exclude_entities": ["sample_preparation"],
                "field_keywords": {},
            }
            overlay["prompt_suffixes"] = {
                "entity_extraction": [
                    "请只抽取能直接支撑审评判断的关键实体，避免把无关短语或上下文碎片当成实体。"
                ]
            }
        elif patch_type == "rule_patch":
            overlay["exclude_rule_ids"] = candidate_rule_ids[:4]
            overlay["prompt_suffixes"] = {
                "rule_filter": [
                    "规则过滤时必须优先核对章节事实与规则要求是否直接对应，避免泛化误报。"
                ]
            }
        elif patch_type == "retrieval_patch":
            overlay["retrieval_section_patterns"] = ["3.2.p.5.1", "3.2.p.5.3.*", "3.2.p.5.6"]
            overlay["prompt_suffixes"] = {
                "retrieval": [
                    "检索补证时优先围绕质量标准映射、方法学验证和关联控制项目展开，避免跨方法域噪声。"
                ]
            }
        elif patch_type == "reviewer_patch":
            overlay["prompt_suffixes"] = {
                "reviewer": [
                    "判断时必须直接引用章节事实和补证，不要输出空泛模板句。"
                ]
            }
        elif patch_type == "merge_patch":
            overlay["prompt_suffixes"] = {
                "result_merge": [
                    "结果聚合时优先保留模型返回的结构化判断，禁止把 fallback 兜底结果与已生成的任务项混流。"
                ]
            }
        return overlay

    @staticmethod
    def _build_target_key(*, error_type: str, rule_ids: List[str], method_profiles: List[str]) -> str:
        if rule_ids:
            return f"{error_type}:{','.join(rule_ids[:3])}"
        if method_profiles:
            return f"{error_type}:{','.join(method_profiles[:2])}"
        return error_type

    @staticmethod
    def _build_trace_snapshot_summary(trace_snapshot: Dict[str, Any]) -> Dict[str, Any]:
        method_context = trace_snapshot.get("method_context", {}) if isinstance(trace_snapshot.get("method_context", {}), dict) else {}
        review_result = trace_snapshot.get("review_result", {}) if isinstance(trace_snapshot.get("review_result", {}), dict) else {}
        candidate_findings = trace_snapshot.get("candidate_findings", []) if isinstance(trace_snapshot.get("candidate_findings", []), list) else []
        entities = trace_snapshot.get("entity_extraction", {}) if isinstance(trace_snapshot.get("entity_extraction", {}), dict) else {}
        return {
            "method_profiles": method_context.get("method_profiles", []) if isinstance(method_context.get("method_profiles", []), list) else [],
            "candidate_findings": [
                {
                    "rule_id": str(item.get("rule_id", "") or "").strip(),
                    "rule_name": str(item.get("rule_name", "") or "").strip(),
                }
                for item in candidate_findings[:8]
                if isinstance(item, dict)
            ],
            "entity_keys": list((entities.get("entities", {}) if isinstance(entities.get("entities", {}), dict) else {}).keys())[:12],
            "section_summary": str(review_result.get("section_summary", "") or "").strip(),
            "conclusion": str(review_result.get("pre_review_conclusion", "") or "").strip(),
        }
