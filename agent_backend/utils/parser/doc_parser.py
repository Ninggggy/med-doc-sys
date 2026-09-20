import os
import re
from typing import Dict, List

from agent.agent_backend.utils.parser.docx_parser import parse_docx


def _chunk_lines(lines: List[str], max_len: int = 800) -> List[str]:
    chunks: List[str] = []
    current = ""
    for line in lines:
        clean = str(line or "").strip()
        if not clean:
            continue
        if len(current) + len(clean) + 1 <= max_len:
            current = f"{current}\n{clean}".strip()
        else:
            if current:
                chunks.append(current)
            current = clean
    if current:
        chunks.append(current)
    return chunks


def _looks_like_docx(file_path: str) -> bool:
    try:
        with open(file_path, "rb") as f:
            head = f.read(4)
        is_docx = head == b"PK\x03\x04"
        return is_docx
    except Exception:
        return False


def _extract_utf16_text(raw: bytes) -> List[str]:
    # Old .doc often stores text in UTF-16LE-like runs.
    pattern = re.compile(rb"(?:[\x20-\x7e]\x00|[\x80-\xff][\x00-\xff]){8,}")
    lines: List[str] = []
    for match in pattern.finditer(raw):
        blob = match.group(0)
        try:
            text = blob.decode("utf-16le", errors="ignore")
        except Exception:
            continue
        for line in re.split(r"[\r\n\t]+", text):
            line = re.sub(r"\s+", " ", line).strip()
            if len(line) >= 4:
                lines.append(line)
    return lines


def _extract_single_byte_text(raw: bytes) -> List[str]:
    # Fallback for compressed old-doc text and mixed encodings.
    pattern = re.compile(rb"[\x20-\x7e\x80-\xff]{12,}")
    lines: List[str] = []
    for match in pattern.finditer(raw):
        blob = match.group(0)
        decoded = ""
        for enc in ("gb18030", "gbk", "utf-8", "latin1"):
            try:
                decoded = blob.decode(enc, errors="ignore")
                if decoded:
                    break
            except Exception:
                continue
        if not decoded:
            continue
        for line in re.split(r"[\r\n\t]+", decoded):
            line = re.sub(r"\s+", " ", line).strip()
            if len(line) >= 4:
                lines.append(line)
    return lines


def _dedupe_lines(lines: List[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for line in lines:
        normalized = re.sub(r"\s+", " ", str(line or "")).strip()
        if not normalized:
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
    return result


def parse_doc(file_path: str) -> List[Dict]:
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"file not found: {file_path}")

    if _looks_like_docx(file_path):
        return parse_docx(file_path)

    with open(file_path, "rb") as f:
        raw = f.read()

    utf16_lines = _extract_utf16_text(raw)
    single_byte_lines = _extract_single_byte_text(raw)
    merged_lines = _dedupe_lines(utf16_lines + single_byte_lines)
    chunks = _chunk_lines(merged_lines)

    result: List[Dict] = []
    for idx, chunk in enumerate(chunks, start=1):
        result.append(
            {
                "chunk_id": f"doc_{idx}",
                "page": None,
                "text": chunk,
                "tables": [],
                "image_paths": [],
            }
        )
    return result
