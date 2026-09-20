from __future__ import annotations

from typing import Any, Dict, List, Set

from agent.agent_backend.llm.client import LLMClient
from agent.agent_backend.llm.errors import LLMExecutionError
from agent.agent_backend.prompts.template_manager import PromptTemplateManager


class QAAgent:
    """Validate section outputs and run-level aggregation consistency."""

    def __init__(self) -> None:
        self.llm = LLMClient()
        self.prompts = PromptTemplateManager("pre_review_agent_prompt")

    @staticmethod
    def _safe_dict(payload: Any) -> Dict[str, Any]:
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _safe_list(payload: Any) -> List[Any]:
        return payload if isinstance(payload, list) else []

    @staticmethod
    def _build_issue(
        *,
        scope: str,
        issue_type: str,
        severity: str,
        detail: str,
        suggestion: str,
    ) -> Dict[str, str]:
        return {
            "scope": scope,
            "issue_type": issue_type,
            "severity": severity,
            "detail": detail,
            "suggestion": suggestion,
        }

    @classmethod
    def _dedupe_issues(cls, issues: List[Dict[str, Any]]) -> List[Dict[str, str]]:
        out: List[Dict[str, str]] = []
        seen: Set[str] = set()
        for item in issues:
            if not isinstance(item, dict):
                continue
            row = {
                "scope": str(item.get("scope", "") or "section").strip() or "section",
                "issue_type": str(item.get("issue_type", "") or "display_gap").strip() or "display_gap",
                "severity": str(item.get("severity", "") or "medium").strip().lower() or "medium",
                "detail": str(item.get("detail", "") or "").strip(),
                "suggestion": str(item.get("suggestion", "") or "").strip(),
            }
            if not row["detail"]:
                continue
            if row["severity"] not in {"low", "medium", "high"}:
                row["severity"] = "medium"
            key = "|".join([row["scope"], row["issue_type"], row["severity"], row["detail"], row["suggestion"]])
            if key in seen:
                continue
            seen.add(key)
            out.append(row)
        return out

    def _validate_task_verdicts(self, section_result: Dict[str, Any]) -> List[Dict[str, str]]:
        issues: List[Dict[str, str]] = []
        conclusion = str(section_result.get("conclusion", section_result.get("pre_review_conclusion", "")) or "").strip().lower()
        task_verdicts = [item for item in self._safe_list(section_result.get("task_verdicts", [])) if isinstance(item, dict)]
        if conclusion in {"supported", "unsupported", "insufficient_information"} and not task_verdicts:
            issues.append(
                self._build_issue(
                    scope="section",
                    issue_type="missing_field",
                    severity="high",
                    detail="章节已有预审结论，但缺少 task_verdicts，无法回溯逐任务判断。",
                    suggestion="为每个审评任务补齐 task_code、status、basis、reason 和 advice。",
                )
            )
            return issues

        for item in task_verdicts:
            task_code = str(item.get("task_code", "") or "").strip()
            status = str(item.get("status", "") or "").strip().lower()
            basis = str(item.get("basis", "") or "").strip()
            reason = str(item.get("reason", "") or "").strip()
            advice = str(item.get("advice", "") or "").strip()
            if not task_code:
                issues.append(
                    self._build_issue(
                        scope="section",
                        issue_type="missing_field",
                        severity="high",
                        detail="存在缺少 task_code 的 task_verdict，无法关联任务边界。",
                        suggestion="补齐 task_verdict.task_code，并保持与 review_tasks/task_questions 对齐。",
                    )
                )
            if status not in {"supported", "unsupported", "insufficient_information"}:
                issues.append(
                    self._build_issue(
                        scope="section",
                        issue_type="conclusion_conflict",
                        severity="high",
                        detail=f"任务 {task_code or '[unknown]'} 的 status 不合法：{status or '[empty]'}。",
                        suggestion="将 task_verdict.status 约束为 supported、unsupported 或 insufficient_information。",
                    )
                )
            if status in {"unsupported", "insufficient_information"} and (not basis or not reason or not advice):
                issues.append(
                    self._build_issue(
                        scope="section",
                        issue_type="reasoning_gap",
                        severity="medium",
                        detail=f"任务 {task_code or '[unknown]'} 为负向结论，但 basis/reason/advice 不完整。",
                        suggestion="补齐规则依据、判断原因和申请人可执行建议。",
                    )
                )
        return issues

    def _validate_reasoning_chain(self, section_result: Dict[str, Any]) -> List[Dict[str, str]]:
        issues: List[Dict[str, str]] = []
        task_verdicts = [item for item in self._safe_list(section_result.get("task_verdicts", [])) if isinstance(item, dict)]
        reasoning_items = [item for item in self._safe_list(section_result.get("reasoning_chain_items", [])) if isinstance(item, dict)]
        if task_verdicts and not reasoning_items:
            issues.append(
                self._build_issue(
                    scope="section",
                    issue_type="reasoning_gap",
                    severity="high",
                    detail="存在 task_verdicts，但缺少 reasoning_chain_items，无法解释结论形成过程。",
                    suggestion="为每个任务补齐规则要求、原文事实、证据支持、比较分析和判断原因。",
                )
            )
            return issues

        reasoning_by_task = {
            str(item.get("task_code", "") or "").strip()
            for item in reasoning_items
            if str(item.get("task_code", "") or "").strip()
        }
        for verdict in task_verdicts:
            task_code = str(verdict.get("task_code", "") or "").strip()
            if task_code and task_code not in reasoning_by_task:
                issues.append(
                    self._build_issue(
                        scope="section",
                        issue_type="reasoning_gap",
                        severity="medium",
                        detail=f"任务 {task_code} 存在 task_verdict，但缺少对应 reasoning_chain_item。",
                        suggestion="保证每个 task_verdict 都有同 task_code 的 reasoning_chain_item。",
                    )
                )

        for item in reasoning_items:
            task_code = str(item.get("task_code", "") or "").strip() or "[unknown]"
            rule_requirement = str(item.get("rule_requirement", "") or "").strip()
            material_fact = str(item.get("material_fact", "") or "").strip()
            comparison = str(item.get("comparison", "") or "").strip()
            judgment_reason = str(item.get("judgment_reason", "") or "").strip()
            if not rule_requirement or not comparison or not judgment_reason:
                issues.append(
                    self._build_issue(
                        scope="section",
                        issue_type="reasoning_gap",
                        severity="medium",
                        detail=f"任务 {task_code} 的 reasoning_chain_item 缺少关键字段。",
                        suggestion="至少补齐 rule_requirement、comparison 和 judgment_reason；如可能，补充 material_fact。",
                    )
                )
            if not material_fact:
                issues.append(
                    self._build_issue(
                        scope="section",
                        issue_type="reasoning_gap",
                        severity="low",
                        detail=f"任务 {task_code} 的 reasoning_chain_item 未明确章节原文事实。",
                        suggestion="补充 material_fact，说明原文中支持或不支持结论的事实。",
                    )
                )
        return issues

    def _validate_problem_basis_advice(self, section_result: Dict[str, Any]) -> List[Dict[str, str]]:
        issues: List[Dict[str, str]] = []
        conclusion = str(section_result.get("conclusion", section_result.get("pre_review_conclusion", "")) or "").strip().lower()
        items = [item for item in self._safe_list(section_result.get("problem_basis_advice_items", [])) if isinstance(item, dict)]
        statuses = {str(item.get("status", "") or "").strip().lower() for item in items if str(item.get("status", "") or "").strip()}

        if conclusion in {"unsupported", "insufficient_information"} and not items:
            issues.append(
                self._build_issue(
                    scope="section",
                    issue_type="display_gap",
                    severity="high",
                    detail="章节为负向结论，但缺少 problem_basis_advice_items，前端无法稳定展示问题-依据-建议。",
                    suggestion="补齐 problem_basis_advice_items，并至少包含 issue、basis、advice。",
                )
            )
        if conclusion == "supported" and {"issue", "question"} & statuses:
            issues.append(
                self._build_issue(
                    scope="section",
                    issue_type="conclusion_conflict",
                    severity="high",
                    detail="章节结论为 supported，但展示条目中仍存在 issue/question，前后自相矛盾。",
                    suggestion="检查 reviewer 的结论收敛逻辑，避免 supported 与 issue/question 共存。",
                )
            )
        if conclusion in {"unsupported", "insufficient_information"} and statuses and statuses <= {"supported"}:
            issues.append(
                self._build_issue(
                    scope="section",
                    issue_type="conclusion_conflict",
                    severity="high",
                    detail="章节结论为负向，但展示条目只包含 supported，无法支撑负向结论。",
                    suggestion="将负向结论对应的问题项显式写入 problem_basis_advice_items。",
                )
            )
        for item in items:
            status = str(item.get("status", "") or "").strip().lower() or "issue"
            problem = str(item.get("problem", "") or item.get("issue", "") or "").strip()
            basis = str(item.get("basis", "") or "").strip()
            advice = str(item.get("advice", "") or "").strip()
            if status in {"issue", "question"} and (not problem or not basis or not advice):
                issues.append(
                    self._build_issue(
                        scope="section",
                        issue_type="display_gap",
                        severity="medium",
                        detail="存在不完整的 problem_basis_advice_item，缺少 problem、basis 或 advice。",
                        suggestion="负向展示条目必须完整包含 problem、basis 和 advice。",
                    )
                )
        return issues

    def _deterministic_review_section(self, section_result: Dict[str, Any]) -> Dict[str, Any]:
        issues: List[Dict[str, str]] = []
        issues.extend(self._validate_task_verdicts(section_result))
        issues.extend(self._validate_reasoning_chain(section_result))
        issues.extend(self._validate_problem_basis_advice(section_result))
        normalized = self._dedupe_issues(issues)
        return {"qa_status": "fail" if normalized else "pass", "qa_issues": normalized}

    def _deterministic_review_run(self, run_payload: Dict[str, Any]) -> Dict[str, Any]:
        issues: List[Dict[str, str]] = []
        sections = [item for item in self._safe_list(run_payload.get("sections", [])) if isinstance(item, dict)]
        consistency_result = self._safe_dict(run_payload.get("consistency_result", {}))
        qa_checks = [item for item in self._safe_list(run_payload.get("qa_checks", [])) if isinstance(item, dict)]
        if not sections:
            issues.append(
                self._build_issue(
                    scope="run",
                    issue_type="missing_field",
                    severity="high",
                    detail="运行级 QA 输入缺少 sections，无法执行汇总质控。",
                    suggestion="在 run-level QA 中传入章节结果列表。",
                )
            )
        if self._safe_list(consistency_result.get("issues", [])):
            issues.append(
                self._build_issue(
                    scope="run",
                    issue_type="consistency_gap",
                    severity="high",
                    detail="一致性检查已发现跨章节问题，但运行级质控尚未闭环。",
                    suggestion="优先处理 consistency_result 中的跨章节冲突，再生成 lead summary。",
                )
            )
        failed_sections = sum(1 for item in qa_checks if str(item.get("qa_status", "") or "").strip().lower() == "fail")
        if failed_sections > 0:
            issues.append(
                self._build_issue(
                    scope="run",
                    issue_type="consistency_gap",
                    severity="high",
                    detail=f"共有 {failed_sections} 个章节未通过 section-level QA。",
                    suggestion="先修复章节级问题，再执行运行级总评。",
                )
            )
        normalized = self._dedupe_issues(issues)
        return {"qa_status": "fail" if normalized else "pass", "qa_issues": normalized}

    def _llm_review(self, payload: Dict[str, Any], prompt_config: Dict[str, Any] | None = None) -> Dict[str, Any]:
        try:
            prompt = self.prompts.render("qa_checker.j2", {"section_result": payload}, prompt_config=prompt_config or {})
        except Exception:
            raise LLMExecutionError("model_configuration_invalid", stage="quality_assurance") from None
        raw = self.llm.chat(
            messages=[
                {"role": "system", "content": "你是审评质控代理。只输出合法 JSON。"},
                {"role": "user", "content": prompt},
            ],
            default='{"qa_status": "pass", "qa_issues": []}',
        )
        data = self.llm.extract_json(raw)
        if data is None:
            raise LLMExecutionError("model_invalid_json", stage="quality_assurance")
        if (not isinstance(data, dict) or not isinstance(data.get("qa_status"), str)
                or data["qa_status"] not in {"pass", "fail"}
                or not isinstance(data.get("qa_issues"), list)
                or any(not isinstance(item, dict) or not isinstance(item.get("detail"), str)
                       or not item["detail"].strip() for item in data["qa_issues"])
                or (data["qa_status"] == "fail" and not data["qa_issues"])):
            raise LLMExecutionError("model_invalid_output", stage="quality_assurance")
        return {
            "qa_status": str(data.get("qa_status", "pass") or "pass").strip().lower(),
            "qa_issues": self._dedupe_issues(data.get("qa_issues", [])),
        }

    def describe(self) -> Dict[str, Any]:
        return {
            "name": "qa_reviewer",
            "description": "Validate evidence binding, unsupported conclusions, and major omissions.",
            "inputs": ["section_result", "consistency_result"],
            "outputs": ["qa_status", "qa_issues"],
        }

    def review_section(self, section_result: Dict[str, Any], prompt_config: Dict[str, Any] | None = None) -> Dict[str, Any]:
        llm_result = self._llm_review(section_result, prompt_config=prompt_config)
        deterministic = self._deterministic_review_section(section_result)
        issues = self._dedupe_issues(llm_result.get("qa_issues", []) + deterministic.get("qa_issues", []))
        return {"qa_status": "fail" if issues else "pass", "qa_issues": issues}

    def review_run(self, run_payload: Dict[str, Any], prompt_config: Dict[str, Any] | None = None) -> Dict[str, Any]:
        llm_result = self._llm_review(run_payload, prompt_config=prompt_config)
        deterministic = self._deterministic_review_run(run_payload)
        issues = self._dedupe_issues(llm_result.get("qa_issues", []) + deterministic.get("qa_issues", []))
        return {"qa_status": "fail" if issues else "pass", "qa_issues": issues}
