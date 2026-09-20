from __future__ import annotations

from typing import Any, Dict, List

from agent.agent_backend.llm.client import LLMClient
from agent.agent_backend.llm.errors import LLMExecutionError
from agent.agent_backend.prompts.template_manager import PromptTemplateManager


class SectionSummarizerAgent:
    """Generate structured section summary from one section bundle."""

    def __init__(self) -> None:
        self.llm = LLMClient()
        self.prompts = PromptTemplateManager("pre_review_agent_prompt")

    def describe(self) -> Dict[str, Any]:
        return {
            "name": "section_summarizer",
            "description": "Summarize one section into structured facts, missing information, and draft risks.",
            "inputs": ["section_id", "section_name", "section_text", "focus_points", "attached_files"],
            "outputs": ["structured_summary", "key_facts", "missing_items", "draft_risks"],
        }

    def summarize(
        self,
        section_id: str,
        section_name: str,
        text: str,
        focus_points: List[str] | None = None,
        attached_files: List[Dict[str, Any]] | None = None,
        prompt_config: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        content = str(text or "").strip()
        if not content:
            return {
                "section_id": section_id,
                "section_name": section_name,
                "structured_summary": "",
                "key_facts": [],
                "missing_items": ["章节无可用正文"],
                "draft_risks": [],
                "source_files": [],
            }
        files = attached_files or []
        source_files = [str(item.get("file_name", "") or "").strip() for item in files if isinstance(item, dict) and str(item.get("file_name", "") or "").strip()]
        prompt = self.prompts.render(
            "section_summarizer.j2",
            {
                "section_id": section_id,
                "section_name": section_name,
                "focus_points": [str(x).strip() for x in (focus_points or []) if str(x).strip()],
                "attached_files": source_files[:12],
                "content": content[:5000],
            },
            prompt_config=prompt_config or {},
        )
        default = {
            "structured_summary": content[:400],
            "key_facts": [],
            "missing_items": [],
            "draft_risks": [],
        }
        data = self.llm.extract_json(
            self.llm.chat(
                messages=[
                    {"role": "system", "content": "你是章节摘要代理。只输出合法 JSON。"},
                    {"role": "user", "content": prompt},
                ],
                default=str(default).replace("'", '"'),
            )
        )
        if not isinstance(data, dict):
            raise LLMExecutionError('model_invalid_json', stage='section_summarizer')
        if (not isinstance(data.get('structured_summary'), str) or not data['structured_summary'].strip()
                or any(not isinstance(data.get(key), list) or any(not isinstance(v, str) or not v.strip() for v in data[key])
                       for key in ('key_facts', 'missing_items', 'draft_risks'))):
            raise LLMExecutionError('model_invalid_output', stage='section_summarizer')
        return {
            "section_id": section_id,
            "section_name": section_name,
            "structured_summary": str(data.get("structured_summary", "") or default["structured_summary"]).strip(),
            "key_facts": [str(x).strip() for x in data.get("key_facts", []) if str(x).strip()] if isinstance(data.get("key_facts", []), list) else [],
            "missing_items": [str(x).strip() for x in data.get("missing_items", []) if str(x).strip()] if isinstance(data.get("missing_items", []), list) else [],
            "draft_risks": [str(x).strip() for x in data.get("draft_risks", []) if str(x).strip()] if isinstance(data.get("draft_risks", []), list) else [],
            "source_files": source_files,
        }
