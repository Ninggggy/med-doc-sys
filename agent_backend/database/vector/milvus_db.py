from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from agent.agent_backend.config.settings import settings
from agent.agent_backend.utils.file_util import ensure_dir_exists


class MilvusDB:
    """
    Persistent vector store backed by Milvus / Milvus Lite.

    Storage modes:
    - local persistent file: use a filesystem path as `uri`
    - remote Milvus server: use `http://host:19530` + optional token

    Notes:
    - Collection is created lazily on first insert based on vector dimension.
    - Dynamic fields are enabled so metadata can evolve without schema churn.
    """

    OUTPUT_FIELDS = [
        "text",
        "metadata_json",
        "doc_id",
        "chunk_id",
        "chunk_order",
        "item_type",
        "classification",
        "section_id",
        "page",
        "page_start",
        "page_end",
    ]

    def __init__(
        self,
        collection_name: str = "agent_collection",
        uri: str = "",
        token: str = "",
        db_name: str = "",
    ) -> None:
        self.collection_name = str(collection_name or settings.vector_collection).strip() or "agent_collection"
        self.uri = str(uri or settings.vector_db_uri).strip()
        self.token = str(token or settings.milvus_token).strip()
        self.db_name = str(db_name or settings.milvus_db_name).strip()
        self._client = None
        self._dimension: Optional[int] = None
        self._prepare_local_path()

    def _prepare_local_path(self) -> None:
        if not self.uri or self.uri.startswith("http://") or self.uri.startswith("https://"):
            return
        local_path = Path(self.uri).expanduser().resolve(strict=False)
        self.uri = str(local_path)
        ensure_dir_exists(str(local_path.parent))

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            from pymilvus import MilvusClient
        except Exception as exc:
            raise RuntimeError(
                "failed to import pymilvus; please verify pymilvus and its pandas/numpy dependency chain, "
                "especially numpy 2.x compatibility on the current server"
            ) from exc

        kwargs: Dict[str, Any] = {"uri": self.uri}
        if self.token:
            kwargs["token"] = self.token
        if self.db_name:
            kwargs["db_name"] = self.db_name
        try:
            self._client = MilvusClient(**kwargs)
            return self._client
        except Exception as exc:
            # 远程 Milvus 不可达时，自动降级到本地 Milvus Lite，避免主流程中断。
            if self.uri.startswith("http://") or self.uri.startswith("https://"):
                fallback_path = Path(settings.rule_data_dir).resolve().parents[0] / "milvus" / "agent_milvus_fallback.db"
                ensure_dir_exists(str(fallback_path.parent))
                logging.warning(
                    "Milvus remote unavailable (%s), fallback to local Milvus Lite: %s",
                    str(exc),
                    str(fallback_path),
                )
                self.uri = str(fallback_path)
                self.token = ""
                self.db_name = ""
                self._client = MilvusClient(uri=self.uri)
                return self._client
            raise

    @staticmethod
    def _escape_filter_value(value: Any) -> str:
        if value is None:
            return "null"
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        text = str(value).replace("\\", "\\\\").replace('"', '\\"')
        return f'"{text}"'

    def _build_filter(self, filters: Optional[Dict[str, Any]] = None) -> str:
        clauses = []
        for key, value in (filters or {}).items():
            k = str(key or "").strip()
            if not k:
                continue
            clauses.append(f"{k} == {self._escape_filter_value(value)}")
        return " and ".join(clauses)

    @staticmethod
    def _normalize_limit(limit: Optional[int], default: int = 10000) -> int:
        """统一兜底 limit，避免 Milvus 在空表达式 query 时直接报错。"""
        try:
            value = int(limit) if limit is not None else 0
        except Exception:
            value = 0
        return value if value > 0 else int(default)

    def _query_rows_direct(
        self,
        *,
        client: Any,
        filter_expr: str,
        output_fields: List[str],
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """兼容新版 Milvus：空 filter 必须显式带 limit。"""
        kwargs: Dict[str, Any] = {
            "collection_name": self.collection_name,
            "output_fields": output_fields,
        }
        normalized_expr = str(filter_expr or "").strip()
        if normalized_expr:
            kwargs["filter"] = normalized_expr
        else:
            kwargs["filter"] = ""
            kwargs["limit"] = self._normalize_limit(limit)
        if limit is not None and int(limit) > 0:
            kwargs["limit"] = int(limit)
        rows = client.query(**kwargs)
        return list(rows or [])

    def _ensure_collection(self, dimension: int) -> None:
        client = self._get_client()
        if client.has_collection(self.collection_name):
            if self._dimension is None:
                try:
                    info = client.describe_collection(self.collection_name)
                    self._dimension = int(info.get("dimension") or dimension)
                except Exception:
                    self._dimension = dimension
            return
        client.create_collection(
            collection_name=self.collection_name,
            dimension=int(dimension),
            primary_field_name="id",
            id_type="string",
            max_length=512,
            vector_field_name="vector",
            metric_type="COSINE",
            auto_id=False,
            enable_dynamic_field=True,
        )
        self._dimension = int(dimension)

    @staticmethod
    def _metadata_json(metadata: Optional[Dict[str, Any]]) -> str:
        try:
            return json.dumps(metadata or {}, ensure_ascii=False)
        except Exception:
            return "{}"

    @staticmethod
    def _row_from_payload(vec_id: str, vector: List[float], text: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        metadata = metadata or {}
        return {
            "id": str(vec_id),
            "vector": list(vector or []),
            "text": str(text or ""),
            "metadata_json": MilvusDB._metadata_json(metadata),
            "doc_id": str(metadata.get("doc_id", "") or ""),
            "chunk_id": str(metadata.get("chunk_id", "") or ""),
            "chunk_order": int(metadata.get("chunk_order", 0) or 0),
            "item_type": str(metadata.get("item_type", "") or ""),
            "classification": str(metadata.get("classification", "") or ""),
            "section_id": str(metadata.get("section_id", "") or ""),
            "page": metadata.get("page"),
            "page_start": metadata.get("page_start"),
            "page_end": metadata.get("page_end"),
        }

    @staticmethod
    def _parse_metadata(row: Dict[str, Any]) -> Dict[str, Any]:
        raw = str(row.get("metadata_json", "") or "").strip()
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except Exception:
            return {}

    def insert(self, vec_id: str, vector: List[float], text: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        if not vector:
            raise ValueError("vector is required")
        self._ensure_collection(len(vector))
        client = self._get_client()
        row = self._row_from_payload(vec_id=vec_id, vector=vector, text=text, metadata=metadata)
        client.upsert(collection_name=self.collection_name, data=[row])

    def search(self, query_vector: List[float], top_k: int = 5, filters: Optional[Dict[str, Any]] = None):
        if not query_vector:
            return []
        client = self._get_client()
        if not client.has_collection(self.collection_name):
            return []
        expr = self._build_filter(filters)
        rows = client.search(
            collection_name=self.collection_name,
            data=[list(query_vector)],
            filter=expr,
            limit=max(1, int(top_k or 5)),
            output_fields=self.OUTPUT_FIELDS,
            search_params={"metric_type": "COSINE"},
        )
        batch = rows[0] if rows else []
        out = []
        for item in batch:
            entity = item.get("entity", {}) if isinstance(item.get("entity", {}), dict) else {}
            metadata = self._parse_metadata(entity)
            out.append(
                {
                    "id": item.get("id") or entity.get("id"),
                    "score": item.get("distance", item.get("score", 0.0)),
                    "text": entity.get("text", ""),
                    "metadata": metadata,
                }
            )
        return out

    def delete(self, vec_id: str) -> None:
        client = self._get_client()
        if not client.has_collection(self.collection_name):
            return
        client.delete(collection_name=self.collection_name, ids=[str(vec_id)])

    def delete_by_doc(self, doc_id: str) -> int:
        client = self._get_client()
        if not client.has_collection(self.collection_name):
            return 0
        result = client.delete(
            collection_name=self.collection_name,
            filter=f'doc_id == {self._escape_filter_value(doc_id)}',
        )
        return int((result or {}).get("delete_count", 0) or 0)

    def count(self) -> int:
        client = self._get_client()
        if not client.has_collection(self.collection_name):
            return 0
        try:
            info = client.describe_collection(self.collection_name)
            return int(info.get("num_entities") or info.get("row_count") or 0)
        except Exception:
            try:
                if hasattr(client, "query_iterator"):
                    iterator = client.query_iterator(
                        collection_name=self.collection_name,
                        batch_size=1000,
                        limit=-1,
                        output_fields=["id"],
                    )
                    total = 0
                    while True:
                        batch = iterator.next()
                        if not batch:
                            break
                        total += len(batch)
                    iterator.close()
                    return total
                rows = client.query(
                    **{
                        "collection_name": self.collection_name,
                        "filter": "",
                        "output_fields": ["id"],
                        "limit": self._normalize_limit(10000),
                    }
                )
                return len(rows or [])
            except Exception:
                return 0

    def status(self) -> Dict[str, Any]:
        try:
            client = self._get_client()
            has_collection = bool(client.has_collection(self.collection_name))
            return {
                "ok": True,
                "mode": "remote" if self.uri.startswith("http://") or self.uri.startswith("https://") else "milvus_lite",
                "uri": self.uri,
                "collection_name": self.collection_name,
                "collection_exists": has_collection,
                "count": self.count() if has_collection else 0,
                "error": "",
            }
        except Exception as exc:
            return {
                "ok": False,
                "mode": "remote" if self.uri.startswith("http://") or self.uri.startswith("https://") else "milvus_lite",
                "uri": self.uri,
                "collection_name": self.collection_name,
                "collection_exists": False,
                "count": 0,
                "error": str(exc),
            }

    def list_by_doc(self, doc_id: str):
        client = self._get_client()
        if not client.has_collection(self.collection_name):
            return []
        rows = self._query_rows_direct(
            client=client,
            filter_expr=f'doc_id == {self._escape_filter_value(doc_id)}',
            output_fields=["id"] + self.OUTPUT_FIELDS,
        )
        out = []
        for row in rows or []:
            metadata = self._parse_metadata(row)
            out.append(
                {
                    "id": row.get("id"),
                    "text": row.get("text", ""),
                    "metadata": metadata,
                }
            )
        out.sort(key=lambda x: int((x.get("metadata") or {}).get("chunk_order", 10**9)))
        return out

    def query_rows(
        self,
        filters: Optional[Dict[str, Any]] = None,
        limit: Optional[int] = None,
        output_fields: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        client = self._get_client()
        if not client.has_collection(self.collection_name):
            return []
        fields = ["id"] + list(output_fields or self.OUTPUT_FIELDS)
        expr = self._build_filter(filters)
        if hasattr(client, "query_iterator"):
            iterator = client.query_iterator(
                collection_name=self.collection_name,
                batch_size=1000,
                limit=int(limit) if limit is not None and int(limit) > 0 else -1,
                filter=expr,
                output_fields=fields,
            )
            rows: List[Dict[str, Any]] = []
            try:
                while True:
                    batch = iterator.next()
                    if not batch:
                        break
                    rows.extend(batch)
                    if limit is not None and int(limit) > 0 and len(rows) >= int(limit):
                        return rows[: int(limit)]
            finally:
                try:
                    iterator.close()
                except Exception:
                    pass
            return rows
        rows = self._query_rows_direct(
            client=client,
            filter_expr=expr,
            output_fields=fields,
            limit=limit,
        )
        if limit is not None and int(limit) > 0:
            return list(rows or [])[: int(limit)]
        return list(rows or [])

    def export_snapshot(self, snapshot_path: str) -> Dict[str, Any]:
        client = self._get_client()
        rows: List[Dict[str, Any]] = []
        if client.has_collection(self.collection_name):
            if hasattr(client, "query_iterator"):
                iterator = client.query_iterator(
                    collection_name=self.collection_name,
                    batch_size=1000,
                    limit=-1,
                    output_fields=["id", "vector"] + self.OUTPUT_FIELDS,
                )
                try:
                    while True:
                        batch = iterator.next()
                        if not batch:
                            break
                        rows.extend(batch)
                finally:
                    try:
                        iterator.close()
                    except Exception:
                        pass
            else:
                rows = list(
                    self._query_rows_direct(
                        client=client,
                        filter_expr="",
                        output_fields=["id", "vector"] + self.OUTPUT_FIELDS,
                        limit=10000,
                    )
                    or []
                )
        payload = {
            "collection_name": self.collection_name,
            "uri": self.uri,
            "db_name": self.db_name,
            "dimension": self._dimension,
            "row_count": len(rows),
            "rows": rows,
        }
        target = Path(snapshot_path)
        ensure_dir_exists(str(target.parent))
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return {"snapshot_path": str(target), "row_count": len(rows)}

    def import_snapshot(self, snapshot_path: str, replace: bool = False) -> Dict[str, Any]:
        source = Path(snapshot_path)
        payload = json.loads(source.read_text(encoding="utf-8"))
        rows = payload.get("rows", []) if isinstance(payload.get("rows", []), list) else []
        if not rows:
            return {"snapshot_path": str(source), "row_count": 0}
        dimension = int(payload.get("dimension") or len((rows[0] or {}).get("vector", []) or []))
        self._ensure_collection(dimension)
        client = self._get_client()
        if replace and client.has_collection(self.collection_name):
            client.drop_collection(self.collection_name)
            self._client = None
            self._dimension = None
            client = self._get_client()
            self._ensure_collection(dimension)
        batch_size = 200
        for start in range(0, len(rows), batch_size):
            client.upsert(collection_name=self.collection_name, data=rows[start : start + batch_size])
        return {"snapshot_path": str(source), "row_count": len(rows)}
