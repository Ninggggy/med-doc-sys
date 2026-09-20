import argparse
import hashlib
import json
import os
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple


def _try_import_parsers():
    fitz = None
    pdfplumber = None
    try:
        import fitz as _fitz  # type: ignore

        fitz = _fitz
    except Exception:
        fitz = None

    try:
        import pdfplumber as _pdfplumber  # type: ignore

        pdfplumber = _pdfplumber
    except Exception:
        pdfplumber = None
    return fitz, pdfplumber


def _try_import_ocr():
    pytesseract = None
    image_cls = None
    try:
        import pytesseract as _pytesseract  # type: ignore

        pytesseract = _pytesseract
    except Exception:
        pytesseract = None
    try:
        from PIL import Image as _Image  # type: ignore

        image_cls = _Image
    except Exception:
        image_cls = None
    return pytesseract, image_cls


def _normalize_text(text: str) -> str:
    t = (text or "").replace("\u3000", " ")
    t = re.sub(r"\r\n?", "\n", t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def _text_quality_score(text: str) -> float:
    if not text:
        return 0.0
    total = len(text)
    if total == 0:
        return 0.0
    # Keep common readable chars as positive signals.
    readable = re.findall(r"[\u4e00-\u9fffA-Za-z0-9，。！？；：、】【《》‘’“”\-—\n\s]", text)
    return len(readable) / total


def _keywords(text: str, top_k: int = 12) -> List[str]:
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9_-]{1,}|[\u4e00-\u9fff]{2,}", text or "")
    # Minimal stopwords, keep it small and deterministic.
    stop = {
        "我们",
        "你们",
        "他们",
        "以及",
        "或者",
        "相关",
        "进行",
        "可以",
        "应当",
        "其中",
        "根据",
        "包括",
    }
    terms = [x for x in tokens if x not in stop]
    freq = Counter(terms)
    return [x for x, _ in freq.most_common(top_k)]


def _is_heading(line: str) -> bool:
    s = (line or "").strip()
    if not s:
        return False
    if len(s) > 60:
        return False
    # Avoid table-like short rows: "3 R R T", "WR WR", etc.
    if re.match(r"^[A-Za-z0-9]+(?:\s+[A-Za-z0-9%.\-]+){2,}$", s):
        return False
    # Avoid long sentence lines.
    if re.search(r"[，。；！？:：]$", s):
        return False

    rules = [
        r"^\u3010[^\u3011]{1,40}\u3011$",  # 【章节】
        r"^第[一二三四五六七八九十百千0-9]+[章节部分条款]",  # 第X章/节/条
        r"^[一二三四五六七八九十]+[、.．]\s*",  # 一、 / 二.
        r"^[（(][一二三四五六七八九十0-9]+[)）]\s*",  # （一） / (1)
        r"^\d+(\.\d+){1,3}\s*",  # 1.1 / 1.1.1
        r"^附录[A-Za-z0-9一二三四五六七八九十]*[\s\S]{0,20}$",
        r"^附件[A-Za-z0-9一二三四五六七八九十]*[\s\S]{0,20}$",
    ]
    if not any(re.match(p, s) for p in rules):
        return False

    # Heading should contain Chinese or clear numbering markers.
    if re.search(r"[\u4e00-\u9fff]", s):
        return True
    return bool(re.match(r"^\d+(\.\d+){1,3}\s*$", s))


def _heading_level(line: str) -> int:
    s = (line or "").strip()
    if re.match(r"^\u3010[^\u3011]{1,40}\u3011$", s) or re.match(r"^第[一二三四五六七八九十百千0-9]+[章节部分条款]", s):
        return 1
    if re.match(r"^[一二三四五六七八九十]+[、.．]\s*", s) or re.match(r"^附录", s) or re.match(r"^附件", s):
        return 1
    if re.match(r"^[（(][一二三四五六七八九十0-9]+[)）]\s*", s):
        return 2
    if re.match(r"^\d+\.\d+\s*", s):
        return 2
    if re.match(r"^\d+(\.\d+){2,3}\s*", s):
        return 3
    return 2


@dataclass
class PageItem:
    page_num: int
    text: str


def _locate_section_by_page(sections: List[Dict[str, Any]], page_num: int) -> Tuple[str, str]:
    for sec in sections:
        if int(sec.get("start_page", 0)) <= page_num <= int(sec.get("end_page", 0)):
            return str(sec.get("section_id", "")), str(sec.get("title", ""))
    return "", ""


def _extract_pages_with_pymupdf(pdf_path: str, fitz: Any) -> Tuple[Dict[str, Any], List[PageItem]]:
    pages: List[PageItem] = []
    meta: Dict[str, Any] = {
        "file_name": os.path.basename(pdf_path),
        "file_path": os.path.abspath(pdf_path),
        "file_size": os.path.getsize(pdf_path) if os.path.exists(pdf_path) else 0,
        "page_count": 0,
        "extractor": "pymupdf",
    }
    with fitz.open(pdf_path) as doc:
        meta["page_count"] = len(doc)
        md = {}
        try:
            md = dict(getattr(doc, "metadata", {}) or {})
        except Exception:
            md = {}
        meta["pdf_version"] = str(md.get("format", ""))
        meta["author"] = str(md.get("author", ""))
        meta["creation_date"] = str(md.get("creationDate", ""))
        for i, p in enumerate(doc, start=1):
            txt = _normalize_text(p.get_text("text"))
            pages.append(PageItem(page_num=i, text=txt))
    return meta, pages


def _extract_pages_with_pdfplumber(pdf_path: str, pdfplumber: Any) -> Tuple[Dict[str, Any], List[PageItem]]:
    pages: List[PageItem] = []
    meta: Dict[str, Any] = {
        "file_name": os.path.basename(pdf_path),
        "file_path": os.path.abspath(pdf_path),
        "file_size": os.path.getsize(pdf_path) if os.path.exists(pdf_path) else 0,
        "page_count": 0,
        "extractor": "pdfplumber",
        "pdf_version": "",
        "author": "",
        "creation_date": "",
    }
    with pdfplumber.open(pdf_path) as doc:
        meta["page_count"] = len(doc.pages)
        for i, p in enumerate(doc.pages, start=1):
            txt = _normalize_text(p.extract_text() or "")
            pages.append(PageItem(page_num=i, text=txt))
    return meta, pages


def _extract_pages(pdf_path: str) -> Tuple[Dict[str, Any], List[PageItem]]:
    fitz, pdfplumber = _try_import_parsers()
    candidates: List[Tuple[Dict[str, Any], List[PageItem]]] = []

    if fitz is not None:
        candidates.append(_extract_pages_with_pymupdf(pdf_path, fitz))

    if pdfplumber is not None:
        candidates.append(_extract_pages_with_pdfplumber(pdf_path, pdfplumber))

    if candidates:
        ranked: List[Tuple[float, Dict[str, Any], List[PageItem]]] = []
        for meta, pages in candidates:
            text = "\n".join(p.text for p in pages if p.text)
            score = _text_quality_score(text)
            ranked.append((score, meta, pages))
            print(
                "[KB Parser] extractor candidate:",
                {"extractor": meta.get("extractor"), "quality": round(score, 4), "pages": len(pages)},
            )
        ranked.sort(key=lambda x: x[0], reverse=True)
        best = ranked[0]
        print(
            "[KB Parser] extractor selected:",
            {"extractor": best[1].get("extractor"), "quality": round(best[0], 4)},
        )
        return best[1], best[2]

    raise RuntimeError("No available PDF parser. Please install PyMuPDF or pdfplumber.")


def _build_sections(pages: List[PageItem]) -> List[Dict[str, Any]]:
    sections: List[Dict[str, Any]] = []
    current = {
        "section_id": "sec_1",
        "title": "文档正文",
        "level": 0,
        "start_page": pages[0].page_num if pages else 1,
        "end_page": pages[0].page_num if pages else 1,
        "content": "",
    }
    sid = 1

    for page in pages:
        lines = [x.strip() for x in page.text.splitlines() if x.strip()]
        for line in lines:
            if _is_heading(line):
                lvl = _heading_level(line)
                # If current section is still empty default, reuse it as first heading.
                if not current["content"].strip() and current["title"] == "文档正文":
                    current["title"] = line[:120]
                    current["level"] = lvl
                    current["start_page"] = page.page_num
                    current["end_page"] = page.page_num
                    continue
                if current["content"].strip():
                    current["content"] = _normalize_text(current["content"])
                    current["char_count"] = len(current["content"])
                    current["keywords"] = _keywords(current["content"])
                    current["content_preview"] = current["content"][:280]
                    sections.append(current)
                sid += 1
                current = {
                    "section_id": f"sec_{sid}",
                    "title": line[:120],
                    "level": lvl,
                    "start_page": page.page_num,
                    "end_page": page.page_num,
                    "content": "",
                }
            else:
                current["content"] += ("\n" if current["content"] else "") + line
        current["end_page"] = page.page_num

    if current["content"].strip():
        current["content"] = _normalize_text(current["content"])
        current["char_count"] = len(current["content"])
        current["keywords"] = _keywords(current["content"])
        current["content_preview"] = current["content"][:280]
        sections.append(current)

    # Build parent-child links by heading level.
    level_stack: Dict[int, str] = {}
    for sec in sections:
        level = int(sec.get("level", 2))
        parent_id = None
        for lv in range(level - 1, 0, -1):
            if lv in level_stack:
                parent_id = level_stack[lv]
                break
        sec["parent_section_id"] = parent_id
        level_stack[level] = sec.get("section_id")
        # Drop deeper stack levels when moving up.
        for lv in list(level_stack.keys()):
            if lv > level:
                level_stack.pop(lv, None)

    return sections


def _split_sentences(text: str) -> List[str]:
    text = _normalize_text(text)
    if not text:
        return []
    parts = re.split(r"(?<=[。！？；;.!?])\s*", text)
    out = [p.strip() for p in parts if p and p.strip()]
    return out if out else [text]


def _semantic_chunk_text(text: str, target_chars: int = 700, max_chars: int = 900, min_chars: int = 220) -> List[str]:
    sentences = _split_sentences(text)
    if not sentences:
        return []
    chunks: List[str] = []
    buf = ""

    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        # If one sentence is extremely long, split by comma, then hard split.
        if len(sent) > max_chars:
            sub_parts = [x.strip() for x in re.split(r"(?<=[，,])\s*", sent) if x.strip()]
            for sp in sub_parts:
                if len(sp) <= max_chars:
                    if not buf:
                        buf = sp
                    elif len(buf) + 1 + len(sp) <= max_chars:
                        buf = f"{buf} {sp}"
                    else:
                        if buf:
                            chunks.append(buf)
                        buf = sp
                else:
                    if buf:
                        chunks.append(buf)
                        buf = ""
                    for i in range(0, len(sp), max_chars):
                        chunks.append(sp[i : i + max_chars])
            continue

        candidate = sent if not buf else f"{buf} {sent}"
        if len(candidate) <= target_chars:
            buf = candidate
            continue
        if len(candidate) <= max_chars and len(buf) < min_chars:
            buf = candidate
            continue
        if buf:
            chunks.append(buf)
        buf = sent

    if buf:
        chunks.append(buf)
    return chunks


def _is_heading_like_chunk(text: str) -> bool:
    s = _normalize_text(text)
    if not s:
        return True
    if len(s) > 100:
        return False
    line_count = len([x for x in s.splitlines() if x.strip()])
    if line_count > 3:
        return False
    # Lacks sentence punctuation and looks like heading/label list.
    no_sentence_punct = not re.search(r"[。！？；;.!?]", s)
    has_heading_mark = bool(
        re.search(
            r"(^第[一二三四五六七八九十百千0-9]+[章节部分条款])|(^[一二三四五六七八九十]+[、.．])|(^[（(][一二三四五六七八九十0-9]+[)）])|(^附录)|(^附件)",
            s,
        )
    )
    return no_sentence_punct and has_heading_mark


def _split_chunks(sections: List[Dict[str, Any]], max_chars: int = 900, overlap: int = 120) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    cid = 0
    for sec in sections:
        text = str(sec.get("content", "")).strip()
        if not text:
            continue
        semantic_chunks = _semantic_chunk_text(text, target_chars=max_chars - 180, max_chars=max_chars, min_chars=220)
        for idx, chunk_text in enumerate(semantic_chunks):
            if chunk_text:
                if _is_heading_like_chunk(chunk_text):
                    continue
                cid += 1
                prev_tail = ""
                if overlap > 0 and idx > 0:
                    prev = semantic_chunks[idx - 1]
                    prev_tail = prev[-overlap:].strip()
                merged_text = f"{prev_tail} {chunk_text}".strip() if prev_tail else chunk_text
                out.append(
                    {
                        "chunk_id": f"chunk_{cid}",
                        "section_id": sec.get("section_id"),
                        "section_title": sec.get("title"),
                        "section_level": sec.get("level", 0),
                        "page_start": sec.get("start_page"),
                        "page_end": sec.get("end_page"),
                        "char_count": len(merged_text),
                        "keywords": _keywords(merged_text, top_k=8),
                        "text": merged_text,
                    }
                )
    # Merge tiny neighbor chunks inside the same section to reduce fragmentation.
    merged: List[Dict[str, Any]] = []
    for chunk in out:
        if not merged:
            merged.append(chunk)
            continue
        prev = merged[-1]
        same_sec = prev.get("section_id") == chunk.get("section_id")
        tiny = int(chunk.get("char_count", 0)) < 160
        if same_sec and tiny and int(prev.get("char_count", 0)) < max_chars:
            new_text = f"{prev.get('text', '')}\n{chunk.get('text', '')}".strip()
            prev["text"] = new_text
            prev["char_count"] = len(new_text)
            prev["keywords"] = _keywords(new_text, top_k=8)
            continue
        merged.append(chunk)

    # Reindex chunk ids after merge.
    for i, c in enumerate(merged, start=1):
        c["chunk_id"] = f"chunk_{i}"
    return merged


def _normalize_table_cell(cell: Any) -> str:
    if cell is None:
        return ""
    text = str(cell).replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _table_to_markdown(rows: List[List[str]], max_rows: int = 12) -> str:
    if not rows:
        return ""
    header = rows[0]
    body = rows[1:max_rows]
    if not header:
        return ""
    out = []
    def _md_cell(val: str) -> str:
        t = str(val)
        t = t.replace("|", "｜")
        t = t.replace("\n", "<br>")
        return t

    out.append("| " + " | ".join(_md_cell(x) for x in header) + " |")
    out.append("| " + " | ".join(["---"] * len(header)) + " |")
    for row in body:
        fixed = row + [""] * (len(header) - len(row))
        out.append("| " + " | ".join(_md_cell(x) for x in fixed[: len(header)]) + " |")
    return "\n".join(out)


def _table_rows_to_records(rows: List[List[str]], max_records: int = 50) -> List[Dict[str, str]]:
    if len(rows) < 2:
        return []
    header = rows[0]
    records: List[Dict[str, str]] = []
    for row in rows[1 : 1 + max_records]:
        fixed = row + [""] * (len(header) - len(row))
        rec = {str(header[i]).strip() or f"col_{i+1}": fixed[i].strip() for i in range(len(header))}
        records.append(rec)
    return records


def _extract_tables(pdf_path: str, sections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    fitz, pdfplumber = _try_import_parsers()
    tables: List[Dict[str, Any]] = []
    tid = 0

    if pdfplumber is not None:
        try:
            with pdfplumber.open(pdf_path) as doc:
                for page_idx, page in enumerate(doc.pages, start=1):
                    page_tables = page.extract_tables() or []
                    for t in page_tables:
                        if not t:
                            continue
                        cleaned = [[_normalize_table_cell(c) for c in row] for row in t if row]
                        if not cleaned:
                            continue
                        max_cols = max(len(r) for r in cleaned)
                        if max_cols < 2:
                            continue
                        normalized_rows = [r + [""] * (max_cols - len(r)) for r in cleaned]
                        non_empty_cells = sum(1 for r in normalized_rows for c in r if c)
                        if non_empty_cells < max(4, max_cols):
                            continue
                        tid += 1
                        sec_id, sec_title = _locate_section_by_page(sections, page_idx)
                        tables.append(
                            {
                                "table_id": f"table_{tid}",
                                "page_num": page_idx,
                                "section_id": sec_id,
                                "section_title": sec_title,
                                "shape": [len(normalized_rows), max_cols],
                                "rows_raw": normalized_rows,
                                "rows": normalized_rows,
                                "header": normalized_rows[0],
                                "records": _table_rows_to_records(normalized_rows),
                                "markdown": _table_to_markdown(normalized_rows),
                                "text_preview": " ".join(" ".join(r) for r in normalized_rows[:3])[:260],
                                "source": "pdfplumber",
                            }
                        )
        except Exception as e:
            print(f"[KB Parser] table extract failed (pdfplumber): {e}")

    if fitz is not None:
        try:
            with fitz.open(pdf_path) as doc:
                for page_idx, page in enumerate(doc, start=1):
                    find_tables = getattr(page, "find_tables", None)
                    if not callable(find_tables):
                        continue
                    result = find_tables()
                    table_objs = getattr(result, "tables", []) if result else []
                    for t in table_objs:
                        try:
                            rows_raw = t.extract()
                        except Exception:
                            rows_raw = []
                        if not rows_raw:
                            continue
                        cleaned = [[_normalize_table_cell(c) for c in row] for row in rows_raw if row]
                        if not cleaned:
                            continue
                        max_cols = max(len(r) for r in cleaned)
                        if max_cols < 2:
                            continue
                        normalized_rows = [r + [""] * (max_cols - len(r)) for r in cleaned]
                        non_empty_cells = sum(1 for r in normalized_rows for c in r if c)
                        if non_empty_cells < max(4, max_cols):
                            continue
                        preview = " ".join(" ".join(r) for r in normalized_rows[:2])[:120]
                        duplicate = any(
                            x.get("page_num") == page_idx and x.get("text_preview", "")[:120] == preview
                            for x in tables
                        )
                        if duplicate:
                            continue
                        tid += 1
                        sec_id, sec_title = _locate_section_by_page(sections, page_idx)
                        tables.append(
                            {
                                "table_id": f"table_{tid}",
                                "page_num": page_idx,
                                "section_id": sec_id,
                                "section_title": sec_title,
                                "shape": [len(normalized_rows), max_cols],
                                "rows_raw": normalized_rows,
                                "rows": normalized_rows,
                                "header": normalized_rows[0],
                                "records": _table_rows_to_records(normalized_rows),
                                "markdown": _table_to_markdown(normalized_rows),
                                "text_preview": " ".join(" ".join(r) for r in normalized_rows[:3])[:260],
                                "source": "pymupdf",
                            }
                        )
        except Exception as e:
            print(f"[KB Parser] table extract failed (pymupdf): {e}")
    return tables


def _find_figure_caption(page_text: str) -> str:
    t = _normalize_text(page_text or "")
    if not t:
        return ""
    m = re.search(r"(图|Figure)\s*[0-9一二三四五六七八九十]+[^\n。]{0,80}", t, flags=re.IGNORECASE)
    return m.group(0).strip() if m else ""


def _ocr_image_text(img_path: Path) -> str:
    pytesseract, image_cls = _try_import_ocr()
    if pytesseract is None or image_cls is None:
        return ""
    try:
        with image_cls.open(img_path) as im:
            txt = pytesseract.image_to_string(im, lang="chi_sim+eng")
    except Exception:
        return ""
    txt = _normalize_text(txt)
    return txt[:1200]


def _extract_images(
    pdf_path: str, output_path: str, sections: List[Dict[str, Any]], pages: List[PageItem]
) -> List[Dict[str, Any]]:
    fitz, _ = _try_import_parsers()
    images: List[Dict[str, Any]] = []
    if fitz is None:
        return images

    out_dir = Path(output_path).parent
    stem = Path(pdf_path).stem
    img_dir = out_dir / f"{stem}_assets" / "images"
    img_dir.mkdir(parents=True, exist_ok=True)

    iid = 0
    seen_sha1 = set()
    page_text_map = {p.page_num: p.text for p in pages}
    try:
        with fitz.open(pdf_path) as doc:
            for page_idx, page in enumerate(doc, start=1):
                img_infos = page.get_images(full=True) or []
                for seq, info in enumerate(img_infos, start=1):
                    if not info:
                        continue
                    xref = int(info[0])
                    try:
                        base = doc.extract_image(xref)
                    except Exception:
                        continue
                    if not base:
                        continue
                    img_bytes = base.get("image")
                    if not img_bytes:
                        continue
                    ext = str(base.get("ext", "png")).lower()
                    width = int(base.get("width", 0) or 0)
                    height = int(base.get("height", 0) or 0)
                    # Skip tiny icons / separators.
                    if width < 32 or height < 32:
                        continue
                    if len(img_bytes) < 1024:
                        continue
                    sha1 = hashlib.sha1(img_bytes).hexdigest()
                    if sha1 in seen_sha1:
                        continue
                    seen_sha1.add(sha1)
                    iid += 1
                    file_name = f"img_p{page_idx}_{seq}_{xref}.{ext}"
                    file_path = img_dir / file_name
                    file_path.write_bytes(img_bytes)
                    sec_id, sec_title = _locate_section_by_page(sections, page_idx)
                    caption = _find_figure_caption(page_text_map.get(page_idx, ""))
                    ocr_text = _ocr_image_text(file_path)
                    images.append(
                        {
                            "image_id": f"image_{iid}",
                            "page_num": page_idx,
                            "section_id": sec_id,
                            "section_title": sec_title,
                            "xref": xref,
                            "width": width,
                            "height": height,
                            "ext": ext,
                            "size": len(img_bytes),
                            "sha1": sha1,
                            "path": str(file_path),
                            "caption": caption,
                            "ocr_text": ocr_text,
                        }
                    )
    except Exception as e:
        print(f"[KB Parser] image extract failed: {e}")
    return images


def _heading_to_md(level: int, title: str) -> str:
    lv = max(1, min(6, int(level) + 1))
    return f"{'#' * lv} {title.strip()}"


def _normalize_paragraphs(text: str) -> str:
    t = _normalize_text(text)
    if not t:
        return ""
    lines = [x.strip() for x in t.splitlines()]
    merged: List[str] = []
    buf = ""
    for ln in lines:
        if not ln:
            if buf:
                merged.append(buf)
                buf = ""
            continue
        # likely sentence continuation
        if buf and not re.search(r"[。！？；:：.!?]$", buf):
            buf = f"{buf}{ln}"
        else:
            if buf:
                merged.append(buf)
            buf = ln
    if buf:
        merged.append(buf)
    return "\n\n".join(merged)


def _repair_broken_lines_for_md(text: str) -> str:
    if not text:
        return ""
    lines = [x.rstrip() for x in text.splitlines()]
    out: List[str] = []
    for ln in lines:
        s = ln.strip()
        if not s:
            out.append("")
            continue
        # Keep markdown structural lines as standalone.
        if re.match(r"^(#{1,6}\s|[-*]\s|>\s|\|.*\||!\[.*\]\(.*\)|---\s*$)", s):
            out.append(s)
            continue
        if not out:
            out.append(s)
            continue

        prev = out[-1]
        if re.match(r"^(#{1,6}\s|[-*]\s|>\s|\|.*\||!\[.*\]\(.*\)|---\s*$)", prev):
            out.append(s)
            continue
        # Merge isolated short symbol/token lines into previous line.
        if len(s) <= 6 and re.match(r"^[A-Za-z0-9%+\-_/().]+$", s):
            out[-1] = f"{prev}{s}"
            continue
        # Merge when previous line obviously not ended.
        if prev and not re.search(r"[。！？；:：.!?]$", prev):
            out[-1] = f"{prev}{s}"
            continue
        out.append(s)
    # normalize extra blank lines
    txt = "\n".join(out)
    txt = re.sub(r"\n{3,}", "\n\n", txt)
    return txt.strip()


def _relative_posix_path(from_file: Path, target: Path) -> str:
    try:
        rel = target.relative_to(from_file.parent)
    except Exception:
        rel = target
    return rel.as_posix()


_CHEM_ELEMS = {
    "H","He","Li","Be","B","C","N","O","F","Ne","Na","Mg","Al","Si","P","S","Cl","Ar","K","Ca",
    "Sc","Ti","V","Cr","Mn","Fe","Co","Ni","Cu","Zn","Ga","Ge","As","Se","Br","Kr","Rb","Sr","Y","Zr",
    "Nb","Mo","Tc","Ru","Rh","Pd","Ag","Cd","In","Sn","Sb","Te","I","Xe","Cs","Ba","La","Ce","Pr","Nd",
    "Pm","Sm","Eu","Gd","Tb","Dy","Ho","Er","Tm","Yb","Lu","Hf","Ta","W","Re","Os","Ir","Pt","Au","Hg",
    "Tl","Pb","Bi","Po","At","Rn","Fr","Ra","Ac","Th","Pa","U","Np","Pu","Am","Cm","Bk","Cf","Es","Fm",
    "Md","No","Lr","Rf","Db","Sg","Bh","Hs","Mt","Ds","Rg","Cn","Nh","Fl","Mc","Lv","Ts","Og",
}

_SUBSCRIPT_MAP = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")
_REV_SUBSCRIPT_MAP = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")


def _formula_to_subscript(formula: str) -> str:
    out = []
    for ch in formula:
        if ch.isdigit():
            out.append(ch.translate(_SUBSCRIPT_MAP))
        else:
            out.append(ch)
    return "".join(out)


def _normalize_formula_text(s: str) -> str:
    return s.translate(_REV_SUBSCRIPT_MAP).replace(" ", "")


def _parse_formula_segment(seg: str, multiplier: int = 1) -> Dict[str, int]:
    tokens = re.findall(r"([A-Z][a-z]?)(\d*)", seg)
    if not tokens:
        return {}
    # Strict reconstruction to avoid partial matches.
    recon = "".join(sym + num for sym, num in tokens)
    if recon != seg:
        return {}
    out: Dict[str, int] = {}
    for sym, num in tokens:
        if sym not in _CHEM_ELEMS:
            return {}
        out[sym] = out.get(sym, 0) + (int(num) if num else 1) * multiplier
    return out


def _parse_formula(formula: str) -> Dict[str, int]:
    formula = _normalize_formula_text(formula)
    if not formula:
        return {}
    # Support hydrate style: CuSO4·5H2O / CuSO4.5H2O
    parts = re.split(r"[·.]", formula)
    out: Dict[str, int] = {}
    for p in parts:
        if not p:
            continue
        m = re.match(r"^(\d+)([A-Z].+)$", p)
        if m:
            mul = int(m.group(1))
            seg = m.group(2)
        else:
            mul = 1
            seg = p
        comp = _parse_formula_segment(seg, multiplier=mul)
        if not comp:
            return {}
        for k, v in comp.items():
            out[k] = out.get(k, 0) + v
    return out


def _find_chemical_formulas(
    doc_text: str, tables: List[Dict[str, Any]], images: List[Dict[str, Any]]
) -> Tuple[List[str], List[Dict[str, Any]]]:
    candidates: List[Tuple[str, str]] = []
    candidates.append((doc_text, "document"))
    for t in tables:
        text = "\n".join(" ".join(r) for r in t.get("rows_raw", []) if isinstance(r, list))
        if text:
            candidates.append((text, f"table:{t.get('table_id')}"))
    for im in images:
        ocr_text = str(im.get("ocr_text", "") or "")
        if ocr_text:
            candidates.append((ocr_text, f"image:{im.get('image_id')}"))

    found: Dict[str, Dict[str, Any]] = {}
    for text, source in candidates:
        text = _normalize_formula_text(text)
        # Match potential molecular formulas, length-controlled.
        pattern = r"(?<![A-Za-z0-9])((?:[A-Z][a-z]?\d{0,3}){2,16}(?:[·.](?:\d{0,3})?(?:[A-Z][a-z]?\d{0,3}){1,12})*)(?![A-Za-z0-9])"
        for m in re.finditer(pattern, text):
            raw = m.group(1).strip()
            if len(raw) < 3 or len(raw) > 40:
                continue
            comp = _parse_formula(raw)
            if not comp:
                continue
            # Filter out all-1 simple salts with only two atoms if too short noise.
            atom_sum = sum(comp.values())
            if atom_sum < 3:
                continue
            if raw not in found:
                found[raw] = {
                    "formula": raw,
                    "formula_subscript": _formula_to_subscript(raw),
                    "composition": comp,
                    "occurrences": 0,
                    "sources": [],
                }
            found[raw]["occurrences"] += 1
            if source not in found[raw]["sources"]:
                found[raw]["sources"].append(source)

    formulas = sorted(found.keys(), key=lambda x: (-int(found[x]["occurrences"]), x))
    detail = [found[k] for k in formulas]
    return formulas, detail


def _build_markdown(
    pdf_path: str,
    output_path: str,
    meta: Dict[str, Any],
    sections: List[Dict[str, Any]],
    tables: List[Dict[str, Any]],
    images: List[Dict[str, Any]],
    chemical_formulas_detail: List[Dict[str, Any]],
) -> Tuple[str, str]:
    md_path = str(Path(output_path).with_suffix(".md"))
    md_file = Path(md_path)

    tables_by_sec: Dict[str, List[Dict[str, Any]]] = {}
    for t in tables:
        sid = str(t.get("section_id", ""))
        tables_by_sec.setdefault(sid, []).append(t)

    images_by_sec: Dict[str, List[Dict[str, Any]]] = {}
    for im in images:
        sid = str(im.get("section_id", ""))
        images_by_sec.setdefault(sid, []).append(im)

    lines: List[str] = []
    lines.append(f"# {meta.get('file_name', Path(pdf_path).name)}")
    lines.append("")
    lines.append("## 文档信息")
    lines.append("")
    lines.append(f"- 页数: {meta.get('page_count', 0)}")
    lines.append(f"- 提取器: {meta.get('extractor', '')}")
    lines.append(f"- PDF 版本: {meta.get('pdf_version', '')}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 化学式识别")
    lines.append("")
    if chemical_formulas_detail:
        for item in chemical_formulas_detail:
            lines.append(
                f"- `{item.get('formula')}` -> {item.get('formula_subscript')} | 出现次数: {item.get('occurrences')}"
            )
    else:
        lines.append("- 未识别到明确化学式")
    lines.append("")
    lines.append("---")
    lines.append("")

    for sec in sections:
        sid = str(sec.get("section_id", ""))
        title = str(sec.get("title", "未命名章节"))
        level = int(sec.get("level", 1))
        lines.append(_heading_to_md(level, title))
        lines.append("")
        lines.append(
            f"> 页码: {sec.get('start_page', '')} - {sec.get('end_page', '')} | section_id: `{sid}`"
        )
        lines.append("")

        content = _normalize_paragraphs(str(sec.get("content", "")))
        if content:
            lines.append(content)
            lines.append("")

        sec_tables = tables_by_sec.get(sid, [])
        if sec_tables:
            lines.append("**表格**")
            lines.append("")
            for t in sec_tables:
                lines.append(
                    f"- 表格 `{t.get('table_id')}` (p.{t.get('page_num')}, {t.get('shape', [0,0])[0]}x{t.get('shape', [0,0])[1]})"
                )
                md_table = str(t.get("markdown", "")).strip()
                if md_table:
                    lines.append("")
                    lines.append(md_table)
                    lines.append("")

        sec_images = images_by_sec.get(sid, [])
        if sec_images:
            lines.append("**图片**")
            lines.append("")
            for im in sec_images:
                p = Path(str(im.get("path", "")))
                rel = _relative_posix_path(md_file, p)
                lines.append(
                    f"- 图片 `{im.get('image_id')}` (p.{im.get('page_num')}, {im.get('width')}x{im.get('height')})"
                )
                if im.get("caption"):
                    lines.append(f"  - 图题: {im.get('caption')}")
                if im.get("ocr_text"):
                    ocr_preview = _normalize_text(str(im.get("ocr_text")))[:280]
                    lines.append(f"  - OCR: {ocr_preview}")
                lines.append(f"![{im.get('image_id')}]({rel})")
                lines.append("")

        lines.append("---")
        lines.append("")

    md_text = "\n".join(lines).strip() + "\n"
    md_text = _repair_broken_lines_for_md(md_text)
    md_text += "\n"
    md_file.write_text(md_text, encoding="utf-8")
    return md_path, md_text


def _build_markdown_by_pages(
    pdf_path: str,
    output_path: str,
    meta: Dict[str, Any],
    page_rows: List[Dict[str, Any]],
) -> str:
    md_path = str(Path(output_path).with_suffix(".pages.md"))
    lines: List[str] = []
    lines.append(f"# {meta.get('file_name', Path(pdf_path).name)}（按页还原）")
    lines.append("")
    lines.append("## 文档信息")
    lines.append("")
    lines.append(f"- 页数: {meta.get('page_count', 0)}")
    lines.append(f"- 提取器: {meta.get('extractor', '')}")
    lines.append("")
    lines.append("---")
    lines.append("")
    for p in page_rows:
        page_num = p.get("page_num", 0)
        text = _normalize_text(str(p.get("text", "")))
        lines.append(f"## 第 {page_num} 页")
        lines.append("")
        if text:
            lines.append(_repair_broken_lines_for_md(text))
        else:
            lines.append("_空白页或未提取到文本_")
        lines.append("")
        lines.append("---")
        lines.append("")
    page_md = "\n".join(lines).strip() + "\n"
    Path(md_path).write_text(page_md, encoding="utf-8")
    return md_path


def _load_reference_schema(folder: Path) -> List[str]:
    candidates = sorted(folder.glob("*.json"))
    for fp in candidates:
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return list(data.keys())
        except Exception:
            continue
    return []


def _align_with_reference_schema(result: Dict[str, Any], keys: List[str]) -> Dict[str, Any]:
    if not keys:
        return result
    aligned = dict(result)
    for k in keys:
        aligned.setdefault(k, None)
    return aligned


def parse_pdf_to_json(pdf_path: str, output_path: str) -> Dict[str, Any]:
    meta, pages = _extract_pages(pdf_path)
    sections = _build_sections(pages)
    chunks = _split_chunks(sections)
    tables = _extract_tables(pdf_path, sections)
    images = _extract_images(pdf_path, output_path, sections, pages)
    formulas, formulas_detail = _find_chemical_formulas(doc_text="\n\n".join([p.text for p in pages if p.text]), tables=tables, images=images)
    markdown_path, _ = _build_markdown(pdf_path, output_path, meta, sections, tables, images, formulas_detail)

    page_rows = [
        {
            "page_num": p.page_num,
            "char_count": len(p.text),
            "text_preview": p.text[:260],
            "text": p.text,
        }
        for p in pages
    ]

    doc_text = "\n\n".join([p.text for p in pages if p.text])
    result: Dict[str, Any] = {
        "schema_version": "kb_pdf_parser_v1",
        "doc_meta": meta,
        "statistics": {
            "total_chars": len(doc_text),
            "section_count": len(sections),
            "chunk_count": len(chunks),
            "table_count": len(tables),
            "image_count": len(images),
            "non_empty_page_count": sum(1 for p in pages if p.text.strip()),
            "text_quality": round(_text_quality_score(doc_text), 4),
        },
        "document_keywords": _keywords(doc_text, top_k=18),
        "raw_pages": page_rows,
        "sections": sections,
        "chunks": chunks,
        "tables": tables,
        "images": images,
        "chemical_formulas": formulas,
        "chemical_formulas_detail": formulas_detail,
        "markdown": {
            "path": markdown_path,
        },
    }
    page_markdown_path = _build_markdown_by_pages(pdf_path, output_path, meta, page_rows)
    result["markdown"]["pages_path"] = page_markdown_path

    ref_keys = _load_reference_schema(Path(output_path).parent)
    aligned = _align_with_reference_schema(result, ref_keys)

    Path(output_path).write_text(json.dumps(aligned, ensure_ascii=False, indent=2), encoding="utf-8")
    return aligned


def main():
    parser = argparse.ArgumentParser(description="Parse PDF in script/knowledge_data and save structured JSON.")
    parser.add_argument("--pdf", required=False, default="", help="PDF file path")
    parser.add_argument("--out", required=False, default="", help="Output JSON path")
    parser.add_argument("--json", required=False, default="", help="Existing parsed JSON path")
    parser.add_argument("--emit-md-only", action="store_true", help="Only rebuild markdown from existing JSON")
    args = parser.parse_args()

    base_dir = Path(__file__).resolve().parent
    data_dir = base_dir / "knowledge_data"

    if args.pdf:
        pdf_path = Path(args.pdf)
    else:
        pdfs = sorted(data_dir.glob("*.pdf"))
        if not pdfs:
            raise FileNotFoundError(f"No PDF found in {data_dir}")
        pdf_path = pdfs[0]

    if args.out:
        out_path = Path(args.out)
    else:
        out_path = data_dir / f"{pdf_path.stem}.parsed.json"

    if args.emit_md_only:
        json_path = Path(args.json) if args.json else out_path
        if not json_path.exists():
            raise FileNotFoundError(f"Parsed json not found: {json_path}")
        data = json.loads(json_path.read_text(encoding="utf-8"))
        md_path, _ = _build_markdown(
            str(pdf_path),
            str(json_path),
            data.get("doc_meta", {}),
            data.get("sections", []),
            data.get("tables", []),
            data.get("images", []),
            data.get("chemical_formulas_detail", []),
        )
        page_md = _build_markdown_by_pages(
            str(pdf_path),
            str(json_path),
            data.get("doc_meta", {}),
            data.get("raw_pages", []),
        )
        data.setdefault("markdown", {})
        data["markdown"]["path"] = md_path
        data["markdown"]["pages_path"] = page_md
        json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[KB Parser] markdown rebuilt: {md_path}")
        print(f"[KB Parser] page markdown rebuilt: {page_md}")
        return

    print(f"[KB Parser] input: {pdf_path}")
    print(f"[KB Parser] output: {out_path}")
    result = parse_pdf_to_json(str(pdf_path), str(out_path))
    print(
        "[KB Parser] done:",
        {
            "pages": result.get("doc_meta", {}).get("page_count", 0),
            "sections": result.get("statistics", {}).get("section_count", 0),
            "chunks": result.get("statistics", {}).get("chunk_count", 0),
            "tables": result.get("statistics", {}).get("table_count", 0),
            "images": result.get("statistics", {}).get("image_count", 0),
        },
    )


if __name__ == "__main__":
    main()
