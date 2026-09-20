from __future__ import annotations

from typing import Any, Dict, List


class P52FeedbackClassifier:
    """对 3.2.P.5.2 反馈做轻量错误归因。"""

    _KEYWORD_MAP = {
        "method_profile_error": ["方法类型", "方法识别", "hplc", "滴定", "卡尔费休", "卡氏", "方法域"],
        "entity_extraction_error": ["实体", "抽取", "字段", "mobile_phase", "medium_name", "抽错", "乱七八糟"],
        "quality_standard_mapping_error": ["质量标准", "5.1", "映射", "检验项目", "放行标准", "货架期"],
        "rule_false_positive": ["误报", "明明", "已经写了", "为什么还", "不该报", "空话"],
        "rule_false_negative": ["漏报", "没报", "未识别", "没有识别", "没发现"],
        "retrieval_scope_error": ["串章", "检索错", "证据错", "吃到", "其他章节", "补证不对"],
        "retrieval_ranking_error": ["排序", "召回不准", "证据质量", "检索结果不对"],
        "reviewer_reasoning_error": ["套话", "没有审评", "判断不准", "推理不对", "空泛"],
        "result_merge_error": ["重复", "混流", "fallback", "未能稳定形成", "重复判断"],
        "frontend_projection_error": ["前端", "展示", "页面", "summary", "结果页"],
    }

    @staticmethod
    def _safe_list(value: Any) -> List[Any]:
        return value if isinstance(value, list) else []

    @staticmethod
    def _safe_dict(value: Any) -> Dict[str, Any]:
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _compact_text(value: Any) -> str:
        return " ".join(str(value or "").strip().split())

    def _extract_issue_rows(self, feedback_record: Dict[str, Any]) -> List[Dict[str, Any]]:
        rows = feedback_record.get("issue_feedback", [])
        normalized_rows: List[Dict[str, Any]] = []
        if isinstance(rows, list):
            normalized_rows = [item for item in rows if isinstance(item, dict)]
        elif isinstance(rows, dict):
            for issue_key, item in rows.items():
                if not isinstance(item, dict):
                    continue
                row = dict(item)
                if not str(row.get("issue_key", "") or "").strip():
                    row["issue_key"] = str(issue_key or "").strip()
                normalized_rows.append(row)
        return normalized_rows

    def _build_structured_feedback_text(self, issue_rows: List[Dict[str, Any]], missing_item_feedback: Dict[str, Any]) -> str:
        parts: List[str] = []
        for item in issue_rows:
            for key in [
                "feedback_text",
                "issue",
                "problem",
                "advice",
                "error_reason",
                "task_question",
                "rule_code",
                "rule_text",
                "basis",
                "judgment_reason",
                "material_fact",
                "comparison",
            ]:
                text = self._compact_text(item.get(key, ""))
                if text:
                    parts.append(text)
        for key in ["text", "reason"]:
            text = self._compact_text(missing_item_feedback.get(key, ""))
            if text:
                parts.append(text)
        return " ".join(parts)

    def _collect_issue_rule_ids(self, issue_rows: List[Dict[str, Any]]) -> List[str]:
        ids: List[str] = []
        seen = set()
        for item in issue_rows:
            rule_code = str(item.get("rule_code", "") or "").strip()
            if not rule_code or rule_code in seen:
                continue
            seen.add(rule_code)
            ids.append(rule_code)
        return ids

    def classify(
        self,
        feedback_record: Dict[str, Any],
        trace_snapshot: Dict[str, Any],
    ) -> Dict[str, Any]:
        issue_rows = self._extract_issue_rows(feedback_record)
        missing_item_feedback = self._safe_dict(feedback_record.get("missing_item_feedback", {}))
        structured_feedback_text = self._build_structured_feedback_text(issue_rows, missing_item_feedback)
        feedback_text = " ".join(
            [
                str(feedback_record.get("feedback_text", "") or "").strip(),
                str(feedback_record.get("suggestion", "") or "").strip(),
                str(feedback_record.get("conclusion_feedback", "") or "").strip(),
                str(feedback_record.get("retrieval_feedback", "") or "").strip(),
                structured_feedback_text,
            ]
        ).lower()
        error_types: List[str] = []
        reasons: List[str] = []

        for error_type, keywords in self._KEYWORD_MAP.items():
            if any(keyword.lower() in feedback_text for keyword in keywords):
                error_types.append(error_type)
                reasons.append(f"反馈文本命中 {error_type} 关键词。")

        feedback_type = str(feedback_record.get("feedback_type", "") or "").strip().lower()
        if feedback_type == "false_positive" and "rule_false_positive" not in error_types:
            error_types.append("rule_false_positive")
            reasons.append("反馈类型为 false_positive。")
        if feedback_type == "missed" and "rule_false_negative" not in error_types:
            error_types.append("rule_false_negative")
            reasons.append("反馈类型为 missed。")
        if issue_rows:
            reasons.append(f"结构化判断反馈共 {len(issue_rows)} 条。")

        verdicts: List[str] = []
        for item in issue_rows:
            verdicts.append(str(item.get("verdict", "") or "").strip().lower())
            field_feedback = self._safe_dict(item.get("field_feedback", {}))
            for value in field_feedback.values():
                verdicts.append(str(value or "").strip().lower())
            if str(item.get("feedback_kind", "") or "").strip().lower() == "missing_item":
                verdicts.append("missing")
        if "incorrect" in verdicts and "rule_false_positive" not in error_types:
            error_types.append("rule_false_positive")
            reasons.append("结构化反馈存在 incorrect，归因为规则误报或判断错误。")
        if "missing" in verdicts and "rule_false_negative" not in error_types:
            error_types.append("rule_false_negative")
            reasons.append("结构化反馈存在 missing，归因为规则漏报或漏审。")

        method_context = trace_snapshot.get("method_context", {}) if isinstance(trace_snapshot.get("method_context", {}), dict) else {}
        method_profiles = [
            str(item).strip()
            for item in method_context.get("method_profiles", []) or []
            if str(item).strip()
        ]
        if not method_profiles and "method_profile_error" not in error_types:
            error_types.append("method_profile_error")
            reasons.append("trace 中未识别到稳定的方法域。")

        candidate_findings = trace_snapshot.get("candidate_findings", [])
        if isinstance(candidate_findings, list):
            rule_ids = [
                str(item.get("rule_id", "") or "").strip()
                for item in candidate_findings
                if isinstance(item, dict) and str(item.get("rule_id", "") or "").strip()
            ]
        else:
            rule_ids = []
        issue_rule_ids = self._collect_issue_rule_ids(issue_rows)
        for rule_code in issue_rule_ids:
            if rule_code not in rule_ids:
                rule_ids.append(rule_code)
        if rule_ids and ("规则" in feedback_text or "rule" in feedback_text):
            reasons.append(f"trace 中已存在候选规则: {', '.join(rule_ids[:6])}")
        if issue_rule_ids:
            reasons.append(f"结构化反馈涉及规则: {', '.join(issue_rule_ids[:6])}")

        review_result = trace_snapshot.get("review_result", {}) if isinstance(trace_snapshot.get("review_result", {}), dict) else {}
        llm_execution = review_result.get("llm_execution", {}) if isinstance(review_result.get("llm_execution", {}), dict) else {}
        if bool(llm_execution.get("used_default_fallback", False)) and "result_merge_error" not in error_types:
            error_types.append("result_merge_error")
            reasons.append("review 结果使用了默认 fallback。")

        if not error_types:
            error_types.append("reviewer_reasoning_error")
            reasons.append("未命中特定类别，默认按 reviewer 推理问题处理。")

        return {
            "error_types": error_types,
            "primary_error_type": error_types[0] if error_types else "",
            "reasons": reasons,
            "method_profiles": method_profiles,
            "candidate_rule_ids": rule_ids,
            "issue_feedback_count": len(issue_rows),
            "confidence": "medium",
        }
