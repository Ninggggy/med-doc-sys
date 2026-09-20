from __future__ import annotations

import json
import uuid
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from agent.agent_backend.database.mysql.db_model import PreReviewExecutionAudit
from agent.agent_backend.prompts.prompt_envelope_builder import PromptEnvelopeBuilder
from agent.agent_backend.services.memory_governance_service import MemoryGovernanceService
from agent.agent_backend.services.pre_review_prompt_rule_service import TASK_TEMPLATE_MAP

if TYPE_CHECKING:
    from agent.agent_backend.services.pre_review_service import PreReviewService


class ExecutionAuditService:
    """Build, serialize, and diff execution audits without leaking audit logic into facade services."""

    def __init__(self, service: "PreReviewService") -> None:
        self.service = service

    @staticmethod
    def extract_active_rule_refs(prompt_config: Optional[Dict[str, Any]], task_type: str) -> List[Dict[str, Any]]:
        if not isinstance(prompt_config, dict):
            return []
        prompt_bundle = prompt_config.get("prompt_bundle", {}) if isinstance(prompt_config.get("prompt_bundle", {}), dict) else {}
        active_rules = prompt_bundle.get("active_rules", {}) if isinstance(prompt_bundle.get("active_rules", {}), dict) else {}
        rules = active_rules.get(task_type, []) if isinstance(active_rules.get(task_type, []), list) else []
        refs: List[Dict[str, Any]] = []
        for item in rules:
            if not isinstance(item, dict):
                continue
            refs.append(
                {
                    "rule_id": str(item.get("rule_id", "") or "").strip(),
                    "rule_code": str(item.get("rule_code", "") or "").strip(),
                    "source_type": str(item.get("source_type", "") or "").strip(),
                    "priority": int(item.get("priority", 0) or 0),
                }
            )
        return refs

    @staticmethod
    def build_execution_tool_signatures(stage: str) -> Dict[str, str]:
        shared = {
            "memory_tool": "MemoryTool",
            "prompt_rule_service": "PreReviewPromptRuleService.compose_prompt_config",
            "submission_parser": "ctd_paser.parse_ctd_submission_to_payload",
        }
        stage_specific = {
            "planner": {
                "agent_class": "PlannerReviewerAgent.plan",
            },
            "retrieval": {
                "knowledge_retriever": "KnowledgeService.semantic_query",
                "pharmacopeia_retriever": "PharmacopeiaService.search_entries",
            },
            "retrieval_evaluator": {
                "agent_class": "RetrievalEvaluatorAgent.evaluate",
            },
            "task_question": {
                "agent_class": "TaskQuestionAgent.build_questions",
            },
            "reviewer": {
                "agent_class": "PlannerReviewerAgent.review",
            },
            "feedback_analyzer": {
                "agent_class": "FeedbackAgent.analyze_feedback",
            },
            "feedback_optimizer": {
                "agent_class": "FeedbackAgent.propose_patch",
            },
            "meta_reflector": {
                "agent_class": "MetaReflectionAgent.reflect",
            },
        }
        merged = dict(shared)
        merged.update(stage_specific.get(str(stage or "").strip(), {}))
        return merged

    def build_execution_version_snapshot(
        self,
        *,
        task_type: str,
        prompt_config: Optional[Dict[str, Any]] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        try:
            envelope_info = PromptEnvelopeBuilder.version_info(str(task_type or "").strip())
        except Exception:
            envelope_info = {
                "task_type": str(task_type or "").strip(),
                "envelope_version": "",
                "protocol_version": "",
            }
        prompt_payload = prompt_config if isinstance(prompt_config, dict) else {}
        prompt_bundle = prompt_payload.get("prompt_bundle", {}) if isinstance(prompt_payload.get("prompt_bundle", {}), dict) else {}
        active_rule_refs = self.extract_active_rule_refs(prompt_payload, task_type)
        template_name = str(TASK_TEMPLATE_MAP.get(task_type, "") or "").strip()
        if not template_name and active_rule_refs:
            template_name = str(
                (
                    (
                        prompt_bundle.get("active_rules", {})
                        if isinstance(prompt_bundle.get("active_rules", {}), dict)
                        else {}
                    ).get(task_type, [{}])[0].get("template_name", "")
                )
                or ""
            ).strip()
        snapshot = {
            "task_type": envelope_info.get("task_type", str(task_type or "").strip()),
            "envelope_version": envelope_info.get("envelope_version", ""),
            "protocol_version": envelope_info.get("protocol_version", ""),
            "memory_governance_version": MemoryGovernanceService.GOVERNANCE_VERSION,
            "prompt_version_id": str(prompt_payload.get("prompt_version_id", "") or "").strip(),
            "prompt_bundle_path": str(prompt_payload.get("prompt_bundle_path", "") or "").strip(),
            "template_name": template_name,
            "template_mode": "business_material_v1",
            "active_rule_ids": [item["rule_id"] for item in active_rule_refs if item.get("rule_id")],
            "active_rule_codes": [item["rule_code"] for item in active_rule_refs if item.get("rule_code")],
            "active_rules": active_rule_refs,
            "tool_signatures": self.build_execution_tool_signatures(task_type),
        }
        if isinstance(extra, dict):
            for key, value in extra.items():
                if value is None:
                    continue
                snapshot[str(key)] = value
        return snapshot

    def build_execution_audit_row(
        self,
        *,
        run_id: str,
        section_id: str,
        stage: str,
        agent_name: str,
        input_digest: Optional[Dict[str, Any]] = None,
        output_digest: Optional[Dict[str, Any]] = None,
        prompt_config: Optional[Dict[str, Any]] = None,
        status: str = "completed",
        attempt_no: int = 1,
        error_message: str = "",
        extra_version: Optional[Dict[str, Any]] = None,
    ) -> PreReviewExecutionAudit:
        return PreReviewExecutionAudit(
            audit_id=f"audit_{uuid.uuid4().hex[:16]}",
            run_id=str(run_id or "").strip(),
            section_id=str(section_id or "").strip(),
            stage=str(stage or "").strip(),
            agent_name=str(agent_name or "").strip(),
            attempt_no=int(attempt_no or 1),
            status=str(status or "completed").strip() or "completed",
            input_digest_json=json.dumps(input_digest if isinstance(input_digest, dict) else {}, ensure_ascii=False),
            output_digest_json=json.dumps(output_digest if isinstance(output_digest, dict) else {}, ensure_ascii=False),
            version_snapshot_json=json.dumps(
                self.build_execution_version_snapshot(
                    task_type=str(stage or "").strip(),
                    prompt_config=prompt_config,
                    extra=extra_version if isinstance(extra_version, dict) else {},
                ),
                ensure_ascii=False,
            ),
            error_message=self.service._preview(error_message, max_len=1000) if str(error_message or "").strip() else None,
            create_time=self.service._now(),
        )

    @staticmethod
    def serialize_execution_audit_row(row: PreReviewExecutionAudit) -> Dict[str, Any]:
        try:
            input_digest = json.loads(getattr(row, "input_digest_json", "") or "{}")
        except Exception:
            input_digest = {}
        try:
            output_digest = json.loads(getattr(row, "output_digest_json", "") or "{}")
        except Exception:
            output_digest = {}
        try:
            version_snapshot = json.loads(getattr(row, "version_snapshot_json", "") or "{}")
        except Exception:
            version_snapshot = {}
        return {
            "audit_id": str(getattr(row, "audit_id", "") or "").strip(),
            "run_id": str(getattr(row, "run_id", "") or "").strip(),
            "section_id": str(getattr(row, "section_id", "") or "").strip(),
            "stage": str(getattr(row, "stage", "") or "").strip() or "unknown",
            "agent_name": str(getattr(row, "agent_name", "") or "").strip(),
            "attempt_no": int(getattr(row, "attempt_no", 1) or 1),
            "status": str(getattr(row, "status", "") or "").strip() or "unknown",
            "input_digest": input_digest if isinstance(input_digest, dict) else {},
            "output_digest": output_digest if isinstance(output_digest, dict) else {},
            "version_snapshot": version_snapshot if isinstance(version_snapshot, dict) else {},
            "error_message": str(getattr(row, "error_message", "") or "").strip(),
            "create_time": row.create_time.strftime("%Y-%m-%d %H:%M:%S") if getattr(row, "create_time", None) else "",
        }

    @staticmethod
    def summarize_execution_audit_items(items: List[Dict[str, Any]]) -> Dict[str, Dict[str, int]]:
        stage_breakdown: Dict[str, int] = {}
        status_breakdown: Dict[str, int] = {}
        envelope_breakdown: Dict[str, int] = {}
        protocol_breakdown: Dict[str, int] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            stage = str(item.get("stage", "") or "").strip() or "unknown"
            status = str(item.get("status", "") or "").strip() or "unknown"
            version_snapshot = item.get("version_snapshot", {}) if isinstance(item.get("version_snapshot", {}), dict) else {}
            envelope_version = str(version_snapshot.get("envelope_version", "") or "").strip() or "-"
            protocol_version = str(version_snapshot.get("protocol_version", "") or "").strip() or "-"
            stage_breakdown[stage] = int(stage_breakdown.get(stage, 0)) + 1
            status_breakdown[status] = int(status_breakdown.get(status, 0)) + 1
            envelope_breakdown[envelope_version] = int(envelope_breakdown.get(envelope_version, 0)) + 1
            protocol_breakdown[protocol_version] = int(protocol_breakdown.get(protocol_version, 0)) + 1
        return {
            "stage_breakdown": stage_breakdown,
            "status_breakdown": status_breakdown,
            "envelope_breakdown": envelope_breakdown,
            "protocol_breakdown": protocol_breakdown,
        }

    @staticmethod
    def build_execution_audit_diff(
        current_items: List[Dict[str, Any]],
        previous_items: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        compare_fields = [
            "prompt_version_id",
            "template_name",
            "template_mode",
            "task_type",
            "envelope_version",
            "protocol_version",
            "memory_governance_version",
            "active_rule_codes",
        ]
        current_by_stage = {
            str(item.get("stage", "") or "").strip(): item
            for item in current_items
            if isinstance(item, dict) and str(item.get("stage", "") or "").strip()
        }
        previous_by_stage = {
            str(item.get("stage", "") or "").strip(): item
            for item in previous_items
            if isinstance(item, dict) and str(item.get("stage", "") or "").strip()
        }
        stage_diffs: List[Dict[str, Any]] = []
        changed_stage_count = 0
        changed_field_count = 0
        stages: List[str] = []
        for stage in list(current_by_stage.keys()) + list(previous_by_stage.keys()):
            if stage and stage not in stages:
                stages.append(stage)
        for stage in stages:
            current_snapshot = current_by_stage.get(stage, {}).get("version_snapshot", {}) if isinstance(current_by_stage.get(stage, {}), dict) else {}
            previous_snapshot = previous_by_stage.get(stage, {}).get("version_snapshot", {}) if isinstance(previous_by_stage.get(stage, {}), dict) else {}
            changes: List[Dict[str, Any]] = []
            for field in compare_fields:
                current_value = current_snapshot.get(field)
                previous_value = previous_snapshot.get(field)
                if current_value != previous_value:
                    changes.append(
                        {
                            "field": field,
                            "current": current_value,
                            "previous": previous_value,
                        }
                    )
            if changes:
                changed_stage_count += 1
                changed_field_count += len(changes)
            stage_diffs.append(
                {
                    "stage": stage,
                    "changed": bool(changes),
                    "changes": changes,
                    "current": current_snapshot if isinstance(current_snapshot, dict) else {},
                    "previous": previous_snapshot if isinstance(previous_snapshot, dict) else {},
                }
            )
        return {
            "stage_diffs": stage_diffs,
            "summary": {
                "stage_total": len(stage_diffs),
                "changed_stage_count": changed_stage_count,
                "changed_field_count": changed_field_count,
            },
        }
