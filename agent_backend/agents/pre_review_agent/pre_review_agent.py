from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Optional

from agent.agent_backend.agents.multi_agent import MultiAgentPreReviewWorkflow
from agent.agent_backend.common_tools.builtin.memory_tool import MemoryTool
from agent.agent_backend.agents.pre_review_agent.pre_review_state import SectionReviewPacket


class PreReviewAgent:
    """
    Multi-agent orchestrator for pre-review.
    Workflow paradigm: plan-and-solve + reflection.
    """

    def __init__(self, memory_tool: Optional[MemoryTool] = None):
        self.memory_tool = memory_tool or MemoryTool()
        self.workflow = MultiAgentPreReviewWorkflow(memory_tool=self.memory_tool)

    def describe_roles(self) -> List[Dict[str, Any]]:
        return self.workflow.describe_roles()

    @staticmethod
    def _group_hits_by_type(hits: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for h in hits or []:
            if not isinstance(h, dict):
                continue
            t = str(h.get("memory_type", "unknown"))
            grouped[t].append(
                {
                    "key": h.get("key", ""),
                    "score": float(h.get("score", 0.0)),
                    "semantic_score": float(h.get("semantic_score", 0.0)),
                    "recency_score": float(h.get("recency_score", 0.0)),
                    "metadata": h.get("metadata", {}) or {},
                    "value_preview": str(h.get("value", ""))[:220],
                }
            )
        for t in grouped:
            grouped[t].sort(key=lambda x: x["score"], reverse=True)
            grouped[t] = grouped[t][:5]
        return dict(grouped)

    def run(
        self,
        doc_id: str = "",
        section_id: str = "",
        content: str = "",
        related_rules: Optional[List[Dict[str, Any]]] = None,
        coordination_payload: Optional[Dict[str, Any]] = None,
        memory_metadata_filter: Optional[Dict[str, Any]] = None,
        packet: Optional[SectionReviewPacket] = None,
    ) -> Dict[str, Any]:
        if packet is not None:
            doc_id = packet.doc_id
            section_id = packet.section_id
            content = packet.section_text
            related_rules = packet.related_rules
            coordination_payload = packet.coordination_payload
            memory_metadata_filter = packet.memory_metadata_filter

        mem_ctx = self.memory_tool.context(
            query=(content or "")[:300],
            top_k=8,
            memory_types=["episodic", "semantic", "working"],
            max_chars=1200,
            metadata_filter=memory_metadata_filter,
        )
        memory_hits = mem_ctx.get("hits", []) if isinstance(mem_ctx, dict) else []
        memory_structured = mem_ctx.get("structured", {}) if isinstance(mem_ctx, dict) else {}
        memory_package = {
            "query": (content or "")[:300],
            "context_text": mem_ctx.get("context", "") if isinstance(mem_ctx, dict) else "",
            "structured": memory_structured,
            "hits_by_type": self._group_hits_by_type(memory_hits),
            "hit_count": len(memory_hits) if isinstance(memory_hits, list) else 0,
        }

        state = {
            "doc_id": doc_id,
            "section_id": section_id,
            "content": content or "",
            "section_meta": (
                {
                    "project_id": packet.project_id,
                    "run_id": packet.run_id,
                    "section_code": packet.section_code,
                    "section_title": packet.section_title,
                    "title_path": list(packet.title_path),
                    "page_start": packet.page_start,
                    "page_end": packet.page_end,
                    "unit_type": packet.unit_type,
                }
                if packet is not None
                else {}
            ),
            "retrieval_context": packet.retrieval_context if packet is not None else {},
            "memory_context": memory_package.get("context_text", ""),
            "memory_package": memory_package,
            "related_rules": related_rules or [],
            "coordination_payload": coordination_payload or {},
            "run_config": dict(packet.run_config) if packet is not None else {},
            "prompt_config": dict((packet.run_config or {}).get("prompt_config", {}) or {}) if packet is not None else {},
            "trace": {
                "memory_package": memory_package,
                "coordination_payload": coordination_payload or {},
                "run_config": dict(packet.run_config) if packet is not None else {},
            },
        }
        out = self.workflow.run(state)
        trace = out.get("trace", {}) or {}
        return {
            "doc_id": out.get("doc_id", doc_id),
            "section_id": out.get("section_id", section_id),
            "findings": out.get("findings", []),
            "score": float(out.get("score", 0.0)),
            "conclusion": out.get("conclusion", ""),
            "retrieval_context": packet.retrieval_context if packet is not None else {},
            "memory_hits": memory_hits,
            "memory_context": memory_package.get("context_text", ""),
            "memory_package": memory_package,
            "strategy": str((packet.run_config or {}).get("strategy", "multi_agent_plan_and_solve+reflection")) if packet is not None else "multi_agent_plan_and_solve+reflection",
            "agent_roles": [r.get("name") for r in self.describe_roles()],
            "trace": trace,
        }


def build_pre_review_agent(memory_tool: Optional[MemoryTool] = None):
    return PreReviewAgent(memory_tool=memory_tool)
