import html
import math
import re
import unicodedata
from agent.agent_backend.services.filing_numeric_revision import numeric_check_confirmed
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class FilingChangeStabilityService:
    """稳定性数据分析。

    分析边界是“结构化表格记录”：每份文档优先使用专用 Word 解析器生成的
    ``stability_records``，只在该文档没有结构化记录时才使用 Markdown 表格回退。
    叙述性正文不参与数值趋势计算。
    """

    _REFERENCE_ROLE_TOKENS = ("参比", "原研", "对照", "reference", "comparator")
    _SELF_ROLE_TOKENS = ("自制", "试制", "受试", "申报", "test product")
    _CHART_COLORS = ("#2563eb", "#dc2626", "#059669", "#9333ea", "#d97706", "#0891b2", "#4f46e5", "#be185d")
    _NUMBER_RE = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
    _UNIT_FACTORS = {
        "dimensionless": ("dimensionless", 1),
        "%": ("percent", 1.0),
        "mg/g": ("mass_ratio", 1e-3),
        "ug/g": ("mass_ratio", 1e-6),
        "g/kg": ("mass_ratio", 1e-3),
        "mg/kg": ("mass_ratio", 1e-6),
        "ug/kg": ("mass_ratio", 1e-9),
        "cfu/ml": ("microbial_volume", 1.0),
        "cfu/g": ("microbial_mass", 1.0),
        "g/l": ("mass_volume", 1),
        "mg/ml": ("mass_volume", 1),
        "ug/ml": ("mass_volume", "0.001"),
        "mg/l": ("mass_volume", "0.001"),
        "ug/l": ("mass_volume", "0.000001"),
    }
    _LIMIT_REASON_TEXT = {
        "numeric_uncertain": "OCR数值核验存在分歧或尚未完成，未参与自动限度和趋势判断",
        "unavailable_measurement": "结果或标准为未检测、待确认或未量化表达，需人工核对",
        "duplicate_component": "组成项名称重复，无法唯一对应",
        "missing_component_result": "缺少该组成项检测结果",
        "missing_component_limit": "缺少该组成项可接受标准",
        "malformed_component": "组成项表达不完整，需人工核对",
        "composite_components": "按组成项与对应限度逐项判断，保留复合原文",
        "missing_limit": "未提供可接受标准或限度",
        "missing_result": "未提供检测结果",
        "qualitative_failure": "定性结果明确不符合规定",
        "qualitative_match": "定性结果符合规定",
        "not_detected": "未检出，符合“不得检出”要求",
        "qualitative_result_requires_review": "定性结果与当前标准无法自动比较",
        "prohibited_detection": "检出了标准规定不得检出的项目",
        "non_scalar_or_qualified_result": "结果为复合值、修饰值或非单一数值，无法自动比较",
        "unrecognized_limit": "可接受标准含义不明确，无法结构化为上下限",
        "unit_mismatch": "检测结果与可接受标准的单位不一致且无法安全换算",
        "unknown_unit": "存在尚不支持的单位，需人工核对",
        "missing_unit": "一侧缺少单位且没有明确关联的单位来源，需人工核对",
        "unit_context_conflict": "表头或指标中的单位与结果或标准冲突，需人工核对",
        "numeric_within_limit": "数值位于可接受范围内",
        "numeric_out_of_limit": "数值明确超出可接受范围",
        "censored_result_overlaps_limit": "修饰结果的可能范围与标准边界重叠",
        "unrecognized_limit_or_result": "无法识别检测结果或可接受标准",
    }

    def run(self, application_form: Dict[str, Any], submissions: List[Dict[str, Any]], project_id: str = "") -> Dict[str, Any]:
        form_json = (application_form or {}).get("form_json", {}) if isinstance((application_form or {}).get("form_json", {}), dict) else {}
        validity_field = form_json.get("item_15_validity_period", {}) if isinstance(form_json, dict) else {}
        validity_sub = validity_field.get("sub_fields", {}) if isinstance(validity_field, dict) else {}
        proposed_text = str(validity_sub.get("proposed_validity_period", "") or "").strip()
        proposed_months = self._parse_month_value(proposed_text)

        collected_records: List[Dict[str, Any]] = []
        extracted_attachments: List[str] = []
        packaging_values: List[str] = []
        source_stats = {"structured_stability_table": 0, "markdown_table_fallback": 0}

        for submission in submissions or []:
            from agent.agent_backend.services.filing_change_extraction_service import is_submission
            if not isinstance(submission, dict) or not is_submission(submission) or not self._is_stability_submission(submission):
                continue
            extracted = submission.get("extracted_json", {}) if isinstance(submission.get("extracted_json", {}), dict) else {}
            doc_meta = {
                "source_doc_id": submission.get("doc_id", ""),
                "source_file_name": submission.get("file_name", ""),
                "available_status": (submission.get("latest_attempt") or {}).get("content_status") or submission.get("parse_status") or "unknown",
            }
            doc_direct_records = [item for item in (extracted.get("stability_records", []) or []) if isinstance(item, dict)]
            if doc_direct_records:
                for record in doc_direct_records:
                    if self._is_reference_record(record) and not record.get('drug_name'):
                        identities=[f for f in extracted.get('facts',[]) if f['field'] in ('reference_identity','drug_name') and f.get('status')=='available' and f.get('source',{}).get('batch_no')==record.get('batch_no') and (f['field']=='reference_identity' or f.get('source',{}).get('product_role')=='参比制剂')]
                        if len({f['normalized_value'] for f in identities})==1:record={**record,'drug_name':identities[0]['raw_value'],'identity_source':identities[0]['source']}
                    collected_records.append({**record, **doc_meta, "data_source_type": "structured_stability_table"})
                source_stats["structured_stability_table"] += len(doc_direct_records)
            else:
                doc_tables: List[Dict[str, Any]] = []
                for table in extracted.get("markdown_tables", []) or []:
                    if isinstance(table, dict):
                        doc_tables.append({**table, **doc_meta})
                fallback_records = self._build_stability_records(doc_tables)
                for record in fallback_records:
                    collected_records.append({**record, "data_source_type": "markdown_table_fallback"})
                source_stats["markdown_table_fallback"] += len(fallback_records)

            extracted_attachments.extend(
                str(item or "").strip()
                for item in (extracted.get("attachments", []) or [])
                if str(item or "").strip()
            )
            packaging_values.extend(
                str(item or "").strip()
                for item in (extracted.get("packaging_values", []) or [])
                if str(item or "").strip()
            )

        all_records = self._dedupe_records([self._enrich_record(record) for record in collected_records])
        reference_records = [item for item in all_records if self._is_reference_record(item)]
        # 专用解析器会明确标注参比/自制；旧版表格可能没有角色字段。分析时排除
        # 明确的参比记录，但保留未标注角色的待审样品，避免多文档时丢掉旧表格数据。
        records = [item for item in all_records if not self._is_reference_record(item)]
        unknown_records = [item for item in records if not self._is_explicit_self_record(item)]
        conflicts = self._record_conflicts(records)
        coverage = self._coverage(records, proposed_months, submissions)


        grouped = self._group_records_by_indicator(records)
        key_indicators = self._build_indicator_results(grouped)
        charts = self._build_charts(grouped, project_id=project_id)

        max_month = max([float(item.get("month", 0) or 0) for item in records], default=0.0)
        out_of_spec = [item for item in records if item.get("within_standard") is False]
        undecidable_records = [
            item
            for item in records
            if item.get("within_standard") is None
            and str(item.get("result_text", "") or "").strip().upper() not in {"", "/", "NA", "N/A"}
        ]
        limit_check = self._build_limit_check(records, out_of_spec, undecidable_records)
        missing_data: List[str] = list(coverage["missing"])
        risk_points: List[str] = []

        if not records:
            missing_data.append("未从申报资料的结构化稳定性表格中提取到可计算数据")
        if proposed_text and proposed_months is None:
            missing_data.append(f"无法识别拟延长后有效期：{proposed_text}")
        if proposed_months is not None and max_month < proposed_months:
            missing_data.append(f"自制制剂稳定性数据最大时间点为 {self._fmt_month(max_month)}，尚未覆盖拟延长后有效期 {self._fmt_month(proposed_months)}")
        if undecidable_records:
            missing_data.append(f"有 {len(undecidable_records)} 条表格记录因缺少可比较限度或结果为定性/复合文本，需人工确认")
        if any(value in {"", "/", "未提供"} for value in packaging_values):
            risk_points.append("稳定性样品包装信息缺失或仅以“/”表示，无法核验包装一致性")
        if out_of_spec:
            risk_points.extend(
                f"{item.get('indicator', '未知指标')} 在 {self._record_context(item)} 出现超出标准结果"
                for item in out_of_spec[:10]
            )
        for item in key_indicators:
            if str(item.get("risk_level", "")) in {"中", "高"}:
                risk_points.append(
                    f"{item.get('indicator', '未知指标')} 在全部自制批次中的综合趋势为 {item.get('trend', '待确认')}，"
                    f"范围 {item.get('value_range_display', '-')}，风险等级 {item.get('risk_level', '')}"
                )

        only_qualitative_uncertain = bool(records) and not out_of_spec and not (
            proposed_months is not None and max_month < proposed_months
        ) and bool(undecidable_records)

        if not records:
            result = "需补充资料"
            summary = "未提取到结构化稳定性表格数据，当前无法形成有效趋势分析。"
        elif out_of_spec:
            result = "不支持延长"
            summary = "全部自制制剂批次的稳定性数据中已识别出超出标准的结果，当前不支持直接延长药品有效期。"
        elif only_qualitative_uncertain:
            result = "需人工确认"
            summary = "已按批号和放置方向汇总全部自制制剂数据，数值项与定性/复合文本项已分开；后者需审评员对照原表确认。"
        elif missing_data:
            result = "需补充资料"
            summary = "已按全部自制制剂批次提取稳定性数据，但时间点覆盖或关键指标信息仍不足，需要补充资料后复核。"
        else:
            result = "支持延长"
            summary = "已综合全部自制制剂批次，并按批号、正置/倒置分序列分析；时间点覆盖拟延长后有效期，未发现明确超标项。"

        significance = self._significant_assessment(records, submissions)
        if result == '支持延长' and (not coverage['requirements_known'] or significance['status'] != '通过' or conflicts or unknown_records):
            result = '需人工确认'
            summary = '当前已判定限度未超限，但方案覆盖、显著变化依据或记录对应仍需核验。'
        self_batches = sorted({str(item.get("batch_no", "") or "").strip() for item in records if str(item.get("batch_no", "") or "").strip()})
        reference_batches = sorted({str(item.get("batch_no", "") or "").strip() for item in reference_records if str(item.get("batch_no", "") or "").strip()})
        return {
            "result": result,
            "summary": summary,
            "limit_check": limit_check,
            "key_indicators": key_indicators,
            "missing_data": missing_data,
            "risk_points": list(dict.fromkeys(risk_points)),
            "charts": charts,
            # 技术审评规则会继续使用这些记录核对批次与超标项，不能在此截断后再分析。
            "records": records,
            "reference_records": reference_records,
            "record_count": len(records),
            "reference_record_count": len(reference_records),
            "coverage_month": max_month,
            "coverage": coverage, "record_conflicts": conflicts, "unknown_role_records": unknown_records,
            "significant_change_assessment": significance,
            "proposed_validity_period": proposed_text,
            "attachments": list(dict.fromkeys(extracted_attachments)),
            "data_scope": {
                "analysis_population": "自制及角色待核验样品" if unknown_records else "全部自制制剂批次",
                "self_batches": self_batches,
                "self_batch_count": len(self_batches),
                "reference_batches": reference_batches,
                "reference_batch_count": len(reference_batches),
                "narrative_text_used_for_numeric_analysis": False,
                "source_record_counts_before_deduplication": source_stats,
                "deduplicated_record_count": len(all_records),
            },
        }

    @staticmethod
    def _fmt_month(value: Any) -> str:
        try:
            month = float(value or 0)
        except Exception:
            month = 0.0
        return f"{int(month)}个月" if month.is_integer() else f"{month:.1f}个月"

    @staticmethod
    def _fmt_value(value: Any) -> str:
        try:
            return f"{float(value):.12g}"
        except Exception:
            return str(value or "").strip()

    @staticmethod
    def _parse_month_value(text: str) -> Optional[float]:
        value = str(text or "").strip().lower()
        if not value:
            return None
        match = re.search(r"(\d+(?:\.\d+)?)", value)
        if not match:
            return None
        number = float(match.group(1))
        if "year" in value or "年" in value:
            return number * 12.0
        if "week" in value or "周" in value:
            return number / 4.0
        return number

    @staticmethod
    def _normalize_header(text: str) -> str:
        value = str(text or "").strip().lower()
        if any(token in value for token in ["时间", "time", "月份", "month", "考察周期", "留样"]):
            return "time_point"
        if any(token in value for token in ["项目", "指标", "检验项", "考察项", "test item"]):
            return "indicator"
        if any(token in value for token in ["结果", "测定值", "实测", "检测值", "含量", "result", "%"]):
            return "result"
        if any(token in value for token in ["限度", "标准", "规定", "specification", "acceptance"]):
            return "limit"
        if any(token in value for token in ["批号", "batch"]):
            return "batch_no"
        if any(token in value for token in ["条件", "贮藏", "storage"]):
            return "condition"
        return value or "unknown"

    @staticmethod
    def _is_stability_submission(submission: Dict[str, Any]) -> bool:
        quality_tags = submission.get("quality_tags", []) or []
        if any("稳定性研究资料" in str(tag or "") for tag in quality_tags):
            return True
        file_name = str(submission.get("file_name", "") or "")
        if "稳定性" in file_name:
            return True
        extracted = submission.get("extracted_json", {}) if isinstance(submission.get("extracted_json", {}), dict) else {}
        return bool(extracted.get("stability_records")) or "稳定性" in str(extracted.get("raw_text_preview", "") or "")

    @staticmethod
    def _normalize_comparison_text(text: Any) -> str:
        return (
            unicodedata.normalize("NFKC", str(text or ""))
            .strip()
            .replace("－", "-")
            .replace("％", "%")
            .replace("≦", "≤")
            .replace("≧", "≥")
            .replace("=<", "≤")
            .replace("=>", "≥")
            .replace("<=", "≤")
            .replace(">=", "≥")
            .replace("μ", "u")
            .replace("µ", "u")
        )

    @classmethod
    def _extract_numeric(cls, text: str) -> Optional[float]:
        """只接受精确的单一标量；修饰值和复合值不进入趋势曲线。"""
        value = cls._normalize_comparison_text(text)
        if not value or value.upper() in {"/", "NA", "N/A", "ND", "N.D."}:
            return None
        if value.replace(" ", "").startswith(("<", ">", "≤", "≥", "约")):
            return None
        if len(re.findall(cls._NUMBER_RE, value)) != 1:
            return None
        match = re.fullmatch(
            rf"\s*({cls._NUMBER_RE})\s*(?:%|[A-Za-zμµ一-龥]+(?:/[A-Za-zμµ一-龥]+)?)?\s*",
            value,
            flags=re.IGNORECASE,
        )
        if not match:
            return None
        try:
            return float(match.group(1))
        except (TypeError, ValueError):
            return None

    @classmethod
    def _first_numeric(cls, text: str) -> Optional[float]:
        match = re.search(cls._NUMBER_RE, cls._normalize_comparison_text(text))
        if not match:
            return None
        try:
            return float(match.group(0))
        except (TypeError, ValueError):
            return None

    @classmethod
    def _extract_unit(cls, text: Any) -> str:
        value = cls._normalize_comparison_text(text).lower().replace(" ", "")
        match = re.search(rf"(?:{cls._NUMBER_RE})(%|[a-z一-龥]+(?:/[a-z一-龥]+)?)$", value)
        return cls._normalize_unit(match[1]) if match else ""

    @classmethod
    def _normalize_unit(cls, text: str) -> str:
        value = cls._normalize_comparison_text(text).lower().replace(" ", "")
        return 'dimensionless' if value in ('无单位', '无量纲', 'dimensionless') else value.replace("菌落形成单位", "cfu")

    @classmethod
    def _parse_quantity(cls, text: str):
        """完整读取单值或单侧修饰值；未知单位保留，不能当作无单位。"""
        value = cls._normalize_comparison_text(text).strip()
        match = re.fullmatch(rf"([<>≤≥]?)\s*({cls._NUMBER_RE})\s*(%|[A-Za-z一-龥]+(?:\s*/\s*[A-Za-z一-龥]+)?)?", value)
        if not match:
            return None
        number = Decimal(match[2])
        op = match[1]
        return {
            "low": None if op in ("<", "≤") else number,
            "high": None if op in (">", "≥") else number,
            "low_inclusive": op not in ("<", ">"),
            "high_inclusive": op not in ("<", ">"),
            "unit": cls._normalize_unit(match[3] or ""),
        }

    @classmethod
    def _parse_limit_bounds(cls, text: str):
        value = cls._normalize_comparison_text(text).strip()
        # 仅兼容已有明确的指标前缀，不能从任意描述中搜一个数字作为标准。
        value = re.sub(r"^(需氧菌总数|霉菌和酵母菌总数|霉菌及酵母菌总数)\s*[:：]?\s*", "", value)
        translations = {"不得大于": "≤", "不大于": "≤", "不得过": "≤", "不超过": "≤",
                        "不得小于": "≥", "不小于": "≥", "不低于": "≥", "不少于": "≥", "不得少于": "≥",
                        "小于": "<", "大于": ">"}
        for label, operator in translations.items():
            if value.startswith(label):
                value = operator + value[len(label):].strip()
                break
        number = cls._NUMBER_RE
        unit = r"(%|[A-Za-z一-龥]+(?:/[A-Za-z一-龥]+)?)?"
        match = re.fullmatch(rf"\s*({number})\s*{unit}\s*[-~～—–至]\s*({number})\s*{unit}\s*", value)
        if match:
            low, high = Decimal(match[1]), Decimal(match[3])
            left, right = cls._normalize_unit(match[2] or ""), cls._normalize_unit(match[4] or "")
            if low > high or (left and right and left != right):
                return None
            return {"low": low, "high": high, "low_inclusive": True, "high_inclusive": True, "unit": left or right}
        return cls._parse_quantity(value)

    @classmethod
    def _numeric_interval(cls, result_text: str) -> Optional[Dict[str, Any]]:
        return cls._parse_quantity(result_text)

    @classmethod
    def _convert_decision_units(
        cls,
        interval: Dict[str, Any],
        lower: Optional[float],
        upper: Optional[float],
        result_unit: str,
        limit_unit: str,
    ) -> Optional[Tuple[Dict[str, Any], Optional[float], Optional[float]]]:
        if result_unit == limit_unit and (not result_unit or result_unit in cls._UNIT_FACTORS):
            return interval, lower, upper
        result_definition = cls._UNIT_FACTORS.get(result_unit)
        limit_definition = cls._UNIT_FACTORS.get(limit_unit)
        if not result_definition or not limit_definition or result_definition[0] != limit_definition[0]:
            return None
        converted = dict(interval)
        if converted.get("low") is not None:
            converted["low"] = Decimal(str(converted["low"])) * Decimal(str(result_definition[1]))
        if converted.get("high") is not None:
            converted["high"] = Decimal(str(converted["high"])) * Decimal(str(result_definition[1]))
        return (
            converted,
            Decimal(str(lower)) * Decimal(str(limit_definition[1])) if lower is not None else None,
            Decimal(str(upper)) * Decimal(str(limit_definition[1])) if upper is not None else None,
        )

    def _evaluate_limit(self, result_text: str, limit_text: str, unit_context: str = "", unit_source: str = "", column_units=None) -> Dict[str, Any]:
        result_value = self._normalize_comparison_text(result_text)
        limit_value = self._normalize_comparison_text(limit_text)
        result_unit = self._extract_unit(result_value)
        limit_unit = self._extract_unit(limit_value)
        decision: Dict[str, Any] = {
            "within_standard": None,
            "reason": "unrecognized_limit_or_result",
            "result_unit": result_unit,
            "limit_unit": limit_unit,
        }
        if not limit_value:
            decision["reason"] = "missing_limit"
            return decision
        if not result_value or result_value.upper() in {"/", "NA", "N/A"}:
            decision["reason"] = "missing_result"
            return decision

        compact_result = result_value.replace(" ", "")
        compact_limit = limit_value.replace(" ", "")
        unavailable = {"待确认", "待核查", "未检测", "未测定", "待检", "未检", "未知", "无数据", "无结果", "暂无", "-", "--", "<LOQ", "≤LOQ", "LOQ", "N/A", "NA"}
        if compact_result.upper() in unavailable or compact_limit.upper() in unavailable:
            decision["reason"] = "unavailable_measurement"
            return decision
        qualitative_limits = {"符合规定", "符合要求", "应符合规定", "应符合要求", "应合格", "合格"}
        if compact_result in {"不符合规定", "不符合要求", "不合格"}:
            if compact_limit in qualitative_limits:
                decision.update(within_standard=False, reason="qualitative_failure")
                return decision
        if compact_result in {"符合规定", "符合要求", "合格"}:
            if compact_limit in qualitative_limits:
                decision.update(within_standard=True, reason="qualitative_match")
                return decision
        if compact_result.upper() in {"ND", "N.D.", "未检出"}:
            if compact_limit == "不得检出":
                decision.update(within_standard=True, reason="not_detected")
            else:
                decision["reason"] = "qualitative_result_requires_review"
            return decision
        if compact_result == "检出" and compact_limit == "不得检出":
            decision.update(within_standard=False, reason="prohibited_detection")
            return decision

        # 只支持明确性状表达；任意相同自由文本不是符合标准的证据。
        appearance = r'(无色|白色|类白色|淡黄色|黄色)(澄清液体|澄明液体|液体|粉末|固体|结晶性粉末)'
        if compact_result == compact_limit and re.fullmatch(appearance, compact_result):
            decision.update(within_standard=True, reason='qualitative_match')
            return decision
        qualitative_range=re.fullmatch(r'(.+?)至(.+?)(澄清液体|澄明液体|液体|粉末|固体)',compact_limit)
        if qualitative_range and compact_result in {qualitative_range[1]+qualitative_range[3],qualitative_range[2]+qualitative_range[3]}:
            decision.update(within_standard=True, reason='qualitative_match')
            return decision
        if re.search(r'[:：]', result_value) and re.search(r'[:：]', limit_value):
            def parts(text):
                grouped, malformed = {}, []
                for raw in re.split('[;；]', text):
                    match = re.fullmatch(r'([^:：]+)[:：]([^:：]+)', raw.strip())
                    if not match or not match[1].strip() or not match[2].strip():
                        malformed.append(raw)
                    else:
                        grouped.setdefault(match[1].strip(), []).append(match[2].strip())
                return grouped, malformed
            (results, bad_results), (limits, bad_limits) = parts(result_value), parts(limit_value)
            components = []
            for name in dict.fromkeys([*results, *limits]):
                values, standards = results.get(name, []), limits.get(name, [])
                component = {'item': name, 'result': ';'.join(values), 'limit': ';'.join(standards),
                             'result_candidates': values, 'limit_candidates': standards}
                if len(values) == len(standards) == 1:
                    component.update(self._evaluate_limit(values[0], standards[0], unit_context, unit_source, column_units))
                else:
                    reason = 'duplicate_component' if len(values) > 1 or len(standards) > 1 else 'missing_component_result' if not values else 'missing_component_limit'
                    component.update(within_standard=None, reason=reason)
                components.append(component)
            for side, fragments in [('result', bad_results), ('limit', bad_limits)]:
                for fragment in fragments:
                    components.append({'item': '', side: fragment, 'within_standard': None, 'reason': 'malformed_component'})
            flags = [item['within_standard'] for item in components]
            decision.update(within_standard=False if False in flags else True if flags and all(flag is True for flag in flags) else None,
                            reason='composite_components', components=components)
            return decision
        interval = self._numeric_interval(result_value)
        if interval is None:
            decision["reason"] = "non_scalar_or_qualified_result"
            return decision

        bounds = self._parse_limit_bounds(limit_value)
        if bounds is None:
            decision["reason"] = "unrecognized_limit"
            return decision
        lower, upper = bounds["low"], bounds["high"]
        lower_inclusive, upper_inclusive = bounds["low_inclusive"], bounds["high_inclusive"]
        result_unit, limit_unit = interval["unit"], bounds["unit"]
        context = self._normalize_unit(unit_context)
        column_units = column_units or {}
        result_context = self._normalize_unit(column_units.get('result', '') or context)
        limit_context = self._normalize_unit(column_units.get('limit', '') or context)
        decision.update(result_unit=result_unit, limit_unit=limit_unit,
                        result_unit_source="result" if result_unit else "",
                        limit_unit_source="standard" if limit_unit else "")
        if any(unit and unit not in self._UNIT_FACTORS for unit in (result_unit, limit_unit, context, result_context, limit_context)):
            decision["reason"] = "unknown_unit"
            decision["unit_status"] = "unknown"
            return decision
        for name, explicit, inherited in [('result', result_unit, result_context), ('limit', limit_unit, limit_context)]:
            # 列单位仅属于对应一侧，不能跨列借用；指标/单位列才可共同继承。
            if inherited and any(unit and self._UNIT_FACTORS[unit][0] != self._UNIT_FACTORS[inherited][0]
                                 for unit in (explicit, context)):
                decision["reason"] = "unit_context_conflict"
                return decision
            if inherited and not explicit:
                decision[name + '_unit'] = inherited
                decision[name + '_unit_source'] = name + '_column_header' if column_units.get(name) else unit_source
        result_unit, limit_unit = decision['result_unit'], decision['limit_unit']
        if bool(result_unit) != bool(limit_unit):
            decision["reason"] = "missing_unit"
            decision["unit_status"] = "missing"
            return decision
        decision['unit_status'] = 'dimensionless' if result_unit == limit_unit == 'dimensionless' else 'explicit' if result_unit and limit_unit else 'unmarked'
        decision["unit_note"] = "" if result_unit else "双方未标注单位，按同一指标原始数值比较；未进行单位换算"
        converted = self._convert_decision_units(interval, lower, upper, result_unit, limit_unit)
        if converted is None:
            decision["reason"] = "unit_mismatch"
            return decision
        interval, lower, upper = converted
        result_low = interval.get("low")
        result_high = interval.get("high")
        decision.update(near_limit=False, near_limit_applicable=False)
        within_lower = lower is None or (
            result_low is not None
            and (result_low > lower or (result_low == lower and (lower_inclusive or not interval["low_inclusive"])))
        )
        within_upper = upper is None or (
            result_high is not None
            and (result_high < upper or (result_high == upper and (upper_inclusive or not interval["high_inclusive"])))
        )
        if within_lower and within_upper:
            decision.update(within_standard=True, reason="numeric_within_limit")
            exact = result_low is not None and result_low == result_high
            if exact and lower is None and upper is not None and upper > 0:
                decision.update(near_limit_applicable=True, near_limit=result_low >= upper * Decimal("0.85"))
            elif exact and upper is None and lower is not None and lower > 0:
                decision.update(near_limit_applicable=True, near_limit=result_low <= lower * Decimal("1.15"))
            return decision

        entirely_below = lower is not None and result_high is not None and (
            result_high < lower or (result_high == lower and not (interval["high_inclusive"] and lower_inclusive))
        )
        entirely_above = upper is not None and result_low is not None and (
            result_low > upper or (result_low == upper and not (interval["low_inclusive"] and upper_inclusive))
        )
        exact_result = result_low is not None and result_high is not None and result_low == result_high
        if entirely_below or entirely_above or exact_result:
            decision.update(within_standard=False, reason="numeric_out_of_limit")
            return decision
        decision["reason"] = "censored_result_overlaps_limit"
        return decision

    def _judge_within_standard(self, result_text: str, limit_text: str) -> Optional[bool]:
        return self._evaluate_limit(result_text, limit_text).get("within_standard")

    @staticmethod
    def _normalize_orientation(record: Dict[str, Any]) -> str:
        orientation = str(record.get("orientation", "") or "").strip()
        if orientation:
            return orientation
        time_text = str(record.get("time_point", "") or "")
        if "倒置" in time_text:
            return "倒置"
        if "正置" in time_text:
            return "正置"
        return ""

    def _enrich_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        item = dict(record or {})
        time_text = str(item.get("time_point", "") or "").strip()
        month = item.get("month")
        if month is None or str(month).strip() == "":
            month = self._parse_month_value(time_text)
        item["month"] = float(month) if month is not None else None
        item["time_point"] = time_text or (self._fmt_month(item["month"]) if month is not None else "未知时间点")
        item["orientation"] = self._normalize_orientation(item)
        item["product_role"] = str(item.get("product_role", "") or "").strip()
        item["batch_no"] = str(item.get("batch_no", "") or "").strip()
        source_indicator = str(item.get("indicator", "") or "").strip()
        item["source_indicator"] = source_indicator
        item["indicator"] = self._refine_indicator_label(
            str(item.get("indicator_group", "") or "").strip(),
            source_indicator,
            str(item.get("standard_text", item.get("limit_text", "")) or "").strip(),
        )
        item["condition"] = item.get("condition") or item.get("storage_condition") or ""
        item["unknown_dimensions"] = [key for key in ("product_role","batch_no","condition","specification","packaging") if not item.get(key) or item.get(key)=="/"]
        item["result_text"] = str(item.get("result_text", "") or "").strip()
        item["result_num"] = self._extract_numeric(item["result_text"])
        raw = item['result_text']
        item['result_kind'] = ('exact' if item['result_num'] is not None else 'not_detected' if raw.upper() in ('ND','未检出') else 'boundary' if re.match(r'^[<>≤≥]', raw) else 'composite' if re.search(r'[;；]',raw) else 'qualitative')
        item['components'] = [x.strip() for x in re.split(r'[;；]',raw) if x.strip()] if item['result_kind']=='composite' else []
        unit_context = str(item.get("unit", "") or "").strip()
        unit_source = "record.unit" if unit_context else ""
        indicator_units = re.findall(r"\(([^()]+)\)", self._normalize_comparison_text(source_indicator))
        indicator_units = [self._normalize_unit(v) for v in indicator_units
                           if self._normalize_unit(v) in self._UNIT_FACTORS or "/" in v]
        if not unit_context and len(set(indicator_units)) == 1:
            unit_context, unit_source = indicator_units[0], "indicator"
        limit_decision = self._evaluate_limit(
            item["result_text"],
            str(item.get("standard_text", item.get("limit_text", "")) or ""),
            unit_context, unit_source,
            {'result': item.get('result_column_unit', ''), 'limit': item.get('limit_column_unit', '')},
        )
        if len(set(indicator_units)) > 1 or (unit_context and indicator_units and
                any(u != self._normalize_unit(unit_context) for u in indicator_units)):
            limit_decision.update(within_standard=None, reason="unit_context_conflict", near_limit=False)
        if item.get('numeric_status') == 'numeric_uncertain' or any(
                isinstance(v, dict) and not numeric_check_confirmed(v) for v in item.get('numeric_verification', [])):
            item['result_num'] = None
            item['result_kind'] = 'numeric_uncertain'
            limit_decision.update(within_standard=None, reason='numeric_uncertain', near_limit=False,
                                  near_limit_applicable=False, components=[])
        if limit_decision.get("components"):
            item["components"] = limit_decision["components"]
        item["within_standard"] = limit_decision.get("within_standard")
        item["limit_reason"] = str(limit_decision.get("reason", "") or "")
        item["result_unit"] = str(limit_decision.get("result_unit", "") or "")
        item["limit_unit"] = str(limit_decision.get("limit_unit", "") or "")
        for key in ("unit_note", "unit_status", "result_unit_source", "limit_unit_source"):
            item[key] = limit_decision.get(key, "")
        item["near_limit"] = bool(limit_decision.get("near_limit", False))
        item["near_limit_applicable"] = bool(limit_decision.get("near_limit_applicable", False))
        item["limit_text"] = str(item.get("limit_text", item.get("standard_text", "")) or "").strip()
        item['source_position'] = {'doc_id':item.get('source_doc_id'), 'file_name':item.get('source_file_name'),
                                   'page':item.get('source_page'), 'table':item.get('source_caption') or item.get('source_table'),
                                   'row':item['source_row_index']+1 if isinstance(item.get('source_row_index'),int) else None,
                                   'cell':item['source_column_index']+1 if isinstance(item.get('source_column_index'),int) else None}
        item["data_source_type"] = str(item.get("data_source_type", "") or "").strip() or "structured_stability_table"
        return item

    @staticmethod
    def _refine_indicator_label(indicator_group: str, indicator: str, standard_text: str) -> str:
        """兼容已入库的旧解析结果：在分析层再次校正合并单元格子指标。"""
        group = str(indicator_group or "").strip()
        base = str(indicator or group).strip()
        standard = str(standard_text or "").strip()
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

    @classmethod
    def _is_reference_record(cls, item: Dict[str, Any]) -> bool:
        role = str(item.get("product_role", "") or "").strip().lower()
        return any(token in role for token in cls._REFERENCE_ROLE_TOKENS)

    @classmethod
    def _is_explicit_self_record(cls, item: Dict[str, Any]) -> bool:
        role = str(item.get("product_role", "") or "").strip().lower()
        return any(token in role for token in cls._SELF_ROLE_TOKENS)

    @staticmethod
    def _dedupe_records(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        deduped: List[Dict[str, Any]] = []
        seen = set()
        for item in records or []:
            if not isinstance(item, dict) or not str(item.get("indicator", "") or "").strip():
                continue
            key = (
                str(item.get("source_doc_id", "") or "").strip(),
                str(item.get("condition") or item.get("storage_condition") or ""),
                str(item.get("specification") or ""), str(item.get("packaging") or ""),
                str(item.get("product_role", "") or "").strip(),
                str(item.get("batch_no", "") or "").strip(),
                str(item.get("month")), str(item.get("time_point", "")),
                str(item.get("orientation", "") or "").strip(),
                str(item.get("indicator", "") or "").strip(),
                str(item.get("result_text", "") or "").strip(),
                str(item.get("limit_text", "") or "").strip(),
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    def _build_stability_records(self, tables: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        from agent.agent_backend.services.filing_change_extraction_service import classify_table
        for table in tables or []:
            if classify_table([table.get('headers', [])] + table.get('rows', []), table.get('section_title', '')) != 'stability_result':
                continue
            headers = [self._normalize_header(x) for x in (table.get("headers", []) or [])]
            rows = table.get("rows", []) or []
            if not headers or not rows:
                continue
            if "time_point" in headers and "indicator" in headers and "result" in headers:
                records.extend(self._build_long_table_records(table, headers, rows))
            elif "time_point" in headers:
                records.extend(self._build_wide_table_records(table, headers, rows))
        return records

    @staticmethod
    def _table_numeric_evidence(table, positions):
        evidence = []
        for cell in (table.get('source_table') or table).get('cells', []):
            if any(cell.get('row', -1) <= r < cell.get('row', -1)+cell.get('rowspan', 1)
                   and cell.get('column', -1) <= c < cell.get('column', -1)+cell.get('colspan', 1)
                   for r, c in positions if c >= 0):
                evidence.extend(cell.get('numeric_verification') or [])
        return {'numeric_verification': evidence,
                'numeric_status': 'numeric_uncertain' if any(not numeric_check_confirmed(v) for v in evidence)
                else 'manual_confirmed' if any(v.get('status') == 'manual_confirmed' for v in evidence)
                else 'verified'} if evidence else {}

    def _build_long_table_records(self, table: Dict[str, Any], headers: List[str], rows: List[List[str]]) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        time_idx = headers.index("time_point")
        indicator_idx = headers.index("indicator")
        result_idx = headers.index("result")
        limit_idx = headers.index("limit") if "limit" in headers else -1
        batch_idx = headers.index("batch_no") if "batch_no" in headers else -1
        condition_idx = headers.index("condition") if "condition" in headers else -1
        for row_index, row in enumerate(rows, 1):
            time_text = row[time_idx] if time_idx < len(row) else ""
            indicator = row[indicator_idx] if indicator_idx < len(row) else ""
            month = self._parse_month_value(time_text)
            if month is None or not str(indicator or "").strip():
                continue
            records.append(
                {
                    "source_doc_id": table.get("source_doc_id", ""),
                    "source_file_name": table.get("source_file_name", ""),
                    "source_caption": table.get("section_title", ""),
                    "source_row_index": row_index if 'row_index' in locals() else None,
                    "source_page": table.get("page"), "source_table": table.get("table_index"),
                    "specification": table.get("specification", ""), "packaging": table.get("packaging", ""),
                    "section_title": table.get("section_title", ""),
                    "product_role": table.get("product_role", ""),
                    "batch_no": row[batch_idx] if batch_idx >= 0 and batch_idx < len(row) else table.get("batch_no", ""),
                    "condition": row[condition_idx] if condition_idx >= 0 and condition_idx < len(row) else table.get("condition", ""),
                    "time_point": time_text,
                    "month": month,
                    "orientation": self._normalize_orientation({"time_point": time_text}),
                    "indicator": str(indicator).strip(),
                    "result_text": str(row[result_idx] if result_idx < len(row) else "").strip(),
                    "limit_text": str(row[limit_idx] if limit_idx >= 0 and limit_idx < len(row) else "").strip(),
                    **self._table_numeric_evidence(table, [(r, c) for r in (0, row_index)
                        for c in (time_idx, indicator_idx, result_idx, limit_idx, batch_idx, condition_idx)]),
                }
            )
        return records

    def _build_wide_table_records(self, table: Dict[str, Any], headers: List[str], rows: List[List[str]]) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        time_idx = headers.index("time_point")
        limit_row: Dict[int, str] = {}
        limit_row_index = None
        for row_index, row in enumerate(rows, 1):
            first_cell = str(row[time_idx] if time_idx < len(row) else "").strip()
            if any(token in first_cell for token in ["限度", "标准", "规定"]):
                limit_row_index = row_index
                for idx, cell in enumerate(row):
                    if idx != time_idx:
                        limit_row[idx] = str(cell).strip()
                continue
            month = self._parse_month_value(first_cell)
            if month is None:
                continue
            for idx, header in enumerate(table.get("headers", []) or []):
                if idx == time_idx or str(header).strip() in ("批号","条件","规格","包装","样品角色","放置方向"):
                    continue
                indicator = str(header or "").strip()
                result_text = row[idx] if idx < len(row) else ""
                if not indicator or not str(result_text or "").strip():
                    continue
                records.append(
                    {
                        "source_doc_id": table.get("source_doc_id", ""),
                        "source_file_name": table.get("source_file_name", ""),
                        "source_caption": table.get("section_title", ""),
                    "source_row_index": row_index if 'row_index' in locals() else None,
                    "source_page": table.get("page"), "source_table": table.get("table_index"),
                    "specification": table.get("specification", ""), "packaging": table.get("packaging", ""),
                    "section_title": table.get("section_title", ""),
                        "product_role": table.get("product_role", ""),
                        "batch_no": table.get("batch_no", ""),
                        "condition": table.get("condition", ""),
                        "time_point": first_cell,
                        "month": month,
                        "orientation": self._normalize_orientation({"time_point": first_cell}),
                        "indicator": indicator,
                        "result_text": str(result_text).strip(),
                        "limit_text": limit_row.get(idx, ""),
                        **self._table_numeric_evidence(table, [(0, idx), (row_index, idx), (row_index, time_idx)]
                            + ([(limit_row_index, idx)] if limit_row_index is not None else [])),
                    }
                )
        return records

    @staticmethod
    def _group_records_by_indicator(records: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for item in records or []:
            indicator = str(item.get("indicator", "") or "").strip()
            if indicator:
                grouped.setdefault(indicator, []).append(item)
        separated = {}
        for indicator, rows in grouped.items():
            units = {row.get('result_unit') or row.get('limit_unit') or '' for row in rows}
            if len(units) > 1:
                for row in rows:
                    unit = row.get('result_unit') or row.get('limit_unit') or '单位未知'
                    separated.setdefault(f'{indicator} [{unit}]', []).append(row)
            else:
                separated[indicator] = rows
        grouped = separated
        for indicator, rows in grouped.items():
            grouped[indicator] = sorted(
                rows,
                key=lambda row: (
                    str(row.get("batch_no", "") or ""),
                    str(row.get("orientation", "") or ""),
                    float(row.get("month", 0) or 0),
                ),
            )
        return grouped

    @staticmethod
    def _group_key(row):
        dims = tuple(str(row.get(k) or '') for k in ('product_role','batch_no','condition','specification','packaging'))
        return dims + ((str(row.get('source_doc_id') or ''),) if any(v in ('','/') for v in dims) else ('',))

    def _build_series_groups(self, rows):
        grouped = {}
        for row in rows:
            grouped.setdefault(self._group_key(row) + (row.get('result_unit') or row.get('limit_unit') or '',), []).append(row)
        result=[]
        for key, values in grouped.items():
            for group in self._build_series_groups_legacy(values):
                group['series_key'] = '|'.join(key) + '|' + group['series_key']
                group['label'] += ' / ' + (key[0] or "角色未知") + ' / ' + ' / '.join(v or '未知' for v in key[2:5])
                result.append(group)
        return result

    def _record_conflicts(self, records):
        groups={}
        for row in records:
            key=self._group_key(row)+(row.get('orientation',''),str(row.get('month')),row.get('indicator',''))
            groups.setdefault(key,[]).append(row)
        return [{'reason':'同一检测维度结果或限度冲突','records':rows} for rows in groups.values() if len({(r.get('result_text'),r.get('limit_text')) for r in rows})>1]

    def _coverage(self, records, target, submissions):
        groups={}; details=[]; missing=[]
        plans=[p for s in submissions if isinstance(s,dict) for p in (s.get('extracted_json') or {}).get('study_plans',[])]
        for row in records:
            if self._is_reference_record(row): continue
            key=self._group_key(row)+(row.get('orientation',''),)
            groups.setdefault(key,[]).append(row)
        for key,rows in groups.items():
            months=sorted({r['month'] for r in rows if r.get('month') is not None})
            # 没有方向的共同0月不是一组末点；它只属于同一完整维度的各方向。
            if key[-1]=='' and months==[0] and any(k[:-1]==key[:-1] and k[-1] for k in groups): continue
            label=' / '.join(v or '未知' for v in key[:5])+' / '+(key[-1] or '方向未知')
            gaps=[]
            directions={k[-1] for k in groups if k[:-1]==key[:-1] and k[-1]}
            direction_required=any(p.get('orientation')==key[-1] and all(str(p.get(k) or '')==str(rows[0].get(k) or '') for k in ('product_role','batch_no','condition','specification','packaging')) and target in p.get('time_points',[]) for p in plans)
            if target is not None and target not in months and (len(directions)<=1 or direction_required):
                gaps.append(f'{label}：缺少 {self._fmt_month(target)} 末点')
            # 同一结果矩阵中有明确限度、同时间点的空白才报告具体项目缺口。
            for row in rows:
                if not str(row.get('result_text') or '').strip() or row.get('result_text')=='/':
                    gaps.append(f"{label}：{row.get('time_point')} 缺少 {row.get('indicator')} 结果")
            missing.extend(gaps)
            details.append({'group':label,'dimensions':dict(zip(('product_role','batch_no','condition','specification','packaging'),key[:5])),'orientation':key[-1],'months':months,'missing':gaps,'sources':[{'doc_id':r.get('source_doc_id'),'table':r.get('source_caption'),'row':r.get('source_row_index')} for r in rows]})
        plans=[p for s in submissions if isinstance(s,dict) for p in (s.get('extracted_json') or {}).get('study_plans',[])]
        complete_plans=[p for p in plans if p.get('product_role') not in ('参比制剂','参比') and all(p.get(k) not in ('',None,'/',[]) for k in ('product_role','batch_no','condition','specification','packaging','time_points','indicators','standard'))]
        for plan in complete_plans:
            matching=[r for r in records if all(str(r.get(k) or '')==str(plan[k]) for k in ('product_role','batch_no','condition','specification','packaging')) and (not plan.get('orientation') or r.get('orientation')==plan['orientation'] or (r.get('month')==0 and not r.get('orientation')))]
            for month in plan['time_points']:
                if target is not None and month>target: continue
                for indicator in plan['indicators']:
                    restricted=next((v.get('time_points') for v in plan.get('item_requirements',[]) if v['name']==indicator),[])
                    if restricted and month not in restricted:continue
                    if not any(r['month']==month and (r['indicator']==indicator or r.get('indicator_group')==indicator) and r.get('result_text') not in ('','/','NA','N/A') for r in matching):
                        missing.append(f"{plan['batch_no']} / {plan['condition']} / {plan['specification']} / {plan['packaging']} / {plan.get('orientation') or '方案未限定方向'}：缺少 {month:g}月 {indicator}")
        known=bool(complete_plans) and all(any(all(str(row.get(k) or '')==str(p[k]) for k in ('product_role','batch_no','condition','specification','packaging')) for p in complete_plans) for row in records)
        if not known: missing_requirements='完整时间点与项目要求缺少可逐批、条件、规格和包装对应的方案/适用标准'
        else: missing_requirements='按选入方案逐批、条件、规格和包装核对时间点与项目'
        if any(not p.get('orientation') for p in complete_plans) and len({r.get('orientation') for r in records if r.get('orientation')})>1:
            missing_requirements+='；方案未明确各方向时间表，未强制各方向相同覆盖'
        return {'status':'存在缺口' if missing else ('已核对' if known else '证据不足'),'groups':details,'missing':list(dict.fromkeys(missing)),'requirements_evidence':[f for p in plans for f in p.get('sources',[])],'requirements_known':known,'reason':missing_requirements}

    def _significant_assessment(self, records, submissions):
        facts=[f for s in submissions if isinstance(s,dict) and not any(f.get('field')=='study_object' and '参比' in f.get('raw_value','') and '自制' not in f.get('raw_value','') for f in (s.get('extracted_json') or {}).get('facts',[])) for f in (s.get('extracted_json') or {}).get('facts',[]) if f.get('field')=='significant_criteria']
        assessments=[]
        # 仅执行明确给出项目、条件、变化方向/量、单位及依据的判定标准。
        for fact in facts:
            text=fact['raw_value']
            match=re.search(r'项目=([^;；]+)[;；]适用条件=([^;；]+)[;；](绝对变化|下降|上升)([>≥])([\d.]+)(%|mg/g|个百分点)[;；]依据=(.+)',text)
            if not match:
                assessments.append({'status':'证据不足','reason':'判定标准尚不能结构化为项目、条件、方向、阈值、单位与依据','evidence':fact});continue
            indicator,condition,kind,op,threshold,unit,basis=match.groups()
            rows=[r for r in records if not self._is_reference_record(r) and r.get('indicator')==indicator and r.get('condition')==condition]
            groups=self._build_series_groups(rows)
            if re.search(r'任一|每个|各时间',basis) and any(r.get('result_num') is None for r in rows):
                assessments.append({'status':'证据不足','reason':'适用时间点含非精确结果，不能据其余数值宣称全部时间点无显著变化','evidence':fact,'points':[r for r in rows if r.get('result_num') is None]})
            if not groups:assessments.append({'status':'证据不足','reason':'判定标准未匹配检测组','evidence':fact})
            for group in groups:
                points=group['rows'];units={r.get('result_unit') or r.get('limit_unit') for r in points}
                if len(points)<2 or (unit not in units and not (unit=='个百分点' and units=={'%'})):
                    assessments.append({'status':'证据不足','reason':'数值点或单位不足','group':group['label'],'evidence':fact});continue
                zero=[r for r in points if r.get('month')==0]
                if len({r['result_num'] for r in zero})!=1:
                    assessments.append({'status':'证据不足','reason':'缺少唯一可用0月基准','group':group['label'],'evidence':fact});continue
                applicable=points[1:] if re.search(r'任一|每个|各时间',basis) else points[-1:]
                for point in applicable:
                    if point.get('month')==0:continue
                    delta=point['result_num']-zero[0]['result_num']
                    value=abs(delta) if kind=='绝对变化' else (-delta if kind=='下降' else delta)
                    hit=value>=float(threshold) if op=='≥' else value>float(threshold)
                    assessments.append({'status':'发现问题' if hit else '通过','reason':f"{point['month']:g}月较0月{kind} {value:g}{unit}；计算 {point['result_num']:g} - {zero[0]['result_num']:g}；适用阈值 {op}{threshold}{unit}；依据 {basis}",'group':group['label'],'evidence':fact,'points':[zero[0],point]})
        status='发现问题' if any(a['status']=='发现问题' for a in assessments) else ('通过' if assessments and all(a['status']=='通过' for a in assessments) and all(any(a.get('points') and row in a['points'] for a in assessments) for row in records if row.get('result_num') is not None) else '证据不足')
        return {'status':status,'reason':'依据选入的适用判定标准逐组判断；未覆盖项目仍为证据不足','criteria':facts,'assessments':assessments}

    def _build_series_groups_legacy(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        batch_rows: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows or []:
            if row.get("result_num") is None or row.get("month") is None:
                continue
            batch = str(row.get("batch_no", "") or "").strip() or "未标注批号"
            batch_rows.setdefault(batch, []).append(row)

        groups: List[Dict[str, Any]] = []
        for batch, current_rows in sorted(batch_rows.items()):
            orientation_map: Dict[str, List[Dict[str, Any]]] = {}
            for row in current_rows:
                orientation_map.setdefault(str(row.get("orientation", "") or "").strip(), []).append(row)
            non_empty_orientations = [value for value in orientation_map if value]
            baseline_rows = [row for row in orientation_map.get("", []) if math.isclose(float(row.get("month", 0) or 0), 0.0)]
            target_orientations = non_empty_orientations or [""]
            if "" in orientation_map and any(not math.isclose(float(row.get("month", 0) or 0), 0.0) for row in orientation_map[""]):
                target_orientations = [""] + non_empty_orientations
            for orientation in target_orientations:
                series_rows = list(orientation_map.get(orientation, []))
                if orientation:
                    series_rows = baseline_rows + series_rows
                unique_rows: List[Dict[str, Any]] = []
                seen_points = set()
                for row in sorted(series_rows, key=lambda item: float(item.get("month", 0) or 0)):
                    point_key = (float(row.get("month", 0) or 0), float(row.get("result_num", 0) or 0))
                    if point_key in seen_points:
                        continue
                    seen_points.add(point_key)
                    unique_rows.append(row)
                if unique_rows:
                    groups.append(
                        {
                            "series_key": f"{batch}::{orientation or '未区分方向'}",
                            "batch_no": batch,
                            "orientation": orientation,
                            "label": f"{batch}批{orientation}" if orientation else f"{batch}批",
                            "rows": unique_rows,
                        }
                    )
        return groups

    @staticmethod
    def _series_trend(rows: List[Dict[str, Any]]) -> Tuple[str, bool]:
        if len(rows) < 2:
            return "数据不足", False
        first = float(rows[0].get("result_num", 0) or 0)
        last = float(rows[-1].get("result_num", 0) or 0)
        delta = last - first
        if abs(delta) < 1e-12:
            trend = "基本稳定"
        elif delta > 0:
            trend = "上升"
        else:
            trend = "下降"
        base = abs(first) if abs(first) > 1e-12 else 1.0
        return trend, None  # 显著变化必须另有适用品种、条件和判定依据。

    def _record_context(self, row: Dict[str, Any]) -> str:
        batch = str(row.get("batch_no", "") or "").strip()
        batch_text = f"{batch}批" if batch and not batch.endswith("批") else (batch or "未标注批号")
        month = float(row.get("month", 0) or 0)
        time_text = str(row.get("time_point", "") or "").strip()
        if not time_text:
            time_text = f"{self._fmt_value(month)}月"
        orientation = str(row.get("orientation", "") or "").strip()
        if orientation and orientation not in time_text:
            time_text = f"{time_text}{orientation}"
        return f"{batch_text}{time_text}"

    def _limit_detail(self, row: Dict[str, Any]) -> Dict[str, Any]:
        reason = str(row.get("limit_reason", "") or "").strip()
        return {
            "indicator": str(row.get("indicator", "") or "未知指标").strip(),
            "context": self._record_context(row),
            "source_position": row.get("source_position", {}),
            "numeric_verification": row.get("numeric_verification", []),
            "result_text": str(row.get("result_text", "") or "").strip(),
            "limit_text": str(row.get("limit_text", "") or "").strip(),
            "result_unit": str(row.get("result_unit", "") or "").strip(),
            "limit_unit": str(row.get("limit_unit", "") or "").strip(),
            "reason": reason,
            "reason_text": self._LIMIT_REASON_TEXT.get(reason, reason),
            "unit_note": row.get("unit_note", ""),
            "result_unit_source": row.get("result_unit_source", ""),
            "limit_unit_source": row.get("limit_unit_source", ""),
            "near_limit_applicable": bool(row.get("near_limit_applicable", False)),
            "source_doc_id": str(row.get("source_doc_id", "") or "").strip(),
            "source_file_name": str(row.get("source_file_name", "") or "").strip(),
            "source_row_index": row.get("source_row_index"),
            "components": [
                {**component, "reason_text": self._LIMIT_REASON_TEXT.get(component.get("reason"), component.get("reason", ""))}
                for component in row.get("components", []) if isinstance(component, dict)
            ],
        }

    def _build_limit_check(
        self,
        records: List[Dict[str, Any]],
        out_of_spec: List[Dict[str, Any]],
        undecidable_records: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        checked_count = sum(1 for item in records if item.get("within_standard") is not None)
        within_standard_count = sum(1 for item in records if item.get("within_standard") is True)
        if out_of_spec:
            status = "已超限"
            status_code = "out_of_spec"
            severity = "error"
            reminder = f"发现 {len(out_of_spec)} 条稳定性检测结果超出可接受标准，请重点复核。"
        elif undecidable_records:
            status = "待确认"
            status_code = "manual_review"
            severity = "warning"
            reminder = f"未发现可明确判为超限的结果，但有 {len(undecidable_records)} 条结果无法自动比较，需人工确认。"
        elif checked_count:
            status = "未超限"
            status_code = "within_spec"
            severity = "success"
            reminder = f"已自动比较 {checked_count} 条稳定性检测结果，均未超出可接受标准。"
        else:
            status = "待确认"
            status_code = "manual_review"
            severity = "warning"
            reminder = "未提取到可与限度自动比较的检测结果，无法判断是否超限。"
        unmarked_count = sum(bool(item.get("unit_note")) for item in records)
        near_inapplicable_count = sum(item.get("within_standard") is not None and
                                     not item.get("near_limit_applicable", False) for item in records)
        if unmarked_count:
            reminder += f" {unmarked_count}条双方未标注单位，按同一指标原始数值比较，未进行单位换算。"
        if near_inapplicable_count:
            reminder += f" {near_inapplicable_count}条不适用正数单侧精确值的接近预警规则，不影响其限度判断。"
        return {
            "status": status,
            "status_code": status_code,
            "severity": severity,
            "reminder": reminder,
            "checked_count": checked_count,
            "within_standard_count": within_standard_count,
            "out_of_spec_count": len(out_of_spec),
            "undecidable_count": len(undecidable_records),
            "out_of_spec_details": [self._limit_detail(item) for item in out_of_spec],
            "undecidable_details": [self._limit_detail(item) for item in undecidable_records],
        }

    def _build_indicator_results(self, grouped: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        for indicator, rows in grouped.items():
            numeric_rows = [row for row in rows if row.get("result_num") is not None]
            series_groups = self._build_series_groups(rows)
            series_summaries: List[Dict[str, Any]] = []
            trend_values: List[str] = []
            significant_change = None
            for group in series_groups:
                series_trend, series_significant = self._series_trend(group["rows"])
                if series_trend != "数据不足":
                    trend_values.append(series_trend)
                significant_change = None
                series_summaries.append(
                    {
                        "series_key": group["series_key"],
                        "batch_no": group["batch_no"],
                        "orientation": group["orientation"],
                        "label": group["label"],
                        "trend": series_trend,
                        "significant_change": series_significant,
                        "point_count": len(group["rows"]),
                        "start_month": group["rows"][0].get("month") if group["rows"] else None,
                        "end_month": group["rows"][-1].get("month") if group["rows"] else None,
                        "start_value": group["rows"][0].get("result_num") if group["rows"] else None,
                        "end_value": group["rows"][-1].get("result_num") if group["rows"] else None,
                    }
                )

            unique_trends = set(trend_values)
            if not trend_values:
                trend = "数值点不足，无法形成趋势" if numeric_rows else ("非单一数值项目" if rows else "数据不足")
            elif len(unique_trends) == 1:
                trend = trend_values[0]
            elif unique_trends <= {"基本稳定", "上升"}:
                trend = "整体上升"
            elif unique_trends <= {"基本稳定", "下降"}:
                trend = "整体下降"
            else:
                trend = "批次/放置方向趋势不一致"

            explicit_flags = [row.get("within_standard") for row in rows if row.get("within_standard") is not None]
            within_standard = all(bool(flag) for flag in explicit_flags) if explicit_flags else None
            undecidable_count = sum(1 for row in rows if row.get("within_standard") is None)
            undecidable_result_count = sum(
                1
                for row in rows
                if row.get("within_standard") is None
                and str(row.get("result_text", "") or "").strip().upper() not in {"", "/", "NA", "N/A"}
            )
            out_of_spec_rows = [row for row in rows if row.get("within_standard") is False]
            if out_of_spec_rows:
                limit_status = "已超限"
                limit_status_code = "out_of_spec"
                limit_severity = "error"
                limit_reminder = f"已超限（{len(out_of_spec_rows)} 条）"
            elif undecidable_result_count or not explicit_flags:
                limit_status = "待确认"
                limit_status_code = "manual_review"
                limit_severity = "warning"
                limit_reminder = (
                    f"待确认（{undecidable_result_count} 条无法自动比较）"
                    if undecidable_result_count
                    else "待确认（缺少可比较结果或限度）"
                )
            else:
                limit_status = "未超限"
                limit_status_code = "within_spec"
                limit_severity = "success"
                limit_reminder = f"未超限（{len(explicit_flags)} 条已判定记录）"
            near_limit = any(self._is_near_limit(row) for row in numeric_rows)

            min_value = min([float(row["result_num"]) for row in numeric_rows], default=None)
            max_value = max([float(row["result_num"]) for row in numeric_rows], default=None)
            min_rows = [row for row in numeric_rows if min_value is not None and math.isclose(float(row["result_num"]), min_value, rel_tol=1e-9, abs_tol=1e-12)]
            max_rows = [row for row in numeric_rows if max_value is not None and math.isclose(float(row["result_num"]), max_value, rel_tol=1e-9, abs_tol=1e-12)]
            min_contexts = list(dict.fromkeys(self._record_context(row) for row in min_rows))
            max_contexts = list(dict.fromkeys(self._record_context(row) for row in max_rows))
            min_display = f"{self._fmt_value(min_value)}（{'；'.join(min_contexts)}）" if min_value is not None else "-"
            max_display = f"{self._fmt_value(max_value)}（{'；'.join(max_contexts)}）" if max_value is not None else "-"
            value_range_display = f"{self._fmt_value(min_value)} ～ {self._fmt_value(max_value)}" if min_value is not None and max_value is not None else "非单一数值，不进行数值趋势计算"

            if within_standard is False:
                risk_level = "高"
            elif not numeric_rows:
                risk_level = "待确认"
            elif within_standard is None or undecidable_result_count or significant_change or near_limit:
                risk_level = "中"
            else:
                risk_level = "低"

            batches = sorted({str(row.get("batch_no", "") or "").strip() for row in rows if str(row.get("batch_no", "") or "").strip()})
            results.append(
                {
                    "indicator": indicator,
                    "trend": trend,
                    "within_standard": within_standard,
                    "limit_status": limit_status,
                    "limit_status_code": limit_status_code,
                    "limit_severity": limit_severity,
                    "limit_reminder": limit_reminder,
                    "out_of_spec_count": len(out_of_spec_rows),
                    "out_of_spec_details": [self._limit_detail(row) for row in out_of_spec_rows[:10]],
                    "undecidable_details": [
                        self._limit_detail(row)
                        for row in rows
                        if row.get("within_standard") is None
                        and str(row.get("result_text", "") or "").strip().upper() not in {"", "/", "NA", "N/A"}
                    ][:10],
                    "significant_change": significant_change,
                    "significant_change_reason": "趋势方向不等于显著变化，详见显著变化专项评价",
                    "min_sources": min_rows, "max_sources": max_rows,
                    "near_limit": near_limit,
                    "risk_level": risk_level,
                    "undecidable_points": undecidable_count,
                    "point_count": len(rows),
                    "numeric_point_count": len(numeric_rows),
                    "self_batch_count": len(batches),
                    "self_batches": batches,
                    "data_scope": "全部自制制剂批次",
                    "min_value": min_value,
                    "min_result": self._fmt_value(min_value) if min_value is not None else "",
                    "min_contexts": min_contexts,
                    "min_display": min_display,
                    "max_value": max_value,
                    "max_result": self._fmt_value(max_value) if max_value is not None else "",
                    "max_contexts": max_contexts,
                    "max_display": max_display,
                    "value_range_display": value_range_display,
                    "latest_time_point": "全部自制批次汇总",
                    "latest_result": value_range_display,
                    "series_summaries": series_summaries,
                    "data_source_types": sorted({str(row.get("data_source_type", "") or "") for row in rows if str(row.get("data_source_type", "") or "")}),
                }
            )
        return results

    def _is_near_limit(self, row: Dict[str, Any]) -> bool:
        if "near_limit" in row:
            return row.get("within_standard") is True and bool(row["near_limit"])
        return bool(self._enrich_record(row).get("near_limit", False))

    def _build_charts(self, grouped: Dict[str, List[Dict[str, Any]]], project_id: str = "") -> List[Dict[str, Any]]:
        charts: List[Dict[str, Any]] = []
        for indicator, rows in grouped.items():
            groups = [group for group in self._build_series_groups(rows) if len(group.get("rows", [])) >= 2]
            if not groups:
                continue
            public_groups = []
            flattened = []
            for group in groups:
                points = [
                    {
                        "time_point": row.get("time_point", ""),
                        "month": row.get("month", 0),
                        "value": row.get("result_num"),
                        "source_position": row.get("source_position", {}),
                        "batch_no": group["batch_no"],
                        "orientation": group["orientation"],
                    }
                    for row in group["rows"]
                ]
                public_groups.append({key: value for key, value in group.items() if key != "rows"} | {"points": points})
                flattened.extend(points)
            chart_item = {
                "indicator": indicator,
                "data_scope": "按角色、批号、条件、规格、包装、方向分组",
                "series": flattened,
                "series_groups": public_groups,
                "svg_content": self._build_svg_chart(indicator, groups),
            }
            if project_id:
                chart_file = Path("filing_change_review") / "projects" / project_id / "charts" / f"{self._sanitize_file_name(indicator)}.svg"
                chart_item["file_name"] = chart_file.name
            charts.append(chart_item)
        return charts

    @staticmethod
    def _sanitize_file_name(name: str) -> str:
        safe = re.sub(r"[^0-9A-Za-z\u4e00-\u9fa5._-]+", "_", str(name or "").strip())
        return safe or "stability_chart"

    def _build_svg_chart(self, indicator: str, groups: List[Dict[str, Any]]) -> str:
        width = 620
        padding_left, padding_right, padding_top = 62, 20, 34
        legend_rows = max(1, math.ceil(len(groups) / 3))
        plot_height = 175
        plot_bottom = padding_top + plot_height
        x_tick_y = plot_bottom + 18
        x_axis_title_y = plot_bottom + 37
        legend_start_y = plot_bottom + 59
        height = legend_start_y + (legend_rows - 1) * 15 + 10
        all_rows = [row for group in groups for row in group.get("rows", [])]
        values = [float(row.get("result_num", 0) or 0) for row in all_rows]
        months = [float(row.get("month", 0) or 0) for row in all_rows]
        min_value, max_value = min(values), max(values)
        if math.isclose(min_value, max_value):
            margin = max(abs(min_value) * 0.05, 1.0)
            min_value, max_value = min_value - margin, max_value + margin
        min_month, max_month = min(months), max(months)
        if math.isclose(min_month, max_month):
            max_month = min_month + 1

        def x_pos(month: float) -> float:
            return padding_left + (month - min_month) / (max_month - min_month) * (width - padding_left - padding_right)

        def y_pos(value: float) -> float:
            return padding_top + (max_value - value) / (max_value - min_value) * plot_height

        grid: List[str] = []
        for idx in range(5):
            value = min_value + (max_value - min_value) * idx / 4
            y = y_pos(value)
            grid.append(f'<line x1="{padding_left}" y1="{y:.2f}" x2="{width - padding_right}" y2="{y:.2f}" stroke="#e2e8f0" stroke-dasharray="3 3" />')
            grid.append(f'<text x="{padding_left - 8}" y="{y + 4:.2f}" font-size="10" text-anchor="end" fill="#64748b">{self._fmt_value(value)}</text>')

        unique_months = sorted(set(months))
        x_labels = []
        for month in unique_months:
            x = x_pos(month)
            x_labels.append(f'<line x1="{x:.2f}" y1="{plot_bottom}" x2="{x:.2f}" y2="{plot_bottom + 4}" stroke="#94a3b8" />')
            x_labels.append(f'<text x="{x:.2f}" y="{x_tick_y}" font-size="9" text-anchor="middle" fill="#475569">{self._fmt_value(month)}月</text>')

        series_svg: List[str] = []
        legend_svg: List[str] = []
        for index, group in enumerate(groups):
            color = self._CHART_COLORS[index % len(self._CHART_COLORS)]
            series_rows = group.get("rows", [])
            points = " ".join(
                f"{x_pos(float(row.get('month', 0) or 0)):.2f},{y_pos(float(row.get('result_num', 0) or 0)):.2f}"
                for row in series_rows
            )
            series_svg.append(f'<polyline fill="none" stroke="{color}" stroke-width="2" points="{points}" />')
            for row in series_rows:
                cx = x_pos(float(row.get("month", 0) or 0))
                cy = y_pos(float(row.get("result_num", 0) or 0))
                tooltip = html.escape(f"{group.get('label', '')} {row.get('time_point', '')}: {row.get('result_text', '')}；来源 {row.get('source_position', {})}")
                series_svg.append(f'<circle cx="{cx:.2f}" cy="{cy:.2f}" r="2.8" fill="{color}"><title>{tooltip}</title></circle>')
            legend_x = padding_left + (index % 3) * 176
            legend_y = legend_start_y + (index // 3) * 15
            full_label = html.escape(str(group.get("label", "") or ""))
            label = html.escape(str(group.get("label", "") or "")[:22]) + "…"
            legend_svg.append(f'<line x1="{legend_x}" y1="{legend_y - 4}" x2="{legend_x + 16}" y2="{legend_y - 4}" stroke="{color}" stroke-width="2" />')
            legend_svg.append(f'<text x="{legend_x + 21}" y="{legend_y}" font-size="9" fill="#475569"><title>{full_label}</title>{label}</text>')

        plot_width = width - padding_left - padding_right
        title = html.escape(f"{indicator} - 分组趋势")
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">'
            f'<rect width="{width}" height="{height}" fill="#ffffff" />'
            f'<text x="{padding_left}" y="19" font-size="13" font-weight="600" fill="#0f172a">{title}</text>'
            f'{"".join(grid)}'
            f'<line x1="{padding_left}" y1="{plot_bottom}" x2="{width - padding_right}" y2="{plot_bottom}" stroke="#94a3b8" />'
            f'<line x1="{padding_left}" y1="{padding_top}" x2="{padding_left}" y2="{plot_bottom}" stroke="#94a3b8" />'
            f'<text x="{padding_left + plot_width / 2:.2f}" y="{x_axis_title_y}" font-size="10" text-anchor="middle" fill="#64748b">考察时间点（月）</text>'
            f'<text x="18" y="{padding_top + plot_height / 2:.2f}" font-size="10" text-anchor="middle" fill="#64748b" transform="rotate(-90 18 {padding_top + plot_height / 2:.2f})">检测结果</text>'
            f'{"".join(x_labels)}{"".join(series_svg)}{"".join(legend_svg)}'
            f'</svg>'
        )
