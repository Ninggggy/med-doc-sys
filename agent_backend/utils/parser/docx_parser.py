from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from agent.agent_backend.config.settings import settings
from agent.agent_backend.utils.parser.text_sanitizer import sanitize_parser_text


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


def _walk_sections(nodes: List[Dict[str, Any]], parent_path: List[str] | None = None) -> List[Dict[str, Any]]:
    output: List[Dict[str, Any]] = []
    parent_path = list(parent_path or [])
    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        title = sanitize_parser_text(node.get("section_title") or node.get("title") or "")
        current_path = parent_path + ([title] if title else [])
        children = node.get("children_sections") or []
        item = dict(node)
        item["_title_path"] = current_path
        has_content = bool(sanitize_parser_text(node.get("content", "")))
        has_tables = bool(node.get("tables") or [])
        has_images = bool(node.get("images") or [])
        if has_content or has_tables or has_images or not children:
            output.append(item)
        if children:
            output.extend(_walk_sections(children, current_path))
    return output


def _build_section_chunk_rows(payload: Dict[str, Any], max_chars: int = 1200, overlap: int = 120) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    sections = payload.get("sections") or []
    for section in _walk_sections(sections):
        section_id = str(section.get("section_id", "")).strip()
        if not section_id:
            continue
        title_path = [
            sanitize_parser_text(node)
            for node in (section.get("_title_path") or [])
            if sanitize_parser_text(node)
        ]
        if not title_path:
            fallback_title = sanitize_parser_text(section.get("section_title") or section.get("title") or section_id)
            title_path = [fallback_title] if fallback_title else [section_id]

        content = sanitize_parser_text(section.get("content", ""))
        tables = list(section.get("tables") or [])
        images = list(section.get("images") or [])
        markdown_parts: List[str] = []
        if content:
            markdown_parts.append(content)
        for table in tables:
            if isinstance(table, dict):
                table_markdown = sanitize_parser_text(table.get("markdown", ""))
                if table_markdown:
                    markdown_parts.append(table_markdown)
        for image in images:
            if isinstance(image, dict):
                markdown_ref = sanitize_parser_text(image.get("markdown_ref", ""))
                caption = sanitize_parser_text(image.get("caption", "")) or sanitize_parser_text(image.get("filename", ""))
                if markdown_ref:
                    markdown_parts.append(f"![{caption or 'image'}]({markdown_ref})")

        body = "\n\n".join([part for part in markdown_parts if part]).strip()
        if not body:
            continue

        heading_text = "\n".join(title_path).strip()
        chunk_source_text = f"{heading_text}\n\n{body}".strip() if heading_text else body
        section_path_text = settings.kb_section_path_sep.join(title_path).strip()
        raw_pages = [page for page in (section.get("raw_pages") or []) if isinstance(page, int)]
        page_start = section.get("page_start") if isinstance(section.get("page_start"), int) else (min(raw_pages) if raw_pages else None)
        page_end = max(raw_pages) if raw_pages else page_start

        for idx, chunk_text in enumerate(_split_text_chunks(chunk_source_text, max_chars=max_chars, overlap=overlap), start=1):
            rows.append(
                {
                    "chunk_id": f"{section_id}_chunk_{idx}",
                    "page": page_start,
                    "page_start": page_start,
                    "page_end": page_end,
                    "text": chunk_text,
                    "tables": tables,
                    "image_paths": [],
                    "unit_type": "structured_section_chunk",
                    "section_id": section_id,
                    "section_name": title_path[-1] if title_path else section_id,
                    "section_path": title_path,
                    "section_path_text": section_path_text,
                    "title_path": title_path,
                    "source_chunk_ids": [section_id],
                    "raw_pages": raw_pages,
                    "char_count": len(chunk_text),
                }
            )
    return rows


def parse_docx(file_path: str) -> List[Dict[str, Any]]:
    from agent.agent_backend.utils.parser.docx_markdown_parser import parse_docx_to_markdown_json

    payload = parse_docx_to_markdown_json(
        input_path=Path(file_path).expanduser().resolve(),
        title=None,
        embed_images=False,
        skip_toc=True,
        filter_header_footer=True,
        merge_continuous_tables=True,
    )
    rows = _build_section_chunk_rows(
        payload,
        max_chars=max(200, int(settings.kb_chunk_max_chars)),
        overlap=max(0, int(settings.kb_chunk_overlap)),
    )
    return rows
