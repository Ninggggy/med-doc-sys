from typing import Optional

from agent.agent_backend.memory.base import BaseMemory, MemoryConfig, MemoryItem


class LongTermMemory(BaseMemory):
    def __init__(self, config: MemoryConfig | None = None):
        super().__init__(config or MemoryConfig(max_items=10000))
        self._store: dict[str, MemoryItem] = {}

    def add(self, item: MemoryItem) -> None:
        self._store[item.key] = item

    def get(self, key: str) -> Optional[MemoryItem]:
        return self._store.get(key)

    def list_items(self) -> list[MemoryItem]:
        return list(self._store.values())

    def delete(self, key: str) -> None:
        self._store.pop(key, None)
