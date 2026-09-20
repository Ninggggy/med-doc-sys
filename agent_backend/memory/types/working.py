from datetime import datetime, timedelta
from typing import Optional

from agent.agent_backend.memory.base import BaseMemory, MemoryConfig, MemoryItem


class WorkingMemory(BaseMemory):
    def __init__(self, config: MemoryConfig | None = None):
        super().__init__(config or MemoryConfig(max_items=200, ttl_seconds=3600))
        self._data: dict[str, MemoryItem] = {}

    def _expired(self, item: MemoryItem) -> bool:
        if self.config.ttl_seconds is None:
            return False
        return datetime.utcnow() > item.timestamp + timedelta(seconds=self.config.ttl_seconds)

    def add(self, item: MemoryItem) -> None:
        self._data[item.key] = item
        if len(self._data) > self.config.max_items:
            oldest = sorted(self._data.values(), key=lambda x: x.timestamp)[0]
            self._data.pop(oldest.key, None)

    def get(self, key: str) -> Optional[MemoryItem]:
        item = self._data.get(key)
        if not item:
            return None
        if self._expired(item):
            self._data.pop(key, None)
            return None
        return item

    def list_items(self) -> list[MemoryItem]:
        keys = list(self._data.keys())
        for k in keys:
            self.get(k)
        return list(self._data.values())

    def delete(self, key: str) -> None:
        self._data.pop(key, None)
