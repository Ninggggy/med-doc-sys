from typing import Any, Dict, List, Optional

from agent.agent_backend.memory.memory_manager import MemoryManager


class MemoryTool:
    def __init__(self, manager: MemoryManager | None = None):
        self.manager = manager or MemoryManager()

    def remember(
        self,
        key: str,
        value: Any,
        memory_type: str = "working",
        metadata: Optional[Dict[str, Any]] = None,
    ):
        self.manager.add(key, value, memory_type=memory_type, metadata=metadata or {})
        return {"ok": True, "key": key, "memory_type": memory_type}

    def recall(self, key: str, memory_type: str = "working"):
        item = self.manager.get(key, memory_type=memory_type)
        if not item:
            return None
        return {
            "key": item.key,
            "memory_type": item.memory_type,
            "value": item.value,
            "metadata": item.metadata,
            "timestamp": item.timestamp.isoformat(),
        }

    def search(
        self,
        query: str,
        top_k: int = 8,
        memory_types: Optional[List[str]] = None,
        metadata_filter: Optional[Dict[str, Any]] = None,
    ):
        return self.manager.search(
            query=query,
            top_k=top_k,
            memory_types=memory_types,
            metadata_filter=metadata_filter,
        )

    def context(
        self,
        query: str,
        top_k: int = 8,
        memory_types: Optional[List[str]] = None,
        max_chars: int = 1500,
        metadata_filter: Optional[Dict[str, Any]] = None,
    ):
        return self.manager.build_context(
            query=query,
            top_k=top_k,
            memory_types=memory_types,
            max_chars=max_chars,
            metadata_filter=metadata_filter,
        )
