from typing import Optional

from agent.agent_backend.memory.base import BaseMemory, MemoryConfig, MemoryItem


class PerceptualMemory(BaseMemory):
    def __init__(self, config: MemoryConfig | None = None):
        super().__init__(config or MemoryConfig(max_items=1000))
        self._items: dict[str, MemoryItem] = {}

    def add(self, item: MemoryItem) -> None:
        self._items[item.key] = item

    def get(self, key: str) -> Optional[MemoryItem]:
        return self._items.get(key)

    def list_items(self) -> list[MemoryItem]:
        return list(self._items.values())

    def delete(self, key: str) -> None:
        self._items.pop(key, None)
