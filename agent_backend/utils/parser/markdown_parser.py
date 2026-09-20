from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.agent_backend.config.settings import settings


HEADING_RE = re.compile(r"^(#{1,3})\s+(.+?)\s*$")


def _normalize_line_endings(text: str) -> str:
    return str(text or "").replace("\ufeff", "", 1).replace("\r\n", "\n").replace("\r", "\n")


def _clean_title(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _normalize_markdown_body(lines: List[str]) -> str:
    normalized: List[str] = []
    blank_streak = 0
    for raw_line in lines:
        line = raw_line.rstrip()
        if not line.strip():
            blank_streak += 1
            if blank_streak <= 1:
                normalized.append("")
            continue
        blank_streak = 0
        normalized.append(line)
    return "\n".join(normalized).strip()


def _split_markdown_chunks(text: str, max_chars: int = 1200, overlap: int = 120) -> List[str]:
    value = _normalize_markdown_body(_normalize_line_endings(text).split("\n"))
    if not value:
        return []
    if len(value) <= max_chars:
        return [value]

    paragraphs = [part.strip() for part in value.split("\n\n") if part.strip()]
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
            split_at = max_chars
            window = para[:max_chars]
            for sep in ("\n", "。", "；", ";", "，", ",", " "):
                pos = window.rfind(sep)
                if pos >= max(1, int(max_chars * 0.6)):
                    split_at = pos + 1
                    break
            piece = para[:split_at].strip()
            if piece:
                chunks.append(piece)
            para = para[max(1, split_at - overlap) :].strip()
        current = para
    if current:
        chunks.append(current)
    return [chunk for chunk in chunks if chunk]


def _build_heading_block(title_path: List[str]) -> str:
    lines: List[str] = []
    for idx, title in enumerate(title_path[:3], start=1):
        clean = _clean_title(title)
        if clean:
            lines.append(f"{'#' * idx} {clean}")
    if not lines:
        clean = _clean_title(title_path[-1]) if title_path else ""
        if clean:
            lines.append(f"# {clean}")
    return "\n".join(lines).strip()


def _new_section(section_id: str, title: str, level: int, title_path: List[str], parent_section_id: str = "") -> Dict[str, Any]:
    return {
        "section_id": section_id,
        "section_name": title,
        "section_title": title,
        "level": level,
        "title_path": list(title_path),
        "parent_section_id": parent_section_id,
        "_content_lines": [],
        "_has_children": False,
    }


def _parse_markdown_sections(file_path: str) -> List[Dict[str, Any]]:
    text = Path(file_path).read_text(encoding="utf-8", errors="ignore")
    lines = _normalize_line_endings(text).split("\n")

    sections: List[Dict[str, Any]] = []
    stack: List[Dict[str, Any]] = []
    root = _new_section("md_root", "文档引言", 0, ["文档引言"])
    current = root
    section_counter = 0

    for raw_line in lines:
        match = HEADING_RE.match(raw_line)
        if match:
            hashes, title = match.groups()
            level = len(hashes)
            title = _clean_title(title)
            if not title:
                continue
            while stack and int(stack[-1]["level"]) >= level:
                stack.pop()
            title_path = [str(node["section_title"]) for node in stack] + [title]
            parent_section_id = str(stack[-1]["section_id"]) if stack else ""
            section_counter += 1
            section = _new_section(f"md_sec_{section_counter}", title, level, title_path, parent_section_id)
            if stack:
                stack[-1]["_has_children"] = True
            sections.append(section)
            stack.append(section)
            current = section
            continue
        current["_content_lines"].append(raw_line)

    if root["_content_lines"] and _normalize_markdown_body(root["_content_lines"]):
        sections.insert(0, root)
    return sections


def _collect_section_body(section: Dict[str, Any], section_map: Dict[str, Dict[str, Any]]) -> str:
    lineage: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = section
    while current:
        lineage.append(current)
        parent_section_id = str(current.get("parent_section_id") or "").strip()
        current = section_map.get(parent_section_id) if parent_section_id else None
    lineage.reverse()

    body_parts: List[str] = []
    for node in lineage:
        body = _normalize_markdown_body(list(node.get("_content_lines") or []))
        if body:
            body_parts.append(body)
    return "\n\n".join(body_parts).strip()


def parse_markdown(file_path: str) -> List[Dict[str, Any]]:
    max_chars = max(200, int(settings.kb_chunk_max_chars))
    overlap = max(0, int(settings.kb_chunk_overlap))
    rows: List[Dict[str, Any]] = []
    sections = _parse_markdown_sections(file_path)
    section_map = {str(section.get("section_id") or ""): section for section in sections}

    for section in sections:
        if section.get("_has_children"):
            continue
        body = _collect_section_body(section, section_map)
        if not body:
            continue
        title_path = [_clean_title(node) for node in (section.get("title_path") or []) if _clean_title(node)]
        heading_block = _build_heading_block(title_path)
        source_text = f"{heading_block}\n\n{body}".strip() if heading_block else body
        section_path_text = settings.kb_section_path_sep.join(title_path).strip()
        section_id = str(section.get("section_id") or "")
        section_name = _clean_title(section.get("section_name") or section_id)
        chunks = _split_markdown_chunks(source_text, max_chars=max_chars, overlap=overlap)
        for idx, chunk_text in enumerate(chunks, start=1):
            rows.append(
                {
                    "chunk_id": f"{section_id}_chunk_{idx}",
                    "page": None,
                    "page_start": None,
                    "page_end": None,
                    "text": chunk_text,
                    "tables": [],
                    "image_paths": [],
                    "unit_type": "markdown_leaf_section_chunk",
                    "section_id": section_id,
                    "section_name": section_name,
                    "section_path": title_path,
                    "section_path_text": section_path_text,
                    "title_path": title_path,
                    "source_chunk_ids": [section_id],
                    "raw_pages": [],
                    "char_count": len(chunk_text),
                }
            )
    return rows
