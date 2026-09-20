#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import base64
import copy
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import fitz
import numpy as np
import requests
from agent.agent_backend.utils.parser.ocr_request_budget import request_budgets

OUTLINE_S = [
    {"section_id": "3.2.s.1", "section_name": "基本信息", "children_sections": [
        {"section_id": "3.2.s.1.1", "section_name": "药品名称"},
        {"section_id": "3.2.s.1.2", "section_name": "结构"},
        {"section_id": "3.2.s.1.3", "section_name": "理化性质"},
    ]},
    {"section_id": "3.2.s.2", "section_name": "生产", "children_sections": [
        {"section_id": "3.2.s.2.1", "section_name": "生产商"},
        {"section_id": "3.2.s.2.2", "section_name": "生产工艺和工艺控制"},
        {"section_id": "3.2.s.2.3", "section_name": "物料控制"},
        {"section_id": "3.2.s.2.4", "section_name": "关键步骤和中间体的控制"},
        {"section_id": "3.2.s.2.5", "section_name": "工艺验证"},
        {"section_id": "3.2.s.2.6", "section_name": "生产工艺的开发"},
    ]},
    {"section_id": "3.2.s.3", "section_name": "特性鉴定", "children_sections": [
        {"section_id": "3.2.s.3.1", "section_name": "结构和理化性质"},
        {"section_id": "3.2.s.3.2", "section_name": "杂质"},
    ]},
    {"section_id": "3.2.s.4", "section_name": "原料药的质量控制", "children_sections": [
        {"section_id": "3.2.s.4.1", "section_name": "质量标准"},
        {"section_id": "3.2.s.4.2", "section_name": "分析方法"},
        {"section_id": "3.2.s.4.3", "section_name": "分析方法的验证"},
        {"section_id": "3.2.s.4.4", "section_name": "批分析"},
        {"section_id": "3.2.s.4.5", "section_name": "质量标准制定依据"},
    ]},
    {"section_id": "3.2.s.5", "section_name": "对照品"},
    {"section_id": "3.2.s.6", "section_name": "包装系统"},
    {"section_id": "3.2.s.7", "section_name": "稳定性", "children_sections": [
        {"section_id": "3.2.s.7.1", "section_name": "稳定性总结和结论"},
        {"section_id": "3.2.s.7.2", "section_name": "批准后稳定性研究方案和承诺"},
        {"section_id": "3.2.s.7.3", "section_name": "稳定性数据"},
    ]},
]

OUTLINE_P = [
    {"section_id": "3.2.p.1", "section_name": "剂型及产品组成"},
    {"section_id": "3.2.p.2", "section_name": "产品开发", "children_sections": [
        {"section_id": "3.2.p.2.1", "section_name": "处方组成"},
        {"section_id": "3.2.p.2.2", "section_name": "制剂"},
        {"section_id": "3.2.p.2.3", "section_name": "生产工艺的开发"},
        {"section_id": "3.2.p.2.4", "section_name": "包装系统"},
        {"section_id": "3.2.p.2.5", "section_name": "微生物属性"},
        {"section_id": "3.2.p.2.6", "section_name": "相容性"},
    ]},
    {"section_id": "3.2.p.3", "section_name": "生产", "children_sections": [
        {"section_id": "3.2.p.3.1", "section_name": "生产商"},
        {"section_id": "3.2.p.3.2", "section_name": "批处方"},
        {"section_id": "3.2.p.3.3", "section_name": "生产工艺和工艺控制"},
        {"section_id": "3.2.p.3.4", "section_name": "关键步骤和中间产品的控制"},
        {"section_id": "3.2.p.3.5", "section_name": "工艺验证"},
    ]},
    {"section_id": "3.2.p.4", "section_name": "辅料的控制"},
    {"section_id": "3.2.p.5", "section_name": "制剂的质量控制", "children_sections": [
        {"section_id": "3.2.p.5.1", "section_name": "质量标准"},
        {"section_id": "3.2.p.5.2", "section_name": "分析方法"},
        {"section_id": "3.2.p.5.3", "section_name": "分析方法的验证"},
        {"section_id": "3.2.p.5.4", "section_name": "批分析"},
        {"section_id": "3.2.p.5.5", "section_name": "杂质分析"},
        {"section_id": "3.2.p.5.6", "section_name": "质量标准制定依据"},
    ]},
    {"section_id": "3.2.p.6", "section_name": "对照品"},
    {"section_id": "3.2.p.7", "section_name": "包装系统"},
    {"section_id": "3.2.p.8", "section_name": "稳定性", "children_sections": [
        {"section_id": "3.2.p.8.1", "section_name": "稳定性总结和结论"},
        {"section_id": "3.2.p.8.2", "section_name": "批准后稳定性研究方案和承诺"},
        {"section_id": "3.2.p.8.3", "section_name": "稳定性数据"},
    ]},
]

GENERIC_ID_RE = re.compile(r"^(3\.2\.[A-Za-z]\.\d+(?:\.\d+)*)\b", re.IGNORECASE)
DEEPER_ID_RE = re.compile(r"^(3\.2\.[A-Za-z]\.\d+(?:\.\d+){2,})\b", re.IGNORECASE)
PAGE_NUM_RE = re.compile(r"^\s*(第\s*)?\d+(\s*页)?\s*$")
REF_CONTEXT_RE = re.compile(r"(参见|见|详见|附件|附录|表|图|附表|附图)")

def str2bool(v: str) -> bool:
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}

def normalize_text(s: str) -> str:
    if s is None:
        return ""
    s = str(s).replace("\u3000", " ").replace("\xa0", " ")
    s = s.replace("—", "-").replace("–", "-")
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r"\s+\n", "\n", s)
    s = re.sub(r"\n\s+", "\n", s)
    return s.strip()

def normalize_heading_text(s: str) -> str:
    s = normalize_text(s)
    return re.sub(r"[：:;；,.，。/\\（）()\[\]【】\- ]+", "", s)

def escape_md(text: str) -> str:
    return str(text).replace("\n", " ").strip().replace("|", "\\|")

def markdown_table(columns: List[str], data: List[List[str]]) -> str:
    if not columns:
        return ""
    out = []
    out.append("| " + " | ".join(escape_md(c) for c in columns) + " |")
    out.append("| " + " | ".join("---" for _ in columns) + " |")
    for row in data:
        row = row + [""] * (len(columns) - len(row))
        out.append("| " + " | ".join(escape_md(c) for c in row[: len(columns)]) + " |")
    return "\n".join(out)

def markdownize_text_line(text: str) -> str:
    text = normalize_text(text)
    if not text:
        return ""
    m = re.match(r"^\s*([（(]?\d+[）)])\s*(.+)$", text)
    if m:
        return f"1. {m.group(2).strip()}"
    m = re.match(r"^\s*([一二三四五六七八九十]+[、.])\s*(.+)$", text)
    if m:
        return f"- {m.group(2).strip()}"
    m = re.match(r"^\s*([\-•·●])\s*(.+)$", text)
    if m:
        return f"- {m.group(2).strip()}"
    return text

def flatten_sections(outline):
    sections = []
    def walk(items):
        for item in items:
            sections.append({"section_id": item["section_id"], "section_name": item["section_name"]})
            if item.get("children_sections"):
                walk(item["children_sections"])
    walk(outline)
    sections.sort(key=lambda x: (len(x["section_id"]), x["section_id"]), reverse=True)
    return sections

def build_allowed_ids(outline):
    return {x["section_id"] for x in flatten_sections(outline)}

def init_outline(outline):
    tree = copy.deepcopy(outline)
    def walk(items):
        for item in items:
            item.setdefault("content", "")
            item.setdefault("tables", [])
            item.setdefault("raw_pages", [])
            item.setdefault("_content_parts", [])
            if item.get("children_sections"):
                walk(item["children_sections"])
    walk(tree)
    return tree

def clean_internal_fields(items):
    for item in items:
        item.pop("_content_parts", None)
        if item.get("children_sections"):
            clean_internal_fields(item["children_sections"])

def find_section_node(items, section_id):
    for item in items:
        if item["section_id"] == section_id:
            return item
        if item.get("children_sections"):
            found = find_section_node(item["children_sections"], section_id)
            if found:
                return found
    return None

def append_unique_page(node, page_no):
    if page_no not in node["raw_pages"]:
        node["raw_pages"].append(page_no)

class OCRServiceClient:
    supports_execution_budget = True
    supports_preprocessing = True
    def __init__(self, base_url: str, timeout: int = 120):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def healthcheck(self) -> bool:
        try:
            r = requests.get(f"{self.base_url}/health", timeout=10)
            r.raise_for_status()
            return r.json().get("status") == "ok"
        except Exception:
            return False

    def image_to_data(self, image_bytes: bytes, lang: str = "chi_sim+eng", psm: int = 6, execution_budget_seconds=None, preprocessing='standard'):
        files = {"file": ("image.png", image_bytes, "image/png")}
        data = {"lang": lang, "psm": str(psm)}
        if preprocessing not in ('standard', 'original_gray'):
            raise ValueError('invalid_preprocessing')
        if preprocessing != 'standard':
            data['preprocessing'] = preprocessing
        request_timeout, execution_budget = request_budgets(self.timeout, execution_budget_seconds)
        data['execution_budget_seconds'] = str(execution_budget)
        r = requests.post(f"{self.base_url}/ocr/data", files=files, data=data,
                          timeout=request_timeout)
        r.raise_for_status()
        return r.json()["data"]

    def image_to_text(self, image_bytes: bytes, lang: str = "chi_sim+eng", psm: int = 6):
        files = {"file": ("image.png", image_bytes, "image/png")}
        data = {"lang": lang, "psm": str(psm)}
        r = requests.post(f"{self.base_url}/ocr/text", files=files, data=data, timeout=self.timeout)
        r.raise_for_status()
        return r.json()["text"]

def png_bytes_from_page(page: fitz.Page, zoom: float = 2.0) -> bytes:
    return page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False).tobytes("png")

def png_bytes_from_image(img: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise RuntimeError("failed to encode image")
    return buf.tobytes()

def merge_wrapped_lines(lines):
    if not lines:
        return []
    merged = [lines[0]]
    for cur in lines[1:]:
        prev = merged[-1]
        gap = cur["bbox"][1] - prev["bbox"][3]
        same_block = gap < 5.5
        should_merge = (
            same_block and prev["text"] and cur["text"]
            and not GENERIC_ID_RE.match(cur["text"])
            and not prev["text"].endswith(("。", "；", "：", ":", ".", "）", ")", "|"))
        )
        if should_merge:
            prev["text"] = normalize_text(prev["text"] + " " + cur["text"])
            prev["bbox"] = [
                min(prev["bbox"][0], cur["bbox"][0]),
                min(prev["bbox"][1], cur["bbox"][1]),
                max(prev["bbox"][2], cur["bbox"][2]),
                max(prev["bbox"][3], cur["bbox"][3]),
            ]
            prev["words"].extend(cur["words"])
        else:
            merged.append(cur)
    return merged

def extract_page_lines_pdf(page):
    words = page.get_text("words")
    if not words:
        return []
    rows = {}
    for w in words:
        x0, y0, x1, y1, text, *_ = w
        rows.setdefault(round(y0, 1), []).append((x0, y0, x1, y1, str(text)))
    lines = []
    for key in sorted(rows.keys()):
        row_words = sorted(rows[key], key=lambda t: t[0])
        x0 = min(w[0] for w in row_words); y0 = min(w[1] for w in row_words)
        x1 = max(w[2] for w in row_words); y1 = max(w[3] for w in row_words)
        line_text = " ".join(w[4] for w in row_words)
        lines.append({"type": "line", "text": normalize_text(line_text), "bbox": [x0, y0, x1, y1], "words": row_words, "source": "pdf"})
    return merge_wrapped_lines(lines)

def ocr_page_lines(page, client, lang="chi_sim+eng", zoom=2.0):
    data = client.image_to_data(png_bytes_from_page(page, zoom=zoom), lang=lang, psm=6)
    rows = {}
    for i in range(len(data["text"])):
        txt = normalize_text(data["text"][i])
        try:
            conf = float(data["conf"][i])
        except Exception:
            conf = -1
        if not txt or conf < 0:
            continue
        x = float(data["left"][i]) / zoom
        y = float(data["top"][i]) / zoom
        w = float(data["width"][i]) / zoom
        h = float(data["height"][i]) / zoom
        key = int(round(y / 3.0) * 3)
        rows.setdefault(key, []).append((x, y, x + w, y + h, txt))
    lines = []
    for key in sorted(rows.keys()):
        row_words = sorted(rows[key], key=lambda t: t[0])
        x0 = min(w[0] for w in row_words); y0 = min(w[1] for w in row_words)
        x1 = max(w[2] for w in row_words); y1 = max(w[3] for w in row_words)
        lines.append({"type": "line", "text": normalize_text(" ".join(w[4] for w in row_words)), "bbox": [x0, y0, x1, y1], "words": row_words, "source": "ocr_service"})
    return merge_wrapped_lines(lines)

def suspicious_text_score(lines):
    text = "".join(line.get("text", "") for line in lines)
    if not text:
        return 1.0
    total = len(text)
    ascii_ratio = sum(1 for ch in text if ord(ch) < 128) / total
    cjk_ratio = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff") / total
    suspicious_chars = "哇映陛碉喔锟斤拷鈥 ■□△○◇※￡"
    suspicious_ratio = sum(1 for ch in text if ch in suspicious_chars) / total
    return min((0.45 if cjk_ratio < 0.15 else 0) + (0.45 if suspicious_ratio > 0.02 else 0) + (0.2 if ascii_ratio > 0.7 else 0), 1.0)

def extract_page_lines(page, text_source, ocr_lang, ocr_client):
    pdf_lines = extract_page_lines_pdf(page)
    if text_source == "pdf":
        return pdf_lines
    if ocr_client is None:
        return pdf_lines
    if text_source == "ocr":
        return ocr_page_lines(page, client=ocr_client, lang=ocr_lang)
    score = suspicious_text_score(pdf_lines)
    if score >= 0.35 or len(pdf_lines) <= 3:
        ocr_lines = ocr_page_lines(page, client=ocr_client, lang=ocr_lang)
        if len(ocr_lines) >= max(3, int(len(pdf_lines) * 0.5)):
            return ocr_lines
    return pdf_lines

def collect_header_footer_noise(page_lines_map, min_repeat_pages=3):
    top_counter = Counter(); bottom_counter = Counter()
    for lines in page_lines_map.values():
        if not lines:
            continue
        page_height = max(line["bbox"][3] for line in lines)
        for line in lines:
            text = normalize_text(line["text"])
            if not text:
                continue
            if len(text) <= 90 and line["bbox"][1] < page_height * 0.12:
                top_counter[text] += 1
            if len(text) <= 90 and line["bbox"][3] > page_height * 0.88:
                bottom_counter[text] += 1
    noise = set()
    for k, v in top_counter.items():
        if v >= min_repeat_pages:
            noise.add(k)
    for k, v in bottom_counter.items():
        if v >= min_repeat_pages:
            noise.add(k)
    return noise

def filter_noise_lines(lines, noise_texts):
    out = []
    for line in lines:
        text = normalize_text(line["text"])
        if not text:
            continue
        if text in noise_texts:
            continue
        if PAGE_NUM_RE.fullmatch(text):
            continue
        if re.search(r"CTD申报资料", text) and len(text) <= 120:
            continue
        out.append(line)
    return out

def classify_page(lines, allowed_ids):
    texts = [normalize_text(x["text"]) for x in lines if normalize_text(x["text"])]
    toc_hits = 0; page_num_tail_hits = 0; deep_hits = 0
    for t in texts:
        m = GENERIC_ID_RE.match(t)
        if m and m.group(1).lower() in allowed_ids:
            toc_hits += 1
            if re.search(r"\s+\d+\s*$", t):
                page_num_tail_hits += 1
        if DEEPER_ID_RE.match(t):
            deep_hits += 1
    avg_len = sum(len(t) for t in texts) / max(1, len(texts))
    is_toc = toc_hits >= 5 or (toc_hits >= 3 and page_num_tail_hits >= 2) or (toc_hits >= 4 and avg_len < 30 and deep_hits >= 1)
    return {"page_type": "toc" if is_toc else "body", "toc_hits": toc_hits, "avg_len": avg_len, "line_count": len(texts)}

def build_heading_patterns(section_id, section_name):
    escaped_id = re.escape(section_id); escaped_name = re.escape(section_name)
    return [
        re.compile(rf"^\s*{escaped_id}\s*[\.、]?\s*{escaped_name}\s*$", re.I),
        re.compile(rf"^\s*{escaped_id}\s+[^\n]*{escaped_name}\s*$", re.I),
        re.compile(rf"^\s*{escaped_id}\s*$", re.I),
    ]

def is_reference_like(text):
    return bool(REF_CONTEXT_RE.search(text[:12])) or "参见" in text or "详见" in text

def heading_score(line, page_lines, idx, section_id, section_name, page_type):
    text = normalize_text(line["text"])
    if not text or page_type == "toc" or is_reference_like(text):
        return -999.0
    score = 0.0
    ntext = normalize_heading_text(text)
    if section_id.lower() in text.lower():
        score += 0.8
    if normalize_heading_text(section_name) in ntext:
        score += 0.8
    for pat in build_heading_patterns(section_id, section_name):
        if pat.match(text):
            score += 0.8
            break
    if re.match(r"^\s*" + re.escape(section_id) + r"\s*$", text) and idx + 1 < len(page_lines):
        next_text = normalize_text(page_lines[idx + 1]["text"])
        if normalize_heading_text(section_name) in normalize_heading_text(next_text):
            score += 1.0
    page_height = max(l["bbox"][3] for l in page_lines) if page_lines else 1000
    y0 = line["bbox"][1]
    if y0 < page_height * 0.35:
        score += 0.2
    if y0 > page_height * 0.65:
        score -= 0.2
    if len(text) <= 45:
        score += 0.15
    else:
        score -= 0.15
    if re.search(r"(应|开展|研究|结果|方法|限度|样品|批次|符合|进行)", text) and section_id not in text:
        score -= 0.2
    return score

def collect_anchors(page_lines_map, page_meta, sections, allowed_ids):
    candidates = []
    for sec in sections:
        sid = sec["section_id"]; sname = sec["section_name"]
        for page_no, lines in page_lines_map.items():
            if page_meta[page_no]["page_type"] == "toc":
                continue
            for idx, line in enumerate(lines):
                text = normalize_text(line["text"])
                m = GENERIC_ID_RE.match(text)
                if m:
                    found_id = m.group(1).lower()
                    if found_id not in allowed_ids and found_id != sid:
                        continue
                score = heading_score(line, lines, idx, sid, sname, page_meta[page_no]["page_type"])
                if score >= 1.15:
                    bbox = line["bbox"]; match_text = text
                    if re.match(r"^\s*" + re.escape(sid) + r"\s*$", text) and idx + 1 < len(lines):
                        n2 = normalize_text(lines[idx + 1]["text"])
                        if normalize_heading_text(sname) in normalize_heading_text(n2):
                            bbox = [min(line["bbox"][0], lines[idx + 1]["bbox"][0]), min(line["bbox"][1], lines[idx + 1]["bbox"][1]), max(line["bbox"][2], lines[idx + 1]["bbox"][2]), max(line["bbox"][3], lines[idx + 1]["bbox"][3])]
                            match_text = text + " " + n2
                    candidates.append({"section_id": sid, "section_name": sname, "page": page_no, "y": bbox[1], "bbox": bbox, "match_text": match_text, "score": score})
    best_by_sid = {}
    for c in sorted(candidates, key=lambda x: (-x["score"], x["page"], x["y"])):
        sid = c["section_id"]
        if sid not in best_by_sid:
            best_by_sid[sid] = c
    return sorted(best_by_sid.values(), key=lambda x: (x["page"], x["y"]))

def bbox_overlap(b1, b2):
    return not (b1[2] < b2[0] or b1[0] > b2[2] or b1[3] < b2[1] or b1[1] > b2[3])

def line_in_any_group(line, groups):
    for g in groups:
        gb = [min(i["bbox"][0] for i in g), min(i["bbox"][1] for i in g), max(i["bbox"][2] for i in g), max(i["bbox"][3] for i in g)]
        if bbox_overlap(line["bbox"], gb):
            return True
    return False

def detect_table_candidates(lines):
    groups = []; current = []
    def line_col_count(line, gap_threshold=18.0):
        words = sorted(line["words"], key=lambda t: t[0])
        if not words:
            return 0
        count = 1; prev_x1 = words[0][2]
        for w in words[1:]:
            if w[0] - prev_x1 > gap_threshold:
                count += 1
            prev_x1 = w[2]
        return count
    def looks_table_like(group):
        if len(group) < 3:
            return False
        col_counts = [line_col_count(g) for g in group]
        multi_col_rows = sum(1 for c in col_counts if c >= 2)
        if multi_col_rows < 2 or max(col_counts) < 2:
            return False
        ys = [g["bbox"][1] for g in group]
        gaps = [ys[i + 1] - ys[i] for i in range(len(ys) - 1)]
        stable_gap = np.std(gaps) < 8 if len(gaps) >= 2 else True
        text = " ".join(g["text"] for g in group)
        header_hint = bool(re.search(r"(项目|结果|标准|限度|批号|时间|条件|方法|含量)", text))
        long_sentence_rows = sum(1 for g in group if len(g["text"]) > 40 and ("。" in g["text"] or "；" in g["text"]))
        if long_sentence_rows >= max(2, len(group) // 2):
            return False
        return stable_gap and (header_hint or multi_col_rows >= 3)
    for line in lines:
        words = sorted(line["words"], key=lambda t: t[0]); cols = 0
        if words:
            cols = 1; prev_x1 = words[0][2]
            for w in words[1:]:
                if w[0] - prev_x1 > 18.0:
                    cols += 1
                prev_x1 = w[2]
        if cols >= 2:
            current.append(line)
        else:
            if looks_table_like(current):
                groups.append(current)
            current = []
    if looks_table_like(current):
        groups.append(current)
    return groups

def split_columns_from_words(line, gap_threshold=18.0):
    words = sorted(line["words"], key=lambda t: t[0])
    if not words:
        return []
    cells = [[str(words[0][4])]]
    prev_x1 = words[0][2]
    for w in words[1:]:
        if w[0] - prev_x1 > gap_threshold:
            cells.append([str(w[4])])
        else:
            cells[-1].append(str(w[4]))
        prev_x1 = w[2]
    return [normalize_text(" ".join(c)) for c in cells if normalize_text(" ".join(c))]

def group_bbox(group):
    return [min(g["bbox"][0] for g in group), min(g["bbox"][1] for g in group), max(g["bbox"][2] for g in group), max(g["bbox"][3] for g in group)]

def table_group_to_frame(group, page_num, table_idx):
    rows = [split_columns_from_words(line) for line in group]
    rows = [r for r in rows if r]
    if not rows:
        return {}
    max_cols = max(len(r) for r in rows)
    norm_rows = [r + [""] * (max_cols - len(r)) for r in rows]
    columns = norm_rows[0]; data = norm_rows[1:] if len(norm_rows) > 1 else []
    return {"id": f"tbl_p{page_num}_{table_idx}", "type": "frame", "source": "text", "page": page_num, "bbox": group_bbox(group), "columns": columns, "data": data, "markdown": markdown_table(columns, data)}

def get_image_blocks(page, min_width, min_height):
    info = page.get_text("dict")
    out = []; idx = 0
    for b in info.get("blocks", []):
        if b.get("type") != 1:
            continue
        bbox = b.get("bbox")
        if not bbox:
            continue
        x0, y0, x1, y1 = bbox; w = x1 - x0; h = y1 - y0
        if w < min_width or h < min_height:
            continue
        idx += 1
        out.append({"type": "image_block", "bbox": [x0, y0, x1, y1], "width": w, "height": h, "index": idx})
    return out

def nearby_caption(lines, bbox, max_gap=28.0):
    x0, y0, x1, y1 = bbox
    caps = []
    for line in lines:
        lx0, ly0, lx1, ly1 = line["bbox"]
        vertical_near = (0 <= ly0 - y1 <= max_gap) or (0 <= y0 - ly1 <= max_gap)
        horizontal_overlap = not (lx1 < x0 or lx0 > x1)
        if vertical_near and horizontal_overlap and re.search(r"(表|图|Table|Figure|\d)", line["text"]):
            caps.append(line["text"])
    return normalize_text(" ".join(caps))

def crop_pixmap(page, bbox, zoom=2.5):
    return page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=fitz.Rect(bbox), alpha=False)

def pixmap_to_cv(pix):
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n == 4:
        return cv2.cvtColor(arr, cv2.COLOR_RGBA2BGR)
    if pix.n == 3:
        return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)
    return cv2.cvtColor(arr, cv2.COLOR_GRAY2BGR)

def preprocess_table_image(img):
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    return cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 11)

def detect_grid_cells(bin_img):
    h, w = bin_img.shape
    scale_x = max(20, w // 25); scale_y = max(20, h // 25)
    horiz = cv2.morphologyEx(bin_img, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (scale_x, 1)))
    vert = cv2.morphologyEx(bin_img, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, scale_y)))
    grid = cv2.add(horiz, vert)
    grid = cv2.dilate(grid, np.ones((3, 3), np.uint8), iterations=1)
    contours, _ = cv2.findContours(grid, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
    cells = []
    for cnt in contours:
        x, y, cw, ch = cv2.boundingRect(cnt)
        area = cw * ch
        if cw < 20 or ch < 12 or area < 500:
            continue
        if cw > 0.98 * w and ch > 0.98 * h:
            continue
        cells.append((x, y, cw, ch))
    deduped = []
    for c in sorted(cells, key=lambda t: (t[1], t[0], t[2] * t[3])):
        if any(abs(c[0] - d[0]) < 6 and abs(c[1] - d[1]) < 6 and abs(c[2] - d[2]) < 8 and abs(c[3] - d[3]) < 8 for d in deduped):
            continue
        deduped.append(c)
    return deduped

def cluster_positions(vals, tol=12.0):
    if not vals:
        return []
    vals = sorted(vals); groups = [[vals[0]]]
    for v in vals[1:]:
        if abs(v - groups[-1][-1]) <= tol:
            groups[-1].append(v)
        else:
            groups.append([v])
    return [sum(g) / len(g) for g in groups]

def assign_to_cluster(v, centers):
    return int(np.argmin([abs(v - c) for c in centers]))

def ocr_cell_via_service(img, client, lang):
    return normalize_text(re.sub(r"\s{2,}", " ", client.image_to_text(png_bytes_from_image(img), lang=lang, psm=6)))

def ocr_table_from_image_via_service(img, client, lang):
    if client is None:
        return None
    bin_img = preprocess_table_image(img)
    cells = detect_grid_cells(bin_img)
    if len(cells) < 4:
        return None
    row_centers = cluster_positions([y + h / 2 for (_, y, _, h) in cells], tol=18.0)
    col_centers = cluster_positions([x + w / 2 for (x, _, w, _) in cells], tol=24.0)
    if len(row_centers) < 2 or len(col_centers) < 2:
        return None
    grid = {}
    for x, y, w, h in cells:
        r = assign_to_cluster(y + h / 2, row_centers); c = assign_to_cluster(x + w / 2, col_centers)
        pad_x = max(2, int(w * 0.04)); pad_y = max(2, int(h * 0.08))
        x0 = max(0, x + pad_x); y0 = max(0, y + pad_y)
        x1 = min(img.shape[1], x + w - pad_x); y1 = min(img.shape[0], y + h - pad_y)
        txt = ""
        if x1 > x0 and y1 > y0:
            txt = ocr_cell_via_service(img[y0:y1, x0:x1], client=client, lang=lang)
        grid[(r, c)] = txt
    rows = []
    for r in range(len(row_centers)):
        rows.append([normalize_text(grid.get((r, c), "")) for c in range(len(col_centers))])
    rows = [r for r in rows if any(normalize_text(x) for x in r)]
    if len(rows) < 2:
        return None
    keep_cols = [c for c in range(max(len(r) for r in rows)) if any(c < len(r) and normalize_text(r[c]) for r in rows)]
    rows = [[r[c] if c < len(r) else "" for c in keep_cols] for r in rows]
    if len(rows) < 2 or len(rows[0]) < 2:
        return None
    fill_ratio = sum(1 for r in rows for x in r if normalize_text(x)) / max(1, len(rows) * len(rows[0]))
    if fill_ratio < 0.18:
        return None
    columns = rows[0]; data = rows[1:]
    if sum(1 for x in columns if normalize_text(x)) <= max(1, len(columns) // 3):
        columns = [f"col_{i+1}" for i in range(len(columns))]; data = rows
    return {"columns": columns, "data": data}

def image_to_base64(pix):
    return base64.b64encode(pix.tobytes("png")).decode("utf-8")

def image_block_to_entry(page, lines, block, page_num, image_idx, embed_images, ocr_lang, ocr_client):
    caption = nearby_caption(lines, block["bbox"])
    pix = crop_pixmap(page, block["bbox"], zoom=2.5)
    cv_img = pixmap_to_cv(pix)
    ocr_table = ocr_table_from_image_via_service(cv_img, client=ocr_client, lang=ocr_lang)
    if ocr_table is not None:
        entry = {"id": f"imgtbl_p{page_num}_{image_idx}", "type": "frame", "source": "image_ocr_service", "page": page_num, "bbox": block["bbox"], "width": block["width"], "height": block["height"], "caption": caption, "columns": ocr_table["columns"], "data": ocr_table["data"]}
        entry["markdown"] = markdown_table(entry["columns"], entry["data"])
        if embed_images:
            entry["image_base64_png"] = image_to_base64(pix)
        return entry
    entry = {"id": f"imgtbl_p{page_num}_{image_idx}", "type": "image_frame", "source": "image_fallback", "page": page_num, "bbox": block["bbox"], "width": block["width"], "height": block["height"], "caption": caption, "markdown_ref": f"table_image://p{page_num}/{image_idx}"}
    if embed_images:
        entry["image_base64_png"] = image_to_base64(pix)
    return entry

def build_page_elements(page, page_num, page_lines, min_img_w, min_img_h, embed_images, ocr_lang, ocr_client):
    table_groups = detect_table_candidates(page_lines)
    frames = []
    for idx, grp in enumerate(table_groups, start=1):
        frame = table_group_to_frame(grp, page_num, idx)
        if frame:
            frames.append(frame)
    image_entries = [image_block_to_entry(page, page_lines, img, page_num, idx, embed_images, ocr_lang, ocr_client) for idx, img in enumerate(get_image_blocks(page, min_img_w, min_img_h), start=1)]
    elements = []
    for line in page_lines:
        if line_in_any_group(line, table_groups):
            continue
        elements.append({"kind": "text", "page": page_num, "y": line["bbox"][1], "bbox": line["bbox"], "text": line["text"], "source": line.get("source", "pdf")})
    for frame in frames:
        elements.append({"kind": "frame", "page": page_num, "y": frame["bbox"][1], "bbox": frame["bbox"], "frame": frame})
    for img in image_entries:
        elements.append({"kind": "image", "page": page_num, "y": img["bbox"][1], "bbox": img["bbox"], "image": img})
    elements.sort(key=lambda e: (e["page"], e["y"], e["bbox"][0]))
    return elements

def locate_section_for_element(page_no, y, anchors):
    prev = None
    for a in anchors:
        if (a["page"], a["y"]) <= (page_no, y):
            prev = a
        else:
            break
    return prev["section_id"] if prev else None

def is_element_in_anchor_region(el, anchors):
    if el["kind"] != "text":
        return False
    return any(el["page"] == a["page"] and bbox_overlap(el["bbox"], a["bbox"]) for a in anchors)

def is_inline_reference(text, allowed_ids):
    text = normalize_text(text)
    if not text:
        return False
    if is_reference_like(text):
        return True
    m = GENERIC_ID_RE.search(text)
    if not m:
        return False
    sid = m.group(1)
    if sid in allowed_ids and len(text) > len(sid) + 8:
        return True
    return False

def convert_deeper_heading_to_md(text, allowed_ids):
    text = normalize_text(text)
    if not text or is_inline_reference(text, allowed_ids):
        return None
    m = DEEPER_ID_RE.match(text)
    if not m:
        return None
    sid = m.group(1)
    if sid in allowed_ids:
        return None
    return f"### {text}"

def compact_markdown_parts(parts):
    blocks = []; text_buffer = []
    def flush_text_buffer():
        nonlocal text_buffer, blocks
        if not text_buffer:
            return
        current_para = []
        for line in text_buffer:
            line = markdownize_text_line(line)
            if not line:
                if current_para:
                    blocks.append("\n".join(current_para)); current_para = []
                continue
            if line.startswith("- ") or re.match(r"^\d+\.\s+", line) or line.startswith("### "):
                if current_para:
                    # Preserve the original line breaks inside prose paragraphs.
                    blocks.append("\n".join(current_para)); current_para = []
                blocks.append(line)
            else:
                current_para.append(line)
        if current_para:
            # Keep the source line layout instead of flattening to one sentence.
            blocks.append("\n".join(current_para))
        text_buffer = []
    for part in parts:
        value = part["value"].strip()
        if not value:
            continue
        if part["kind"] == "text":
            text_buffer.append(value)
        else:
            flush_text_buffer(); blocks.append(value)
    flush_text_buffer()
    return re.sub(r"\n{3,}", "\n\n", "\n\n".join(blocks)).strip()

def post_fix_section_boundaries(items, allowed_ids):
    ordered = []
    def walk(nodes):
        for n in nodes:
            ordered.append(n)
            if n.get("children_sections"):
                walk(n["children_sections"])
    walk(items)
    ids = [x["section_id"] for x in ordered]
    for i, node in enumerate(ordered[:-1]):
        next_ids = ids[i + 1:i + 4]
        content = node.get("content", "")
        cut_pos = None
        for nid in next_ids:
            m = re.search(r"(^|\n)\s*" + re.escape(nid) + r"\b", content)
            if m:
                cut_pos = m.start()
                break
        if cut_pos is not None:
            node["content"] = content[:cut_pos].strip()

def parse_pdf_to_markdown_json(pdf_path, outline, text_source="auto", ocr_lang="chi_sim+eng", ocr_client=None, embed_images=True, image_min_width=120.0, image_min_height=60.0, title="CTD 解析结果"):
    doc = fitz.open(pdf_path)
    result = init_outline(outline)
    flat_sections = flatten_sections(outline)
    allowed_ids = build_allowed_ids(outline)

    from agent.agent_backend.utils.parser.pdf_page_extractor import extract_pdf_pages, inside
    source_pages = extract_pdf_pages(str(pdf_path), ocr_client=ocr_client, lang=ocr_lang, force_ocr=text_source == 'ocr')
    page_lines_map = {}
    for source_page in source_pages:
        page_lines_map[source_page['page']] = [dict(line, words=[(*w['bbox'], w['text']) for w in source_page['words'] if inside(w['bbox'], line['bbox'])]) for line in source_page['lines']]

    noise = collect_header_footer_noise(page_lines_map)
    for page_no in list(page_lines_map.keys()):
        page_lines_map[page_no] = filter_noise_lines(page_lines_map[page_no], noise)

    page_meta = {}
    for page_no, lines in page_lines_map.items():
        page_meta[page_no] = classify_page(lines, allowed_ids)

    anchors = collect_anchors(page_lines_map, page_meta, flat_sections, allowed_ids)

    all_elements = []
    for source_page in source_pages:
        page_no = source_page['page']
        for line in page_lines_map[page_no]:
            if not any(inside(line['bbox'], t['bbox_pdf']) for t in source_page['tables']):
                all_elements.append(dict(line, kind='text', page=page_no, y=line['bbox'][1]))
        for table in source_page['tables']:
            all_elements.append({'kind': 'frame', 'page': page_no, 'y': table['bbox_pdf'][1], 'bbox': table['bbox_pdf'], 'frame': table})
    all_elements.sort(key=lambda e: (e['page'], e['y'], e['bbox'][0]))
    unassigned = []

    for el in all_elements:
        if is_element_in_anchor_region(el, anchors):
            continue
        section_id = locate_section_for_element(el["page"], el["y"], anchors)
        if not section_id:
            unassigned.append(el)
            continue
        node = find_section_node(result, section_id)
        if not node:
            continue
        append_unique_page(node, el["page"])

        if el["kind"] == "text":
            if page_meta[el["page"]]["page_type"] == "toc":
                continue
            md_sub = convert_deeper_heading_to_md(el["text"], allowed_ids)
            if md_sub:
                node["_content_parts"].append({"kind": "text", "value": md_sub})
            else:
                if not is_inline_reference(el["text"], allowed_ids) or len(el["text"]) > 20:
                    node["_content_parts"].append({"kind": "text", "value": el["text"]})
        elif el["kind"] == "frame":
            node["tables"].append(el["frame"])
            node["_content_parts"].append({"kind": "table", "value": el["frame"]["markdown"]})
        elif el["kind"] == "image":
            node["tables"].append(el["image"])
            if el["image"]["type"] == "frame":
                node["_content_parts"].append({"kind": "table", "value": el["image"]["markdown"]})
            else:
                caption = el["image"].get("caption", "").strip() or f"图片表格 page {el['image']['page']}"
                node["_content_parts"].append({"kind": "image", "value": f"![{caption}]({el['image']['markdown_ref']})"})

    def finalize(nodes):
        for item in nodes:
            item["content"] = compact_markdown_parts(item["_content_parts"])
            if item.get("children_sections"):
                finalize(item["children_sections"])
    finalize(result)
    clean_internal_fields(result)
    post_fix_section_boundaries(result, allowed_ids)

    doc.close()
    return {
        "title": title,
        "source_pdf": str(pdf_path),
        "root_section_id": "3.2.s" if any(str(x["section_id"]).lower().startswith("3.2.s") for x in flat_sections) else "3.2.p",
        "structure_type": "ctd_fixed_outline_parser_with_page_classification_and_main_anchor_segmentation",
        "anchors": anchors,
        "pages": page_meta,
        "sections": result,
        "source_pages": source_pages,
        "unassigned_elements": unassigned,
        "parse_diagnostics": source_pages[0]['parse_diagnostics'] if source_pages else {'status': 'failed'},
    }

def derive_default_output(pdf_path, part):
    return pdf_path.parent / f"{pdf_path.stem}_parsed_{part.lower()}_outline.json"

def get_outline(part):
    normalized_part = str(part or "").strip().lower()
    if normalized_part == "s":
        return OUTLINE_S
    if normalized_part == "p":
        return OUTLINE_P
    raise ValueError("part must be s or p")


def _flatten_leaf_sections(nodes, parent_section_id="", title_path=None):
    title_path = list(title_path or [])
    out = []
    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        section_id = str(node.get("section_id", "") or "").strip()
        if not section_id:
            continue
        section_name = str(
            node.get("section_name", "")
            or node.get("title", "")
            or section_id
        ).strip() or section_id
        node_title_path = list(node.get("title_path") or (title_path + [section_name]))
        children = [child for child in (node.get("children_sections") or []) if isinstance(child, dict)]
        if children:
            out.extend(
                _flatten_leaf_sections(
                    children,
                    parent_section_id=section_id,
                    title_path=node_title_path,
                )
            )
            continue
        content = normalize_text(node.get("content", "") or "")
        out.append(
            {
                "section_id": section_id,
                "section_code": str(node.get("section_code", "") or section_id).strip() or section_id,
                "section_name": section_name,
                "title_path": node_title_path,
                "parent_section_id": str(node.get("parent_section_id", "") or parent_section_id).strip(),
                "content": content,
                "content_preview": content[:320],
                "char_count": len(content),
                "raw_pages": list(node.get("raw_pages") or []),
                "page_start": min(node.get("raw_pages") or []) if (node.get("raw_pages") or []) else None,
                "page_end": max(node.get("raw_pages") or []) if (node.get("raw_pages") or []) else None,
                "tables": list(node.get("tables") or []),
            }
        )
    return out


def _build_review_units_from_leaf_sections(leaf_sections):
    review_units = []
    for idx, section in enumerate(leaf_sections or [], start=1):
        text = normalize_text(section.get("content", "") or "")
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
                "tables": list(section.get('tables') or []),
                "raw_pages": list(section.get('raw_pages') or []),
            }
        )
    return review_units


def _resolve_root_outline(catalog, root_section_id):
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
            return children, str(node.get("section_name", "") or root_key).strip() or root_key
    raise ValueError(f"root section not found: {root_key}")


def parse_ctd_submission_to_payload(
    file_path,
    *,
    catalog,
    root_section_id,
    text_source="auto",
    ocr_lang="chi_sim+eng",
    ocr_client=None,
    embed_images=False,
):
    pdf_path = Path(file_path).expanduser().resolve()
    outline, root_name = _resolve_root_outline(catalog, root_section_id)
    parsed = parse_pdf_to_markdown_json(
        pdf_path=pdf_path,
        outline=outline,
        text_source=text_source,
        ocr_lang=ocr_lang,
        ocr_client=ocr_client,
        embed_images=embed_images,
        title=f"{root_name}解析结果",
    )
    leaf_sections = _flatten_leaf_sections(parsed.get("sections") or [])
    review_units = _build_review_units_from_leaf_sections(leaf_sections)
    return {
        "title": parsed.get("title") or f"{root_name}解析结果",
        "source_pdf": str(pdf_path),
        "root_section_id": str(root_section_id or "").strip(),
        "root_section_id": parsed.get("root_section_id") or str(root_section_id or "").strip().lower(),
        "structure_type": "ctd_leaf_section_payload_v1",
        "anchors": parsed.get("anchors") or [],
        "pages": parsed.get("pages") or {},
        "source_pages": parsed.get("source_pages") or [],
        "unassigned_elements": parsed.get("unassigned_elements") or [],
        "parse_diagnostics": parsed.get("parse_diagnostics") or {},
        "sections": leaf_sections,
        "review_units": review_units,
        "statistics": {
            "section_total": len(leaf_sections),
            "review_unit_total": len(review_units),
        },
    }


def _extract_pdf_full_text(file_path):
    pdf_path = Path(file_path).expanduser().resolve()
    blocks = []
    with fitz.open(pdf_path) as doc:
        for page_idx, page in enumerate(doc, start=1):
            text = normalize_text(page.get_text("text") or "")
            if not text:
                continue
            blocks.append((page_idx, text))
    full_text = "\n\n".join(text for _, text in blocks).strip()
    return full_text, blocks


def parse_submission_pdf_to_payload(file_path, title=None, embed_images=False):
    from agent.agent_backend.utils.parser.submission_pdf_markdown_parser import parse_submission_pdf_to_payload as parse_shared
    return parse_shared(file_path, title=title, embed_images=embed_images)


def parse_bound_section_pdf_to_payload(file_path, *, section_id, section_name="", parent_section_id=""):
    base_payload = parse_submission_pdf_to_payload(file_path=file_path, title=section_name or section_id)
    full_text = ""
    page_start = None
    page_end = None
    if isinstance(base_payload, dict) and isinstance(base_payload.get("review_units"), list) and base_payload["review_units"]:
        units = base_payload["review_units"]
        full_text = '\n\n'.join(u.get('text', '') for u in units)
        page_start = units[0].get("page_start")
        page_end = units[-1].get("page_end")
    normalized_section_id = str(section_id or "").strip()
    normalized_section_name = str(section_name or normalized_section_id).strip() or normalized_section_id
    return {
        "title": normalized_section_name,
        "source_pdf": base_payload.get("source_pdf"),
        "structure_type": "bound_section_pdf_payload_v1",
        "source_pages": base_payload.get('pages', []),
        "parse_diagnostics": base_payload.get('parse_diagnostics', {}),
        "sections": [
            {
                "section_id": normalized_section_id,
                "section_code": normalized_section_id,
                "section_name": normalized_section_name,
                "title_path": [normalized_section_name],
                "parent_section_id": str(parent_section_id or "").strip(),
                "content": full_text,
                "content_preview": full_text[:320],
                "char_count": len(full_text),
                "page_start": page_start,
                "page_end": page_end,
                "raw_pages": [p['page'] for p in base_payload.get('pages', [])],
                "tables": [t for p in base_payload.get('pages', []) for t in p['tables']],
                "source_pages": base_payload.get('pages', []),
            }
        ],
        "review_units": [
            {
                "chunk_id": normalized_section_id,
                "section_id": normalized_section_id,
                "section_code": normalized_section_id,
                "section_name": normalized_section_name,
                "parent_section_id": str(parent_section_id or "").strip(),
                "page": page_start,
                "page_start": page_start,
                "page_end": page_end,
                "text": full_text,
                "title_path": [normalized_section_name],
                "unit_order": 1,
                "unit_type": "bound_section_content",
                "tables": [t for p in base_payload.get('pages', []) for t in p['tables']],
                "source_pages": base_payload.get('pages', []),
                "parse_diagnostics": base_payload.get('parse_diagnostics', {}),
                "char_count": len(full_text),
            }
        ],
        "statistics": {
            "section_total": 1 if full_text else 0,
            "review_unit_total": 1 if full_text else 0,
        },
    }

def main():
    parser = argparse.ArgumentParser(description="CTD 固定目录结构解析器")
    parser.add_argument("pdf")
    parser.add_argument("--part", default="s", choices=["s", "p"])
    parser.add_argument("--output-json")
    parser.add_argument("--title", default="CTD 解析结果")
    parser.add_argument("--text-source", default="auto", choices=["auto", "ocr", "pdf"])
    parser.add_argument("--ocr-base-url", default="http://localhost:8000")
    parser.add_argument("--ocr-timeout", type=int, default=120)
    parser.add_argument("--ocr-lang", default="chi_sim+eng")
    parser.add_argument("--embed-images", default="true")
    parser.add_argument("--image-min-width", type=float, default=120.0)
    parser.add_argument("--image-min-height", type=float, default=60.0)
    args = parser.parse_args()

    pdf_path = Path(args.pdf).expanduser().resolve()
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF 文件不存在: {pdf_path}")

    output_json = Path(args.output_json).expanduser().resolve() if args.output_json else derive_default_output(pdf_path, args.part)
    outline = get_outline(args.part)

    ocr_client = None
    if args.text_source in {"auto", "ocr"}:
        client = OCRServiceClient(args.ocr_base_url, timeout=args.ocr_timeout)
        if client.healthcheck():
            ocr_client = client
        elif args.text_source == "ocr":
            raise RuntimeError(f"OCR 服务不可用: {args.ocr_base_url}")
        else:
            print("[WARN] OCR 服务不可用，auto 模式自动退回 pdf 文本层")

    result = parse_pdf_to_markdown_json(
        pdf_path=pdf_path,
        outline=outline,
        text_source=args.text_source if ocr_client is not None or args.text_source == "pdf" else "pdf",
        ocr_lang=args.ocr_lang,
        ocr_client=ocr_client,
        embed_images=str2bool(args.embed_images),
        image_min_width=args.image_min_width,
        image_min_height=args.image_min_height,
        title=args.title,
    )

    output_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] JSON: {output_json}")

if __name__ == "__main__":
    main()

