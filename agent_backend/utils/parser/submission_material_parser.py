import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


def _try_import_pdfplumber():
    try:
        import pdfplumber as _pdfplumber  # type: ignore

        return _pdfplumber
    except Exception:
        return None


def _normalize_text(text: str) -> str:
    t = (text or "").replace("\u3000", " ")
    t = re.sub(r"\r\n?", "\n", t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


@dataclass
class PageData:
    page_num: int
    lines: List[str]
    text: str


@dataclass
class HeadingHit:
    code: str
    title: str
    page_num: int
    line_idx: int
    raw_line: str


CTD_CODE_RE = re.compile(r"^(\d+(?:\.[0-9A-Za-z]+){1,12})(?:\s*[-—:：.]?\s*)(.*)$")
TOC_RE = re.compile(r"^(\d+(?:\.[0-9A-Za-z]+){1,12})\s*([^.]{1,120}?)\s*\.{2,}\s*(\d{1,4})\s*$")


def _extract_pages(pdf_path: str) -> Tuple[Dict[str, Any], List[PageData]]:
    pdfplumber = _try_import_pdfplumber()
    if pdfplumber is None:
        raise RuntimeError("pdfplumber not installed")

    pages: List[PageData] = []
    with pdfplumber.open(pdf_path) as doc:
        meta = {
            "file_name": os.path.basename(pdf_path),
            "file_path": os.path.abspath(pdf_path),
            "file_size": os.path.getsize(pdf_path) if os.path.exists(pdf_path) else 0,
            "page_count": len(doc.pages),
            "extractor": "pdfplumber",
        }

        for i, page in enumerate(doc.pages, start=1):
            raw = page.extract_text() or ""
            raw = _normalize_text(raw)
            lines = [x.strip() for x in raw.splitlines() if x.strip()]
            pages.append(PageData(page_num=i, lines=lines, text=raw))
    return meta, pages


def _is_probably_toc_page(page: PageData) -> bool:
    text = page.text
    if not text:
        return False
    has_catalog = ("目录" in text) or ("contents" in text.lower())
    dotted_count = len(re.findall(r"\.{2,}\s*\d+", text))
    return has_catalog or dotted_count >= 6


def _parse_toc_entries(pages: List[PageData], max_scan_pages: int = 24) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for p in pages[:max_scan_pages]:
        if not _is_probably_toc_page(p):
            continue
        for ln in p.lines:
            m = TOC_RE.match(ln)
            if not m:
                continue
            code, title, page_ref = m.group(1).strip(), m.group(2).strip(), int(m.group(3))
            out.append(
                {
                    "code": code,
                    "title": title,
                    "toc_page": p.page_num,
                    "target_page": page_ref,
                    "raw": ln,
                }
            )

    seen = set()
    uniq = []
    for x in out:
        k = (x["code"], x["title"])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(x)
    return uniq


def _line_is_noise(line: str) -> bool:
    s = line.strip()
    if not s:
        return True
    if re.fullmatch(r"[\-—_=·•*\s]+", s):
        return True
    if re.fullmatch(r"第?\s*\d+\s*页", s):
        return True
    if re.fullmatch(r"\d+", s):
        return True
    return False


def _infer_allowed_prefixes(toc_entries: List[Dict[str, Any]]) -> List[str]:
    codes = [str(x.get("code", "")).strip() for x in toc_entries if str(x.get("code", "")).strip()]
    if not codes:
        return []
    prefixes = set()
    for c in codes:
        parts = c.split(".")
        if len(parts) >= 3 and parts[2].isalpha():
            prefixes.add(".".join(parts[:3]))
        else:
            prefixes.add(".".join(parts[: min(2, len(parts))]))
    return sorted(prefixes)


def _code_allowed_by_toc(code: str, toc_codes: set, allowed_prefixes: List[str]) -> bool:
    if not toc_codes:
        return True
    if code in toc_codes:
        return True
    for tc in toc_codes:
        if code.startswith(tc + ".") or tc.startswith(code + "."):
            return True
    for p in allowed_prefixes:
        if code.startswith(p + ".") or code == p:
            return True
    return False


def _heading_title_quality_ok(title: str) -> bool:
    t = (title or "").strip()
    if not t:
        return True
    if len(t) > 120:
        return False
    if re.fullmatch(r"[0-9A-Za-z%±°/.,()\-\s]+", t):
        return False
    if re.search(r"(mg|g|mL|ml|ng|μg|L|℃|%)\b", t, flags=re.IGNORECASE) and len(t) < 20:
        return False
    return True


def _extract_heading_hits(
    pages: List[PageData], toc_pages: set, toc_entries: Optional[List[Dict[str, Any]]] = None
) -> List[HeadingHit]:
    hits: List[HeadingHit] = []
    toc_entries = toc_entries or []
    toc_codes = {str(x.get("code", "")).strip() for x in toc_entries if str(x.get("code", "")).strip()}
    allowed_prefixes = _infer_allowed_prefixes(toc_entries)

    for p in pages:
        if p.page_num in toc_pages:
            continue
        for idx, ln in enumerate(p.lines):
            if _line_is_noise(ln):
                continue
            m = CTD_CODE_RE.match(ln)
            if not m:
                continue

            code = m.group(1).strip()
            tail = (m.group(2) or "").strip()

            if code.count(".") < 1:
                continue
            if len(code) > 40:
                continue
            if re.search(r"[^0-9A-Za-z.]", code):
                continue
            segs = code.split(".")
            if len(segs) <= 2 and all(x.isdigit() for x in segs):
                continue
            if not _code_allowed_by_toc(code, toc_codes, allowed_prefixes):
                continue

            title = tail
            if not title and idx + 1 < len(p.lines):
                nxt = p.lines[idx + 1].strip()
                if nxt and not CTD_CODE_RE.match(nxt):
                    title = nxt

            title = re.sub(r"\.{2,}\s*\d+$", "", title).strip()
            title = title[:120]
            if not _heading_title_quality_ok(title):
                continue

            hits.append(HeadingHit(code=code, title=title, page_num=p.page_num, line_idx=idx, raw_line=ln))

    code_first: Dict[str, HeadingHit] = {}
    for h in sorted(hits, key=lambda x: (x.page_num, x.line_idx)):
        if h.code not in code_first:
            code_first[h.code] = h
        elif (not code_first[h.code].title) and h.title:
            code_first[h.code] = h

    return sorted(code_first.values(), key=lambda x: (x.page_num, x.line_idx))


def _slice_section_content(pages: List[PageData], hits: List[HeadingHit]) -> List[Dict[str, Any]]:
    if not hits:
        return []

    sections: List[Dict[str, Any]] = []
    ordered = sorted(hits, key=lambda x: (x.page_num, x.line_idx))

    for i, h in enumerate(ordered):
        nxt = ordered[i + 1] if i + 1 < len(ordered) else None

        content_lines: List[str] = []
        start_page = h.page_num
        end_page = h.page_num

        for p in pages:
            if p.page_num < h.page_num:
                continue
            if nxt and p.page_num > nxt.page_num:
                break

            l_start = 0
            l_end = len(p.lines)
            if p.page_num == h.page_num:
                l_start = h.line_idx + 1
            if nxt and p.page_num == nxt.page_num:
                l_end = nxt.line_idx

            if l_start < l_end:
                content_lines.extend(p.lines[l_start:l_end])
                end_page = p.page_num

        content = _normalize_text("\n".join(content_lines))
        sections.append(
            {
                "section_id": f"sec_{i+1}",
                "code": h.code,
                "title": h.title or h.code,
                "heading": f"{h.code} {h.title}".strip(),
                "page_start": start_page,
                "page_end": end_page,
                "char_count": len(content),
                "content": content,
                "content_preview": content[:260],
                "raw_heading_line": h.raw_line,
            }
        )

    return sections


def _build_section_tree(sections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_code: Dict[str, Dict[str, Any]] = {s["code"]: s for s in sections}
    roots: List[Dict[str, Any]] = []

    for s in sections:
        code = s["code"]
        parts = code.split(".")
        parent = None
        for i in range(len(parts) - 1, 0, -1):
            pc = ".".join(parts[:i])
            if pc in by_code:
                parent = pc
                break
        s["parent_code"] = parent
        s["children"] = []

    for s in sections:
        parent = s.get("parent_code")
        if parent and parent in by_code:
            by_code[parent]["children"].append(s)
        else:
            roots.append(s)
    return roots


def _build_chapter_structure_from_sections(sections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    nodes = []
    by_code = {}
    for s in sections:
        n = {
            "code": s.get("code"),
            "title": s.get("title"),
            "page_start": s.get("page_start"),
            "page_end": s.get("page_end"),
            "children": [],
        }
        nodes.append(n)
        by_code[n["code"]] = n

    roots = []
    for n in nodes:
        code = n["code"]
        parts = str(code).split(".")
        parent = None
        for i in range(len(parts) - 1, 0, -1):
            pc = ".".join(parts[:i])
            if pc in by_code:
                parent = pc
                break
        if parent:
            by_code[parent]["children"].append(n)
        else:
            roots.append(n)
    return roots


def _annotate_sections_and_aggregate_leaf_siblings(
    sections: List[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    by_code: Dict[str, Dict[str, Any]] = {s["code"]: s for s in sections}
    ordered = sorted(sections, key=lambda x: int(str(x.get("section_id", "sec_0")).split("_")[-1]))

    children_by_parent: Dict[str, List[Dict[str, Any]]] = {}
    for s in ordered:
        parent = s.get("parent_code")
        if parent:
            children_by_parent.setdefault(parent, []).append(s)

    for s in ordered:
        code = str(s.get("code", ""))
        parent_code = s.get("parent_code")
        parent_section = by_code.get(parent_code) if parent_code else None
        s["level"] = len(code.split(".")) if code else 1
        s["parent_title"] = parent_section.get("title") if parent_section else None
        s["parent_section_id"] = parent_section.get("section_id") if parent_section else None
        s["is_leaf"] = code not in children_by_parent

    leaf_sibling_groups: List[Dict[str, Any]] = []
    gid = 0
    for parent_code, children in children_by_parent.items():
        leaf_children = [c for c in children if c.get("is_leaf")]
        if not leaf_children:
            continue
        gid += 1
        leaf_children = sorted(leaf_children, key=lambda x: int(str(x.get("section_id", "sec_0")).split("_")[-1]))

        group_content_parts = []
        total_chars = 0
        for c in leaf_children:
            title = str(c.get("title", c.get("code", "")))
            content = str(c.get("content", "") or "").strip()
            block = f"## {c.get('code')} {title}\n{content}" if content else f"## {c.get('code')} {title}"
            group_content_parts.append(block)
            total_chars += len(content)
            c["leaf_sibling_group_id"] = f"group_{gid}"

        parent_section = by_code.get(parent_code)
        agg_text = "\n\n".join(group_content_parts).strip()
        leaf_sibling_groups.append(
            {
                "group_id": f"group_{gid}",
                "parent_code": parent_code,
                "parent_title": parent_section.get("title") if parent_section else "",
                "parent_section_id": parent_section.get("section_id") if parent_section else None,
                "child_count": len(leaf_children),
                "child_codes": [x.get("code") for x in leaf_children],
                "page_start": min(int(x.get("page_start", 0)) for x in leaf_children),
                "page_end": max(int(x.get("page_end", 0)) for x in leaf_children),
                "char_count": total_chars,
                "content": agg_text,
                "content_preview": agg_text[:320],
            }
        )

    return ordered, leaf_sibling_groups


def _build_review_units(sections: List[Dict[str, Any]], leaf_sibling_groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    def _code_sort_key(code: str) -> Tuple:
        parts = str(code or "").split(".")
        key = []
        for p in parts:
            if p.isdigit():
                key.append((0, int(p)))
            else:
                key.append((1, p))
        return tuple(key)

    units: List[Dict[str, Any]] = []

    # 1) leaf sibling groups as primary review unit
    for g in leaf_sibling_groups:
        text = str(g.get("content", "")).strip()
        if not text:
            continue
        units.append(
            {
                "chunk_id": str(g.get("group_id")),
                "section_id": str(g.get("group_id")),
                "section_code": str(g.get("parent_code", "")),
                "section_name": str(g.get("parent_title", "")) or str(g.get("parent_code", "")),
                "parent_section_id": g.get("parent_section_id"),
                "parent_code": str(g.get("parent_code", "")),
                "page_start": g.get("page_start"),
                "page_end": g.get("page_end"),
                "page": g.get("page_start"),
                "char_count": len(text),
                "text": text,
                "unit_type": "leaf_sibling_group",
                "source_section_codes": g.get("child_codes", []),
                "title_path": [str(g.get("parent_title", ""))] if str(g.get("parent_title", "")) else [],
            }
        )

    # 2) fallback leaf section units
    grouped_codes = set()
    for g in leaf_sibling_groups:
        for c in g.get("child_codes", []):
            grouped_codes.add(str(c))

    for s in sections:
        code = str(s.get("code", ""))
        if code in grouped_codes:
            continue
        if not bool(s.get("is_leaf")):
            continue
        text = str(s.get("content", "")).strip()
        if not text:
            continue
        units.append(
            {
                "chunk_id": str(s.get("section_id")),
                "section_id": str(s.get("section_id")),
                "section_code": code,
                "section_name": str(s.get("title", code)),
                "parent_section_id": s.get("parent_section_id"),
                "parent_code": str(s.get("parent_code", "")),
                "page_start": s.get("page_start"),
                "page_end": s.get("page_end"),
                "page": s.get("page_start"),
                "char_count": len(text),
                "text": text,
                "unit_type": "leaf_section",
                "source_section_codes": [code],
                "title_path": [
                    str(x)
                    for x in [s.get("parent_title", ""), s.get("title", "")]
                    if str(x).strip()
                ],
            }
        )
    ordered = sorted(
        units,
        key=lambda x: (
            _code_sort_key(str(x.get("section_code", ""))),
            int(x.get("page_start") or 0),
            str(x.get("section_id")),
        ),
    )
    for i, u in enumerate(ordered, start=1):
        u["unit_order"] = i
    return ordered


def parse_submission_material(file_path: str) -> Dict[str, Any]:
    meta, pages = _extract_pages(file_path)
    toc_entries = _parse_toc_entries(pages)
    toc_pages = set(x["toc_page"] for x in toc_entries)

    heading_hits = _extract_heading_hits(pages, toc_pages=toc_pages, toc_entries=toc_entries)
    sections = _slice_section_content(pages, heading_hits)

    toc_map = {x["code"]: x["title"] for x in toc_entries}
    for s in sections:
        if (not s.get("title")) or s.get("title") == s.get("code"):
            if s["code"] in toc_map and toc_map[s["code"]]:
                s["title"] = toc_map[s["code"]]

    section_tree = _build_section_tree(sections)
    sections, leaf_sibling_groups = _annotate_sections_and_aggregate_leaf_siblings(sections)
    chapter_structure = _build_chapter_structure_from_sections(sections)
    review_units = _build_review_units(sections, leaf_sibling_groups)

    found_codes = {s["code"] for s in sections}
    toc_codes = [x["code"] for x in toc_entries]
    toc_missing = [x for x in toc_entries if x["code"] not in found_codes]

    return {
        "schema_version": "submission_material_v2",
        "doc_meta": meta,
        "statistics": {
            "toc_entry_count": len(toc_entries),
            "heading_hit_count": len(heading_hits),
            "section_count": len(sections),
            "leaf_sibling_group_count": len(leaf_sibling_groups),
            "review_unit_count": len(review_units),
            "toc_covered_count": len(toc_codes) - len(toc_missing),
            "toc_coverage_ratio": round((len(toc_codes) - len(toc_missing)) / len(toc_codes), 4) if toc_codes else 0.0,
            "non_empty_page_count": sum(1 for p in pages if p.text),
        },
        "toc_entries": toc_entries,
        "toc_missing_sections": toc_missing,
        "sections": sections,
        "section_tree": section_tree,
        "chapter_structure": chapter_structure,
        "leaf_sibling_groups": leaf_sibling_groups,
        "review_units": review_units,
        "raw_pages": [
            {
                "page_num": p.page_num,
                "line_count": len(p.lines),
                "text_preview": p.text[:260],
            }
            for p in pages
        ],
    }
