from __future__ import annotations

from typing import Any, Dict, List

from agent.agent_backend.llm.client import LLMClient
from agent.agent_backend.llm.errors import LLMExecutionError
from agent.agent_backend.prompts.template_manager import PromptTemplateManager


class ConsistencyAgent:
    """Check cross-section consistency after section-level review."""

    def __init__(self) -> None:
        self.llm = LLMClient()
        self.prompts = PromptTemplateManager("pre_review_agent_prompt")

    def describe(self) -> Dict[str, Any]:
        return {
            "name": "consistency_checker",
            "description": "Check contradictions, missing bridges, and broken chains across reviewed sections.",
            "inputs": ["sections"],
            "outputs": ["issues", "summary"],
        }

    def check(self, section_packets: List[Dict[str, Any]], prompt_config: Dict[str, Any] | None = None) -> Dict[str, Any]:
        concise = []
        for item in section_packets[:20]:
            if not isinstance(item, dict):
                continue
            concise.append({
                "section_id": item.get("section_id", ""),
                "section_name": item.get("section_name", ""),
                "summary": item.get("summary", {}),
                "conclusion": item.get("conclusion", ""),
                "risk_level": item.get("risk_level", "low"),
            })
        if not concise:
            return {"issues": [], "summary": ""}
        prompt = self.prompts.render("consistency_checker.j2", {"sections": concise}, prompt_config=prompt_config or {})
        raw = self.llm.chat(
            messages=[
                {"role": "system", "content": "你是跨章节一致性检查代理。只输出合法 JSON。"},
                {"role": "user", "content": prompt},
            ],
            default='{"summary": "", "issues": []}',
        )
        data = self.llm.extract_json(raw)
        if data is None:
            raise LLMExecutionError("model_invalid_json", stage="consistency")
        if (not isinstance(data, dict) or not isinstance(data.get("summary"), str)
                or not data["summary"].strip() or not isinstance(data.get("issues"), list)
                or not all(isinstance(item, dict) for item in data["issues"])):
            raise LLMExecutionError("model_invalid_output", stage="consistency")
        return {
            "summary": str(data.get("summary", "") or "").strip(),
            "issues": [item for item in data.get("issues", []) if isinstance(item, dict)] if isinstance(data.get("issues", []), list) else [],
        }
