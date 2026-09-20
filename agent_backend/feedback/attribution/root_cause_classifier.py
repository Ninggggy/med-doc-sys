from __future__ import annotations

from typing import Any, Dict, List


class RootCauseClassifier:
    """Classify feedback into coarse root-cause buckets."""

    RETRIEVAL_SUBCATEGORIES = {
        "query_miss",
        "retrieval_scope_error",
        "retrieval_ranking_error",
        "historical_experience_missing",
    }
    AGENT_SUBCATEGORIES = {
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
    PROMPT_SUBCATEGORIES = {
        "wording_not_actionable",
        "unhelpful_question_to_applicant",
    }
    RULE_SUBCATEGORIES = {
        "rule_mapping_error",
        "missing_regulatory_basis",
    }

    def classify(self, feedback_record: Dict[str, object], run_trace: Dict[str, object]) -> Dict[str, object]:
        trace_meta = self._extract_trace_meta(run_trace)
        sub_category = self._infer_sub_category(feedback_record, trace_meta)
        root_category = (
            self.classify_rag_issue(feedback_record, trace_meta, sub_category)
            or self.classify_agent_issue(feedback_record, trace_meta, sub_category)
            or self.classify_prompt_issue(feedback_record, trace_meta, sub_category)
            or self.classify_rule_issue(feedback_record, trace_meta, sub_category)
            or "general_issue"
        )
        confidence = self._infer_confidence(feedback_record, trace_meta, sub_category)
        evidence = {
            "feedback_labels": list(feedback_record.get("labels", []) or []),
            "conclusion_feedback": str(feedback_record.get("conclusion_feedback", "") or ""),
            "retrieval_feedback": str(feedback_record.get("retrieval_feedback", "") or ""),
            "issue_feedback_count": len(feedback_record.get("issue_feedback", []) or []),
            "paragraph_feedback_count": len(feedback_record.get("paragraph_feedback", []) or []),
            "evidence_feedback_count": len(feedback_record.get("evidence_feedback", []) or []),
            "signal_summary": feedback_record.get("signal_summary", {}) if isinstance(feedback_record.get("signal_summary", {}), dict) else {},
            "retrieval_hit_count": trace_meta["retrieval_hit_count"],
            "effective_queries": trace_meta["effective_queries"],
            "source_breakdown": trace_meta["source_breakdown"],
            "error_breakdown": trace_meta["error_breakdown"],
            "retrieved_material_titles": trace_meta["retrieved_material_titles"],
            "retrieved_material_types": trace_meta["retrieved_material_types"],
            "missing_info_flags": trace_meta["missing_info_flags"],
            "active_rule_codes": trace_meta["active_rule_codes"],
            "agent_findings_count": trace_meta["findings_count"],
        }
        return {
            "category": root_category,
            "root_category": root_category,
            "sub_category": sub_category,
            "confidence": confidence,
            "evidence": evidence,
            "metadata": {
                "focus_point_count": trace_meta["focus_point_count"],
                "question_count": trace_meta["question_count"],
                "focus_points": trace_meta["focus_points"],
            },
        }

    def _extract_trace_meta(self, run_trace: Dict[str, object]) -> Dict[str, object]:
        coordination = run_trace.get("coordination", {}) if isinstance(run_trace.get("coordination"), dict) else {}
        retrieval = coordination.get("retrieval", {}) if isinstance(coordination.get("retrieval"), dict) else {}
        retrieval_detail = run_trace.get("retrieval_detail", {}) if isinstance(run_trace.get("retrieval_detail"), dict) else {}
        agent_meta = run_trace.get("agent", {}) if isinstance(run_trace.get("agent"), dict) else {}
        trace_payload = run_trace.get("trace", {}) if isinstance(run_trace.get("trace"), dict) else {}
        section_packet = trace_payload.get("section_packet", {}) if isinstance(trace_payload.get("section_packet"), dict) else {}
        planner_result = trace_payload.get("planner_result", {}) if isinstance(trace_payload.get("planner_result"), dict) else {}
        prompt_bundle = run_trace.get("prompt_bundle", {}) if isinstance(run_trace.get("prompt_bundle"), dict) else {}
        prompt_active_rules = prompt_bundle.get("active_rules", {}) if isinstance(prompt_bundle.get("active_rules"), dict) else {}
        retrieved_materials = retrieval.get("retrieved_materials", []) if isinstance(retrieval.get("retrieved_materials", []), list) else []
        titles: List[str] = []
        material_types: List[str] = []
        for item in retrieved_materials:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "") or item.get("doc_title", "") or "").strip()
            material_type = str(item.get("source_type", "") or item.get("classification", "") or "").strip()
            if title:
                titles.append(title)
            if material_type:
                material_types.append(material_type)
        active_rule_codes: List[str] = []
        for rows in prompt_active_rules.values():
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                code = str(row.get("rule_code", "") or "").strip()
                if code:
                    active_rule_codes.append(code)
        return {
            "retrieval_hit_count": int(retrieval.get("hit_count", 0) or 0),
            "effective_queries": [
                str(x).strip()
                for x in (
                    retrieval.get("effective_queries", [])
                    or planner_result.get("query_list", [])
                    or []
                )
                if str(x).strip()
            ],
            "source_breakdown": retrieval_detail.get("source_breakdown", {}) if isinstance(retrieval_detail.get("source_breakdown", {}), dict) else {},
            "error_breakdown": retrieval_detail.get("error_breakdown", {}) if isinstance(retrieval_detail.get("error_breakdown", {}), dict) else {},
            "retrieved_material_titles": titles,
            "retrieved_material_types": material_types,
            "findings_count": int(agent_meta.get("findings_count", 0) or 0),
            "focus_points": [str(x).strip() for x in (retrieval.get("focus_points", []) or section_packet.get("focus_points", []) or []) if str(x).strip()],
            "focus_point_count": len(retrieval.get("focus_points", []) or section_packet.get("focus_points", []) or []),
            "question_count": len(trace_payload.get("questions", []) or []) if isinstance(trace_payload.get("questions", []), list) else 0,
            "missing_info_flags": [str(x).strip() for x in (planner_result.get("missing_info_flags", []) or []) if str(x).strip()],
            "active_rule_codes": active_rule_codes,
        }

    def _infer_sub_category(self, feedback_record: Dict[str, object], trace_meta: Dict[str, object]) -> str:
        labels = {str(x).lower() for x in feedback_record.get("labels", []) or []}
        feedback_text = str(feedback_record.get("feedback_text", "") or "").lower()
        decision = str(feedback_record.get("decision", "") or "").lower()
        signal_summary = feedback_record.get("signal_summary", {}) if isinstance(feedback_record.get("signal_summary", {}), dict) else {}
        field_error_counts = signal_summary.get("field_error_counts", {}) if isinstance(signal_summary.get("field_error_counts", {}), dict) else {}
        missing_items = signal_summary.get("missing_items", []) if isinstance(signal_summary.get("missing_items", []), list) else []
        error_breakdown = {str(k): int(v or 0) for k, v in (trace_meta.get("error_breakdown", {}) or {}).items()}
        source_breakdown = {str(k): int(v or 0) for k, v in (trace_meta.get("source_breakdown", {}) or {}).items()}
        retrieval_hit_count = int(trace_meta.get("retrieval_hit_count", 0) or 0)
        effective_queries = trace_meta.get("effective_queries", []) or []
        focus_points = trace_meta.get("focus_points", []) or []
        missing_info_flags = trace_meta.get("missing_info_flags", []) or []
        retrieved_material_types = {str(x).strip() for x in (trace_meta.get("retrieved_material_types", []) or []) if str(x).strip()}
        findings_count = int(trace_meta.get("findings_count", 0) or 0)
        retrieval_feedback = str(feedback_record.get("retrieval_feedback", "") or "").lower()
        conclusion_feedback = str(feedback_record.get("conclusion_feedback", "") or "").lower()

        if "retrieval_miss" in labels:
            return "query_miss"
        if "chunking_issue" in labels:
            return "retrieval_scope_error"
        if "wrong_reference" in labels:
            return "missing_regulatory_basis"
        if "reasoning_error" in labels:
            return "evidence_interpretation_error"
        if "style_issue" in labels:
            return "wording_not_actionable"

        if int(field_error_counts.get("rule", 0) or 0) > 0:
            if not trace_meta.get("active_rule_codes") or "法规" in feedback_text or "药典" in feedback_text:
                return "missing_regulatory_basis"
            return "rule_mapping_error"
        if int(field_error_counts.get("fact", 0) or 0) > 0:
            return "section_fact_extraction_error"
        if int(field_error_counts.get("reasoning", 0) or 0) > 0 or int(field_error_counts.get("conclusion", 0) or 0) > 0:
            return "reasoning_chain_error"
        if int(field_error_counts.get("evidence", 0) or 0) > 0:
            if retrieval_hit_count <= 0:
                return "query_miss"
            if retrieval_feedback == "incorrect":
                return "retrieval_scope_error"
            return "evidence_interpretation_error"
        if missing_items:
            if retrieval_hit_count <= 0 or not effective_queries:
                return "query_miss"
            if retrieval_feedback == "incorrect":
                return "retrieval_scope_error"
            if int(trace_meta.get("focus_point_count", 0) or 0) > 0:
                return "focus_point_miss"
            return "under_identification"

        ordered_breakdowns = [
            "knowledge_understanding_error",
            "query_miss",
            "retrieval_scope_error",
            "retrieval_ranking_error",
            "historical_experience_missing",
            "rule_mapping_error",
            "focus_point_miss",
            "task_question_error",
            "reasoning_chain_error",
            "evidence_interpretation_error",
            "over_inference",
            "under_identification",
            "wrong_severity",
            "missing_regulatory_basis",
            "wording_not_actionable",
            "unhelpful_question_to_applicant",
        ]
        for key in ordered_breakdowns:
            if error_breakdown.get(key, 0) > 0:
                return key

        if retrieval_feedback == "incorrect":
            if retrieval_hit_count <= 0 or not effective_queries:
                return "query_miss"
            if source_breakdown.get("法律法规", 0) <= 0 and "法规" in feedback_text:
                return "retrieval_scope_error"
            return "retrieval_ranking_error"
        if retrieval_feedback == "partial":
            return "retrieval_scope_error"
        if conclusion_feedback == "incorrect":
            if findings_count > 0 and retrieval_hit_count > 0:
                return "evidence_interpretation_error"
            return "over_inference"
        if conclusion_feedback == "partial":
            return "under_identification"

        if missing_info_flags and ("缺失" in feedback_text or "没检到" in feedback_text or "未覆盖" in feedback_text):
            return "retrieval_scope_error"
        if focus_points and ("未覆盖关注点" in feedback_text or "漏掉关注点" in feedback_text):
            return "focus_point_miss"
        if "历史经验" in feedback_text and "历史经验" not in retrieved_material_types:
            return "historical_experience_missing"
        if "法规" in feedback_text and "法律法规" not in retrieved_material_types:
            return "missing_regulatory_basis"
        if "药典" in feedback_text and "药典数据" not in retrieved_material_types:
            return "retrieval_scope_error"

        if decision in {"missed", "missing_risk"}:
            if retrieval_hit_count <= 0 or not effective_queries:
                return "query_miss"
            if source_breakdown.get("历史经验", 0) <= 0 and "经验" in feedback_text:
                return "historical_experience_missing"
            if int(trace_meta.get("focus_point_count", 0) or 0) > 0:
                return "focus_point_miss"
            return "under_identification"

        if decision in {"false_positive", "rejected"}:
            if retrieval_hit_count > 0 and findings_count > 0:
                return "evidence_interpretation_error"
            if findings_count > 0:
                return "over_inference"
            return "wrong_severity"

        if "依据" in feedback_text and ("不匹配" in feedback_text or "不对" in feedback_text or "错误" in feedback_text):
            return "missing_regulatory_basis"
        if "不可执行" in feedback_text or "太空" in feedback_text or "进一步说明" in feedback_text:
            return "wording_not_actionable"
        if "漏" in feedback_text and int(trace_meta.get("focus_point_count", 0) or 0) > 0:
            return "focus_point_miss"
        if retrieval_hit_count <= 0:
            return "query_miss"
        return ""

    @staticmethod
    def _infer_confidence(feedback_record: Dict[str, object], trace_meta: Dict[str, object], sub_category: str) -> float:
        score = 0.4
        if feedback_record.get("labels"):
            score += 0.2
        if sub_category:
            score += 0.15
        if trace_meta.get("effective_queries"):
            score += 0.1
        if trace_meta.get("error_breakdown"):
            score += 0.1
        if trace_meta.get("retrieved_material_titles"):
            score += 0.05
        return min(score, 0.95)

    def classify_rag_issue(self, feedback_record: Dict[str, object], trace_meta: Dict[str, object], sub_category: str) -> str | None:
        if sub_category in self.RETRIEVAL_SUBCATEGORIES:
            return "rag_issue"
        if str(feedback_record.get("decision", "") or "") in {"missed", "missing_risk"} and int(trace_meta.get("retrieval_hit_count", 0) or 0) == 0:
            return "rag_issue"
        return None

    def classify_agent_issue(self, feedback_record: Dict[str, object], trace_meta: Dict[str, object], sub_category: str) -> str | None:
        if sub_category in self.AGENT_SUBCATEGORIES:
            return "agent_issue"
        return None

    def classify_prompt_issue(self, feedback_record: Dict[str, object], trace_meta: Dict[str, object], sub_category: str) -> str | None:
        if sub_category in self.PROMPT_SUBCATEGORIES:
            return "prompt_issue"
        return None

    def classify_rule_issue(self, feedback_record: Dict[str, object], trace_meta: Dict[str, object], sub_category: str) -> str | None:
        if sub_category in self.RULE_SUBCATEGORIES:
            return "rule_issue"
        return None
