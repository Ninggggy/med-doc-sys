from typing import Dict, List

import fitz
from agent.agent_backend.config.settings import settings
from agent.agent_backend.utils.parser.text_sanitizer import sanitize_parser_text

def _append_full_text(result: List[Dict], text: str, chunk_id: str, unit_type: str, page: int | None = None) -> None:
    normalized = sanitize_parser_text(text)
    if not normalized:
        return
    result.append(
        {
            "chunk_id": chunk_id,
            "page": page,
            "text": normalized,
            "tables": [],
            "image_paths": [],
            "unit_type": unit_type,
        }
    )


def _chunk_markdown(text: str, max_len: int = 250) -> List[str]:
    lines = [line.rstrip() for line in str(text or "").splitlines()]
    chunks: List[str] = []
    current: List[str] = []
    current_len = 0
    for line in lines:
        normalized = line.strip()
        if not normalized:
            if current:
                current.append("")
            continue
        is_heading = normalized.startswith("#")
        line_len = len(line) + 1
        if current and (current_len + line_len > max_len or (is_heading and current_len >= 300)):
            chunk = "\n".join(current).strip()
            if chunk:
                chunks.append(chunk)
            current = []
            current_len = 0
        current.append(line)
        current_len += line_len
    if current:
        chunk = "\n".join(current).strip()
        if chunk:
            chunks.append(chunk)
    return chunks


def _split_text_chunks(text: str, max_chars: int = 1200, overlap: int = 120) -> List[str]:
    value = sanitize_parser_text(text)
    if not value:
        return []
    if len(value) <= max_chars:
        return [value]

    paragraphs = [part.strip() for part in str(value).split("\n\n") if part.strip()]
    chunks: List[str] = []
    current = ""
    for para in paragraphs:
        candidate = para if not current else f"{current}\n\n{para}"
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
            if overlap > 0 and len(current) > overlap:
                current = f"{current[-overlap:].strip()}\n\n{para}".strip()
            else:
                current = para
            if len(current) <= max_chars:
                continue
        while len(para) > max_chars:
            chunks.append(para[:max_chars].strip())
            para = para[max(1, max_chars - overlap) :].strip()
        current = para
    if current:
        chunks.append(current)
    return [chunk for chunk in chunks if chunk]


def _flatten_leaf_sections(payload: Dict) -> List[Dict]:
    sections = payload.get("sections") or []
    if not isinstance(sections, list):
        return []
    has_child = {
        str(item.get("parent_section_id", "")).strip()
        for item in sections
        if isinstance(item, dict) and str(item.get("parent_section_id", "")).strip()
    }
    return [
        item
        for item in sections
        if isinstance(item, dict) and str(item.get("section_id", "")).strip() not in has_child
    ]


def _build_section_chunk_rows(payload: Dict, max_chars: int = 1200, overlap: int = 120) -> List[Dict]:
    rows: List[Dict] = []
    for section in _flatten_leaf_sections(payload):
        section_id = str(section.get("section_id", "")).strip()
        if not section_id:
            continue
        content = sanitize_parser_text(section.get("content", ""))
        if not content:
            continue

        title_path = [sanitize_parser_text(node) for node in (section.get("title_path") or []) if sanitize_parser_text(node)]
        if not title_path:
            fallback_title = sanitize_parser_text(
                section.get("section_title", "") or section.get("section_name", "") or section.get("title", "")
            )
            if fallback_title:
                title_path = [fallback_title]
        if not title_path:
            continue

        section_path_text = settings.kb_section_path_sep.join(title_path).strip()
        heading_text = "\n".join(title_path)
        base_text = f"{heading_text}\n\n{content}".strip()
        raw_pages = [page for page in (section.get("raw_pages") or []) if isinstance(page, int)]
        page_start = section.get("page_start") if isinstance(section.get("page_start"), int) else (min(raw_pages) if raw_pages else None)
        page_end = max(raw_pages) if raw_pages else page_start

        for idx, chunk_text in enumerate(_split_text_chunks(base_text, max_chars=max_chars, overlap=overlap), start=1):
            rows.append(
                {
                    "chunk_id": f"{section_id}_chunk_{idx}",
                    "page": page_start,
                    "page_start": page_start,
                    "page_end": page_end,
                    "text": chunk_text,
                    "tables": list(section.get("tables") or []),
                    "image_paths": [],
                    "unit_type": "leaf_section_chunk",
                    "section_id": section_id,
                    "section_name": title_path[-1],
                    "section_path": title_path,
                    "section_path_text": section_path_text,
                    "title_path": title_path,
                    "source_chunk_ids": [section_id],
                    "raw_pages": raw_pages,
                    "char_count": len(chunk_text),
                }
            )
    return rows


def _parse_pdf_via_structured_markdown(file_path: str) -> List[Dict]:
    # 保留旧调用入口，统一走有页码和诊断的共享提取路径。
    return parse_pdf(file_path)
def parse_pdf(file_path: str) -> List[Dict]:
    from agent.agent_backend.utils.parser.pdf_page_extractor import extract_pdf_pages
    return extract_pdf_pages(file_path)
