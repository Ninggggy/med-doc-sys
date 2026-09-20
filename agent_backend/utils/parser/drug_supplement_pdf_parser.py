from __future__ import annotations

import json
import re
import shutil
import statistics
import uuid
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from contextvars import ContextVar
from typing import Any, Dict, List, Optional, Sequence, Tuple

import fitz
import pdfplumber
import requests
from agent.agent_backend.utils.parser.ocr_request_budget import request_budgets

from agent.agent_backend.config.settings import settings


ITEM_TITLES: Dict[int, str] = {
    1: "本申请属于",
    2: "药品注册分类",
    3: "是否为OTC",
    4: "原申请品种状态",
    5: "申请事项分类",
    6: "药品通用名称",
    7: "英文名称/拉丁名称",
    8: "汉语拼音",
    9: "化学名称",
    10: "商品名称",
    11: "剂型",
    12: "规格",
    13: "同品种已被受理或同期申报的其他制剂规格",
    14: "包装",
    15: "药品有效期",
    16: "处方",
    17: "原/辅料/包材来源",
    18: "中药材标准",
    19: "受理前药品注册检验",
    20: "主要适应症或者功能主治",
    21: "补充申请的内容",
    22: "提出补充申请的理由",
    23: "原批准注册内容及相关信息",
    24: "专利情况",
    25: "数据保护相关内容",
    26: "中药品种保护",
    27: "同品种新药监测期",
    28: "本次申请为",
    29: "历次申请情况",
    30: "药品注册申请人",
    31: "生产企业",
    32: "委托研究机构",
    33: "其他特别申明事项",
}

SAFE_REPLACEMENTS = {
    "：": ":",
    "；": ";",
    "（": "(",
    "）": ")",
    "％": "%",
    "／": "/",
    "．": ".",
    "，": ",",
    "。": ".",
    "\xa0": " ",
    "\u3000": " ",
}

ITEM_START_RE = re.compile(r"^\s*(\d{1,2})\s*[\.、,，]\s*(.*)$", re.S)

SUBFIELD_ALIASES: Dict[str, Sequence[str]] = {
    "直接接触药品的包装材料和容器": ["直接接触药品的包装材料和容器"],
    "包装规格": ["包装规格"],
    "活性成分/中药成分/...": ["活性成份/中药成份/...", "活性成分/中药成分/...", "活性成分", "中药成分"],
    "研究负责人": ["研究负责人"],
    "研究机构名称": ["研究机构名称"],
    "地址": ["地址"],
    "联系电话": ["联系电话"],
    "商品名": ["商品名"],
    "分类号": ["分类号"],
    "检品编号": ["检品编号"],
    "辅料": ["辅料"],
    "是否有变更": ["是否有变更"],
    "适应症分类": ["适应症分类"],
    "补充申请涉及内容": ["补充申请涉及内容"],
    "原申请受理号": ["原申请受理号"],
    "临床批件编号/临床通知书编号": ["临床批件编号/临床通知书编号"],
    "原药品批准文号/登记号": ["原药品批准文号/登记号"],
    "药品标准编号": ["药品标准编号"],
    "其他": ["其他"],
    "是否涉及数据保护": ["是否涉及数据保护"],
    "所在省份": ["所在省份"],
    "申请人类型": ["申请人类型"],
    "中文名称": ["中文名称"],
    "英文名称": ["英文名称"],
    "统一社会信用代码/组织机构代码": ["统一社会信用代码/组织机构代码", "统一社会信用代码及组织机构代码"],
    "统一社会信用代码": ["统一社会信用代码"],
    "组织机构代码": ["组织机构代码"],
    "法定代表人": ["法定代表人"],
    "职位": ["职位"],
    "注册地址": ["注册地址(住所)", "注册地址"],
    "生产地址": ["生产地址"],
    "通讯地址": ["通讯地址"],
    "邮编": ["邮编"],
    "注册申请负责人": ["注册申请负责人"],
    "联系人": ["联系人"],
    "电话": ["电话"],
    "传真": ["传真"],
    "电子信箱": ["电子信箱"],
    "手机": ["手机"],
    "《药品生产许可证》编号": ["《药品生产许可证》编号", "药品生产许可证编号", "药品生产许可证号"],
    "GMP证书编号(如有)": ["GMP证书编号(如有)"],
}

SECTION_LINES = {
    "声明",
    "申请事项",
    "药品情况",
    "补充内容",
    "相关情况",
    "申请人及委托研究机构",
}

STOP_AFTER_ITEM_31_PREFIXES = ["经审查", "审查机关", "申请表已适配浏览器"]


@dataclass
class OCRToken:
    page: int
    text: str
    x: int
    y: int
    w: int
    h: int
    confidence: float


@dataclass
class OCRLine:
    page: int
    text: str
    x: int
    y: int
    w: int
    h: int
    confidence: float
    region_id: int = 0
    unassigned: bool = False
    manual_target_item_no: int = 0


@dataclass
class ParsedItem:
    item_no: int
    title: str
    raw_text: str
    normalized_text: str
    subfields: Dict[str, Any]
    source_pages: List[int]
    source_regions: List[Dict[str, Any]]
    tables: List[Dict[str, Any]]
    option_fragments: List[str] = field(default_factory=list)


ocr_region_errors = ContextVar('ocr_region_errors', default=None)


class OCRServiceClient:
    supports_execution_budget = True
    supports_preprocessing = True
    def __init__(self, base_url: str, timeout_seconds: int = 120) -> None:
        self.base_url = str(base_url or "").rstrip("/")
        self.timeout_seconds = int(timeout_seconds or 120)

    def image_to_data(self, image_bytes: bytes, lang: str, psm: int = 6, execution_budget_seconds=None, preprocessing='standard') -> Dict[str, Any]:
        fields = {"lang": lang, "psm": str(psm)}
        if preprocessing not in ('standard', 'original_gray'):
            raise ValueError('invalid_preprocessing')
        if preprocessing != 'standard':
            fields['preprocessing'] = preprocessing
        request_timeout, execution_budget = request_budgets(self.timeout_seconds, execution_budget_seconds)
        fields['execution_budget_seconds'] = str(execution_budget)
        response = requests.post(
            f"{self.base_url}/ocr/data",
            files={"file": ("page.png", image_bytes, "image/png")},
            data=fields,
            timeout=request_timeout,
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get('data') if isinstance(payload, dict) else None
        if not isinstance(data, dict) or not isinstance(data.get('text'), list):
            from agent.agent_backend.services.filing_parse_outcome import OCRResponseError
            raise OCRResponseError('OCR 响应缺少有效识别数据')
        size = len(data['text'])
        if size and any(not isinstance(data.get(key), list) or len(data[key]) != size for key in ('left', 'top', 'width', 'height', 'conf')):
            from agent.agent_backend.services.filing_parse_outcome import OCRResponseError
            raise OCRResponseError('OCR 响应坐标或置信度数据不完整')
        return data

    def image_to_text(self, image_bytes: bytes, lang: str, psm: int = 6) -> str:
        response = requests.post(
            f"{self.base_url}/ocr/text",
            files={"file": ("crop.png", image_bytes, "image/png")},
            data={"lang": lang, "psm": str(psm)},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get('text'), str):
            from agent.agent_backend.services.filing_parse_outcome import OCRResponseError
            raise OCRResponseError('OCR 响应缺少有效识别文字')
        return payload['text']


def normalize_text(text: str) -> str:
    for old, new in SAFE_REPLACEMENTS.items():
        text = str(text or "").replace(old, new)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def compact_for_matching(text: str) -> str:
    text = normalize_text(text)
    previous = None
    while previous != text:
        previous = text
        text = re.sub(r"([\u4e00-\u9fff])\s+([\u4e00-\u9fff])", r"\1\2", text)
    text = re.sub(r"\s*([:/;,])\s*", r"\1", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def smart_join(tokens: Sequence[str]) -> str:
    if not tokens:
        return ""
    return compact_for_matching(" ".join(tokens))


def is_text_layer_usable(text: str) -> bool:
    from agent.agent_backend.utils.parser.pdf_page_extractor import text_quality
    return text_quality(str(text or ""))["usable"]


def render_pdf(pdf_path: Path, dpi: int, out_dir: Path) -> List[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    matrix = fitz.Matrix(dpi / 72.0, dpi / 72.0)
    output: List[Path] = []
    with fitz.open(pdf_path) as doc:
        for page_no, page in enumerate(doc, start=1):
            image_path = out_dir / f"page-{page_no}.png"
            page.get_pixmap(matrix=matrix, alpha=False).save(str(image_path))
            output.append(image_path)
    return output


def ocr_page(image_path: Path, page_no: int, client: OCRServiceClient, lang: str, psm: int) -> Tuple[List[OCRToken], List[OCRLine], str]:
    data = client.image_to_data(image_path.read_bytes(), lang=lang, psm=psm)
    tokens: List[OCRToken] = []
    grouped: Dict[Tuple[int, int, int], List[OCRToken]] = {}
    text_list = data.get("text", []) or []
    for index, raw in enumerate(text_list):
        text = normalize_text(str(raw or ""))
        if not text:
            continue
        try:
            confidence = float((data.get("conf") or [])[index])
        except Exception:
            confidence = 0.0
        token = OCRToken(
            page=page_no,
            text=text,
            x=int(float((data.get("left") or [0])[index] or 0)),
            y=int(float((data.get("top") or [0])[index] or 0)),
            w=int(float((data.get("width") or [0])[index] or 0)),
            h=int(float((data.get("height") or [0])[index] or 0)),
            confidence=confidence,
        )
        tokens.append(token)
        key = (
            int(float((data.get("block_num") or [0])[index] or 0)),
            int(float((data.get("par_num") or [0])[index] or 0)),
            int(float((data.get("line_num") or [0])[index] or 0)),
        )
        grouped.setdefault(key, []).append(token)

    lines: List[OCRLine] = []
    for words in grouped.values():
        words.sort(key=lambda item: item.x)
        xs = [item.x for item in words]
        ys = [item.y for item in words]
        x2 = [item.x + item.w for item in words]
        y2 = [item.y + item.h for item in words]
        conf = [item.confidence for item in words if item.confidence >= 0]
        lines.append(
            OCRLine(
                page=page_no,
                text=normalize_text(" ".join(item.text for item in words)),
                x=min(xs),
                y=min(ys),
                w=max(x2) - min(xs),
                h=max(y2) - min(ys),
                confidence=round(statistics.mean(conf), 2) if conf else 0.0,
            )
        )
    lines.sort(key=lambda item: (item.y, item.x))
    return tokens, lines, "\n".join(item.text for item in lines)


def text_layer_lines(pdf_path: Path, dpi: int = 72) -> Tuple[List[OCRLine], Dict[int, List[OCRLine]], str]:
    from agent.agent_backend.utils.parser.pdf_page_extractor import native_lines
    all_lines: List[OCRLine] = []
    by_page: Dict[int, List[OCRLine]] = {}
    page_texts: List[str] = []
    with fitz.open(pdf_path) as doc:
        for page_no, page in enumerate(doc, start=1):
            lines: List[OCRLine] = []
            for entry in native_lines(page):
                text = normalize_text(entry['text'])
                if text:
                    x0, y0, x1, y1 = [v * dpi / 72 for v in entry['bbox']]
                    lines.append(OCRLine(page_no, text, x0, y0, x1-x0, y1-y0, 100.0))
            by_page[page_no] = lines
            all_lines.extend(lines)
            page_texts.append("\n".join(item.text for item in lines))
    return all_lines, by_page, "\n\n".join(page_texts)


def item_start(text: str) -> Optional[Tuple[int, str]]:
    from .form_field_semantics import heading
    found = heading(text)
    if found:
        # 非编号标题要求明确冒号或独立标题，防止“申请人类型”等子标题重开区域。
        normalized = normalize_text(text)
        if not ITEM_START_RE.match(normalized) and found[2]:
            left = normalized.split(':', 1)[0]
            if compact_for_matching(left) != compact_for_matching(found[1]):
                return None
        return found[0], found[1] + ': ' + found[2]
    return None


def strip_label(item_no: int, text: str) -> str:
    text = normalize_text(text)
    if ":" in text:
        left, right = text.split(":", 1)
        if len(compact_for_matching(left)) <= len(ITEM_TITLES[item_no]) + 10:
            return normalize_text(right)
    canonical = ITEM_TITLES[item_no]
    if compact_for_matching(text).startswith(canonical):
        return normalize_text(text.split(":", 1)[1] if ":" in text else "")
    return text


def parse_subfields(text: str) -> Dict[str, Any]:
    normalized = normalize_text(text).replace('：', ':').replace('；', ';')
    # 标签本身允许 OCR 字距；保留无冒号标签和值之间的空格。
    normalized = re.sub(r'活\s*性\s*成\s*[份分]\s*/\s*中\s*药\s*成\s*[份分]\s*/[.．,，… \t\n]*', '活性成分/中药成分/...', normalized)
    alias_to_name: Dict[str, str] = {}
    for name, aliases in SUBFIELD_ALIASES.items():
        for alias in aliases:
            alias_to_name[re.sub(r'\s+', '', compact_for_matching(alias))] = name
    if not alias_to_name:
        return {}
    alias_pattern = "|".join(r'\s*'.join(re.escape(c) for c in alias) for alias in sorted(alias_to_name, key=len, reverse=True))
    # 显式冒号可分隔紧邻子字段；无冒号标签仍须有片段边界，避免拆开值内词语。
    pattern = re.compile(rf"(?P<label>{alias_pattern})\s*[:,;|，][ \t]*|(?<![^\n])(?P<bare_label>{alias_pattern})(?:[ \t]*\n[ \t]*|[ \t]+)")
    matches = list(pattern.finditer(normalized))
    result: Dict[str, Any] = {}
    for index, match in enumerate(matches):
        label = alias_to_name[re.sub(r'\s+', '', match.group("label") or match.group("bare_label"))]
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(normalized)
        value = compact_for_matching(normalized[start:end]).strip(" ,;|")
        if label in result:
            if not isinstance(result[label], list):
                result[label] = [result[label]]
            result[label].append(value)
        else:
            result[label] = value
    return result


def _subfield_start(text: str) -> Optional[str]:
    normalized = compact_for_matching(text)
    for name, aliases in SUBFIELD_ALIASES.items():
        for alias in aliases:
            label = compact_for_matching(alias)
            # 与parse_subfields已有显式分隔符保持一致；否则值提取能识别
            # “标签,”，区域划分却继承上一列，造成并列字段串值。
            if normalized == label or normalized.startswith(tuple(label + mark for mark in ':,;|，')):
                return name
    return None


def _belongs_to_parent(text: str, number: Optional[int]) -> bool:
    # “英文名称”等同时是一级字段和主体子字段，须在所在主体区域内解释。
    return number in (30, 31, 32) and _subfield_start(text) is not None and not ITEM_START_RE.match(normalize_text(text))


def _field_region_groups(lines: Sequence[OCRLine], cells, *, subfields=False, option_markers=False, issues=None):
    """先按真实行带和标题划分区域，再收集续行；空标题也建立区域。

    有框表以单元格边界为准。无框并列区结合标题与后续行的位置
    确定分界，后续标题只替换所在列，页/节边界终止并列状态。
    """
    rows = []
    for line in sorted(lines, key=lambda l: (l.page, l.y, l.x)):
        if (not rows or rows[-1][0].page != line.page or
                min(l.y + l.h for l in rows[-1]) <= line.y + line.h / 2):
            rows.append([])
        rows[-1].append(line)

    groups, active = [], []
    last_page = None
    seen_heading = False
    previous_candidates = []
    for row_index, row in enumerate(rows):
        row.sort(key=lambda l: l.x)
        page = row[0].page
        if page != last_page:
            # 单列正文允许跨页续接；多列必须在新页重新取得位置依据。
            if len(active) > 1:
                previous_candidates = [a['number'] for a in active if a['number'] is not None]
            active = active if len(active) == 1 else []
            last_page = page
        separators = [l for l in row if compact_for_matching(l.text).strip(' |:') in SECTION_LINES]
        if separators and not subfields:
            groups.extend([[l] for l in row])
            active = []
            seen_heading = False
            previous_candidates = []
            continue

        def owner(line, *, anchor=False):
            candidates = [a for a in active if a['left'] <= line.x < a['right'] and
                          (anchor or line.x + line.w <= a['right']) and
                          (a['bottom'] is None or (line.y if anchor else line.y + line.h) <= a['bottom'])]
            return candidates[0] if len(candidates) == 1 else None

        def heading_label(line, parent=None):
            if option_markers:
                return re.search(r'[☑√✓✔■●☐□○]', line.text)
            if subfields:
                return _subfield_start(line.text)
            return None if _belongs_to_parent(line.text, parent) else item_start(line.text)

        def staggered_cut(line, old):
            # 标题只提供相邻列的分界锚点，已出现文字的包围盒不是填写
            # 区域。延后首值和缩进续文在分界内即可形成独立列证据。
            anchor = old['lines'][0]
            if anchor.page != page:
                return None
            # 只核对两个标题能否并列；较早正文的宽度不能成为后续列宽。
            x0, x1 = anchor.x, anchor.x + anchor.w
            if x1 <= line.x:
                cut, old_on_left = line.x, True
            elif line.x + line.w <= x0:
                cut, old_on_left = x0, False
            else:
                return None
            crossing = False
            for later_row in rows[row_index:]:
                if later_row[0].page != page:
                    break
                for later in later_row:
                    if later is line or later.y <= line.y:
                        continue
                    if compact_for_matching(later.text).strip(' |:') in SECTION_LINES:
                        return (cut, old_on_left) if crossing else None
                    if old['bottom'] is not None and later.y + later.h > old['bottom']:
                        continue
                    in_old_column = (old['left'] <= later.x and later.x + later.w <= cut
                                     if old_on_left else cut <= later.x and later.x + later.w <= old['right'])
                    if not in_old_column:
                        # 通栏标题终止旧区域，不能从它之后借续文作依据。
                        if later.x < cut < later.x + later.w and heading_label(later, old['number']):
                            return (cut, old_on_left) if crossing else None
                        if later.x < cut < later.x + later.w:
                            crossing = True
                        continue
                    if heading_label(later, old['number']):
                        return (cut, old_on_left) if crossing else None
                    if compact_for_matching(later.text) not in (':', ';', '|'):
                        return cut, old_on_left
            # 只有跨界正文时也不能交给新标题；保留两个候选边界，让
            # owner 产生现有的坐标诊断，正文不填入任一字段。
            return (cut, old_on_left) if crossing else None

        headings = []
        for line in row:
            old = owner(line, anchor=True)
            label = heading_label(line, old['number'] if old else None)
            if label:
                headings.append((line, label))

        if headings:
            seen_heading = True
            boundaries = []
            for line, _ in headings:
                containing = [c for c in cells.get(page, []) if
                              c[0] <= line.x + line.w / 2 <= c[2] and
                              c[1] <= line.y + line.h / 2 <= c[3]]
                cell = min(containing, key=lambda c: (c[2]-c[0])*(c[3]-c[1]), default=None)
                boundaries.append(cell)
            if len(headings) > 1:
                # 不使用页面中点、固定列宽或字段编号推断左右位置。
                # 多个字段共用一个合并格时，格边不能把内部字段压成一列。
                cuts = [cell[0] if cell and boundaries.count(cell) == 1 else line.x
                        for (line, _), cell in zip(headings, boundaries)]
                active = []
            for index, ((line, label), cell) in enumerate(zip(headings, boundaries)):
                old = owner(line, anchor=True)
                left = old['left'] if old else float('-inf')
                right = old['right'] if old else float('inf')
                if len(headings) > 1:
                    left = cuts[index] if index else float('-inf')
                    right = cuts[index+1] if index+1 < len(cuts) else float('inf')
                elif old and line.x + line.w > right:
                    # 横跨旧列的标题开启新的通栏区域。
                    active = []
                    left, right = float('-inf'), float('inf')
                elif old:
                    split = staggered_cut(line, old)
                    if split:
                        cut, old_on_left = split
                        if old_on_left:
                            old['right'], left = cut, cut
                        else:
                            old['left'], right = cut, cut
                        # 保留旧列活动区域，新标题只占空出的相邻列。
                        old = None
                if old is None and len(headings) == 1:
                    # 空标签格可能允许向右找填写格，但不能越过已确认的
                    # 邻字段。尤其在右标题先出现时，左空格不能覆盖右列。
                    right = min([right] + [a['left'] for a in active if a['left'] > line.x])
                if cell:
                    left = max(left, cell[0])
                    # 标签单元格右侧可另有填写格；只有同格内已有值/续行时
                    # 才把其右边作为字段边界，其余由下一个字段标题截断。
                    next_cell = any(c[0] == cell[2] and c[1] == cell[1] for c in cells.get(page, []))
                    if not next_cell or any(l is not line and cell[0] <= l.x < cell[2] and
                                           cell[1] <= l.y < cell[3] for l in lines):
                        right = min(right, cell[2])
                bucket = [line]
                groups.append(bucket)
                active = [a for a in active if a is not old]
                # 独立父标题可能是表头合并格，正文在后续行；只在并列
                # 单元格/子字段区域中用格底终止续行，不能截掉父字段子表。
                bottom = cell[3] if cell and (subfields or len(headings) > 1) else None
                active.append({'left': left, 'right': right, 'bottom': bottom,
                               'number': None if subfields or option_markers else label[0], 'lines': bucket})
        heading_ids = {id(l) for l, _ in headings}
        for line in row:
            if id(line) in heading_ids:
                continue
            region = owner(line)
            if region:
                region['lines'].append(line)
            else:
                if seen_heading and compact_for_matching(line.text) not in (':', ';', '|'):
                    crossing = owner(line, anchor=True) is not None
                    if issues is not None:
                        issues.append({'code': 'field_region_crossing' if crossing else 'field_region_unassigned',
                                       'stage': 'field_region', 'page': line.page, 'text': line.text,
                                       'bbox_px': [line.x, line.y, line.x+line.w, line.y+line.h],
                                       'candidate_items': sorted(set(previous_candidates + [a['number'] for a in active if a['number'] is not None])),
                                       'reason': '文字跨越并列字段区域，归属不确定；已保留原文位置，请人工核对。' if crossing else
                                                 '文字缺少可确认的字段归属，已保留原文位置，请人工核对。',
                                       'needs_review': True})
                    line = replace(line, unassigned=True)
                groups.append([line])
    return groups


def _region_reading_order(lines: Sequence[OCRLine], cells, issues=None) -> List[OCRLine]:
    ordered = []
    for region_id, group in enumerate(_field_region_groups(lines, cells, issues=issues), start=1):
        first = group[0]
        start = item_start(first.text)
        # 同一父字段中的并列子字段也分别收集续行，避免仅一级字段正确。
        body = group[1:] if start else group
        nested = _field_region_groups(body, cells, subfields=True, issues=issues)
        content = ([first] if start else []) + [l for part in nested for l in part]
        ordered.extend(replace(l, region_id=region_id) for l in content)
    return ordered


def _option_fragments(lines: Sequence[OCRLine], cells, issues=None) -> List[str]:
    """按单元格或无框选项列收集正文，续行不能成为相邻选项的片段。"""
    fragments = {}
    for line in lines:
        containing = [tuple(c) for c in cells.get(line.page, []) if
                      c[0] <= line.x + line.w / 2 <= c[2] and
                      c[1] <= line.y + line.h / 2 <= c[3]]
        cell = min(containing, key=lambda c: (c[2]-c[0])*(c[3]-c[1]), default=None)
        key = (line.page, line.region_id, cell)
        fragments.setdefault(key, []).append(line)
    output = []
    for (_, _, cell), part in fragments.items():
        # 无框选项用标记所在的真实行带建立列，沿用字段的区域收集，
        # 不依据事项编号、药名或“贮藏条件”等业务词猜测如何拼接。
        groups = [part] if cell else _field_region_groups(part, {}, subfields=True, option_markers=True, issues=issues)
        output.extend('\n'.join(line.text for line in group if not line.unassigned).strip() for group in groups
                      if any(not line.unassigned for line in group))
    return output


def _page_form_lines(page: Dict[str, Any], dpi: int) -> List[OCRLine]:
    """拆开提取器合并的跨单元格/跨列行，保留原有字词的真实坐标。"""
    cells = [c['bbox_pdf'] for t in page['tables'] for c in t.get('cells', []) if c.get('bbox_pdf')]
    output = []
    for line in page['lines']:
        bbox = line['bbox']
        if (line.get('source') == 'manual_revision'
                and line.get('position_kind') == 'review_region_not_glyph'):
            # 表外完整补录只有核对区域，没有逐行字形坐标。明确标签
            # 按人工保留的换行划分，不把整块内容当成首字段的值。
            parts = [part.strip() for part in line['text'].splitlines() if part.strip()]
            starts = [item_start(part) for part in parts]
            continuation = line.get('manual_continuation_item_no')
            if sum(start is not None for start in starts) > 1 or continuation is not None:
                if continuation is not None and (type(continuation) is not int or continuation not in ITEM_TITLES):
                    raise ValueError('人工续文缺少有效的明确字段归属')
                segments = [[continuation, []]] if continuation is not None else []
                seen = {continuation} if continuation is not None else set()
                outside_fields = False
                for part, start in zip(parts, starts):
                    if compact_for_matching(part).strip(' |:') in SECTION_LINES:
                        continue
                    # 完整人工区域同样遵守主体结束边界。审查机关及打印说明
                    # 仍保留在有效页正文/原始修订中，但不得成为研究机构字段。
                    if (segments and segments[-1][0] in (31, 32)
                            and any(compact_for_matching(part).startswith(prefix)
                                    for prefix in STOP_AFTER_ITEM_31_PREFIXES)):
                        outside_fields = True
                        continue
                    # 主体内的英文名称等是子字段；复用正常解析的父子规则，
                    # 不能把申请人后续信用代码、联系人串入药品英文名称。
                    if segments and _belongs_to_parent(part, segments[-1][0]):
                        start = None
                    if start:
                        if start[0] in seen:
                            raise ValueError('人工补录区域存在重复字段标签，请拆分并明确字段归属后保存')
                        seen.add(start[0])
                        outside_fields = False
                        from .form_field_semantics import heading
                        segments.append([start[0], [heading(part)[2]]])
                    elif outside_fields:
                        continue
                    elif segments:
                        segments[-1][1].append(part)
                    else:
                        raise ValueError('人工补录区域有无法归属的前置文字，请核对其所属字段；跨页续文须明确选择续接字段，不能自动猜测')
                x0, y0, x1, y1 = [v * dpi / 72 for v in bbox]
                for number, texts in segments:
                    output.append(OCRLine(page['page'], '\n'.join(texts).strip(), x0, y0, x1-x0, y1-y0,
                                          0, manual_target_item_no=number))
                continue
        # 修订视图的行由多个原词重新组装，原词仍保留native/OCR/manual来源。
        # 此时按坐标取回原词，不能因行级来源不同而丢失跨列拆分依据。
        words = [w for w in page.get('words', []) if (w['source'] == line['source'] or line['source'] == 'revision_view') and
                 bbox[0] <= (w['bbox'][0]+w['bbox'][2])/2 <= bbox[2] and
                 bbox[1] <= (w['bbox'][1]+w['bbox'][3])/2 <= bbox[3]]
        parts = []
        last_cell = None
        for word in sorted(words, key=lambda w: (w['bbox'][0], w['bbox'][1])):
            box = word['bbox']
            cell = next((c for c in cells if c[0] <= (box[0]+box[2])/2 <= c[2] and
                         c[1] <= (box[1]+box[3])/2 <= c[3]), None)
            gap = box[0] - parts[-1][-1]['bbox'][2] if parts else 0
            if (not parts or cell != last_cell or
                    word.get('manual_target_item_no') != parts[-1][-1].get('manual_target_item_no') or
                    (cell is None and gap > 1.5 * max(box[3]-box[1], parts[-1][-1]['bbox'][3]-parts[-1][-1]['bbox'][1]))):
                parts.append([])
            parts[-1].append(word)
            last_cell = cell
        # 没有跨区域时沿用原提取文本，避免重新拼词改变原文。
        entries = [line]
        if len(parts) > 1 or any(w.get('manual_target_item_no') for w in words):
            entries = [{'text': ' '.join(w['text'] for w in part),
                        'manual_target_item_no': part[0].get('manual_target_item_no', 0),
                        'bbox': [min(w['bbox'][0] for w in part), min(w['bbox'][1] for w in part),
                                 max(w['bbox'][2] for w in part), max(w['bbox'][3] for w in part)]}
                       for part in parts]
        for entry in entries:
            x0, y0, x1, y1 = [v * dpi / 72 for v in entry['bbox']]
            output.append(OCRLine(page['page'], entry['text'], x0, y0, x1-x0, y1-y0,
                                  100 if line['source'] == 'native' else 0,
                                  manual_target_item_no=entry.get('manual_target_item_no', 0)))
    return output


def parse_items(lines: Sequence[OCRLine], dpi: int, *, cells=None, issues=None) -> Tuple[List[ParsedItem], Dict[str, str], List[str]]:
    def assigned_target(line):
        return type(line.manual_target_item_no) is int and line.manual_target_item_no in ITEM_TITLES
    assigned = [line for line in lines if assigned_target(line)]
    lines = _region_reading_order([line for line in lines if not assigned_target(line)], cells or {}, issues)
    current_no: Optional[int] = None
    buffers: Dict[int, List[OCRLine]] = {item_no: [] for item_no in ITEM_TITLES}
    title_lines: Dict[int, List[OCRLine]] = {item_no: [] for item_no in ITEM_TITLES}
    detached: Dict[str, List[str]] = {"生产企业": []}
    before_first: List[str] = []
    in_production_block = False
    current_region = None

    for line in lines:
        if line.unassigned:
            before_first.append(line.text)
            continue
        if line.region_id != current_region:
            current_no = None
            in_production_block = False
            current_region = line.region_id
        text = normalize_text(line.text)
        compact = compact_for_matching(text)
        if compact.strip(' |:') in SECTION_LINES:
            current_no = None
            in_production_block = False
            continue
        if compact in (':', ';', '|'):
            continue
        start = item_start(text)
        if _belongs_to_parent(text, current_no):
            start = None
        if start:
            current_no = start[0]
            title_lines[current_no].append(line)
            in_production_block = False
            # 原文正文仅去掉一级标题；标点和否定词不在此处改写。
            from .form_field_semantics import heading
            original_heading = heading(line.text)
            remainder = original_heading[2] if original_heading else strip_label(current_no, start[1])
            if remainder:
                buffers[current_no].append(replace(line, text=remainder))
            continue

        if current_no == 30 and compact.startswith("生产企业:"):
            in_production_block = True
            detached["生产企业"].append(text)
            continue
        if in_production_block:
            detached["生产企业"].append(text)
            continue

        if current_no in (31, 32) and any(compact.startswith(prefix) for prefix in STOP_AFTER_ITEM_31_PREFIXES):
            current_no = None
            continue
        if compact in SECTION_LINES:
            current_no = None
            continue
        if current_no is None:
            before_first.append(text)
        else:
            buffers[current_no].append(line)

    # 仅有效人工覆盖层产生该标记；不再凭几何将其猜入相邻字段。
    # 保留正文和来源坐标，字段内容按原页顺序合并，不替换已有人工字段。
    for index, line in enumerate(assigned):
        assigned_line = replace(line, region_id=-index-1)
        buffers[line.manual_target_item_no].append(assigned_line)
        lines.append(assigned_line)
    for item_no in {line.manual_target_item_no for line in assigned}:
        buffers[item_no].sort(key=lambda line: (line.page, line.y, line.x))
    items: List[ParsedItem] = []
    for item_no, title in ITEM_TITLES.items():
        item_lines = buffers[item_no]
        raw = "\n".join(item.text for item in item_lines).strip()
        normalized = compact_for_matching(raw)
        regions: List[Dict[str, Any]] = []
        page_groups: Dict[Tuple[int, int], List[OCRLine]] = {}
        region_lines = item_lines or title_lines[item_no]
        for line in region_lines:
            page_groups.setdefault((line.page, line.region_id), []).append(line)
        for (page_no, region_id), page_lines in sorted(page_groups.items()):
            xs = [line.x for line in page_lines]
            ys = [line.y for line in page_lines]
            x2 = [line.x + line.w for line in page_lines]
            y2 = [line.y + line.h for line in page_lines]
            bbox_px = [min(xs), min(ys), max(x2), max(y2)]
            # 合并后的空白间隔可能盖住邻字段或未归属文字。此时保留
            # 每段实际文字的区域，不能让来源高亮把已排除内容重新圈入。
            overlaps_other = any(
                line.page == page_no and (line.region_id != region_id or line.unassigned) and
                max(bbox_px[0], line.x) < min(bbox_px[2], line.x + line.w) and
                max(bbox_px[1], line.y) < min(bbox_px[3], line.y + line.h)
                for line in lines)
            boxes = ([[line.x, line.y, line.x + line.w, line.y + line.h] for line in page_lines]
                     if overlaps_other else [bbox_px])
            scale = 72.0 / float(dpi or 180)
            for box in boxes:
                regions.append(
                    {
                        "page": page_no,
                        "bbox_px": box,
                        "bbox_pdf": [round(item * scale, 2) for item in box],
                        "coordinate_unit": "pdf_point",
                        "dpi": dpi,
                        **({'position_kind': 'review_region_not_glyph'}
                           if any(line.manual_target_item_no for line in page_lines) else {}),
                    }
                )
        items.append(
            ParsedItem(
                item_no=item_no,
                title=title,
                raw_text=raw,
                normalized_text=normalized,
                subfields=parse_subfields(raw),
                source_pages=sorted({item.page for item in region_lines}),
                source_regions=regions,
                tables=[],
                option_fragments=_option_fragments(item_lines, cells or {}, issues),
            )
        )
    detached_text = {key: normalize_text("\n".join(value)) for key, value in detached.items() if value}
    return items, detached_text, before_first


def pdf_bbox_to_px(bbox: Tuple[float, float, float, float], dpi: int) -> Tuple[int, int, int, int]:
    scale = dpi / 72.0
    return tuple(int(round(item * scale)) for item in bbox)


def tokens_inside(tokens: Sequence[OCRToken], bbox_px: Tuple[int, int, int, int]) -> str:
    x0, y0, x1, y1 = bbox_px
    selected: List[OCRToken] = []
    for token in tokens:
        cx = token.x + token.w / 2
        cy = token.y + token.h / 2
        if x0 <= cx <= x1 and y0 <= cy <= y1:
            selected.append(token)
    if not selected:
        return ""
    selected.sort(key=lambda item: (item.y, item.x))
    lines: List[List[OCRToken]] = []
    for token in selected:
        if not lines:
            lines.append([token])
            continue
        median_y = statistics.mean(item.y for item in lines[-1])
        if abs(token.y - median_y) <= max(8, token.h * 0.8):
            lines[-1].append(token)
        else:
            lines.append([token])
    return "\n".join(smart_join([item.text for item in sorted(line, key=lambda obj: obj.x)]) for line in lines)


def crop_image_bytes(page_image_path: Path, bbox_px: Tuple[int, int, int, int]) -> bytes:
    pixmap = fitz.Pixmap(str(page_image_path))
    image_doc = fitz.open("png", pixmap.tobytes("png"))
    page = image_doc[0]
    x0, y0, x1, y1 = bbox_px
    rect = fitz.Rect(max(0, x0), max(0, y0), max(x0 + 1, x1), max(y0 + 1, y1))
    cropped = page.get_pixmap(clip=rect, alpha=False)
    data = cropped.tobytes("png")
    image_doc.close()
    return data


def ocr_table_cell(client: OCRServiceClient, page_image_path: Path, bbox_px: Tuple[int, int, int, int], lang: str) -> str:
    try:
        return compact_for_matching(client.image_to_text(crop_image_bytes(page_image_path, bbox_px), lang=lang, psm=6))
    except Exception:
        return ""


def ocr_pdf_region(
    pdf_path: str | Path,
    region: Dict[str, Any],
    *,
    margin_pt: float = 6.0,
    zoom: float = 2.5,
    lang: str | None = None,
    psm: int = 6,
    ocr_client: OCRServiceClient | None = None,
) -> str:
    pdf_file = Path(pdf_path)
    bbox = region.get("bbox_pdf", []) if isinstance(region, dict) else []
    page_no = int(region.get("page", 0) or 0) if isinstance(region, dict) else 0
    if page_no <= 0 or len(bbox) != 4:
        return ""
    client = ocr_client or OCRServiceClient(settings.ocr_service_url, timeout_seconds=settings.ocr_timeout_seconds)
    ocr_lang = str(lang or settings.ocr_lang or "chi_sim+eng")
    with fitz.open(pdf_file) as doc:
        if page_no > len(doc):
            return ""
        page = doc[page_no - 1]
        page.set_rotation(0)
        x0, y0, x1, y1 = [float(item or 0) for item in bbox]
        rect = fitz.Rect(x0 - margin_pt, y0 - margin_pt, x1 + margin_pt, y1 + margin_pt)
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=rect, alpha=False)
        try:
            text = normalize_text(client.image_to_text(pix.tobytes("png"), lang=ocr_lang, psm=psm))
            if not text and ocr_region_errors.get() is not None:
                ocr_region_errors.get().append({'page': page_no, 'stage': 'ocr', 'reason': 'OCR 未返回有效文字', 'bbox_pdf': bbox})
            return text
        except Exception as exc:
            if ocr_region_errors.get() is not None:
                from agent.agent_backend.services.filing_parse_outcome import ocr_exception_details, MESSAGES
                detail = ocr_exception_details(exc)
                ocr_region_errors.get().append({'page': page_no, 'stage': 'ocr', **detail, 'reason': MESSAGES[detail['code']], 'bbox_pdf': bbox})
            return ""


def attach_tables(
    pdf_path: Path,
    dpi: int,
    lang: str,
    rendered_pages: Sequence[Path],
    client: OCRServiceClient,
    tokens_by_page: Dict[int, List[OCRToken]],
    lines_by_page: Dict[int, List[OCRLine]],
    items: List[ParsedItem],
) -> List[Dict[str, Any]]:
    item_map = {item.item_no: item for item in items}
    output: List[Dict[str, Any]] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page_no, page in enumerate(pdf.pages, start=1):
            for table_index, table in enumerate(page.find_tables()):
                if len(table.cells) < 4:
                    continue
                rows: List[List[str]] = []
                for row in table.rows:
                    cells: List[str] = []
                    for cell in row.cells:
                        if not cell:
                            cells.append("")
                            continue
                        bbox_px = pdf_bbox_to_px(cell, dpi)
                        text = tokens_inside(tokens_by_page.get(page_no, []), bbox_px)
                        if not text:
                            text = ocr_table_cell(client, rendered_pages[page_no - 1], bbox_px, lang)
                        cells.append(text)
                    rows.append(cells)

                table_top = pdf_bbox_to_px(table.bbox, dpi)[1]
                candidates: List[Tuple[int, int]] = []
                for line in lines_by_page.get(page_no, []):
                    start = item_start(line.text)
                    if start and line.y <= table_top:
                        candidates.append((line.y, start[0]))
                nearest_item_no = max(candidates, default=(0, 0))[1] or None
                table_json = {
                    "page": page_no,
                    "table_index": table_index,
                    "bbox_pdf": [round(item, 2) for item in table.bbox],
                    "nearest_item_no": nearest_item_no,
                    "rows": rows,
                }
                output.append(table_json)
                if nearest_item_no in item_map:
                    item_map[nearest_item_no].tables.append(table_json)
    return output


def extra_blocks(raw_text: str, detached: Dict[str, str]) -> Dict[str, str]:
    blocks = dict(detached)
    normalized = normalize_text(raw_text)
    declaration = re.search(r"声明\s*(.*?)\s*申请事项", normalized, flags=re.S)
    if declaration:
        blocks["声明"] = normalize_text(declaration.group(1))
    special = re.search(r"其他特别申明事项\s*:\s*(.*?)(?:法定代表人|年\s+月\s+日|申请事项)", normalized, flags=re.S)
    if special:
        blocks["其他特别申明事项"] = normalize_text(special.group(1))
    return blocks


def parse_drug_supplement_pdf(
    pdf_path: str | Path,
    *,
    ocr_client: OCRServiceClient | None = None,
    dpi: int = 180,
    lang: str | None = None,
    psm: int = 6,
    force_ocr: bool = False,
) -> Dict[str, Any]:
    pdf_file = Path(pdf_path)
    if not pdf_file.exists():
        raise FileNotFoundError(str(pdf_file))

    client = ocr_client or OCRServiceClient(settings.ocr_service_url, timeout_seconds=settings.ocr_timeout_seconds)
    ocr_lang = str(lang or settings.ocr_lang or "chi_sim+eng")

    with fitz.open(pdf_file) as doc:
        page_count = len(doc)
        metadata = dict(doc.metadata or {})
    from agent.agent_backend.utils.parser.pdf_page_extractor import extract_pdf_pages
    pages = extract_pdf_pages(str(pdf_file), ocr_client=client, dpi=dpi, lang=ocr_lang, force_ocr=force_ocr, psm=psm)
    return build_pdf_form_from_pages(pages, source_file=pdf_file.name, metadata=metadata,
                                     dpi=dpi, ocr_lang=ocr_lang, psm=psm)


def manual_table_field_pairs(table):
    """仅返回能由完整逻辑单元格唯一配对的申请字段，不推断字形位置。"""
    from agent.agent_backend.utils.parser.form_field_semantics import heading
    cells = table.get('cells', [])
    if not table.get('rows') or not cells:
        return None
    pairs = []
    for row_index in sorted({cell['row'] for cell in cells}):
        logical = sorted((cell for cell in cells if cell['row'] == row_index), key=lambda cell: cell['column'])
        if not logical or len(logical) % 2:
            return None
        for offset in range(0, len(logical), 2):
            label_cell, value_cell = logical[offset:offset + 2]
            label = heading(label_cell['text'])
            if (not label or label[2]
                    or label_cell.get('rowspan', 1) != value_cell.get('rowspan', 1)
                    or label_cell['column'] + label_cell.get('colspan', 1) != value_cell['column']):
                return None
            pairs.append((label[0], value_cell['text']))
    return pairs


def build_pdf_form_from_pages(pages, *, source_file='', metadata=None, dpi=180, ocr_lang='', psm=6):
    """从已有有效页重建字段结构，不读原件、不调用OCR、不改写传入证据。"""
    from copy import deepcopy
    pages = deepcopy(pages)
    from agent.agent_backend.utils.parser.form_content_evidence import pdf_form_structure, form_page_lines
    structure = pdf_form_structure(pages)
    use_ocr = any(p.get('ocr_calls') for p in pages)
    all_lines = []
    manual_table_issues = []
    for page in pages:
        view = form_page_lines(page, structure)
        # 完整人工表格不再有逐字坐标，不能把Markdown竖线当作申请字段。
        # 依据真实逻辑单元格读取标签—值对，合并覆盖位置不是空白值。
        from agent.agent_backend.utils.parser.form_field_semantics import heading
        for table in page.get('tables', []):
            if table.get('source') != 'manual_revision':
                continue
            rows = table.get('rows', [])
            cells = table.get('cells', [])
            pairs = manual_table_field_pairs(table)
            if pairs is None:
                candidates = sorted({label[0] for cell in cells
                                     if (label := heading(cell['text'])) and not label[2]})
                if candidates:
                    # 有字段标签但不能唯一建立对应时，禁止继续把Markdown猜入普通字段。
                    bounds = table['bbox_pdf']
                    view['lines'] = [line for line in view['lines']
                                     if not (line.get('source') == 'manual_revision' and line.get('bbox') == bounds)]
                    manual_table_issues.append({
                        'page': page['page'], 'code': 'field_region_unassigned', 'stage': 'field_region',
                        'reason': '人工表格含申请字段标签，但标签和值的对应仍不明确，请核对合并关系和字段归属',
                        'text': table.get('markdown', ''), 'candidate_items': candidates,
                        'bbox_px': [v * dpi / 72 for v in bounds], 'needs_review': True})
                continue
            bounds = table['bbox_pdf']
            view['lines'] = [line for line in view['lines']
                             if not (line.get('source') == 'manual_revision' and line.get('bbox') == bounds)]
            x0, y0, x1, y1 = [v * dpi / 72 for v in bounds]
            for target, value in pairs:
                all_lines.append(OCRLine(page['page'], value, x0, y0, x1-x0, y1-y0, 0,
                                         manual_target_item_no=target))
        all_lines.extend(_page_form_lines(view, dpi))
    all_lines.sort(key=lambda l: (l.page, l.y, l.x))
    raw_text = '\n\n'.join(p.get('raw_text', '') for p in pages)
    cells = {p['page']: [[v * dpi / 72 for v in c['bbox_pdf']]
                        for t in p['tables'] for c in t.get('cells', []) if c.get('bbox_pdf')] for p in pages}
    region_issues = manual_table_issues
    items, detached, before_first = parse_items(all_lines, dpi=dpi, cells=cells, issues=region_issues)
    for issue in region_issues:
        issue.update(bbox_pdf=[round(v * 72 / dpi, 2) for v in issue['bbox_px']],
                     coordinate_unit='pdf_point', dpi=dpi, exception_type='')
        issue['source_regions'] = [{key: issue[key] for key in ('page', 'bbox_px', 'bbox_pdf', 'coordinate_unit', 'dpi')}]
    tables = [t for p in pages for t in p['tables']]
    item_map = {i.item_no: i for i in items}
    for table in tables:
        candidates = [(l.page, l.y, item_start(l.text)[0]) for l in all_lines
                      if (l.page < table['page'] or (l.page == table['page'] and l.y <= table['bbox_pdf'][1]*dpi/72)) and item_start(l.text)]
        nearest = max(candidates, default=(0, 0, 0))[2]
        if table.get('source') == 'manual_revision':
            # 完整人工正文只有区域位置，不能把这些字段的共同外框用于
            # “最近标题”猜测。物料表须同时有已核对的17项标签和唯一表头。
            bounds = [v * dpi / 72 for v in table['bbox_pdf']]
            explicit_targets = {l.manual_target_item_no for l in all_lines
                                if l.page == table['page'] and l.manual_target_item_no
                                and l.x <= bounds[0] and l.y <= bounds[1]
                                and l.x + l.w >= bounds[2] and l.y + l.h >= bounds[3]}
            if 17 in explicit_targets and table.get('rows'):
                from agent.agent_backend.services.filing_parse_outcome import MATERIAL_SOURCE_ALIASES
                columns = {}
                ambiguous = False
                for column, value in enumerate(table['rows'][0]):
                    value = compact_for_matching(str(value))
                    matches = [key for key, aliases in MATERIAL_SOURCE_ALIASES.items()
                               if any(value in (compact_for_matching(label), compact_for_matching(label + '名称'))
                                      for label in aliases)]
                    if len(matches) > 1 or any(key in columns for key in matches):
                        ambiguous = True
                    for key in matches:
                        columns[key] = column
                if ambiguous:
                    raise ValueError('人工核对的物料来源表表头重复或归属不唯一，请明确各列含义后保存')
                if (not ambiguous and 'material_name' in columns
                        and ('register_no' in columns or 'manufacturer' in columns)):
                    nearest = 17
            if 29 in explicit_targets and table.get('rows'):
                headers = [compact_for_matching(str(value)).strip(':') for value in table['rows'][0]]
                required = ('受理号', '批件号', '批准内容')
                if any(headers.count(label) > 1 for label in required):
                    raise ValueError('人工核对的历次申请表存在重复表头，请明确各列含义后保存')
                if all(headers.count(label) == 1 for label in required):
                    nearest = 29
                    # 历次申请是正文型字段，表格仅进入tables还不足以供
                    # 页面及报告读取；保留完整表头与行，而不只抽取首个编号。
                    item = item_map[29]
                    markdown = str(table.get('markdown') or '').strip()
                    if markdown and markdown not in item.raw_text:
                        item.raw_text = '\n\n'.join(part for part in (item.raw_text, markdown) if part)
                        item.normalized_text = compact_for_matching(item.raw_text)
                    item.source_regions.append({'page': table['page'], 'bbox_pdf': list(table['bbox_pdf']),
                        'bbox_px': bounds, 'dpi': dpi, 'coordinate_unit': 'pdf_point', 'source': 'manual_revision',
                        'position_kind': 'review_region_not_glyph', 'table_id': table.get('id')})
        table['nearest_item_no'] = nearest or None
        if nearest in item_map:
            item_map[nearest].tables.append(table)
            if table.get('source') == 'manual_revision' and manual_table_field_pairs(table) is None:
                # 普通数据表不包含申请字段的标签—值对；已确定父字段时，
                # 不能只挂在内部tables里而让页面/下游读取的字段原文仍为空。
                # 复用已有归属，不从表头猜测新字段；有交叉归属诊断时不补值。
                bounds = table['bbox_pdf']
                uncertain = any(issue.get('page') == table['page']
                                and issue.get('code') in ('field_region_crossing', 'field_region_unassigned')
                                and (not issue.get('bbox_pdf') or
                                     (issue['bbox_pdf'][0] < bounds[2] and issue['bbox_pdf'][2] > bounds[0]
                                      and issue['bbox_pdf'][1] < bounds[3] and issue['bbox_pdf'][3] > bounds[1]))
                                for issue in region_issues)
                if not uncertain:
                    item = item_map[nearest]
                    markdown = str(table.get('markdown') or '').strip()
                    if markdown and markdown not in item.raw_text:
                        item.raw_text = '\n\n'.join(part for part in (item.raw_text, markdown) if part)
                        item.normalized_text = compact_for_matching(item.raw_text)
                    if not any(region.get('page') == table['page'] and region.get('bbox_pdf') == list(bounds)
                               and region.get('source') == 'manual_revision' and 'table_id' in region
                               and region.get('table_id') == table.get('id') for region in item.source_regions):
                        item.source_regions.append({'page': table['page'], 'bbox_pdf': list(bounds),
                            'bbox_px': [v * dpi / 72 for v in bounds], 'dpi': dpi,
                            'coordinate_unit': 'pdf_point', 'source': 'manual_revision',
                            'position_kind': 'review_region_not_glyph', 'table_id': table.get('id')})

    warnings: List[str] = []
    diagnostics = dict(pages[0].get('parse_diagnostics') or {}) if pages else {'status': 'failed'}
    if region_issues:
        uncertain_pages = {issue['page'] for issue in region_issues}
        diagnostics.update(status='partial', field_region_issues=region_issues,
                           failed_pages=sorted(set(diagnostics.get('failed_pages', [])) | uncertain_pages),
                           successful_pages=[p for p in diagnostics.get('successful_pages', []) if p not in uncertain_pages])
        for page in pages:
            page['parse_diagnostics'] = diagnostics
            if page['page'] in uncertain_pages:
                page['field_region_issues'] = [issue for issue in region_issues if issue['page'] == page['page']]
                # 有效页可能已包含上次字段重建的同一证据；重新映射不能
                # 每次追加一个阻塞副本。仅排除完整相等的新副本，不删除
                # 历史证据，也不合并位置、原文或候选字段不同的问题。
                errors = page.setdefault('errors', [])
                for issue in page['field_region_issues']:
                    if issue not in errors:
                        errors.append(issue)
                if page['status'] == 'success':
                    page['status'] = 'partial'
        warnings.append('部分文字跨越字段边界或缺少归属依据，已保留原文与坐标且未自动补值，请核对 unassigned_regions。')
    empty_items = [item.item_no for item in items if not item.normalized_text and not item.tables]
    if empty_items:
        warnings.append(f"未抽取到内容的表单项: {empty_items}。空白模板场景下这可能是正常现象。")
    if use_ocr:
        warnings.append("部分页面或图像区域使用了 OCR；实际路径和质量诊断见 pages。")
    warnings.append("PDF 申请表解析结果仅作为辅助填充结果，勾选项、签章和低置信度字段仍需人工复核。")

    return {
        "source_file": source_file,
        "page_count": len(pages),
        "metadata": metadata or {},
        "parser": {
            "strategy": "ocr_fallback" if use_ocr else "pdf_text_layer",
            "dpi": dpi,
            "ocr_lang": ocr_lang,
            "psm": psm,
        },
        "raw_text": raw_text,
        "form_structure": structure,
        "pages": pages,
        "parse_diagnostics": diagnostics,
        "unassigned_regions": region_issues,
        "extra_blocks": extra_blocks(raw_text, detached),
        "items": [asdict(item) for item in items],
        "tables": tables,
        "unnumbered_text_before_first_item": before_first,
        "warnings": warnings,
    }


def parse_drug_supplement_pdf_to_json(pdf_path: str | Path, output_path: str | Path) -> Dict[str, Any]:
    result = parse_drug_supplement_pdf(pdf_path)
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
