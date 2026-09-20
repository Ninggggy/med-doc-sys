from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, TypedDict

from agent.agent_backend.agents.multi_agent.prompt_manager import PromptManager
from agent.agent_backend.agents.multi_agent.roles import AgentRole, build_default_roles
from agent.agent_backend.common_tools.builtin.memory_tool import MemoryTool
from agent.agent_backend.llm.client import LLMClient

try:
    from langgraph.graph import END, StateGraph

    HAS_LANGGRAPH = True
except Exception:  # pragma: no cover
    HAS_LANGGRAPH = False
    END = "__end__"
    StateGraph = None


class WorkflowState(TypedDict, total=False):
    doc_id: str
    section_id: str
    content: str
    section_meta: Dict[str, Any]
    retrieval_context: Dict[str, Any]
    memory_context: str
    memory_package: Dict[str, Any]
    related_rules: List[Dict[str, Any]]
    coordination_payload: Dict[str, Any]
    plan_steps: List[str]
    rule_evidence: List[str]
    shared_context: Dict[str, Any]
    run_config: Dict[str, Any]
    prompt_config: Dict[str, Any]
    findings_draft: List[Dict[str, Any]]
    findings_refined: List[Dict[str, Any]]
    findings: List[Dict[str, Any]]
    score: float
    conclusion: str
    trace: Dict[str, Any]


class MultiAgentPreReviewWorkflow:
    RISK_KEYWORDS: Dict[str, List[str]] = {
        "contraindication": ["contraindication", "禁忌", "慎用", "不适用", "不得使用"],
        "adverse_reaction": ["adverse reaction", "不良反应", "安全性", "风险", "毒性"],
        "dosage": ["dosage", "dose", "剂量", "用法", "给药", "频次"],
        "warning": ["warning", "precaution", "注意事项", "警示", "风险控制"],
        "interaction": ["drug interaction", "相互作用", "联用", "配伍"],
    }

    def __init__(self, memory_tool: MemoryTool | None = None, roles: List[AgentRole] | None = None):
        self.memory_tool = memory_tool or MemoryTool()
        self.roles = roles or build_default_roles()
        self.prompts = PromptManager()
        self.llm = LLMClient()
        self.graph = self._build_graph() if HAS_LANGGRAPH else None

    def describe_roles(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": r.name,
                "description": r.description,
                "capabilities": r.capabilities,
                "collaboration_contract": r.collaboration_contract,
                "inputs": r.inputs,
                "outputs": r.outputs,
            }
            for r in self.roles
        ]

    @staticmethod
    def _uniq(items: List[str]) -> List[str]:
        seen = set()
        out: List[str] = []
        for item in items:
            normalized = str(item or "").strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            out.append(normalized)
        return out

    @staticmethod
    def _contains_any(source: str, words: List[str]) -> bool:
        low = (source or "").lower()
        return any(w.lower() in low for w in words)

    @staticmethod
    def _append_trace(state: WorkflowState, agent: str, payload: Dict[str, Any]) -> None:
        trace = state.setdefault("trace", {})
        steps = trace.setdefault("agent_steps", [])
        steps.append(
            {
                "agent": agent,
                "ts": datetime.utcnow().isoformat(),
                "payload": payload,
            }
        )

    def _safe_prompt(self, template_name: str, context: Dict[str, Any], state: WorkflowState) -> str:
        try:
            return self.prompts.render(template_name, context, prompt_config=state.get("prompt_config", {}) or {})
        except Exception as e:
            return f"[prompt_render_error] {template_name}: {str(e)}"

    @staticmethod
    def _normalize_finding(item: Any) -> Dict[str, Any]:
        if isinstance(item, dict):
            title = str(item.get("title", "") or "").strip()
            return {
                "title": title,
                "problem_type": str(item.get("problem_type", "") or "").strip(),
                "severity": str(item.get("severity", "low") or "low").strip().lower(),
                "evidence": str(item.get("evidence", "") or "").strip(),
                "recommendation": str(item.get("recommendation", "") or "").strip(),
                "metadata": item.get("metadata", {}) if isinstance(item.get("metadata", {}), dict) else {},
            }
        title = str(item or "").strip()
        return {
            "title": title,
            "problem_type": "",
            "severity": "low",
            "evidence": "",
            "recommendation": "",
            "metadata": {},
        }

    def _normalize_findings(self, items: List[Any]) -> List[Dict[str, Any]]:
        seen = set()
        normalized: List[Dict[str, Any]] = []
        for item in items or []:
            finding = self._normalize_finding(item)
            title = finding.get("title", "").strip()
            if not title:
                continue
            key = (
                title,
                finding.get("problem_type", ""),
                finding.get("severity", "low"),
                finding.get("evidence", ""),
                finding.get("recommendation", ""),
                json.dumps(finding.get("metadata", {}), ensure_ascii=False, sort_keys=True),
            )
            if key in seen:
                continue
            seen.add(key)
            normalized.append(finding)
        return normalized

    def _llm_json(self, messages: List[Dict[str, str]], default: Dict[str, Any]) -> Dict[str, Any]:
        fallback = json.dumps(default, ensure_ascii=False)
        raw = self.llm.chat(messages=messages, default=fallback)
        data = self.llm.extract_json(raw)
        return data if isinstance(data, dict) else default

    def _rule_evidence_from_related(self, related_rules: List[Dict[str, Any]]) -> List[str]:
        evidence: List[str] = []
        for item in related_rules:
            doc_id = str(item.get("doc_id", "")).strip()
            cls = str(item.get("classification", "")).strip()
            score = item.get("score", None)
            content = str(item.get("content", "")).strip()
            if not (doc_id or content):
                continue
            score_part = ""
            if score is not None:
                try:
                    score_part = f"(score={float(score):.3f})"
                except Exception:
                    score_part = ""
            head = "/".join([x for x in [doc_id, cls] if x]) or "rule"
            evidence.append(f"{head}{score_part}: {content[:280]}")
        return self._uniq(evidence)[:12]

    def _build_shared_context(self, state: WorkflowState) -> Dict[str, Any]:
        mem_pkg = state.get("memory_package", {}) or {}
        retrieval_context = state.get("retrieval_context", {}) or {}
        retrieval_hits = retrieval_context.get("hits", []) if isinstance(retrieval_context, dict) else []
        grouped_docs = retrieval_context.get("grouped_docs", []) if isinstance(retrieval_context, dict) else []
        return {
            "section_id": state.get("section_id", ""),
            "section_meta": state.get("section_meta", {}) or {},
            "plan_steps": state.get("plan_steps", []),
            "memory_context": state.get("memory_context", "")[:1200],
            "memory_highlights": (mem_pkg.get("hits_by_type", {}) if isinstance(mem_pkg, dict) else {}),
            "rule_evidence": state.get("rule_evidence", []),
            "retrieval_hit_count": len(retrieval_hits),
            "retrieval_grouped_docs": grouped_docs[:3],
            "coordination_payload": state.get("coordination_payload", {}) or {},
            "prompt_config": state.get("prompt_config", {}) or {},
        }

    def _plan_node(self, state: WorkflowState) -> WorkflowState:
        text = state.get("content", "")[:3000]
        prompt = self._safe_prompt(
            "planner.j2",
            {
                "section_id": state.get("section_id", ""),
                "section_meta": state.get("section_meta", {}),
                "content": text,
                "memory_context": state.get("memory_context", "")[:1200],
            },
            state,
        )
        default_steps: List[str] = [
            "确认本章节的审评目标、资料边界和章节类型",
            "抽取关键风险点、关键参数、缺失项和上下文依赖",
            "结合检索证据和历史反馈列出核查步骤",
            "输出带证据的问题清单、结论和风险等级",
        ]
        resp = self._llm_json(
            messages=[
                {"role": "system", "content": "你是预审规划代理。只输出合法 JSON。"},
                {
                    "role": "user",
                    "content": (
                        f"{prompt}\n\n"
                        "输出 JSON 结构: "
                        '{"plan_steps": ["..."], "risk_focus": ["..."], "section_type": "...", "notes": "..."}'
                    ),
                },
            ],
            default={"plan_steps": default_steps, "risk_focus": []},
        )
        steps = self._uniq([str(x) for x in resp.get("plan_steps", [])]) or default_steps
        state["plan_steps"] = steps
        self._append_trace(state, "planner", {"plan_steps": steps, "prompt": prompt, "llm": resp})
        return state

    def _retrieve_node(self, state: WorkflowState) -> WorkflowState:
        evidence = self._rule_evidence_from_related(state.get("related_rules", []))
        prompt = self._safe_prompt(
            "retriever.j2",
            {"plan_steps": state.get("plan_steps", []), "related_rules": evidence},
            state,
        )
        resp = self._llm_json(
            messages=[
                {"role": "system", "content": "你是证据筛选代理。只输出合法 JSON。"},
                {
                    "role": "user",
                    "content": f"{prompt}\n\n输出 JSON 结构: {{\"rule_evidence\": [\"...\"]}}",
                },
            ],
            default={"rule_evidence": evidence[:8]},
        )
        ranked = self._uniq([str(x) for x in resp.get("rule_evidence", [])])
        state["rule_evidence"] = ranked[:10] if ranked else evidence[:10]
        self._append_trace(state, "retriever", {"rule_evidence": state["rule_evidence"], "prompt": prompt, "llm": resp})
        return state

    def _context_node(self, state: WorkflowState) -> WorkflowState:
        shared = self._build_shared_context(state)
        state["shared_context"] = shared
        preview = {
            "section_meta": shared.get("section_meta", {}),
            "plan_steps": shared.get("plan_steps", [])[:4],
            "rule_evidence_count": len(shared.get("rule_evidence", [])),
            "retrieval_hit_count": shared.get("retrieval_hit_count", 0),
            "memory_types": list((shared.get("memory_highlights", {}) or {}).keys()),
            "memory_context_preview": str(shared.get("memory_context", ""))[:300],
            "coordination_keys": list((shared.get("coordination_payload", {}) or {}).keys()),
            "prompt_version_id": str((shared.get("prompt_config", {}) or {}).get("prompt_version_id", "") or ""),
        }
        self._append_trace(state, "context_builder", {"shared_context_preview": preview})
        return state

    def _review_node(self, state: WorkflowState) -> WorkflowState:
        text = state.get("content", "")[:3200]
        shared = state.get("shared_context", {}) or {}
        mem = str(shared.get("memory_context", state.get("memory_context", "")))[:1500]
        rule_evidence = shared.get("rule_evidence", state.get("rule_evidence", []))
        retrieval_grouped_docs = shared.get("retrieval_grouped_docs", [])
        prompt = self._safe_prompt(
            "reviewer.j2",
            {
                "content": text,
                "memory_context": mem,
                "rule_evidence": rule_evidence,
                "retrieval_grouped_docs": retrieval_grouped_docs,
                "section_meta": shared.get("section_meta", {}),
            },
            state,
        )
        resp = self._llm_json(
            messages=[
                {"role": "system", "content": "你是章节审评代理。只输出合法 JSON。"},
                {
                    "role": "user",
                    "content": (
                        f"{prompt}\n\n"
                        "输出 JSON 结构: "
                        '{"findings_draft": [{"title": "...", "problem_type": "...", "severity": "high|medium|low", '
                        '"evidence": "...", "recommendation": "..."}], "conclusion": "...", "score": 0}'
                    ),
                },
            ],
            default={"findings_draft": []},
        )

        findings = self._normalize_findings(resp.get("findings_draft", []))
        if not findings:
            if self._contains_any(text, self.RISK_KEYWORDS["contraindication"]):
                findings.append(self._normalize_finding({
                    "title": "发现禁忌或限制性用药信息，需要重点核查适应证和风险控制表述",
                    "problem_type": "禁忌与限制性信息核查",
                    "severity": "high",
                }))
            if self._contains_any(text, self.RISK_KEYWORDS["adverse_reaction"]):
                findings.append(self._normalize_finding({
                    "title": "不良反应或安全性描述可能不完整，需要补充证据和风险说明",
                    "problem_type": "安全性信息不足",
                    "severity": "medium",
                    "recommendation": "补充安全性依据，核对说明书、不良反应监测和风险控制内容",
                }))
            if self._contains_any(text, self.RISK_KEYWORDS["dosage"]):
                findings.append(self._normalize_finding({
                    "title": "用法用量或给药方案信息可能不一致，需要核对关键参数",
                    "problem_type": "给药方案不一致",
                    "severity": "medium",
                    "recommendation": "核对剂量、给药频次、人群差异和关键试验依据",
                }))
        state["findings_draft"] = self._normalize_findings(findings)
        self._append_trace(
            state,
            "reviewer",
            {
                "findings_draft": state["findings_draft"],
                "retrieval_hit_count": shared.get("retrieval_hit_count", 0),
                "prompt": prompt,
                "llm": resp,
            },
        )
        return state

    def _reflect_node(self, state: WorkflowState) -> WorkflowState:
        prompt = self._safe_prompt(
            "reflector.j2",
            {
                "findings_draft": state.get("findings_draft", []),
                "rule_evidence": state.get("rule_evidence", []),
                "plan_steps": state.get("plan_steps", []),
            },
            state,
        )
        resp = self._llm_json(
            messages=[
                {"role": "system", "content": "你是审评反思代理。只输出合法 JSON。"},
                {
                    "role": "user",
                    "content": (
                        f"{prompt}\n\n"
                        "输出 JSON 结构: "
                        '{"findings_refined": [{"title": "...", "problem_type": "...", "severity": "high|medium|low", '
                        '"evidence": "...", "recommendation": "..."}], "notes": ["..."]}'
                    ),
                },
            ],
            default={"findings_refined": state.get("findings_draft", []), "notes": []},
        )
        refined = self._normalize_findings(resp.get("findings_refined", []))
        if not refined:
            refined = self._normalize_findings(state.get("findings_draft", []))
        state["findings_refined"] = refined
        self._append_trace(state, "reflector", {"findings_refined": refined, "prompt": prompt, "llm": resp})
        return state

    def _solve_node(self, state: WorkflowState) -> WorkflowState:
        findings = self._normalize_findings(state.get("findings_refined", []))
        evidence_count = len(state.get("rule_evidence", []))
        base_score = min(len(findings) / 8.0, 1.0)
        evidence_bonus = min(evidence_count * 0.03, 0.15)
        score = min(base_score + evidence_bonus, 1.0)

        prompt = self._safe_prompt("synthesizer.j2", {"findings": findings, "score": score}, state)
        default_conclusion = "；".join([item.get("title", "") for item in findings]) if findings else "当前章节未发现明确缺陷，但仍需结合全卷和上下文进一步核对"
        resp = self._llm_json(
            messages=[
                {"role": "system", "content": "你是结论综合代理。只输出合法 JSON。"},
                {
                    "role": "user",
                    "content": f"{prompt}\n\n输出 JSON 结构: {{\"conclusion\": \"...\", \"score\": 0~1}}",
                },
            ],
            default={"conclusion": default_conclusion, "score": score},
        )

        final_conclusion = str(resp.get("conclusion", "")).strip() or default_conclusion
        try:
            score = float(resp.get("score", score))
        except Exception:
            pass
        score = max(0.0, min(1.0, score))

        state["findings"] = findings
        state["score"] = score
        state["conclusion"] = final_conclusion

        self._append_trace(
            state,
            "synthesizer",
            {"findings": findings, "score": score, "conclusion": final_conclusion, "prompt": prompt, "llm": resp},
        )
        trace = state.setdefault("trace", {})
        trace["workflow_version"] = str((state.get("prompt_config", {}) or {}).get("prompt_version_id", "v5_prompt_bundle"))
        trace["summary"] = {
            "plan_steps": state.get("plan_steps", []),
            "rule_evidence_count": len(state.get("rule_evidence", [])),
            "findings_count": len(findings),
            "score": score,
            "prompt_bundle_path": str((state.get("prompt_config", {}) or {}).get("prompt_bundle_path", "") or ""),
        }
        return state

    def _build_graph(self):
        graph = StateGraph(WorkflowState)
        graph.add_node("plan", self._plan_node)
        graph.add_node("retrieve", self._retrieve_node)
        graph.add_node("context", self._context_node)
        graph.add_node("review", self._review_node)
        graph.add_node("reflect", self._reflect_node)
        graph.add_node("solve", self._solve_node)
        graph.set_entry_point("plan")
        graph.add_edge("plan", "retrieve")
        graph.add_edge("retrieve", "context")
        graph.add_edge("context", "review")
        graph.add_edge("review", "reflect")
        graph.add_edge("reflect", "solve")
        graph.add_edge("solve", END)
        return graph.compile()

    def run(self, state: WorkflowState) -> WorkflowState:
        state.setdefault("trace", {})
        state["trace"]["started_at"] = datetime.utcnow().isoformat()
        if self.graph is not None:
            out = self.graph.invoke(state)
            out.setdefault("trace", {})["finished_at"] = datetime.utcnow().isoformat()
            return out
        s = self._plan_node(state)
        s = self._retrieve_node(s)
        s = self._context_node(s)
        s = self._review_node(s)
        s = self._reflect_node(s)
        s = self._solve_node(s)
        s.setdefault("trace", {})["finished_at"] = datetime.utcnow().isoformat()
        return s
