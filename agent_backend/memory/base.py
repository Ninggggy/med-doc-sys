from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional


@dataclass
class MemoryItem:
    key: str
    value: Any
    memory_type: str
    timestamp: datetime = field(default_factory=datetime.utcnow)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_text(self) -> str:
        if isinstance(self.value, str):
            return self.value
        return str(self.value)


@dataclass
class MemoryConfig:
    max_items: int = 1000
    ttl_seconds: Optional[int] = None


class BaseMemory:
    def __init__(self, config: MemoryConfig):
        self.config = config

    def add(self, item: MemoryItem) -> None:
        raise NotImplementedError

    def get(self, key: str) -> Optional[MemoryItem]:
        raise NotImplementedError

    def list_items(self) -> list[MemoryItem]:
        raise NotImplementedError

    def delete(self, key: str) -> None:
        raise NotImplementedError

