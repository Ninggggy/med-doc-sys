from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Set

from agent.agent_backend.llm.client import LLMClient
from agent.agent_backend.llm.errors import LLMExecutionError
from agent.agent_backend.prompts.prompt_envelope_builder import PromptEnvelopeBuilder
from agent.agent_backend.prompts.template_manager import PromptTemplateManager


class TaskQuestionAgent:
    """Build atomic judgment items from section rules and approved evidence."""

    GUIDANCE_MARKERS = (
        "优先寻找",
        "直接适用的法律法规",
        "指导原则",
        "ICH",
        "药典条款原文",
        "原始研究数据",
        "表格",
        "图谱",
        "方法学",
        "稳定性结果",
        "可直接定位到章节原文",
        "二手转述",
        "不要只复述规则原文",
    )

    META_MARKERS = (
        "确认章节",
        "核对资料是否",
        "核对章节结论是否",
        "完成申报所需",
        "是否提供了完成申报所需",
        "当前章节是否",
        "本章节是否",
        "是否合规",
    )

    FACT_LABELS = (
        "药品名称",
        "中文名",
        "英文名",
        "中文化学名",
        "英文化学名",
        "通用名",
        "商品名",
        "CAS号",
        "规格",
        "批号",
        "性状",
        "溶解性",
        "引湿性",
        "熔点",
        "沸点",
        "比旋度",
        "溶液pH",
        "晶型",
        "水合物",
        "溶剂化物",
        "粒度",
        "粒度分布",
    )

    FACT_PATTERNS = (
        ("药品名称", r"(?:^|\\n)\\s*(?:药品名称|Drug\\s*Name)\\s*[:：]\\s*([^\\n\\r]{1,200})"),
        ("中文名", r"(?:^|\\n)\\s*(?:中文名|Chinese\\s*Name)\\s*[:：]\\s*([^\\n\\r]{1,200})"),
        ("英文名", r"(?:^|\\n)\\s*(?:英文名|English\\s*Name)\\s*[:：]\\s*([^\\n\\r]{1,220})"),
        ("中文化学名", r"(?:^|\\n)\\s*(?:中文化学名|Chinese\\s*Chemical\\s*Name)\\s*[:：]\\s*([^\\n\\r]{1,260})"),
        ("英文化学名", r"(?:^|\\n)\\s*(?:英文化学名|English\\s*Chemical\\s*Name)\\s*[:：]\\s*([^\\n\\r]{1,260})"),
        ("通用名", r"(?:^|\\n)\\s*(?:通用名|Generic\\s*Name)\\s*[:：]\\s*([^\\n\\r]{1,200})"),
        ("商品名", r"(?:^|\\n)\\s*(?:商品名|Brand\\s*Name)\\s*[:：]\\s*([^\\n\\r]{1,200})"),
        ("CAS号", r"(?:^|\\n)\\s*(?:CAS(?:号| No\\.?)?)\\s*[:：]\\s*([^\\n\\r]{1,80})"),
        ("规格", r"(?:^|\\n)\\s*(?:规格|Specification)\\s*[:：]\\s*([^\\n\\r]{1,160})"),
        ("批号", r"(?:^|\\n)\\s*(?:批号|Batch(?:\\s*No\\.?)?)\\s*[:：]\\s*([^\\n\\r]{1,160})"),
        ("性状", r"(?:^|\\n)\\s*(?:性状|Appearance)\\s*[:：]\\s*([^\\n\\r]{1,200})"),
        ("溶解性", r"(?:^|\\n)\\s*(?:溶解性|Solubility)\\s*[:：]\\s*([^\\n\\r]{1,200})"),
        ("引湿性", r"(?:^|\\n)\\s*(?:引湿性|Hygroscopicity)\\s*[:：]\\s*([^\\n\\r]{1,200})"),
        ("熔点", r"(?:^|\\n)\\s*(?:熔点|Melting\\s*Point)\\s*[:：]\\s*([^\\n\\r]{1,120})"),
        ("沸点", r"(?:^|\\n)\\s*(?:沸点|Boiling\\s*Point)\\s*[:：]\\s*([^\\n\\r]{1,120})"),
        ("比旋度", r"(?:^|\\n)\\s*(?:比旋度|Specific\\s*Rotation)\\s*[:：]\\s*([^\\n\\r]{1,120})"),
        ("溶液pH", r"(?:^|\\n)\\s*(?:溶液\\s*pH|pH)\\s*[:：]\\s*([^\\n\\r]{1,120})"),
        ("晶型", r"(?:^|\\n)\\s*(?:晶型|Crystal\\s*Form)\\s*[:：]\\s*([^\\n\\r]{1,120})"),
        ("水合物", r"(?:^|\\n)\\s*(?:水合物|Hydrate)\\s*[:：]\\s*([^\\n\\r]{1,120})"),
        ("溶剂化物", r"(?:^|\\n)\\s*(?:溶剂化物|Solvate)\\s*[:：]\\s*([^\\n\\r]{1,120})"),
        ("粒度", r"(?:^|\\n)\\s*(?:粒度|Particle\\s*Size)\\s*[:：]\\s*([^\\n\\r]{1,120})"),
        ("粒度分布", r"(?:^|\\n)\\s*(?:粒度分布|Particle\\s*Size\\s*Distribution)\\s*[:：]\\s*([^\\n\\r]{1,120})"),
    )

    def __init__(self) -> None:
        self.llm = LLMClient()
        self.envelopes = PromptEnvelopeBuilder()
        self.prompts = PromptTemplateManager("pre_review_agent_prompt")

    @staticmethod
    def _safe_text_list(value: Any) -> List[str]:
        if not isinstance(value, list):
            return []
        seen: Set[str] = set()
        out: List[str] = []
        for item in value:
            text = str(item or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            out.append(text)
        return out

    @staticmethod
    def _safe_dict_list(value: Any) -> List[Dict[str, Any]]:
        if not isinstance(value, list):
            return []
        return [dict(item) for item in value if isinstance(item, dict)]

    @staticmethod
    def _safe_dict_map(value: Any) -> Dict[str, Dict[str, Any]]:
        if not isinstance(value, dict):
            return {}
        out: Dict[str, Dict[str, Any]] = {}
        for key, item in value.items():
            key_text = str(key or "").strip()
            if not key_text or not isinstance(item, dict):
                continue
            out[key_text] = dict(item)
        return out

    PHYSICOCHEMICAL_AXIS_PATTERNS = {
        "appearance": {
            "label": "\u6027\u72b6",
            "keywords": ["\u6027\u72b6", "appearance"],
        },
        "hygroscopicity": {
            "label": "\u5f15\u6e7f\u6027",
            "keywords": ["\u5f15\u6e7f\u6027", "\u5438\u6e7f", "hygroscopic"],
        },
        "melting_boiling": {
            "label": "\u7194\u70b9/\u6cb8\u70b9",
            "keywords": ["\u7194\u70b9", "\u6cb8\u70b9", "melting point", "boiling point"],
        },
        "specific_rotation": {
            "label": "\u6bd4\u65cb\u5ea6",
            "keywords": ["\u6bd4\u65cb\u5ea6", "specific rotation"],
        },
        "solubility": {
            "label": "\u6eb6\u89e3\u6027",
            "keywords": ["\u6eb6\u89e3\u6027", "\u6eb6\u89e3\u5ea6", "solubility"],
        },
        "solution_ph": {
            "label": "\u6eb6\u6db2 pH",
            "keywords": ["\u6eb6\u6db2 pH", "\u6eb6\u6db2pH", " solution ph", " pH", "ph value"],
        },
        "crystal_form": {
            "label": "\u6676\u578b",
            "keywords": ["\u6676\u578b", "crystal form"],
        },
        "hydrate_solvate": {
            "label": "\u6c34\u5408\u7269/\u6eb6\u5242\u5316\u7269",
            "keywords": ["\u6c34\u5408\u7269", "\u6eb6\u5242\u5316\u7269", "hydrate", "solvate"],
        },
        "particle_size_distribution": {
            "label": "\u7c92\u5ea6\u53ca\u5206\u5e03",
            "keywords": ["\u7c92\u5ea6", "\u7c92\u5f84", "\u7c92\u5ea6\u53ca\u5206\u5e03", "particle size", "distribution"],
        },
    }

    IMPURITY_AXIS_PATTERNS = {
        "process_basis": {
            "label": "生产工艺",
            "keywords": ["生产工艺", "工艺", "工艺路线", "反应步骤", "工艺信息表"],
        },
        "reaction_mechanism": {
            "label": "反应机理",
            "keywords": ["反应机理", "反应机制", "副反应", "机理"],
        },
        "structure_characteristics": {
            "label": "结构特点",
            "keywords": ["结构特点", "结构特征", "结构相近", "结构相关"],
        },
        "degradation_pathway": {
            "label": "降解途径",
            "keywords": ["降解途径", "降解路径", "强制降解", "降解产物", "稳定性"],
        },
        "multi_batch_data": {
            "label": "多批次数据",
            "keywords": ["多批次", "多批", "批次数据", "批分析", "批检验"],
        },
        "clearance_conversion": {
            "label": "杂质转化/清除情况",
            "keywords": ["转化", "清除", "去除", "清除能力", "转化情况"],
        },
        "reference_comparison": {
            "label": "药典/参比制剂公开信息",
            "keywords": ["药典", "参比制剂", "公开信息", "对照信息", "公开资料"],
        },
        "impurity_profile": {
            "label": "杂质谱分析",
            "keywords": ["杂质谱", "有关物质", "已知杂质", "未知杂质", "降解杂质"],
        },
        "control_strategy": {
            "label": "控制策略",
            "keywords": ["控制策略", "限度", "控制限", "订入标准", "控制要求"],
        },
    }

    @classmethod
    def _canonical_physicochemical_axis(cls, value: str) -> str:
        text = str(value or "").strip().lower()
        if not text:
            return ""
        for axis_code, config in cls.PHYSICOCHEMICAL_AXIS_PATTERNS.items():
            for keyword in config.get("keywords", []):
                if str(keyword).lower() in text:
                    return axis_code
        return ""

    @classmethod
    def _extract_physicochemical_axis_coverage(
        cls,
        raw_text: str,
        facts: Dict[str, str],
        comparison_axes: List[str],
    ) -> Dict[str, Any]:
        source_fragments = [str(raw_text or "")]
        if isinstance(facts, dict):
            source_fragments.extend(
                [f"{str(key or '').strip()}:{str(value or '').strip()}" for key, value in facts.items() if str(value or "").strip()]
            )
        source_text = " ".join(source_fragments).lower()

        required_codes: List[str] = []
        for axis in comparison_axes:
            axis_code = cls._canonical_physicochemical_axis(axis)
            if axis_code and axis_code not in required_codes:
                required_codes.append(axis_code)
        if not required_codes:
            required_codes = list(cls.PHYSICOCHEMICAL_AXIS_PATTERNS.keys())

        covered_codes: List[str] = []
        missing_codes: List[str] = []
        for axis_code in required_codes:
            keywords = [str(item).lower() for item in cls.PHYSICOCHEMICAL_AXIS_PATTERNS.get(axis_code, {}).get("keywords", [])]
            if any(keyword and keyword in source_text for keyword in keywords):
                covered_codes.append(axis_code)
            else:
                missing_codes.append(axis_code)

        covered_axes = [cls.PHYSICOCHEMICAL_AXIS_PATTERNS[axis_code]["label"] for axis_code in covered_codes]
        missing_axes = [cls.PHYSICOCHEMICAL_AXIS_PATTERNS[axis_code]["label"] for axis_code in missing_codes]
        coverage_ratio = round(len(covered_codes) / len(required_codes), 4) if required_codes else 0.0
        return {
            "covered_axes": covered_axes,
            "missing_axes": missing_axes,
            "coverage_ratio": coverage_ratio,
        }

    @classmethod
    def _canonical_impurity_axis(cls, value: str) -> str:
        text = str(value or "").strip().lower()
        if not text:
            return ""
        for axis_code, config in cls.IMPURITY_AXIS_PATTERNS.items():
            for keyword in config.get("keywords", []):
                if str(keyword).lower() in text:
                    return axis_code
        return ""

    @classmethod
    def _extract_impurity_axis_coverage(
        cls,
        raw_text: str,
        facts: Dict[str, str],
        comparison_axes: List[str],
    ) -> Dict[str, Any]:
        source_fragments = [str(raw_text or "")]
        if isinstance(facts, dict):
            source_fragments.extend(
                [f"{str(key or '').strip()}:{str(value or '').strip()}" for key, value in facts.items() if str(value or "").strip()]
            )
        source_text = " ".join(source_fragments).lower()

        required_codes: List[str] = []
        for axis in comparison_axes:
            axis_code = cls._canonical_impurity_axis(axis)
            if axis_code and axis_code not in required_codes:
                required_codes.append(axis_code)
        if not required_codes:
            required_codes = list(cls.IMPURITY_AXIS_PATTERNS.keys())

        covered_codes: List[str] = []
        missing_codes: List[str] = []
        for axis_code in required_codes:
            keywords = [str(item).lower() for item in cls.IMPURITY_AXIS_PATTERNS.get(axis_code, {}).get("keywords", [])]
            if any(keyword and keyword in source_text for keyword in keywords):
                covered_codes.append(axis_code)
            else:
                missing_codes.append(axis_code)

        covered_axes = [cls.IMPURITY_AXIS_PATTERNS[axis_code]["label"] for axis_code in covered_codes]
        missing_axes = [cls.IMPURITY_AXIS_PATTERNS[axis_code]["label"] for axis_code in missing_codes]
        coverage_ratio = round(len(covered_codes) / len(required_codes), 4) if required_codes else 0.0
        return {
            "covered_axes": covered_axes,
            "missing_axes": missing_axes,
            "coverage_ratio": coverage_ratio,
        }

    @staticmethod
    def _compact_text(text: Any, max_len: int = 160) -> str:
        value = re.sub(r"\\s+", " ", str(text or "").strip())
        if not value:
            return ""
        return value if len(value) <= max_len else value[: max_len - 3].rstrip() + "..."

    def _build_llm_execution_meta(
        self,
        *,
        agent_name: str,
        raw_text: Any,
        parsed_payload: Any,
    ) -> Dict[str, Any]:
        used_default_fallback = not isinstance(parsed_payload, dict)
        failure_reason = ""
        if used_default_fallback:
            failure_reason = "empty_response" if not str(raw_text or "").strip() else "json_parse_failed_or_non_object"
        return {
            "agent": agent_name,
            "used_default_fallback": used_default_fallback,
            "used_model_output": not used_default_fallback,
            "json_parse_ok": isinstance(parsed_payload, dict),
            "failure_reason": failure_reason,
            "raw_preview": "",
            "output_chars": len(str(raw_text or '')),
        }

    @staticmethod
    def _material_evidence_id(item: Dict[str, Any]) -> str:
        evidence_id = str(item.get("evidence_id", "") or "").strip()
        if evidence_id:
            return evidence_id
        doc_id = str(item.get("doc_id", "") or "").strip()
        chunk_id = str(item.get("chunk_id", "") or "").strip()
        if doc_id or chunk_id:
            return f"{doc_id}:{chunk_id}"
        return ""

    @staticmethod
    def _normalize_task_code(value: Any, index: int) -> str:
        text = str(value or "").strip()
        return text or f"task_{index}"

    @classmethod
    def _split_fragments(cls, text: Any) -> List[str]:
        value = str(text or "").strip()
        if not value:
            return []
        parts = re.split(r"[；;\n\r]+", value)
        out: List[str] = []
        seen: Set[str] = set()
        for part in parts:
            item = re.sub(r"\s+", " ", str(part or "").strip()).strip("。；;，,：: ")
            if not item or len(item) < 2 or item in seen:
                continue
            seen.add(item)
            out.append(item)
        return out

    @classmethod
    def _is_guidance_fragment(cls, text: Any) -> bool:
        value = str(text or "").strip().lower()
        if not value:
            return False
        return any(marker.lower() in value for marker in cls.GUIDANCE_MARKERS)

    @classmethod
    def _is_meta_requirement_fragment(cls, text: Any) -> bool:
        value = str(text or "").strip()
        if not value:
            return False
        return any(marker in value for marker in cls.META_MARKERS)

    @classmethod
    def _normalize_rule_requirements(cls, values: Any) -> List[str]:
        raw_values = values if isinstance(values, list) else [values]
        out: List[str] = []
        seen: Set[str] = set()
        for raw in raw_values:
            for fragment in cls._split_fragments(raw):
                if cls._is_guidance_fragment(fragment) or cls._is_meta_requirement_fragment(fragment):
                    continue
                if fragment not in seen:
                    seen.add(fragment)
                    out.append(fragment)
        return out

    @classmethod
    def _normalize_evidence_guidance(cls, values: Any) -> List[str]:
        raw_values = values if isinstance(values, list) else [values]
        out: List[str] = []
        seen: Set[str] = set()
        for raw in raw_values:
            for fragment in cls._split_fragments(raw):
                if not cls._is_guidance_fragment(fragment):
                    continue
                if fragment not in seen:
                    seen.add(fragment)
                    out.append(fragment)
        return out

    @classmethod
    def _derive_rule_axes(cls, rule_requirement: str) -> List[str]:
        text = str(rule_requirement or "").strip()
        if not text:
            return []
        text = re.sub(r"^(应结合|应关注|关注|核对|确认|判断|检查|分析|提供|明确|全面分析)", "", text).strip()
        text = text.replace("和/或", "、").replace("及其", "、").replace("以及", "、")
        fragments = re.split(r"[、；;，,\n\r]+", text)
        axes: List[str] = []
        seen: Set[str] = set()
        generic_markers = (
            "应",
            "需",
            "需要",
            "是否",
            "资料",
            "章节",
            "要求",
            "规则",
            "判断",
            "说明",
            "一致",
            "对比",
            "分析",
            "形成",
            "完成",
        )
        for fragment in fragments:
            item = re.sub(r"\s+", " ", str(fragment or "").strip()).strip("。；，,:：")
            if not item or len(item) < 2:
                continue
            if item in seen:
                continue
            if item in generic_markers:
                continue
            if item.startswith("如") and len(item) <= 8:
                continue
            seen.add(item)
            axes.append(item)
        return axes[:8]

    @classmethod
    def _extract_generic_axis_coverage(
        cls,
        raw_text: str,
        facts: Dict[str, str],
        comparison_axes: List[str],
    ) -> Dict[str, Any]:
        source_fragments = [str(raw_text or "")]
        if isinstance(facts, dict):
            source_fragments.extend(
                [f"{str(key or '').strip()}:{str(value or '').strip()}" for key, value in facts.items() if str(value or "").strip()]
            )
        source_text = " ".join(source_fragments).lower()
        covered_axes: List[str] = []
        missing_axes: List[str] = []
        for axis in comparison_axes:
            axis_text = str(axis or "").strip()
            if not axis_text:
                continue
            keywords = [token for token in re.split(r"[、/／\s]+", axis_text) if len(token.strip()) >= 2]
            if not keywords:
                missing_axes.append(axis_text)
                continue
            if any(str(keyword).lower() in source_text for keyword in keywords):
                covered_axes.append(axis_text)
            else:
                missing_axes.append(axis_text)
        total = len(covered_axes) + len(missing_axes)
        coverage_ratio = round(len(covered_axes) / total, 4) if total else 0.0
        return {
            "covered_axes": covered_axes,
            "missing_axes": missing_axes,
            "coverage_ratio": coverage_ratio,
        }

    @classmethod
    def _extract_labeled_facts(cls, raw_text: str) -> Dict[str, str]:
        text = str(raw_text or "").strip()
        if not text:
            return {}
        facts: Dict[str, str] = {}
        for label, pattern in cls.FACT_PATTERNS:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if not match:
                continue
            value = " ".join(str(match.group(1) or "").strip().split())
            if value:
                facts[label] = value
        return facts

    @classmethod
    def _looks_meta_review_object(cls, text: Any) -> bool:
        value = str(text or "").strip()
        if not value:
            return True
        generic_markers = ("确认", "核对", "判断", "检查", "是否", "资料", "章节结论", "当前章节")
        return any(marker in value for marker in generic_markers)

    @classmethod
    def _infer_review_object(
        cls,
        task: Dict[str, Any],
        payload: Dict[str, Any],
        facts: Dict[str, str],
        rule_requirement: str,
    ) -> str:
        candidate = str(task.get("review_object", "") or "").strip()
        if candidate and not cls._looks_meta_review_object(candidate):
            return candidate
        text = " ".join(
            [
                rule_requirement,
                str(task.get("task_question", "") or ""),
                str(payload.get("section_name", "") or ""),
            ]
        )
        if any(keyword in text for keyword in ("中文名", "英文名", "通用名", "商品名", "命名")):
            return "中文名" if facts.get("中文名") else "药品名称"
        if any(keyword in text for keyword in ("理化性质", "性状", "引湿性", "熔点", "沸点", "比旋度", "溶解性", "pH", "晶型", "水合物", "溶剂化物", "粒度")):
            return "理化性质"
        if any(keyword in text for keyword in ("化学结构式", "分子式", "分子量", "立体结构", "结构")):
            return "结构信息"
        if any(keyword in text for keyword in ("杂质", "有关物质", "降解产物")):
            return "杂质控制"
        section_name = str(payload.get("section_name", "") or "").strip()
        return section_name or "当前章节材料"

    @classmethod
    def _build_reference_targets(
        cls,
        task: Dict[str, Any],
        facts: Dict[str, str],
        review_object: str,
    ) -> List[str]:
        explicit = cls._safe_text_list(task.get("reference_targets", []))
        if explicit:
            return explicit[:4]
        targets: List[str] = []
        if review_object == "中文名":
            for label in ("中文名", "英文名", "通用名", "CAS号"):
                value = str(facts.get(label, "") or "").strip()
                if value and value not in targets:
                    targets.append(value)
        elif review_object == "理化性质":
            for label in ("性状", "引湿性", "熔点", "沸点", "比旋度", "溶解性", "溶液pH", "晶型", "水合物", "溶剂化物", "粒度", "粒度分布"):
                value = str(facts.get(label, "") or "").strip()
                if value and value not in targets:
                    targets.append(value)
        elif review_object == "结构信息":
            for label in ("中文化学名", "英文化学名", "CAS号"):
                value = str(facts.get(label, "") or "").strip()
                if value and value not in targets:
                    targets.append(value)
        if review_object and review_object not in targets:
            targets.append(review_object)
        return targets[:4]

    @classmethod
    def _infer_comparison_axes(
        cls,
        review_object: str,
        rule_requirement: str,
        facts: Dict[str, str],
        task: Dict[str, Any],
    ) -> List[str]:
        explicit = cls._safe_text_list(task.get("comparison_axes", []))
        if explicit:
            return explicit[:6]
        derived_axes = cls._derive_rule_axes(rule_requirement)
        if derived_axes:
            return derived_axes[:6]
        axes: List[str] = []
        text = " ".join([review_object, rule_requirement])
        if review_object == "中文名":
            axes.extend(["中文名", "英文名", "通用名", "盐型"])
        elif review_object == "理化性质":
            axes.extend(
                [
                    "性状",
                    "引湿性",
                    "熔点/沸点",
                    "比旋度",
                    "溶解性",
                    "溶液 pH",
                    "晶型/水合物/溶剂化物",
                    "粒度及分布",
                ]
            )
        elif review_object == "结构信息":
            axes.extend(["化学结构式", "分子式", "分子量", "立体结构"])
        elif review_object == "杂质控制":
            axes.extend(
                [
                    "生产工艺",
                    "反应机理",
                    "结构特点",
                    "降解途径",
                    "多批次数据",
                    "杂质转化/清除情况",
                    "药典/参比制剂公开信息",
                    "杂质谱分析",
                    "控制策略",
                ]
            )
        if "对比" in text or "一致" in text:
            axes.append("与药典或参比制剂公开信息对比")
        if "不一致" in text and "说明" in text:
            axes.append("不一致说明")
        deduped: List[str] = []
        for item in axes:
            if item and item not in deduped:
                deduped.append(item)
        if deduped:
            return deduped[:6]
        for label in cls.FACT_LABELS:
            if facts.get(label):
                deduped.append(label)
        return deduped[:6]

    @classmethod
    def _infer_required_rule_types(cls, review_object: str, rule_requirement: str) -> List[str]:
        text = " ".join([review_object, rule_requirement])
        out: List[str] = []
        if any(keyword in text for keyword in ("中文名", "英文名", "通用名", "商品名", "命名")):
            out.extend(["naming_rule", "official_name_reference"])
        if any(keyword in text for keyword in ("理化性质", "性状", "引湿性", "熔点", "沸点", "比旋度", "溶解性", "pH", "晶型", "水合物", "溶剂化物", "粒度")):
            out.extend(["physicochemical_requirement", "reference_comparison"])
        if any(keyword in text for keyword in ("化学结构式", "分子式", "分子量", "立体结构", "结构")):
            out.extend(["structure_requirement", "reference_comparison"])
        if any(keyword in text for keyword in ("杂质", "有关物质", "降解产物", "杂质谱", "控制策略", "转化", "清除")):
            out.extend(["impurity_requirement", "reference_comparison"])
        if not out:
            out.append("rule_conformance")
        deduped: List[str] = []
        for item in out:
            if item not in deduped:
                deduped.append(item)
        return deduped[:4]

    @classmethod
    def _infer_question_type(cls, review_object: str, rule_requirement: str) -> str:
        text = " ".join([review_object, rule_requirement])
        if any(keyword in text for keyword in ("中文名", "英文名", "通用名", "商品名", "命名")):
            return "naming_conformance"
        if any(keyword in text for keyword in ("理化性质", "性状", "引湿性", "熔点", "沸点", "比旋度", "溶解性", "pH", "晶型", "水合物", "溶剂化物", "粒度")):
            return "physicochemical_conformance"
        if any(keyword in text for keyword in ("化学结构式", "分子式", "分子量", "立体结构", "结构")):
            return "structure_conformance"
        if any(keyword in text for keyword in ("杂质", "有关物质", "降解产物", "杂质谱", "控制策略", "转化", "清除")):
            return "impurity_control_conformance"
        return "rule_conformance"

    @classmethod
    def _build_atomic_task_question(
        cls,
        review_object: str,
        rule_requirement: str,
        comparison_axes: List[str],
    ) -> str:
        rule_text = str(rule_requirement or "").strip()
        if review_object == "中文名":
            if "商品名" in rule_text and "通用名" in rule_text:
                return "当前中文名是否使用了未经批准的商品名作为通用名"
            if "对应" in rule_text or "命名原则" in rule_text:
                return "当前中文名是否符合中国药典通用命名原则"
        if review_object == "理化性质":
            if "如不一致" in rule_text and "说明" in rule_text:
                return "如理化性质与药典或参比制剂公开信息不一致，章节是否给出合理说明"
            if "对比" in rule_text:
                return "理化性质结果是否已与参比制剂公开信息或国内外药典标准进行对比"
            if "关注" in rule_text:
                requirement = rule_text.replace("关注", "", 1).strip("。")
                return f"理化性质资料是否覆盖{requirement}"
        if review_object == "结构信息":
            if "一致" in rule_text:
                return "结构信息是否与药典或参比制剂公开信息一致"
        if review_object == "杂质控制":
            if "控制策略" in rule_text or "杂质谱" in rule_text:
                return "杂质资料是否已结合工艺、降解、多批次和对照信息完成杂质谱分析并明确控制策略"
            if "转化" in rule_text or "清除" in rule_text:
                return "杂质资料是否说明杂质来源以及后续转化或清除情况"
            if "参比制剂" in rule_text or "药典" in rule_text:
                return "杂质控制是否已结合药典或参比制剂公开信息完成对比分析"
        if "如不一致" in rule_text and "说明" in rule_text:
            return f"{review_object}如与对照标准不一致，是否给出合理说明"
        if "对比" in rule_text:
            return f"{review_object}是否已完成与对照标准或公开信息的比对"
        if comparison_axes:
            axes_text = "、".join(comparison_axes[:4])
            return f"章节资料是否已围绕{axes_text}等关键维度，对{review_object}形成直接支撑和规则比对"
        return f"当前章节中，{review_object}是否满足“{rule_text}”要求"

    @classmethod
    def _looks_meta_task_question(cls, text: Any) -> bool:
        value = str(text or "").strip()
        if not value:
            return True
        meta_markers = ("确认章节", "核对资料", "核对章节结论", "完成申报所需", "当前章节中")
        return any(marker in value for marker in meta_markers)

    @classmethod
    def _build_seed_review_tasks(cls, payload: Dict[str, Any], facts: Dict[str, str]) -> List[Dict[str, Any]]:
        review_tasks = cls._safe_dict_list(payload.get("review_tasks", []))
        if review_tasks:
            return review_tasks
        section_review_profile = payload.get("section_review_profile", {}) if isinstance(payload.get("section_review_profile", {}), dict) else {}
        section_rules = cls._normalize_rule_requirements(payload.get("section_rules", []))
        focus_points = cls._safe_text_list(payload.get("focus_points", []))
        must_answer_questions = cls._safe_text_list(section_review_profile.get("must_answer_questions", []))
        core_review_principles = cls._normalize_rule_requirements(section_review_profile.get("core_review_principles", []))
        chapter_role = str(section_review_profile.get("chapter_role", "") or "").strip()
        core_review_question = str(section_review_profile.get("core_review_question", "") or "").strip()
        if not section_rules:
            section_name = str(payload.get("section_name", "") or "").strip()
            section_rules = core_review_principles[:4] or focus_points[:4] or ([section_name] if section_name else [])
        out: List[Dict[str, Any]] = []
        if must_answer_questions:
            default_rule_requirement = section_rules[0] if section_rules else (core_review_question or chapter_role)
            for index, question in enumerate(must_answer_questions[:6], start=1):
                question_text = str(question or "").strip()
                if not question_text:
                    continue
                review_object = cls._infer_review_object({}, payload, facts, question_text or default_rule_requirement)
                comparison_axes = cls._infer_comparison_axes(review_object, question_text or default_rule_requirement, facts, {})
                out.append(
                    {
                        "task_code": f"profile_task_{index}",
                        "review_object": review_object,
                        "rule_targets": [default_rule_requirement] if default_rule_requirement else [],
                        "comparison_axes": comparison_axes,
                        "required_rule_types": cls._infer_required_rule_types(review_object, default_rule_requirement or question_text),
                        "reference_targets": cls._build_reference_targets({}, facts, review_object),
                        "expected_evidence": ["规则依据", "对照标准", "章节事实"],
                        "completion_criteria": ["存在单条规则依据", "存在对照标准或公开信息", "存在可比对章节事实"],
                        "task_question": question_text,
                    }
                )
        if out:
            return out
        for index, rule_requirement in enumerate(section_rules[:6], start=1):
            review_object = cls._infer_review_object({}, payload, facts, rule_requirement)
            comparison_axes = cls._infer_comparison_axes(review_object, rule_requirement, facts, {})
            out.append(
                {
                    "task_code": f"task_{index}",
                    "review_object": review_object,
                    "rule_targets": [rule_requirement],
                    "comparison_axes": comparison_axes,
                    "required_rule_types": cls._infer_required_rule_types(review_object, rule_requirement),
                    "reference_targets": cls._build_reference_targets({}, facts, review_object),
                    "expected_evidence": ["规则依据", "对照标准", "章节事实"],
                    "completion_criteria": ["存在单条规则依据", "存在对照标准或公开信息", "存在可比对章节事实"],
                    "task_question": cls._build_atomic_task_question(review_object, rule_requirement, comparison_axes),
                }
            )
        return out

    def _build_fallback_questions(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        section_id = str(payload.get("section_id", "") or "").strip()
        raw_text = str(payload.get("raw_text", "") or "")
        facts = self._extract_labeled_facts(raw_text)
        review_tasks = self._build_seed_review_tasks(payload, facts)
        retrieval_result = self._safe_dict_map(payload.get("retrieval_evaluation_result", {}))
        coverage_by_task = self._safe_dict_map(retrieval_result.get("coverage_by_task", {}))
        evidence_bundles_by_task = self._safe_dict_map(retrieval_result.get("evidence_bundles_by_task", {}))
        missing_rows = self._safe_dict_list(retrieval_result.get("missing_evidence_by_task", []))
        missing_by_task = {
            str(item.get("task_code", "") or "").strip(): self._safe_text_list(item.get("missing_information", []))
            for item in missing_rows
            if str(item.get("task_code", "") or "").strip()
        }
        approved_materials = self._safe_dict_list(payload.get("retrieved_materials", []))
        fallback_evidence_ids = [
            self._material_evidence_id(item)
            for item in approved_materials
            if self._material_evidence_id(item)
        ][:4]

        task_questions: List[Dict[str, Any]] = []
        global_missing: List[str] = []

        for task_index, task in enumerate(review_tasks[:8], start=1):
            base_task_code = self._normalize_task_code(task.get("task_code", ""), task_index)
            rule_requirements = self._normalize_rule_requirements(
                self._safe_text_list(task.get("rule_targets", [])) or self._safe_text_list(task.get("judgment_basis", []))
            )
            if not rule_requirements:
                fallback_requirement = str(task.get("task_question", "") or task.get("review_object", "") or "").strip()
                if fallback_requirement:
                    rule_requirements = [fallback_requirement]

            coverage = coverage_by_task.get(base_task_code, {})
            evidence_bundle = evidence_bundles_by_task.get(base_task_code, {})
            base_supporting = self._safe_text_list(coverage.get("approved_evidence_ids", [])) or self._safe_text_list(
                evidence_bundle.get("supporting_evidence_ids", [])
            ) or list(fallback_evidence_ids)
            base_rule_evidence = self._safe_text_list(coverage.get("rule_evidence_ids", [])) or self._safe_text_list(
                evidence_bundle.get("rule_evidence_ids", [])
            )
            base_reference_evidence = self._safe_text_list(coverage.get("reference_evidence_ids", [])) or self._safe_text_list(
                evidence_bundle.get("reference_evidence_ids", [])
            )
            base_fact_evidence = self._safe_text_list(coverage.get("fact_evidence_ids", [])) or self._safe_text_list(
                evidence_bundle.get("fact_evidence_ids", [])
            )
            base_experience_evidence = self._safe_text_list(coverage.get("experience_evidence_ids", [])) or self._safe_text_list(
                evidence_bundle.get("experience_evidence_ids", [])
            )
            base_missing = self._safe_text_list(missing_by_task.get(base_task_code, []))

            for rule_index, rule_requirement in enumerate(rule_requirements[:4], start=1):
                task_code = base_task_code if len(rule_requirements) == 1 else f"{base_task_code}__{rule_index}"
                review_object = self._infer_review_object(task, payload, facts, rule_requirement)
                comparison_axes = self._infer_comparison_axes(review_object, rule_requirement, facts, task)
                required_rule_types = self._safe_text_list(task.get("required_rule_types", [])) or self._infer_required_rule_types(
                    review_object, rule_requirement
                )
                comparison_targets = self._build_reference_targets(task, facts, review_object)
                question_type = self._infer_question_type(review_object, rule_requirement)
                explicit_task_question = str(task.get("task_question", "") or "").strip()
                if explicit_task_question and not self._looks_meta_task_question(explicit_task_question):
                    task_question = explicit_task_question
                else:
                    task_question = self._build_atomic_task_question(review_object, rule_requirement, comparison_axes)
                axis_coverage = {"covered_axes": [], "missing_axes": [], "coverage_ratio": 0.0}
                if question_type == "physicochemical_conformance":
                    axis_coverage = self._extract_physicochemical_axis_coverage(raw_text, facts, comparison_axes)
                elif question_type == "impurity_control_conformance":
                    axis_coverage = self._extract_impurity_axis_coverage(raw_text, facts, comparison_axes)
                elif comparison_axes:
                    axis_coverage = self._extract_generic_axis_coverage(raw_text, facts, comparison_axes)
                missing_information = base_missing[:4]
                if axis_coverage["missing_axes"]:
                    for axis_label in axis_coverage["missing_axes"][:4]:
                        missing_entry = f"\u672a\u89c1{axis_label}\u76f8\u5173\u4fe1\u606f\u6216\u76f4\u63a5\u652f\u6491"
                        if missing_entry not in missing_information:
                            missing_information.append(missing_entry)
                if not base_supporting and not missing_information:
                    missing_information = self._safe_text_list(task.get("expected_evidence", []))[:3] or ["缺少直接支撑该判断项的规则依据或章节事实"]
                global_missing.extend(missing_information)

                task_questions.append(
                    {
                        "task_code": task_code,
                        "question_type": question_type,
                        "task_question": task_question,
                        "review_object": review_object,
                        "judgment_basis": [rule_requirement],
                        "required_rule_types": required_rule_types[:4],
                        "comparison_axes": comparison_axes[:6],
                        "comparison_targets": comparison_targets[:6],
                        "supporting_evidence_ids": base_supporting[:4],
                        "rule_evidence_ids": base_rule_evidence[:4],
                        "reference_evidence_ids": base_reference_evidence[:4],
                        "fact_evidence_ids": base_fact_evidence[:4],
                        "experience_evidence_ids": base_experience_evidence[:4],
                        "covered_axes": self._safe_text_list(axis_coverage.get("covered_axes", []))[:6],
                        "missing_axes": self._safe_text_list(axis_coverage.get("missing_axes", []))[:6],
                        "coverage_ratio": axis_coverage.get("coverage_ratio", 0.0),
                        "missing_information": missing_information,
                        "judgment_ready": bool(base_supporting) and not bool(axis_coverage.get("missing_axes", [])),
                    }
                )

        return {
            "section_id": section_id,
            "judgment_ready": bool(task_questions) and all(bool(item.get("judgment_ready", False)) for item in task_questions),
            "task_questions": task_questions,
            "reasoning_plan": [
                "先区分决策规则、检索引导和元任务，只把决策规则展开成判断项。",
                "每个判断项只保留一条核心规则要求，并绑定当前任务真正使用到的证据。",
                "后续 reviewer 只围绕这些原子判断项完成规则、事实和证据对比判断。",
            ],
            "missing_information": self._safe_text_list(global_missing),
        }

    def _build_prompt_payload(self, payload: Dict[str, Any], fallback_data: Dict[str, Any]) -> Dict[str, Any]:
        section_rules = self._safe_text_list(payload.get("section_rules", []))
        evidence_requirements = self._safe_text_list(payload.get("evidence_requirements", []))
        retrieval_result = self._safe_dict_map(payload.get("retrieval_evaluation_result", {}))
        section_review_profile = payload.get("section_review_profile", {}) if isinstance(payload.get("section_review_profile", {}), dict) else {}
        task_definition = payload.get("task_definition", {}) if isinstance(payload.get("task_definition", {}), dict) else {}
        return {
            "task_id": payload.get("task_id", ""),
            "section_id": payload.get("section_id", ""),
            "section_name": payload.get("section_name", ""),
            "raw_text": payload.get("raw_text", ""),
            "task_definition": {
                "chapter_role": str(task_definition.get("chapter_role", "") or "").strip(),
                "core_review_question": str(task_definition.get("core_review_question", "") or task_definition.get("review_goal", "") or "").strip(),
                "must_answer_questions": self._safe_text_list(task_definition.get("must_answer_questions", [])),
                "reasoning_principles": self._safe_text_list(task_definition.get("reasoning_principles", [])),
                "common_risks": self._safe_text_list(task_definition.get("common_risks", [])),
                "reviewer_mindset": self._safe_text_list(task_definition.get("reviewer_mindset", []))[:5],
                "task_generation_rules": self._safe_text_list(task_definition.get("task_generation_rules", []))[:4],
            },
            "section_review_profile": {
                "chapter_role": str(section_review_profile.get("chapter_role", "") or "").strip(),
                "core_review_question": str(section_review_profile.get("core_review_question", "") or "").strip(),
                "must_answer_questions": self._safe_text_list(section_review_profile.get("must_answer_questions", [])),
                "core_review_principles": self._safe_text_list(section_review_profile.get("core_review_principles", [])),
                "common_risks": self._safe_text_list(section_review_profile.get("common_risks", [])),
                "reviewer_mindset": self._safe_text_list(section_review_profile.get("reviewer_mindset", []))[:5],
                "task_generation_rules": self._safe_text_list(section_review_profile.get("task_generation_rules", []))[:4],
            },
            "facts": self._extract_labeled_facts(str(payload.get("raw_text", "") or "")),
            "decision_rules": self._normalize_rule_requirements(section_rules),
            "evidence_guidance": self._normalize_evidence_guidance(section_rules + evidence_requirements),
            "task_questions": [
                {
                    "task_code": item.get("task_code", ""),
                    "task_question": item.get("task_question", ""),
                    "review_object": item.get("review_object", ""),
                    "judgment_basis": item.get("judgment_basis", []),
                    "comparison_axes": item.get("comparison_axes", []),
                    "comparison_targets": item.get("comparison_targets", []),
                    "covered_axes": item.get("covered_axes", []),
                    "missing_axes": item.get("missing_axes", []),
                    "coverage_ratio": item.get("coverage_ratio", 0.0),
                    "supporting_evidence_ids": item.get("supporting_evidence_ids", []),
                    "missing_information": item.get("missing_information", []),
                }
                for item in fallback_data.get("task_questions", [])
            ],
            "coverage_by_task": retrieval_result.get("coverage_by_task", {}),
            "evidence_bundles_by_task": retrieval_result.get("evidence_bundles_by_task", {}),
            "missing_evidence_by_task": retrieval_result.get("missing_evidence_by_task", []),
        }

    def _merge_llm_questions(
        self,
        value: Any,
        fallback_data: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        fallback_rows = self._safe_dict_list(fallback_data.get("task_questions", []))
        fallback_map = {
            str(item.get("task_code", "") or "").strip(): item
            for item in fallback_rows
            if str(item.get("task_code", "") or "").strip()
        }
        rows = self._safe_dict_list(value)
        if not rows:
            return fallback_rows

        out: List[Dict[str, Any]] = []
        seen: Set[str] = set()
        used_codes: Set[str] = set()

        for index, item in enumerate(rows, start=1):
            task_code = self._normalize_task_code(item.get("task_code", ""), index)
            seed = fallback_map.get(task_code, {})
            merged = {
                "task_code": task_code,
                "question_type": str(item.get("question_type", "") or seed.get("question_type", "") or "").strip()
                or str(seed.get("question_type", "") or "rule_conformance"),
                "task_question": str(item.get("task_question", "") or seed.get("task_question", "") or "").strip(),
                "review_object": str(item.get("review_object", "") or seed.get("review_object", "") or "").strip(),
                "judgment_basis": self._normalize_rule_requirements(item.get("judgment_basis", []))
                or self._safe_text_list(seed.get("judgment_basis", [])),
                "required_rule_types": self._safe_text_list(item.get("required_rule_types", []))
                or self._safe_text_list(seed.get("required_rule_types", [])),
                "comparison_axes": self._safe_text_list(item.get("comparison_axes", []))
                or self._safe_text_list(seed.get("comparison_axes", [])),
                "comparison_targets": self._safe_text_list(item.get("comparison_targets", []))
                or self._safe_text_list(seed.get("comparison_targets", [])),
                "supporting_evidence_ids": self._safe_text_list(item.get("supporting_evidence_ids", []))
                or self._safe_text_list(seed.get("supporting_evidence_ids", [])),
                "rule_evidence_ids": self._safe_text_list(item.get("rule_evidence_ids", []))
                or self._safe_text_list(seed.get("rule_evidence_ids", [])),
                "reference_evidence_ids": self._safe_text_list(item.get("reference_evidence_ids", []))
                or self._safe_text_list(seed.get("reference_evidence_ids", [])),
                "fact_evidence_ids": self._safe_text_list(item.get("fact_evidence_ids", []))
                or self._safe_text_list(seed.get("fact_evidence_ids", [])),
                "experience_evidence_ids": self._safe_text_list(item.get("experience_evidence_ids", []))
                or self._safe_text_list(seed.get("experience_evidence_ids", [])),
                "covered_axes": self._safe_text_list(item.get("covered_axes", []))
                or self._safe_text_list(seed.get("covered_axes", [])),
                "missing_axes": self._safe_text_list(item.get("missing_axes", []))
                or self._safe_text_list(seed.get("missing_axes", [])),
                "coverage_ratio": item.get("coverage_ratio", seed.get("coverage_ratio", 0.0)),
                "missing_information": self._safe_text_list(item.get("missing_information", []))
                or self._safe_text_list(seed.get("missing_information", [])),
                "judgment_ready": bool(item.get("judgment_ready", seed.get("judgment_ready", False))),
            }
            if merged["judgment_basis"]:
                merged["judgment_basis"] = merged["judgment_basis"][:1]
            signature = "|".join(
                [
                    merged["task_code"],
                    self._compact_text(merged["task_question"], max_len=96),
                    self._compact_text("；".join(merged["judgment_basis"]), max_len=96),
                ]
            )
            if signature in seen:
                continue
            seen.add(signature)
            out.append(merged)
            used_codes.add(task_code)

        for item in fallback_rows:
            task_code = str(item.get("task_code", "") or "").strip()
            if not task_code or task_code in used_codes:
                continue
            out.append(item)
        return out or fallback_rows

    def describe(self) -> Dict[str, Any]:
        return {
            "name": "task_question",
            "inputs": [
                "task_id",
                "section_id",
                "section_name",
                "raw_text",
                "review_tasks",
                "retrieved_materials",
                "retrieval_evaluation_result",
                "section_rules",
                "task_definition",
                "section_review_profile",
            ],
            "outputs": [
                "task_questions",
                "judgment_ready",
                "missing_information",
                "reasoning_plan",
                "question_type",
            ],
        }

    def build_questions(self, payload: Dict[str, Any], prompt_config: Dict[str, Any] | None = None) -> Dict[str, Any]:
        default_data = self._build_fallback_questions(payload)
        prompt_payload = self._build_prompt_payload(payload, default_data)
        prompt = self.prompts.render("task_question.j2", prompt_payload, prompt_config=prompt_config or {})
        raw = self.llm.chat(
            messages=self.envelopes.build("task_question", prompt),
            default=json.dumps(default_data, ensure_ascii=False),
        )
        parsed = self.llm.extract_json(raw)
        if not isinstance(parsed, dict):
            raise LLMExecutionError('model_invalid_json', stage='task_question')
        if (not isinstance(parsed.get('task_questions'), list)
                or type(parsed.get('judgment_ready')) is not bool
                or any(not isinstance(parsed.get(key), list) or any(not isinstance(v, str) for v in parsed[key])
                       for key in ('reasoning_plan', 'missing_information'))
                or any(not isinstance(question, dict)
                       or any(not isinstance(question.get(key), str) or not question[key].strip()
                              for key in ('task_code', 'task_question', 'review_object'))
                       or type(question.get('judgment_ready')) is not bool
                       for question in parsed['task_questions'])):
            raise LLMExecutionError('model_invalid_output', stage='task_question')
        llm_execution = self._build_llm_execution_meta(
            agent_name="task_question",
            raw_text=raw,
            parsed_payload=parsed,
        )
        data = parsed
        data["section_id"] = str(data.get("section_id", payload.get("section_id", "")) or "").strip()
        data["task_questions"] = self._merge_llm_questions(data.get("task_questions", []), default_data)
        data["reasoning_plan"] = self._safe_text_list(data.get("reasoning_plan", [])) or list(default_data.get("reasoning_plan", []))
        data["missing_information"] = self._safe_text_list(data.get("missing_information", []))
        if not data["missing_information"]:
            missing_pool: List[str] = []
            for item in data["task_questions"]:
                missing_pool.extend(self._safe_text_list(item.get("missing_information", [])))
            data["missing_information"] = self._safe_text_list(missing_pool)
        data["judgment_ready"] = bool(data["task_questions"]) and all(bool(item.get("judgment_ready", False)) for item in data["task_questions"])
        data["llm_execution"] = llm_execution
        return data
