from __future__ import annotations

from typing import Any, Dict, List


class WorkflowOptimizerAgent:
    """Generate workflow-level optimization candidates from feedback attribution."""

    def describe(self) -> Dict[str, Any]:
        return {
            "name": "workflow_optimizer",
            "description": "Suggest workflow graph changes such as stronger retrieval, QA insertion, or consistency ordering.",
        }

    def propose(
        self,
        feedback_record: Dict[str, Any],
        root_cause: Dict[str, Any],
        run_trace: Dict[str, Any],
    ) -> Dict[str, Any]:
        category = str(root_cause.get("root_category", root_cause.get("category", "")) or "").lower()
        sub_category = str(root_cause.get("sub_category", "") or "").lower()
        decision = str(feedback_record.get("decision", "") or "").lower()
        actions: List[str] = []
        graph_patch: Dict[str, Any] = {"insert_nodes": [], "reorder_nodes": [], "force_flags": []}

        if category in {"rag_issue", "retrieval"} or "retrieval" in sub_category:
            actions.append("在 reviewer 前强制执行更严格的证据筛选")
            graph_patch["force_flags"].append("force_rule_retrieval")
        if category in {"agent_issue", "workflow_issue"} or "workflow" in sub_category:
            actions.append("在汇总前保留 QA 节点并把高风险问题先过一致性检查")
            graph_patch["reorder_nodes"].append(["consistency", "qa_sections", "qa_run", "lead"])
        if decision in {"missed", "missing_risk"}:
            actions.append("对高风险章节开启强制二次复核")
            graph_patch["force_flags"].append("double_review_high_risk_sections")
        if decision in {"false_positive", "rejected"}:
            actions.append("在总结前对证据不足问题执行降级")
            graph_patch["insert_nodes"].append("evidence_gate_before_lead")

        return {
            "type": "workflow_fix",
            "root_cause": root_cause,
            "actions": actions,
            "graph_patch": graph_patch,
            "trace_summary": {
                "agent_steps": len(run_trace.get("agent_steps", []) or []) if isinstance(run_trace, dict) else 0,
            },
        }
