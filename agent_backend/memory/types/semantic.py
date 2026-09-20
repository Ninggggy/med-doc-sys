from typing import Optional

from agent.agent_backend.memory.base import BaseMemory, MemoryConfig, MemoryItem


class SemanticMemory(BaseMemory):
    def __init__(self, config: MemoryConfig | None = None):
        super().__init__(config or MemoryConfig(max_items=2000))
        self._graph: dict[str, MemoryItem] = {}

    def add(self, item: MemoryItem) -> None:
        self._graph[item.key] = item

    def get(self, key: str) -> Optional[MemoryItem]:
        return self._graph.get(key)

    def list_items(self) -> list[MemoryItem]:
        return list(self._graph.values())

    def delete(self, key: str) -> None:
        self._graph.pop(key, None)
