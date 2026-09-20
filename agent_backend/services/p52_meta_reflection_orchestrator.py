from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Dict, List


class P52MetaReflectionOrchestrator:
    """P52 专用元反思链路。"""

    def __init__(self, service: Any) -> None:
        self.service = service

    def reflect(
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
            "p52_meta_reflection",
            "reflection_start",
            workflow_id=workflow_id,
            run_id=run_id,
            section_id=section_id,
            patch_count=len(patch_rows),
        )
        trace_snapshot = self._extract_trace_snapshot(run_trace)
        method_context = trace_snapshot.get("method_context", {}) if isinstance(trace_snapshot.get("method_context", {}), dict) else {}
        review_result = trace_snapshot.get("review_result", {}) if isinstance(trace_snapshot.get("review_result", {}), dict) else {}
        candidate_findings = trace_snapshot.get("candidate_findings", []) if isinstance(trace_snapshot.get("candidate_findings", []), list) else []
        retrieval_artifacts = trace_snapshot.get("retrieval_artifacts", {}) if isinstance(trace_snapshot.get("retrieval_artifacts", {}), dict) else {}
        method_profiles = [
            str(item).strip()
            for item in method_context.get("method_profiles", []) or []
            if str(item).strip()
        ]
        rule_ids = [
            str(item.get("rule_id", "") or "").strip()
            for item in candidate_findings
            if isinstance(item, dict) and str(item.get("rule_id", "") or "").strip()
        ]
        unresolved_count = len(
            [
                item
                for item in (
                    review_result.get("linked_rules", [])
                    if isinstance(review_result.get("linked_rules", []), list)
                    else []
                )
                if str(item or "").strip()
            ]
        )
        approved_materials = retrieval_artifacts.get("retrieved_materials", []) if isinstance(retrieval_artifacts.get("retrieved_materials", []), list) else []
        method_keywords = self._extract_method_keywords(
            section_id=section_id,
            section_name=str((run_trace.get("section_name", "") if isinstance(run_trace, dict) else "") or "").strip(),
            method_context=method_context,
        )
        error_family = self._infer_error_family(feedback_record, patch_rows)
        llm_reflection = self._run_llm_meta_reflection(
            section_id=section_id,
            feedback_record=feedback_record,
            run_trace=run_trace,
            patch_rows=patch_rows,
            method_profiles=method_profiles,
            rule_ids=rule_ids,
            error_family=error_family,
            unresolved_count=unresolved_count,
            approved_material_count=len(approved_materials),
        )
        reflection_summary = str(llm_reflection.get("reflection_summary", "") or "").strip()
        if not reflection_summary:
            reflection_summary = self._build_reflection_summary(
                section_id=section_id,
                method_profiles=method_profiles,
                error_family=error_family,
                unresolved_count=unresolved_count,
                approved_material_count=len(approved_materials),
            )
        distilled_experiences = llm_reflection.get("distilled_experiences", []) if isinstance(llm_reflection.get("distilled_experiences", []), list) else []
        if not distilled_experiences:
            distilled_experiences = self._build_distilled_experiences(
                method_profiles=method_profiles,
                rule_ids=rule_ids,
                error_family=error_family,
                patch_rows=patch_rows,
                approved_material_count=len(approved_materials),
            )
        recommended_focus = llm_reflection.get("recommended_focus", []) if isinstance(llm_reflection.get("recommended_focus", []), list) else []
        if not recommended_focus:
            recommended_focus = [
                "先修上游结构化判断，再修 reviewer 表达。",
                "优先验证 5.1 质量标准映射、方法识别和规则适用边界。",
                "回放验证必须包含当前章节、同方法域章节和对抗章节。",
            ]
        self.service._log_p52_workflow_event(
            "p52_meta_reflection",
            "reflection_completed",
            workflow_id=workflow_id,
            run_id=run_id,
            section_id=section_id,
            error_family=error_family,
            experience_count=len(distilled_experiences),
        )
        if distilled_experiences:
            self._persist_distilled_experiences(
                run_id=run_id,
                section_id=section_id,
                distilled_experiences=distilled_experiences,
                method_profiles=method_profiles,
                rule_ids=rule_ids,
                method_keywords=method_keywords,
                taxonomy_hits=self._extract_taxonomy_hits(feedback_record=feedback_record),
                project_id=str((run_context or {}).get("project_id", "") or "").strip(),
            )
        return {
            "success": True,
            "workflow_mode": "p52_meta_reflection_v1",
            "workflow_id": workflow_id,
            "meta_reflection": {
                "bad_case": True,
                "reflection_summary": reflection_summary,
                "error_family": error_family,
                "method_profiles": method_profiles,
                "candidate_rule_ids": rule_ids,
                "approved_material_count": len(approved_materials),
                "distilled_experiences": distilled_experiences,
                "recommended_focus": recommended_focus,
                "few_shot_example": {
                    "title": f"{section_id} P52 元反思示例",
                    "content": reflection_summary,
                },
                "llm_execution": llm_reflection.get("llm_execution", {}) if isinstance(llm_reflection.get("llm_execution", {}), dict) else {},
            },
            "run_context": run_context,
        }

    @staticmethod
    def _normalize_keywords(values: List[Any]) -> List[str]:
        out: List[str] = []
        seen = set()
        for item in values or []:
            token = str(item or "").strip().lower()
            if not token or len(token) < 2:
                continue
            if token in seen:
                continue
            seen.add(token)
            out.append(token)
        return out

    def _extract_method_keywords(
        self,
        *,
        section_id: str,
        section_name: str,
        method_context: Dict[str, Any],
    ) -> List[str]:
        seed: List[str] = []
        seed.extend(str(item or "").strip() for item in method_context.get("method_profiles", []) if str(item or "").strip())
        seed.extend(
            [
                str(section_id or "").strip(),
                str(section_name or "").strip(),
                str((method_context.get("method_type", "") if isinstance(method_context, dict) else "") or "").strip(),
                str((method_context.get("method_purpose", "") if isinstance(method_context, dict) else "") or "").strip(),
            ]
        )
        for key in ["column_name", "mobile_phase", "medium_name", "enumeration_method", "test_scope"]:
            payload = method_context.get(key, {}) if isinstance(method_context.get(key, {}), dict) else {}
            if payload.get("found"):
                seed.append(str(payload.get("value", "") or payload.get("excerpt", "") or "").strip())
        raw_text = " ".join(seed)
        tokens = re.findall(r"[A-Za-z0-9\-\.\+]{2,}|[\u4e00-\u9fff]{2,}", raw_text)
        # 中文注释：保留关键方法学关键词，避免将长句子原样入库造成噪声
        blacklist = {"方法", "分析", "章节", "当前", "相关", "以及", "或者", "进行", "用于", "根据"}
        filtered = [token for token in tokens if token not in blacklist and len(token) <= 48]
        return self._normalize_keywords(filtered)[:64]

    @staticmethod
    def _extract_taxonomy_hits(feedback_record: Dict[str, Any]) -> List[str]:
        labels = feedback_record.get("labels", []) if isinstance(feedback_record.get("labels", []), list) else []
        taxonomy_hits: List[str] = []
        for label in labels:
            value = str(label or "").strip().lower()
            if not value:
                continue
            if ":" in value:
                value = value.split(":", 1)[0].strip()
            taxonomy_hits.append(value)
        return list(dict.fromkeys([item for item in taxonomy_hits if item]))

    def _run_llm_meta_reflection(
        self,
        *,
        section_id: str,
        feedback_record: Dict[str, Any],
        run_trace: Dict[str, Any],
        patch_rows: List[Dict[str, Any]],
        method_profiles: List[str],
        rule_ids: List[str],
        error_family: str,
        unresolved_count: int,
        approved_material_count: int,
    ) -> Dict[str, Any]:
        """调用通用元反思 agent 生成可复用反思结果。"""
        trace_snapshot = self._extract_trace_snapshot(run_trace)
        review_result = trace_snapshot.get("review_result", {}) if isinstance(trace_snapshot.get("review_result", {}), dict) else {}
        retrieval_artifacts = trace_snapshot.get("retrieval_artifacts", {}) if isinstance(trace_snapshot.get("retrieval_artifacts", {}), dict) else {}
        retrieved_materials = retrieval_artifacts.get("retrieved_materials", []) if isinstance(retrieval_artifacts.get("retrieved_materials", []), list) else []
        doc_ids = []
        for item in retrieved_materials:
            if not isinstance(item, dict):
                continue
            doc_id = str(item.get("evidence_id", "") or item.get("doc_id", "") or "").strip()
            if doc_id:
                doc_ids.append(doc_id)
        payload = {
            "task_id": f"p52_meta_reflection:{section_id}",
            "section_id": section_id,
            "section_name": str((run_trace.get("section_name", "") if isinstance(run_trace, dict) else "") or "").strip(),
            "review_output": {
                "pre_review_conclusion": str(review_result.get("pre_review_conclusion", "") or "").strip(),
                "section_summary": str(review_result.get("section_summary", "") or "").strip(),
                "linked_rules": review_result.get("linked_rules", []) if isinstance(review_result.get("linked_rules", []), list) else rule_ids,
            },
            "feedback_signals": {
                "conclusion_feedback": str(feedback_record.get("conclusion_feedback", "") or "").strip(),
                "retrieval_feedback": str(feedback_record.get("retrieval_feedback", "") or "").strip(),
                "reasoning_feedback": str(feedback_record.get("feedback_text", "") or "").strip(),
                "error_family": error_family,
                "unresolved_count": unresolved_count,
                "approved_material_count": approved_material_count,
            },
            "feedback_text": str(feedback_record.get("feedback_text", "") or "").strip(),
            "suggestion": str(feedback_record.get("suggestion", "") or "").strip(),
            "section_rules": rule_ids,
            "focus_points": method_profiles,
            "analysis_result": review_result if isinstance(review_result, dict) else {},
            "patch_result": {
                "patches": patch_rows if isinstance(patch_rows, list) else [],
            },
            "retrieval_materials": retrieved_materials,
            "high_frequency_doc_ids": doc_ids[:8],
            "reference_example": {
                "title": f"{section_id} P52 元反思示例",
                "expected_output": {
                    "error_family": error_family,
                    "recommended_focus": [
                        "先修上游结构化判断，再修 reviewer 表达。",
                        "优先验证 5.1 质量标准映射、方法识别和规则适用边界。",
                    ],
                },
            },
        }
        reflected = self.service.meta_reflection_agent.reflect(payload)
        reflected_payload = reflected if isinstance(reflected, dict) else {}
        recommended_focus: List[str] = []
        for item in reflected_payload.get("distilled_experiences", []) if isinstance(reflected_payload.get("distilled_experiences", []), list) else []:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content", "") or "").strip()
            if content:
                recommended_focus.append(content)
            if len(recommended_focus) >= 3:
                break
        return {
            "reflection_summary": str(reflected_payload.get("reflection_summary", "") or "").strip(),
            "distilled_experiences": reflected_payload.get("distilled_experiences", []) if isinstance(reflected_payload.get("distilled_experiences", []), list) else [],
            "recommended_focus": recommended_focus,
            "llm_execution": reflected_payload.get("llm_execution", {}) if isinstance(reflected_payload.get("llm_execution", {}), dict) else {},
        }

    @staticmethod
    def _extract_trace_snapshot(run_trace: Dict[str, Any]) -> Dict[str, Any]:
        trace_block = run_trace.get("trace", {}) if isinstance(run_trace.get("trace", {}), dict) else {}
        if trace_block:
            return trace_block
        return run_trace if isinstance(run_trace, dict) else {}

    @staticmethod
    def _infer_error_family(feedback_record: Dict[str, Any], patch_rows: List[Dict[str, Any]]) -> str:
        text = " ".join(
            [
                str(feedback_record.get("feedback_text", "") or "").strip(),
                str(feedback_record.get("suggestion", "") or "").strip(),
            ]
        )
        patch_types = [str(item.get("patch_type", "") or "").strip() for item in patch_rows if isinstance(item, dict)]
        if any(kind in {"rule_patch", "profile_patch"} for kind in patch_types):
            return "上游判别问题"
        if any(kind in {"retrieval_patch"} for kind in patch_types):
            return "检索补证问题"
        if any(kind in {"reviewer_patch", "merge_patch"} for kind in patch_types):
            return "reviewer 推理与结果聚合问题"
        if "展示" in text or "前端" in text:
            return "结果投影问题"
        return "待进一步定位"

    @staticmethod
    def _build_reflection_summary(
        *,
        section_id: str,
        method_profiles: List[str],
        error_family: str,
        unresolved_count: int,
        approved_material_count: int,
    ) -> str:
        method_text = "、".join(method_profiles) if method_profiles else "待识别方法域"
        return (
            f"{section_id} 当前更像是 {error_family}。"
            f"方法域识别为 {method_text}，当前链路中有 {unresolved_count} 项规则判断仍待闭环，"
            f"通过检索进入审评的证据数为 {approved_material_count}。"
            "后续优化应优先收敛上游结构化判断，再验证 reviewer 是否正确消费这些事实。"
        )

    @staticmethod
    def _build_distilled_experiences(
        *,
        method_profiles: List[str],
        rule_ids: List[str],
        error_family: str,
        patch_rows: List[Dict[str, Any]],
        approved_material_count: int,
    ) -> List[Dict[str, Any]]:
        return [
            {
                "experience_type": "meta_reflection",
                "knowledge_category": "p52_feedback_optimize",
                "content": f"当方法域为 {'、'.join(method_profiles) if method_profiles else '未知'} 且错误家族为 {error_family} 时，先验证 5.1 映射、实体抽取与规则适用范围。",
                "source_rule_ids": rule_ids[:6],
            },
            {
                "experience_type": "meta_reflection",
                "knowledge_category": "p52_feedback_optimize",
                "content": f"候选 patch 数量 {len(patch_rows)}，检索通过证据数 {approved_material_count}；回放验证时至少保留一个同方法域章节和一个对抗章节。",
                "source_rule_ids": rule_ids[:6],
            },
        ]

    @staticmethod
    def _is_p52_experience_admissible(exp: Dict[str, Any]) -> bool:
        """检查经验是否符合 P52 写入标准"""
        content = str(exp.get("content", "")).strip()
        return bool(content) and len(content) >= 10

    def _persist_distilled_experiences(
        self,
        *,
        run_id: str,
        section_id: str,
        distilled_experiences: List[Dict[str, Any]],
        method_profiles: List[str],
        rule_ids: List[str],
        method_keywords: List[str],
        taxonomy_hits: List[str],
        project_id: str,
    ) -> None:
        """将蒸馏出的经验持久化到经验表"""
        session = self.service.db_conn.get_session()
        try:
            from agent.agent_backend.database.mysql.db_model import PreReviewExperienceMemory

            for idx, exp in enumerate(distilled_experiences):
                if not self._is_p52_experience_admissible(exp):
                    continue

                experience_id = f"p52_exp:{run_id}:{section_id}:{idx}"
                content = str(exp.get("content", "")).strip()
                experience_type = str(exp.get("experience_type", "p52_meta_reflection")).strip()

                payload = {
                    "source": "p52_meta_reflection",
                    "method_profiles": method_profiles,
                    "method_keywords": method_keywords,
                    "rule_ids": rule_ids,
                    "taxonomy_hits": taxonomy_hits,
                    "knowledge_category": str(exp.get("knowledge_category", "p52_feedback_optimize")),
                    "optimization_target": str(exp.get("optimization_target", "p52_reviewer")),
                    "error_family": str(exp.get("error_family", "")),
                }

                existing = (
                    session.query(PreReviewExperienceMemory)
                    .filter(PreReviewExperienceMemory.experience_id == experience_id)
                    .first()
                )

                now = datetime.now()
                if existing:
                    existing.content = content
                    existing.experience_type = experience_type
                    existing.payload_json = json.dumps(payload, ensure_ascii=False)
                    existing.update_time = now
                else:
                    # 中文注释：优先沉淀到项目+章节作用域，确保同项目同章节先收益；再并行写一份全局章节经验用于跨项目复用
                    row = PreReviewExperienceMemory(
                        experience_id=experience_id,
                        scope_type="project_section",
                        scope_key=f"{project_id}::{section_id}" if project_id else section_id,
                        experience_type=experience_type,
                        content=content,
                        source_feedback_ids=run_id,
                        trigger_conditions=json.dumps({"method_profiles": method_profiles, "method_keywords": method_keywords}, ensure_ascii=False),
                        status="active",
                        usage_count=0,
                        success_count=0,
                        payload_json=json.dumps(payload, ensure_ascii=False),
                        create_time=now,
                        update_time=now,
                    )
                    session.add(row)
                    global_experience_id = f"{experience_id}:global"
                    existing_global = (
                        session.query(PreReviewExperienceMemory)
                        .filter(PreReviewExperienceMemory.experience_id == global_experience_id)
                        .first()
                    )
                    if existing_global:
                        existing_global.content = content
                        existing_global.experience_type = experience_type
                        existing_global.payload_json = json.dumps(payload, ensure_ascii=False)
                        existing_global.trigger_conditions = json.dumps({"method_profiles": method_profiles, "method_keywords": method_keywords}, ensure_ascii=False)
                        existing_global.status = "active"
                        existing_global.update_time = now
                    else:
                        global_row = PreReviewExperienceMemory(
                            experience_id=global_experience_id,
                            scope_type="section_global",
                            scope_key=section_id,
                            experience_type=experience_type,
                            content=content,
                            source_feedback_ids=run_id,
                            trigger_conditions=json.dumps({"method_profiles": method_profiles, "method_keywords": method_keywords}, ensure_ascii=False),
                            status="active",
                            usage_count=0,
                            success_count=0,
                            payload_json=json.dumps(payload, ensure_ascii=False),
                            create_time=now,
                            update_time=now,
                        )
                        session.add(global_row)

            session.commit()
        except Exception as e:
            session.rollback()
            self.service._log_p52_workflow_event(
                "p52_experience_persist",
                "failed",
                run_id=run_id,
                section_id=section_id,
                error_message=str(e),
            )
        finally:
            session.close()
