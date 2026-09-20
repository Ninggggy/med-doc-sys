from __future__ import annotations

from typing import List

from agent.agent_backend.database.vector.vector_db import Embedding


class EmbeddingService:
    """Unified local embedding service using database/vector/vector_db.py::Embedding."""

    def __init__(self) -> None:
        self.model = None

    def _get_model(self) -> Embedding:
        if self.model is None:
            self.model = Embedding()
        return self.model

    def embed(self, text: str) -> List[float]:
        vector = self._get_model().embed_query(text)
        if vector:
            return vector
        return [0.0] * 768

    def batch_embed(self, texts: list[str]) -> list[list[float]]:
        vectors = self._get_model().convert_text_to_embedding(texts or [])
        if vectors and len(vectors) == len(texts or []):
            return vectors
        return [self.embed(text) for text in (texts or [])]
