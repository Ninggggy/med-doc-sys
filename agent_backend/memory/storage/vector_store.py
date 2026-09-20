from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from agent.agent_backend.config.settings import settings
from agent.agent_backend.database.vector.milvus_db import MilvusDB
from agent.agent_backend.database.vector.vector_db import Embedding


class VectorStore:
    """Unified project vector store backed by persistent Milvus."""

    def __init__(self, collection_name: str = "") -> None:
        self.collection_name = str(collection_name or settings.vector_collection).strip() or settings.vector_collection
        self.db = MilvusDB(collection_name=self.collection_name)
        self.embedding = None

    def _get_embedding(self) -> Embedding:
        if self.embedding is None:
            self.embedding = Embedding()
        return self.embedding

    @staticmethod
    def _normalize_filters(filters: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for key, value in (filters or {}).items():
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            out[str(key)] = value
        return out

    @staticmethod
    def _query_terms(query: str) -> List[str]:
        terms = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]{2,}", (query or "").lower())
        return [item for item in terms if item]

    @staticmethod
    def _keyword_score(query_terms: List[str], row: Dict[str, Any]) -> float:
        if not query_terms:
            return 0.0
        metadata = row.get("metadata") or {}
        haystack = "\n".join(
            [
                str(row.get("text", "") or ""),
                str(metadata.get("summary", "") or ""),
                " ".join([str(item).strip() for item in metadata.get("keywords", []) if str(item).strip()]),
                str(metadata.get("classification", "") or ""),
                str(metadata.get("section_name", "") or ""),
                str(metadata.get("section_path_text", "") or ""),
            ]
        ).lower()
        if not haystack.strip():
            return 0.0
        hit = sum(1 for term in query_terms if term in haystack)
        return hit / max(1, len(query_terms))

    def add(
        self,
        vec_id: str,
        vector: Optional[List[float]],
        text: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        vec = list(vector or [])
        if not vec:
            vec = self._get_embedding().embed_query(text)
        self.db.insert(vec_id=str(vec_id), vector=vec, text=str(text or ""), metadata=metadata or {})

    def add_text(
        self,
        vec_id: str,
        text: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.add(vec_id=vec_id, vector=None, text=text, metadata=metadata)

    def search(
        self,
        query_vector: List[float],
        top_k: int = 5,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        return self.db.search(
            query_vector=list(query_vector or []),
            top_k=max(1, int(top_k or 5)),
            filters=self._normalize_filters(filters),
        )

    def search_text(
        self,
        query: str,
        top_k: int = 5,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        query_vector = self._get_embedding().embed_query(query)
        return self.search(query_vector=query_vector, top_k=top_k, filters=filters)

    def keyword_search(
        self,
        query: str,
        top_k: int = 5,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        terms = self._query_terms(query)
        rows = self.db.query_rows(filters=self._normalize_filters(filters))
        hits: List[Dict[str, Any]] = []
        for row in rows:
            metadata = self.db._parse_metadata(row)
            payload = {
                "id": row.get("id"),
                "text": row.get("text", ""),
                "metadata": metadata,
            }
            score = self._keyword_score(terms, payload)
            if score <= 0:
                continue
            payload["score"] = score
            hits.append(payload)
        hits.sort(key=lambda item: float(item.get("score", 0.0)), reverse=True)
        return hits[: max(1, int(top_k or 5))]

    def delete(self, vec_id: str) -> None:
        self.db.delete(str(vec_id))

    def delete_by_doc(self, doc_id: str) -> int:
        return self.db.delete_by_doc(str(doc_id or ""))

    def list_by_doc(self, doc_id: str) -> List[Dict[str, Any]]:
        return self.db.list_by_doc(str(doc_id or ""))

    def has_doc(self, doc_id: str) -> bool:
        rows = self.db.query_rows(filters={"doc_id": str(doc_id or "")}, limit=1, output_fields=["doc_id"])
        return bool(rows)

    def count(self) -> int:
        return self.db.count()

    def export_snapshot(self, snapshot_path: str) -> Dict[str, Any]:
        return self.db.export_snapshot(snapshot_path)

    def import_snapshot(self, snapshot_path: str, replace: bool = False) -> Dict[str, Any]:
        return self.db.import_snapshot(snapshot_path, replace=replace)
