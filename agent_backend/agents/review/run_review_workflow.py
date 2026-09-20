from __future__ import annotations

from datetime import datetime
from copy import deepcopy
from typing import Any, Dict, List, TypedDict

from agent.agent_backend.agents.review.consistency_agent import ConsistencyAgent
from agent.agent_backend.agents.review.lead_reviewer_agent import LeadReviewerAgent
from agent.agent_backend.agents.review.qa_agent import QAAgent
from agent.agent_backend.llm.errors import LLMContextError, LLMExecutionError

try:
    from langgraph.graph import END, StateGraph

    HAS_LANGGRAPH = True
except Exception:  # pragma: no cover
    HAS_LANGGRAPH = False
    END = "__end__"
    StateGraph = None


class RunReviewState(TypedDict, total=False):
    project_meta: Dict[str, Any]
    section_results: List[Dict[str, Any]]
    prompt_config: Dict[str, Any]
    consistency_result: Dict[str, Any]
    qa_checks: List[Dict[str, Any]]
    qa_section_results: List[Dict[str, Any]]
    run_qa_result: Dict[str, Any]
    lead_result: Dict[str, Any]
    trace: List[Dict[str, Any]]


class RunReviewWorkflow:
    """Explicit run-level workflow for consistency, QA, and final aggregation."""

    def __init__(
        self,
        consistency_agent: ConsistencyAgent | None = None,
        qa_agent: QAAgent | None = None,
        lead_reviewer_agent: LeadReviewerAgent | None = None,
    ) -> None:
        self.consistency_agent = consistency_agent or ConsistencyAgent()
        self.qa_agent = qa_agent or QAAgent()
        self.lead_reviewer_agent = lead_reviewer_agent or LeadReviewerAgent()
        self.graph = self._build_graph() if HAS_LANGGRAPH else None

    @staticmethod
    def _append_trace(state: RunReviewState, node: str, payload: Dict[str, Any]) -> None:
        trace = state.setdefault("trace", [])
        trace.append(
            {
                "node": node,
                "ts": datetime.utcnow().isoformat(),
                "payload": payload,
            }
        )

    @staticmethod
    def _build_qa_section_results(section_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for item in section_results or []:
            conclusion_row = item.get("conclusion_row")
            out.append(
                {
                    "section_id": item.get("section_id", ""),
                    "section_name": item.get("section_meta", {}).get("section_name", "") if isinstance(item.get("section_meta", {}), dict) else "",
                    "section_summary": item.get("section_summary", {}),
                    "conclusion": conclusion_row.conclusion if conclusion_row is not None else "",
                    "risk_level": item.get("risk_level", "low"),
                    "highlighted_issues": item.get("findings", []),
                    "linked_rules": item.get("linked_rules", []),
                    "problem_basis_advice_items": item.get("problem_basis_advice_items", [])
                    if isinstance(item.get("problem_basis_advice_items", []), list)
                    else (
                        item.get("review_result", {}).get("problem_basis_advice_items", [])
                        if isinstance(item.get("review_result", {}), dict)
                        else []
                    ),
                    "task_verdicts": item.get("task_verdicts", [])
                    if isinstance(item.get("task_verdicts", []), list)
                    else (
                        item.get("review_result", {}).get("task_verdicts", [])
                        if isinstance(item.get("review_result", {}), dict)
                        else []
                    ),
                    "reasoning_chain_items": item.get("reasoning_chain_items", [])
                    if isinstance(item.get("reasoning_chain_items", []), list)
                    else (
                        item.get("review_result", {}).get("reasoning_chain_items", [])
                        if isinstance(item.get("review_result", {}), dict)
                        else []
                    ),
                    "medical_key_entities": item.get("medical_key_entities", [])
                    if isinstance(item.get("medical_key_entities", []), list)
                    else (
                        item.get("review_result", {}).get("medical_key_entities", [])
                        if isinstance(item.get("review_result", {}), dict)
                        else []
                    ),
                    "medical_key_data_points": item.get("medical_key_data_points", [])
                    if isinstance(item.get("medical_key_data_points", []), list)
                    else (
                        item.get("review_result", {}).get("medical_key_data_points", [])
                        if isinstance(item.get("review_result", {}), dict)
                        else []
                    ),
                }
            )
        return out

    def _consistency_node(self, state: RunReviewState) -> RunReviewState:
        concise = []
        for item in state.get("section_results", []) or []:
            concise.append(
                {
                    "section_id": item.get("section_id", ""),
                    "section_name": item.get("section_meta", {}).get("section_name", "") if isinstance(item.get("section_meta", {}), dict) else "",
                    "summary": item.get("section_summary", {}),
                    "conclusion": item.get("conclusion_row").conclusion if item.get("conclusion_row") is not None else "",
                    "risk_level": item.get("risk_level", "low"),
                    "highlighted_issues": item.get("findings", []),
                }
            )
        result = self.consistency_agent.check(concise, prompt_config=state.get("prompt_config", {}) or {})
        state["consistency_result"] = result
        self._append_trace(state, "consistency", {"issue_count": len(result.get("issues", []) or [])})
        return state

    def _qa_sections_node(self, state: RunReviewState) -> RunReviewState:
        qa_section_results = self._build_qa_section_results(state.get("section_results", []) or [])
        state["qa_section_results"] = qa_section_results
        qa_checks = state["qa_checks"] = []
        for item in qa_section_results:
            qa_checks.append(self.qa_agent.review_section(item, prompt_config=state.get("prompt_config", {}) or {}))
        self._append_trace(state, "qa_sections", {"section_count": len(qa_section_results), "check_count": len(qa_checks)})
        return state

    def _qa_run_node(self, state: RunReviewState) -> RunReviewState:
        payload = {
            "sections": state.get("qa_section_results", []) or [],
            "consistency_result": state.get("consistency_result", {}) or {},
            "qa_checks": state.get("qa_checks", []) or [],
        }
        result = self.qa_agent.review_run(payload, prompt_config=state.get("prompt_config", {}) or {})
        state["run_qa_result"] = result
        self._append_trace(state, "qa_run", {"qa_status": result.get("qa_status", "pass")})
        return state

    def _lead_node(self, state: RunReviewState) -> RunReviewState:
        result = self.lead_reviewer_agent.summarize(
            project_meta=state.get("project_meta", {}) or {},
            section_results=state.get("qa_section_results", []) or [],
            consistency_result=state.get("consistency_result", {}) or {},
            qa_result=state.get("run_qa_result", {}) or {},
            prompt_config=state.get("prompt_config", {}) or {},
        )
        state["lead_result"] = result
        self._append_trace(state, "lead", {"key_question_count": len(result.get("key_questions", []) or [])})
        return state

    def _execute_node(self, name, node, state):
        try:
            return node(state)
        except (LLMContextError, LLMExecutionError) as exc:
            # 只保存已产生的业务结果，不序列化ORM对象、提示词或原始异常。
            exc.partial_result = deepcopy({key: state[key] for key in
                ('consistency_result', 'qa_checks', 'run_qa_result', 'lead_result') if key in state})
            exc.completed_stages = [entry['node'] for entry in state.get('trace', [])]
            stages = ['consistency', 'qa_sections', 'qa_run', 'lead']
            exc.incomplete_stages = stages[stages.index(name):]
            if exc.stage in ('chat', 'parse'):
                exc.stage = 'run_review.' + name
            raise

    def _build_graph(self):
        graph = StateGraph(RunReviewState)
        for name, node in [('consistency', self._consistency_node), ('qa_sections', self._qa_sections_node),
                           ('qa_run', self._qa_run_node), ('lead', self._lead_node)]:
            graph.add_node(name, lambda state, name=name, node=node: self._execute_node(name, node, state))
        graph.set_entry_point("consistency")
        graph.add_edge("consistency", "qa_sections")
        graph.add_edge("qa_sections", "qa_run")
        graph.add_edge("qa_run", "lead")
        graph.add_edge("lead", END)
        return graph.compile()

    def run(
        self,
        project_meta: Dict[str, Any],
        section_results: List[Dict[str, Any]],
        prompt_config: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        state: RunReviewState = {
            "project_meta": project_meta or {},
            "section_results": section_results or [],
            "prompt_config": prompt_config or {},
            "trace": [],
        }
        if self.graph is not None:
            state = self.graph.invoke(state)
        else:
            for name, node in [('consistency', self._consistency_node), ('qa_sections', self._qa_sections_node),
                               ('qa_run', self._qa_run_node), ('lead', self._lead_node)]:
                state = self._execute_node(name, node, state)
        return {
            "consistency_result": state.get("consistency_result", {}) or {},
            "qa_checks": state.get("qa_checks", []) or [],
            "run_qa_result": state.get("run_qa_result", {}) or {},
            "lead_result": state.get("lead_result", {}) or {},
            "trace": state.get("trace", []) or [],
        }
