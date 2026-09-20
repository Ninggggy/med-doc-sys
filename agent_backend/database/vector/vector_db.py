from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.agent_backend.config.settings import settings
from agent.agent_backend.database import singleton
from agent.agent_backend.database.vector.milvus_db import MilvusDB
from agent.agent_backend.utils.file_util import get_home_cache_dir


def _ensure_writable_directory(path_value: str) -> str:
    candidate = Path(str(path_value or "").strip()).expanduser()
    if not str(candidate):
        raise ValueError("cache directory path is empty")
    candidate.mkdir(parents=True, exist_ok=True)
    probe = candidate / ".write_probe"
    probe.write_text("ok", encoding="utf-8")
    probe.unlink(missing_ok=True)
    return str(candidate)


def resolve_modelscope_cache_dir() -> str:
    configured = str(os.getenv("MODELSCOPE_CACHE", "") or "").strip()
    project_default = str((Path(settings.rule_data_dir).resolve().parents[0] / "modelscope_cache"))
    home_default = str(Path(get_home_cache_dir()) / "modelscope_cache")
    for candidate in [configured, project_default, home_default]:
        if not str(candidate or "").strip():
            continue
        try:
            return _ensure_writable_directory(candidate)
        except Exception:
            continue
    raise PermissionError("unable to resolve a writable ModelScope cache directory")


@singleton
class Embedding:
    """
    Project-local embedding entry.

    Current policy:
    - use the local modelscope sentence embedding pipeline
    - the model is selected by environment variables and should be baked into
      the backend image cache for deployment
    """

    def __init__(
        self,
        model_id: str = "iic/nlp_gte_sentence-embedding_chinese-large",
        sequence_length: int = 512,
    ) -> None:
        os.environ["MODELSCOPE_CACHE"] = resolve_modelscope_cache_dir()
        try:
            from modelscope.pipelines import pipeline
            from modelscope.utils.constant import Tasks
        except Exception as exc:
            raise RuntimeError("modelscope is required for local Embedding") from exc

        self.model_id = str(os.getenv("EMBEDDING_LOCAL_MODEL_ID", model_id) or model_id)
        self.sequence_length = int(os.getenv("EMBEDDING_LOCAL_SEQUENCE_LENGTH", str(sequence_length)) or sequence_length)
        self.pipeline = pipeline(
            Tasks.sentence_embedding,
            model=self.model_id,
            sequence_length=self.sequence_length,
        )

    def convert_text_to_embedding(self, source_sentence: List[str]) -> List[List[float]]:
        texts = [str(item or "").strip() for item in (source_sentence or []) if str(item or "").strip()]
        if not texts:
            return []
        result = self.pipeline(input={"source_sentence": texts})
        vectors = result.get("text_embedding", [])
        if hasattr(vectors, "tolist"):
            vectors = vectors.tolist()
        return [list(map(float, row or [])) for row in (vectors or [])]

    def embed_query(self, text: str) -> List[float]:
        rows = self.convert_text_to_embedding([text])
        return rows[0] if rows else []


@singleton
class VectorDB:
    """
    Backward-compatible vector DB facade.
    Internally backed by persistent MilvusDB.
    """

    def __init__(
        self,
        db_path: str = "",
        collection_name: str = "",
        collection_dim: int = 1024,
    ) -> None:
        uri = db_path or settings.vector_db_uri
        name = collection_name or settings.vector_collection
        self.collection_name = str(name)
        self.collection_dim = int(collection_dim or 1024)
        self.db = MilvusDB(collection_name=self.collection_name, uri=uri)

    def save(self, data: List[Dict[str, Any]]):
        count = 0
        for row in data or []:
            vec_id = str(row.get("id", "") or "")
            vector = list(row.get("vector") or [])
            text = str(row.get("text", row.get("raw_text", "")) or "")
            metadata = dict(row.get("metadata") or {})
            if not vec_id or not vector:
                continue
            self.db.insert(vec_id=vec_id, vector=vector, text=text, metadata=metadata)
            count += 1
        return {"insert_count": count}

    def deleteByIds(self, doc_ids: List[str]):
        deleted = 0
        for item in doc_ids or []:
            self.db.delete(str(item))
            deleted += 1
        return {"delete_count": deleted}

    def search(
        self,
        query_embedding: List[List[float]],
        limit: int = 5,
        metric_type: str = "COSINE",
        params: Optional[Dict[str, Any]] = None,
    ):
        _ = metric_type
        _ = params
        if not query_embedding:
            return []
        query_vector = list((query_embedding or [[]])[0] or [])
        return [self.db.search(query_vector=query_vector, top_k=limit)]
