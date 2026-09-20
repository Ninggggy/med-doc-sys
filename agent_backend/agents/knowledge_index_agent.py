import re
from collections import Counter
from typing import Dict, List

from agent.agent_backend.llm.client import LLMClient
from agent.agent_backend.prompts.template_manager import PromptTemplateManager


class KnowledgeIndexAgent:
    """Single-purpose enrich agent for knowledge indexing."""

    def __init__(self):
        self.llm = LLMClient()
        self.prompts = PromptTemplateManager("knowledge_parse_agent_prompt")

    @staticmethod
    def _fallback_keywords(text: str, top_k: int = 8) -> List[str]:
        tokens = re.findall(r"[A-Za-z][A-Za-z0-9_-]{1,}|[\u4e00-\u9fff]{2,}", text or "")
        if not tokens:
            return []
        stop_words = {"我们", "你们", "他们", "以及", "相关", "进行", "其中", "包括", "根据", "这个", "那个", "可以"}
        terms = [token for token in tokens if token not in stop_words]
        freq = Counter(terms)
        return [item for item, _ in freq.most_common(top_k)]

    def _run_chunk_prompt(self, text: str, fallback_summary: str, fallback_keywords: List[str]) -> Dict[str, object]:
        raw = self.llm.chat(
            messages=[
                {"role": "system", "content": self.prompts.render("system_json_only.j2", {})},
                {"role": "user", "content": self.prompts.render("chunk_enrich.j2", {"text": text[:6000]})},
            ],
            default=f'{{"summary":"{fallback_summary}","keywords":[]}}',
        )
        data = self.llm.extract_json(raw)
        if not isinstance(data, dict):
            return {"summary": fallback_summary, "keywords": fallback_keywords}

        summary = str(data.get("summary", "")).strip() or fallback_summary
        raw_keywords = data.get("keywords", [])
        keywords = [str(item).strip() for item in raw_keywords if str(item).strip()] if isinstance(raw_keywords, list) else []
        if not keywords:
            keywords = fallback_keywords

        seen = set()
        dedup: List[str] = []
        for keyword in keywords:
            if keyword and keyword not in seen:
                seen.add(keyword)
                dedup.append(keyword)
        return {"summary": summary[:400], "keywords": dedup[:20]}

    def analyze_chunk(self, text: str) -> Dict[str, object]:
        source = (text or "").strip()
        if not source:
            return {"summary": "", "keywords": []}
        return self._run_chunk_prompt(
            text=source,
            fallback_summary=source[:120],
            fallback_keywords=self._fallback_keywords(source, top_k=8),
        )

    def analyze_document(self, title: str, chunks: List[Dict[str, object]], full_text: str = "") -> Dict[str, object]:
        source = (full_text or "").strip()
        if not source:
            source = "\n".join([str(item.get("summary", "")).strip() for item in chunks if isinstance(item, dict)]).strip()
        if not source:
            source = "\n".join([str(item.get("text", ""))[:200] for item in chunks if isinstance(item, dict)]).strip()
        if not source:
            return {"summary": "", "keywords": [], "chunks": []}

        raw = self.llm.chat(
            messages=[
                {"role": "system", "content": self.prompts.render("system_json_only.j2", {})},
                {
                    "role": "user",
                    "content": self.prompts.render(
                        "chunk_enrich.j2",
                        {"title": title or "", "text": source},
                    ),
                },
            ],
            default='{"summary":"","keywords":[],"chunks":[]}',
        )
        data = self.llm.extract_json(raw)
        if not isinstance(data, dict):
            data = {}

        keyword_pool: List[str] = []
        for item in chunks:
            if isinstance(item, dict) and isinstance(item.get("keywords"), list):
                keyword_pool.extend([str(x).strip() for x in item.get("keywords", []) if str(x).strip()])
        fallback_keywords = self._fallback_keywords(" ".join(keyword_pool) or source, top_k=12)

        summary = str(data.get("summary", "")).strip() or source[:220]
        raw_keywords = data.get("keywords", [])
        keywords = [str(item).strip() for item in raw_keywords if str(item).strip()] if isinstance(raw_keywords, list) else []
        if not keywords:
            keywords = fallback_keywords

        normalized_chunks: List[Dict[str, object]] = []
        raw_chunks = data.get("chunks", [])
        if isinstance(raw_chunks, list):
            for item in raw_chunks:
                if not isinstance(item, dict):
                    continue
                text = str(item.get("text", "")).strip()
                if not text:
                    continue
                chunk_summary = str(item.get("chunk_summary", "")).strip() or text[:120]
                chunk_keywords_raw = item.get("chunk_keywords", [])
                chunk_keywords = (
                    [str(x).strip() for x in chunk_keywords_raw if str(x).strip()]
                    if isinstance(chunk_keywords_raw, list)
                    else []
                )
                if not chunk_keywords:
                    chunk_keywords = self._fallback_keywords(text, top_k=8)
                normalized_chunks.append(
                    {
                        "chunk_summary": chunk_summary[:400],
                        "chunk_keywords": chunk_keywords[:20],
                        "text": text,
                    }
                )

        if not normalized_chunks:
            fallback = self.analyze_chunk(source)
            normalized_chunks = [
                {
                    "chunk_summary": str(fallback.get("summary", "")).strip() or source[:120],
                    "chunk_keywords": list(fallback.get("keywords", []) or []),
                    "text": source,
                }
            ]

        return {
            "summary": summary[:400],
            "keywords": keywords[:20],
            "chunks": normalized_chunks,
        }
