import json
import math
import os
import re
from typing import Dict, List, Optional, Tuple

from agent.agent_backend.memory.rag.schemas import Chunk, ParsedDocument, ParsedUnit
from agent.agent_backend.utils.parser import ParserManager


class DocumentProcessor:
    """Parse document -> compose full text -> rule-based chunking."""

    def __init__(
        self,
        target_chunk_chars: int = 220,
        max_chunk_chars: int = 400,
        min_chunk_chars: int = 80,
    ):
        self.target_chunk_chars = target_chunk_chars
        self.max_chunk_chars = max_chunk_chars
        self.min_chunk_chars = min_chunk_chars

    @staticmethod
    def _normalize_text(text: str) -> str:
        text = (text or "").replace("\u3000", " ")
        text = re.sub(r"\r\n?", "\n", text)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    @staticmethod
    def _detect_doc_type(file_path: str, file_type: str = "") -> str:
        ext = (file_type or "").strip().lower().lstrip(".")
        if not ext:
            ext = os.path.splitext(file_path)[1].lower().lstrip(".")
        return ext or "unknown"

    @staticmethod
    def _sentence_split(text: str) -> List[str]:
        if not text:
            return []
        parts = re.split(r"(?<=[。！？；.!?;])\s+|\n{2,}", text)
        return [part.strip() for part in parts if part and part.strip()]

    def _split_long_text(self, text: str, limit: Optional[int] = None) -> List[str]:
        max_len = int(limit or self.max_chunk_chars)
        text = self._normalize_text(text)
        if not text:
            return []
        if len(text) <= max_len:
            return [text]

        segments: List[str] = []
        current = ""
        for sentence in self._sentence_split(text):
            if len(sentence) > max_len:
                if current:
                    segments.append(current)
                    current = ""
                for idx in range(0, len(sentence), max_len):
                    piece = sentence[idx : idx + max_len].strip()
                    if piece:
                        segments.append(piece)
                continue
            if len(current) + len(sentence) + 1 <= max_len:
                current = f"{current} {sentence}".strip()
            else:
                if current:
                    segments.append(current)
                current = sentence
        if current:
            segments.append(current)
        return segments

    def parse_to_document(self, file_path: str, file_type: str = "", doc_id: str = "") -> ParsedDocument:
        raw_chunks = ParserManager.parse(file_path, ext_hint=file_type)
        units: List[ParsedUnit] = []
        for idx, item in enumerate(raw_chunks or [], start=1):
            if not isinstance(item, dict):
                continue
            text = self._normalize_text(str(item.get("text", "")))
            if not text:
                continue
            page = item.get("page_start", item.get("page"))
            section_title = (
                str(item.get("section_name", "") or "")
                or str(item.get("section_title", "") or "")
                or None
            )
            section_path = list(item.get("section_path") or item.get("title_path") or [])
            section_path_text = str(item.get("section_path_text", "") or "").strip()
            if not section_path_text and section_path:
                section_path_text = " > ".join([str(node).strip() for node in section_path if str(node).strip()])
            units.append(
                ParsedUnit(
                    unit_id=str(item.get("chunk_id", idx)),
                    text=text,
                    page_no=page if isinstance(page, int) else None,
                    section_title=section_title,
                    section_path=section_path,
                    unit_type=str(item.get("unit_type", "text") or "text"),
                    metadata={
                        **{k: item[k] for k in ('tables', 'lines', 'words', 'parse_diagnostics', 'errors', 'status', 'coordinate_unit', 'coordinate_origin', 'page_bbox', 'source_rotation', 'dpi', 'image_regions', 'ocr_regions', 'ocr_quality', 'unresolved_tables', 'native_text', 'path', 'tables_in_text') if k in item},
                        "source_chunk_id": str(item.get("chunk_id", idx)),
                        "page": page if isinstance(page, int) else None,
                        "page_start": item.get("page_start") if isinstance(item.get("page_start"), int) else (page if isinstance(page, int) else None),
                        "page_end": item.get("page_end") if isinstance(item.get("page_end"), int) else (page if isinstance(page, int) else None),
                        "section_id": str(item.get("section_id", "") or ""),
                        "section_name": section_title or "",
                        "section_path": section_path,
                        "section_path_text": section_path_text,
                        "source_chunk_ids": list(item.get("source_chunk_ids") or [str(item.get("chunk_id", idx))]),
                        "char_count": int(item.get("char_count", len(text) or 0)),
                    },
                )
            )

        parsed_doc = ParsedDocument(
            doc_id=doc_id or os.path.basename(file_path),
            doc_type=self._detect_doc_type(file_path, file_type),
            title=os.path.basename(file_path),
            source_path=file_path,
            raw_units=units,
            metadata={"unit_count": len(units), "source_pages": [r for r in raw_chunks if r.get('unit_type') == 'pdf_page']},
        )
        return parsed_doc

    @staticmethod
    def _is_prechunked_unit(unit: ParsedUnit) -> bool:
        return str(unit.unit_type or "").strip().lower() in {"leaf_section_chunk", "structured_section_chunk", "pdf_page"}

    def _passthrough_chunks(self, parsed_doc: ParsedDocument) -> List[Chunk]:
        chunks: List[Chunk] = []
        chunk_idx = 1
        for unit in parsed_doc.raw_units:
            text = self._normalize_text(unit.text)
            if not text:
                continue
            metadata = dict(unit.metadata or {})
            page_start = metadata.get("page_start") if isinstance(metadata.get("page_start"), int) else unit.page_no
            page_end = metadata.get("page_end") if isinstance(metadata.get("page_end"), int) else page_start
            source_chunk_ids = list(metadata.get("source_chunk_ids") or [metadata.get("source_chunk_id") or unit.unit_id])
            section_path = list(unit.section_path or metadata.get("section_path") or [])
            section_path_text = str(metadata.get("section_path_text", "") or "").strip()
            if not section_path_text and section_path:
                section_path_text = " > ".join([str(node).strip() for node in section_path if str(node).strip()])
            chunks.append(
                Chunk(
                    chunk_id=f"rag_{chunk_idx}",
                    doc_id=parsed_doc.doc_id,
                    doc_type=parsed_doc.doc_type,
                    text=text,
                    chunk_type="semantic",
                    section_id=str(metadata.get("section_id", "") or "") or None,
                    section_path=section_path,
                    page_start=page_start,
                    page_end=page_end,
                    token_count=len(text),
                    metadata={
                        **metadata,
                        "page": page_start,
                        "char_count": int(metadata.get("char_count", len(text))),
                        "source_chunk_ids": source_chunk_ids,
                        "section_name": unit.section_title or metadata.get("section_name"),
                        "section_path_text": section_path_text,
                        "unit_type": unit.unit_type or metadata.get("unit_type", "text"),
                    },
                )
            )
            chunk_idx += 1
        return chunks

    def _compose_full_text(self, parsed_doc: ParsedDocument) -> Tuple[str, List[Dict]]:
        full_parts: List[str] = []
        spans: List[Dict] = []
        cursor = 0
        for index, unit in enumerate(parsed_doc.raw_units):
            if index > 0:
                sep = "\n\n"
                full_parts.append(sep)
                cursor += len(sep)
            start = cursor
            full_parts.append(unit.text)
            cursor += len(unit.text)
            spans.append(
                {
                    "start": start,
                    "end": cursor,
                    "page_no": unit.page_no,
                    "unit_id": unit.unit_id,
                    "section_title": unit.section_title,
                    "section_path": list(unit.section_path),
                    "unit_type": unit.unit_type,
                }
            )
        full_text = "".join(full_parts)
        return full_text, spans

    def _llm_chunk_once(self, text: str) -> List[str]:
        text = self._normalize_text(text)
        if not text:
            return []
        return self._split_long_text(text, limit=self.max_chunk_chars)

    def _is_valid_segments(self, segments: List[str], full_text: str) -> bool:
        if not segments:
            return False
        cleaned = [self._normalize_text(x) for x in segments if self._normalize_text(x)]
        if not cleaned:
            return False
        total = sum(len(x) for x in cleaned)
        ratio = total / max(1, len(full_text))
        return 0.45 <= ratio <= 1.8

    def _merge_small_segments(self, segments: List[str]) -> List[str]:
        output: List[str] = []
        current = ""
        for seg in [self._normalize_text(x) for x in segments if self._normalize_text(x)]:
            if not current:
                current = seg
                continue
            if len(current) < self.min_chunk_chars:
                current = f"{current} {seg}".strip()
                if len(current) > self.max_chunk_chars:
                    output.extend(self._split_long_text(current, limit=self.max_chunk_chars))
                    current = ""
                continue
            output.append(current)
            current = seg
        if current:
            if output and len(current) < self.min_chunk_chars:
                output[-1] = f"{output[-1]} {current}".strip()
            else:
                output.append(current)
        final: List[str] = []
        for item in output:
            if len(item) > self.max_chunk_chars:
                final.extend(self._split_long_text(item, limit=self.max_chunk_chars))
            else:
                final.append(item)
        return final

    def _semantic_chunk_full_text(self, full_text: str) -> List[str]:
        full_text = self._normalize_text(full_text)
        if not full_text:
            return []

        budget = max(4000, self.max_chunk_chars * 8)
        if len(full_text) <= budget:
            segments = self._llm_chunk_once(full_text)
            if self._is_valid_segments(segments, full_text):
                return self._merge_small_segments(segments)
            return self._merge_small_segments(self._split_long_text(full_text, limit=self.max_chunk_chars))

        merged_segments: List[str] = []
        for idx in range(0, len(full_text), budget):
            piece = full_text[idx : idx + budget]
            piece_segments = self._llm_chunk_once(piece)
            if not self._is_valid_segments(piece_segments, piece):
                piece_segments = self._split_long_text(piece, limit=self.max_chunk_chars)
            merged_segments.extend(piece_segments)
        return self._merge_small_segments(merged_segments)

    @staticmethod
    def _overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> int:
        return max(0, min(a_end, b_end) - max(a_start, b_start))

    def _segment_meta_by_ratio(self, segments: List[str], spans: List[Dict], full_text_len: int) -> List[Dict]:
        if not segments:
            return []
        total_seg_len = max(1, sum(len(seg) for seg in segments))
        consumed = 0
        mapped: List[Dict] = []

        for seg in segments:
            seg_len = max(1, len(seg))
            start = int(math.floor(full_text_len * consumed / total_seg_len))
            consumed += seg_len
            end = int(math.floor(full_text_len * consumed / total_seg_len))
            if end <= start:
                end = min(full_text_len, start + seg_len)

            touched = []
            for span in spans:
                overlap = self._overlap(start, end, int(span["start"]), int(span["end"]))
                if overlap > 0:
                    touched.append((overlap, span))
            touched.sort(key=lambda item: (-item[0], int(item[1]["start"])))
            span_rows = [item[1] for item in touched]

            pages = [row.get("page_no") for row in span_rows if isinstance(row.get("page_no"), int)]
            source_ids = [str(row.get("unit_id")) for row in span_rows if row.get("unit_id")]
            section_path: List[str] = []
            for row in span_rows:
                for node in row.get("section_path") or []:
                    node = str(node).strip()
                    if node and node not in section_path:
                        section_path.append(node)

            mapped.append(
                {
                    "text": seg,
                    "page_start": min(pages) if pages else None,
                    "page_end": max(pages) if pages else None,
                    "source_chunk_ids": sorted(set(source_ids)),
                    "section_path": section_path,
                    "section_path_text": " > ".join(section_path) if section_path else "",
                    "section_title": next((row.get("section_title") for row in span_rows if row.get("section_title")), None),
                    "unit_type": next((row.get("unit_type") for row in span_rows if row.get("unit_type")), "text"),
                }
            )
        return mapped

    def chunk_document(self, parsed_doc: ParsedDocument) -> List[Chunk]:
        if parsed_doc.raw_units and all(self._is_prechunked_unit(unit) for unit in parsed_doc.raw_units):
            chunks = self._passthrough_chunks(parsed_doc)
            return chunks

        grouped_units: List[List[ParsedUnit]] = []
        current_group: List[ParsedUnit] = []
        current_key: Tuple[str, ...] | None = None

        for unit in parsed_doc.raw_units:
            key = tuple(unit.section_path or ([unit.section_title] if unit.section_title else []))
            if current_group and key != current_key:
                grouped_units.append(current_group)
                current_group = []
            current_group.append(unit)
            current_key = key
        if current_group:
            grouped_units.append(current_group)

        if not grouped_units:
            return []

        chunks: List[Chunk] = []
        chunk_idx = 1
        for group in grouped_units:
            sub_doc = ParsedDocument(
                doc_id=parsed_doc.doc_id,
                doc_type=parsed_doc.doc_type,
                title=parsed_doc.title,
                source_path=parsed_doc.source_path,
                raw_units=group,
                metadata=parsed_doc.metadata,
            )
            full_text, spans = self._compose_full_text(sub_doc)
            if not full_text:
                continue

            segments = self._semantic_chunk_full_text(full_text)
            if not segments:
                segments = self._split_long_text(full_text, limit=self.max_chunk_chars)
            segments = self._merge_small_segments(segments)
            meta_rows = self._segment_meta_by_ratio(segments, spans, len(full_text))

            for row in meta_rows:
                text = self._normalize_text(str(row.get("text", "")))
                if not text:
                    continue
                chunks.append(
                    Chunk(
                        chunk_id=f"rag_{chunk_idx}",
                        doc_id=parsed_doc.doc_id,
                        doc_type=parsed_doc.doc_type,
                        text=text,
                        chunk_type="semantic",
                        section_id=None,
                        section_path=list(row.get("section_path") or []),
                        page_start=row.get("page_start"),
                        page_end=row.get("page_end"),
                        token_count=len(text),
                        metadata={
                            "page": row.get("page_start"),
                            "char_count": len(text),
                            "source_chunk_ids": list(row.get("source_chunk_ids") or []),
                            "section_name": row.get("section_title"),
                            "section_path_text": str(row.get("section_path_text", "") or "").strip(),
                            "unit_type": row.get("unit_type", "text"),
                        },
                    )
                )
                chunk_idx += 1
        return chunks

    def process(self, file_path: str, file_type: str = "", doc_id: str = "") -> List[Dict]:
        parsed_doc = self.parse_to_document(file_path=file_path, file_type=file_type, doc_id=doc_id)
        chunks = self.chunk_document(parsed_doc)
        return [chunk.to_dict() for chunk in chunks]
