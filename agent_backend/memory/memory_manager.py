from datetime import datetime
from typing import Any, Dict, List, Optional

from agent.agent_backend.context.builder import ContextBuilder
from agent.agent_backend.memory.base import MemoryItem
from agent.agent_backend.memory.embedding import EmbeddingService
from agent.agent_backend.memory.types.episodic import EpisodicMemory
from agent.agent_backend.memory.types.long_term import LongTermMemory
from agent.agent_backend.memory.types.perceptual import PerceptualMemory
from agent.agent_backend.memory.types.semantic import SemanticMemory
from agent.agent_backend.memory.types.working import WorkingMemory


class MemoryManager:
    def __init__(self):
        self.working = WorkingMemory()
        self.episodic = EpisodicMemory()
        self.semantic = SemanticMemory()
        self.perceptual = PerceptualMemory()
        self.long_term = LongTermMemory()
        self.embedding = EmbeddingService()
        self.context_builder = ContextBuilder()

    @staticmethod
    def _all_memory_names() -> list[str]:
        return ["working", "episodic", "semantic", "perceptual", "long_term"]

    def _get_memory(self, memory_type: str):
        if memory_type not in self._all_memory_names():
            raise ValueError(f"Unknown memory_type: {memory_type}")
        return getattr(self, memory_type)

    def add(self, key: str, value: Any, memory_type: str = "working", metadata: dict | None = None) -> None:
        item = MemoryItem(
            key=key,
            value=value,
            memory_type=memory_type,
            metadata=metadata or {},
        )
        self._get_memory(memory_type).add(item)

    def get(self, key: str, memory_type: str = "working") -> Optional[MemoryItem]:
        return self._get_memory(memory_type).get(key)

    def delete(self, key: str, memory_type: str = "working") -> None:
        self._get_memory(memory_type).delete(key)

    def list_by_type(self, memory_type: str) -> list[MemoryItem]:
        return self._get_memory(memory_type).list_items()

    def list_all(self) -> dict[str, list[MemoryItem]]:
        return {m: self._get_memory(m).list_items() for m in self._all_memory_names()}

    @staticmethod
    def _cosine(a: list[float], b: list[float]) -> float:
        if not a or not b:
            return 0.0
        num = sum(x * y for x, y in zip(a, b))
        den1 = sum(x * x for x in a) ** 0.5
        den2 = sum(y * y for y in b) ** 0.5
        if den1 == 0 or den2 == 0:
            return 0.0
        return num / (den1 * den2)

    @staticmethod
    def _recency_bonus(item: MemoryItem, tau_seconds: float = 3600.0) -> float:
        delta = (datetime.utcnow() - item.timestamp).total_seconds()
        if delta <= 0:
            return 1.0
        return 1.0 / (1.0 + delta / tau_seconds)

    def search(
        self,
        query: str,
        top_k: int = 8,
        memory_types: Optional[List[str]] = None,
        metadata_filter: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        memory_types = memory_types or self._all_memory_names()
        metadata_filter = metadata_filter or {}

        qv = self.embedding.embed(query)
        candidates: List[Dict[str, Any]] = []
        for m in memory_types:
            mem = self._get_memory(m)
            for item in mem.list_items():
                if any(item.metadata.get(k) != v for k, v in metadata_filter.items()):
                    continue
                iv = self.embedding.embed(item.to_text())
                sim = self._cosine(qv, iv)
                rec = self._recency_bonus(item)
                score = 0.85 * sim + 0.15 * rec
                candidates.append(
                    {
                        "key": item.key,
                        "memory_type": item.memory_type,
                        "value": item.value,
                        "metadata": item.metadata,
                        "timestamp": item.timestamp.isoformat(),
                        "score": score,
                        "semantic_score": sim,
                        "recency_score": rec,
                    }
                )

        candidates.sort(key=lambda x: x["score"], reverse=True)
        return candidates[: max(1, int(top_k))]

    def build_context(
        self,
        query: str,
        top_k: int = 8,
        memory_types: Optional[List[str]] = None,
        max_chars: int = 1500,
        metadata_filter: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        hits = self.search(
            query=query,
            top_k=top_k,
            memory_types=memory_types,
            metadata_filter=metadata_filter,
        )
        gssc = self.context_builder.build(
            sources=[
                {
                    "text": str(h["value"]),
                    "score": h["score"],
                    "source": h["memory_type"],
                    "metadata": h["metadata"],
                }
                for h in hits
            ],
            max_items=top_k,
            output_char_budget=max_chars,
        )
        return {
            "query": query,
            "hits": hits,
            "context": gssc.compressed,
            "structured": gssc.structured,
            "metadata_filter": metadata_filter or {},
        }
