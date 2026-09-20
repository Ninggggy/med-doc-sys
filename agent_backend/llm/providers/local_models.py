from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib import request
from urllib.error import HTTPError, URLError

from agent.agent_backend.llm.base import EmbeddingModelBase, RerankModelBase


def _load_api_key(conf: Dict[str, Any]) -> str:
    p = Path(str(conf.get("api_key_path", "") or ""))
    if p.exists():
        return p.read_text(encoding="utf-8").strip()
    return ""


class HashEmbeddingModel(EmbeddingModelBase):
    def __init__(self, conf: Dict[str, Any]):
        self.dim = max(8, int(conf.get("dim", 128)))

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        low = (text or "").lower()
        return re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", low)

    def embed(self, text: str, dimensions: Optional[int] = None) -> List[float]:
        print(f"[LLMDebug] HashEmbeddingModel.embed.input.dimensions: {dimensions}")
        print(f"[LLM] HashEmbeddingModel input_chars={len(text or '')}")
        tokens = self._tokenize(text)
        c = Counter(tokens)
        dim = self.dim if dimensions is None else max(8, int(dimensions))
        vec = [0.0] * dim
        for tok, freq in c.items():
            vec[hash(tok) % dim] += float(freq)
        print(f"[LLMDebug] HashEmbeddingModel.embed.output.dim: {len(vec)}")
        return vec


class OpenAICompatibleEmbeddingModel(EmbeddingModelBase):
    def __init__(self, conf: Dict[str, Any]):
        self.model = str(conf.get("model", ""))
        self.base_url = str(conf.get("base_url", "")).rstrip("/")
        self.api_key = _load_api_key(conf)
        self.timeout = int(conf.get("timeout", 30))

    def embed(self, text: str, dimensions: Optional[int] = None) -> List[float]:
        print(f"[LLMDebug] OpenAICompatibleEmbeddingModel.embed.input.dimensions: {dimensions}")
        print(f"[LLM] OpenAICompatibleEmbeddingModel input_chars={len(text or '')}")
        if not self.base_url or not self.model or not self.api_key:
            return []
        payload = {"model": self.model, "input": text}
        if dimensions is not None:
            payload["dimensions"] = int(dimensions)
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = request.Request(
            f"{self.base_url}/embeddings",
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self.timeout) as resp:
                raw = json.loads(resp.read().decode("utf-8", errors="ignore"))
            data = raw.get("data") if isinstance(raw, dict) else None
            if isinstance(data, list) and data:
                emb = data[0].get("embedding", [])
                if isinstance(emb, list):
                    result = [float(x) for x in emb]
                    print(f"[LLMDebug] OpenAICompatibleEmbeddingModel.embed.output.dim: {len(result)}")
                    return result
        except (HTTPError, URLError, TimeoutError, ValueError, TypeError, json.JSONDecodeError):
            return []
        except Exception:
            return []
        return []


class LexicalRerankModel(RerankModelBase):
    @staticmethod
    def _score_one(query: str, text: str) -> float:
        q = (query or "").lower()
        t = (text or "").lower()
        q_terms = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", q)
        if not q_terms:
            return 0.0
        hit = sum(1 for tok in q_terms if tok in t)
        return float(hit) / float(len(q_terms))

    def rerank(self, query: str, documents: List[str], top_n: Optional[int] = None) -> List[float]:
        print(f"[LLM] LexicalRerankModel query_chars={len(query or '')}")
        print(f"[LLMDebug] LexicalRerankModel.rerank.input.top_n: {top_n}")
        print(f"[LLM] LexicalRerankModel document_count={len(documents or [])}")
        result = [self._score_one(query, d) for d in documents]
        print("[LLMDebug] LexicalRerankModel.rerank.output.scores:")
        print(json.dumps(result, ensure_ascii=False))
        return result


class HashRerankModel(RerankModelBase):
    def rerank(self, query: str, documents: List[str], top_n: Optional[int] = None) -> List[float]:
        print(f"[LLM] HashRerankModel query_chars={len(query or '')}")
        print(f"[LLMDebug] HashRerankModel.rerank.input.top_n: {top_n}")
        print(f"[LLM] HashRerankModel document_count={len(documents or [])}")
        scores: List[float] = []
        q = (query or "").encode("utf-8", errors="ignore")
        for d in documents:
            b = (d or "").encode("utf-8", errors="ignore")
            h = hashlib.md5(q + b).hexdigest()
            scores.append((int(h[:8], 16) % 1000) / 1000.0)
        print("[LLMDebug] HashRerankModel.rerank.output.scores:")
        print(json.dumps(scores, ensure_ascii=False))
        return scores
