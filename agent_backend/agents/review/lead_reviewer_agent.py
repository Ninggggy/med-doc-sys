from __future__ import annotations

from typing import Any, Dict, List

from agent.agent_backend.llm.client import LLMClient
from agent.agent_backend.llm.errors import LLMExecutionError
from agent.agent_backend.prompts.template_manager import PromptTemplateManager


class LeadReviewerAgent:
    """Aggregate reviewed sections into final run-level summary."""

    def __init__(self) -> None:
        self.llm = LLMClient()
        self.prompts = PromptTemplateManager("pre_review_agent_prompt")

    def describe(self) -> Dict[str, Any]:
        return {
            "name": "lead_reviewer",
            "description": "Aggregate section conclusions into overall conclusions and key supplement questions.",
            "inputs": ["section_results", "consistency_result", "qa_result"],
            "outputs": [
                "overall_conclusion",
                "risk_map",
                "key_questions",
                "compliance_highlights",
                "problem_basis_advice_items",
                "task_status_breakdown",
                "task_code_breakdown",
                "issue_status_breakdown",
            ],
        }

    @staticmethod
    def _normalize_problem_basis_advice_items(values: Any) -> List[Dict[str, str]]:
        if not isinstance(values, list):
            return []
        out: List[Dict[str, str]] = []
        seen = set()
        for item in values:
            if not isinstance(item, dict):
                continue
            row = {
                "section_id": str(item.get("section_id", "") or "").strip(),
                "section_name": str(item.get("section_name", "") or "").strip(),
                "status": str(item.get("status", "") or "").strip().lower() or "issue",
                "problem": str(item.get("problem", "") or item.get("issue", "") or "").strip(),
                "basis": str(item.get("basis", "") or item.get("evidence", "") or "").strip(),
                "advice": str(item.get("advice", "") or item.get("recommendation", "") or "").strip(),
                "risk_level": str(item.get("risk_level", "") or "").strip(),
            }
            if not row["problem"] and not row["basis"] and not row["advice"]:
                continue
            key = "|".join([row["section_id"], row["section_name"], row["status"], row["problem"], row["basis"], row["advice"]])
            if key in seen:
                continue
            seen.add(key)
            out.append(row)
        return out

    def _aggregate_problem_basis_advice_items(self, section_results: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        items: List[Dict[str, Any]] = []
        for item in section_results or []:
            if not isinstance(item, dict):
                continue
            section_id = str(item.get("section_id", "") or "").strip()
            section_name = str(item.get("section_name", "") or "").strip()
            risk_level = str(item.get("risk_level", "") or "").strip()
            source_items = item.get("problem_basis_advice_items", [])
            if isinstance(source_items, list):
                for entry in source_items:
                    if not isinstance(entry, dict):
                        continue
                    merged = dict(entry)
                    merged.setdefault("section_id", section_id)
                    merged.setdefault("section_name", section_name)
                    merged.setdefault("risk_level", risk_level)
                    items.append(merged)
                continue
            for finding in item.get("highlighted_issues", []) if isinstance(item.get("highlighted_issues", []), list) else []:
                if not isinstance(finding, dict):
                    continue
                items.append(
                    {
                        "section_id": section_id,
                        "section_name": section_name,
                        "status": "issue",
                        "problem": str(finding.get("title", "") or "").strip(),
                        "basis": str(finding.get("evidence", "") or "").strip(),
                        "advice": str(finding.get("recommendation", "") or "").strip(),
                        "risk_level": risk_level,
                    }
                )
        return self._normalize_problem_basis_advice_items(items)[:12]

    @staticmethod
    def _build_task_status_breakdown(section_results: List[Dict[str, Any]]) -> Dict[str, int]:
        breakdown: Dict[str, int] = {}
        for item in section_results or []:
            if not isinstance(item, dict):
                continue
            for verdict in item.get("task_verdicts", []) if isinstance(item.get("task_verdicts", []), list) else []:
                if not isinstance(verdict, dict):
                    continue
                status = str(verdict.get("status", "") or "").strip().lower()
                if not status:
                    continue
                breakdown[status] = int(breakdown.get(status, 0)) + 1
        return breakdown

    @staticmethod
    def _build_task_code_breakdown(section_results: List[Dict[str, Any]]) -> Dict[str, int]:
        breakdown: Dict[str, int] = {}
        for item in section_results or []:
            if not isinstance(item, dict):
                continue
            for verdict in item.get("task_verdicts", []) if isinstance(item.get("task_verdicts", []), list) else []:
                if not isinstance(verdict, dict):
                    continue
                task_code = str(verdict.get("task_code", "") or "").strip()
                if not task_code:
                    continue
                breakdown[task_code] = int(breakdown.get(task_code, 0)) + 1
        return breakdown

    @staticmethod
    def _build_issue_status_breakdown(problem_basis_advice_items: List[Dict[str, str]]) -> Dict[str, int]:
        breakdown: Dict[str, int] = {}
        for item in problem_basis_advice_items:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status", "") or "").strip().lower()
            if not status:
                continue
            breakdown[status] = int(breakdown.get(status, 0)) + 1
        return breakdown

    def summarize(
        self,
        project_meta: Dict[str, Any],
        section_results: List[Dict[str, Any]],
        consistency_result: Dict[str, Any],
        qa_result: Dict[str, Any],
        prompt_config: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        concise_sections = []
        for item in section_results[:40]:
            if not isinstance(item, dict):
                continue
            concise_sections.append(
                {
                    "section_id": item.get("section_id", ""),
                    "section_name": item.get("section_name", ""),
                    "risk_level": item.get("risk_level", "low"),
                    "conclusion": item.get("conclusion", ""),
                    "issues": item.get("highlighted_issues", []),
                    "problem_basis_advice_items": item.get("problem_basis_advice_items", []),
                    "task_verdicts": item.get("task_verdicts", []),
                }
            )
        aggregated_items = self._aggregate_problem_basis_advice_items(section_results)
        task_status_breakdown = self._build_task_status_breakdown(section_results)
        task_code_breakdown = self._build_task_code_breakdown(section_results)
        issue_status_breakdown = self._build_issue_status_breakdown(aggregated_items)
        prompt = self.prompts.render(
            "lead_reviewer.j2",
            {
                "project_meta": project_meta,
                "section_results": concise_sections,
                "consistency_result": consistency_result,
                "qa_result": qa_result,
                "problem_basis_advice_items": aggregated_items,
                "task_status_breakdown": task_status_breakdown,
                "task_code_breakdown": task_code_breakdown,
                "issue_status_breakdown": issue_status_breakdown,
            },
            prompt_config=prompt_config or {},
        )
        raw = self.llm.chat(
            messages=[
                {"role": "system", "content": "你是总评汇总代理。只输出合法 JSON。"},
                {"role": "user", "content": prompt},
            ],
            default='{"overall_conclusion": "", "risk_map": [], "key_questions": [], "compliance_highlights": [], "problem_basis_advice_items": [], "summary": ""}',
        )
        data = self.llm.extract_json(raw)
        if data is None:
            raise LLMExecutionError("model_invalid_json", stage="lead_review")
        if (not isinstance(data, dict)
                or any(not isinstance(data.get(key), str) or not data[key].strip() for key in ("overall_conclusion", "summary"))
                or any(not isinstance(data.get(key), list) for key in ("risk_map", "key_questions", "compliance_highlights", "problem_basis_advice_items"))
                or any(not isinstance(item, dict) for key in ("risk_map", "problem_basis_advice_items") for item in data[key])
                or any(not isinstance(item, str) or not item.strip() for key in ("key_questions", "compliance_highlights") for item in data[key])):
            raise LLMExecutionError("model_invalid_output", stage="lead_review")
        return {
            "overall_conclusion": str(data.get("overall_conclusion", "") or "").strip(),
            "risk_map": [item for item in data.get("risk_map", []) if isinstance(item, dict)] if isinstance(data.get("risk_map", []), list) else [],
            "key_questions": [str(x).strip() for x in data.get("key_questions", []) if str(x).strip()] if isinstance(data.get("key_questions", []), list) else [],
            "compliance_highlights": [str(x).strip() for x in data.get("compliance_highlights", []) if str(x).strip()] if isinstance(data.get("compliance_highlights", []), list) else [],
            "problem_basis_advice_items": self._normalize_problem_basis_advice_items(data.get("problem_basis_advice_items", [])) or aggregated_items,
            "task_status_breakdown": task_status_breakdown,
            "task_code_breakdown": task_code_breakdown,
            "issue_status_breakdown": issue_status_breakdown,
            "summary": str(data.get("summary", "") or "").strip(),
        }
