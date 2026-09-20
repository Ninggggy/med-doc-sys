from __future__ import annotations

import base64
import copy
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import fitz
from openai import OpenAI

from agent.agent_backend.llm.model_setting import get_model_conf


PROMPTS = {
    "document_to_markdown": "<image>\n<|grounding|>Convert the document to markdown.",
    "parse_figure": "<image>\nParse the figure.",
}

PAGE_MARKER_RE = re.compile(r"<!--PAGE:(\d+)-->")
SECTION_ID_RE = re.compile(r"3\.2\.[a-z]\.\d+(?:\.\d+)*", flags=re.IGNORECASE)
PAGE_NUMBER_RE = re.compile(r"^\s*(?:第\s*\d+\s*页|\d+\s*/\s*\d+|-+\s*\d+\s*-+|\d+)\s*$")


def _normalize_section_id(text: str) -> str:
    value = str(text or "")
    value = re.sub(r"(?<=\d)\s*\.\s*(?=\d|[A-Za-z])", ".", value)
    value = re.sub(r"(?<=[A-Za-z])\s*\.\s*(?=\d)", ".", value)
    value = re.sub(r"\.{2,}", ".", value)
    value = re.sub(r"(?<=\.)\s*([A-Za-z])\s*(?=\.)", lambda m: m.group(1).lower(), value)
    return value


def _normalize_text(text: str) -> str:
    value = str(text or "")
    value = re.sub(r"<\|ref\|>.*?<\|/ref\|>", "", value, flags=re.DOTALL)
    value = re.sub(r"<\|det\|>.*?<\|/det\|>", "", value, flags=re.DOTALL)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = _normalize_section_id(value)
    value = re.sub(r"[ \t]+\n", "\n", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _is_toc_line(line: str) -> bool:
    value = str(line or "").strip()
    if value in {"目录", "目 录"}:
        return True
    return len(SECTION_ID_RE.findall(value)) >= 3


def _normalize_page_text(text: str) -> str:
    lines: List[str] = []
    for raw_line in _normalize_text(text).splitlines():
        line = raw_line.strip()
        if not line:
            lines.append("")
            continue
        if PAGE_NUMBER_RE.fullmatch(line):
            continue
        if _is_toc_line(line):
            continue
        line = re.sub(
            r"(?<!\n)(中文名：|英文名：|中文化学名：|英文化学名：|CAS号：|结构式：|分子式：|分子量：|性状：|溶解性：|比旋度：|生产商名称：|注册地址：|生产地址：|邮编：|电话：|传真：)",
            r"\n\1",
            line,
        )
        lines.append(line)
    value = "\n".join(lines)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _clean_body(text: str) -> str:
    out: List[str] = []
    for raw_line in _normalize_text(text).splitlines():
        line = raw_line.strip()
        if not line:
            out.append("")
            continue
        if line.startswith("<!--PAGE:"):
            out.append(line)
            continue
        if PAGE_NUMBER_RE.fullmatch(line) or _is_toc_line(line):
            continue
        if re.match(r"^\s*(?:\*\*\s*)?(?:#+\s*)?3\.2\.[a-z]\.\d+(?:\.\d+)?\s*", line, flags=re.IGNORECASE):
            continue
        line = re.sub(r"^\s*#{1,6}\s*", "", line)
        if re.match(r"^(步骤|表|图|附图|Figure|Scheme)\s*\d+", line, flags=re.IGNORECASE):
            line = f"**{line}**"
        elif re.match(r"^[（(]?[一二三四五六七八九十]+[）)]", line):
            line = f"- {line}"
        elif re.match(r"^\d+[、.]", line):
            line = f"- {line}"
        out.append(line)
    value = "\n".join(out)
    value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def _build_anchor_patterns(section_id: str, section_name: str) -> List[re.Pattern]:
    sid = re.escape(section_id)
    sname = re.escape(section_name)
    return [
        re.compile(rf"(?m)^\s*(?:\*\*\s*)?(?:#+\s*)?{sid}\s*{sname}\s*(?:\*\*)?\s*$", flags=re.IGNORECASE),
        re.compile(rf"(?m)^\s*(?:\*\*\s*)?(?:#+\s*)?{sid}\s*(?:\*\*)?\s*$", flags=re.IGNORECASE),
        re.compile(rf"(?m)^\s*(?:\*\*\s*)?(?:#+\s*)?{sname}\s*(?:\*\*)?\s*$"),
        re.compile(rf"(?m)(?<!\d)\d*({sid})\s*{sname}", flags=re.IGNORECASE),
        re.compile(rf"(?m)(?<!\d)\d*({sid})(?!\.\d)", flags=re.IGNORECASE),
    ]


def _find_anchor(text: str, start: int, section_id: str, section_name: str) -> Tuple[int, int] | None:
    best: Tuple[int, int] | None = None
    for pattern in _build_anchor_patterns(section_id, section_name):
        match = pattern.search(text, start)
        if not match:
            continue
        anchor_start = match.start(1) if match.lastindex else match.start()
        candidate = (anchor_start, match.end())
        if best is None or candidate[0] < best[0]:
            best = candidate
    return best


def _find_ordered_anchors(text: str, sections: Iterable[Dict[str, Any]]) -> List[Tuple[int, int, Dict[str, Any]]]:
    anchors: List[Tuple[int, int, Dict[str, Any]]] = []
    cursor = 0
    for section in sections:
        match = _find_anchor(text, cursor, str(section.get("section_id", "")), str(section.get("section_name", "")))
        if not match:
            continue
        anchors.append((match[0], match[1], section))
        cursor = match[0] + 1
    return anchors


def _extract_page_numbers(text: str) -> List[int]:
    return sorted({int(item) for item in PAGE_MARKER_RE.findall(text or "")})


def _strip_page_markers(text: str) -> str:
    return PAGE_MARKER_RE.sub("", text or "")


def _split_leaf_block(block_text: str, children: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    contents: Dict[str, Dict[str, Any]] = {}
    if not children:
        return contents
    child_anchors = _find_ordered_anchors(block_text, children)
    if not child_anchors:
        body = _clean_body(block_text)
        if body:
            cleaned = _strip_page_markers(body).strip()
            pages = _extract_page_numbers(body)
            contents[str(children[0]["section_id"])] = {"content": cleaned, "pages": pages}
        return contents

    first_index = next(index for index, child in enumerate(children) if child["section_id"] == child_anchors[0][2]["section_id"])
    leading = _clean_body(block_text[: child_anchors[0][0]])
    if leading:
        target_id = str(children[first_index]["section_id"] if first_index == 0 else children[0]["section_id"])
        contents[target_id] = {
            "content": _strip_page_markers(leading).strip(),
            "pages": _extract_page_numbers(leading),
        }

    for index, (_, end, child) in enumerate(child_anchors):
        next_start = child_anchors[index + 1][0] if index + 1 < len(child_anchors) else len(block_text)
        body = _clean_body(block_text[end:next_start])
        child_id = str(child["section_id"])
        if not body:
            contents.setdefault(child_id, {"content": "", "pages": []})
            continue
        merged = "\n\n".join(
            part for part in [contents.get(child_id, {}).get("content", ""), _strip_page_markers(body).strip()] if part
        ).strip()
        merged_pages = sorted(set(contents.get(child_id, {}).get("pages", []) + _extract_page_numbers(body)))
        contents[child_id] = {"content": merged, "pages": merged_pages}
    return contents


def _split_outline_contents(text: str, outline: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    contents: Dict[str, Dict[str, Any]] = {}
    parents = [{"section_id": item["section_id"], "section_name": item["section_name"]} for item in outline]
    parent_anchors = _find_ordered_anchors(text, parents)
    if not parent_anchors:
        return contents
    for index, (_, end, section) in enumerate(parent_anchors):
        next_start = parent_anchors[index + 1][0] if index + 1 < len(parent_anchors) else len(text)
        block_text = text[end:next_start]
        item = next(node for node in outline if node["section_id"] == section["section_id"])
        children = [child for child in item.get("children_sections", []) if isinstance(child, dict)]
        if children:
            contents.update(_split_leaf_block(block_text, children))
            continue
        body = _clean_body(block_text)
        if body:
            contents[str(item["section_id"])] = {
                "content": _strip_page_markers(body).strip(),
                "pages": _extract_page_numbers(body),
            }
    return contents


class DeepSeekOCRDocumentClient:
    def __init__(self, model_name: str = "deepseek_ocr"):
        conf = get_model_conf(model_name)
        self.model = str(conf.get("model", "")).strip()
        self.base_url = str(conf.get("base_url", "")).strip()
        self.timeout = int(conf.get("timeout", 60))
        api_key_path = Path(str(conf.get("api_key_path", "") or "")).expanduser()
        self.api_key = api_key_path.read_text(encoding="utf-8").strip() if api_key_path.exists() else ""
        if not self.model:
            raise ValueError("deepseek ocr model is empty")
        if not self.base_url:
            raise ValueError("deepseek ocr base_url is empty")
        if not self.api_key:
            raise ValueError(f"deepseek ocr api key not found: {api_key_path}")
        self.client = OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=self.timeout)


    def _render_prompt(self, prompt_key: str) -> str:
        return PROMPTS.get(prompt_key, PROMPTS["document_to_markdown"])

    @staticmethod
    def _pixmap_data_url(pixmap: fitz.Pixmap) -> str:
        raw = base64.b64encode(pixmap.tobytes("png")).decode("utf-8")
        return f"data:image/png;base64,{raw}"

    def run(self, pixmap: fitz.Pixmap, prompt_key: str, max_tokens: int = 8000) -> str:
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                max_tokens=max_tokens,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": self._pixmap_data_url(pixmap)}},
                            {"type": "text", "text": self._render_prompt(prompt_key)},
                        ],
                    }
                ],
            )
            content = resp.choices[0].message.content if resp and resp.choices else ""
            return str(content or "").strip()
        except Exception as exc:
            raise RuntimeError(
                f"deepseek ocr request failed: prompt={prompt_key}, model={self.model}, base_url={self.base_url}, error={exc}"
            ) from exc


def _extract_image_enhancements(page: fitz.Page, page_text: str, client: DeepSeekOCRDocumentClient) -> str:
    blocks = page.get_text("dict").get("blocks", [])
    image_blocks = [
        block
        for block in blocks
        if block.get("type") == 1 and (block["bbox"][2] - block["bbox"][0]) >= 80 and (block["bbox"][3] - block["bbox"][1]) >= 50
    ]
    if not image_blocks:
        return page_text
    caption_lines = [
        line.strip()
        for line in page_text.splitlines()
        if re.match(r"^(图|附图|Figure|Scheme|结构式|工艺流程图)", line.strip(), flags=re.IGNORECASE)
    ]
    used = set()
    value = page_text
    for index, block in enumerate(image_blocks, start=1):
        caption = caption_lines[index - 1] if index - 1 < len(caption_lines) else f"图片 第{page.number + 1}页-{index}"
        figure_pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=fitz.Rect(block["bbox"]), alpha=False)
        figure_text = _normalize_page_text(client.run(figure_pix, "parse_figure", max_tokens=3000))
        placeholder = f"![{caption}](image://page/{page.number + 1}/{index})"
        if caption in value and caption not in used:
            value = value.replace(caption, f"{caption}\n{placeholder}", 1)
            used.add(caption)
        else:
            value = f"{value}\n\n{placeholder}".strip()
        if figure_text:
            value = f"{value}\n{figure_text}".strip()
    return value


def _render_deepseek_markdown_document(file_path: str, client: DeepSeekOCRDocumentClient) -> Tuple[str, int]:
    page_texts: List[str] = []
    with fitz.open(file_path) as pdf:
        for page_index in range(pdf.page_count):
            page = pdf.load_page(page_index)
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            page_text = _normalize_page_text(client.run(pix, "document_to_markdown"))
            page_text = _extract_image_enhancements(page, page_text, client)
            if page_text:
                page_texts.append(f"<!--PAGE:{page_index + 1}-->\n{page_text}")
    return "\n\n".join(page_texts).strip(), len(page_texts)


def _iter_leaf_sections(outline: List[Dict[str, Any]], parent_title_path: List[str] | None = None) -> Iterable[Dict[str, Any]]:
    title_path = list(parent_title_path or [])
    for item in outline:
        section_id = str(item.get("section_id", "") or "").strip()
        section_name = str(item.get("section_name", "") or section_id).strip() or section_id
        current_path = title_path + [section_name]
        children = [child for child in item.get("children_sections", []) if isinstance(child, dict)]
        if children:
            yield from _iter_leaf_sections(children, current_path)
            continue
        yield {
            "section_id": section_id,
            "section_code": section_id,
            "section_name": section_name,
            "parent_section_id": "",
            "title_path": current_path,
        }


def _build_review_units_from_leaf_sections(leaf_sections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    review_units: List[Dict[str, Any]] = []
    for idx, section in enumerate(leaf_sections or [], start=1):
        text = _normalize_text(section.get("content", "") or "")
        if not text:
            continue
        review_units.append(
            {
                "chunk_id": section["section_id"],
                "section_id": section["section_id"],
                "section_code": section["section_code"],
                "section_name": section["section_name"],
                "parent_section_id": section.get("parent_section_id", ""),
                "page": section.get("page_start"),
                "page_start": section.get("page_start"),
                "page_end": section.get("page_end"),
                "text": text,
                "title_path": list(section.get("title_path") or [section["section_name"]]),
                "unit_order": idx,
                "unit_type": "ctd_leaf_section",
                "char_count": len(text),
            }
        )
    return review_units


def _resolve_root_outline(catalog: Dict[str, Any], root_section_id: str) -> Tuple[List[Dict[str, Any]], str]:
    root_key = str(root_section_id or "").strip()
    if not root_key:
        raise ValueError("root_section_id is required")
    if not isinstance(catalog, dict):
        raise ValueError("catalog is required")
    for node in catalog.get("chapter_structure", []) or []:
        if not isinstance(node, dict):
            continue
        if str(node.get("section_id", "") or "").strip() == root_key:
            children = [child for child in (node.get("children_sections") or []) if isinstance(child, dict)]
            if not children:
                raise ValueError(f"root section {root_key} has no children")
            section_name = str(node.get("section_name", "") or root_key).strip() or root_key
            return children, section_name
    raise ValueError(f"root section not found: {root_key}")


def parse_ctd_submission_with_deepseek_ocr_to_payload(
    file_path: str,
    *,
    catalog: Dict[str, Any],
    root_section_id: str,
) -> Dict[str, Any]:
    pdf_path = Path(file_path).expanduser().resolve()
    outline, root_name = _resolve_root_outline(catalog, root_section_id)
    ocr_client = DeepSeekOCRDocumentClient("deepseek_ocr")
    merged_text, page_total = _render_deepseek_markdown_document(str(pdf_path), ocr_client)
    content_map = _split_outline_contents(merged_text, outline)

    leaf_sections: List[Dict[str, Any]] = []
    for leaf in _iter_leaf_sections(copy.deepcopy(outline)):
        section_id = str(leaf["section_id"])
        content_info = content_map.get(section_id, {})
        content = str(content_info.get("content", "") or "").strip()
        raw_pages = list(content_info.get("pages", []) or [])
        parent_section_id = ""
        for parent in outline:
            children = [child for child in parent.get("children_sections", []) if isinstance(child, dict)]
            if any(str(child.get("section_id", "")).strip() == section_id for child in children):
                parent_section_id = str(parent.get("section_id", "") or "").strip()
                break
        leaf_sections.append(
            {
                "section_id": section_id,
                "section_code": section_id,
                "section_name": str(leaf["section_name"]),
                "title_path": list(leaf.get("title_path") or [str(leaf["section_name"])]),
                "parent_section_id": parent_section_id,
                "content": content,
                "content_preview": content[:320],
                "char_count": len(content),
                "raw_pages": raw_pages,
                "page_start": min(raw_pages) if raw_pages else None,
                "page_end": max(raw_pages) if raw_pages else None,
                "tables": [],
            }
        )

    review_units = _build_review_units_from_leaf_sections(leaf_sections)
    return {
        "title": f"{root_name}解析结果",
        "source_pdf": str(pdf_path),
        "root_section_id": str(root_section_id or "").strip(),
        "structure_type": "ctd_leaf_section_payload_v1",
        "parser_mode": "detailed",
        "source_parser": "ctd_deepseek_ocr",
        "anchors": [],
        "pages": {"page_total": page_total},
        "sections": leaf_sections,
        "review_units": review_units,
        "statistics": {
            "section_total": len(leaf_sections),
            "review_unit_total": len(review_units),
        },
    }
