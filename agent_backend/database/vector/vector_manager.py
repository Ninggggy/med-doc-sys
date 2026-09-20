import math
from threading import RLock
from typing import Dict, List


def cosine_similarity(a: List[float], b: List[float]) -> float:
    num = sum(x * y for x, y in zip(a, b))
    den1 = math.sqrt(sum(x * x for x in a))
    den2 = math.sqrt(sum(y * y for y in b))
    if den1 == 0 or den2 == 0:
        return 0.0
    return num / (den1 * den2)


class VectorManager:
    _shared_store: Dict[str, Dict] = {}
    _shared_lock = RLock()

    def __init__(self):
        self._store = VectorManager._shared_store
        self._lock = VectorManager._shared_lock

    def upsert(self, vec_id: str, vector: List[float], text: str, metadata: Dict | None = None) -> None:
        with self._lock:
            self._store[vec_id] = {
                "vector": vector,
                "text": text,
                "metadata": metadata or {},
            }

    def delete(self, vec_id: str) -> None:
        with self._lock:
            self._store.pop(vec_id, None)

    def delete_by_doc(self, doc_id: str) -> int:
        with self._lock:
            keys = [k for k, v in self._store.items() if (v.get("metadata") or {}).get("doc_id") == doc_id]
            for k in keys:
                self._store.pop(k, None)
            return len(keys)

    def count(self) -> int:
        with self._lock:
            return len(self._store)

    def search(self, query_vector: List[float], top_k: int = 5, filters: Dict | None = None):
        filters = filters or {}
        scored = []
        with self._lock:
            items = list(self._store.items())
        for vec_id, payload in items:
            metadata = payload.get("metadata") or {}
            if any(metadata.get(k) != v for k, v in filters.items()):
                continue
            score = cosine_similarity(query_vector, payload["vector"])
            scored.append(
                {
                    "id": vec_id,
                    "score": score,
                    "text": payload["text"],
                    "metadata": metadata,
                }
            )
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_k]

    def list_by_doc(self, doc_id: str):
        rows = []
        with self._lock:
            items = list(self._store.items())
        for vec_id, payload in items:
            metadata = payload.get("metadata") or {}
            if metadata.get("doc_id") != doc_id:
                continue
            rows.append(
                {
                    "id": vec_id,
                    "text": payload.get("text", ""),
                    "metadata": metadata,
                }
            )
        rows.sort(key=lambda x: int((x.get("metadata") or {}).get("chunk_order", 10**9)))
        return rows
