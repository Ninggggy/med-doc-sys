from collections import deque
from typing import Optional

from agent.agent_backend.memory.base import BaseMemory, MemoryConfig, MemoryItem


class EpisodicMemory(BaseMemory):
    def __init__(self, config: MemoryConfig | None = None):
        super().__init__(config or MemoryConfig(max_items=500))
        self._events = deque(maxlen=self.config.max_items)

    def add(self, item: MemoryItem) -> None:
        self._events.append(item)

    def get(self, key: str) -> Optional[MemoryItem]:
        for e in reversed(self._events):
            if e.key == key:
                return e
        return None

    def list_items(self) -> list[MemoryItem]:
        return list(self._events)

    def delete(self, key: str) -> None:
        self._events = deque([e for e in self._events if e.key != key], maxlen=self.config.max_items)
