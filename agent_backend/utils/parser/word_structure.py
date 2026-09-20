"""Low-level OOXML helpers shared by Word document parsers.

The public ``python-docx`` paragraph API intentionally omits several pieces of
information that users can see in Word, most notably automatic list numbers,
hyperlink text in older versions, outline levels and text-box content.  This
module reads those structures without making any CTD or filing-review decision.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph


def _int_value(value: Any, default: int = 0) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


def _xml_value(element: Any, child_name: str, default: str = "") -> str:
    if element is None:
        return default
    child = element.find(qn(child_name))
    if child is None:
        return default
    return str(child.get(qn("w:val")) or default)


def visible_text_from_xml(element: Any) -> str:
    """Return visible text in XML order, including hyperlinks and text boxes."""

    parts: List[str] = []
    for node in element.iter():
        if node.tag == qn("w:t"):
            parts.append(str(node.text or ""))
        elif node.tag == qn("w:tab"):
            parts.append("\t")
        elif node.tag in {qn("w:br"), qn("w:cr")}:
            parts.append("\n")
    value = "".join(parts)
    value = value.replace("\u3000", " ").replace("\xa0", " ")
    value = re.sub(r"[ \t]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    return value.strip()


def paragraph_visible_text(paragraph: Paragraph) -> str:
    return visible_text_from_xml(paragraph._p)


def _style_chain(paragraph: Paragraph) -> Iterable[Any]:
    style = getattr(paragraph, "style", None)
    seen = set()
    while style is not None:
        style_id = str(getattr(style, "style_id", "") or id(style))
        if style_id in seen:
            break
        seen.add(style_id)
        yield style
        style = getattr(style, "base_style", None)


def _num_properties_from_ppr(ppr: Any) -> Optional[Tuple[int, int]]:
    if ppr is None:
        return None
    num_pr = ppr.find(qn("w:numPr"))
    if num_pr is None:
        return None
    num_id = _int_value(_xml_value(num_pr, "w:numId", "-1"), -1)
    ilvl = _int_value(_xml_value(num_pr, "w:ilvl", "0"), 0)
    if num_id <= 0:
        return None
    return num_id, max(0, ilvl)


def paragraph_number_properties(paragraph: Paragraph) -> Optional[Tuple[int, int]]:
    direct = _num_properties_from_ppr(getattr(paragraph._p, "pPr", None))
    if direct is not None:
        return direct
    for style in _style_chain(paragraph):
        element = getattr(style, "element", None)
        inherited = _num_properties_from_ppr(
            element.find(qn("w:pPr")) if element is not None else None
        )
        if inherited is not None:
            return inherited
    return None


def _outline_from_ppr(ppr: Any) -> Optional[int]:
    if ppr is None:
        return None
    outline = ppr.find(qn("w:outlineLvl"))
    if outline is None:
        return None
    value = _int_value(outline.get(qn("w:val")), -1)
    if value < 0 or value >= 9:
        return None
    return value + 1


def paragraph_outline_level(paragraph: Paragraph) -> Optional[int]:
    direct = _outline_from_ppr(getattr(paragraph._p, "pPr", None))
    if direct is not None:
        return direct
    for style in _style_chain(paragraph):
        element = getattr(style, "element", None)
        inherited = _outline_from_ppr(
            element.find(qn("w:pPr")) if element is not None else None
        )
        if inherited is not None:
            return inherited
    return None


def _roman(number: int) -> str:
    if number <= 0:
        return str(number)
    values = (
        (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
        (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
        (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
    )
    out: List[str] = []
    remaining = number
    for value, token in values:
        while remaining >= value:
            out.append(token)
            remaining -= value
    return "".join(out)


def _letters(number: int) -> str:
    if number <= 0:
        return str(number)
    out: List[str] = []
    remaining = number
    while remaining:
        remaining, mod = divmod(remaining - 1, 26)
        out.append(chr(ord("A") + mod))
    return "".join(reversed(out))


def _chinese_number(number: int) -> str:
    if number <= 0 or number > 9999:
        return str(number)
    digits = "零一二三四五六七八九"
    units = ("", "十", "百", "千")
    chars: List[str] = []
    zero_pending = False
    for idx in range(3, -1, -1):
        divisor = 10 ** idx
        digit = number // divisor
        number %= divisor
        if digit:
            if zero_pending and chars:
                chars.append(digits[0])
            if not (idx == 1 and digit == 1 and not chars):
                chars.append(digits[digit])
            chars.append(units[idx])
            zero_pending = False
        elif chars and number:
            zero_pending = True
    return "".join(chars) or digits[0]


def _format_counter(number: int, number_format: str) -> str:
    fmt = str(number_format or "decimal")
    if fmt in {"upperLetter", "upperAlpha"}:
        return _letters(number)
    if fmt in {"lowerLetter", "lowerAlpha"}:
        return _letters(number).lower()
    if fmt == "upperRoman":
        return _roman(number)
    if fmt == "lowerRoman":
        return _roman(number).lower()
    if fmt in {"chineseCounting", "chineseLegalSimplified", "ideographTraditional"}:
        return _chinese_number(number)
    return str(number)


@dataclass(frozen=True)
class NumberingResult:
    text: str
    num_id: int
    level: int
    number_format: str
    template: str


class WordNumberingResolver:
    """Stateful renderer for Word automatic multilevel numbering."""

    def __init__(self, document: Any) -> None:
        self._abstract_levels: Dict[int, Dict[int, Dict[str, Any]]] = {}
        self._num_to_abstract: Dict[int, int] = {}
        self._start_overrides: Dict[Tuple[int, int], int] = {}
        self._counters: Dict[int, Dict[int, int]] = {}
        self._load(document)

    def _load(self, document: Any) -> None:
        try:
            root = document.part.numbering_part.element
        except Exception:
            return
        for abstract in root.findall(qn("w:abstractNum")):
            abstract_id = _int_value(abstract.get(qn("w:abstractNumId")), -1)
            if abstract_id < 0:
                continue
            levels: Dict[int, Dict[str, Any]] = {}
            for level in abstract.findall(qn("w:lvl")):
                ilvl = _int_value(level.get(qn("w:ilvl")), 0)
                levels[ilvl] = {
                    "start": max(1, _int_value(_xml_value(level, "w:start", "1"), 1)),
                    "format": _xml_value(level, "w:numFmt", "decimal"),
                    "template": _xml_value(level, "w:lvlText", f"%{ilvl + 1}."),
                }
            self._abstract_levels[abstract_id] = levels

        for num in root.findall(qn("w:num")):
            num_id = _int_value(num.get(qn("w:numId")), -1)
            abstract_id = _int_value(_xml_value(num, "w:abstractNumId", "-1"), -1)
            if num_id <= 0 or abstract_id < 0:
                continue
            self._num_to_abstract[num_id] = abstract_id
            for override in num.findall(qn("w:lvlOverride")):
                ilvl = _int_value(override.get(qn("w:ilvl")), 0)
                start = _xml_value(override, "w:startOverride", "")
                if start:
                    self._start_overrides[(num_id, ilvl)] = max(1, _int_value(start, 1))

    def resolve(self, paragraph: Paragraph) -> Optional[NumberingResult]:
        properties = paragraph_number_properties(paragraph)
        if properties is None:
            return None
        num_id, ilvl = properties
        abstract_id = self._num_to_abstract.get(num_id)
        levels = self._abstract_levels.get(abstract_id if abstract_id is not None else -1, {})
        level = levels.get(ilvl)
        if not level:
            return None
        number_format = str(level.get("format", "decimal") or "decimal")
        template = str(level.get("template", "") or "")
        if number_format in {"bullet", "none"} or not template:
            return None

        counters = self._counters.setdefault(num_id, {})
        start = self._start_overrides.get((num_id, ilvl), int(level.get("start", 1) or 1))
        counters[ilvl] = counters.get(ilvl, start - 1) + 1
        for deeper in [key for key in counters if key > ilvl]:
            counters.pop(deeper, None)

        rendered = template
        for placeholder_level in range(9):
            placeholder = f"%{placeholder_level + 1}"
            if placeholder not in rendered:
                continue
            definition = levels.get(placeholder_level, {})
            placeholder_start = self._start_overrides.get(
                (num_id, placeholder_level), int(definition.get("start", 1) or 1)
            )
            counter = counters.get(placeholder_level, placeholder_start)
            rendered = rendered.replace(
                placeholder,
                _format_counter(counter, str(definition.get("format", "decimal") or "decimal")),
            )
        rendered = re.sub(r"\s+", " ", rendered).strip()
        if not rendered:
            return None
        return NumberingResult(
            text=rendered,
            num_id=num_id,
            level=ilvl + 1,
            number_format=number_format,
            template=template,
        )


def combine_number_and_text(number_text: str, paragraph_text: str) -> str:
    number = str(number_text or "").strip()
    text = str(paragraph_text or "").strip()
    if not number:
        return text
    comparable_number = re.sub(r"[\s。．]+", ".", number).strip(".").lower()
    comparable_text = re.sub(r"[\s。．]+", ".", text).strip(".").lower()
    if comparable_text.startswith(comparable_number):
        return text
    separator = "" if number.endswith((".", "。", "．", "-", "—", ":", "：", "、")) else " "
    return f"{number}{separator}{text}".strip()
