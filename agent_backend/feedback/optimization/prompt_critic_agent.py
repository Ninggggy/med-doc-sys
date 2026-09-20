from __future__ import annotations

from typing import Any, Dict, List


class PromptCriticAgent:
    """Analyze failed feedback cases and produce prompt optimization suggestions."""

    TEMPLATE_ORDER = [
        "chapter_planner.j2",
        "retrieval_evaluator.j2",
        "task_question.j2",
        "chapter_reviewer.j2",
        "feedback_analyzer.j2",
        "feedback_optimizer.j2",
    ]

    def describe(self) -> Dict[str, Any]:
        return {
            "name": "prompt_critic",
            "description": "Convert feedback and root cause into prompt-target diagnosis and template patches.",
        }

    def analyze(self, feedback_record: Dict[str, Any], root_cause: Dict[str, Any]) -> Dict[str, Any]:
        decision = str(feedback_record.get("decision", "") or "").lower()
        labels = [str(x).strip() for x in feedback_record.get("labels", []) or [] if str(x).strip()]
        feedback_text = str(feedback_record.get("feedback_text", "") or "").strip()
        suggestion = str(feedback_record.get("suggestion", "") or "").strip()
        issue_feedback = feedback_record.get("issue_feedback", []) if isinstance(feedback_record.get("issue_feedback", []), list) else []
        paragraph_feedback = feedback_record.get("paragraph_feedback", []) if isinstance(feedback_record.get("paragraph_feedback", []), list) else []
        evidence_feedback = feedback_record.get("evidence_feedback", []) if isinstance(feedback_record.get("evidence_feedback", []), list) else []
        category = str(root_cause.get("category", root_cause.get("root_category", "")) or "").lower()
        sub_category = str(root_cause.get("sub_category", root_cause.get("primary_error_type", "")) or "").lower()

        target_templates: List[str] = []
        if category in {"prompt", "agent", "prompt_issue"}:
            target_templates.extend(["chapter_planner.j2", "task_question.j2", "chapter_reviewer.j2"])
        if category in {"rag", "retrieval", "rag_issue"} or "retrieval" in sub_category or sub_category in {"query_miss", "retrieval_scope_error", "retrieval_ranking_error"}:
            target_templates.extend(["chapter_planner.j2", "retrieval_evaluator.j2"])
        if sub_category in {"knowledge_understanding_error", "section_fact_extraction_error", "focus_point_miss"}:
            target_templates.extend(["chapter_planner.j2"])
        if sub_category in {"task_question_error", "rule_mapping_error", "reasoning_chain_error", "evidence_interpretation_error"}:
            target_templates.extend(["task_question.j2", "chapter_reviewer.j2"])
        if decision in {"missed", "missing_risk"}:
            target_templates.extend(["chapter_planner.j2", "retrieval_evaluator.j2", "task_question.j2", "chapter_reviewer.j2"])
        if decision in {"false_positive", "rejected"}:
            target_templates.extend(["chapter_reviewer.j2"])
        if labels or feedback_text:
            target_templates.extend(["feedback_analyzer.j2", "feedback_optimizer.j2"])
        target_templates = [name for name in self.TEMPLATE_ORDER if name in set(target_templates)] or ["chapter_reviewer.j2"]

        diagnosis = {
            "decision": decision,
            "labels": labels,
            "root_cause_category": category,
            "root_cause_sub_category": sub_category,
            "issue_feedback_count": len(issue_feedback),
            "paragraph_feedback_count": len(paragraph_feedback),
            "evidence_feedback_count": len(evidence_feedback),
            "feedback_text": feedback_text,
            "suggestion": suggestion,
        }

        optimization_actions: List[str] = []
        if decision in {"missed", "missing_risk"}:
            optimization_actions.append("提高问题发现覆盖率，减少关键问题漏报。")
        if decision in {"false_positive", "rejected"}:
            optimization_actions.append("收紧结论输出门槛，避免证据不足时直接下判断。")
        if evidence_feedback:
            optimization_actions.append("强化证据引用与结论绑定规则。")
        if paragraph_feedback:
            optimization_actions.append("强化段落锚点与事实依据绑定规则。")
        if issue_feedback:
            optimization_actions.append("减少泛化问题和重复问题。")
        if suggestion:
            optimization_actions.append(f"人工建议: {suggestion}")
        if feedback_text:
            optimization_actions.append(f"反馈摘要: {feedback_text}")

        evidence_rules = []
        for item in evidence_feedback[:6]:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("evidence_id", "") or item.get("doc_id", "") or "").strip()
            status = str(item.get("decision", "") or item.get("judgement", "") or "").strip()
            note = str(item.get("feedback_text", "") or item.get("note", "") or "").strip()
            evidence_rules.append(f"证据 {item_id or '[unknown]'} 处理状态 {status or 'unspecified'}，备注：{note or '无'}")

        paragraph_rules = []
        for item in paragraph_feedback[:6]:
            if not isinstance(item, dict):
                continue
            anchor_id = str(item.get("anchor_id", "") or item.get("paragraph_id", "") or "").strip()
            status = str(item.get("decision", "") or item.get("judgement", "") or "").strip()
            note = str(item.get("feedback_text", "") or item.get("note", "") or "").strip()
            paragraph_rules.append(f"段落 {anchor_id or '[unknown]'} 处理状态 {status or 'unspecified'}，备注：{note or '无'}")

        template_suffixes = {
            "chapter_planner.j2": "\n".join(
                [
                    "新增规划规则:",
                    "1. 先显式识别审评对象、任务边界和高风险假设，再生成检索计划。",
                    "2. retrieval_plan 的每一步都要说明该来源要验证什么，不要泛化成整章检索。",
                    "3. 如果命中不足，先补 query 质量和关注点覆盖，再考虑扩大来源范围。",
                ]
            ).strip(),
            "retrieval_evaluator.j2": "\n".join(
                [
                    "新增证据门控规则:",
                    "1. 只保留能直接支撑当前 review_tasks 判断的证据。",
                    "2. 必须输出 coverage_by_task 和 missing_evidence_by_task。",
                    "3. judgment_ready 只能表示是否具备进入 reviewer 的最低证据条件。",
                ]
            ).strip(),
            "task_question.j2": "\n".join(
                [
                    "新增任务问题规则:",
                    "1. reviewer 前必须先把 review_tasks 转成显式 task_questions。",
                    "2. 每个 task_question 都要写清 judgment_basis、comparison_axes、supporting_evidence_ids 和 missing_information。",
                    "3. 没有 task_question 时，不允许 reviewer 直接生成最终结论。",
                ]
            ).strip(),
            "chapter_reviewer.j2": "\n".join(
                [
                    "新增审评规则:",
                    "1. 没有原文事实或检索证据时，不得直接下肯定或否定结论。",
                    "2. 必须输出 task_verdicts 和 reasoning_chain_items，显式写出规则-事实-证据-比较-判断链。",
                    "3. 问题清单必须同时给出 issue、basis、requested_action。",
                    *([f"- {x}" for x in paragraph_rules] if paragraph_rules else []),
                    *([f"- {x}" for x in evidence_rules] if evidence_rules else []),
                ]
            ).strip(),
            "feedback_analyzer.j2": "\n".join(
                [
                    "新增归因规则:",
                    "1. 优先把问题归因到固定 taxonomy，不要输出模糊原因。",
                    "2. 优先区分 knowledge_understanding_error、query_miss、task_question_error、reasoning_chain_error。",
                    "3. new_experience 必须写成可复用规则，不能只是复述用户原话。",
                ]
            ).strip(),
            "feedback_optimizer.j2": "\n".join(
                [
                    "新增优化规则:",
                    "1. 只生成最小 patch，不要重写整套模板。",
                    "2. patch_content 必须是一条可以直接追加到规则层的句子。",
                    "3. target_agent 只能是 planner、retrieval_evaluator、task_question 或 reviewer，保持链路边界清晰。",
                ]
            ).strip(),
        }
        template_suffixes = {k: v for k, v in template_suffixes.items() if k in target_templates}

        return {
            "target_templates": target_templates,
            "diagnosis": diagnosis,
            "optimization_actions": optimization_actions,
            "template_suffixes": template_suffixes,
        }
