import json
import re
from collections import Counter
from typing import Any, Callable, Dict, List, Optional

from agent.agent_backend.context.builder import ContextBuilder
from agent.agent_backend.memory.embedding import EmbeddingService
from agent.agent_backend.memory.rag.document import DocumentProcessor
from agent.agent_backend.memory.rag.schemas import RetrievalContext, RetrievalHit
from agent.agent_backend.memory.storage.vector_store import VectorStore
from agent.agent_backend.database.mysql.mysql_conn import MysqlConnection
from agent.agent_backend.services.knowledge_source_validity import valid_source_ids


class RAGPipeline:
    """Parse -> semantic chunks -> enrich -> embed -> retrieve -> build context."""

    def __init__(self):
        self.processor = DocumentProcessor()
        self.embedding = EmbeddingService()
        self.store = VectorStore()
        self.ctx_builder = ContextBuilder()
        self._indexed_docs: set[str] = set()
        self.source_connection = MysqlConnection()

    def _valid_sources(self, doc_ids, filters=None):
        # 每次读取主表，不使用索引或进程缓存代替资料的当前有效状态。
        return valid_source_ids(self.source_connection, doc_ids, filters)

    @staticmethod
    def _query_terms(query: str) -> List[str]:
        query = (query or "").lower()
        terms = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", query)
        return [term for term in terms if term]

    @staticmethod
    def _lexical_score(query_terms: List[str], text: str) -> float:
        if not query_terms:
            return 0.0
        low = (text or "").lower()
        hit = sum(1 for term in query_terms if term in low)
        return hit / len(query_terms)

    @staticmethod
    def _parse_chunk_order(chunk_id: str) -> int:
        match = re.search(r"(\d+)$", str(chunk_id or ""))
        return int(match.group(1)) if match else 10**9

    @staticmethod
    def _split_text_hard(text: str, limit: int = 250) -> List[str]:
        value = str(text or "").strip()
        if not value:
            return []
        if len(value) <= limit:
            return [value]
        parts: List[str] = []
        for idx in range(0, len(value), limit):
            piece = value[idx : idx + limit].strip()
            if piece:
                parts.append(piece)
        return parts

    @staticmethod
    def _extract_keywords(text: str, limit: int = 8) -> List[str]:
        tokens = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9_/-]{2,}", str(text or ""))
        stopwords = {
            "should", "shall", "with", "from", "that", "this", "into", "using", "used",
            "content", "document", "section", "chapter", "method", "result", "standard",
        }
        counter: Counter[str] = Counter()
        for token in tokens:
            clean = str(token).strip()
            if not clean:
                continue
            if clean.lower() in stopwords:
                continue
            counter[clean] += 1
        return [item for item, _ in counter.most_common(limit)]

    @classmethod
    def _build_chunk_summary(cls, text: str, limit: int = 120) -> str:
        normalized = " ".join(str(text or "").split())
        if not normalized:
            return ""
        if len(normalized) <= limit:
            return normalized
        return normalized[: limit - 3].rstrip() + "..."

    @classmethod
    def _build_document_summary(cls, title: str, full_text: str, max_segments: int = 4) -> str:
        title_text = str(title or "").strip()
        segments = []
        for piece in re.split(r"\n{2,}|(?<=[\u3002\uff01\uff1f\uff1b.!?;])\s+", str(full_text or "")):
            clean = " ".join(piece.split()).strip()
            if clean:
                segments.append(clean)
            if len(segments) >= max_segments:
                break
        body = " ".join(segments).strip()
        if title_text and body:
            return cls._build_chunk_summary(f"{title_text} {body}", limit=400)
        return cls._build_chunk_summary(title_text or body, limit=400)

    @staticmethod
    def _build_doc_summary_index_text(title: str, summary: str) -> str:
        title_text = str(title or "").strip()
        summary_text = str(summary or "").strip()
        if title_text and summary_text:
            return f"{title_text}\n\n{summary_text}"
        return title_text or summary_text

    def _doc_related_chunks(self, doc_id: str, anchor_chunk_id: str = "", window: int = 2, limit: int = 6) -> List[Dict[str, Any]]:
        rows = self.store.list_by_doc(doc_id)
        chunks = [row for row in rows
                  if (row.get("metadata") or {}).get("item_type") == "chunk"
                  and (row.get("metadata") or {}).get("doc_id") == doc_id]
        if not chunks:
            return []
        anchor_order = self._parse_chunk_order(anchor_chunk_id) if anchor_chunk_id else None
        selected = chunks
        if anchor_order is not None and anchor_order < 10**9:
            selected = [
                row
                for row in chunks
                if abs(int((row.get("metadata") or {}).get("chunk_order", 10**9)) - anchor_order) <= window
            ] or chunks
        output: List[Dict[str, Any]] = []
        for row in selected[:limit]:
            metadata = row.get("metadata") or {}
            output.append(
                {
                    "chunk_id": metadata.get("chunk_id"),
                    "chunk_order": metadata.get("chunk_order"),
                    "page": metadata.get("page"),
                    "page_start": metadata.get("page_start"),
                    "page_end": metadata.get("page_end"),
                    "summary": metadata.get("summary", ""),
                    "text": str(row.get("text", ""))[:500],
                }
            )
        return output

    def _doc_summary_meta(self, doc_id: str) -> Dict[str, Any]:
        rows = self.store.list_by_doc(doc_id)
        for row in rows:
            metadata = row.get("metadata") or {}
            if metadata.get("item_type") == "doc_summary" and metadata.get("doc_id") == doc_id:
                return {
                    "summary": metadata.get("summary", ""),
                    "keywords": metadata.get("keywords", []),
                    "text": row.get("text", ""),
                }
        return {"summary": "", "keywords": [], "text": ""}

    def index_file(
        self,
        file_path: str,
        doc_id: str,
        classification: str = "",
        force: bool = False,
        file_type: str = "",
        parsed_output_path: str = "",
        progress_callback: Optional[Callable[[float, str], None]] = None,
    ) -> int:
        if (doc_id in self._indexed_docs) and not force:
            return 0
        if force:
            self.store.delete_by_doc(doc_id)

        if progress_callback:
            progress_callback(0.05, "start coarse parse and chunking")

        parsed_doc = self.processor.parse_to_document(file_path=file_path, file_type=file_type, doc_id=doc_id)
        full_text = parsed_doc.get_full_text()
        if not full_text.strip():
            if parsed_output_path and parsed_doc.metadata.get('source_pages'):
                with open(parsed_output_path, 'w', encoding='utf-8') as fp:
                    json.dump(parsed_doc.metadata['source_pages'], fp, ensure_ascii=False, indent=2)
            raise ValueError(f"parse produced empty full text for doc_id={doc_id}, file_path={file_path}")
        chunks: List[Dict[str, Any]] = []
        fallback_chunks = self.processor.chunk_document(parsed_doc)
        for chunk in fallback_chunks:
            chunks.append(
                {
                    "chunk_id": chunk.chunk_id,
                    "text": chunk.text,
                    "summary": "",
                    "keywords": [],
                    "source_metadata": chunk.metadata,
                    "section_id": chunk.section_id,
                    "section_path": list(chunk.section_path),
                    "section_path_text": str(chunk.metadata.get("section_path_text", "") or ""),
                    "section_name": chunk.metadata.get("section_name"),
                    "page_start": chunk.page_start,
                    "page_end": chunk.page_end,
                    "unit_type": chunk.metadata.get("unit_type") or "semantic_chunk",
                    "source_chunk_ids": chunk.metadata.get("source_chunk_ids", []),
                    "char_count": chunk.metadata.get("char_count", len(chunk.text)),
                }
            )

        if progress_callback:
            progress_callback(0.2, f"coarse parse done, chunks={len(chunks)}")

        parsed_rows: List[Dict[str, Any]] = []
        total = max(1, len(chunks))
        for idx, chunk in enumerate(chunks, start=1):
            text = str(chunk.get("text", "")).strip()
            if not text:
                continue
            summary = str(chunk.get("summary", "")).strip()
            keywords = chunk.get("keywords", [])
            if not isinstance(keywords, list):
                keywords = []
            if not summary:
                summary = self._build_chunk_summary(text)
            if not keywords:
                keywords = self._extract_keywords(text)

            keyword_text = " ".join([str(item).strip() for item in keywords if str(item).strip()])
            section_path = list(chunk.get("section_path") or [])
            section_path_text = str(chunk.get("section_path_text", "") or "").strip()
            if not section_path_text and section_path:
                section_path_text = " > ".join([str(item).strip() for item in section_path if str(item).strip()])
            vector = self.embedding.embed(text)
            item_key = f"{doc_id}:{chunk['chunk_id']}"
            metadata = {
                "doc_id": doc_id,
                "doc_title": parsed_doc.title,
                "doc_type": parsed_doc.doc_type,
                "chunk_id": chunk["chunk_id"],
                "item_type": "chunk",
                "chunk_order": self._parse_chunk_order(chunk["chunk_id"]),
                "page": chunk.get("page_start"),
                "page_start": chunk.get("page_start"),
                "page_end": chunk.get("page_end"),
                "section_id": chunk.get("section_id"),
                "section_path": section_path,
                "section_path_text": section_path_text,
                "section_name": chunk.get("section_name"),
                "unit_type": chunk.get("unit_type"),
                "char_count": chunk.get("char_count", len(text)),
                "source_chunk_ids": chunk.get("source_chunk_ids", []),
                "classification": classification,
                "summary": summary,
                "keywords": keywords,
                "keyword_text": keyword_text,
            }
            self.store.add(item_key, vector, text, metadata=metadata)

            row = {
                **{k: v for k, v in chunk.get('source_metadata', {}).items() if k in ('tables', 'lines', 'words', 'parse_diagnostics', 'errors', 'status', 'coordinate_unit', 'coordinate_origin', 'page_bbox', 'source_rotation', 'dpi', 'image_regions', 'ocr_regions', 'ocr_quality', 'unresolved_tables', 'native_text', 'path', 'tables_in_text')},
                "chunk_id": chunk["chunk_id"],
                "text": text,
                "chunk_type": "semantic",
                "section_id": chunk.get("section_id"),
                "section_path": section_path,
                "section_path_text": section_path_text,
                "page": chunk.get("page_start"),
                "page_start": chunk.get("page_start"),
                "page_end": chunk.get("page_end"),
                "token_count": 0,
            }
            row.update(
                {
                    "classification": classification,
                    "summary": summary,
                    "keywords": keywords,
                    "doc_id": doc_id,
                    "doc_title": parsed_doc.title,
                    "doc_type": parsed_doc.doc_type,
                }
            )
            parsed_rows.append(row)
            if progress_callback:
                progress_callback(0.2 + (0.7 * idx / total), f"enrich chunk {idx}/{total}")

        if progress_callback:
            progress_callback(0.92, "build document summary")
        doc_summary_text = self._build_document_summary(parsed_doc.title, full_text)
        doc_keywords = self._extract_keywords(full_text)
        if not chunks and not doc_summary_text:
            raise ValueError(f"no chunks and no document summary generated for doc_id={doc_id}")
        if doc_summary_text:
            doc_summary_index_text = self._build_doc_summary_index_text(parsed_doc.title, doc_summary_text)
            doc_vector = self.embedding.embed(doc_summary_index_text)
            self.store.add(
                f"{doc_id}:__doc_summary__",
                doc_vector,
                doc_summary_index_text,
                metadata={
                    "doc_id": doc_id,
                    "doc_title": parsed_doc.title,
                    "doc_type": parsed_doc.doc_type,
                    "chunk_id": "__doc_summary__",
                    "item_type": "doc_summary",
                    "chunk_order": 0,
                    "classification": classification,
                    "summary": doc_summary_text,
                    "keywords": doc_keywords,
                    "keyword_text": " ".join([str(item).strip() for item in doc_keywords if str(item).strip()]),
                },
            )

        artifact_rows = [
            {
                "chunk_id": "__doc_summary__",
                "item_type": "doc_summary",
                "doc_id": doc_id,
                "doc_title": parsed_doc.title,
                "doc_type": parsed_doc.doc_type,
                "summary": doc_summary_text,
                "keywords": doc_keywords,
                "text": self._build_doc_summary_index_text(parsed_doc.title, doc_summary_text),
            }
        ]
        artifact_rows.extend(parsed_rows)
        # 无文字的失败页也必须随解析文件保存，不能被向量化过滤后消失。
        artifact_rows.extend(p for p in parsed_doc.metadata.get('source_pages', []) if not p.get('text'))

        if parsed_output_path:
            if progress_callback:
                progress_callback(0.97, "write parsed artifact")
            with open(parsed_output_path, "w", encoding="utf-8") as fp:
                json.dump(artifact_rows, fp, ensure_ascii=False, indent=2)
        self._indexed_docs.add(doc_id)
        if progress_callback:
            progress_callback(1.0, "knowledge indexing completed")
        return len(chunks)

    def index_preparsed(
        self,
        doc_id: str,
        parsed_rows: List[Dict[str, Any]],
        classification: str = "",
        force: bool = False,
    ) -> int:
        if (doc_id in self._indexed_docs) and not force:
            return 0
        if force:
            self.store.delete_by_doc(doc_id)

        count = 0
        for row in parsed_rows or []:
            if not isinstance(row, dict):
                continue
            chunk_id = str(row.get("chunk_id", "")).strip()
            if not chunk_id:
                continue
            item_type = str(row.get("item_type", "chunk") or "chunk")
            text = str(row.get("text", "")).strip()
            if not text:
                continue

            doc_title = str(row.get("doc_title", doc_id) or doc_id)
            doc_type = str(row.get("doc_type", "unknown") or "unknown")

            if item_type == "doc_summary":
                summary = str(row.get("summary", "") or text).strip()
                keywords = row.get("keywords", [])
                if not isinstance(keywords, list):
                    keywords = []
                summary_index_text = self._build_doc_summary_index_text(doc_title, summary)
                vector = self.embedding.embed(summary_index_text)
                self.store.add(
                    f"{doc_id}:__doc_summary__",
                    vector,
                    summary_index_text,
                    metadata={
                        "doc_id": doc_id,
                        "doc_title": doc_title,
                        "doc_type": doc_type,
                        "chunk_id": "__doc_summary__",
                        "item_type": "doc_summary",
                        "chunk_order": 0,
                        "classification": classification,
                        "summary": summary,
                        "keywords": keywords,
                        "keyword_text": " ".join([str(item).strip() for item in keywords if str(item).strip()]),
                    },
                )
                count += 1
                continue

            summary = str(row.get("summary", "")).strip()
            keywords = row.get("keywords", [])
            if not isinstance(keywords, list):
                keywords = []
            vector = self.embedding.embed(text)
            self.store.add(
                f"{doc_id}:{chunk_id}",
                vector,
                text,
                metadata={
                    "doc_id": doc_id,
                    "doc_title": doc_title,
                    "doc_type": doc_type,
                    "chunk_id": chunk_id,
                    "item_type": "chunk",
                    "chunk_order": self._parse_chunk_order(chunk_id),
                    "page": row.get("page"),
                    "page_start": row.get("page_start", row.get("page")),
                    "page_end": row.get("page_end", row.get("page")),
                    "section_id": row.get("section_id"),
                    "section_path": list(row.get("section_path") or []),
                    "section_name": row.get("section_name"),
                    "unit_type": row.get("unit_type"),
                    "char_count": row.get("char_count", len(text)),
                    "source_chunk_ids": row.get("source_chunk_ids", []),
                    "classification": classification,
                    "summary": summary,
                    "keywords": keywords,
                    "keyword_text": " ".join([str(item).strip() for item in keywords if str(item).strip()]),
                },
            )
            count += 1

        if count > 0:
            self._indexed_docs.add(doc_id)
        return count

    def retrieve(
        self,
        query: str,
        top_k: int = 5,
        alpha: float = 0.7,
        min_score: float = 0.0,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        query_vector = self.embedding.embed(query)
        vector_hits = self.store.search(query_vector, top_k=max(top_k * 4, 20), filters=filters or {})
        keyword_hits = self.store.keyword_search(query=query, top_k=max(top_k * 4, 20), filters=filters or {})
        terms = self._query_terms(query)
        vector_map = {str(hit.get("id", "")): hit for hit in vector_hits if str(hit.get("id", ""))}
        keyword_map = {str(hit.get("id", "")): hit for hit in keyword_hits if str(hit.get("id", ""))}
        merged_ids: List[str] = []
        for item in vector_hits + keyword_hits:
            hit_id = str(item.get("id", ""))
            if hit_id and hit_id not in merged_ids:
                merged_ids.append(hit_id)

        typed_hits: List[RetrievalHit] = []
        for hit_id in merged_ids:
            hit = vector_map.get(hit_id) or keyword_map.get(hit_id) or {}
            metadata = hit.get("metadata") or {}
            text_value = str(hit.get("text", ""))
            text_lex = self._lexical_score(terms, text_value)
            summary_lex = self._lexical_score(terms, str(metadata.get("summary", "")))
            keyword_lex = self._lexical_score(terms, str(metadata.get("keyword_text", "")))
            lexical = min(1.0, 0.55 * text_lex + 0.20 * summary_lex + 0.25 * keyword_lex)
            vector_score = float((vector_map.get(hit_id) or {}).get("score", 0.0))
            keyword_score = float((keyword_map.get(hit_id) or {}).get("score", 0.0))
            final_score = alpha * vector_score + (1 - alpha) * max(keyword_score, lexical)
            page_start = metadata.get("page_start")
            page_end = metadata.get("page_end")
            page_span = ""
            if page_start is not None and page_end is not None:
                page_span = f"{page_start}-{page_end}" if page_start != page_end else str(page_start)
            elif page_start is not None:
                page_span = str(page_start)

            typed_hits.append(
                RetrievalHit(
                    chunk_id=str(metadata.get("chunk_id", "")),
                    doc_id=str(metadata.get("doc_id", "")),
                    doc_title=str(metadata.get("doc_title", metadata.get("doc_id", ""))),
                    doc_type=str(metadata.get("doc_type", "unknown")),
                    text=text_value,
                    section_path=list(metadata.get("section_path") or []),
                    page_span=page_span or None,
                    vector_score=vector_score,
                    lexical_score=max(keyword_score, lexical),
                    rerank_score=0.0,
                    final_score=final_score,
                    metadata={
                        "id": hit_id,
                        "item_type": metadata.get("item_type", "chunk"),
                        "chunk_order": metadata.get("chunk_order"),
                        "page": metadata.get("page"),
                        "page_start": page_start,
                        "page_end": page_end,
                        "section_id": metadata.get("section_id"),
                        "section_name": metadata.get("section_name"),
                        "unit_type": metadata.get("unit_type"),
                        "char_count": metadata.get("char_count"),
                        "classification": metadata.get("classification"),
                        "summary": metadata.get("summary"),
                        "keywords": metadata.get("keywords") or [],
                        "match_routes": [
                            route
                            for route, present in (("vector", hit_id in vector_map), ("keyword", hit_id in keyword_map))
                            if present
                        ],
                    },
                )
            )

        typed_hits.sort(key=lambda item: item.final_score, reverse=True)
        active_ids = self._valid_sources([hit.doc_id for hit in typed_hits], filters)
        filtered = [hit for hit in typed_hits if hit.doc_id in active_ids]
        top_hits = filtered[:top_k]

        output: List[Dict[str, Any]] = []
        for hit in top_hits:
            doc_meta = self._doc_summary_meta(hit.doc_id) if hit.doc_id else {"summary": "", "keywords": []}
            anchor_chunk = "" if hit.metadata.get("item_type") == "doc_summary" else hit.chunk_id
            output.append(
                {
                    "id": hit.metadata.get("id"),
                    "doc_id": hit.doc_id,
                    "doc_title": hit.doc_title,
                    "doc_type": hit.doc_type,
                    "chunk_id": hit.chunk_id,
                    "item_type": hit.metadata.get("item_type", "chunk"),
                    "chunk_order": hit.metadata.get("chunk_order"),
                    "page": hit.metadata.get("page"),
                    "page_start": hit.metadata.get("page_start"),
                    "page_end": hit.metadata.get("page_end"),
                    "section_id": hit.metadata.get("section_id"),
                    "section_code": None,
                    "section_name": hit.metadata.get("section_name"),
                    "section_path": hit.section_path,
                    "unit_type": hit.metadata.get("unit_type"),
                    "char_count": hit.metadata.get("char_count"),
                    "classification": hit.metadata.get("classification"),
                    "summary": hit.metadata.get("summary"),
                    "keywords": hit.metadata.get("keywords") or [],
                    "vector_score": hit.vector_score,
                    "lexical_score": hit.lexical_score,
                    "rerank_score": 0.0,
                    "score": hit.final_score,
                    "match_routes": hit.metadata.get("match_routes") or [],
                    "text": hit.text,
                    "doc_summary": doc_meta.get("summary", ""),
                    "doc_keywords": doc_meta.get("keywords", []),
                    "related_chunks": self._doc_related_chunks(doc_id=hit.doc_id, anchor_chunk_id=anchor_chunk),
                }
            )
        active_ids = self._valid_sources([hit["doc_id"] for hit in output], filters)
        return [hit for hit in output if hit["doc_id"] in active_ids]

    def build_context(
        self,
        query: str,
        top_k: int = 6,
        alpha: float = 0.75,
        min_score: float = 0.0,
        max_chars: int = 1600,
        filters: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        hits = self.retrieve(query=query, top_k=top_k, alpha=alpha, min_score=min_score, filters=filters)
        active_ids = self._valid_sources([hit.get("doc_id") for hit in hits], filters)
        hits = [hit for hit in hits if hit.get("doc_id") in active_ids]
        sources = [
            {
                "text": hit["text"],
                "score": hit["score"],
                "source": f"{hit.get('doc_id')}/{hit.get('chunk_id')}",
                "metadata": {
                    "doc_id": hit.get("doc_id"),
                    "doc_title": hit.get("doc_title"),
                    "chunk_id": hit.get("chunk_id"),
                    "page_start": hit.get("page_start"),
                    "page_end": hit.get("page_end"),
                    "section_path": hit.get("section_path") or [],
                },
            }
            for hit in hits
        ]
        gssc = self.ctx_builder.build(sources=sources, max_items=top_k)
        compressed = gssc.compressed[:max_chars]

        grouped_docs: Dict[str, Dict[str, Any]] = {}
        for hit in hits:
            doc_id = str(hit.get("doc_id", "") or "")
            if not doc_id:
                continue
            if doc_id not in grouped_docs:
                grouped_docs[doc_id] = {
                    "doc_id": doc_id,
                    "doc_title": hit.get("doc_title", ""),
                    "doc_type": hit.get("doc_type", ""),
                    "doc_summary": hit.get("doc_summary", ""),
                    "doc_keywords": hit.get("doc_keywords", []),
                    "matched_hits": [],
                    "related_chunks": hit.get("related_chunks", []),
                }
            grouped_docs[doc_id]["matched_hits"].append(
                {
                    "chunk_id": hit.get("chunk_id"),
                    "item_type": hit.get("item_type", "chunk"),
                    "score": hit.get("score"),
                    "summary": hit.get("summary", ""),
                    "page_start": hit.get("page_start"),
                    "page_end": hit.get("page_end"),
                    "section_name": hit.get("section_name"),
                }
            )

        typed_context = RetrievalContext(
            query=query,
            rewritten_queries=[query],
            hits=[
                RetrievalHit(
                    chunk_id=str(hit.get("chunk_id", "")),
                    doc_id=str(hit.get("doc_id", "")),
                    doc_title=str(hit.get("doc_title", "")),
                    doc_type=str(hit.get("doc_type", "")),
                    text=str(hit.get("text", "")),
                    section_path=list(hit.get("section_path") or []),
                    page_span=(
                        f"{hit.get('page_start')}-{hit.get('page_end')}"
                        if hit.get("page_start") is not None and hit.get("page_end") is not None and hit.get("page_start") != hit.get("page_end")
                        else str(hit.get("page_start"))
                        if hit.get("page_start") is not None
                        else None
                    ),
                    vector_score=float(hit.get("vector_score", 0.0)),
                    lexical_score=float(hit.get("lexical_score", 0.0)),
                    rerank_score=float(hit.get("rerank_score", 0.0)),
                    final_score=float(hit.get("score", 0.0)),
                    metadata={
                        "classification": hit.get("classification"),
                        "summary": hit.get("summary"),
                        "keywords": hit.get("keywords") or [],
                    },
                )
                for hit in hits
            ],
            references=[
                {
                    "doc_id": hit.get("doc_id"),
                    "doc_title": hit.get("doc_title"),
                    "chunk_id": hit.get("chunk_id"),
                    "page_start": hit.get("page_start"),
                    "page_end": hit.get("page_end"),
                    "score": hit.get("score"),
                }
                for hit in hits
            ],
            evidence_blocks=sources,
            grouped_docs=list(grouped_docs.values()),
            used_tokens=len(compressed),
            metadata={"filters": filters or {}, "top_k": top_k},
        )
        result = typed_context.to_dict()
        result["context"] = compressed
        return result
