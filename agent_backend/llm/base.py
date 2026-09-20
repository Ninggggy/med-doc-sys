from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class ChatModelBase(ABC):
    @abstractmethod
    def chat(self, messages: List[Dict[str, str]], default: str = "") -> str:
        raise NotImplementedError


class ParseModelBase(ABC):
    @abstractmethod
    def parse(
        self,
        messages: Optional[List[Dict[str, str]]] = None,
        file: str = "",
        default: str = "",
    ) -> str:
        raise NotImplementedError

    def parse_layout(self, file: str) -> Dict[str, Any]:
        raise NotImplementedError


class EmbeddingModelBase(ABC):
    @abstractmethod
    def embed(self, text: str, dimensions: Optional[int] = None) -> List[float]:
        raise NotImplementedError

    def batch_embed(self, texts: List[str]) -> List[List[float]]:
        return [self.embed(t) for t in texts]


class RerankModelBase(ABC):
    @abstractmethod
    def rerank(self, query: str, documents: List[str], top_n: Optional[int] = None) -> List[float]:
        raise NotImplementedError
