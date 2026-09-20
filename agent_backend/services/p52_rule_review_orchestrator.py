from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from agent.agent_backend.infrastructure.repositories.pre_review_repository import (
    SectionConclusionRecord,
    SectionTraceRecord,
)
from agent.agent_backend.services.pre_review_agent_contracts import PreReviewAgentContractBuilder
from agent.agent_backend.services.p52_review_toolkit import P52ReviewToolkit
from agent.agent_backend.services.p52_rule_engine_service import P52RuleEngineService
from agent.agent_backend.utils.agent_logging import log_agent_flow

if TYPE_CHECKING:
    from agent.agent_backend.database.mysql.db_model import PreReviewProject
    from agent.agent_backend.services.pre_review_service import PreReviewService


class P52RuleReviewOrchestrator:
    """3.2.P.5.2 专用规则判别链路。"""

    def __init__(self, service: "PreReviewService") -> None:
        self.service = service
        self.rule_engine = P52RuleEngineService()
        self.toolkit = P52ReviewToolkit(service)

    def _resolve_runtime_overlay(
        self,
        *,
        run_id: str,
        section_id: str,
        run_config: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        config = run_config if isinstance(run_config, dict) else {}
        embedded_overlay = config.get("p52_runtime_overlay", {}) if isinstance(config.get("p52_runtime_overlay", {}), dict) else {}
        if embedded_overlay:
            return dict(embedded_overlay)
        patch_rows = config.get("p52_runtime_patch_rows", []) if isinstance(config.get("p52_runtime_patch_rows", []), list) else None
        return self.service.p52_patch_apply_service.build_runtime_overlay(
            run_id=run_id,
            section_id=section_id,
            patch_rows=patch_rows,
            include_approved=bool(config.get("p52_use_approved_patches", True)),
            include_candidate=bool(config.get("p52_use_candidate_patches", False)),
        )

    @staticmethod
    def _append_prompt_suffix(base_prompt: str, rows: List[str]) -> str:
        suffix_lines = [str(item or "").strip() for item in rows if str(item or "").strip()]
        if not suffix_lines:
            return str(base_prompt or "")
        return f"{str(base_prompt or '').rstrip()}\n\n运行时 Patch 提示：\n- " + "\n- ".join(suffix_lines)

    def _apply_runtime_overlay_to_method_context(
        self,
        *,
        method_context: Dict[str, Any],
        runtime_overlay: Dict[str, Any],
    ) -> Dict[str, Any]:
        merged = dict(method_context if isinstance(method_context, dict) else {})
        force_profiles = [
            str(item).strip()
            for item in runtime_overlay.get("force_method_profiles", [])
            if isinstance(runtime_overlay.get("force_method_profiles", []), list) and str(item).strip()
        ]
        exclude_profiles = {
            str(item).strip()
            for item in runtime_overlay.get("exclude_method_profiles", [])
            if isinstance(runtime_overlay.get("exclude_method_profiles", []), list) and str(item).strip()
        }
        profiles = [
            str(item).strip()
            for item in merged.get("method_profiles", [])
            if isinstance(merged.get("method_profiles", []), list) and str(item).strip()
        ]
        if force_profiles:
            profiles = force_profiles
        profiles = [item for item in profiles if item not in exclude_profiles]
        merged["method_profiles"] = list(dict.fromkeys(profiles))
        merged["method_type"] = "、".join(merged.get("method_profiles", []))
        merged["method_purpose"] = self.rule_engine._infer_method_purpose(merged.get("method_profiles", []))
        entity_hints = runtime_overlay.get("entity_hints", {}) if isinstance(runtime_overlay.get("entity_hints", {}), dict) else {}
        field_keywords = entity_hints.get("field_keywords", {}) if isinstance(entity_hints.get("field_keywords", {}), dict) else {}
        for field_name, keywords in field_keywords.items():
            key = str(field_name or "").strip()
            if not key:
                continue
            payload = merged.get(key, {}) if isinstance(merged.get(key, {}), dict) else {}
            if payload.get("found"):
                continue
            excerpt = self.toolkit.find_keyword_evidence(
                str(merged.get("_source_text", "") or ""),
                [str(item).strip() for item in keywords if str(item).strip()],
                window=80,
            )
            if not excerpt:
                continue
            merged[key] = {
                "found": True,
                "excerpt": excerpt,
                "keywords": [str(item).strip() for item in keywords if str(item).strip()],
                "source": "runtime_patch_overlay",
            }
        return merged

    @staticmethod
    def _apply_runtime_overlay_to_candidates(
        *,
        candidate_findings: List[Dict[str, Any]],
        runtime_overlay: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        include_rule_ids = {
            str(item).strip()
            for item in runtime_overlay.get("include_rule_ids", [])
            if isinstance(runtime_overlay.get("include_rule_ids", []), list) and str(item).strip()
        }
        exclude_rule_ids = {
            str(item).strip()
            for item in runtime_overlay.get("exclude_rule_ids", [])
            if isinstance(runtime_overlay.get("exclude_rule_ids", []), list) and str(item).strip()
        }
        rule_keyword_hints = runtime_overlay.get("rule_keyword_hints", {}) if isinstance(runtime_overlay.get("rule_keyword_hints", {}), dict) else {}
        out: List[Dict[str, Any]] = []
        seen = set()
        for item in candidate_findings if isinstance(candidate_findings, list) else []:
            if not isinstance(item, dict):
                continue
            rule_id = str(item.get("rule_id", "") or "").strip()
            if rule_id and rule_id in exclude_rule_ids:
                continue
            row = dict(item)
            if rule_id and rule_id in rule_keyword_hints:
                existing_keywords = row.get("evidence_keywords", []) if isinstance(row.get("evidence_keywords", []), list) else []
                row["evidence_keywords"] = list(dict.fromkeys(existing_keywords + [str(keyword).strip() for keyword in rule_keyword_hints.get(rule_id, []) if str(keyword).strip()]))
            dedupe_key = rule_id or str(row.get("problem_item", "") or "").strip()
            if dedupe_key and dedupe_key not in seen:
                seen.add(dedupe_key)
                out.append(row)
        for rule_id in include_rule_ids:
            if rule_id in seen:
                continue
            out.append(
                {
                    "rule_id": rule_id,
                    "rule_name": rule_id,
                    "rule_display_text": rule_id,
                    "severity": "major",
                    "problem_item": f"运行时 patch 强制保留规则 {rule_id}",
                    "reasoning": "该规则由反馈优化 patch 指定为必须保留，需在本次回放中重点核对。",
                    "revision_suggestion": "请核查该规则是否真的应适用于当前章节，并根据回放结果继续收敛。",
                    "fields": [],
                    "method_type": "",
                    "retrieval_sections": [],
                    "knowledge_query": "",
                    "evidence_keywords": [],
                    "current_evidence": [],
                }
            )
        return out

    def review_single_chunk(
        self,
        project: "PreReviewProject",
        project_id: str,
        run_id: str,
        source_doc_id: str,
        chunk: Dict[str, Any],
        previous_section_meta: Optional[Dict[str, Any]] = None,
        run_config: Optional[Dict[str, Any]] = None,
        progress_callback=None,
    ) -> Dict[str, Any]:
        section_id = str(chunk.get("section_id") or chunk.get("chunk_id", "") or "").strip().lower()
        section_code = str(chunk.get("section_code") or section_id).strip() or section_id
        section_name = str(chunk.get("section_name") or chunk.get("title") or section_code).strip() or section_code
        text = str(chunk.get("text", "") or "").strip()
        workflow_id = self.service._build_p52_workflow_id("p52_pre_review", run_id, section_id)
        if not text:
            return {"success": False, "message": "empty section text", "section_id": section_id}
        runtime_overlay = self._resolve_runtime_overlay(
            run_id=run_id,
            section_id=section_id,
            run_config=run_config,
        )

        self.service._log_p52_workflow_event(
            "p52_pre_review",
            "start",
            workflow_id=workflow_id,
            run_id=run_id,
            section_id=section_id,
            section_code=section_code,
            section_name=section_name,
            overlay_patch_count=len(runtime_overlay.get("source_patch_ids", []) if isinstance(runtime_overlay.get("source_patch_ids", []), list) else []),
        )

        self.service._emit_progress(
            progress_callback,
            "p52_rule_review_start",
            f"开始 3.2.P.5.2 规则判别审评：{section_code} {section_name}".strip(),
            section_id=section_id,
            section_code=section_code,
            section_name=section_name,
        )

        submission_section_map = self.toolkit.load_submission_section_map(project_id=project_id, source_doc_id=source_doc_id)
        method_context = self.rule_engine.build_method_context(
            section_id=section_id,
            section_name=section_name,
            text=text,
        )
        method_context = self._apply_runtime_overlay_to_method_context(
            method_context=method_context,
            runtime_overlay=runtime_overlay,
        )
        quality_standard_context = self._extract_quality_standard_context(
            section_id=section_id,
            section_name=section_name,
            method_context=method_context,
            submission_section_map=submission_section_map,
            runtime_overlay=runtime_overlay,
        )
        method_context = self._merge_quality_standard_context(
            method_context=method_context,
            quality_standard_context=quality_standard_context,
        )
        analysis_extraction = self._extract_method_scope_and_entities_with_llm(
            section_id=section_id,
            section_name=section_name,
            text=text,
            method_context=method_context,
            runtime_overlay=runtime_overlay,
        )
        entity_extraction = analysis_extraction.get("entity_extraction", {}) if isinstance(analysis_extraction.get("entity_extraction", {}), dict) else {}
        method_judgment = analysis_extraction.get("method_judgment", {}) if isinstance(analysis_extraction.get("method_judgment", {}), dict) else {}
        self.service._log_p52_workflow_event(
            "p52_pre_review",
            "entity_extraction_completed",
            workflow_id=workflow_id,
            run_id=run_id,
            section_id=section_id,
            entity_count=len(entity_extraction.get("medical_key_entities", []) if isinstance(entity_extraction.get("medical_key_entities", []), list) else []),
        )
        method_context = self._merge_entity_extraction(
            method_context=method_context,
            entity_extraction=entity_extraction,
        )
        llm_method_profiles = method_judgment.get("method_profiles", []) if isinstance(method_judgment.get("method_profiles", []), list) else []
        if llm_method_profiles:
            heuristic_profiles = method_context.get("method_profiles", []) if isinstance(method_context.get("method_profiles", []), list) else []
            # 中文注释：保留启发式识别结果，避免 LLM 误判导致整类规则被过滤
            merged_profiles = list(dict.fromkeys([str(item).strip() for item in (heuristic_profiles + llm_method_profiles) if str(item).strip()]))
            method_context["method_profiles"] = merged_profiles
            method_context["method_type"] = "、".join(merged_profiles)
            method_context["method_purpose"] = self.rule_engine._infer_method_purpose(merged_profiles)
        method_context = self._apply_runtime_overlay_to_method_context(
            method_context=method_context,
            runtime_overlay=runtime_overlay,
        )
        self.service._log_p52_workflow_event(
            "p52_pre_review",
            "method_judgment_completed",
            workflow_id=workflow_id,
            run_id=run_id,
            section_id=section_id,
            method_profiles=method_context.get("method_profiles", []),
        )
        method_context["llm_entity_extraction"] = entity_extraction
        method_context["llm_method_judgment"] = method_judgment
        p52_historical_experience = self._load_p52_historical_experience(
            section_id=section_id,
            method_profiles=method_context.get("method_profiles", []),
            method_keywords=self._extract_method_keywords(
                section_id=section_id,
                section_name=section_name,
                method_context=method_context,
            ),
            project_id=project_id,
            limit=8,
        )
        method_context["p52_historical_experience"] = p52_historical_experience
        self.service.mark_p52_experience_usage(
            experience_ids=[
                str(item.get("experience_id", "") or "").strip()
                for item in p52_historical_experience
                if isinstance(item, dict) and str(item.get("experience_id", "") or "").strip()
            ],
            success=False,
        )
        candidate_findings = self._filter_candidate_findings(
            self.rule_engine.evaluate_candidates(method_context),
            method_judgment=method_judgment,
            method_context=method_context,
        )
        candidate_findings = self._apply_runtime_overlay_to_candidates(
            candidate_findings=candidate_findings,
            runtime_overlay=runtime_overlay,
        )
        self.service._log_p52_workflow_event(
            "p52_pre_review",
            "rule_engine_completed",
            workflow_id=workflow_id,
            run_id=run_id,
            section_id=section_id,
            candidate_rule_count=len(candidate_findings),
        )
        self.service._emit_progress(
            progress_callback,
            "p52_retrieval_start",
            "开始执行 3.2.P.5.2 检索补证。",
            section_id=section_id,
            section_code=section_code,
            section_name=section_name,
            candidate_count=len(candidate_findings),
        )
        knowledge_hits_by_method = self._load_method_knowledge_hits(method_context)
        candidate_evidence_packets = self._collect_candidate_evidence_packets(
            section_id=section_id,
            candidate_findings=candidate_findings,
            submission_section_map=submission_section_map,
            knowledge_hits_by_method=knowledge_hits_by_method,
            runtime_overlay=runtime_overlay,
        )
        retrieval_artifacts = self._build_retrieval_artifacts(
            candidate_evidence_packets=candidate_evidence_packets,
            method_context=method_context,
            section_id=section_id,
        )
        self.service._log_p52_workflow_event(
            "p52_pre_review",
            "retrieval_completed",
            workflow_id=workflow_id,
            run_id=run_id,
            section_id=section_id,
            approved_material_count=len(retrieval_artifacts.get("retrieved_materials", [])),
            evidence_bundle_count=len(retrieval_artifacts.get("evidence_bundles_by_task", {})),
        )
        self.service._emit_progress(
            progress_callback,
            "p52_retrieval_done",
            "3.2.P.5.2 检索补证完成。",
            section_id=section_id,
            section_code=section_code,
            section_name=section_name,
            approved_material_count=len(retrieval_artifacts.get("retrieved_materials", [])),
            task_bundle_count=len(retrieval_artifacts.get("evidence_bundles_by_task", {})),
        )
        review_cards = [dict(item.get("review_card", {})) for item in candidate_evidence_packets if isinstance(item, dict) and isinstance(item.get("review_card", {}), dict)]
        fallback_review_result = self._build_review_result(
            section_id=section_id,
            section_name=section_name,
            method_context=method_context,
            review_cards=review_cards,
            previous_section_meta=previous_section_meta or {},
        )
        review_result = self._run_reviewer_agent(
            project=project,
            project_id=project_id,
            run_id=run_id,
            section_id=section_id,
            section_name=section_name,
            chunk=chunk,
            text=text,
            method_context=method_context,
            candidate_findings=candidate_findings,
            candidate_evidence_packets=candidate_evidence_packets,
            retrieval_artifacts=retrieval_artifacts,
            fallback_review_result=fallback_review_result,
            runtime_overlay=runtime_overlay,
        )
        self.service._log_p52_workflow_event(
            "p52_pre_review",
            "reviewer_completed",
            workflow_id=workflow_id,
            run_id=run_id,
            section_id=section_id,
            reviewer_conclusion=str(review_result.get("pre_review_conclusion", "") or "").strip(),
        )
        review_result = self._merge_review_result(
            review_result=review_result,
            fallback_review_result=fallback_review_result,
            method_context=method_context,
            candidate_findings=candidate_findings,
            candidate_evidence_packets=candidate_evidence_packets,
            previous_section_meta=previous_section_meta or {},
        )

        conclusion, findings, linked_rules, risk_level = self.service._map_review_result_to_legacy(review_result)
        findings = self.service._bind_findings_to_paragraphs(
            findings=findings,
            paragraph_blocks=self.service._build_paragraph_anchors(text=text, section_id=section_id, section_code=section_code),
            section_id=section_id,
            section_code=section_code,
        )
        conclusion_row = SectionConclusionRecord(
            run_id=run_id,
            section_id=section_id,
            section_name=f"{section_code} {section_name}".strip(),
            conclusion=conclusion,
            highlighted_issues=findings,
            linked_rules=linked_rules,
            risk_level=risk_level,
            create_time=self.service._now(),
        ).to_entity()
        trace_payload = self._build_trace_payload(
            project=project,
            source_doc_id=source_doc_id,
            chunk=chunk,
            method_context=method_context,
            candidate_findings=candidate_findings,
            review_cards=review_cards,
            candidate_evidence_packets=candidate_evidence_packets,
            knowledge_hits_by_method=knowledge_hits_by_method,
            entity_extraction=entity_extraction,
            method_judgment=method_judgment,
            retrieval_artifacts=retrieval_artifacts,
            review_result=review_result,
            runtime_overlay=runtime_overlay,
        )
        trace_row = SectionTraceRecord(
            run_id=run_id,
            section_id=section_id,
            trace_payload=trace_payload,
            create_time=self.service._now(),
        ).to_entity()
        self.service._emit_progress(
            progress_callback,
            "p52_rule_review_done",
            "3.2.P.5.2 规则判别审评完成。",
            section_id=section_id,
            section_code=section_code,
            section_name=section_name,
            conclusion=str(review_result.get("pre_review_conclusion", "") or ""),
            issue_count=len(review_cards),
        )
        self.service._log_p52_workflow_event(
            "p52_pre_review",
            "completed",
            workflow_id=workflow_id,
            run_id=run_id,
            section_id=section_id,
            final_conclusion=str(review_result.get("pre_review_conclusion", "") or "").strip(),
            review_card_count=len(review_cards),
        )
        return {
            "success": True,
            "section_id": section_id,
            "section_meta": {
                "section_id": section_id,
                "section_name": section_name,
                "section_code": section_code,
            },
            "findings": findings,
            "score": 0.35 if review_result.get("pre_review_conclusion") == "unsupported" else 0.55 if review_result.get("pre_review_conclusion") == "insufficient_information" else 0.88,
            "linked_rules": linked_rules,
            "risk_level": risk_level,
            "section_summary": str(review_result.get("section_summary", "") or ""),
            "planner_result": {},
            "task_question_result": {
                "judgment_ready": True,
                "task_questions": review_result.get("questions", []),
            },
            "review_result": review_result,
            "task_questions": review_result.get("questions", []),
            "task_verdicts": review_result.get("task_verdicts", []),
            "reasoning_chain_items": review_result.get("reasoning_chain_items", []),
            "problem_basis_advice_items": review_result.get("problem_basis_advice_items", []),
            "medical_key_entities": review_result.get("medical_key_entities", []),
            "medical_key_data_points": review_result.get("medical_key_data_points", []),
            "conclusion_row": conclusion_row,
            "trace_row": trace_row,
            "trace_payload": trace_payload,
            "audit_rows": [],
            "workflow_id": workflow_id,
            "previous_section_meta": {
                "section_id": section_id,
                "conclusion_preview": self.service._preview(conclusion),
            },
        }

    def _load_method_knowledge_hits(self, method_context: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
        out: Dict[str, List[Dict[str, Any]]] = {}
        for method_type in method_context.get("method_profiles", []) if isinstance(method_context.get("method_profiles", []), list) else []:
            profile = self.rule_engine._get_profile(method_type)
            query = str(profile.get("knowledge_query", "") or "").strip()
            out[method_type] = self.toolkit.search_knowledge_base(query, top_k=2) if query else []
        return out

    def _extract_quality_standard_context(
        self,
        *,
        section_name: str,
        section_id: str,
        method_context: Dict[str, Any],
        submission_section_map: Dict[str, Dict[str, Any]],
        runtime_overlay: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        quality_standard_section = self.toolkit.get_quality_standard_section(submission_section_map)
        quality_standard_text = str(quality_standard_section.get("content", "") or "").strip()
        if not quality_standard_text:
            return {
                "section_id": "",
                "section_name": "",
                "items": [],
                "current_section_matches": [],
                "section_item_bindings": [],
                "coverage_risks": [],
            }
        p52_sections = [
            {
                "section_id": sid,
                "section_name": str(item.get("section_name", "") or "").strip(),
            }
            for sid, item in submission_section_map.items()
            if str(sid or "").strip().lower().startswith("3.2.p.5.2.")
        ]
        p52_sections = sorted(
            p52_sections,
            key=lambda item: str(item.get("section_id", "") or ""),
        )[:60]
        prompt = (
            "你是 3.2.P.5.1 质量标准抽取 agent。\n"
            "请从质量标准章节中抽取检验项目，以及对应的方法、放行标准限度和货架期标准限度。\n"
            "输出必须基于原文，不要臆造。若未区分放行/货架期，可统一写到 release_limit。\n"
            "你还需要基于提供的 3.2.P.5.2.x 章节清单，判断质量标准项目是否与 3.2.P.5.2.x 一一对应，并给出绑定关系。\n"
            "如果存在漏项（质量标准项目未映射到任何 5.2.x，或 5.2.x 未映射到质量标准项目），请输出 coverage_risks。\n"
            "coverage_risks 必须使用中文自然语言描述，不要输出英文句子。\n"
            "最后给出当前章节最可能对应的质量标准项目（current_section_matches）。\n"
            "只输出 JSON。\n\n"
            f"current_section_id: {section_id}\n"
            f"current_section_name: {section_name}\n"
            f"current_method_profiles: {json.dumps(method_context.get('method_profiles', []), ensure_ascii=False)}\n"
            f"p52_section_catalog: {json.dumps(p52_sections, ensure_ascii=False)}\n"
            f"quality_standard_text: {self.service._preview(quality_standard_text, 4200)}\n\n"
            "JSON schema:\n"
            "{\n"
            '  "items": [\n'
            '    {"category": "string", "inspection_item": "string", "method": "string", "release_limit": "string", "shelf_life_limit": "string", "evidence": "string"}\n'
            "  ],\n"
            '  "current_section_matches": ["string"],\n'
            '  "section_item_bindings": [\n'
            '    {"inspection_item": "string", "matched_section_ids": ["string"], "matched_section_names": ["string"], "coverage_status": "mapped|missing", "evidence": "string"}\n'
            "  ],\n"
            '  "coverage_risks": ["string"]\n'
            "}\n"
        )
        prompt = self._append_prompt_suffix(
            prompt,
            (
                (runtime_overlay.get("prompt_suffixes", {}) if isinstance(runtime_overlay.get("prompt_suffixes", {}), dict) else {}).get("quality_standard_extractor", [])
                if isinstance(runtime_overlay, dict)
                else []
            ),
        )
        default_payload = {"items": [], "current_section_matches": [], "section_item_bindings": [], "coverage_risks": []}
        raw = self.service.planner_reviewer_agent.llm.chat(
            messages=self.service.planner_reviewer_agent.envelopes.build("reviewer", prompt),
            default=json.dumps(default_payload, ensure_ascii=False),
        )
        parsed = self.service.planner_reviewer_agent.llm.extract_json(raw)
        data = dict(parsed) if isinstance(parsed, dict) else dict(default_payload)
        items: List[Dict[str, str]] = []
        for item in data.get("items", []) if isinstance(data.get("items", []), list) else []:
            if not isinstance(item, dict):
                continue
            inspection_item = str(item.get("inspection_item", "") or "").strip()
            if not inspection_item:
                continue
            items.append(
                {
                    "category": str(item.get("category", "") or "").strip(),
                    "inspection_item": inspection_item,
                    "method": str(item.get("method", "") or "").strip(),
                    "release_limit": str(item.get("release_limit", "") or "").strip(),
                    "shelf_life_limit": str(item.get("shelf_life_limit", "") or "").strip(),
                    "evidence": str(item.get("evidence", "") or "").strip(),
                }
            )
        current_section_matches = [
            str(item or "").strip()
            for item in data.get("current_section_matches", [])
            if str(item or "").strip()
        ] if isinstance(data.get("current_section_matches", []), list) else []
        bindings: List[Dict[str, Any]] = []
        for item in data.get("section_item_bindings", []) if isinstance(data.get("section_item_bindings", []), list) else []:
            if not isinstance(item, dict):
                continue
            inspection_item = str(item.get("inspection_item", "") or "").strip()
            if not inspection_item:
                continue
            matched_section_ids = [
                str(sid).strip().lower()
                for sid in item.get("matched_section_ids", [])
                if str(sid).strip()
            ] if isinstance(item.get("matched_section_ids", []), list) else []
            matched_section_names = [
                str(name).strip()
                for name in item.get("matched_section_names", [])
                if str(name).strip()
            ] if isinstance(item.get("matched_section_names", []), list) else []
            bindings.append(
                {
                    "inspection_item": inspection_item,
                    "matched_section_ids": matched_section_ids,
                    "matched_section_names": matched_section_names,
                    "coverage_status": str(item.get("coverage_status", "") or "").strip() or ("mapped" if matched_section_ids else "missing"),
                    "evidence": str(item.get("evidence", "") or "").strip(),
                }
            )
        coverage_risks = [
            str(item or "").strip()
            for item in data.get("coverage_risks", [])
            if str(item or "").strip()
        ] if isinstance(data.get("coverage_risks", []), list) else []
        coverage_risks = self._localize_coverage_risks(coverage_risks)
        mapped_for_current = []
        for row in bindings:
            if section_id in row.get("matched_section_ids", []):
                mapped_for_current.append(str(row.get("inspection_item", "") or "").strip())
        if mapped_for_current:
            current_section_matches = list(dict.fromkeys(current_section_matches + mapped_for_current))
        return {
            "section_id": str(quality_standard_section.get("section_id", "") or "").strip(),
            "section_name": str(quality_standard_section.get("section_name", "") or "").strip(),
            "items": items,
            "current_section_matches": current_section_matches,
            "section_item_bindings": bindings,
            "coverage_risks": coverage_risks,
            "p52_section_catalog": p52_sections,
            "llm_execution": self.service.planner_reviewer_agent._build_llm_execution_meta(
                agent_name="p52_quality_standard_extractor",
                raw_text=raw,
                parsed_payload=parsed,
            ),
        }

    @staticmethod
    def _localize_coverage_risks(rows: List[str]) -> List[str]:
        out: List[str] = []
        for text in rows if isinstance(rows, list) else []:
            raw = str(text or "").strip()
            if not raw:
                continue
            localized = raw
            # 常见英文模型输出归一化为中文
            localized = localized.replace("Catalog section", "目录章节")
            localized = localized.replace("lacks corresponding inspection item in text", "在质量标准文本中缺少对应检验项目")
            localized = localized.replace("Text specifies Titration for Content", "质量标准文本将含量指定为滴定法")
            localized = localized.replace("Quality Standard items", "质量标准项目")
            localized = localized.replace("lack corresponding section IDs in p52_section_catalog", "在 5.2 章节目录中缺少对应章节")
            if "lacks corresponding" in localized and "目录章节" in localized:
                localized = localized.replace("lacks corresponding", "缺少对应")
            out.append(localized)
        return out

    def _merge_quality_standard_context(
        self,
        *,
        method_context: Dict[str, Any],
        quality_standard_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        merged = dict(method_context)
        items = quality_standard_context.get("items", []) if isinstance(quality_standard_context.get("items", []), list) else []
        matches = quality_standard_context.get("current_section_matches", []) if isinstance(quality_standard_context.get("current_section_matches", []), list) else []
        bindings = quality_standard_context.get("section_item_bindings", []) if isinstance(quality_standard_context.get("section_item_bindings", []), list) else []
        merged["quality_standard_context"] = quality_standard_context
        merged["quality_standard_items"] = items
        merged["quality_standard_matches"] = matches
        filtered_items = [
            item for item in items
            if isinstance(item, dict) and str(item.get("inspection_item", "") or "").strip() in set(matches)
        ] if matches else items
        merged["quality_standard_items_filtered"] = filtered_items
        merged["quality_standard_bindings"] = bindings
        merged["quality_standard_coverage_risks"] = quality_standard_context.get("coverage_risks", []) if isinstance(quality_standard_context.get("coverage_risks", []), list) else []
        if matches:
            merged["linked_spec_item"] = {
                "found": True,
                "value": "；".join(matches[:4]),
                "excerpt": "；".join(matches[:4]),
                "source": "quality_standard_context",
            }
        return merged

    def _extract_method_scope_and_entities_with_llm(
        self,
        *,
        section_id: str,
        section_name: str,
        text: str,
        method_context: Dict[str, Any],
        runtime_overlay: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """将方法适用性判别与实体抽取合并为一次 LLM 调用，减少链路重复。"""
        entity_fields = self.rule_engine.list_entity_fields()
        profile_catalog = self.rule_engine.list_method_profiles()
        general_rules = self.rule_engine.list_general_rules()
        prompt = (
            "你是 3.2.P.5.2 联合分析 agent。\n"
            "请在一次输出中完成：方法适用性判别 + 关键实体抽取。\n"
            "必须基于原文，不要臆造，不要为了凑字段填值。\n"
            "如果章节是有关物质 HPLC 方法，不要同时输出 assay_hplc；优先 related_substances。\n"
            "如果章节是含量测定 HPLC 方法，可输出 assay_hplc；如果是滴定法，输出 assay_titration。\n"
            "只有在规则与章节对象直接匹配时才放入 applicable_rule_ids，不匹配的放入 excluded_rule_ids。\n"
            "只输出 JSON。\n\n"
            f"section_id: {section_id}\n"
            f"section_name: {section_name}\n"
            f"raw_text: {self.service._preview(text, 3200)}\n"
            f"heuristic_method_profiles: {json.dumps(method_context.get('method_profiles', []), ensure_ascii=False)}\n"
            f"available_method_profiles: {json.dumps(profile_catalog, ensure_ascii=False)}\n"
            f"general_rules: {json.dumps(general_rules, ensure_ascii=False)}\n"
            f"entity_fields: {json.dumps(entity_fields, ensure_ascii=False)}\n\n"
            "JSON schema:\n"
            "{\n"
            '  "method_profiles": ["string"],\n'
            '  "applicable_rule_ids": ["string"],\n'
            '  "excluded_rule_ids": ["string"],\n'
            '  "reason": "string",\n'
            '  "structured_sections": {"试液与试药": "string", "仪器与用具": "string", "操作方法": {"方法名": "string"}},\n'
            '  "entities": {"field_name": {"value": "string", "evidence": "string"}}\n'
            "}\n"
        )
        prompt = self._append_prompt_suffix(
            prompt,
            (
                (runtime_overlay.get("prompt_suffixes", {}) if isinstance(runtime_overlay.get("prompt_suffixes", {}), dict) else {}).get("entity_extraction", [])
                if isinstance(runtime_overlay, dict)
                else []
            )
            + (
                (runtime_overlay.get("prompt_suffixes", {}) if isinstance(runtime_overlay.get("prompt_suffixes", {}), dict) else {}).get("method_judgment", [])
                if isinstance(runtime_overlay, dict)
                else []
            ),
        )
        default_payload = {
            "method_profiles": method_context.get("method_profiles", []),
            "applicable_rule_ids": [],
            "excluded_rule_ids": [],
            "reason": "使用启发式回退。",
            "structured_sections": {},
            "entities": {},
        }
        raw = self.service.planner_reviewer_agent.llm.chat(
            messages=self.service.planner_reviewer_agent.envelopes.build("reviewer", prompt),
            default=json.dumps(default_payload, ensure_ascii=False),
        )
        parsed = self.service.planner_reviewer_agent.llm.extract_json(raw)
        result = dict(parsed) if isinstance(parsed, dict) else dict(default_payload)
        profiles = self._normalize_method_profiles(
            profiles=result.get("method_profiles", []),
            section_name=section_name,
            text=text,
            fallback_profiles=method_context.get("method_profiles", []),
        )
        entities = result.get("entities", {}) if isinstance(result.get("entities", {}), dict) else {}
        normalized_entities: Dict[str, Dict[str, str]] = {}
        for field_name in entity_fields:
            payload = entities.get(field_name, {})
            if not isinstance(payload, dict):
                continue
            value = str(payload.get("value", "") or "").strip()
            evidence = str(payload.get("evidence", "") or "").strip()
            if not value and not evidence:
                continue
            normalized_entities[field_name] = {"value": value, "evidence": evidence}
        entity_extraction = {
            "method_profiles": profiles,
            "structured_sections": self._normalize_structured_sections(result.get("structured_sections", {})),
            "entities": normalized_entities,
            "llm_execution": self.service.planner_reviewer_agent._build_llm_execution_meta(
                agent_name="p52_entity_method_joint_extractor",
                raw_text=raw,
                parsed_payload=parsed,
            ),
        }
        method_judgment = {
            "method_profiles": profiles,
            "applicable_rule_ids": [str(item or "").strip() for item in result.get("applicable_rule_ids", []) if str(item or "").strip()],
            "excluded_rule_ids": [str(item or "").strip() for item in result.get("excluded_rule_ids", []) if str(item or "").strip()],
            "reason": str(result.get("reason", "") or "").strip(),
            "llm_execution": self.service.planner_reviewer_agent._build_llm_execution_meta(
                agent_name="p52_method_scope_judge",
                raw_text=raw,
                parsed_payload=parsed,
            ),
        }
        return {"entity_extraction": entity_extraction, "method_judgment": method_judgment}

    def _normalize_method_profiles(
        self,
        *,
        profiles: Any,
        section_name: str,
        text: str,
        fallback_profiles: Any,
    ) -> List[str]:
        raw_profiles = [str(item or "").strip() for item in (profiles if isinstance(profiles, list) else []) if str(item or "").strip()]
        if not raw_profiles:
            raw_profiles = [str(item or "").strip() for item in (fallback_profiles if isinstance(fallback_profiles, list) else []) if str(item or "").strip()]
        if not raw_profiles:
            return []
        section_hint = f"{str(section_name or '').strip()} {str(text or '')}".lower()
        deduped: List[str] = []
        for profile in raw_profiles:
            if profile not in deduped:
                deduped.append(profile)
        if "related_substances" in deduped and "assay_hplc" in deduped and ("有关物质" in section_hint or "杂质" in section_hint):
            deduped = [item for item in deduped if item != "assay_hplc"]
        if "assay_hplc" in deduped and "related_substances" in deduped and ("含量" in section_hint and "有关物质" not in section_hint):
            deduped = [item for item in deduped if item != "related_substances"]
        if "assay_titration" in deduped and "assay_hplc" in deduped:
            if any(token in section_hint for token in ["电位滴定", "永停滴定", "滴定液", "滴定仪"]):
                deduped = [item for item in deduped if item != "assay_hplc"]
            elif any(token in section_hint for token in ["高效液相色谱", "hplc", "色谱柱", "流动相"]):
                deduped = [item for item in deduped if item != "assay_titration"]
        return deduped[:3]

    def _extract_entities_with_llm(
        self,
        *,
        section_id: str,
        section_name: str,
        text: str,
        method_context: Dict[str, Any],
        runtime_overlay: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        entity_fields = self.rule_engine.list_entity_fields()
        prompt = (
            "你是 3.2.P.5.2 章节的实体抽取 agent。\n"
            "请从当前章节中抽取分析方法相关实体。抽取必须基于原文，不要臆造，不要为了凑字段而填值。\n"
            "只有在原文明确出现时才返回实体；不明确就不要返回该字段。\n"
            "优先做语义抽取，不要机械按字段名匹配。\n"
            "如果原文天然存在结构化块，例如“试液与试药”“仪器与用具”“操作方法”，请直接按原文语义抽出这些块。\n"
            "如果“操作方法”下存在 UV、IR、HPLC 等子方法，请抽成方法名到内容的映射，不要拆成碎片化短词。\n"
            "对常见场景请直接映射到规范字段：\n"
            "- 出现“25℃±2℃、30-35℃、20-25℃、水浴”等，通常应抽到 temperature 或 incubation_conditions。\n"
            "- 出现“每隔 5 分钟、观察 30 分钟、取样时间、时间点、振摇 30 秒”等，通常应抽到 sampling_timepoints。\n"
            "- 出现“应完全溶解、未完全溶解、与对照品一致、结果:本品在水中极易溶”等，通常应抽到 decision_rule。\n"
            "- 出现“水中溶解度、甲醇中溶解度、加水 1mL、再加水 9mL”等，应结合上下文抽到 medium_name、medium_volume、sample_preparation。\n"
            "如果能够识别出方法类型，也请给出 method_profiles。\n"
            "只输出 JSON。\n\n"
            f"section_id: {section_id}\n"
            f"section_name: {section_name}\n"
            f"raw_text: {self.service._preview(text, 3200)}\n"
            f"heuristic_method_profiles: {json.dumps(method_context.get('method_profiles', []), ensure_ascii=False)}\n"
            f"entity_fields: {json.dumps(entity_fields, ensure_ascii=False)}\n\n"
            "JSON schema:\n"
            "{\n"
            '  "method_profiles": ["string"],\n'
            '  "structured_sections": {\n'
            '    "试液与试药": "string",\n'
            '    "仪器与用具": "string",\n'
            '    "操作方法": {"方法名": "string"}\n'
            "  },\n"
            '  "entities": {\n'
            '    "field_name": {"value": "string", "evidence": "string"}\n'
            "  }\n"
            "}\n"
        )
        prompt = self._append_prompt_suffix(
            prompt,
            (
                (runtime_overlay.get("prompt_suffixes", {}) if isinstance(runtime_overlay.get("prompt_suffixes", {}), dict) else {}).get("entity_extraction", [])
                if isinstance(runtime_overlay, dict)
                else []
            ),
        )
        default_payload = {
            "method_profiles": method_context.get("method_profiles", []),
            "structured_sections": {},
            "entities": {},
        }
        raw = self.service.planner_reviewer_agent.llm.chat(
            messages=self.service.planner_reviewer_agent.envelopes.build("reviewer", prompt),
            default=json.dumps(default_payload, ensure_ascii=False),
        )
        parsed = self.service.planner_reviewer_agent.llm.extract_json(raw)
        result = dict(parsed) if isinstance(parsed, dict) else dict(default_payload)
        result["method_profiles"] = [str(item or "").strip() for item in result.get("method_profiles", []) if str(item or "").strip()]
        result["structured_sections"] = self._normalize_structured_sections(result.get("structured_sections", {}))
        entities = result.get("entities", {}) if isinstance(result.get("entities", {}), dict) else {}
        normalized_entities: Dict[str, Dict[str, str]] = {}
        for field_name in entity_fields:
            payload = entities.get(field_name, {})
            if not isinstance(payload, dict):
                continue
            value = str(payload.get("value", "") or "").strip()
            evidence = str(payload.get("evidence", "") or "").strip()
            if not value and not evidence:
                continue
            normalized_entities[field_name] = {"value": value, "evidence": evidence}
        result["entities"] = normalized_entities
        result["llm_execution"] = self.service.planner_reviewer_agent._build_llm_execution_meta(
            agent_name="p52_entity_extractor",
            raw_text=raw,
            parsed_payload=parsed,
        )
        return result

    @staticmethod
    def _normalize_structured_sections(value: Any) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        payload = value if isinstance(value, dict) else {}
        for key in ["试液与试药", "仪器与用具", "仪器与设备", "试剂", "结果"]:
            text = str(payload.get(key, "") or "").strip()
            if text:
                out[key] = text
        raw_operation = payload.get("操作方法", {})
        if isinstance(raw_operation, dict):
            method_map = {
                str(method_name or "").strip(): str(content or "").strip()
                for method_name, content in raw_operation.items()
                if str(method_name or "").strip() and str(content or "").strip()
            }
            if method_map:
                out["操作方法"] = method_map
        elif str(raw_operation or "").strip():
            out["操作方法"] = str(raw_operation or "").strip()
        return out

    def _merge_entity_extraction(
        self,
        *,
        method_context: Dict[str, Any],
        entity_extraction: Dict[str, Any],
    ) -> Dict[str, Any]:
        merged = dict(method_context)
        llm_profiles = entity_extraction.get("method_profiles", []) if isinstance(entity_extraction.get("method_profiles", []), list) else []
        if llm_profiles:
            merged["method_profiles"] = llm_profiles
            merged["method_type"] = "、".join(llm_profiles)
            merged["method_purpose"] = self.rule_engine._infer_method_purpose(llm_profiles)
        structured_sections = entity_extraction.get("structured_sections", {}) if isinstance(entity_extraction.get("structured_sections", {}), dict) else {}
        if structured_sections:
            merged["_structured_sections"] = structured_sections
        entities = entity_extraction.get("entities", {}) if isinstance(entity_extraction.get("entities", {}), dict) else {}
        enabled_fields = self.rule_engine.get_enabled_entity_fields(
            merged.get("method_profiles", []) if isinstance(merged.get("method_profiles", []), list) else []
        )
        for field_name, payload in entities.items():
            if not isinstance(payload, dict):
                continue
            if enabled_fields and str(field_name).strip() not in enabled_fields:
                continue
            value = str(payload.get("value", "") or "").strip()
            evidence = str(payload.get("evidence", "") or "").strip()
            if value.lower() == str(field_name).lower():
                value = ""
            if evidence.lower() == str(field_name).lower():
                evidence = ""
            # 过滤低信息量模板值，避免“按规定配制”污染关键实体展示
            if str(field_name).strip() in {"sample_preparation", "reference_preparation"}:
                low_signal = {"按规定配制", "按规定制备", "按规定"}
                if value in low_signal and len(evidence) <= 30:
                    continue
            if not value and not evidence:
                continue
            merged[field_name] = {
                "found": True,
                "excerpt": evidence or value,
                "value": value,
                "source": "llm_entity_extraction",
            }
        return merged

    def _judge_method_scope_with_llm(
        self,
        *,
        section_id: str,
        section_name: str,
        text: str,
        method_context: Dict[str, Any],
        runtime_overlay: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        profile_catalog = self.rule_engine.list_method_profiles()
        general_rules = self.rule_engine.list_general_rules()
        prompt = (
            "你是 3.2.P.5.2 分析方法适用性判别 agent。\n"
            "你的任务是先判断当前章节属于哪些分析方法，再判断哪些规则适用、哪些规则明显不适用。\n"
            "不要直接输出最终审评结论，只做方法归类和规则适用判别。\n"
            "如果章节明显属于微生物限度、无菌、细菌内毒素等微生物方法，不要误归到含量/有关物质/溶出。\n"
            "如果章节讨论的是溶解度、极易溶/易溶/略溶、加溶剂后观察是否完全溶解，应优先考虑 solubility，而不是 dissolution。\n"
            "如果章节是含量测定，要先区分是滴定法还是 HPLC 法：出现电位滴定、永停滴定、滴定液、滴定仪时优先归到 assay_titration；出现高效液相色谱、HPLC、色谱柱、流动相、检测波长时优先归到 assay_hplc。\n"
            "若某条规则与当前章节对象明显不匹配，应放入 excluded_rule_ids。\n"
            "只输出 JSON。\n\n"
            f"section_id: {section_id}\n"
            f"section_name: {section_name}\n"
            f"raw_text: {self.service._preview(text, 2400)}\n"
            f"heuristic_method_profiles: {json.dumps(method_context.get('method_profiles', []), ensure_ascii=False)}\n"
            f"available_method_profiles: {json.dumps(profile_catalog, ensure_ascii=False)}\n"
            f"general_rules: {json.dumps(general_rules, ensure_ascii=False)}\n\n"
            "JSON schema:\n"
            "{\n"
            '  "method_profiles": ["string"],\n'
            '  "applicable_rule_ids": ["string"],\n'
            '  "excluded_rule_ids": ["string"],\n'
            '  "reason": "string"\n'
            "}\n"
        )
        prompt = self._append_prompt_suffix(
            prompt,
            (
                (runtime_overlay.get("prompt_suffixes", {}) if isinstance(runtime_overlay.get("prompt_suffixes", {}), dict) else {}).get("method_judgment", [])
                if isinstance(runtime_overlay, dict)
                else []
            ),
        )
        default_payload = {
            "method_profiles": method_context.get("method_profiles", []) if isinstance(method_context.get("method_profiles", []), list) else [],
            "applicable_rule_ids": [],
            "excluded_rule_ids": [],
            "reason": "使用启发式方法识别结果作为回退。",
        }
        raw = self.service.planner_reviewer_agent.llm.chat(
            messages=self.service.planner_reviewer_agent.envelopes.build("reviewer", prompt),
            default=json.dumps(default_payload, ensure_ascii=False),
        )
        parsed = self.service.planner_reviewer_agent.llm.extract_json(raw)
        result = dict(parsed) if isinstance(parsed, dict) else dict(default_payload)
        result["method_profiles"] = [str(item or "").strip() for item in result.get("method_profiles", []) if str(item or "").strip()]
        result["applicable_rule_ids"] = [str(item or "").strip() for item in result.get("applicable_rule_ids", []) if str(item or "").strip()]
        result["excluded_rule_ids"] = [str(item or "").strip() for item in result.get("excluded_rule_ids", []) if str(item or "").strip()]
        result["reason"] = str(result.get("reason", "") or "").strip()
        result["llm_execution"] = self.service.planner_reviewer_agent._build_llm_execution_meta(
            agent_name="p52_method_scope_judge",
            raw_text=raw,
            parsed_payload=parsed,
        )
        return result

    @staticmethod
    def _filter_candidate_findings(
        candidate_findings: List[Dict[str, Any]],
        *,
        method_judgment: Dict[str, Any],
        method_context: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        applicable_rule_ids = {
            str(item or "").strip()
            for item in method_judgment.get("applicable_rule_ids", [])
            if str(item or "").strip()
        } if isinstance(method_judgment, dict) else set()
        excluded_rule_ids = {
            str(item or "").strip()
            for item in method_judgment.get("excluded_rule_ids", [])
            if str(item or "").strip()
        } if isinstance(method_judgment, dict) else set()
        judged_profiles = {
            str(item or "").strip()
            for item in method_judgment.get("method_profiles", [])
            if str(item or "").strip()
        } if isinstance(method_judgment, dict) else set()
        out: List[Dict[str, Any]] = []
        for item in candidate_findings if isinstance(candidate_findings, list) else []:
            if not isinstance(item, dict):
                continue
            rule_id = str(item.get("rule_id", "") or "").strip()
            method_type = str(item.get("method_type", "") or "").strip()
            if excluded_rule_ids and rule_id in excluded_rule_ids:
                continue
            if applicable_rule_ids:
                if rule_id not in applicable_rule_ids:
                    continue
            elif judged_profiles and method_type and method_type not in judged_profiles:
                continue
            if rule_id == "GEN-001" and (
                judged_profiles
                or (method_context.get("method_profiles", []) if isinstance(method_context.get("method_profiles", []), list) else [])
                or str(method_context.get("section_title", "") or "").strip()
            ):
                continue
            if rule_id == "GEN-002" and isinstance(method_context.get("sample_preparation", {}), dict) and method_context.get("sample_preparation", {}).get("found"):
                continue
            out.append(dict(item))
        return out

    def _collect_candidate_evidence_packets(
        self,
        *,
        section_id: str,
        candidate_findings: List[Dict[str, Any]],
        submission_section_map: Dict[str, Dict[str, Any]],
        knowledge_hits_by_method: Dict[str, List[Dict[str, Any]]],
        runtime_overlay: Dict[str, Any] | None = None,
    ) -> List[Dict[str, Any]]:
        packets: List[Dict[str, Any]] = []
        for candidate in candidate_findings:
            method_type = str(candidate.get("method_type", "") or "").strip()
            retrieval_patterns = candidate.get("retrieval_sections", []) if isinstance(candidate.get("retrieval_sections", []), list) else []
            if isinstance(runtime_overlay, dict):
                retrieval_patterns = list(
                    dict.fromkeys(
                        retrieval_patterns
                        + [
                            str(item).strip()
                            for item in runtime_overlay.get("retrieval_section_patterns", [])
                            if isinstance(runtime_overlay.get("retrieval_section_patterns", []), list) and str(item).strip()
                        ]
                    )
                )
            evidence_keywords = candidate.get("evidence_keywords", []) if isinstance(candidate.get("evidence_keywords", []), list) else []
            if isinstance(runtime_overlay, dict):
                evidence_keywords = list(
                    dict.fromkeys(
                        evidence_keywords
                        + [
                            str(item).strip()
                            for item in runtime_overlay.get("retrieval_keyword_hints", [])
                            if isinstance(runtime_overlay.get("retrieval_keyword_hints", []), list) and str(item).strip()
                        ]
                    )
                )
            related_sections = self.toolkit.get_related_sections(
                submission_section_map,
                retrieval_patterns,
                exclude_section_id=section_id,
                limit=6,
            )
            keyword_retrieval = self.toolkit.search_submission_by_keywords(
                submission_section_map,
                keywords=evidence_keywords,
                section_patterns=retrieval_patterns,
                exclude_section_id=section_id,
                limit=4,
            )
            if keyword_retrieval:
                existing = {str(item.get("section_id", "") or "").strip() for item in related_sections if isinstance(item, dict)}
                for item in keyword_retrieval:
                    sid = str(item.get("section_id", "") or "").strip()
                    if sid and sid not in existing:
                        related_sections.append(
                            {
                                "section_id": sid,
                                "section_name": str(item.get("section_name", "") or "").strip(),
                                "content": str(item.get("content", "") or "").strip(),
                            }
                        )
                        existing.add(sid)
            related_evidence = self._find_related_evidence(
                related_sections=related_sections,
                evidence_keywords=evidence_keywords,
            )
            current_evidence = candidate.get("current_evidence", []) if isinstance(candidate.get("current_evidence", []), list) else []
            knowledge_hits = knowledge_hits_by_method.get(method_type, [])
            status = "suspected_issue" if related_evidence else "confirmed_issue"
            key_evidence: List[Dict[str, Any]] = []
            for item in current_evidence[:1]:
                key_evidence.append(
                    {
                        "source_section": str(item.get("source_section", "") or section_id),
                        "evidence_text": str(item.get("excerpt", "") or "").strip(),
                    }
                )
            for item in related_evidence[:2]:
                key_evidence.append(
                    {
                        "source_section": str(item.get("section_id", "") or "").strip(),
                        "evidence_text": str(item.get("excerpt", "") or "").strip(),
                    }
                )
            if not related_evidence and knowledge_hits:
                first_hit = knowledge_hits[0]
                key_evidence.append(
                    {
                        "source_section": str(first_hit.get("doc_title", "") or "knowledge_base"),
                        "evidence_text": self.service._preview(str(first_hit.get("content", "") or ""), 160),
                    }
                )
            review_card = {
                "rule_id": str(candidate.get("rule_id", "") or "").strip(),
                "rule_name": str(candidate.get("rule_name", "") or candidate.get("problem_item", "") or "").strip(),
                "rule_display_text": str(candidate.get("rule_display_text", "") or "").strip(),
                "method_type": method_type,
                "problem_item": str(candidate.get("problem_item", "") or "").strip(),
                "key_facts": self._build_key_facts(candidate, related_evidence=related_evidence, current_evidence=current_evidence),
                "key_evidence": key_evidence[:3],
                "reasoning": self._build_reasoning(candidate, related_evidence=related_evidence),
                "revision_suggestion": str(candidate.get("revision_suggestion", "") or "").strip(),
                "severity": str(candidate.get("severity", "minor") or "minor").strip().lower() or "minor",
                "status": status,
            }
            packets.append(
                {
                    "candidate": dict(candidate),
                    "review_card": review_card,
                    "related_sections": related_sections,
                    "related_evidence": related_evidence,
                    "knowledge_hits": knowledge_hits,
                    "retrieved_materials": self._build_retrieved_materials(
                        section_id=section_id,
                        candidate=candidate,
                        current_evidence=current_evidence,
                        related_evidence=related_evidence,
                        related_sections=related_sections,
                        knowledge_hits=knowledge_hits,
                    ),
                }
            )
        return packets

    def _build_retrieved_materials(
        self,
        *,
        section_id: str,
        candidate: Dict[str, Any],
        current_evidence: List[Dict[str, Any]],
        related_evidence: List[Dict[str, Any]],
        related_sections: List[Dict[str, Any]],
        knowledge_hits: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        materials: List[Dict[str, Any]] = []
        rule_id = str(candidate.get("rule_id", "") or "").strip()
        rule_name = str(candidate.get("rule_name", "") or candidate.get("problem_item", "") or "").strip()
        for index, item in enumerate(current_evidence, start=1):
            materials.append(
                self.toolkit.build_section_material(
                    source_type="CTD申报资料",
                    title=f"当前章节证据 {rule_id} {index}".strip(),
                    content=str(item.get("excerpt", "") or "").strip(),
                    evidence_id=f"{rule_id}:current:{index}",
                    section_id=str(item.get("source_section", "") or section_id).strip(),
                    section_name="当前章节",
                    score=0.92,
                )
            )
        related_section_map = {
            str(item.get("section_id", "") or "").strip(): item
            for item in related_sections
            if isinstance(item, dict) and str(item.get("section_id", "") or "").strip()
        }
        for index, item in enumerate(related_evidence, start=1):
            section_meta = related_section_map.get(str(item.get("section_id", "") or "").strip(), {})
            materials.append(
                self.toolkit.build_section_material(
                    source_type="CTD申报资料",
                    title=f"{str(item.get('section_name', '') or section_meta.get('section_name', '') or '关联章节').strip()} 补证：{self.service._preview(str(item.get('excerpt', '') or ''), 24)}".strip(),
                    content=str(item.get("excerpt", "") or "").strip(),
                    evidence_id=f"{rule_id}:related:{index}",
                    section_id=str(item.get("section_id", "") or "").strip(),
                    section_name=str(item.get("section_name", "") or section_meta.get("section_name", "") or "").strip(),
                    score=0.78,
                )
            )
        for hit in knowledge_hits[:2]:
            materials.append(self.toolkit.build_knowledge_material(hit, score_floor=0.6))
        if not materials and rule_name:
            materials.append(
                self.toolkit.build_section_material(
                    source_type="规则引擎候选",
                    title=f"{rule_id} {rule_name}".strip(),
                    content=str(candidate.get("reasoning", "") or "").strip(),
                    evidence_id=f"{rule_id}:rule",
                    section_id=section_id,
                    section_name="规则候选",
                    score=0.55,
                )
            )
        return self.toolkit.dedupe_materials(materials)

    def _build_retrieval_artifacts(
        self,
        *,
        candidate_evidence_packets: List[Dict[str, Any]],
        method_context: Dict[str, Any],
        section_id: str,
    ) -> Dict[str, Any]:
        retrieved_materials = self._build_agent_materials(candidate_evidence_packets)
        evidence_bundles_by_task: Dict[str, Dict[str, Any]] = {}
        classified_materials: Dict[str, List[Dict[str, Any]]] = {
            "current_section": [],
            "submission_cross_section": [],
            "knowledge_base": [],
        }
        seen_by_class = {
            "current_section": set(),
            "submission_cross_section": set(),
            "knowledge_base": set(),
        }
        retrieval_blueprint = {
            "strategy": "p52_rule_targeted_retrieval",
            "sources": ["current_section", "submission_cross_section", "knowledge_base"],
            "method_profiles": method_context.get("method_profiles", []) if isinstance(method_context.get("method_profiles", []), list) else [],
            "section_id": section_id,
        }
        for packet in candidate_evidence_packets if isinstance(candidate_evidence_packets, list) else []:
            if not isinstance(packet, dict):
                continue
            candidate = packet.get("candidate", {}) if isinstance(packet.get("candidate", {}), dict) else {}
            rule_id = str(candidate.get("rule_id", "") or "").strip()
            materials = packet.get("retrieved_materials", []) if isinstance(packet.get("retrieved_materials", []), list) else []
            support_ids: List[str] = []
            for material in materials:
                if not isinstance(material, dict):
                    continue
                evidence_id = str(material.get("evidence_id", "") or "").strip()
                source_type = str(material.get("source_type", "") or "").strip()
                if evidence_id:
                    support_ids.append(evidence_id)
                bucket = "knowledge_base" if source_type == "知识库" else "current_section" if str(material.get("section_id", "") or "").strip() == section_id else "submission_cross_section"
                dedupe_key = evidence_id or f"{str(material.get('title', '') or '').strip()}::{str(material.get('content', '') or '')[:80]}"
                if dedupe_key in seen_by_class[bucket]:
                    continue
                seen_by_class[bucket].add(dedupe_key)
                classified_materials[bucket].append(dict(material))
            if not rule_id:
                continue
            evidence_bundles_by_task[rule_id] = {
                "required_rule_types": [str(candidate.get("rule_display_text", "") or candidate.get("rule_id", "") or "").strip()],
                "rule_evidence_ids": [f"{rule_id}:rule"] if any(str(item.get("evidence_id", "") or "").strip() == f"{rule_id}:rule" for item in materials if isinstance(item, dict)) else [],
                "reference_evidence_ids": [
                    str(item.get("evidence_id", "") or "").strip()
                    for item in materials
                    if isinstance(item, dict) and str(item.get("source_type", "") or "").strip() == "知识库"
                ][:3],
                "fact_evidence_ids": [
                    str(item.get("evidence_id", "") or "").strip()
                    for item in materials
                    if isinstance(item, dict) and str(item.get("source_type", "") or "").strip() != "知识库"
                ][:4],
                "supporting_evidence_ids": support_ids[:6],
                "reference_targets": [
                    str(candidate.get("rule_display_text", "") or candidate.get("problem_item", "") or "").strip()
                ],
            }
        return {
            "retrieved_materials": retrieved_materials,
            "evidence_bundles_by_task": evidence_bundles_by_task,
            "classified_materials": classified_materials,
            "retrieval_blueprint": retrieval_blueprint,
        }

    def _run_reviewer_agent(
        self,
        *,
        project: "PreReviewProject",
        project_id: str,
        run_id: str,
        section_id: str,
        section_name: str,
        chunk: Dict[str, Any],
        text: str,
        method_context: Dict[str, Any],
        candidate_findings: List[Dict[str, Any]],
        candidate_evidence_packets: List[Dict[str, Any]],
        retrieval_artifacts: Dict[str, Any],
        fallback_review_result: Dict[str, Any],
        runtime_overlay: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        review_tasks = self._build_review_tasks(candidate_findings, method_context=method_context)
        retrieved_materials = retrieval_artifacts.get("retrieved_materials", []) if isinstance(retrieval_artifacts.get("retrieved_materials", []), list) else self._build_agent_materials(candidate_evidence_packets)
        focus_points = [
            str(item.get("problem_item", "") or "").strip()
            for item in candidate_findings
            if str(item.get("problem_item", "") or "").strip()
        ]
        section_rules = [
            str(item.get("rule_display_text", "") or item.get("rule_id", "") or "").strip()
            for item in candidate_findings
            if str(item.get("rule_display_text", "") or item.get("rule_id", "") or "").strip()
        ]
        planner_contract = PreReviewAgentContractBuilder.planner_input(
            task_id=f"p52_rule_review::{run_id}::{section_id}",
            application_id=project_id,
            section_id=section_id,
            section_name=section_name,
            registration_class=str(getattr(project, "registration_class", "") or "").strip(),
            review_domain="药品预审",
            product_type=str(getattr(project, "product_type", "") or "").strip(),
            raw_text=text,
            focus_points=focus_points,
            section_rules=section_rules,
            task_definition={
                "chapter_role": "3.2.P.5.2 分析方法描述审评",
                "core_review_question": "当前章节是否已经充分说明分析方法，并与关联章节、知识依据形成闭环。",
                "must_answer_questions": [item.get("task_question", "") for item in review_tasks if isinstance(item, dict)],
                "reasoning_principles": [
                    "规则命中只用于生成候选问题，不得直接等同于最终否定结论。",
                    "优先核对章节原文，再结合关联章节和知识库资料做综合判断。",
                    "如存在补证，应将结论降级为待补证，而不是直接判定未通过。",
                ],
                "common_risks": focus_points[:6],
                "evidence_judgment_rules": [
                    "当前章节直接事实优先于二手转述。",
                    "关联章节可用于补证，但需说明映射是否明确。",
                    "知识库资料用于说明通常要求，不可替代申报资料事实。",
                ],
                "conclusion_style_rules": [
                    "结论需要区分满足、待补证和不满足。",
                    "规则名称要写成人可读表述，不要只输出规则编码。",
                ],
            },
            section_review_profile={
                "chapter_role": "分析方法章节",
                "core_review_question": "规则候选项是否被章节事实、跨章节补证和知识依据共同支持。",
                "must_answer_questions": [item.get("task_question", "") for item in review_tasks if isinstance(item, dict)],
                "core_review_principles": [
                    "不要把规则引擎结果硬编码为最终结论。",
                    "要明确区分章节缺失、章节闭环不足和已被其他章节补证。",
                ],
                "common_risks": focus_points[:6],
            },
            compliance_targets=section_rules,
            issue_hypotheses=focus_points,
            evidence_requirements=[
                "当前章节事实",
                "关联章节补证",
                "知识库或指导原则依据",
            ],
            output_requirements=[
                "输出结构化任务判断",
                "规则名称可读",
                "说明证据来源",
            ],
            medical_entity_requirements=[str(item.get("entity_name", "") or "").strip() for item in self._build_medical_key_entities(method_context)[:8]],
            medical_data_requirements=[str(item.get("metric_name", "") or "").strip() for item in self._build_medical_key_data_points(method_context)[:8]],
        )
        review_input = PreReviewAgentContractBuilder.reviewer_input(
            planner_contract,
            raw_text=text,
            retrieved_materials=retrieved_materials,
            review_tasks=review_tasks,
            task_questions=review_tasks,
            retrieval_blueprint=retrieval_artifacts.get("retrieval_blueprint", {}) if isinstance(retrieval_artifacts.get("retrieval_blueprint", {}), dict) else {},
            classified_materials=retrieval_artifacts.get("classified_materials", {}) if isinstance(retrieval_artifacts.get("classified_materials", {}), dict) else {},
            evidence_bundles_by_task=retrieval_artifacts.get("evidence_bundles_by_task", {}) if isinstance(retrieval_artifacts.get("evidence_bundles_by_task", {}), dict) else {},
            entity_extraction_focus=[str(item.get("entity_name", "") or "").strip() for item in self._build_medical_key_entities(method_context)[:8]],
            data_extraction_focus=[str(item.get("metric_name", "") or "").strip() for item in self._build_medical_key_data_points(method_context)[:8]],
            extracted_entities=self._build_extracted_entities_payload(method_context),
            method_judgment=method_context.get("llm_method_judgment", {}) if isinstance(method_context.get("llm_method_judgment", {}), dict) else {},
            section_rules=section_rules,
            historical_experience=method_context.get("p52_historical_experience", []),
        ).to_dict()
        reviewer_prompt_config = self.service._compose_runtime_prompt_config(
            project_id=project_id,
            task_type="reviewer",
            base_prompt_config={},
            section_id=section_id,
            section_name=section_name,
            review_domain="药品预审",
            product_type=str(getattr(project, "product_type", "") or "").strip(),
            registration_class=str(getattr(project, "registration_class", "") or "").strip(),
        )
        p52_historical_experience = method_context.get("p52_historical_experience", [])
        p52_experience_notes = self._format_p52_experience_notes(p52_historical_experience)
        if p52_experience_notes:
            prompt_bundle = reviewer_prompt_config.get("prompt_bundle", {}) if isinstance(reviewer_prompt_config.get("prompt_bundle", {}), dict) else {}
            template_suffixes = prompt_bundle.get("template_suffixes", {}) if isinstance(prompt_bundle.get("template_suffixes", {}), dict) else {}
            template_suffixes["p52_chapter_reviewer.j2"] = "\n".join(
                [
                    str(template_suffixes.get("p52_chapter_reviewer.j2", "") or "").strip(),
                    p52_experience_notes,
                ]
            ).strip()
            prompt_bundle["template_suffixes"] = template_suffixes
            reviewer_prompt_config["prompt_bundle"] = prompt_bundle
        reviewer_runtime_suffix = "\n".join(
            [
                str(item).strip()
                for item in (
                    (runtime_overlay.get("prompt_suffixes", {}) if isinstance(runtime_overlay.get("prompt_suffixes", {}), dict) else {}).get("reviewer", [])
                    if isinstance(runtime_overlay, dict)
                    else []
                )
                if str(item).strip()
            ]
        ).strip()
        if reviewer_runtime_suffix:
            prompt_bundle = reviewer_prompt_config.get("prompt_bundle", {}) if isinstance(reviewer_prompt_config.get("prompt_bundle", {}), dict) else {}
            template_suffixes = prompt_bundle.get("template_suffixes", {}) if isinstance(prompt_bundle.get("template_suffixes", {}), dict) else {}
            template_suffixes["p52_chapter_reviewer.j2"] = "\n".join(
                [
                    str(template_suffixes.get("p52_chapter_reviewer.j2", "") or "").strip(),
                    reviewer_runtime_suffix,
                ]
            ).strip()
            prompt_bundle["template_suffixes"] = template_suffixes
            reviewer_prompt_config["prompt_bundle"] = prompt_bundle
        log_agent_flow(
            "reviewer",
            "start",
            run_id=run_id,
            section_id=section_id,
            section_name=section_name,
            review_task_count=len(review_tasks),
            evidence_count=len(retrieved_materials),
        )
        review_result = self.service.p52_reviewer_agent.review(review_input, prompt_config=reviewer_prompt_config)
        review_result = self.service._cache_review_result_evidence_payload(review_result, retrieved_materials)
        log_agent_flow(
            "reviewer",
            "done",
            run_id=run_id,
            section_id=section_id,
            section_name=section_name,
            review_task_count=len(review_result.get("task_verdicts", [])) if isinstance(review_result.get("task_verdicts", []), list) else 0,
            conclusion=str(review_result.get("pre_review_conclusion", "") or "").strip(),
            used_default_fallback=bool((review_result.get("llm_execution", {}) if isinstance(review_result.get("llm_execution", {}), dict) else {}).get("used_default_fallback", False)),
        )
        if not isinstance(review_result, dict):
            return dict(fallback_review_result)
        review_result.setdefault("task_definition", review_input.get("task_definition", {}))
        review_result.setdefault("section_review_profile", review_input.get("section_review_profile", {}))
        return review_result

    def _build_review_tasks(self, candidate_findings: List[Dict[str, Any]], method_context: Dict[str, Any] | None = None) -> List[Dict[str, Any]]:
        tasks: List[Dict[str, Any]] = []
        method_context = method_context if isinstance(method_context, dict) else {}
        for candidate in candidate_findings:
            rule_id = str(candidate.get("rule_id", "") or "").strip()
            rule_display_text = str(candidate.get("rule_display_text", "") or candidate.get("rule_name", "") or rule_id).strip()
            problem_item = str(candidate.get("problem_item", "") or "").strip()
            revision_suggestion = str(candidate.get("revision_suggestion", "") or "").strip()
            reasoning = str(candidate.get("reasoning", "") or "").strip()
            entity_summary = self._summarize_candidate_entities(candidate, method_context=method_context)
            review_object = entity_summary or problem_item or rule_display_text
            task_question = self._build_candidate_task_question(
                candidate=candidate,
                review_object=review_object,
                entity_summary=entity_summary,
            )
            tasks.append(
                {
                    "task_code": rule_id,
                    "task_question": task_question,
                    "rule_name": rule_display_text,
                    "problem_item": problem_item,
                    "reasoning": reasoning,
                    "revision_suggestion": revision_suggestion,
                    "rule_display_text": rule_display_text,
                    "review_object": review_object,
                    "question_type": "rule_candidate_review",
                    "judgment_basis": [rule_display_text],
                    "comparison_axes": ["当前章节事实", "关联章节补证", "知识依据"],
                    "missing_information": [revision_suggestion] if revision_suggestion else [],
                }
            )
        return tasks

    def _build_candidate_task_question(
        self,
        *,
        candidate: Dict[str, Any],
        review_object: str,
        entity_summary: str,
    ) -> str:
        method_type = str(candidate.get("method_type", "") or "").strip()
        rule_display_text = str(candidate.get("rule_display_text", "") or candidate.get("rule_name", "") or candidate.get("rule_id", "") or "").strip()
        if method_type == "microbial_limits":
            return f"微生物限度规则审评：{review_object}"
        if method_type == "solubility":
            return f"溶解度规则审评：{review_object}"
        if entity_summary:
            return f"{rule_display_text}：{entity_summary}"
        return f"规则审评项：{rule_display_text}"

    def _summarize_candidate_entities(self, candidate: Dict[str, Any], *, method_context: Dict[str, Any]) -> str:
        field_labels = {
            "sample_preparation": "供试品制备",
            "reference_preparation": "对照溶液制备",
            "reference_standard": "对照品",
            "method_purpose": "方法目的",
            "method_type": "方法类型",
            "pharmacopoeia_reference": "药典或通则引用",
            "sample_pretreatment": "供试液前处理",
            "diluent_or_neutralizer": "稀释液或中和体系",
            "test_scope": "检查范围",
            "culture_media": "培养基",
            "incubation_conditions": "培养条件",
            "enumeration_method": "计数方法",
            "suitability_reference": "适用性支持",
            "calculation_formula": "计算公式",
            "system_suitability": "系统适用性",
            "peak_identification_rule": "峰识别规则",
            "rrf_rule": "RRF 或校正逻辑",
            "integration_rule": "积分规则",
            "disregard_limit_rule": "忽略限规则",
            "column_name": "色谱柱",
            "mobile_phase": "流动相",
            "flow_rate": "流速",
            "wavelength": "检测波长",
            "apparatus": "装置",
            "medium_name": "介质名称",
            "medium_volume": "介质体积",
            "rotation_speed": "转速",
            "temperature": "温度",
            "sampling_timepoints": "取样时间点",
            "linked_spec_item": "质量标准映射",
            "linked_validation_target": "验证对象映射",
        }
        labels: List[str] = []
        for field_name in candidate.get("fields", []) if isinstance(candidate.get("fields", []), list) else []:
            payload = method_context.get(str(field_name), {})
            label = field_labels.get(str(field_name), str(field_name))
            value = ""
            if isinstance(payload, dict):
                value = str(payload.get("value", "") or payload.get("excerpt", "") or "").strip()
            if value:
                labels.append(f"{label}（已见：{self.service._preview(value, 36)}）")
            else:
                labels.append(label)
        deduped: List[str] = []
        seen = set()
        for item in labels:
            if not item or item in seen:
                continue
            seen.add(item)
            deduped.append(item)
        return "、".join(deduped[:3])

    def _build_extracted_entities_payload(self, method_context: Dict[str, Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        structured_sections = method_context.get("_structured_sections", {}) if isinstance(method_context.get("_structured_sections", {}), dict) else {}
        if structured_sections:
            out["structured_sections"] = structured_sections
        quality_standard_context = method_context.get("quality_standard_context", {}) if isinstance(method_context.get("quality_standard_context", {}), dict) else {}
        if quality_standard_context:
            out["quality_standard_context"] = {
                "section_id": str(quality_standard_context.get("section_id", "") or "").strip(),
                "section_name": str(quality_standard_context.get("section_name", "") or "").strip(),
                "items": (
                    method_context.get("quality_standard_items_filtered", [])
                    if isinstance(method_context.get("quality_standard_items_filtered", []), list)
                    else (
                        quality_standard_context.get("items", [])
                        if isinstance(quality_standard_context.get("items", []), list)
                        else []
                    )
                ),
                "current_section_matches": quality_standard_context.get("current_section_matches", []) if isinstance(quality_standard_context.get("current_section_matches", []), list) else [],
                "section_item_bindings": quality_standard_context.get("section_item_bindings", []) if isinstance(quality_standard_context.get("section_item_bindings", []), list) else [],
                "coverage_risks": quality_standard_context.get("coverage_risks", []) if isinstance(quality_standard_context.get("coverage_risks", []), list) else [],
            }
        for field_name in self.rule_engine.list_entity_fields():
            payload = method_context.get(field_name, {})
            if not isinstance(payload, dict) or not payload.get("found"):
                continue
            source = str(payload.get("source", "") or "method_context").strip()
            if source not in {"llm_entity_extraction", "quality_standard_context"}:
                continue
            out[field_name] = {
                "value": str(payload.get("value", "") or payload.get("excerpt", "") or "").strip(),
                "evidence": str(payload.get("excerpt", "") or "").strip(),
                "source": source,
            }
        return out

    def _build_agent_materials(self, candidate_evidence_packets: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        materials: List[Dict[str, Any]] = []
        for packet in candidate_evidence_packets if isinstance(candidate_evidence_packets, list) else []:
            if not isinstance(packet, dict):
                continue
            materials.extend(packet.get("retrieved_materials", []) if isinstance(packet.get("retrieved_materials", []), list) else [])
        return self.toolkit.dedupe_materials(materials)

    def _find_related_evidence(
        self,
        *,
        related_sections: List[Dict[str, Any]],
        evidence_keywords: List[str],
    ) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for item in related_sections:
            excerpt = self.toolkit.find_keyword_evidence(str(item.get("content", "") or ""), evidence_keywords)
            if not excerpt:
                continue
            out.append(
                {
                    "section_id": str(item.get("section_id", "") or "").strip(),
                    "section_name": str(item.get("section_name", "") or "").strip(),
                    "excerpt": excerpt,
                }
            )
        return out

    def _merge_review_result(
        self,
        *,
        review_result: Dict[str, Any],
        fallback_review_result: Dict[str, Any],
        method_context: Dict[str, Any],
        candidate_findings: List[Dict[str, Any]],
        candidate_evidence_packets: List[Dict[str, Any]],
        previous_section_meta: Dict[str, Any],
    ) -> Dict[str, Any]:
        model_has_substantive_output = False
        if isinstance(review_result, dict):
            llm_execution = review_result.get("llm_execution", {}) if isinstance(review_result.get("llm_execution", {}), dict) else {}
            model_has_substantive_output = (
                not bool(llm_execution.get("used_default_fallback", False))
                and any(
                    bool(review_result.get(key))
                    for key in ["section_summary", "task_verdicts", "rule_findings", "questions", "reasoning_chain_items"]
                )
            )
        merged = dict(review_result) if model_has_substantive_output and isinstance(review_result, dict) else dict(fallback_review_result)
        if isinstance(review_result, dict):
            for key, value in review_result.items():
                if model_has_substantive_output and key in {
                    "task_verdicts",
                    "rule_findings",
                    "questions",
                    "problem_basis_advice_items",
                    "reasoning_chain_items",
                    "medical_key_entities",
                    "medical_key_data_points",
                }:
                    merged[key] = value if value is not None else []
                    continue
                merged[key] = value
        rule_display_map = {
            str(item.get("rule_id", "") or "").strip(): str(item.get("rule_display_text", "") or item.get("rule_name", "") or item.get("problem_item", "") or "").strip()
            for item in candidate_findings
            if isinstance(item, dict) and str(item.get("rule_id", "") or "").strip()
        }
        packet_by_rule = {
            str((packet.get("candidate", {}) if isinstance(packet.get("candidate", {}), dict) else {}).get("rule_id", "") or "").strip(): packet
            for packet in candidate_evidence_packets
            if isinstance(packet, dict) and str((packet.get("candidate", {}) if isinstance(packet.get("candidate", {}), dict) else {}).get("rule_id", "") or "").strip()
        }

        linked_rules = merged.get("linked_rules", []) if isinstance(merged.get("linked_rules", []), list) else []
        normalized_linked_rules: List[str] = []
        seen_linked = set()
        for item in linked_rules + list(rule_display_map.keys()):
            raw_value = str(item or "").strip()
            if not raw_value:
                continue
            rule_code = raw_value.split(":", 1)[0].split(" ", 1)[0].strip()
            display_text = rule_display_map.get(rule_code, raw_value)
            if not display_text or display_text in seen_linked:
                continue
            seen_linked.add(display_text)
            normalized_linked_rules.append(display_text)
        merged["linked_rules"] = normalized_linked_rules

        merged["rule_findings"] = self._normalize_rule_findings(
            merged.get("rule_findings", []),
            rule_display_map=rule_display_map,
            packet_by_rule=packet_by_rule,
        )
        merged["task_verdicts"] = self._normalize_task_verdicts(
            merged.get("task_verdicts", []),
            rule_display_map=rule_display_map,
            packet_by_rule=packet_by_rule,
        )
        merged["questions"] = self._normalize_questions(
            merged.get("questions", []),
            rule_display_map=rule_display_map,
            packet_by_rule=packet_by_rule,
        )
        merged["problem_basis_advice_items"] = self._build_problem_basis_advice_items(
            task_verdicts=merged.get("task_verdicts", []),
            rule_findings=merged.get("rule_findings", []),
            packet_by_rule=packet_by_rule,
        )
        merged["medical_key_entities"] = self._select_best_medical_key_entities(
            review_entities=merged.get("medical_key_entities", []),
            method_context=method_context,
        )
        merged["medical_key_data_points"] = self._select_best_medical_key_data_points(
            review_data_points=merged.get("medical_key_data_points", []),
            method_context=method_context,
        )
        merged["fact_basis"] = merged.get("fact_basis", {}) if isinstance(merged.get("fact_basis", {}), dict) else {}
        merged["fact_basis"].setdefault("explicit_in_text", [str(item.get("entity_name", "") or "").strip() for item in self._build_medical_key_entities(method_context)[:6]])
        merged["reasoning_chain_items"] = self._normalize_reasoning_chain_items(
            merged.get("reasoning_chain_items", []),
            previous_section_meta=previous_section_meta,
            method_context=method_context,
        )
        if not str(merged.get("section_summary", "") or "").strip():
            confirmed_cards = [item.get("review_card", {}) for item in candidate_evidence_packets if isinstance(item, dict) and str((item.get("review_card", {}) if isinstance(item.get("review_card", {}), dict) else {}).get("status", "") or "") == "confirmed_issue"]
            suspected_cards = [item.get("review_card", {}) for item in candidate_evidence_packets if isinstance(item, dict) and str((item.get("review_card", {}) if isinstance(item.get("review_card", {}), dict) else {}).get("status", "") or "") == "suspected_issue"]
            merged["section_summary"] = self._build_section_summary(
                str(method_context.get("section_title", "") or "").strip() or "当前章节",
                method_context,
                confirmed_cards,
                suspected_cards,
            )
        coverage_risks = [
            str(item).strip()
            for item in method_context.get("quality_standard_coverage_risks", [])
            if isinstance(method_context.get("quality_standard_coverage_risks", []), list) and str(item).strip()
        ]
        if coverage_risks:
            existing_text = "\n".join(
                [
                    str(merged.get("section_summary", "") or ""),
                    " ".join([str(item.get("issue", "") or "") for item in merged.get("rule_findings", []) if isinstance(merged.get("rule_findings", []), list) and isinstance(item, dict)]),
                ]
            )
            if all(risk not in existing_text for risk in coverage_risks):
                merged["rule_findings"] = (merged.get("rule_findings", []) if isinstance(merged.get("rule_findings", []), list) else []) + [
                    {
                        "rule_code": "QS-COVERAGE",
                        "rule_text": "质量标准项目与 3.2.P.5.2.x 映射闭环",
                        "issue_type": "conflict",
                        "issue": "；".join(coverage_risks[:2]),
                        "location": "质量标准映射抽取结果",
                        "requirement_point": "3.2.P.5.1 质量标准项目应与 3.2.P.5.2.x 分析方法章节形成可追溯对应关系。",
                        "evidence": (
                            "规则依据：QS-COVERAGE 质量标准项目与 3.2.P.5.2.x 映射闭环；"
                            "规则适用条件：3.2.P.5.1 质量标准项目应与 3.2.P.5.2.x 分析方法章节形成可追溯对应关系。；"
                            f"原文不符合点：{'；'.join(coverage_risks[:2])}；"
                            "推断原因：质量标准项目与分析方法章节未形成一一对应或方法类型不一致，导致章节闭环风险。；"
                            "定位：质量标准映射抽取结果"
                        ),
                        "suggested_fix": "请补充缺失的 5.2.x 方法章节或在当前章节建立明确映射。",
                    }
                ]
                merged["section_summary"] = f"{str(merged.get('section_summary', '') or '').strip()} 当前发现质量标准与 5.2.x 映射闭环风险：{'；'.join(coverage_risks[:2])}。".strip()
        return merged

    def _normalize_rule_findings(
        self,
        rows: Any,
        *,
        rule_display_map: Dict[str, str],
        packet_by_rule: Dict[str, Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        def compose_rule_evidence(
            *,
            rule_code: str,
            rule_text: str,
            requirement_point: str,
            issue_text: str,
            reason_text: str,
            location_text: str,
        ) -> str:
            rule_label = rule_text if rule_text.startswith(rule_code) else f"{rule_code} {rule_text}".strip() if rule_code or rule_text else ""
            parts = [
                f"规则依据：{rule_label or requirement_point}",
                f"规则适用条件：{requirement_point or rule_label}",
                f"原文不符合点：{issue_text}",
            ]
            if reason_text and reason_text != issue_text:
                parts.append(f"推断原因：{reason_text}")
            parts.append(f"定位：{location_text or '当前章节'}")
            return "；".join(parts)

        normalized: List[Dict[str, Any]] = []
        for item in rows if isinstance(rows, list) else []:
            if not isinstance(item, dict):
                continue
            row = dict(item)
            rule_code = str(row.get("rule_code", "") or "").strip()
            packet = packet_by_rule.get(rule_code, {})
            review_card = packet.get("review_card", {}) if isinstance(packet.get("review_card", {}), dict) else {}
            if rule_code:
                row["rule_text"] = rule_display_map.get(rule_code, str(row.get("rule_text", "") or "").strip()) or str(row.get("rule_text", "") or "").strip()
            if not str(row.get("issue", "") or "").strip():
                row["issue"] = str(review_card.get("problem_item", "") or "").strip()
            if not str(row.get("requirement_point", "") or "").strip():
                row["requirement_point"] = str(review_card.get("reasoning", "") or "").strip()
            if not str(row.get("suggested_fix", "") or "").strip():
                row["suggested_fix"] = str(review_card.get("revision_suggestion", "") or "").strip()
            evidence_rows = review_card.get("key_evidence", []) if isinstance(review_card.get("key_evidence", []), list) else []
            evidence_text = str(row.get("evidence", "") or "").strip()
            if not evidence_text:
                evidence_text = "；".join([str(evidence.get("evidence_text", "") or "").strip() for evidence in evidence_rows if isinstance(evidence, dict)])
            rule_basis = str(row.get("requirement_point", "") or row.get("rule_text", "") or rule_code).strip()
            issue_text = str(row.get("issue", "") or "").strip()
            location_text = str(row.get("location", "") or "当前章节").strip() or "当前章节"
            row["evidence"] = compose_rule_evidence(
                rule_code=rule_code,
                rule_text=str(row.get("rule_text", "") or "").strip(),
                requirement_point=rule_basis,
                issue_text=issue_text or evidence_text,
                reason_text=str(row.get("reason", "") or review_card.get("reasoning", "") or "").strip(),
                location_text=location_text,
            )
            normalized.append(row)
        if normalized:
            deduped: Dict[str, Dict[str, Any]] = {}
            for row in normalized:
                rule_code = str(row.get("rule_code", "") or "").strip()
                issue = str(row.get("issue", "") or "").strip()
                dedupe_key = rule_code or issue
                if not dedupe_key:
                    continue
                existing = deduped.get(dedupe_key)
                if existing is None or len(str(row.get("evidence", "") or "")) > len(str(existing.get("evidence", "") or "")):
                    deduped[dedupe_key] = row
            return list(deduped.values())
        for rule_code, packet in packet_by_rule.items():
            review_card = packet.get("review_card", {}) if isinstance(packet.get("review_card", {}), dict) else {}
            key_evidence = review_card.get("key_evidence", []) if isinstance(review_card.get("key_evidence", []), list) else []
            rule_text = rule_display_map.get(rule_code, rule_code)
            issue = str(review_card.get("problem_item", "") or "").strip()
            requirement = str(review_card.get("reasoning", "") or "").strip()
            location = "；".join([str(evidence.get("source_section", "") or "").strip() for evidence in key_evidence if isinstance(evidence, dict)])
            evidence_text = "；".join([str(evidence.get("evidence_text", "") or "").strip() for evidence in key_evidence if isinstance(evidence, dict)])
            normalized.append(
                {
                    "rule_code": rule_code,
                    "rule_text": rule_text,
                    "issue": issue,
                    "issue_type": "unsupported" if str(review_card.get("status", "") or "") == "confirmed_issue" else "missing",
                    "location": location,
                    "evidence": compose_rule_evidence(
                        rule_code=rule_code,
                        rule_text=rule_text,
                        requirement_point=requirement,
                        issue_text=issue or evidence_text,
                        reason_text=requirement,
                        location_text=location or "当前章节",
                    ),
                    "requirement_point": requirement,
                    "suggested_fix": str(review_card.get("revision_suggestion", "") or "").strip(),
                }
            )
        return normalized

    def _normalize_task_verdicts(
        self,
        rows: Any,
        *,
        rule_display_map: Dict[str, str],
        packet_by_rule: Dict[str, Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        for item in rows if isinstance(rows, list) else []:
            if not isinstance(item, dict):
                continue
            row = dict(item)
            task_code = str(row.get("task_code", "") or "").strip()
            packet = packet_by_rule.get(task_code, {})
            review_card = packet.get("review_card", {}) if isinstance(packet.get("review_card", {}), dict) else {}
            candidate = packet.get("candidate", {}) if isinstance(packet.get("candidate", {}), dict) else {}
            key_evidence = review_card.get("key_evidence", []) if isinstance(review_card.get("key_evidence", []), list) else []
            evidence_text = "；".join([str(evidence.get("evidence_text", "") or "").strip() for evidence in key_evidence if isinstance(evidence, dict)][:2])
            rule_text = rule_display_map.get(task_code, task_code)
            if task_code and str(row.get("basis", "") or "").strip() in {"", task_code}:
                row["basis"] = rule_text
            if not str(row.get("task_question", "") or "").strip():
                row["task_question"] = f"规则审评项：{rule_text}"
            if not str(row.get("problem", "") or "").strip():
                row["problem"] = str(review_card.get("problem_item", "") or rule_text or "").strip()
            if not str(row.get("advice", "") or "").strip():
                row["advice"] = str(review_card.get("revision_suggestion", "") or "").strip()
            status = str(row.get("status", "") or row.get("task_status", "") or "").strip().lower()
            if status == "insufficient_information" and review_card:
                entity_text = self._preview_entity_for_verdict(candidate)
                if entity_text:
                    row["problem"] = f"依据“{rule_text}”，当前章节仍缺少与{entity_text}直接对应的规则要件。"
                else:
                    row["problem"] = f"依据“{rule_text}”，当前章节仅提供部分信息，尚不足以形成闭环判断。"
                if evidence_text:
                    row["reason"] = f"规则依据：{rule_text}；原文不符合点：已见“{evidence_text}”，但缺少直接对应的条件/映射/判定依据。"
                elif str(row.get("reason", "") or "").strip() in {"", "当前仅覆盖约 0% 的判断维度，尚不足以得出稳定结论。"}:
                    row["reason"] = f"规则依据：{rule_text}；原文不符合点：当前章节尚缺少直接对应的条件、映射或判定依据。"
            if evidence_text and str(row.get("evidence_support", "") or "").strip() in {"", rule_text}:
                row["evidence_support"] = evidence_text
            normalized.append(row)
        return normalized

    def _normalize_questions(
        self,
        rows: Any,
        *,
        rule_display_map: Dict[str, str],
        packet_by_rule: Dict[str, Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        for item in rows if isinstance(rows, list) else []:
            if not isinstance(item, dict):
                continue
            row = dict(item)
            basis = str(row.get("basis", "") or "").strip()
            if basis in rule_display_map:
                row["basis"] = rule_display_map[basis]
            if not str(row.get("requested_action", "") or "").strip():
                issue = str(row.get("issue", "") or "").strip()
                task_code = next((key for key, value in rule_display_map.items() if value == basis or value == issue), "")
                packet = packet_by_rule.get(task_code, {})
                review_card = packet.get("review_card", {}) if isinstance(packet.get("review_card", {}), dict) else {}
                row["requested_action"] = str(review_card.get("revision_suggestion", "") or "").strip()
            normalized.append(row)
        return normalized

    @staticmethod
    def _preview_entity_for_verdict(candidate: Dict[str, Any]) -> str:
        field_labels = {
            "method_purpose": "方法目的",
            "method_type": "方法类型",
            "sample_preparation": "供试品制备",
            "pharmacopoeia_reference": "药典或通则引用",
            "sample_pretreatment": "供试液前处理",
            "test_scope": "检查范围",
            "linked_spec_item": "质量标准映射",
            "temperature": "温度条件",
            "medium_name": "溶剂/介质",
            "medium_volume": "加入体积",
            "sampling_timepoints": "观察或操作时间",
            "decision_rule": "结果判定表述",
        }
        labels = [
            field_labels.get(str(field_name), str(field_name))
            for field_name in candidate.get("fields", []) if isinstance(candidate.get("fields", []), list)
            if str(field_name)
        ]
        return "、".join(labels[:3])

    def _select_best_medical_key_entities(
        self,
        *,
        review_entities: Any,
        method_context: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        structured_entities = self._build_medical_key_entities(method_context)
        if structured_entities:
            return structured_entities
        return review_entities if isinstance(review_entities, list) and review_entities else []

    def _select_best_medical_key_data_points(
        self,
        *,
        review_data_points: Any,
        method_context: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        structured_sections = method_context.get("_structured_sections", {}) if isinstance(method_context.get("_structured_sections", {}), dict) else {}
        structured_data_points = self._build_medical_key_data_points(method_context)
        if structured_sections:
            return structured_data_points
        return review_data_points if isinstance(review_data_points, list) and review_data_points else structured_data_points

    def _build_problem_basis_advice_items(
        self,
        *,
        task_verdicts: Any,
        rule_findings: Any,
        packet_by_rule: Dict[str, Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        task_map = {
            str(item.get("task_code", "") or "").strip(): item
            for item in task_verdicts if isinstance(task_verdicts, list) and isinstance(item, dict)
            if str(item.get("task_code", "") or "").strip()
        }
        for finding in rule_findings if isinstance(rule_findings, list) else []:
            if not isinstance(finding, dict):
                continue
            rule_code = str(finding.get("rule_code", "") or "").strip()
            verdict = task_map.get(rule_code, {})
            packet = packet_by_rule.get(rule_code, {})
            review_card = packet.get("review_card", {}) if isinstance(packet.get("review_card", {}), dict) else {}
            rule_text_raw = str(finding.get("rule_text", "") or "").strip()
            issue_text = str(verdict.get("problem", "") or finding.get("issue", "") or "").strip()
            basis_text = str(verdict.get("basis", "") or rule_text_raw or "").strip()
            if (
                issue_text
                and basis_text
                and rule_code
                and basis_text.startswith(rule_code)
                and issue_text.lstrip().rstrip() == basis_text[len(rule_code):].lstrip().rstrip()
            ):
                basis_text = issue_text
            items.append(
                {
                    "problem": issue_text,
                    "basis": basis_text,
                    "advice": str(verdict.get("advice", "") or finding.get("suggested_fix", "") or "").strip(),
                    "severity": str(review_card.get("severity", "") or "major").strip(),
                    "status": str(review_card.get("status", "") or verdict.get("status", "") or "").strip(),
                    "rule_id": rule_code,
                    "method_type": str(review_card.get("method_type", "") or "").strip(),
                    "evidence": review_card.get("key_evidence", []) if isinstance(review_card.get("key_evidence", []), list) else [],
                }
            )
        return items

    def _normalize_reasoning_chain_items(
        self,
        rows: Any,
        *,
        previous_section_meta: Dict[str, Any],
        method_context: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        normalized = [dict(item) for item in rows if isinstance(rows, list) and isinstance(item, dict)]
        if normalized:
            return normalized
        return [
            {
                "step": "method_detection",
                "detail": f"识别方法类型：{'、'.join(method_context.get('method_profiles', [])) or '未识别'}",
            },
            {
                "step": "rule_engine_and_agent",
                "detail": "规则引擎先生成候选问题，再由 reviewer agent 结合章节事实、关联章节和知识资料做综合判断。",
            },
            {
                "step": "previous_section_meta",
                "detail": str(previous_section_meta.get("conclusion_preview", "") or "").strip(),
            },
        ]

    @staticmethod
    def _build_key_facts(
        candidate: Dict[str, Any],
        *,
        related_evidence: List[Dict[str, Any]],
        current_evidence: List[Dict[str, Any]],
    ) -> List[str]:
        facts: List[str] = []
        fields = candidate.get("fields", []) if isinstance(candidate.get("fields", []), list) else []
        if fields:
            facts.append(f"命中规则字段：{'、'.join(fields)}")
        facts.append("当前章节已识别到部分相关描述，但不足以形成完整闭环" if current_evidence else "当前章节未识别到对应关键字段或明确表述")
        facts.append("关联章节存在部分补充证据，需要判定是否可视为本节有效映射" if related_evidence else "关联章节未检索到可直接消除问题的反证")
        return facts

    @staticmethod
    def _build_reasoning(candidate: Dict[str, Any], *, related_evidence: List[Dict[str, Any]]) -> str:
        base = str(candidate.get("reasoning", "") or "").strip()
        if related_evidence:
            return f"{base} 同时，在关联章节中检索到部分支撑信息，说明问题更偏向本节闭环不足。"
        return f"{base} 当前章节及关联章节未形成充分补证，问题可直接成立。"

    def _build_review_result(
        self,
        *,
        section_id: str,
        section_name: str,
        method_context: Dict[str, Any],
        review_cards: List[Dict[str, Any]],
        previous_section_meta: Dict[str, Any],
    ) -> Dict[str, Any]:
        method_profiles = method_context.get("method_profiles", []) if isinstance(method_context.get("method_profiles", []), list) else []
        if not review_cards:
            extracted_entities = self._build_medical_key_entities(method_context)
            if method_profiles and extracted_entities:
                section_title = str(method_context.get("section_title", "") or section_name).strip() or "当前章节"
                summary = f"{section_title} 已识别为{'、'.join(method_profiles)}方法，当前未识别到明确的规则缺口。"
                return {
                    "workflow_mode": "p52_rule_review_v1",
                    "pre_review_conclusion": "supported",
                    "confidence": "medium",
                    "section_summary": summary,
                    "supported_points": [summary],
                    "unsupported_points": [],
                    "missing_points": [],
                    "risk_points": [],
                    "linked_rules": [],
                    "rule_findings": [],
                    "questions": [],
                    "problem_basis_advice_items": [],
                    "reasoning_chain_items": [
                        {
                            "step": "method_detection",
                            "detail": f"识别方法类型：{'、'.join(method_profiles)}",
                        },
                        {
                            "step": "candidate_resolution",
                            "detail": "当前方法已完成实体抽取和规则筛查，未识别到明确的文本级缺口。",
                        },
                        {
                            "step": "previous_section_meta",
                            "detail": str(previous_section_meta.get('conclusion_preview', '') or '').strip(),
                        },
                    ],
                    "task_verdicts": [
                        {
                            "task_code": "p52_no_gap_detected",
                            "task_question": f"{section_title} 规则审评结果：未识别明确文本级缺口",
                            "status": "supported",
                            "problem": summary,
                            "basis": "当前已完成方法识别、实体抽取与规则筛查，未识别到明确缺口。",
                            "reason": "当前章节已具备可识别的方法类型和关键实体，且未触发明确的负向规则候选。",
                            "advice": "建议继续结合 3.2.P.5.3 方法学验证资料复核方法适用性。",
                        }
                    ],
                    "medical_key_entities": extracted_entities,
                    "medical_key_data_points": self._build_medical_key_data_points(method_context),
                    "fact_basis": {
                        "explicit_in_text": [str(item.get("entity_name", "") or "").strip() for item in extracted_entities[:6]],
                        "inferred_from_evidence": [],
                        "experience_warning": [],
                        "not_stated_or_uncertain": [],
                    },
                }
            unresolved_reason = "未识别到适用方法/规则，无法形成有效审评。"
            if method_profiles:
                unresolved_reason = f"已识别方法类型为{'、'.join(method_profiles)}，但当前未形成可用规则候选，无法形成有效审评。"
            return {
                "workflow_mode": "p52_rule_review_v1",
                "pre_review_conclusion": "insufficient_information",
                "confidence": "low",
                "section_summary": unresolved_reason,
                "supported_points": [],
                "unsupported_points": [],
                "missing_points": [unresolved_reason],
                "risk_points": [unresolved_reason],
                "linked_rules": [],
                "rule_findings": [],
                "questions": [
                    {
                        "issue": unresolved_reason,
                        "basis": "当前章节未识别到可用于判别的适用方法规则。",
                        "requested_action": "请补充可识别的方法类型、规则依据或进一步校正章节归类后再审评。",
                    }
                ],
                "problem_basis_advice_items": [
                    {
                        "problem": unresolved_reason,
                        "basis": "当前章节未识别到可用于判别的适用方法规则。",
                        "advice": "请补充可识别的方法类型、规则依据或进一步校正章节归类后再审评。",
                        "severity": "major",
                        "status": "question",
                        "rule_id": "",
                        "method_type": "、".join(method_profiles),
                        "evidence": [],
                    }
                ],
                "reasoning_chain_items": [
                    {
                        "step": "method_detection",
                        "detail": f"识别方法类型：{'、'.join(method_profiles) or '未识别'}",
                    },
                    {
                        "step": "rule_resolution",
                        "detail": unresolved_reason,
                    },
                    {
                        "step": "previous_section_meta",
                        "detail": str(previous_section_meta.get('conclusion_preview', '') or '').strip(),
                    },
                ],
                "task_verdicts": [
                    {
                        "task_code": "p52_unresolved_scope",
                        "task_question": "规则审评前置条件：识别适用的方法类型与规则集合",
                        "status": "insufficient_information",
                        "problem": unresolved_reason,
                        "basis": "需要先识别适用的方法类型与规则集合，才能进入有效审评。",
                        "reason": unresolved_reason,
                        "advice": "请补充可识别的方法类型、规则依据或进一步校正章节归类后再审评。",
                    }
                ],
                "medical_key_entities": self._build_medical_key_entities(method_context),
                "medical_key_data_points": self._build_medical_key_data_points(method_context),
                "fact_basis": {
                    "explicit_in_text": [str(item.get("entity_name", "") or "").strip() for item in self._build_medical_key_entities(method_context)[:6]],
                    "not_stated_or_uncertain": [unresolved_reason],
                },
            }
        confirmed_cards = [item for item in review_cards if str(item.get("status", "") or "") == "confirmed_issue"]
        suspected_cards = [item for item in review_cards if str(item.get("status", "") or "") == "suspected_issue"]
        pre_review_conclusion = "unsupported" if confirmed_cards else "insufficient_information" if suspected_cards else "supported"
        return {
            "workflow_mode": "p52_rule_review_v1",
            "pre_review_conclusion": pre_review_conclusion,
            "confidence": "high" if confirmed_cards else "medium" if suspected_cards else "high",
            "section_summary": self._build_section_summary(section_name, method_context, confirmed_cards, suspected_cards),
            "supported_points": [] if review_cards else [f"{section_name} 当前未识别到显著规则缺口"],
            "unsupported_points": [str(item.get("problem_item", "") or "").strip() for item in confirmed_cards],
            "missing_points": [str(item.get("problem_item", "") or "").strip() for item in suspected_cards],
            "risk_points": [
                str(item.get("problem_item", "") or "").strip()
                for item in review_cards
                if str(item.get("severity", "") or "") in {"critical", "major"}
            ],
            "linked_rules": [
                str(item.get("rule_display_text", "") or item.get("rule_name", "") or item.get("rule_id", "") or "").strip()
                for item in review_cards
                if str(item.get("rule_display_text", "") or item.get("rule_name", "") or item.get("rule_id", "") or "").strip()
            ],
            "rule_findings": [self._build_rule_finding(item) for item in review_cards],
            "questions": [
                {
                    "issue": str(item.get("problem_item", "") or "").strip(),
                    "basis": str(item.get("reasoning", "") or "").strip(),
                    "requested_action": str(item.get("revision_suggestion", "") or "").strip(),
                }
                for item in suspected_cards
            ],
            "problem_basis_advice_items": [
                {
                    "problem": str(item.get("problem_item", "") or "").strip(),
                    "basis": str(item.get("reasoning", "") or "").strip(),
                    "advice": str(item.get("revision_suggestion", "") or "").strip(),
                    "severity": str(item.get("severity", "") or "").strip(),
                    "status": str(item.get("status", "") or "").strip(),
                    "rule_id": str(item.get("rule_id", "") or "").strip(),
                    "method_type": str(item.get("method_type", "") or "").strip(),
                    "evidence": item.get("key_evidence", []),
                }
                for item in review_cards
            ],
            "reasoning_chain_items": [
                {
                    "step": "method_detection",
                    "detail": f"识别方法类型：{'、'.join(method_context.get('method_profiles', [])) or '未识别'}",
                },
                {
                    "step": "rule_engine",
                    "detail": f"规则引擎输出 {len(review_cards)} 个候选问题，已结合跨章节补证做确认或降级。",
                },
                {
                    "step": "previous_section_meta",
                    "detail": str(previous_section_meta.get("conclusion_preview", "") or "").strip(),
                }
            ],
            "task_verdicts": [
                {
                    "task_code": str(item.get("rule_id", "") or "").strip(),
                    "task_question": str(item.get("problem_item", "") or "").strip(),
                    "task_status": "failed" if str(item.get("status", "") or "") == "confirmed_issue" else "needs_follow_up",
                    "judgment_reason": str(item.get("reasoning", "") or "").strip(),
                }
                for item in review_cards
            ],
            "medical_key_entities": self._build_medical_key_entities(method_context),
            "medical_key_data_points": self._build_medical_key_data_points(method_context),
            "fact_basis": {
                "explicit_in_text": [str(item.get("entity_name", "") or "").strip() for item in self._build_medical_key_entities(method_context)[:6]],
            },
        }

    @staticmethod
    def _build_rule_finding(item: Dict[str, Any]) -> Dict[str, Any]:
        issue_type = "unsupported" if str(item.get("status", "") or "") == "confirmed_issue" else "missing"
        key_evidence = item.get("key_evidence", []) if isinstance(item.get("key_evidence", []), list) else []
        return {
            "rule_code": str(item.get("rule_id", "") or "").strip(),
            "rule_text": str(item.get("rule_display_text", "") or item.get("rule_name", "") or item.get("problem_item", "") or "").strip(),
            "issue": str(item.get("problem_item", "") or "").strip(),
            "issue_type": issue_type,
            "location": "；".join([str(evidence.get("source_section", "") or "").strip() for evidence in key_evidence if isinstance(evidence, dict)]),
            "evidence": "；".join([str(evidence.get("evidence_text", "") or "").strip() for evidence in key_evidence if isinstance(evidence, dict)]),
            "requirement_point": str(item.get("reasoning", "") or "").strip(),
            "suggested_fix": str(item.get("revision_suggestion", "") or "").strip(),
        }

    @staticmethod
    def _build_medical_key_entities(method_context: Dict[str, Any]) -> List[Dict[str, Any]]:
        structured_sections = method_context.get("_structured_sections", {}) if isinstance(method_context.get("_structured_sections", {}), dict) else {}
        field_labels = {
            "method_type": "方法类型",
            "method_purpose": "方法目的",
            "sample_preparation": "供试品制备",
            "reference_preparation": "对照溶液制备",
            "reference_standard": "对照品",
            "pharmacopoeia_reference": "药典或通则引用",
            "sample_pretreatment": "供试液前处理",
            "diluent_or_neutralizer": "稀释液或中和体系",
            "test_scope": "检查范围",
            "culture_media": "培养基",
            "incubation_conditions": "培养条件",
            "enumeration_method": "计数方法",
            "suitability_reference": "适用性支持",
            "column_name": "色谱柱",
            "mobile_phase": "流动相",
            "medium_name": "溶剂/介质",
            "linked_spec_item": "质量标准项目映射",
            "linked_validation_target": "验证对象映射",
            "decision_rule": "结果判定表述",
        }
        out: List[Dict[str, Any]] = []
        for field_name in ["试液与试药", "仪器与用具", "仪器与设备", "试剂", "结果"]:
            value = str(structured_sections.get(field_name, "") or "").strip()
            if not value:
                continue
            out.append(
                {
                    "entity_type": "section_block",
                    "entity_name": field_name,
                    "normalized_name": field_name,
                    "value": value,
                    "unit": "",
                    "context": value,
                    "evidence": value,
                    "source": "llm_entity_extraction",
                }
            )
        operation_methods = structured_sections.get("操作方法", {})
        if isinstance(operation_methods, dict):
            for method_name, content in operation_methods.items():
                if not str(content or "").strip():
                    continue
                out.append(
                    {
                        "entity_type": "operation_method",
                        "entity_name": f"操作方法-{str(method_name).strip()}",
                        "normalized_name": str(method_name).strip(),
                        "value": str(content or "").strip(),
                        "unit": "",
                        "context": str(content or "").strip(),
                        "evidence": str(content or "").strip(),
                        "source": "llm_entity_extraction",
                    }
                )
        elif str(operation_methods or "").strip():
            out.append(
                {
                    "entity_type": "operation_method",
                    "entity_name": "操作方法",
                    "normalized_name": "操作方法",
                    "value": str(operation_methods or "").strip(),
                    "unit": "",
                    "context": str(operation_methods or "").strip(),
                    "evidence": str(operation_methods or "").strip(),
                    "source": "llm_entity_extraction",
                }
            )
        for field_name, label in field_labels.items():
            payload = method_context.get(field_name, {})
            if not isinstance(payload, dict) or not payload.get("found"):
                continue
            source = str(payload.get("source", "") or "method_context").strip()
            if source not in {"llm_entity_extraction", "quality_standard_context"}:
                continue
            value = str(payload.get("value", "") or payload.get("excerpt", "") or "").strip()
            evidence = str(payload.get("excerpt", "") or value).strip()
            out.append(
                {
                    "entity_type": "medical_entity",
                    "entity_name": label,
                    "normalized_name": str(field_name),
                    "value": value,
                    "unit": "",
                    "context": evidence,
                    "evidence": evidence,
                    "source": source,
                }
            )
        return out[:12]

    @staticmethod
    def _build_medical_key_data_points(method_context: Dict[str, Any]) -> List[Dict[str, Any]]:
        data_labels = {
            "medium_volume": "介质体积",
            "rotation_speed": "转速",
            "temperature": "温度",
            "sampling_timepoints": "观察/操作时间",
            "flow_rate": "流速",
            "wavelength": "检测波长",
            "calculation_formula": "计算公式",
        }
        out: List[Dict[str, Any]] = []
        for field_name, label in data_labels.items():
            payload = method_context.get(field_name, {})
            if not isinstance(payload, dict) or not payload.get("found"):
                continue
            source = str(payload.get("source", "") or "method_context").strip()
            if source != "llm_entity_extraction":
                continue
            evidence = str(payload.get("excerpt", "") or "").strip()
            value = str(payload.get("value", "") or evidence).strip()
            out.append(
                {
                    "data_type": "medical_data_point",
                    "metric_name": label,
                    "value": value,
                    "unit": "",
                    "comparator": "",
                    "context": evidence,
                    "evidence": evidence,
                    "source": source,
                }
            )
        return out[:10]

    @staticmethod
    def _build_section_summary(
        section_name: str,
        method_context: Dict[str, Any],
        confirmed_cards: List[Dict[str, Any]],
        suspected_cards: List[Dict[str, Any]],
    ) -> str:
        method_text = "、".join(method_context.get("method_profiles", [])) if isinstance(method_context.get("method_profiles", []), list) else ""
        if confirmed_cards:
            return f"{section_name} 识别出 {method_text or '方法学'} 相关问题 {len(confirmed_cards)} 项，需优先补齐关键方法字段与闭环映射。"
        if suspected_cards:
            return f"{section_name} 识别出 {len(suspected_cards)} 项待补证问题，当前更偏向章节闭环不足。"
        return f"{section_name} 已按 3.2.P.5.2 规则判别链路完成审评，当前未识别到显著规则缺口。"

    def _build_trace_payload(
        self,
        *,
        project: "PreReviewProject",
        source_doc_id: str,
        chunk: Dict[str, Any],
        method_context: Dict[str, Any],
        candidate_findings: List[Dict[str, Any]],
        review_cards: List[Dict[str, Any]],
        candidate_evidence_packets: List[Dict[str, Any]],
        knowledge_hits_by_method: Dict[str, List[Dict[str, Any]]],
        entity_extraction: Dict[str, Any],
        method_judgment: Dict[str, Any],
        retrieval_artifacts: Dict[str, Any],
        review_result: Dict[str, Any],
        runtime_overlay: Dict[str, Any],
    ) -> Dict[str, Any]:
        return {
            "workflow_mode": "p52_rule_review_v1",
            "project_id": str(getattr(project, "project_id", "") or "").strip(),
            "source_doc_id": source_doc_id,
            "section_id": str(chunk.get("section_id", "") or "").strip(),
            "section_name": str(chunk.get("section_name", "") or "").strip(),
            "raw_text": self.service._preview(str(chunk.get("text", "") or ""), 3000),
            "method_context": method_context,
            "entity_extraction": entity_extraction,
            "method_judgment": method_judgment,
            "candidate_findings": candidate_findings,
            "review_cards": review_cards,
            "candidate_evidence_packets": candidate_evidence_packets,
            "knowledge_hits_by_method": knowledge_hits_by_method,
            "retrieval_artifacts": retrieval_artifacts,
            "runtime_overlay": runtime_overlay,
            "review_result": {
                "pre_review_conclusion": str(review_result.get("pre_review_conclusion", "") or "").strip(),
                "section_summary": str(review_result.get("section_summary", "") or "").strip(),
                "linked_rules": review_result.get("linked_rules", []),
                "llm_execution": review_result.get("llm_execution", {}) if isinstance(review_result.get("llm_execution", {}), dict) else {},
            },
            "p52_historical_experience": method_context.get("p52_historical_experience", []),
        }

    def _load_p52_historical_experience(
        self,
        *,
        section_id: str,
        method_profiles: List[str],
        method_keywords: Optional[List[str]] = None,
        project_id: str = "",
        limit: int = 8,
    ) -> List[Dict[str, Any]]:
        """加载 P52 相关的历史经验"""
        session = self.service.db_conn.get_session()
        try:
            from agent.agent_backend.database.mysql.db_model import PreReviewExperienceMemory

            rows = (
                session.query(PreReviewExperienceMemory)
                .filter(
                    PreReviewExperienceMemory.status == "active",
                    PreReviewExperienceMemory.experience_type.in_([
                        "p52_meta_reflection",
                        "p52_feedback_optimize",
                        "p52_rule_knowledge",
                        "meta_reflection",
                        "reasoning_knowledge",
                    ])
                )
                .order_by(PreReviewExperienceMemory.id.desc())
                .limit(limit * 2)
                .all()
            )

            candidates = []
            section_key = str(section_id or "").strip().lower()
            project_key = str(project_id or "").strip().lower()
            method_profile_set = {str(item or "").strip().lower() for item in method_profiles if str(item or "").strip()}
            method_keyword_set = {
                str(item or "").strip().lower()
                for item in (method_keywords or [])
                if str(item or "").strip()
            }

            for row in rows:
                try:
                    payload = json.loads(getattr(row, "payload_json", "{}") or "{}")
                except Exception:
                    payload = {}

                row_method_profiles = payload.get("method_profiles", [])
                row_method_profile_set = {
                    str(item or "").strip().lower()
                    for item in row_method_profiles
                    if str(item or "").strip()
                }
                row_method_keywords = payload.get("method_keywords", []) if isinstance(payload.get("method_keywords", []), list) else []
                row_method_keyword_set = {
                    str(item or "").strip().lower()
                    for item in row_method_keywords
                    if str(item or "").strip()
                }
                score = 0.0

                row_scope_type = str(getattr(row, "scope_type", "") or "").strip()
                row_scope_key = str(getattr(row, "scope_key", "") or "").strip().lower()
                if row_scope_type == "project_section" and project_key and row_scope_key == f"{project_key}::{section_key}":
                    score += 420
                elif row_scope_type == "section" and row_scope_key == section_key:
                    score += 300
                elif row_scope_type == "section_global" and row_scope_key == section_key:
                    score += 220
                elif row_scope_type == "global":
                    score += 50

                if method_profile_set and row_method_profile_set:
                    matched_methods = method_profile_set & row_method_profile_set
                    score += len(matched_methods) * 60
                if method_keyword_set and row_method_keyword_set:
                    matched_keywords = method_keyword_set & row_method_keyword_set
                    # 中文注释：关键词命中是跨项目同类方法复用的核心信号，赋予较高权重
                    score += min(len(matched_keywords), 8) * 28
                    if matched_keywords:
                        score += 35

                usage_count = int(getattr(row, "usage_count", 0) or 0)
                success_count = int(getattr(row, "success_count", 0) or 0)
                success_ratio = (success_count / usage_count) if usage_count > 0 else 0.0
                score += min(usage_count, 20.0) + success_ratio * 20.0

                row_id = int(getattr(row, "id", 0) or 0)
                score += min(row_id / 1000000.0, 10.0)
                if score < 80:
                    continue

                content = str(getattr(row, "content", "") or "").strip()
                if content:
                    candidates.append((score, {
                        "experience_id": str(getattr(row, "experience_id", "") or "").strip(),
                        "experience_type": str(getattr(row, "experience_type", "") or "").strip(),
                        "content": content,
                        "scope_type": row_scope_type,
                        "scope_key": row_scope_key,
                        "method_profiles": row_method_profiles,
                        "method_keywords": row_method_keywords,
                        "matched_method_profiles": sorted(list(method_profile_set & row_method_profile_set)),
                        "matched_method_keywords": sorted(list(method_keyword_set & row_method_keyword_set))[:12],
                        "score": score,
                    }))

            candidates.sort(key=lambda x: x[0], reverse=True)
            return [item for _, item in candidates[:limit]]
        finally:
            session.close()

    @staticmethod
    def _format_p52_experience_notes(experiences: List[Dict[str, Any]]) -> str:
        """将 P52 经验格式化为字符串，用于 prompt 注入"""
        if not experiences:
            return ""

        notes = []
        for idx, exp in enumerate(experiences[:5], 1):
            content = str(exp.get("content", "")).strip()
            if content:
                matched_keywords = exp.get("matched_method_keywords", []) if isinstance(exp.get("matched_method_keywords", []), list) else []
                matched_profiles = exp.get("matched_method_profiles", []) if isinstance(exp.get("matched_method_profiles", []), list) else []
                tags = []
                if matched_profiles:
                    tags.append(f"方法域:{'、'.join([str(item) for item in matched_profiles[:3]])}")
                if matched_keywords:
                    tags.append(f"关键词:{'、'.join([str(item) for item in matched_keywords[:4]])}")
                tag_suffix = f"（{'；'.join(tags)}）" if tags else ""
                notes.append(f"{idx}. {content}{tag_suffix}")

        if notes:
            return "\n\n".join(["【P52 历史经验提醒】", "本次审评参考以下历史经验："] + notes)
        return ""

    @staticmethod
    def _extract_method_keywords(
        *,
        section_id: str,
        section_name: str,
        method_context: Dict[str, Any],
    ) -> List[str]:
        text_parts: List[str] = [
            str(section_id or "").strip(),
            str(section_name or "").strip(),
            str(method_context.get("method_type", "") or "").strip(),
            str(method_context.get("method_purpose", "") or "").strip(),
        ]
        text_parts.extend([str(item or "").strip() for item in method_context.get("method_profiles", []) if str(item or "").strip()])
        for key in ["column_name", "mobile_phase", "medium_name", "enumeration_method", "test_scope", "decision_rule"]:
            payload = method_context.get(key, {}) if isinstance(method_context.get(key, {}), dict) else {}
            if payload.get("found"):
                text_parts.append(str(payload.get("value", "") or payload.get("excerpt", "") or "").strip())
        merged = " ".join([part for part in text_parts if part])
        tokens = re.findall(r"[A-Za-z0-9\-\.\+]{2,}|[\u4e00-\u9fff]{2,}", merged)
        blacklist = {"方法", "分析", "章节", "当前", "相关", "进行", "用于", "以及"}
        keywords: List[str] = []
        seen = set()
        for token in tokens:
            normalized = str(token or "").strip().lower()
            if not normalized or normalized in blacklist or len(normalized) > 48:
                continue
            if normalized in seen:
                continue
            seen.add(normalized)
            keywords.append(normalized)
        return keywords[:64]
