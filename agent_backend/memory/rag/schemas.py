from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ParsedUnit:
    unit_id: str
    text: str
    page_no: Optional[int] = None
    section_title: Optional[str] = None
    section_path: List[str] = field(default_factory=list)
    unit_type: str = "text"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "unit_id": self.unit_id,
            "text": self.text,
            "page_no": self.page_no,
            "section_title": self.section_title,
            "section_path": list(self.section_path),
            "unit_type": self.unit_type,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ParsedUnit":
        return cls(
            unit_id=str(data.get("unit_id", "")),
            text=str(data.get("text", "")),
            page_no=data.get("page_no"),
            section_title=data.get("section_title"),
            section_path=list(data.get("section_path") or []),
            unit_type=str(data.get("unit_type", "text")),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass
class ParsedDocument:
    doc_id: str
    doc_type: str
    title: str
    source_path: str = ""
    version: Optional[str] = None
    raw_units: List[ParsedUnit] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def get_full_text(self) -> str:
        return "\n\n".join([u.text for u in self.raw_units if u.text]).strip()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "doc_type": self.doc_type,
            "title": self.title,
            "source_path": self.source_path,
            "version": self.version,
            "raw_units": [u.to_dict() for u in self.raw_units],
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ParsedDocument":
        return cls(
            doc_id=str(data.get("doc_id", "")),
            doc_type=str(data.get("doc_type", "unknown")),
            title=str(data.get("title", "")),
            source_path=str(data.get("source_path", "")),
            version=data.get("version"),
            raw_units=[ParsedUnit.from_dict(x) for x in (data.get("raw_units") or []) if isinstance(x, dict)],
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    doc_type: str
    text: str
    chunk_type: str = "semantic"
    section_id: Optional[str] = None
    section_path: List[str] = field(default_factory=list)
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    token_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = {
            "chunk_id": self.chunk_id,
            "text": self.text,
            "chunk_type": self.chunk_type,
            "section_id": self.section_id,
            "section_path": list(self.section_path),
            "page": self.page_start,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "token_count": self.token_count,
        }
        data.update(self.metadata)
        return data


@dataclass
class RetrievalHit:
    chunk_id: str
    doc_id: str
    doc_title: str
    doc_type: str
    text: str
    section_path: List[str] = field(default_factory=list)
    page_span: Optional[str] = None
    vector_score: float = 0.0
    lexical_score: float = 0.0
    rerank_score: float = 0.0
    final_score: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "doc_id": self.doc_id,
            "doc_title": self.doc_title,
            "doc_type": self.doc_type,
            "text": self.text,
            "section_path": list(self.section_path),
            "page_span": self.page_span,
            "vector_score": self.vector_score,
            "lexical_score": self.lexical_score,
            "rerank_score": self.rerank_score,
            "final_score": self.final_score,
            "metadata": dict(self.metadata),
        }


@dataclass
class RetrievalContext:
    query: str
    rewritten_queries: List[str] = field(default_factory=list)
    hits: List[RetrievalHit] = field(default_factory=list)
    references: List[Dict[str, Any]] = field(default_factory=list)
    evidence_blocks: List[Dict[str, Any]] = field(default_factory=list)
    grouped_docs: List[Dict[str, Any]] = field(default_factory=list)
    used_tokens: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "rewritten_queries": list(self.rewritten_queries),
            "hits": [h.to_dict() for h in self.hits],
            "references": list(self.references),
            "evidence_blocks": list(self.evidence_blocks),
            "grouped_docs": list(self.grouped_docs),
            "used_tokens": self.used_tokens,
            "metadata": dict(self.metadata),
        }
