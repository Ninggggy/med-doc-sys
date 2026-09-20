from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional


class P52RuleEngineService:
    """执行 3.2.P.5.2 方法学规则识别、实体抽取和候选问题判断。"""

    RULE_FILE = Path(__file__).resolve().parents[1] / "data" / "rule" / "p52_method_review" / "rules.json"
    PROFILE_FIELD_WHITELIST = {
        "related_substances": {
            "sample_preparation", "reference_preparation", "reference_standard", "suitability_reference",
            "decision_rule", "calculation_formula", "system_suitability", "column_name", "mobile_phase",
            "flow_rate", "wavelength", "peak_identification_rule", "rrf_rule", "integration_rule",
            "disregard_limit_rule", "linked_spec_item", "linked_validation_target",
        },
        "dissolution": {
            "sample_preparation", "decision_rule", "apparatus", "medium_name", "medium_volume",
            "rotation_speed", "temperature", "sampling_timepoints", "filtration", "replacement_volume",
            "quantitation_method_reference", "acceptance_stage_rule", "release_type", "linked_spec_item",
        },
        "solubility": {
            "sample_preparation", "decision_rule", "medium_name", "medium_volume", "temperature",
            "sampling_timepoints", "linked_spec_item",
        },
        "microbial_limits": {
            "sample_pretreatment", "diluent_or_neutralizer", "test_scope", "culture_media",
            "incubation_conditions", "enumeration_method", "suitability_reference", "decision_rule",
            "pharmacopoeia_reference", "linked_spec_item",
        },
        "assay": {
            "sample_preparation", "reference_preparation", "reference_standard", "decision_rule",
            "calculation_formula", "system_suitability", "column_name", "mobile_phase", "flow_rate",
            "wavelength", "linked_spec_item", "linked_validation_target", "potency_correction", "purity_correction",
        },
        "assay_titration": {
            "sample_preparation", "reference_standard", "decision_rule", "calculation_formula",
            "linked_spec_item", "linked_validation_target",
        },
        "assay_hplc": {
            "sample_preparation", "reference_preparation", "reference_standard", "decision_rule",
            "calculation_formula", "system_suitability", "column_name", "mobile_phase", "flow_rate",
            "wavelength", "linked_spec_item", "linked_validation_target", "potency_correction", "purity_correction",
        },
        "water_content": {
            "sample_preparation", "decision_rule", "pharmacopoeia_reference", "linked_spec_item",
        },
        "uv_identification": {
            "sample_preparation", "sample_weight", "dilution_steps", "final_volume",
            "solvent_or_medium", "instrument_or_method_reference", "result_judgment_expression",
            "characteristic_max_wavelengths", "characteristic_min_wavelengths", "absorbance_ratio",
            "specificity_support", "uv_method_present", "complementary_identification_method",
            "quality_standard_uv_requirement", "uv_method_description", "linked_validation_target",
            "decision_rule", "linked_spec_item",
        },
    }
    # 中文注释：字段别名用于把不同方法规则里的异名字段映射到统一抽取字段，提升规则通用性
    FIELD_ALIASES = {
        "sample_amount": "sample_weight",
        "sample_mass": "sample_weight",
        "sample_solution_preparation": "sample_preparation",
        "detection_wavelength": "wavelength",
        "max_wavelength": "characteristic_max_wavelengths",
        "min_wavelength": "characteristic_min_wavelengths",
        "solvent": "solvent_or_medium",
        "medium": "solvent_or_medium",
        "instrument_reference": "instrument_or_method_reference",
        "result_expression": "result_judgment_expression",
        "validation_target": "linked_validation_target",
    }

    def __init__(self) -> None:
        self.rule_document = self._load_rule_document()
        self._rule_file_mtime = self._get_rule_file_mtime()

    def _get_rule_file_mtime(self) -> float:
        try:
            return float(self.RULE_FILE.stat().st_mtime)
        except Exception:
            return 0.0

    def _ensure_rule_document_fresh(self) -> None:
        """中文注释：按文件更新时间热加载规则，避免服务进程内长期持有旧规则。"""
        latest_mtime = self._get_rule_file_mtime()
        if latest_mtime <= 0:
            return
        if latest_mtime == self._rule_file_mtime:
            return
        latest_doc = self._load_rule_document()
        if isinstance(latest_doc, dict) and latest_doc:
            self.rule_document = latest_doc
            self._rule_file_mtime = latest_mtime

    def _load_rule_document(self) -> Dict[str, Any]:
        if not self.RULE_FILE.exists():
            return {}
        try:
            return json.loads(self.RULE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}

    @staticmethod
    def _normalize_text(value: Any) -> str:
        return str(value or "").strip()

    @staticmethod
    def _normalize_section_id(value: Any) -> str:
        return str(value or "").strip().lower()

    def get_rule_definition(self, rule_id: str) -> Dict[str, Any]:
        self._ensure_rule_document_fresh()
        normalized_rule_id = self._normalize_text(rule_id)
        if not normalized_rule_id:
            return {}
        for rule in self.rule_document.get("general_rules", []) if isinstance(self.rule_document.get("general_rules", []), list) else []:
            if not isinstance(rule, dict):
                continue
            if self._normalize_text(rule.get("rule_id", "")) == normalized_rule_id:
                return dict(rule)
        profiles = self.rule_document.get("method_profiles", {}) if isinstance(self.rule_document.get("method_profiles", {}), dict) else {}
        for profile_key, profile in profiles.items():
            if not isinstance(profile, dict):
                continue
            for rule in profile.get("rules", []) if isinstance(profile.get("rules", []), list) else []:
                if not isinstance(rule, dict):
                    continue
                if self._normalize_text(rule.get("rule_id", "")) != normalized_rule_id:
                    continue
                payload = dict(rule)
                payload["method_type"] = str(profile_key).strip()
                payload["method_display_name"] = self._normalize_text(profile.get("display_name", ""))
                return payload
        return {}

    def get_rule_display_text(self, rule_id: str) -> str:
        self._ensure_rule_document_fresh()
        definition = self.get_rule_definition(rule_id)
        normalized_rule_id = self._normalize_text(rule_id)
        rule_name = self._normalize_text(definition.get("rule_name", "")) or self._normalize_text(definition.get("problem_item", ""))
        if normalized_rule_id and rule_name:
            return f"{normalized_rule_id} {rule_name}"
        return normalized_rule_id or rule_name

    def detect_method_profiles(self, *, section_id: str, section_name: str, text: str) -> List[str]:
        self._ensure_rule_document_fresh()
        context = " ".join([self._normalize_text(section_id), self._normalize_text(section_name), self._normalize_text(text)]).lower()
        if "含量" in context:
            titration_markers = ["电位滴定", "永停滴定", "滴定液", "滴定仪", "滴定"]
            hplc_markers = ["高效液相色谱", "hplc", "色谱柱", "流动相", "检测波长", "理论板数", "分离度"]
            if any(marker in context for marker in titration_markers):
                return ["assay_titration"]
            if any(marker in context for marker in hplc_markers):
                return ["assay_hplc"]
        matched: List[str] = []
        profiles = self.rule_document.get("method_profiles", {}) if isinstance(self.rule_document.get("method_profiles", {}), dict) else {}
        for profile_key, profile in profiles.items():
            if not isinstance(profile, dict):
                continue
            keywords = [self._normalize_text(item).lower() for item in profile.get("keywords", []) if self._normalize_text(item)]
            if any(keyword in context for keyword in keywords):
                matched.append(str(profile_key).strip())
        if matched:
            return matched
        if "水分" in context or "卡尔费休" in context or "卡氏" in context:
            return ["water_content"]
        return []

    def build_method_context(self, *, section_id: str, section_name: str, text: str) -> Dict[str, Any]:
        self._ensure_rule_document_fresh()
        method_profiles = self.detect_method_profiles(section_id=section_id, section_name=section_name, text=text)
        entities = self.extract_entities(text=text, section_name=section_name, method_profiles=method_profiles)
        entities["section_id"] = self._normalize_section_id(section_id)
        entities["section_title"] = self._normalize_text(section_name)
        entities["method_profiles"] = method_profiles
        entities["method_type"] = "、".join(method_profiles)
        entities["method_purpose"] = self._infer_method_purpose(method_profiles)
        return entities

    def list_method_profiles(self) -> List[Dict[str, Any]]:
        self._ensure_rule_document_fresh()
        profiles = self.rule_document.get("method_profiles", {}) if isinstance(self.rule_document.get("method_profiles", {}), dict) else {}
        out: List[Dict[str, Any]] = []
        for profile_key, profile in profiles.items():
            if not isinstance(profile, dict):
                continue
            out.append(
                {
                    "profile_key": str(profile_key).strip(),
                    "display_name": self._normalize_text(profile.get("display_name", "")),
                    "keywords": [self._normalize_text(item) for item in profile.get("keywords", []) if self._normalize_text(item)],
                    "rule_ids": [self._normalize_text(item.get("rule_id", "")) for item in profile.get("rules", []) if isinstance(item, dict) and self._normalize_text(item.get("rule_id", ""))],
                }
            )
        return out

    def list_general_rules(self) -> List[Dict[str, str]]:
        self._ensure_rule_document_fresh()
        out: List[Dict[str, str]] = []
        for rule in self.rule_document.get("general_rules", []) if isinstance(self.rule_document.get("general_rules", []), list) else []:
            if not isinstance(rule, dict):
                continue
            rule_id = self._normalize_text(rule.get("rule_id", ""))
            if not rule_id:
                continue
            out.append(
                {
                    "rule_id": rule_id,
                    "rule_name": self._normalize_text(rule.get("rule_name", "")) or self._normalize_text(rule.get("problem_item", "")),
                    "problem_item": self._normalize_text(rule.get("problem_item", "")),
                }
            )
        return out

    def list_entity_fields(self) -> List[str]:
        return [
            "sample_preparation",
            "reference_preparation",
            "reference_standard",
            "pharmacopoeia_reference",
            "sample_pretreatment",
            "diluent_or_neutralizer",
            "test_scope",
            "culture_media",
            "incubation_conditions",
            "enumeration_method",
            "suitability_reference",
            "decision_rule",
            "calculation_formula",
            "system_suitability",
            "column_name",
            "mobile_phase",
            "flow_rate",
            "wavelength",
            "peak_identification_rule",
            "rrf_rule",
            "integration_rule",
            "disregard_limit_rule",
            "apparatus",
            "medium_name",
            "medium_volume",
            "rotation_speed",
            "temperature",
            "sampling_timepoints",
            "filtration",
            "replacement_volume",
            "quantitation_method_reference",
            "acceptance_stage_rule",
            "release_type",
            "linked_spec_item",
            "linked_validation_target",
            "linked_justification_statement",
            "potency_correction",
            "purity_correction",
            "characteristic_max_wavelengths",
            "characteristic_min_wavelengths",
            "absorbance_ratio",
            "specificity_support",
            "uv_method_present",
            "complementary_identification_method",
            "quality_standard_uv_requirement",
            "uv_method_description",
            "instrument_or_method_reference",
            "result_judgment_expression",
            "sample_weight",
            "dilution_steps",
            "final_volume",
            "solvent_or_medium",
        ]

    def get_enabled_entity_fields(self, method_profiles: List[str]) -> set[str]:
        return self._resolve_enabled_fields(method_profiles)

    def evaluate_candidates(self, method_context: Dict[str, Any]) -> List[Dict[str, Any]]:
        self._ensure_rule_document_fresh()
        candidates: List[Dict[str, Any]] = []
        candidates.extend(self._evaluate_rule_block(self.rule_document.get("general_rules", []), method_context, None))
        for profile_key in method_context.get("method_profiles", []) if isinstance(method_context.get("method_profiles", []), list) else []:
            profile = self._get_profile(profile_key)
            candidates.extend(self._evaluate_rule_block(profile.get("rules", []), method_context, profile_key))
        return candidates

    def _get_profile(self, profile_key: str) -> Dict[str, Any]:
        profiles = self.rule_document.get("method_profiles", {}) if isinstance(self.rule_document.get("method_profiles", {}), dict) else {}
        profile = profiles.get(str(profile_key).strip(), {})
        return dict(profile) if isinstance(profile, dict) else {}

    def _evaluate_rule_block(
        self,
        rules: Any,
        method_context: Dict[str, Any],
        profile_key: Optional[str]
    ) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for rule in rules if isinstance(rules, list) else []:
            if not isinstance(rule, dict):
                continue
            if not self._rule_triggered(rule, method_context):
                continue
            fields = [self._normalize_text(item) for item in rule.get("fields", []) if self._normalize_text(item)]
            out.append(
                {
                    "rule_id": self._normalize_text(rule.get("rule_id", "")),
                    "rule_name": self._normalize_text(rule.get("rule_name", "")) or self._normalize_text(rule.get("problem_item", "")),
                    "rule_display_text": self.get_rule_display_text(rule.get("rule_id", "")),
                    "severity": self._normalize_text(rule.get("severity", "minor")).lower() or "minor",
                    "problem_item": self._normalize_text(rule.get("problem_item", "")),
                    "reasoning": self._normalize_text(rule.get("reasoning", "")),
                    "revision_suggestion": self._normalize_text(rule.get("revision_suggestion", "")),
                    "fields": fields,
                    "method_type": profile_key or "",
                    "retrieval_sections": self._get_profile(profile_key).get("retrieval_sections", []) if profile_key else ["3.2.p.5.1", "3.2.p.5.6"],
                    "knowledge_query": self._normalize_text(self._get_profile(profile_key).get("knowledge_query", "")) if profile_key else "",
                    "evidence_keywords": self._build_rule_evidence_keywords(rule, fields, method_context),
                    "current_evidence": self._build_current_evidence(fields, method_context)
                }
            )
        return out

    def _rule_triggered(self, rule: Dict[str, Any], method_context: Dict[str, Any]) -> bool:
        check = self._normalize_text(rule.get("check", ""))
        fields = [self._normalize_text(item) for item in rule.get("fields", []) if self._normalize_text(item)]
        if check in {"missing_field", "missing_any"}:
            return any(not self._field_found(method_context, field) for field in fields)
        if check == "missing_all":
            return bool(fields) and all(not self._field_found(method_context, field) for field in fields)
        if check == "missing_k_of_n":
            if not fields:
                return False
            try:
                min_present = int(rule.get("min_present", 1))
            except Exception:
                min_present = 1
            min_present = max(1, min(min_present, len(fields)))
            present_count = sum(1 for field in fields if self._field_found(method_context, field))
            return present_count < min_present
        if check == "related_substances_rrf":
            if self._field_found(method_context, "rrf_rule"):
                return False
            context = self._normalize_text(method_context.get("_source_text", "")).lower()
            keyword_flags = ["指定杂质", "杂质", "校正因子", "相对响应因子", "总杂", "未知杂质"]
            return any(flag.lower() in context for flag in keyword_flags)
        if check == "assay_correction_logic":
            uses_reference = self._field_found(method_context, "reference_standard") or self._field_found(method_context, "reference_preparation")
            has_correction = self._field_found(method_context, "potency_correction") or self._field_found(method_context, "purity_correction")
            return uses_reference and not has_correction
        if check == "uv_specificity_signal_missing":
            # 中文注释：UV 专属性支持至少应出现“最小吸收波长/吸光度比值/专属性佐证”之一
            has_min = self._field_found(method_context, "characteristic_min_wavelengths")
            has_ratio = self._field_found(method_context, "absorbance_ratio")
            has_support = self._field_found(method_context, "specificity_support")
            has_max = self._field_found(method_context, "characteristic_max_wavelengths")
            return has_max and not (has_min or has_ratio or has_support)
        if check == "single_uv_without_support":
            has_uv = self._field_found(method_context, "uv_method_present") or self._field_found(method_context, "characteristic_max_wavelengths")
            has_complementary = self._field_found(method_context, "complementary_identification_method")
            has_support = self._field_found(method_context, "specificity_support")
            return has_uv and not (has_complementary or has_support)
        if check == "standard_method_mismatch":
            has_qs = self._field_found(method_context, "quality_standard_uv_requirement")
            has_method = self._field_found(method_context, "uv_method_description")
            if not (has_qs and has_method):
                return False
            qs_text = self._normalize_text((method_context.get("quality_standard_uv_requirement", {}) or {}).get("excerpt", "")).lower()
            method_text = self._normalize_text((method_context.get("uv_method_description", {}) or {}).get("excerpt", "")).lower()
            # 中文注释：简化一致性判定：两边都提到最大/最小或吸光度比值则视为一致，否则触发不一致风险
            qs_flags = {"max": any(x in qs_text for x in ["最大", "λmax", "max"]), "min": any(x in qs_text for x in ["最小", "λmin", "min"]), "ratio": any(x in qs_text for x in ["比值", "ratio"])}
            method_flags = {"max": any(x in method_text for x in ["最大", "λmax", "max"]), "min": any(x in method_text for x in ["最小", "λmin", "min"]), "ratio": any(x in method_text for x in ["比值", "ratio"])}
            return any(qs_flags[k] and not method_flags[k] for k in qs_flags.keys())
        return False

    def _field_found(self, method_context: Dict[str, Any], field_name: str) -> bool:
        normalized_field = self._normalize_text(field_name)
        alias_field = self.FIELD_ALIASES.get(normalized_field, normalized_field)
        payload = method_context.get(alias_field, {})
        if isinstance(payload, dict):
            return bool(payload.get("found", False))
        if payload:
            return True
        # 中文注释：当规则字段未被结构化抽取时，退化为关键词检索，避免“规则失效但未提示”的漏检
        source_text = self._normalize_text(method_context.get("_source_text", "")).lower()
        if not source_text:
            return False
        for keyword in self._field_keywords(alias_field):
            token = self._normalize_text(keyword).lower()
            if token and token in source_text:
                return True
        return False

    def _build_current_evidence(self, fields: List[str], method_context: Dict[str, Any]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for field in fields:
            alias_field = self.FIELD_ALIASES.get(self._normalize_text(field), self._normalize_text(field))
            payload = method_context.get(alias_field, {})
            if not isinstance(payload, dict) or not payload.get("found"):
                continue
            excerpt = self._normalize_text(payload.get("excerpt", ""))
            if not excerpt:
                continue
            out.append({"field": alias_field, "excerpt": excerpt, "source_section": self._normalize_text(method_context.get("section_id", ""))})
        return out

    def _build_evidence_keywords(self, fields: List[str], method_context: Dict[str, Any]) -> List[str]:
        out: List[str] = []
        for field in fields:
            alias_field = self.FIELD_ALIASES.get(self._normalize_text(field), self._normalize_text(field))
            out.extend(self._field_keywords(alias_field))
        for profile_key in method_context.get("method_profiles", []) if isinstance(method_context.get("method_profiles", []), list) else []:
            profile = self._get_profile(profile_key)
            out.extend([self._normalize_text(item) for item in profile.get("keywords", []) if self._normalize_text(item)])
        deduped: List[str] = []
        seen = set()
        for item in out:
            if not item or item in seen:
                continue
            seen.add(item)
            deduped.append(item)
        return deduped

    def _build_rule_evidence_keywords(self, rule: Dict[str, Any], fields: List[str], method_context: Dict[str, Any]) -> List[str]:
        out: List[str] = [self._normalize_text(item) for item in rule.get("evidence_keywords", []) if self._normalize_text(item)]
        out.extend(self._build_evidence_keywords(fields, method_context))
        deduped: List[str] = []
        seen = set()
        for item in out:
            if not item or item in seen:
                continue
            seen.add(item)
            deduped.append(item)
        return deduped

    def _infer_method_purpose(self, profiles: List[str]) -> str:
        mapping = {
            "related_substances": "控制杂质与降解产物",
            "dissolution": "评价制剂释放行为",
            "solubility": "评价样品在指定溶剂中的溶解特性",
            "assay_titration": "采用滴定法测定含量",
            "assay_hplc": "采用高效液相色谱法测定含量",
            "water_content": "测定样品水分含量",
            "uv_identification": "通过紫外吸收特征进行鉴别",
        }
        values = [mapping.get(item, "") for item in profiles if mapping.get(item, "")]
        return "、".join(values)

    def extract_entities(self, *, text: str, section_name: str = "", method_profiles: Optional[List[str]] = None) -> Dict[str, Any]:
        source_text = self._normalize_text(text)
        result: Dict[str, Any] = {"_source_text": source_text}
        field_map = {
            "sample_preparation": ["供试品", "样品溶液", "取本品", "制成", "精密量取"],
            "reference_preparation": ["对照溶液", "对照液", "对照品溶液", "参比溶液"],
            "reference_standard": ["对照品", "标准品", "参比制剂"],
            "pharmacopoeia_reference": ["中国药典", "药典", "通则", "检查法"],
            "sample_pretreatment": ["供试液", "前处理", "稀释", "均质", "薄膜过滤"],
            "diluent_or_neutralizer": ["稀释液", "中和剂", "氯化钠-蛋白胨", "吐温", "卵磷脂"],
            "test_scope": ["需氧菌", "霉菌", "酵母菌", "控制菌", "大肠埃希菌", "TAMC", "TYMC"],
            "culture_media": ["培养基", "营养琼脂", "玫瑰红钠琼脂", "胰酪大豆胨琼脂"],
            "incubation_conditions": ["培养", "30-35℃", "20-25℃", "培养48", "培养72"],
            "enumeration_method": ["平皿法", "薄膜过滤法", "倾注法", "菌落计数"],
            "suitability_reference": ["适用性", "方法适用性", "中和", "回收率"],
            "decision_rule": ["应为", "不得过", "限度", "判定", "比较", "应完全溶解", "应未完全溶解", "极易溶", "易溶", "略溶", "结果:"],
            "calculation_formula": ["计算公式", "按外标法", "外标法", "公式", "计算含量"],
            "system_suitability": ["系统适用性", "理论板数", "分离度", "尾峰因子"],
            "column_name": ["色谱柱", "十八烷基硅烷", "C18", "手性柱"],
            "mobile_phase": ["流动相", "乙腈", "甲醇", "磷酸盐缓冲液"],
            "flow_rate": ["流速", "ml/min", "mL/min"],
            "wavelength": ["检测波长", "nm", "波长"],
            "peak_identification_rule": ["保留时间", "峰识别", "主峰", "杂质峰"],
            "rrf_rule": ["RRF", "相对响应因子", "校正因子"],
            "integration_rule": ["积分", "峰面积", "积分参数"],
            "disregard_limit_rule": ["忽略限", "不计峰", "小于"],
            "apparatus": ["桨法", "篮法", "转篮", "溶出仪"],
            "medium_name": ["溶出介质", "介质", "盐酸", "磷酸盐缓冲液", "水", "甲醇"],
            "medium_volume": ["体积", "ml", "mL", "900"],
            "rotation_speed": ["转速", "rpm", "r/min"],
            "temperature": ["温度", "37", "37.0", "25℃", "25℃±2℃", "水浴", "30-35℃", "23-28℃"],
            "sampling_timepoints": ["取样时间", "时间点", "分钟", "min", "每隔 5 分钟", "振摇 30 秒", "观察 30 分钟"],
            "filtration": ["滤膜", "过滤", "滤过"],
            "replacement_volume": ["补液", "补充等体积", "补加"],
            "quantitation_method_reference": ["按含量测定法", "采用 HPLC 法", "定量方法"],
            "acceptance_stage_rule": ["S1", "S2", "S3", "Q=", "阶段"],
            "release_type": ["缓释", "控释", "肠溶", "速释"],
            "linked_spec_item": ["质量标准", "有关物质", "溶出", "含量测定", "溶解度", "比旋度", "鉴别", "水分", "微生物"],
            "linked_validation_target": ["验证", "方法学验证", "准确度", "精密度", "专属性"],
            "linked_justification_statement": ["方法选择", "依据", "合理性", "控制策略"],
            "potency_correction": ["效价", "标示量", "校正"],
            "purity_correction": ["纯度", "扣除", "折算"],
            "characteristic_max_wavelengths": ["最大吸收", "最大波长", "λmax", "max", "最大吸收波长"],
            "characteristic_min_wavelengths": ["最小吸收", "最小波长", "λmin", "min", "最小吸收波长"],
            "absorbance_ratio": ["吸光度比", "吸收比", "A(", "比值"],
            "specificity_support": ["专属性", "特异性", "区分", "干扰", "阴性对照", "空白对照"],
            "uv_method_present": ["UV", "紫外", "紫外-可见", "紫外可见分光光度法"],
            "complementary_identification_method": ["IR", "红外", "保留时间", "薄层", "HPLC 鉴别", "色谱保留行为"],
            "quality_standard_uv_requirement": ["质量标准", "3.2.P.5.1", "鉴别", "紫外", "波长"],
            "uv_method_description": ["操作方法", "供试品溶液", "测定", "在", "nm", "紫外"],
            "instrument_or_method_reference": ["紫外-可见分光光度法", "分光光度计", "按中国药典", "按药典"],
            "result_judgment_expression": ["应在", "出现", "吸收峰", "应符合", "判定", "结果"],
            "sample_weight": ["称取", "精密称取", "取本品", "mg", "g"],
            "dilution_steps": ["加", "稀释", "摇匀", "再加", "定容"],
            "final_volume": ["定容至", "置", "ml", "mL"],
            "solvent_or_medium": ["甲醇", "乙醇", "水", "稀盐酸", "溶剂", "介质"],
        }
        enabled_fields = self._resolve_enabled_fields(method_profiles or [])
        for field_name, keywords in field_map.items():
            if enabled_fields and field_name not in enabled_fields:
                continue
            result[field_name] = self._extract_field_payload(source_text, keywords)
        result["method_type"] = self._extract_field_payload(source_text, [section_name, "方法", "HPLC", "溶出", "含量"])
        # 中文注释：UV 波长字段优先使用模式匹配，避免仅凭“波长”关键词误判为同时具备最大/最小
        result["characteristic_max_wavelengths"] = self._extract_uv_wavelength_payload(source_text, kind="max")
        result["characteristic_min_wavelengths"] = self._extract_uv_wavelength_payload(source_text, kind="min")
        if not result.get("uv_method_present", {}).get("found", False):
            result["uv_method_present"] = self._extract_field_payload(source_text, ["UV", "紫外", "紫外-可见", "分光光度法"])
        return result

    def _resolve_enabled_fields(self, method_profiles: List[str]) -> set[str]:
        if not method_profiles:
            return set()
        enabled: set[str] = {"sample_preparation", "decision_rule", "linked_spec_item", "pharmacopoeia_reference"}
        for profile in method_profiles:
            profile_key = str(profile).strip()
            enabled.update(self.PROFILE_FIELD_WHITELIST.get(profile_key, set()))
            profile_doc = self._get_profile(profile_key)
            rule_fields = [
                self.FIELD_ALIASES.get(self._normalize_text(field), self._normalize_text(field))
                for rule in (profile_doc.get("rules", []) if isinstance(profile_doc.get("rules", []), list) else [])
                if isinstance(rule, dict)
                for field in (rule.get("fields", []) if isinstance(rule.get("fields", []), list) else [])
                if self._normalize_text(field)
            ]
            enabled.update({field for field in rule_fields if field})
        return enabled

    def _extract_field_payload(self, text: str, keywords: List[str]) -> Dict[str, Any]:
        excerpt = self._find_excerpt(text, keywords)
        return {"found": bool(excerpt), "excerpt": excerpt, "keywords": [self._normalize_text(item) for item in keywords if self._normalize_text(item)]}

    def _extract_uv_wavelength_payload(self, text: str, *, kind: str) -> Dict[str, Any]:
        content = self._normalize_text(text)
        if not content:
            return {"found": False, "excerpt": "", "keywords": []}
        if kind == "max":
            patterns = [
                r"(?:最大吸收波长|最大波长|λmax|max)[^\n，。；;:：]{0,20}(\d{2,4}(?:\.\d+)?)\s*nm",
                r"在\s*(\d{2,4}(?:\.\d+)?)\s*nm\s*处.*(?:最大|主)吸收",
            ]
            keywords = ["最大吸收波长", "最大波长", "λmax", "max"]
        else:
            patterns = [
                r"(?:最小吸收波长|最小波长|λmin|min)[^\n，。；;:：]{0,20}(\d{2,4}(?:\.\d+)?)\s*nm",
                r"在\s*(\d{2,4}(?:\.\d+)?)\s*nm\s*处.*最小吸收",
            ]
            keywords = ["最小吸收波长", "最小波长", "λmin", "min"]
        for pattern in patterns:
            match = re.search(pattern, content, flags=re.IGNORECASE)
            if not match:
                continue
            start = max(match.start() - 30, 0)
            end = min(match.end() + 30, len(content))
            return {"found": True, "excerpt": content[start:end].strip(), "keywords": keywords}
        return {"found": False, "excerpt": "", "keywords": keywords}

    def _find_excerpt(self, text: str, keywords: List[str], *, window: int = 50) -> str:
        content = self._normalize_text(text)
        lowered = content.lower()
        for keyword in keywords:
            target = self._normalize_text(keyword)
            if not target:
                continue
            index = lowered.find(target.lower())
            if index < 0:
                continue
            start = max(index - window, 0)
            end = min(index + len(target) + window, len(content))
            return content[start:end].strip()
        return ""

    @staticmethod
    def _field_keywords(field_name: str) -> List[str]:
        mapping = {
            "sample_preparation": ["供试品", "制备", "样品溶液"],
            "reference_preparation": ["对照溶液", "对照液"],
            "reference_standard": ["对照品", "标准品"],
            "pharmacopoeia_reference": ["中国药典", "药典", "通则"],
            "sample_pretreatment": ["供试液", "前处理", "均质", "稀释"],
            "diluent_or_neutralizer": ["稀释液", "中和剂", "氯化钠-蛋白胨"],
            "test_scope": ["需氧菌", "霉菌", "酵母菌", "控制菌", "TAMC", "TYMC"],
            "culture_media": ["培养基", "营养琼脂", "玫瑰红钠琼脂"],
            "incubation_conditions": ["培养", "30-35℃", "20-25℃"],
            "enumeration_method": ["平皿法", "薄膜过滤法", "菌落计数"],
            "suitability_reference": ["适用性", "中和", "回收率"],
            "decision_rule": ["限度", "判定", "应完全溶解", "应未完全溶解", "极易溶", "易溶", "略溶", "结果"],
            "calculation_formula": ["计算公式", "外标法"],
            "system_suitability": ["系统适用性", "分离度", "理论板数"],
            "column_name": ["色谱柱", "C18"],
            "mobile_phase": ["流动相"],
            "flow_rate": ["流速"],
            "wavelength": ["波长"],
            "peak_identification_rule": ["保留时间", "峰识别"],
            "rrf_rule": ["RRF", "相对响应因子"],
            "integration_rule": ["积分"],
            "disregard_limit_rule": ["忽略限"],
            "apparatus": ["桨法", "篮法", "溶出仪"],
            "medium_name": ["溶出介质", "介质", "水", "甲醇"],
            "medium_volume": ["体积"],
            "rotation_speed": ["转速"],
            "temperature": ["温度", "25℃", "25℃±2℃", "水浴"],
            "sampling_timepoints": ["取样时间", "时间点", "每隔 5 分钟", "振摇 30 秒", "观察 30 分钟"],
            "filtration": ["过滤", "滤膜"],
            "replacement_volume": ["补液"],
            "quantitation_method_reference": ["定量方法", "含量测定法"],
            "acceptance_stage_rule": ["S1", "S2", "S3", "Q="],
            "release_type": ["缓释", "控释", "肠溶"],
            "linked_spec_item": ["质量标准", "3.2.P.5.1", "溶解度", "比旋度", "鉴别", "有关物质", "水分", "微生物", "含量"],
            "linked_validation_target": ["验证", "3.2.P.5.3"],
            "linked_justification_statement": ["依据", "合理性", "控制策略"],
            "potency_correction": ["效价", "标示量"],
            "purity_correction": ["纯度"],
            "characteristic_max_wavelengths": ["最大吸收波长", "λmax", "最大波长"],
            "characteristic_min_wavelengths": ["最小吸收波长", "λmin", "最小波长"],
            "absorbance_ratio": ["吸光度比", "吸收比", "比值"],
            "specificity_support": ["专属性", "特异性", "干扰", "区分"],
            "uv_method_present": ["UV", "紫外", "分光光度法"],
            "complementary_identification_method": ["IR", "红外", "薄层", "色谱保留行为"],
            "quality_standard_uv_requirement": ["质量标准", "鉴别", "紫外"],
            "uv_method_description": ["操作方法", "测定", "nm"],
            "instrument_or_method_reference": ["分光光度计", "药典", "紫外-可见分光光度法"],
            "result_judgment_expression": ["应在", "出现", "判定", "结果"],
            "sample_weight": ["称取", "精密称取", "mg", "g"],
            "dilution_steps": ["稀释", "摇匀", "加"],
            "final_volume": ["定容至", "mL", "ml"],
            "solvent_or_medium": ["甲醇", "乙醇", "水", "介质", "溶剂"],
        }
        return list(mapping.get(field_name, []))
