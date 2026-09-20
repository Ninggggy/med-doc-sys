import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from docx import Document
from docx.document import Document as _Document
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table
from docx.text.paragraph import Paragraph

try:
    from agent.agent_backend.utils.parser.word_structure import (
        WordNumberingResolver,
        combine_number_and_text,
        paragraph_outline_level,
        paragraph_visible_text,
        visible_text_from_xml,
    )
except ImportError:  # pragma: no cover - standalone compatibility
    from agent_backend.utils.parser.word_structure import (
        WordNumberingResolver,
        combine_number_and_text,
        paragraph_outline_level,
        paragraph_visible_text,
        visible_text_from_xml,
    )


def parse_filing_change_docx(file_path: str) -> List[Dict[str, Any]]:
    """解析备案变更类 Word 资料，保留段落/表格顺序并抽取稳定性结构化数据。"""
    path = Path(file_path)
    doc = Document(str(path))
    parser = _FilingChangeDocxParser(path.name)
    return parser.parse(doc)


class _FilingChangeDocxParser:
    def __init__(self, file_name: str) -> None:
        self.file_name = file_name
        self.chunk_index = 0
        self.current_section: List[str] = []
        self.buffer_lines: List[str] = []
        self.pending_caption: str = ""
        self.chunks: List[Dict[str, Any]] = []
        self.numbering_resolver: Optional[WordNumberingResolver] = None

    def parse(self, document: _Document) -> List[Dict[str, Any]]:
        self.numbering_resolver = WordNumberingResolver(document)
        for block in self._iter_block_items(document):
            if isinstance(block, Paragraph):
                self._handle_paragraph(block)
            elif isinstance(block, Table):
                self._handle_table(block)
        self._flush_buffer()
        return self.chunks

    def _handle_paragraph(self, paragraph: Paragraph) -> None:
        raw_text = self._normalize_text(paragraph_visible_text(paragraph) or paragraph.text)
        numbering = self.numbering_resolver.resolve(paragraph) if self.numbering_resolver else None
        text = self._normalize_text(combine_number_and_text(numbering.text if numbering else "", raw_text))
        if not text:
            return
        if self._is_section_heading(
            text,
            paragraph.style.name if paragraph.style else "",
            outline_level=paragraph_outline_level(paragraph),
        ):
            self._flush_buffer()
            self.current_section = [text]
            self.buffer_lines.append(text)
            self.pending_caption = ""
            return
        if self._is_table_caption(text):
            self._flush_buffer()
            self.pending_caption = text
            self.buffer_lines.append(text)
            return
        self.buffer_lines.append(text)

    def _handle_table(self, table: Table) -> None:
        rows = self._extract_table_rows(table)
        if not rows:
            return
        table_type = self._classify_table(rows, self.pending_caption)
        structured = self._build_structured_table(table_type, rows, self.pending_caption)
        markdown = self._table_to_markdown(rows)
        self._flush_buffer()
        self.chunk_index += 1
        section_path = list(self.current_section or [])
        if self.pending_caption and self.pending_caption not in section_path:
            section_path = section_path + [self.pending_caption]
        title = self.pending_caption or (section_path[-1] if section_path else f"表格{self.chunk_index}")
        self.chunks.append(
            {
                "chunk_id": f"filing_change_chunk_{self.chunk_index}",
                "section_name": title,
                "section_path": section_path,
                "summary": f"{table_type}:{title}",
                "text": self.pending_caption or title,
                "tables": [
                    {
                        "table_type": table_type,
                        "raw_rows": rows,
                        "caption": self.pending_caption,
                        "headers": structured.get("headers", []),
                        "rows": structured.get("rows", []),
                        "markdown": markdown,
                        "structured_data": structured,
                    }
                ],
            }
        )
        self.pending_caption = ""

    def _flush_buffer(self) -> None:
        text = "\n".join([line for line in self.buffer_lines if self._normalize_text(line)]).strip()
        self.buffer_lines = []
        if not text:
            return
        self.chunk_index += 1
        section_path = list(self.current_section or [])
        title = section_path[-1] if section_path else f"段落{self.chunk_index}"
        self.chunks.append(
            {
                "chunk_id": f"filing_change_chunk_{self.chunk_index}",
                "section_name": title,
                "section_path": section_path,
                "summary": title,
                "text": text,
                "tables": [],
            }
        )

    @staticmethod
    def _iter_block_items(parent: _Document):
        parent_elm = parent.element.body if isinstance(parent, _Document) else parent._tc
        for child in parent_elm.iterchildren():
            if isinstance(child, CT_P):
                yield Paragraph(child, parent)
            elif isinstance(child, CT_Tbl):
                yield Table(child, parent)

    @staticmethod
    def _normalize_text(value: Any) -> str:
        text = str(value or "").replace("\r", "\n").replace("\t", " ")
        text = text.replace("\u3000", " ").replace("\xa0", " ")
        text = re.sub(r"[ ]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    @staticmethod
    def _is_section_heading(text: str, style_name: str, outline_level: Optional[int] = None) -> bool:
        value = str(text or "").strip()
        style = str(style_name or "").strip().lower()
        if value in {"样品来源及批号", "稳定性试验内容", "试验起止日期", "考察项目", "试验方案和数据结果", "补充说明", "附件："}:
            return True
        if re.match(r"^[123456789]\.", value):
            return True
        if value.endswith("：") and len(value) <= 20 and ("结果分析" in value or "附件" in value):
            return True
        if outline_level is not None or "heading" in style or "标题" in style or "caption" in style:
            return True
        return False

    @staticmethod
    def _is_table_caption(text: str) -> bool:
        value = str(text or "").strip()
        return bool(re.match(r"^表\d+[：:]", value))

    def _extract_table_rows(self, table: Table) -> List[List[str]]:
        rows: List[List[str]] = []
        for row in table.rows:
            values = [
                self._normalize_text(visible_text_from_xml(cell._tc) or cell.text).replace("\n", " / ")
                for cell in row.cells
            ]
            rows.append(values)
        rows = self._collapse_duplicate_columns(rows)
        return rows

    @staticmethod
    def _collapse_duplicate_columns(rows: List[List[str]]) -> List[List[str]]:
        if not rows:
            return rows
        max_len = max(len(row) for row in rows)
        normalized = [row + [""] * (max_len - len(row)) for row in rows]
        keep_indexes: List[int] = []
        for idx in range(max_len):
            if idx == 0:
                keep_indexes.append(idx)
                continue
            prev_col = [row[idx - 1] for row in normalized]
            curr_col = [row[idx] for row in normalized]
            if curr_col != prev_col:
                keep_indexes.append(idx)
        return [[row[idx] for idx in keep_indexes] for row in normalized]

    @staticmethod
    def _classify_table(rows: List[List[str]], caption: str) -> str:
        from agent.agent_backend.services.filing_change_extraction_service import classify_table
        return classify_table(rows, caption)

    def _build_structured_table(self, table_type: str, rows: List[List[str]], caption: str) -> Dict[str, Any]:
        if table_type == "sample_source":
            return self._parse_keyed_table(rows)
        if table_type == "quality_standard_revision":
            return self._parse_keyed_table(rows)
        if table_type == "report_explanation":
            return self._parse_keyed_table(rows, header_row_index=1)
        if table_type == "standard_version_timeline":
            return self._parse_keyed_table(rows)
        if table_type == "stability_result":
            return self._parse_stability_table(rows, caption)
        headers = rows[0] if rows else []
        return {"headers": headers, "rows": rows[1:] if len(rows) > 1 else [], "caption": caption}

    def _parse_keyed_table(self, rows: List[List[str]], header_row_index: int = 0) -> Dict[str, Any]:
        if not rows:
            return {"headers": [], "rows": [], "records": []}
        header_row_index = min(max(header_row_index, 0), len(rows) - 1)
        headers = rows[header_row_index]
        data_rows = rows[header_row_index + 1 :]
        records: List[Dict[str, Any]] = []
        for row in data_rows:
            record: Dict[str, Any] = {}
            for idx, header in enumerate(headers):
                key = header or f"col_{idx + 1}"
                record[key] = row[idx] if idx < len(row) else ""
            records.append(record)
        return {"headers": headers, "rows": data_rows, "records": records}

    @staticmethod
    def _stability_header_role(text: str) -> str:
        value = re.sub(r"[\s/\\()（）]+", "", str(text or "").strip().lower())
        if not value:
            return ""
        if any(token in value for token in ["可接受标准", "接受标准", "质量标准", "限度", "specification"]):
            return "limit"
        if any(token in value for token in ["明细项目", "子项目", "分项", "检测指标"]):
            return "indicator_detail"
        if any(token in value for token in ["考察项目", "检测项目", "检验项目", "质量指标", "testitem"]):
            return "indicator"
        if any(token in value for token in ["检测结果", "测定结果", "实测值", "结果", "result"]):
            return "result"
        if any(token in value for token in ["时间点", "考察时间", "考察周期", "时间", "month"]):
            return "time_point"
        if any(token in value for token in ["批号", "batch"]):
            return "batch_no"
        if any(token in value for token in ["放置方向", "方向"]):
            return "orientation"
        return ""

    def _find_stability_header(self, rows: List[List[str]]) -> Dict[str, Any]:
        best: Dict[str, Any] = {"score": -1, "start": 0, "end": 1, "headers": rows[0] if rows else []}
        max_width = max((len(row) for row in rows), default=0)
        for start in range(min(len(rows), 15)):
            for depth in range(1, min(3, len(rows) - start) + 1):
                headers: List[str] = []
                for col_idx in range(max_width):
                    values: List[str] = []
                    for row in rows[start : start + depth]:
                        value = self._normalize_text(row[col_idx] if col_idx < len(row) else "")
                        if value and (not values or value != values[-1]):
                            values.append(value)
                    headers.append(" / ".join(values))
                roles = [self._stability_header_role(value) for value in headers]
                time_like = sum(
                    1
                    for index, value in enumerate(headers)
                    if roles[index] in ('', 'result') and re.search(r"(?:\d+(?:\.\d+)?\s*(?:月|年|天|month)|起始|初始|正置|倒置)", value, re.I)
                )
                score = (
                    (6 if "indicator" in roles or "indicator_detail" in roles else 0)
                    + (6 if "limit" in roles else 0)
                    + (4 if "result" in roles else 0)
                    + (3 if "time_point" in roles else 0)
                    + min(time_like, 6) * 2
                    + (1 if "batch_no" in roles else 0)
                    - (depth - 1)
                )
                if score > int(best.get("score", -1)):
                    best = {"score": score, "start": start, "end": start + depth, "headers": headers}
        return best

    def _extract_stability_metadata(self, rows: List[List[str]], header_start: int) -> Dict[str, str]:
        metadata: Dict[str, str] = {}
        for meta_row in rows[:header_start]:
            for idx, raw_cell in enumerate(meta_row):
                cell = self._normalize_text(raw_cell)
                match = re.match(r"^\s*([^：:]{1,30})[：:]\s*(.+)\s*$", cell)
                if match:
                    metadata[self._normalize_text(match.group(1))] = self._normalize_text(match.group(2))
                    continue
                if cell in {"批号", "规格", "考察条件", "贮藏条件", "包装", "试验开始时间"} and idx + 1 < len(meta_row):
                    next_value = self._normalize_text(meta_row[idx + 1])
                    if next_value and next_value != cell:
                        metadata[cell] = next_value
        return metadata

    def _parse_stability_table(self, rows: List[List[str]], caption: str) -> Dict[str, Any]:
        header_info = self._find_stability_header(rows)
        header_start = int(header_info.get("start", 0) or 0)
        header_end = int(header_info.get("end", header_start + 1) or header_start + 1)
        headers = [self._normalize_text(value) for value in (header_info.get("headers", []) or [])]
        roles = [self._stability_header_role(value) for value in headers]
        metadata = self._extract_stability_metadata(rows, header_start)
        parse_warnings: List[str] = []
        if int(header_info.get("score", 0) or 0) < 12:
            parse_warnings.append("稳定性表格表头识别信心不足，请人工核对字段对应。")

        def role_index(role: str) -> Optional[int]:
            return roles.index(role) if role in roles else None

        indicator_idx = role_index("indicator")
        detail_idx = role_index("indicator_detail")
        limit_idx = role_index("limit")
        result_idx = role_index("result")
        time_idx = role_index("time_point")
        batch_idx = role_index("batch_no")
        orientation_idx = role_index("orientation")
        unit_idx = next((i for i,h in enumerate(headers) if h.strip() in ('单位', 'unit', 'Unit')), None)
        def header_unit(index):
            if index is None:
                return ''
            # 只提取明确的括号/单位声明；时间点及正倒置不是单位。
            matches = re.findall(r'[（(]([^()（）]+)[）)]|单位\s*[:：]\s*([^;；]+)', headers[index])
            values = [a or b for a,b in matches if (a or b) not in ('正置', '倒置')]
            return values[0].strip() if len(set(values)) == 1 else (' / '.join(values) if values else '')
        if indicator_idx is None:
            indicator_idx = detail_idx if detail_idx is not None else 0
        if detail_idx is None:
            indicator_indexes = [idx for idx, role in enumerate(roles) if role == "indicator"]
            detail_idx = indicator_indexes[1] if len(indicator_indexes) > 1 else indicator_idx
        if limit_idx is None:
            parse_warnings.append("未唯一识别“可接受标准/限度”列，限度判定将标记为待确认。")

        time_columns: List[tuple[int, str]] = [
            (idx, re.sub(r"^(?:检测结果|测定结果|实测值|结果)\s*/\s*", "", header).strip())
            for idx, header in enumerate(headers)
            if re.search(r"(?:\d+(?:\.\d+)?\s*(?:月|年|天|month)|起始|初始|效期末|正置|倒置)", header, re.I)
        ]
        # A merged top header such as "检测结果" is repeated over every
        # time-point column.  Multiple time-like headers therefore mean wide
        # layout even though each combined header also contains the word "结果".
        if len(time_columns) >= 2:
            result_idx = None
        if result_idx is None:
            fixed_indexes = {
                idx for idx in [indicator_idx, detail_idx, limit_idx, batch_idx, orientation_idx] if idx is not None
            }
            time_columns = [(idx, header) for idx, header in time_columns if idx not in fixed_indexes]
            if not time_columns:
                parse_warnings.append("未识别到稳定性检测结果列或时间点列。")

        batch_no = metadata.get("批号", "")
        specification = metadata.get("规格", "")
        storage_condition = metadata.get("考察条件", "") or metadata.get("贮藏条件", "")
        packaging = metadata.get("包装", "")
        start_date = metadata.get("试验开始时间", "")
        product_role = "参比制剂" if "参比" in str(caption or "") else "自制制剂"
        records: List[Dict[str, Any]] = []
        display_rows: List[List[str]] = []
        carried = {"indicator": "", "detail": "", "limit": "", "batch": batch_no}

        for source_row_index, source_row in enumerate(rows[header_end:], start=header_end):
            row = [self._normalize_text(value) for value in source_row]
            if not any(row):
                continue
            joined = " ".join(value for value in row if value)
            if joined.startswith(("注：", "注:", "备注：", "备注:")):
                continue
            display_rows.append(row[:])

            def cell(idx: Optional[int]) -> str:
                return row[idx] if idx is not None and idx < len(row) else ""

            indicator_group = cell(indicator_idx) or carried["indicator"]
            detail_value = cell(detail_idx) or carried["detail"] or indicator_group
            standard_text = cell(limit_idx) or carried["limit"]
            current_batch = cell(batch_idx) or carried["batch"] or batch_no
            if cell(indicator_idx):
                carried["indicator"] = cell(indicator_idx)
            if cell(detail_idx):
                carried["detail"] = cell(detail_idx)
            if cell(limit_idx):
                carried["limit"] = cell(limit_idx)
            if cell(batch_idx):
                carried["batch"] = cell(batch_idx)
            indicator = self._refine_stability_indicator(indicator_group, detail_value, standard_text)
            if not indicator:
                continue

            base_record = {
                "product_role": product_role,
                "batch_no": current_batch,
                "specification": specification,
                "storage_condition": storage_condition,
                "packaging": packaging,
                "start_date": start_date,
                "indicator_group": indicator_group,
                "indicator": indicator,
                "standard_text": standard_text,
                "source_caption": caption,
                "source_row_index": source_row_index,
                "unit": cell(unit_idx),
                "limit_column_unit": header_unit(limit_idx),
            }
            if result_idx is not None:
                time_point = cell(time_idx)
                orientation = cell(orientation_idx) or self._parse_orientation(time_point)
                records.append(
                    {
                        **base_record,
                        "time_point": time_point,
                        "month": self._parse_month_from_header(time_point),
                        "orientation": orientation,
                        "result_text": cell(result_idx), "source_column_index": result_idx,
                        "result_column_unit": header_unit(result_idx),
                    }
                )
            else:
                for result_column, time_header in time_columns:
                    records.append(
                        {
                            **base_record,
                            "time_point": time_header,
                            "month": self._parse_month_from_header(time_header),
                            "orientation": self._parse_orientation(time_header),
                            "result_text": cell(result_column), "source_column_index": result_column,
                            "result_column_unit": header_unit(result_column),
                        }
                    )

        return {
            "headers": headers,
            "header_rows": rows[header_start:header_end],
            "header_row_index": header_start,
            "rows": display_rows,
            "records": records,
            "metadata": metadata,
            "batch_no": batch_no,
            "specification": specification,
            "storage_condition": storage_condition,
            "packaging": packaging,
            "start_date": start_date,
            "product_role": product_role,
            "parse_warnings": list(dict.fromkeys(parse_warnings)),
            "coverage_month": max([item.get("month") or 0 for item in records if item.get("month") is not None], default=0),
        }

    @staticmethod
    def _refine_stability_indicator(indicator_group: str, indicator: str, standard_text: str) -> str:
        """将 Word 纵向合并单元格中的复合项目还原为独立指标。"""
        group = str(indicator_group or "").strip()
        base = str(indicator or group).strip()
        standard = str(standard_text or "").strip()
        if not base or not standard:
            return base
        if "微生物限度" in group or "微生物限度" in base:
            if "需氧菌总数" in standard:
                return "微生物限度-需氧菌总数"
            if "霉菌和酵母菌总数" in standard:
                return "微生物限度-霉菌和酵母菌总数"
            if "不得检出" in standard:
                return "微生物限度-控制菌"
        if "和" in group or "和" in base:
            match = re.match(r"^\s*([^:：]{1,30})[:：]", standard)
            if match:
                unit_match = re.search(r"([(（][^)）]+[)）])", group or base)
                return f"{match.group(1).strip()}{unit_match.group(1) if unit_match else ''}"
        return base

    @staticmethod
    def _parse_month_from_header(text: str) -> Optional[float]:
        value = str(text or "").strip()
        match = re.search(r"(\d+(?:\.\d+)?)\s*月", value)
        if match:
            return float(match.group(1))
        return None

    @staticmethod
    def _parse_orientation(text: str) -> str:
        value = str(text or "")
        if "倒置" in value:
            return "倒置"
        if "正置" in value:
            return "正置"
        return ""

    @staticmethod
    def _table_to_markdown(rows: List[List[str]]) -> str:
        if not rows:
            return ""
        width = max(len(row) for row in rows)
        normalized = [row + [""] * (width - len(row)) for row in rows]
        header = normalized[0]
        lines = [
            "| " + " | ".join(_FilingChangeDocxParser._escape_md(cell) for cell in header) + " |",
            "| " + " | ".join(["---"] * width) + " |",
        ]
        for row in normalized[1:]:
            lines.append("| " + " | ".join(_FilingChangeDocxParser._escape_md(cell) for cell in row) + " |")
        return "\n".join(lines)

    @staticmethod
    def _escape_md(text: Any) -> str:
        return str(text or "").replace("|", "\\|").strip()
