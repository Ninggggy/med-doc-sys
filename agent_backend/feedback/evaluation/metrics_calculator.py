from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, List, Optional, Set

from agent.agent_backend.agentic_rl.reward_functions import feedback_metrics


def _safe_ratio(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return float(numerator) / float(denominator)


def _safe_f1(precision: float, recall: float) -> float:
    if precision + recall <= 0:
        return 0.0
    return 2.0 * precision * recall / (precision + recall)


class MetricsCalculator:
    """Calculate replay, feedback, and version-comparison metrics."""

    RETRIEVAL_FALSE_NEGATIVE_ERRORS = {
        "query_miss",
        "historical_experience_missing",
    }

    RETRIEVAL_FALSE_POSITIVE_ERRORS = {
        "retrieval_scope_error",
        "retrieval_ranking_error",
    }

    DEFAULT_FEATURE_FLAGS = {
        "use_prompt_rules": True,
        "use_section_rules": True,
        "use_reference_examples": True,
        "use_experience_memory": True,
        "use_historical_bad_retrievals": True,
        "use_retrieval_evaluator": True,
    }

    @staticmethod
    def _as_dict(payload: Any) -> Dict[str, Any]:
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _normalize_text(value: Any) -> str:
        return str(value or "").strip()

    @classmethod
    def _normalize_text_list(cls, values: Any) -> List[str]:
        if not isinstance(values, list):
            return []
        out: List[str] = []
        for item in values:
            text = cls._normalize_text(item)
            if text and text not in out:
                out.append(text)
        return out

    @classmethod
    def _normalize_string_set(cls, values: Any) -> Set[str]:
        return set(cls._normalize_text_list(values))

    @classmethod
    def _normalize_findings(cls, values: Any) -> Set[str]:
        findings: Set[str] = set()
        if not isinstance(values, list):
            return findings
        for item in values:
            if isinstance(item, dict):
                text = cls._normalize_text(item.get("title", "") or item.get("issue", "") or item.get("problem", ""))
            else:
                text = cls._normalize_text(item)
            if text:
                findings.add(text)
        return findings

    @classmethod
    def _normalize_problem_basis_advice_issues(cls, values: Any) -> Set[str]:
        findings: Set[str] = set()
        if not isinstance(values, list):
            return findings
        for item in values:
            if not isinstance(item, dict):
                continue
            status = cls._normalize_text(item.get("status", "")).lower() or "issue"
            if status not in {"issue", "question"}:
                continue
            text = cls._normalize_text(item.get("problem", "") or item.get("issue", "") or item.get("title", ""))
            if text:
                findings.add(text)
        return findings

    @classmethod
    def _extract_problem_detection_set(cls, payload: Dict[str, Any]) -> Set[str]:
        normalized_payload = cls._as_dict(payload)
        structured_items = cls._normalize_problem_basis_advice_issues(normalized_payload.get("problem_basis_advice_items", []))
        if structured_items:
            return structured_items
        return cls._normalize_findings(normalized_payload.get("highlighted_issues", []))

    @classmethod
    def _evidence_id(cls, item: Any) -> str:
        if not isinstance(item, dict):
            return ""
        evidence_id = cls._normalize_text(item.get("evidence_id", ""))
        if evidence_id:
            return evidence_id
        doc_id = cls._normalize_text(item.get("doc_id", ""))
        chunk_id = cls._normalize_text(item.get("chunk_id", ""))
        if doc_id and chunk_id:
            return f"{doc_id}:{chunk_id}"
        return doc_id

    @classmethod
    def _extract_case_id(cls, result: Dict[str, Any]) -> str:
        if not isinstance(result, dict):
            return ""
        source_case = cls._as_dict(result.get("source_case", {}))
        return (
            cls._normalize_text(result.get("case_id", ""))
            or cls._normalize_text(source_case.get("case_id", ""))
            or cls._normalize_text(source_case.get("section_id", ""))
        )

    @classmethod
    def _extract_expected_conclusion(cls, result: Dict[str, Any]) -> Dict[str, Any]:
        source_case = cls._as_dict(result.get("source_case", result.get("case_data", {})))
        expected = cls._as_dict(source_case.get("expected", result.get("expected", {})))
        return expected

    @classmethod
    def _extract_actual_conclusion(cls, result: Dict[str, Any]) -> Dict[str, Any]:
        replay = cls._as_dict(result.get("replay_result", result))
        data = cls._as_dict(replay.get("data", {}))
        conclusion = cls._as_dict(data.get("conclusion", replay.get("conclusion", {})))
        return conclusion

    @classmethod
    def _extract_expected_trace(cls, result: Dict[str, Any]) -> Dict[str, Any]:
        source_case = cls._as_dict(result.get("source_case", result.get("case_data", {})))
        return cls._as_dict(source_case.get("trace", result.get("trace", {})))

    @classmethod
    def _extract_actual_trace(cls, result: Dict[str, Any]) -> Dict[str, Any]:
        replay = cls._as_dict(result.get("replay_result", result))
        data = cls._as_dict(replay.get("data", {}))
        return cls._as_dict(data.get("trace", replay.get("trace", {})))

    @classmethod
    def _extract_approved_evidence_ids(cls, trace_payload: Dict[str, Any]) -> Set[str]:
        trace = cls._as_dict(trace_payload)
        coordination = cls._as_dict(trace.get("coordination", {}))
        retrieval = cls._as_dict(coordination.get("retrieval", {}))
        materials = retrieval.get("retrieved_materials", trace.get("retrieved_materials", []))
        out: Set[str] = set()
        if isinstance(materials, list):
            for item in materials:
                evidence_id = cls._evidence_id(item)
                if evidence_id:
                    out.add(evidence_id)
        return out

    @classmethod
    def _extract_rejected_evidence_ids(cls, trace_payload: Dict[str, Any]) -> Set[str]:
        trace = cls._as_dict(trace_payload)
        retrieval_evaluation = cls._as_dict(trace.get("retrieval_evaluation", {}))
        materials = retrieval_evaluation.get("rejected_materials", [])
        out: Set[str] = set()
        if isinstance(materials, list):
            for item in materials:
                evidence_id = cls._evidence_id(item)
                if evidence_id:
                    out.add(evidence_id)
        return out

    @classmethod
    def _extract_feedback_meta(cls, payload: Dict[str, Any]) -> Dict[str, Any]:
        record = cls._as_dict(payload)
        meta = cls._as_dict(record.get("feedback_meta", {}))
        optimize_evaluation = meta.get("optimize_evaluation", record.get("optimize_evaluation", {}))
        if not isinstance(optimize_evaluation, dict):
            optimize_evaluation = {}
        return {
            "feedback_key": cls._normalize_text(record.get("feedback_key", "")),
            "analysis_kind": cls._normalize_text(record.get("analysis_kind", "")) or "feedback_optimize",
            "chain_mode": cls._normalize_text(meta.get("chain_mode", "") or record.get("chain_mode", "")) or "feedback_optimize",
            "feedback_type": cls._normalize_text(meta.get("feedback_type", "") or record.get("feedback_type", "")),
            "decision": cls._normalize_text(meta.get("decision", "") or record.get("decision", "")),
            "manual_modified": bool(meta.get("manual_modified", False)),
            "diff_changed": bool(meta.get("diff_changed", False)),
            "labels": meta.get("labels", []) if isinstance(meta.get("labels", []), list) else [],
            "evidence_feedback": meta.get("evidence_feedback", []) if isinstance(meta.get("evidence_feedback", []), list) else [],
            "retrieval_feedback": cls._normalize_text(meta.get("retrieval_feedback", "") or record.get("retrieval_feedback", "")),
            "conclusion_feedback": cls._normalize_text(meta.get("conclusion_feedback", "") or record.get("conclusion_feedback", "")),
            "feedback_optimize_status": cls._normalize_text(meta.get("feedback_optimize_status", "") or record.get("feedback_optimize_status", "")),
            "candidate_register_status": cls._normalize_text(meta.get("candidate_register_status", "") or record.get("candidate_register_status", "")),
            "replay_status": cls._normalize_text(meta.get("replay_status", "") or record.get("replay_status", "")),
            "error_message": cls._normalize_text(meta.get("error_message", "") or record.get("error_message", "")),
            "suggestion": cls._normalize_text(meta.get("suggestion", "") or record.get("suggestion", "")),
            "optimize_evaluation": optimize_evaluation,
        }

    @classmethod
    def _merge_analysis_payloads(cls, payloads: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        grouped: Dict[str, Dict[str, Any]] = {}
        ordered_keys: List[str] = []
        for index, raw_payload in enumerate(payloads or []):
            payload = deepcopy(raw_payload) if isinstance(raw_payload, dict) else {}
            meta = cls._extract_feedback_meta(payload)
            feedback_key = meta["feedback_key"] or f"feedback_event_{index}"
            if feedback_key not in grouped:
                grouped[feedback_key] = payload
                ordered_keys.append(feedback_key)
                continue
            merged = grouped[feedback_key]
            for key, value in payload.items():
                if key == "analysis_kind":
                    existing_kind = cls._normalize_text(merged.get("analysis_kind", ""))
                    incoming_kind = cls._normalize_text(value)
                    if existing_kind == "feedback_optimize":
                        continue
                    if incoming_kind:
                        merged[key] = incoming_kind
                    continue
                if isinstance(value, dict) and isinstance(merged.get(key, {}), dict):
                    next_value = dict(merged.get(key, {}))
                    next_value.update(value)
                    merged[key] = next_value
                elif value not in (None, "", [], {}):
                    merged[key] = value
            merged_meta = cls._as_dict(merged.get("feedback_meta", {}))
            incoming_meta = cls._as_dict(payload.get("feedback_meta", {}))
            for meta_key, meta_value in incoming_meta.items():
                if meta_value not in (None, "", [], {}):
                    merged_meta[meta_key] = meta_value
            merged["feedback_meta"] = merged_meta
            grouped[feedback_key] = merged
        return [grouped[key] for key in ordered_keys]

    @classmethod
    def _case_review_detail(cls, result: Dict[str, Any]) -> Dict[str, Any]:
        replay = cls._as_dict(result.get("replay_result", result))
        replay_data = cls._as_dict(replay.get("data", {}))
        expected = cls._extract_expected_conclusion(result)
        actual = cls._extract_actual_conclusion(result)
        expected_conclusion = cls._normalize_text(expected.get("conclusion", expected.get("pre_review_conclusion", "")))
        actual_conclusion = cls._normalize_text(actual.get("conclusion", actual.get("pre_review_conclusion", "")))
        expected_risk = cls._normalize_text(expected.get("risk_level", ""))
        actual_risk = cls._normalize_text(actual.get("risk_level", ""))
        expected_findings = cls._extract_problem_detection_set(expected)
        actual_findings = cls._extract_problem_detection_set(actual)
        expected_rules = cls._normalize_string_set(expected.get("linked_rules", []))
        actual_rules = cls._normalize_string_set(actual.get("linked_rules", []))
        finding_tp = len(expected_findings & actual_findings)
        finding_fp = len(actual_findings - expected_findings)
        finding_fn = len(expected_findings - actual_findings)
        finding_precision = _safe_ratio(finding_tp, finding_tp + finding_fp)
        finding_recall = _safe_ratio(finding_tp, finding_tp + finding_fn)
        finding_f1 = _safe_f1(finding_precision, finding_recall)
        rule_tp = len(expected_rules & actual_rules)
        rule_fp = len(actual_rules - expected_rules)
        rule_fn = len(expected_rules - actual_rules)
        rule_precision = _safe_ratio(rule_tp, rule_tp + rule_fp)
        rule_recall = _safe_ratio(rule_tp, rule_tp + rule_fn)
        rule_f1 = _safe_f1(rule_precision, rule_recall)
        conclusion_match = bool(expected_conclusion) and expected_conclusion == actual_conclusion
        risk_match = (not expected_risk and not actual_risk) or (expected_risk and expected_risk == actual_risk)
        composite_score = (
            0.55 * (1.0 if conclusion_match else 0.0)
            + 0.15 * (1.0 if risk_match else 0.0)
            + 0.20 * finding_f1
            + 0.10 * rule_f1
        )
        return {
            "case_id": cls._extract_case_id(result),
            "success": bool(replay.get("success", False)),
            "message": cls._normalize_text(replay.get("message", "")),
            "replay_mode": cls._normalize_text(result.get("replay_mode", replay_data.get("mode", ""))),
            "expected_conclusion": expected_conclusion,
            "actual_conclusion": actual_conclusion,
            "conclusion_match": conclusion_match,
            "expected_risk_level": expected_risk,
            "actual_risk_level": actual_risk,
            "risk_level_match": risk_match,
            "finding_precision": finding_precision,
            "finding_recall": finding_recall,
            "finding_f1": finding_f1,
            "rule_precision": rule_precision,
            "rule_recall": rule_recall,
            "rule_f1": rule_f1,
            "correct_issue_count": finding_tp,
            "false_positive_issue_count": finding_fp,
            "missed_issue_count": finding_fn,
            "issue_accuracy": _safe_ratio(finding_tp, finding_tp + finding_fp + finding_fn),
            "issue_precision": finding_precision,
            "issue_recall": finding_recall,
            "issue_f1": finding_f1,
            "expected_issue_count": len(expected_findings),
            "actual_issue_count": len(actual_findings),
            "expected_finding_count": len(expected_findings),
            "actual_finding_count": len(actual_findings),
            "expected_rule_count": len(expected_rules),
            "actual_rule_count": len(actual_rules),
            "composite_score": composite_score if bool(replay.get("success", False)) else 0.0,
        }

    @classmethod
    def _case_retrieval_detail(cls, result: Dict[str, Any]) -> Dict[str, Any]:
        replay = cls._as_dict(result.get("replay_result", result))
        expected_trace = cls._extract_expected_trace(result)
        actual_trace = cls._extract_actual_trace(result)
        expected_approved = cls._extract_approved_evidence_ids(expected_trace)
        actual_approved = cls._extract_approved_evidence_ids(actual_trace)
        expected_rejected = cls._extract_rejected_evidence_ids(expected_trace)
        actual_rejected = cls._extract_rejected_evidence_ids(actual_trace)
        approved_tp = len(expected_approved & actual_approved)
        approved_fp = len(actual_approved - expected_approved)
        approved_fn = len(expected_approved - actual_approved)
        approved_precision = _safe_ratio(approved_tp, approved_tp + approved_fp)
        approved_recall = _safe_ratio(approved_tp, approved_tp + approved_fn)
        approved_f1 = _safe_f1(approved_precision, approved_recall)
        exact_match = expected_approved == actual_approved and expected_rejected == actual_rejected
        return {
            "case_id": cls._extract_case_id(result),
            "success": bool(replay.get("success", False)),
            "approved_precision": approved_precision,
            "approved_recall": approved_recall,
            "approved_f1": approved_f1,
            "approved_exact_match": exact_match,
            "expected_approved_count": len(expected_approved),
            "actual_approved_count": len(actual_approved),
            "expected_rejected_count": len(expected_rejected),
            "actual_rejected_count": len(actual_rejected),
            "approved_tp": approved_tp,
            "approved_fp": approved_fp,
            "approved_fn": approved_fn,
            "composite_score": approved_f1 if bool(replay.get("success", False)) else 0.0,
        }

    @classmethod
    def calc_retrieval_metrics(cls, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        case_details = [cls._case_retrieval_detail(item if isinstance(item, dict) else {}) for item in results or []]
        case_count = len(case_details)
        success_count = sum(1 for item in case_details if item["success"])
        approved_tp = sum(int(item["approved_tp"]) for item in case_details)
        approved_fp = sum(int(item["approved_fp"]) for item in case_details)
        approved_fn = sum(int(item["approved_fn"]) for item in case_details)
        approved_precision = _safe_ratio(approved_tp, approved_tp + approved_fp)
        approved_recall = _safe_ratio(approved_tp, approved_tp + approved_fn)
        approved_f1 = _safe_f1(approved_precision, approved_recall)
        exact_match_count = sum(1 for item in case_details if item["approved_exact_match"])
        average_score = _safe_ratio(sum(float(item["composite_score"]) for item in case_details), case_count)
        return {
            "case_count": case_count,
            "success_count": success_count,
            "failed_count": max(case_count - success_count, 0),
            "success_rate": _safe_ratio(success_count, case_count),
            "evaluated_case_count": case_count,
            "approved_exact_match_count": exact_match_count,
            "approved_exact_match_rate": _safe_ratio(exact_match_count, case_count),
            "true_positive_count": approved_tp,
            "false_positive_count": approved_fp,
            "false_negative_count": approved_fn,
            "accuracy": _safe_ratio(approved_tp, approved_tp + approved_fp + approved_fn),
            "precision": approved_precision,
            "recall": approved_recall,
            "f1": approved_f1,
            "retrieval_coverage": approved_recall,
            "composite_score": average_score,
            "case_details": case_details,
        }

    @classmethod
    def calc_review_metrics(cls, results: List[Dict[str, Any]]) -> Dict[str, Any]:
        case_details = [cls._case_review_detail(item if isinstance(item, dict) else {}) for item in results or []]
        case_count = len(case_details)
        success_count = sum(1 for item in case_details if item["success"])
        conclusion_match_count = sum(1 for item in case_details if item["conclusion_match"])
        risk_match_count = sum(1 for item in case_details if item["risk_level_match"])
        exact_match_count = sum(1 for item in case_details if item["conclusion_match"] and item["risk_level_match"])
        correct_issue_count = sum(int(item.get("correct_issue_count", 0) or 0) for item in case_details)
        false_positive_issue_count = sum(int(item.get("false_positive_issue_count", 0) or 0) for item in case_details)
        missed_issue_count = sum(int(item.get("missed_issue_count", 0) or 0) for item in case_details)
        issue_precision = _safe_ratio(correct_issue_count, correct_issue_count + false_positive_issue_count)
        issue_recall = _safe_ratio(correct_issue_count, correct_issue_count + missed_issue_count)
        issue_f1 = _safe_f1(issue_precision, issue_recall)
        issue_accuracy = _safe_ratio(correct_issue_count, correct_issue_count + false_positive_issue_count + missed_issue_count)
        finding_precision = _safe_ratio(sum(float(item["finding_precision"]) for item in case_details), case_count)
        finding_recall = _safe_ratio(sum(float(item["finding_recall"]) for item in case_details), case_count)
        finding_f1 = _safe_ratio(sum(float(item["finding_f1"]) for item in case_details), case_count)
        rule_precision = _safe_ratio(sum(float(item["rule_precision"]) for item in case_details), case_count)
        rule_recall = _safe_ratio(sum(float(item["rule_recall"]) for item in case_details), case_count)
        rule_f1 = _safe_ratio(sum(float(item["rule_f1"]) for item in case_details), case_count)
        average_score = _safe_ratio(sum(float(item["composite_score"]) for item in case_details), case_count)
        conclusion_match_rate = _safe_ratio(conclusion_match_count, case_count)
        return {
            "case_count": case_count,
            "success_count": success_count,
            "failed_count": max(case_count - success_count, 0),
            "success_rate": _safe_ratio(success_count, case_count),
            "conclusion_match_count": conclusion_match_count,
            "conclusion_match_rate": conclusion_match_rate,
            "risk_level_match_count": risk_match_count,
            "risk_level_match_rate": _safe_ratio(risk_match_count, case_count),
            "exact_match_count": exact_match_count,
            "exact_match_rate": _safe_ratio(exact_match_count, case_count),
            "correct_issue_count": correct_issue_count,
            "false_positive_issue_count": false_positive_issue_count,
            "missed_issue_count": missed_issue_count,
            "issue_accuracy": issue_accuracy,
            "issue_precision": issue_precision,
            "issue_recall": issue_recall,
            "issue_f1": issue_f1,
            "finding_precision": finding_precision,
            "finding_recall": finding_recall,
            "finding_f1": finding_f1,
            "rule_precision": rule_precision,
            "rule_recall": rule_recall,
            "rule_f1": rule_f1,
            "review_accuracy": conclusion_match_rate,
            "composite_score": average_score,
            "problem_detection": {
                "correct_issue_count": correct_issue_count,
                "false_positive_issue_count": false_positive_issue_count,
                "missed_issue_count": missed_issue_count,
                "accuracy": issue_accuracy,
                "precision": issue_precision,
                "recall": issue_recall,
                "f1": issue_f1,
            },
            "case_details": case_details,
        }

    @classmethod
    def calc_feedback_metrics(
        cls,
        feedback_records: List[Any],
        analysis_payloads: Optional[List[Dict[str, Any]]] = None,
        previous_false_positive_count: Optional[int] = None,
    ) -> Dict[str, Any]:
        normalized_feedback_records = []
        for item in feedback_records or []:
            if isinstance(item, dict):
                normalized_feedback_records.append({"feedback_type": cls._normalize_text(item.get("feedback_type", ""))})
            else:
                normalized_feedback_records.append({"feedback_type": cls._normalize_text(item)})
        base_review = feedback_metrics([item["feedback_type"] for item in normalized_feedback_records if item["feedback_type"]])
        payloads = cls._merge_analysis_payloads(
            [item for item in (analysis_payloads or []) if isinstance(item, dict)]
        )
        feedback_only_count = 0
        feedback_optimize_count = 0
        feedback_optimize_success_count = 0
        feedback_optimize_failed_count = 0
        candidate_register_success_count = 0
        candidate_register_failed_count = 0
        replay_pass_count = 0
        replay_failed_count = 0
        manual_total = 0
        manual_changed = 0
        retrieval_tp = 0
        retrieval_fp = 0
        retrieval_fn = 0
        optimize_evaluation_count = 0
        optimize_evaluation_pass_count = 0
        verdict_breakdown: Dict[str, int] = {}
        breakdown: Dict[str, int] = {}

        for payload in payloads:
            meta = cls._extract_feedback_meta(payload)
            analysis_kind = meta["analysis_kind"]
            if meta["chain_mode"] == "feedback_only":
                feedback_only_count += 1
            elif meta["chain_mode"] == "feedback_optimize":
                feedback_optimize_count += 1
            if meta["feedback_optimize_status"] == "completed":
                feedback_optimize_success_count += 1
            elif meta["feedback_optimize_status"] == "failed":
                feedback_optimize_failed_count += 1
            if meta["candidate_register_status"] == "completed":
                candidate_register_success_count += 1
            elif meta["candidate_register_status"] == "failed":
                candidate_register_failed_count += 1
            if meta["replay_status"] == "passed":
                replay_pass_count += 1
            elif meta["replay_status"] == "failed":
                replay_failed_count += 1
            if meta["manual_modified"] or meta["diff_changed"]:
                manual_changed += 1
            if analysis_kind in {"feedback_event", "feedback_optimize"}:
                manual_total += 1
            optimize_evaluation = meta["optimize_evaluation"] if isinstance(meta["optimize_evaluation"], dict) else {}
            if optimize_evaluation:
                optimize_evaluation_count += 1
                verdict = cls._normalize_text(optimize_evaluation.get("overall_verdict", ""))
                if verdict:
                    verdict_breakdown[verdict] = int(verdict_breakdown.get(verdict, 0)) + 1
                if verdict in {"accepted", "improved", "pass", "passed", "better"}:
                    optimize_evaluation_pass_count += 1
            if analysis_kind != "feedback_optimize":
                continue
            error_types = {
                cls._normalize_text(x)
                for x in payload.get("error_types", [])
                if cls._normalize_text(x)
            }
            primary_error_type = cls._normalize_text(payload.get("primary_error_type", ""))
            if primary_error_type:
                error_types.add(primary_error_type)
            if error_types & cls.RETRIEVAL_FALSE_NEGATIVE_ERRORS:
                retrieval_fn += 1
            elif error_types & cls.RETRIEVAL_FALSE_POSITIVE_ERRORS:
                retrieval_fp += 1
            else:
                retrieval_tp += 1
            for item in error_types:
                breakdown[item] = int(breakdown.get(item, 0)) + 1

        retrieval_total = retrieval_tp + retrieval_fp + retrieval_fn
        retrieval_precision = _safe_ratio(retrieval_tp, retrieval_tp + retrieval_fp)
        retrieval_recall = _safe_ratio(retrieval_tp, retrieval_tp + retrieval_fn)
        trajectory = {
            "previous_false_positive_count": int(previous_false_positive_count) if previous_false_positive_count is not None else None,
            "false_positive_delta": None,
            "false_positive_reduction_rate": None,
        }
        if previous_false_positive_count is not None:
            previous_fp = int(previous_false_positive_count)
            fp_delta = previous_fp - int(base_review["fp_false_positive"])
            trajectory["false_positive_delta"] = fp_delta
            trajectory["false_positive_reduction_rate"] = _safe_ratio(max(fp_delta, 0), previous_fp) if previous_fp > 0 else None
        problem_detection = {
            "feedback_total": int(base_review["feedback_total"]),
            "correct_issue_count": int(base_review["tp_valid"]),
            "false_positive_issue_count": int(base_review["fp_false_positive"]),
            "missed_issue_count": int(base_review["fn_missed"]),
            "accuracy": float(base_review["accuracy"]),
            "precision": float(base_review["precision"]),
            "recall": float(base_review["recall"]),
            "f1": float(base_review["f1"]),
            "reward_score": float(base_review["reward_score"]),
        }
        return {
            "feedback_count": len(normalized_feedback_records),
            "review": {
                "feedback_total": int(base_review["feedback_total"]),
                "valid_count": int(base_review["tp_valid"]),
                "false_positive_count": int(base_review["fp_false_positive"]),
                "missed_count": int(base_review["fn_missed"]),
                "correct_issue_count": int(base_review["tp_valid"]),
                "false_positive_issue_count": int(base_review["fp_false_positive"]),
                "missed_issue_count": int(base_review["fn_missed"]),
                "issue_accuracy": float(base_review["accuracy"]),
                "issue_precision": float(base_review["precision"]),
                "issue_recall": float(base_review["recall"]),
                "issue_f1": float(base_review["f1"]),
                "accuracy": float(base_review["accuracy"]),
                "precision": float(base_review["precision"]),
                "recall": float(base_review["recall"]),
                "f1": float(base_review["f1"]),
                "reward_score": float(base_review["reward_score"]),
                "problem_detection": {
                    "feedback_total": int(base_review["feedback_total"]),
                    "correct_issue_count": int(base_review["tp_valid"]),
                    "false_positive_issue_count": int(base_review["fp_false_positive"]),
                    "missed_issue_count": int(base_review["fn_missed"]),
                    "accuracy": float(base_review["accuracy"]),
                    "precision": float(base_review["precision"]),
                    "recall": float(base_review["recall"]),
                    "f1": float(base_review["f1"]),
                    "reward_score": float(base_review["reward_score"]),
                },
            },
            "problem_detection": problem_detection,
            "retrieval": {
                "evaluated_feedback_count": int(retrieval_total),
                "true_positive_count": int(retrieval_tp),
                "false_positive_count": int(retrieval_fp),
                "false_negative_count": int(retrieval_fn),
                "accuracy": float(_safe_ratio(retrieval_tp, retrieval_total)),
                "precision": float(retrieval_precision),
                "recall": float(retrieval_recall),
                "f1": float(_safe_f1(retrieval_precision, retrieval_recall)),
                "error_breakdown": breakdown,
            },
            "feedback": {
                "feedback_only_count": int(feedback_only_count),
                "feedback_optimize_count": int(feedback_optimize_count),
                "feedback_optimize_success_count": int(feedback_optimize_success_count),
                "feedback_optimize_failed_count": int(feedback_optimize_failed_count),
                "candidate_register_success_count": int(candidate_register_success_count),
                "candidate_register_failed_count": int(candidate_register_failed_count),
                "replay_pass_count": int(replay_pass_count),
                "replay_failed_count": int(replay_failed_count),
                "feedback_acceptance_rate": float(_safe_ratio(base_review["tp_valid"], base_review["feedback_total"])),
                "manual_modification_count": int(manual_changed),
                "manual_modification_total": int(manual_total),
                "manual_modification_rate": float(_safe_ratio(manual_changed, manual_total)),
                "optimize_evaluation_count": int(optimize_evaluation_count),
                "optimize_evaluation_pass_count": int(optimize_evaluation_pass_count),
                "optimize_evaluation_pass_rate": float(_safe_ratio(optimize_evaluation_pass_count, optimize_evaluation_count)),
                "optimize_evaluation_breakdown": verdict_breakdown,
            },
            "trajectory": trajectory,
        }

    @classmethod
    def calc_version_gain(cls, old_results: List[Dict[str, Any]], new_results: List[Dict[str, Any]]) -> Dict[str, Any]:
        baseline_review = cls.calc_review_metrics(old_results)
        candidate_review = cls.calc_review_metrics(new_results)
        baseline_retrieval = cls.calc_retrieval_metrics(old_results)
        candidate_retrieval = cls.calc_retrieval_metrics(new_results)
        old_case_map = {
            item["case_id"]: item
            for item in baseline_review.get("case_details", [])
            if cls._normalize_text(item.get("case_id", ""))
        }
        new_case_map = {
            item["case_id"]: item
            for item in candidate_review.get("case_details", [])
            if cls._normalize_text(item.get("case_id", ""))
        }
        case_ids = []
        for item in list(old_case_map.keys()) + list(new_case_map.keys()):
            if item and item not in case_ids:
                case_ids.append(item)
        wins = losses = ties = 0
        case_deltas: List[Dict[str, Any]] = []
        for case_id in case_ids:
            old_case = old_case_map.get(case_id, {})
            new_case = new_case_map.get(case_id, {})
            old_score = float(old_case.get("composite_score", 0.0) or 0.0)
            new_score = float(new_case.get("composite_score", 0.0) or 0.0)
            delta = new_score - old_score
            if delta > 1e-9:
                wins += 1
            elif delta < -1e-9:
                losses += 1
            else:
                ties += 1
            case_deltas.append(
                {
                    "case_id": case_id,
                    "baseline_score": old_score,
                    "candidate_score": new_score,
                    "delta": delta,
                    "baseline_conclusion_match": bool(old_case.get("conclusion_match", False)),
                    "candidate_conclusion_match": bool(new_case.get("conclusion_match", False)),
                }
            )
        review_delta = float(candidate_review.get("composite_score", 0.0) or 0.0) - float(baseline_review.get("composite_score", 0.0) or 0.0)
        retrieval_delta = float(candidate_retrieval.get("composite_score", 0.0) or 0.0) - float(baseline_retrieval.get("composite_score", 0.0) or 0.0)
        overall_delta = 0.7 * review_delta + 0.3 * retrieval_delta
        recommendation = "manual_review_required"
        if overall_delta > 1e-9 and losses == 0 and float(candidate_review.get("conclusion_match_rate", 0.0) or 0.0) >= float(baseline_review.get("conclusion_match_rate", 0.0) or 0.0):
            recommendation = "candidate_preferred"
        elif overall_delta < -1e-9 or losses > wins:
            recommendation = "candidate_rejected"
        return {
            "old_count": len(old_results or []),
            "new_count": len(new_results or []),
            "baseline_metrics": {
                "review": baseline_review,
                "retrieval": baseline_retrieval,
            },
            "candidate_metrics": {
                "review": candidate_review,
                "retrieval": candidate_retrieval,
            },
            "case_deltas": case_deltas,
            "summary": {
                "case_count": len(case_ids),
                "win_count": wins,
                "loss_count": losses,
                "tie_count": ties,
                "review_delta": review_delta,
                "retrieval_delta": retrieval_delta,
                "overall_delta": overall_delta,
                "recommendation": recommendation,
            },
        }

    @classmethod
    def build_feature_flags(cls, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, bool]:
        flags = dict(cls.DEFAULT_FEATURE_FLAGS)
        for key, value in cls.normalize_feature_overrides(overrides).items():
            flags[key] = value
        return flags

    @classmethod
    def normalize_feature_overrides(cls, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, bool]:
        normalized: Dict[str, bool] = {}
        for key, value in (overrides or {}).items():
            if key in cls.DEFAULT_FEATURE_FLAGS and isinstance(value, bool):
                normalized[key] = value
        return normalized
