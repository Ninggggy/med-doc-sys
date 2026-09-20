from __future__ import annotations

import json
import re
from typing import Any, Dict, List

from agent.agent_backend.llm.client import LLMClient
from agent.agent_backend.llm.review_output_validation import validate_plan_output, validate_review_output
from agent.agent_backend.prompts.prompt_envelope_builder import PromptEnvelopeBuilder
from agent.agent_backend.prompts.template_manager import PromptTemplateManager


class PlannerReviewerAgent:
    """Unified runtime agent for chapter planning and review."""

    def __init__(self) -> None:
        self.llm = LLMClient()
        self.envelopes = PromptEnvelopeBuilder()
        self.prompts = PromptTemplateManager("pre_review_agent_prompt")

    def describe(self) -> Dict[str, Any]:
        return {
            "name": "planner_reviewer",
            "modes": ["plan", "review"],
            "inputs": [
                "task_id",
                "section_id",
                "section_name",
                "registration_class",
                "review_domain",
                "product_type",
                "raw_text",
                "focus_points",
                "section_rules",
                "task_definition",
                "section_review_profile",
                "compliance_targets",
                "issue_hypotheses",
                "evidence_requirements",
                "output_requirements",
                "medical_entity_requirements",
                "medical_data_requirements",
                "reference_examples",
                "historical_experience",
                "retrieved_materials",
                "review_tasks",
                "task_questions",
            ],
            "outputs": [
                "planner_result",
                "review_result",
                "task_verdicts",
                "reasoning_chain_items",
                "medical_key_entities",
                "medical_key_data_points",
            ],
        }

    @staticmethod
    def _safe_list(value: Any) -> List[str]:
        if not isinstance(value, list):
            return []
        out: List[str] = []
        seen = set()
        for item in value:
            text = str(item or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            out.append(text)
        return out

    @staticmethod
    def _compact_text(text: str, max_len: int = 96) -> str:
        value = re.sub(r"\s+", " ", str(text or "").strip())
        if not value:
            return ""
        if len(value) <= max_len:
            return value
        for sep in ["；", ";", "。", ".", "，", ",", " "]:
            idx = value.find(sep)
            if 0 < idx <= max_len:
                value = value[:idx]
                break
        return value[:max_len].strip(" ,;，。")

    @staticmethod
    def _split_reference_tokens(value: Any) -> List[str]:
        raw_items = value if isinstance(value, list) else [value]
        tokens: List[str] = []
        seen = set()
        for raw in raw_items:
            for fragment in re.split(r"[ï¼›;ï¼Œ,\n\r]+", str(raw or "").strip()):
                text = str(fragment or "").strip()
                if not text or text in seen:
                    continue
                seen.add(text)
                tokens.append(text)
        return tokens

    @staticmethod
    def _looks_internal_reference(text: str) -> bool:
        value = str(text or "").strip()
        if not value:
            return False
        if re.match(r"^[0-9a-f]{8,}:[A-Za-z0-9._-]+$", value, flags=re.IGNORECASE):
            return True
        if re.match(r"^[A-Za-z_]+:[A-Za-z0-9._-]+$", value):
            return True
        if re.match(r"^(?:pharm|rag|guidance|rule|ich|cp|doc|ref|meta|experience):", value, flags=re.IGNORECASE):
            return True
        return False

    def _material_display_name(self, item: Dict[str, Any]) -> str:
        if not isinstance(item, dict):
            return ""
        for key in ["display_name", "file_name_zh", "file_name", "doc_title", "title", "doc_id", "evidence_id"]:
            text = self._compact_text(str(item.get(key, "") or "").strip(), max_len=96)
            if text:
                return text
        return ""

    def _build_material_label_lookup(self, materials: List[Dict[str, Any]]) -> Dict[str, str]:
        lookup: Dict[str, str] = {}
        for item in materials if isinstance(materials, list) else []:
            if not isinstance(item, dict):
                continue
            label = self._material_display_name(item)
            if not label:
                continue
            source_type = str(item.get("source_type", "") or "").strip()
            doc_id = str(item.get("doc_id", "") or "").strip()
            chunk_id = str(item.get("chunk_id", "") or "").strip()
            title = str(item.get("title", "") or "").strip()
            aliases = [
                str(item.get("evidence_id", "") or "").strip(),
                str(item.get("display_name", "") or "").strip(),
                str(item.get("file_name_zh", "") or "").strip(),
                str(item.get("file_name", "") or "").strip(),
                str(item.get("doc_title", "") or "").strip(),
                title,
                doc_id,
            ]
            if source_type and title:
                aliases.append(f"{source_type}:{title}")
            if doc_id and chunk_id:
                aliases.append(f"{doc_id}:{chunk_id}")
            for alias in aliases:
                alias = str(alias or "").strip()
                if not alias:
                    continue
                lookup.setdefault(alias, label)
                if ":" in alias:
                    suffix = alias.split(":", 1)[1].strip()
                    if suffix:
                        lookup.setdefault(suffix, label)
        return lookup

    def _resolve_evidence_labels(
        self,
        references: Any,
        materials: List[Dict[str, Any]],
        *,
        limit: int = 4,
        allow_unmapped_text: bool = True,
    ) -> List[str]:
        lookup = self._build_material_label_lookup(materials)
        labels: List[str] = []
        seen = set()
        for token in self._split_reference_tokens(references):
            label = lookup.get(token, "")
            if not label and ":" in token:
                label = lookup.get(token.split(":", 1)[1].strip(), "")
            if not label and allow_unmapped_text and not self._looks_internal_reference(token):
                label = self._compact_text(token, max_len=96)
            if not label or label in seen:
                continue
            seen.add(label)
            labels.append(label)
            if len(labels) >= limit:
                break
        return labels

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
    def _extract_labeled_facts(raw_text: str) -> Dict[str, str]:
        text = str(raw_text or "").strip()
        if not text:
            return {}
        patterns = [
            ("药品名称", r"(?:^|\n)\s*药品名称\s*[:：]\s*([^\n\r]{1,200})"),
            ("中文名", r"(?:^|\n)\s*中文名(?:称)?\s*[:：]\s*([^\n\r]{1,200})"),
            ("英文名", r"(?:^|\n)\s*英文名(?:称)?\s*[:：]\s*([^\n\r]{1,240})"),
            ("中文化学名", r"(?:^|\n)\s*中文化学名\s*[:：]\s*([^\n\r]{1,240})"),
            ("英文化学名", r"(?:^|\n)\s*英文化学名\s*[:：]\s*([^\n\r]{1,240})"),
            ("通用名", r"(?:^|\n)\s*通用名\s*[:：]\s*([^\n\r]{1,200})"),
            ("商品名", r"(?:^|\n)\s*商品名\s*[:：]\s*([^\n\r]{1,200})"),
            ("CAS号", r"(?:^|\n)\s*CAS\s*(?:号|No\.?)?\s*[:：]\s*([^\n\r]{1,80})"),
            ("规格", r"(?:^|\n)\s*规格\s*[:：]\s*([^\n\r]{1,120})"),
            ("批号", r"(?:^|\n)\s*批号\s*[:：]\s*([^\n\r]{1,120})"),
        ]
        facts: Dict[str, str] = {}
        for label, pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if not match:
                continue
            value = re.sub(r"\s+", " ", str(match.group(1) or "").strip())
            if value:
                facts[label] = value
        return facts

    @staticmethod
    def _looks_generic_task_question(text: str) -> bool:
        value = str(text or "").strip()
        if not value:
            return True
        generic_markers = ["当前章节是否满足", "相关要求", "当前任务", "是否合规"]
        return any(marker in value for marker in generic_markers)

    @staticmethod
    def _looks_meta_review_object(text: str) -> bool:
        value = str(text or "").strip()
        if not value:
            return True
        meta_markers = ["确认", "核对", "判断", "检查", "是否", "资料", "章节结论", "结构"]
        return any(marker in value for marker in meta_markers)

    @classmethod
    def _build_explicit_task_question(
        cls,
        *,
        task_question: str = "",
        review_object: str = "",
        rule_targets: List[str] | None = None,
        comparison_axes: List[str] | None = None,
        facts: Dict[str, str] | None = None,
    ) -> str:
        if task_question and not cls._looks_generic_task_question(task_question):
            return str(task_question).strip()
        rule_text = "；".join(cls._safe_list(rule_targets or [])[:2])
        object_text = str(review_object or "").strip()
        comparison_text = "、".join(cls._safe_list(comparison_axes or [])[:3])
        fact_labels = [
            label
            for label in ["药品名称", "中文名", "英文名", "中文化学名", "英文化学名", "通用名", "商品名", "CAS号", "规格", "批号"]
            if isinstance(facts, dict) and str(facts.get(label, "") or "").strip()
        ]
        if not object_text:
            if "中文名" in fact_labels:
                object_text = "中文名命名规范"
            elif "药品名称" in fact_labels:
                object_text = "药品名称规范"
            elif fact_labels:
                object_text = "当前申报对象"
        if rule_text and object_text and fact_labels:
            return f"需要依据“{rule_text}”判断章节中的{'、'.join(fact_labels[:3])}是否能够支持“{object_text}”吗？"
        if rule_text and object_text and comparison_text:
            return f"需要依据“{rule_text}”判断“{object_text}”在{comparison_text}这些维度上是否符合要求吗？"
        if rule_text and object_text:
            return f"需要依据“{rule_text}”判断“{object_text}”是否符合要求吗？"
        if object_text and comparison_text:
            return f"需要根据{comparison_text}这些维度判断“{object_text}”是否成立吗？"
        if object_text:
            return f"需要判断“{object_text}”是否满足当前章节要求吗？"
        return str(task_question or "需要判断当前章节是否满足相关要求吗？").strip()

    @classmethod
    def _build_task_statement(
        cls,
        *,
        status: str,
        review_object: str = "",
        task_question: str = "",
        rule_requirement: str = "",
        facts: Dict[str, str] | None = None,
    ) -> str:
        requirement = cls._compact_text(rule_requirement or "当前审评要求", max_len=48)
        fact_subject = ""
        if isinstance(facts, dict):
            if "中文名" in facts and any(keyword in (review_object + task_question) for keyword in ["名称", "命名", "中文名", "通用名", "商品名"]):
                fact_subject = f"中文名“{facts['中文名']}”"
            elif "药品名称" in facts and any(keyword in (review_object + task_question) for keyword in ["名称", "命名"]):
                fact_subject = f"药品名称“{facts['药品名称']}”"
            elif "规格" in facts and "规格" in (review_object + task_question):
                fact_subject = f"规格“{facts['规格']}”"
        subject = fact_subject or (f"“{review_object}”" if str(review_object or "").strip() else "当前审评对象")
        if status == "supported":
            return f"{subject}满足“{requirement}”"
        if status == "unsupported":
            return f"{subject}不满足“{requirement}”"
        return f"当前无法判断{subject}是否满足“{requirement}”"

    @staticmethod
    def _is_naming_task(review_object: str = "", task_question: str = "", rule_requirement: str = "") -> bool:
        text = " ".join([str(review_object or ""), str(task_question or ""), str(rule_requirement or "")])
        return any(keyword in text for keyword in ["命名", "中文名", "英文名", "通用名", "商品名", "药品名称"])

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        try:
            if value is None or value == "":
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _format_axis_text(values: Any, limit: int = 6) -> str:
        axis_values: List[str] = []
        seen = set()
        for item in values if isinstance(values, list) else []:
            text = str(item or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            axis_values.append(text)
        return "、".join(axis_values[:limit])

    def _build_task_comparison(
        self,
        *,
        status: str,
        covered_axes: List[str] | None = None,
        missing_axes: List[str] | None = None,
        coverage_ratio: Any = None,
    ) -> str:
        covered_text = self._format_axis_text(covered_axes or [])
        missing_text = self._format_axis_text(missing_axes or [])
        ratio = self._safe_float(coverage_ratio)
        ratio_text = ""
        if ratio is not None:
            ratio_text = f"已覆盖约 {int(round(max(0.0, min(1.0, ratio)) * 100))}% 的判断维度。"

        if covered_text or missing_text:
            if status == "supported":
                segments = []
                if covered_text:
                    segments.append(f"章节已见{covered_text}等关键信息")
                if ratio_text:
                    segments.append(ratio_text)
                segments.append("当前覆盖维度能够支撑本条判断。")
                return "，".join(segment.strip("。") for segment in segments if segment)
            segments = []
            if covered_text:
                segments.append(f"章节目前仅见{covered_text}")
            if missing_text:
                segments.append(f"但未见{missing_text}")
            if ratio_text:
                segments.append(ratio_text)
            segments.append("因此目前仅能形成部分覆盖，不足以直接得出“已满足”结论。")
            return "，".join(segment.strip("。") for segment in segments if segment)

        return "当前任务的规则要求、章节事实与证据支持尚未形成具体的维度化对比结果。"

    def _build_task_reason(
        self,
        *,
        status: str,
        task_question: str,
        review_object: str,
        rule_requirement: str,
        facts: Dict[str, str],
        missing_information: List[str],
        evidence_support: str,
        covered_axes: List[str] | None = None,
        missing_axes: List[str] | None = None,
        coverage_ratio: Any = None,
    ) -> str:
        covered_text = self._format_axis_text(covered_axes or [])
        missing_text = self._format_axis_text(missing_axes or [])
        if status == "supported":
            if self._is_naming_task(review_object, task_question, rule_requirement) and facts.get("中文名") and facts.get("英文名"):
                chinese_name = facts.get("中文名", "")
                english_name = facts.get("英文名", "")
                return (
                    f"章节原文记载中文名“{chinese_name}”和英文名“{english_name}”，"
                    f"结合“{rule_requirement or '相关命名要求'}”及现有证据“{evidence_support or '已检索到的规则/标准资料'}”，"
                    "当前未发现该命名与规则要求直接冲突。"
                )
            if covered_text:
                if evidence_support:
                    return f"章节已提供{covered_text}等信息，并能结合证据“{evidence_support}”完成当前规则判断。"
                return f"章节已提供{covered_text}等信息，当前能够直接支撑“{rule_requirement or '相关要求'}”的判断。"
            fact_summary = self._summarize_material_facts(facts, "")
            if fact_summary and evidence_support:
                return (
                    f"章节事实“{fact_summary}”与证据“{evidence_support}”能够共同支持当前判断，"
                    f"未发现其与“{rule_requirement or '相关要求'}”直接冲突。"
                )
            if fact_summary:
                return f"章节事实“{fact_summary}”能够支持当前判断，未发现其与“{rule_requirement or '相关要求'}”直接冲突。"
            if evidence_support:
                return f"结合证据“{evidence_support}”，当前未发现违反“{rule_requirement or '相关要求'}”的直接证据。"
            return f"当前未发现违反“{rule_requirement or '相关要求'}”的直接证据。"
        if status == "unsupported":
            return f"现有证据显示当前任务与“{rule_requirement or '相关要求'}”存在直接不匹配。"
        if covered_text or missing_text:
            if covered_text and missing_text:
                return f"章节目前仅见{covered_text}，但未见{missing_text}相关信息或直接支撑，因此不足以判断已覆盖主要审评维度。"
            if missing_text:
                return f"章节未见{missing_text}相关信息或直接支撑，当前不足以完成“{rule_requirement or '相关要求'}”的判断。"
            if covered_text:
                return f"章节仅能说明{covered_text}等部分维度，仍缺少完成全面判断所需的规则或对照信息。"
        if missing_information:
            return f"缺少足以完成判断的信息：{'；'.join(missing_information[:3])}"
        return "缺少足以完成判断的直接证据。"

    def _build_task_advice(
        self,
        *,
        status: str,
        review_object: str,
        task_question: str,
        rule_requirement: str,
    ) -> str:
        if status == "supported":
            if self._is_naming_task(review_object, task_question, rule_requirement):
                return "保持当前名称表述与命名依据、药典标准及证据链的一致性。"
            return "保持当前事实表述、标准依据和证据链的一致性。"
        if status == "unsupported":
            if self._is_naming_task(review_object, task_question, rule_requirement):
                return "请依据中国药典或批准标准修订名称表述，并明确主体名、盐型或标准命名依据。"
            return "请针对当前任务对应的规则要求修订材料表述，并补充直接支持依据。"
        return "请补充与当前任务直接对应的规则依据、对象标准或章节事实，再进行判断。"

    @classmethod
    def _summarize_material_facts(cls, facts: Dict[str, str], raw_text: str) -> str:
        if facts:
            summary = "；".join(
                [
                    f"{label}：{value}"
                    for label, value in facts.items()
                    if str(value or "").strip()
                ][:4]
            )
            if summary:
                return cls._compact_text(summary, max_len=180)
        return cls._compact_text(str(raw_text or "").strip(), max_len=180)

    def _build_rule_grounded_queries(
        self,
        *,
        base_terms: List[str],
        review_tasks: List[Dict[str, Any]],
        facts: Dict[str, str],
    ) -> List[str]:
        queries: List[str] = []
        seen = set()

        def add_query(text: str) -> None:
            compact = self._compact_text(text, max_len=96)
            if not compact or compact in seen:
                return
            seen.add(compact)
            queries.append(compact)

        primary_rules: List[str] = []
        for task in review_tasks:
            primary_rules.extend(self._safe_list(task.get("rule_targets", [])))
        primary_rules = self._normalize_query_list(primary_rules, max_len=96)[:4]
        primary_fact_values = [
            str(facts.get(label, "") or "").strip()
            for label in ["药品名称", "中文名", "英文名", "中文化学名", "英文化学名", "通用名", "商品名", "CAS号", "规格", "批号"]
            if str(facts.get(label, "") or "").strip()
        ][:4]

        for rule in primary_rules:
            add_query(rule)
        for task in review_tasks[:6]:
            object_text = str(task.get("review_object", "") or "").strip()
            question_text = str(task.get("task_question", "") or "").strip()
            comparison_axes = self._safe_list(task.get("comparison_axes", []))[:3]
            task_rules = self._safe_list(task.get("rule_targets", []))[:2] or primary_rules[:1]
            for rule in task_rules:
                if object_text:
                    add_query(f"{object_text} {rule}")
                for axis in comparison_axes:
                    add_query(f"{axis} {rule}")
            if question_text:
                add_query(question_text)
        for value in primary_fact_values:
            add_query(f"{value} 药典")
            for rule in primary_rules[:2]:
                add_query(f"{value} {rule}")
        if facts.get("中文名") and facts.get("英文名"):
            add_query(f"{facts['中文名']} {facts['英文名']}")
            for rule in primary_rules[:1]:
                add_query(f"{facts['中文名']} {facts['英文名']} {rule}")
        for term in base_terms[:3]:
            add_query(term)
        return queries[:8]

    @staticmethod
    def _guess_entity_type(text: str) -> str:
        value = str(text or "").strip().lower()
        if any(keyword in value for keyword in ["é€šç”¨å", "è¯å“åç§°", "drug name", "å“å"]):
            return "drug_name"
        if any(keyword in value for keyword in ["æˆåˆ†", "åŽŸæ–™è¯", "active ingredient", "api", "è¾…æ–™"]):
            return "ingredient"
        if any(keyword in value for keyword in ["å‰‚åž‹", "ç»™è¯é€”å¾„", "è§„æ ¼", "åŒ…è£…"]):
            return "product_attribute"
        if any(keyword in value for keyword in ["æ‰¹æ¬¡", "æ ·å“", "å¯¹ç…§å“", "å‚æ¯”åˆ¶å‰‚"]):
            return "sample_or_batch"
        if any(keyword in value for keyword in ["æ‚è´¨", "é™è§£äº§ç‰©", "æŒ‡æ ‡æˆåˆ†"]):
            return "impurity_or_analyte"
        if any(keyword in value for keyword in ["æ–¹æ³•", "ä»ªå™¨", "æ£€éªŒ", "åˆ†æž"]):
            return "method_or_instrument"
        if any(keyword in value for keyword in ["é€‚åº”ç—‡", "äººç¾¤", "åˆ†ç»„", "ç»™è¯æ–¹æ¡ˆ", "ç–—æ•ˆç»ˆç‚¹", "å®‰å…¨æ€§"]):
            return "clinical_entity"
        return "medical_entity"

    @staticmethod
    def _guess_data_type(metric_name: str, unit: str = "", context: str = "") -> str:
        text = " ".join([str(metric_name or ""), str(unit or ""), str(context or "")]).lower()
        if "ph" in text:
            return "ph_value"
        if any(keyword in text for keyword in ["æ‚è´¨", "impurity"]):
            return "impurity_result"
        if any(keyword in text for keyword in ["æº¶å‡º", "dissolution"]):
            return "dissolution_result"
        if any(keyword in text for keyword in ["å«é‡", "assay"]):
            return "assay_result"
        if any(keyword in text for keyword in ["ç¨³å®š", "stability", "æ—¶é—´ç‚¹", "å‚¨å­˜"]):
            return "stability_result"
        if any(keyword in text for keyword in ["æ ·æœ¬é‡", "sample size", "åˆ†ç»„"]):
            return "sample_size"
        if any(keyword in text for keyword in ["ä¸è‰¯ååº”", "å®‰å…¨", "safety"]):
            return "safety_endpoint"
        if any(keyword in text for keyword in ["ç–—æ•ˆ", "endpoint", "pk", "auc", "cmax", "tmax"]):
            return "efficacy_or_pk_endpoint"
        return "medical_data_point"

    def _normalize_query_list(self, value: Any, max_len: int = 96) -> List[str]:
        out: List[str] = []
        seen = set()
        for item in self._safe_list(value):
            compact = self._compact_text(item, max_len=max_len)
            if not compact or compact in seen:
                continue
            seen.add(compact)
            out.append(compact)
        return out

    @staticmethod
    def _infer_required_rule_types(task: Dict[str, Any], facts: Dict[str, str] | None = None) -> List[str]:
        text = " ".join(
            [
                str(task.get("task_question", "") or ""),
                str(task.get("review_object", "") or ""),
                " ".join(str(item or "") for item in (task.get("rule_targets", []) or [])),
                " ".join(str(item or "") for item in (task.get("comparison_axes", []) or [])),
                " ".join(str(item or "") for item in (task.get("expected_evidence", []) or [])),
            ]
        )
        facts = facts if isinstance(facts, dict) else {}
        types: List[str] = []
        if any(keyword in text for keyword in ["命名", "中文名", "英文名", "通用名", "商品名", "药品名称"]):
            types.extend(["naming_rule", "official_name_reference"])
        if any(keyword in text for keyword in ["药典", "标准", "批准", "批准通用名", "对照"]):
            types.append("official_name_reference")
        if any(keyword in text for keyword in ["规格", "限度", "杂质", "含量", "检查", "质量标准", "鉴别", "溶出", "有关物质"]):
            types.append("quality_standard")
        if any(keyword in text for keyword in ["方法", "检验", "分析", "验证", "专属性", "精密度", "准确度"]):
            types.append("method_validation")
        if any(keyword in text for keyword in ["稳定", "时间点", "储存", "有效期"]):
            types.append("stability_requirement")
        if any(keyword in text for keyword in ["适应症", "终点", "疗效", "安全性", "受试者", "样本量", "暴露量", "药代"]):
            types.append("clinical_support")
        if not types and facts.get("中文名") and facts.get("英文名"):
            types.extend(["naming_rule", "official_name_reference"])
        out: List[str] = []
        for item in types:
            if item and item not in out:
                out.append(item)
        return out[:4]

    @staticmethod
    def _build_reference_targets(task: Dict[str, Any], facts: Dict[str, str] | None = None) -> List[str]:
        facts = facts if isinstance(facts, dict) else {}
        out: List[str] = []
        for label in ["药品名称", "中文名", "英文名", "中文化学名", "英文化学名", "通用名", "商品名", "CAS号", "规格", "批号"]:
            value = str(facts.get(label, "") or "").strip()
            if value and value not in out:
                out.append(value)
        review_object = str(task.get("review_object", "") or "").strip()
        if review_object and review_object not in out:
            out.append(review_object)
        return out[:4]

    def _build_retrieval_blueprint(
        self,
        *,
        review_tasks: List[Dict[str, Any]],
        facts: Dict[str, str],
        query_list: List[str],
    ) -> Dict[str, Any]:
        rule_queries: List[str] = []
        reference_queries: List[str] = []
        comparison_queries: List[str] = []
        required_rule_types: List[str] = []
        task_blueprints: List[Dict[str, Any]] = []

        def add_unique(target: List[str], value: str) -> None:
            text = self._compact_text(value, max_len=96)
            if text and text not in target:
                target.append(text)

        for index, task in enumerate(review_tasks[:8], start=1):
            task_code = str(task.get("task_code", "") or f"task_{index}").strip() or f"task_{index}"
            task_rule_types = self._safe_list(task.get("required_rule_types", [])) or self._infer_required_rule_types(task, facts)
            for item in task_rule_types:
                if item not in required_rule_types:
                    required_rule_types.append(item)
            task_rules = self._safe_list(task.get("rule_targets", []))
            task_refs = self._safe_list(task.get("reference_targets", [])) or self._build_reference_targets(task, facts)
            axes = self._safe_list(task.get("comparison_axes", []))
            task_question = str(task.get("task_question", "") or "").strip()
            task_rule_queries: List[str] = []
            task_reference_queries: List[str] = []
            task_comparison_queries: List[str] = []
            for rule in task_rules[:3]:
                add_unique(rule_queries, rule)
                if rule not in task_rule_queries:
                    task_rule_queries.append(rule)
                if task_refs:
                    query = f"{task_refs[0]} {rule}"
                    add_unique(rule_queries, query)
                    if query not in task_rule_queries:
                        task_rule_queries.append(query)
            for ref in task_refs[:3]:
                add_unique(reference_queries, ref)
                add_unique(reference_queries, f"{ref} 药典")
                if ref not in task_reference_queries:
                    task_reference_queries.append(ref)
            if task_question:
                add_unique(comparison_queries, task_question)
                task_comparison_queries.append(task_question)
            for axis in axes[:3]:
                add_unique(comparison_queries, axis)
                if axis not in task_comparison_queries:
                    task_comparison_queries.append(axis)
            task_blueprints.append(
                {
                    "task_code": task_code,
                    "required_rule_types": task_rule_types,
                    "rule_queries": task_rule_queries[:3],
                    "reference_queries": task_reference_queries[:3],
                    "comparison_queries": task_comparison_queries[:3],
                }
            )

        for query in query_list[:8]:
            if any(keyword in query for keyword in ["药典", "原则", "法规", "指导原则", "ICH", "标准", "批准"]):
                add_unique(rule_queries, query)
            elif any(keyword in query for keyword in ["CAS", "中文名", "英文名", "化学名", "规格", "批号"]):
                add_unique(reference_queries, query)
            else:
                add_unique(comparison_queries, query)

        return {
            "required_rule_types": required_rule_types[:6],
            "rule_queries": rule_queries[:8] or query_list[:2],
            "reference_queries": reference_queries[:8] or query_list[:2],
            "comparison_queries": comparison_queries[:8] or query_list[:2],
            "task_blueprints": task_blueprints[:8],
        }

    def _normalize_review_tasks(self, value: Any) -> List[Dict[str, Any]]:
        if not isinstance(value, list):
            return []
        out: List[Dict[str, Any]] = []
        seen = set()
        for index, item in enumerate(value, start=1):
            if not isinstance(item, dict):
                continue
            row = {
                "task_code": str(item.get("task_code", "") or f"task_{index}").strip() or f"task_{index}",
                "task_question": str(item.get("task_question", "") or "").strip(),
                "review_object": str(item.get("review_object", "") or "").strip(),
                "rule_targets": self._safe_list(item.get("rule_targets", [])),
                "required_rule_types": self._safe_list(item.get("required_rule_types", [])),
                "reference_targets": self._safe_list(item.get("reference_targets", [])),
                "comparison_axes": self._safe_list(item.get("comparison_axes", [])),
                "expected_evidence": self._safe_list(item.get("expected_evidence", [])),
                "completion_criteria": self._safe_list(item.get("completion_criteria", [])),
            }
            row["task_question"] = self._build_explicit_task_question(
                task_question=row["task_question"],
                review_object=row["review_object"],
                rule_targets=row["rule_targets"],
                comparison_axes=row["comparison_axes"],
                facts={},
            )
            if not row["task_question"] and row["review_object"]:
                row["task_question"] = f"当前章节是否满足“{row['review_object']}”相关要求？"
            if not row["task_question"]:
                continue
            key = "|".join([row["task_code"], row["task_question"], row["review_object"]])
            if key in seen:
                continue
            seen.add(key)
            out.append(row)
        return out[:8]

    def _normalize_task_verdicts(self, value: Any) -> List[Dict[str, str]]:
        if not isinstance(value, list):
            return []
        out: List[Dict[str, str]] = []
        seen = set()
        for item in value:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status", "") or "").strip().lower()
            if status not in {"supported", "unsupported", "insufficient_information"}:
                status = "unsupported"
            row = {
                "task_code": str(item.get("task_code", "") or "").strip(),
                "status": status,
                "task_question": str(item.get("task_question", "") or "").strip(),
                "problem": str(item.get("problem", "") or "").strip(),
                "basis": str(item.get("basis", "") or "").strip(),
                "reason": str(item.get("reason", "") or "").strip(),
                "advice": str(item.get("advice", "") or "").strip(),
            }
            if not row["task_code"] and not row["task_question"]:
                continue
            signature = "|".join(
                [
                    row["status"],
                    self._compact_text(row["task_question"], max_len=96),
                    self._compact_text(row["problem"], max_len=96),
                    self._compact_text(row["basis"], max_len=96),
                ]
            )
            key = signature or "|".join(row.values())
            if key in seen:
                continue
            seen.add(key)
            out.append(row)
        return out[:12]

    def _normalize_reasoning_chain_items(self, value: Any) -> List[Dict[str, str]]:
        if not isinstance(value, list):
            return []
        out: List[Dict[str, str]] = []
        seen = set()
        for item in value:
            if not isinstance(item, dict):
                continue
            row = {
                "task_code": str(item.get("task_code", "") or "").strip(),
                "rule_requirement": str(item.get("rule_requirement", "") or "").strip(),
                "material_fact": str(item.get("material_fact", "") or "").strip(),
                "evidence_support": str(item.get("evidence_support", "") or "").strip(),
                "comparison": str(item.get("comparison", "") or "").strip(),
                "judgment_reason": str(item.get("judgment_reason", "") or "").strip(),
            }
            if not any(row.values()):
                continue
            key = "|".join(
                [
                    self._compact_text(row["rule_requirement"], max_len=96),
                    self._compact_text(row["material_fact"], max_len=96),
                    self._compact_text(row["comparison"], max_len=96),
                ]
            ) or "|".join(row.values())
            if key in seen:
                continue
            seen.add(key)
            out.append(row)
        return out[:16]

    def _apply_task_question_reasoning_overrides(self, payload: Dict[str, Any], data: Dict[str, Any]) -> None:
        source_questions = payload.get("task_questions", []) if isinstance(payload.get("task_questions", []), list) else []
        if not source_questions or not isinstance(data, dict):
            return

        task_meta: Dict[str, Dict[str, Any]] = {}
        for index, item in enumerate(source_questions, start=1):
            if not isinstance(item, dict):
                continue
            task_code = str(item.get("task_code", "") or f"task_{index}").strip() or f"task_{index}"
            task_meta[task_code] = dict(item)

        if not task_meta or not isinstance(data.get("task_verdicts", []), list):
            return

        facts = self._extract_labeled_facts(str(payload.get("raw_text", "") or ""))
        reasoning_map: Dict[str, Dict[str, Any]] = {}
        for row in data.get("reasoning_chain_items", []):
            if not isinstance(row, dict):
                continue
            task_code = str(row.get("task_code", "") or "").strip()
            if task_code:
                reasoning_map[task_code] = row

        derived_supported: List[str] = []
        derived_missing: List[str] = []
        derived_unsupported: List[str] = []
        derived_risk: List[str] = []
        derived_findings: List[Dict[str, str]] = []
        derived_questions: List[Dict[str, str]] = []
        touched = False

        for index, item in enumerate(data.get("task_verdicts", []), start=1):
            if not isinstance(item, dict):
                continue
            task_code = str(item.get("task_code", "") or f"task_{index}").strip() or f"task_{index}"
            meta = task_meta.get(task_code)
            if not meta:
                continue

            covered_axes = self._safe_list(meta.get("covered_axes", []))
            missing_axes = self._safe_list(meta.get("missing_axes", []))
            coverage_ratio = meta.get("coverage_ratio", 0.0)
            question_type = str(meta.get("question_type", "") or "").strip()
            if not covered_axes and not missing_axes and question_type != "physicochemical_conformance":
                continue

            current_status = str(item.get("status", "") or "").strip().lower()
            if missing_axes and current_status == "supported":
                item["status"] = "insufficient_information"
                current_status = "insufficient_information"
                touched = True

            rule_requirement = str(item.get("basis", "") or "").strip() or "；".join(self._safe_list(meta.get("judgment_basis", []))[:1])
            review_object = str(meta.get("review_object", "") or "").strip()
            task_question = str(item.get("task_question", "") or meta.get("task_question", "") or "").strip()
            reasoning_row = reasoning_map.get(task_code)
            evidence_support = ""
            if isinstance(reasoning_row, dict):
                evidence_support = str(reasoning_row.get("evidence_support", "") or "").strip()

            comparison = self._build_task_comparison(
                status=current_status,
                covered_axes=covered_axes,
                missing_axes=missing_axes,
                coverage_ratio=coverage_ratio,
            )
            reason = self._build_task_reason(
                status=current_status,
                task_question=task_question,
                review_object=review_object,
                rule_requirement=rule_requirement,
                facts=facts,
                missing_information=self._safe_list(meta.get("missing_information", [])),
                evidence_support=evidence_support,
                covered_axes=covered_axes,
                missing_axes=missing_axes,
                coverage_ratio=coverage_ratio,
            )

            item["problem"] = self._build_task_statement(
                status=current_status,
                review_object=review_object,
                task_question=task_question,
                rule_requirement=rule_requirement,
                facts=facts,
            )
            item["basis"] = rule_requirement
            item["reason"] = reason
            item["advice"] = self._build_task_advice(
                status=current_status,
                review_object=review_object,
                task_question=task_question,
                rule_requirement=rule_requirement,
            )

            if isinstance(reasoning_row, dict):
                reasoning_row["rule_requirement"] = rule_requirement
                reasoning_row["comparison"] = comparison
                reasoning_row["judgment_reason"] = reason
                if not str(reasoning_row.get("material_fact", "") or "").strip():
                    reasoning_row["material_fact"] = self._summarize_material_facts(facts, str(payload.get("raw_text", "") or ""))

            if current_status == "supported":
                derived_supported.append(str(item.get("problem", "") or "").strip())
            elif current_status == "unsupported":
                derived_unsupported.append(str(item.get("problem", "") or "").strip())
                derived_risk.append(rule_requirement or task_question)
                derived_findings.append(
                    {
                        "location": "原文与对照信息不一致",
                        "rule_code": task_code,
                        "rule_text": rule_requirement,
                        "requirement_point": rule_requirement or task_question,
                        "issue_type": "unsupported",
                        "issue": str(item.get("problem", "") or "").strip(),
                        "evidence": evidence_support,
                        "suggested_fix": str(item.get("advice", "") or "").strip(),
                    }
                )
            else:
                derived_missing.extend(missing_axes[:4] or self._safe_list(meta.get("missing_information", []))[:4] or [rule_requirement or task_question])
                derived_risk.append(rule_requirement or task_question)
                derived_findings.append(
                    {
                        "location": "全文未见充分支撑",
                        "rule_code": task_code,
                        "rule_text": rule_requirement,
                        "requirement_point": rule_requirement or task_question,
                        "issue_type": "missing",
                        "issue": str(item.get("problem", "") or "").strip(),
                        "evidence": evidence_support,
                        "suggested_fix": str(item.get("advice", "") or "").strip(),
                    }
                )
                derived_questions.append(
                    {
                        "issue": str(item.get("problem", "") or "").strip(),
                        "basis": rule_requirement or task_question,
                        "requested_action": str(item.get("advice", "") or "").strip(),
                    }
                )

        if not touched and not derived_findings and not derived_supported and not derived_missing and not derived_unsupported:
            return

        data["supported_points"] = self._safe_list(derived_supported)
        data["missing_points"] = self._safe_list(derived_missing)
        data["unsupported_points"] = self._safe_list(derived_unsupported)
        data["risk_points"] = self._safe_list(derived_risk)
        data["rule_findings"] = self._refine_rule_findings(derived_findings)
        data["questions"] = self._refine_questions(derived_questions, data["rule_findings"])

    def _enforce_rule_finding_task_consistency(self, data: Dict[str, Any]) -> None:
        if not isinstance(data, dict):
            return
        task_verdicts = data.get("task_verdicts", [])
        rule_findings = data.get("rule_findings", [])
        if not isinstance(task_verdicts, list) or not isinstance(rule_findings, list):
            return
        if not task_verdicts or not rule_findings:
            return

        statuses = [
            str(item.get("status", "") or "").strip().lower()
            for item in task_verdicts
            if isinstance(item, dict)
        ]
        if not statuses or any(status in {"unsupported", "insufficient_information"} for status in statuses):
            return

        primary_finding = next((item for item in rule_findings if isinstance(item, dict)), None)
        if not primary_finding:
            return

        issue_type = str(primary_finding.get("issue_type", "") or "").strip().lower()
        target_status = "unsupported" if issue_type in {"missing", "unsupported", "conflict"} else "insufficient_information"
        requirement_point = str(primary_finding.get("requirement_point", "") or primary_finding.get("rule_text", "") or "").strip()
        finding_issue = str(primary_finding.get("issue", "") or "").strip()
        suggested_fix = str(primary_finding.get("suggested_fix", "") or "").strip()
        evidence = str(primary_finding.get("evidence", "") or "").strip()

        reasoning_map = {}
        for row in data.get("reasoning_chain_items", []):
            if not isinstance(row, dict):
                continue
            task_code = str(row.get("task_code", "") or "").strip()
            if task_code:
                reasoning_map[task_code] = row

        for item in task_verdicts:
            if not isinstance(item, dict):
                continue
            item["status"] = target_status
            if requirement_point:
                item["basis"] = requirement_point
            if finding_issue:
                item["problem"] = finding_issue
                item["reason"] = finding_issue
            if suggested_fix:
                item["advice"] = suggested_fix

            task_code = str(item.get("task_code", "") or "").strip()
            reasoning_row = reasoning_map.get(task_code)
            if isinstance(reasoning_row, dict):
                if requirement_point:
                    reasoning_row["rule_requirement"] = requirement_point
                if evidence:
                    reasoning_row["evidence_support"] = evidence
                reasoning_row["comparison"] = (
                    "章节事实与规则要求之间尚未形成可直接支撑“已满足”结论的证据链。"
                    if target_status == "insufficient_information"
                    else "现有章节事实与规则要求之间存在未满足或未补足的关键要求。"
                )
                if finding_issue:
                    reasoning_row["judgment_reason"] = finding_issue

    def _normalize_rule_findings(self, value: Any) -> List[Dict[str, str]]:
        if not isinstance(value, list):
            return []
        out: List[Dict[str, str]] = []
        seen = set()
        for item in value:
            if not isinstance(item, dict):
                continue
            row = {
                "location": str(item.get("location", "") or "").strip(),
                "rule_code": str(item.get("rule_code", "") or "").strip(),
                "rule_text": str(item.get("rule_text", "") or "").strip(),
                "requirement_point": str(item.get("requirement_point", "") or item.get("rule_clause", "") or "").strip(),
                "issue_type": str(item.get("issue_type", "") or "").strip().lower(),
                "issue": str(item.get("issue", "") or "").strip(),
                "evidence": str(item.get("evidence", "") or "").strip(),
                "violating_text": str(item.get("violating_text", "") or item.get("source_excerpt", "") or "").strip(),
                "suggested_fix": str(item.get("suggested_fix", "") or item.get("requested_action", "") or "").strip(),
            }
            if row["issue_type"] not in {"missing", "unsupported", "conflict", "uncertain"}:
                row["issue_type"] = "uncertain"
            if not any(row.values()):
                continue
            dedupe_key = (
                row["issue_type"],
                self._compact_text(row["requirement_point"], max_len=96),
                self._compact_text(row["issue"], max_len=96),
                self._compact_text(row["location"], max_len=48),
            )
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            out.append(row)
        return out

    def _looks_like_generic_fix(self, text: str, requirement: str = "", rule_text: str = "") -> bool:
        value = str(text or "").strip()
        if not value:
            return True
        compact_value = re.sub(r"\s+", "", value)
        compact_requirement = re.sub(r"\s+", "", str(requirement or ""))
        compact_rule_text = re.sub(r"\s+", "", str(rule_text or ""))
        generic_markers = [
            "请补充与",
            "直接对应的研究资料",
            "直接相关的原始研究内容",
            "方法说明或法规依据",
            "方法依据或法规支持",
        ]
        if any(marker in value for marker in generic_markers):
            return True
        if compact_requirement and compact_value in {compact_requirement, f"请补充{compact_requirement}", f"补充{compact_requirement}"}:
            return True
        if compact_rule_text and compact_value in {compact_rule_text, f"请补充{compact_rule_text}", f"补充{compact_rule_text}"}:
            return True
        action_markers = ("补充", "修订", "明确", "说明", "给出", "增加", "提供", "列明", "更新", "补足")
        return not any(marker in value for marker in action_markers)

    def _build_concrete_fix_suggestion(
        self,
        *,
        location: str = "",
        requirement_point: str = "",
        issue: str = "",
        evidence: str = "",
    ) -> str:
        location_text = self._compact_text(location or "全文未见", max_len=24) or "全文未见"
        target_text = self._compact_text(requirement_point or issue or "相关要求", max_len=48) or "相关要求"
        issue_text = self._compact_text(issue or "", max_len=64)
        evidence_text = self._compact_text(evidence or "", max_len=72)
        if location_text == "全文未见":
            message = f"请在本章节中新增与“{target_text}”直接对应的申报内容，至少写清研究对象、方法或依据、关键结果和结论。"
        else:
            message = f"请修订{location_text}处表述，围绕“{target_text}”补足申报内容，并把关键依据或结果写清楚。"
        if issue_text:
            message += f" 需要直接解决“{issue_text}”。"
        if evidence_text and evidence_text not in {"未见", "未提供", "不存在/未见"}:
            message += f" 可结合现有证据“{evidence_text}”补充说明，但不要只复述规则原文。"
        else:
            message += " 不要只复述规则要求，要补具体资料、数据、表格或论证结论。"
        return message

    def _build_concrete_requested_action(self, *, basis: str = "", location: str = "", issue: str = "") -> str:
        target_text = self._compact_text(basis or issue or "相关要求", max_len=48) or "相关要求"
        location_text = self._compact_text(location or "", max_len=24)
        if location_text and location_text != "全文未见":
            message = f"请优先修订{location_text}处内容，补充“{target_text}”对应的原始资料、关键数据、表格或结论说明。"
        else:
            message = f"请在本章节中补充“{target_text}”对应的原始资料、关键数据、表格或结论说明。"
        compact_issue = self._compact_text(issue or "", max_len=56)
        if compact_issue:
            message += f" 修订后要能够直接回应“{compact_issue}”。"
        return message

    def _refine_rule_findings(self, value: Any) -> List[Dict[str, str]]:
        rows = self._normalize_rule_findings(value)
        for row in rows:
            if self._looks_like_generic_fix(
                row.get("suggested_fix", ""),
                requirement=row.get("requirement_point", ""),
                rule_text=row.get("rule_text", ""),
            ):
                row["suggested_fix"] = self._build_concrete_fix_suggestion(
                    location=row.get("location", ""),
                    requirement_point=row.get("requirement_point", ""),
                    issue=row.get("issue", ""),
                    evidence=row.get("evidence", ""),
                )
        return rows

    def _refine_questions(self, value: Any, rule_findings: List[Dict[str, str]]) -> List[Dict[str, str]]:
        if not isinstance(value, list):
            return []
        questions: List[Dict[str, str]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            issue = str(item.get("issue", "") or "").strip()
            basis = str(item.get("basis", "") or "").strip()
            requested_action = str(item.get("requested_action", "") or "").strip()
            if not issue or not basis:
                continue
            matched_finding = None
            for finding in rule_findings:
                requirement_text = str(finding.get("requirement_point", "") or "").strip()
                finding_issue = str(finding.get("issue", "") or "").strip()
                if basis and (
                    (requirement_text and (basis in requirement_text or requirement_text in basis))
                    or (finding_issue and (basis in finding_issue or finding_issue in basis))
                ):
                    matched_finding = finding
                    break
            if self._looks_like_generic_fix(
                requested_action,
                requirement=basis,
                rule_text=str((matched_finding or {}).get("rule_text", "") or ""),
            ):
                requested_action = self._build_concrete_requested_action(
                    basis=basis,
                    location=str((matched_finding or {}).get("location", "") or ""),
                    issue=issue or str((matched_finding or {}).get("issue", "") or ""),
                )
            questions.append({
                "issue": issue,
                "basis": basis,
                "requested_action": requested_action,
            })
        return questions

    def _derive_negative_points_from_rule_findings(self, rule_findings: List[Dict[str, str]]) -> Dict[str, List[str]]:
        missing_points: List[str] = []
        unsupported_points: List[str] = []
        risk_points: List[str] = []
        for item in rule_findings:
            issue_type = str(item.get("issue_type", "") or "").strip().lower()
            issue = str(item.get("issue", "") or "").strip()
            requirement = str(item.get("requirement_point", "") or "").strip()
            rule_text = str(item.get("rule_text", "") or "").strip()
            summary = issue or requirement or rule_text
            if not summary:
                continue
            if issue_type == "missing":
                missing_points.append(summary)
            else:
                unsupported_points.append(summary)
            risk_points.append(requirement or issue or rule_text)
        return {
            "missing_points": self._safe_list(missing_points),
            "unsupported_points": self._safe_list(unsupported_points),
            "risk_points": self._safe_list(risk_points),
        }

    def _derive_questions_from_rule_findings(self, rule_findings: List[Dict[str, str]]) -> List[Dict[str, str]]:
        questions: List[Dict[str, str]] = []
        for item in rule_findings[:5]:
            issue = str(item.get("issue", "") or item.get("requirement_point", "") or "").strip()
            basis = str(item.get("requirement_point", "") or item.get("rule_text", "") or item.get("rule_code", "") or "").strip()
            requested_action = str(item.get("suggested_fix", "") or "").strip()
            if self._looks_like_generic_fix(
                requested_action,
                requirement=basis,
                rule_text=str(item.get("rule_text", "") or ""),
            ):
                requested_action = self._build_concrete_requested_action(
                    basis=basis,
                    location=str(item.get("location", "") or ""),
                    issue=issue,
                )
            if issue and basis and requested_action:
                questions.append(
                    {
                        "issue": issue,
                        "basis": basis,
                        "requested_action": requested_action,
                    }
                )
        return questions

    def _derive_evidence_refs(self, payload: Dict[str, Any], rule_findings: List[Dict[str, str]]) -> List[str]:
        evidence_refs: List[str] = []
        for item in rule_findings:
            evidence = str(item.get("evidence", "") or "").strip()
            if evidence:
                evidence_refs.append(evidence)
        for item in payload.get("retrieved_materials", []) if isinstance(payload.get("retrieved_materials", []), list) else []:
            if not isinstance(item, dict):
                continue
            title = self._material_display_name(item)
            if title:
                evidence_refs.append(title)
        resolved_refs = self._resolve_evidence_labels(
            evidence_refs,
            payload.get("retrieved_materials", []) if isinstance(payload.get("retrieved_materials", []), list) else [],
            limit=8,
            allow_unmapped_text=True,
        )
        return self._safe_list(resolved_refs)[:8]

    def _normalize_problem_basis_advice_items(self, value: Any) -> List[Dict[str, str]]:
        if not isinstance(value, list):
            return []
        items: List[Dict[str, str]] = []
        seen = set()
        for item in value:
            if not isinstance(item, dict):
                continue
            row = {
                "status": str(item.get("status", "") or "").strip().lower() or "issue",
                "problem": str(item.get("problem", "") or item.get("issue", "") or "").strip(),
                "basis": str(item.get("basis", "") or item.get("evidence", "") or "").strip(),
                "advice": str(item.get("advice", "") or item.get("suggested_fix", "") or item.get("requested_action", "") or "").strip(),
                "evidence": str(item.get("evidence", "") or "").strip(),
                "rule_code": str(item.get("rule_code", "") or "").strip(),
                "rule_text": str(item.get("rule_text", "") or "").strip(),
                "issue_type": str(item.get("issue_type", "") or "").strip(),
            }
            if not row["problem"] and not row["basis"] and not row["advice"]:
                continue
            key = "|".join([row["status"], row["problem"], row["basis"], row["advice"], row["rule_code"]])
            if key in seen:
                continue
            seen.add(key)
            items.append(row)
        return items

    def _normalize_medical_key_entities(self, value: Any) -> List[Dict[str, str]]:
        if not isinstance(value, list):
            return []
        items: List[Dict[str, str]] = []
        seen = set()
        for item in value:
            if not isinstance(item, dict):
                continue
            row = {
                "entity_type": str(item.get("entity_type", "") or "").strip() or self._guess_entity_type(item.get("entity_name", "")),
                "entity_name": str(item.get("entity_name", "") or item.get("name", "") or "").strip(),
                "normalized_name": str(item.get("normalized_name", "") or item.get("canonical_name", "") or item.get("entity_name", "") or item.get("name", "") or "").strip(),
                "value": str(item.get("value", "") or "").strip(),
                "unit": str(item.get("unit", "") or "").strip(),
                "context": self._compact_text(str(item.get("context", "") or item.get("description", "") or "").strip(), max_len=120),
                "evidence": self._compact_text(str(item.get("evidence", "") or "").strip(), max_len=120),
                "source": str(item.get("source", "") or "").strip() or "review_output",
            }
            if not row["entity_name"] and not row["normalized_name"]:
                continue
            key = "|".join([row["entity_type"], row["entity_name"], row["normalized_name"], row["value"], row["unit"], row["source"]])
            if key in seen:
                continue
            seen.add(key)
            items.append(row)
        return items[:12]

    def _normalize_medical_key_data_points(self, value: Any) -> List[Dict[str, str]]:
        if not isinstance(value, list):
            return []
        items: List[Dict[str, str]] = []
        seen = set()
        for item in value:
            if not isinstance(item, dict):
                continue
            metric_name = str(item.get("metric_name", "") or item.get("name", "") or "").strip()
            unit = str(item.get("unit", "") or "").strip()
            context = str(item.get("context", "") or item.get("description", "") or "").strip()
            row = {
                "data_type": str(item.get("data_type", "") or "").strip() or self._guess_data_type(metric_name, unit=unit, context=context),
                "metric_name": metric_name,
                "value": str(item.get("value", "") or "").strip(),
                "unit": unit,
                "comparator": str(item.get("comparator", "") or "").strip(),
                "context": self._compact_text(context, max_len=120),
                "evidence": self._compact_text(str(item.get("evidence", "") or "").strip(), max_len=120),
                "source": str(item.get("source", "") or "").strip() or "review_output",
            }
            if not row["metric_name"] and not row["value"]:
                continue
            key = "|".join([row["data_type"], row["metric_name"], row["value"], row["unit"], row["comparator"], row["source"]])
            if key in seen:
                continue
            seen.add(key)
            items.append(row)
        return items[:12]

    def _extract_measurement_candidates(self, text: str, *, source: str, limit: int = 8) -> List[Dict[str, str]]:
        raw = str(text or "").strip()
        if not raw:
            return []
        items: List[Dict[str, str]] = []
        seen = set()
        patterns = [
            re.compile(r"(pH)\s*[:=]?\s*(\d+(?:\.\d+)?)", re.IGNORECASE),
            re.compile(r"([A-Za-z\u4e00-\u9fa5]{1,16})?\s*(<=|>=|<|>|≈|=)?\s*(\d+(?:\.\d+)?)\s*(%|mg/mL|mg|g|μg/mL|ug/mL|ng/mL|μg|ug|ng|mL|L|min|h|d|day|days|month|months|个月|天|小时|℃|°C|rpm|μm|um|nm|mmol/L|mol/L|mm|cm)", re.IGNORECASE),
        ]
        for pattern in patterns:
            for match in pattern.finditer(raw):
                if len(items) >= limit:
                    return items
                if pattern.pattern.startswith("(pH)"):
                    metric_name = "pH"
                    comparator = "="
                    value = match.group(2)
                    unit = ""
                else:
                    metric_name = self._compact_text(match.group(1) or "关键指标", max_len=24) or "关键指标"
                    comparator = str(match.group(2) or "").strip()
                    value = str(match.group(3) or "").strip()
                    unit = str(match.group(4) or "").strip()
                start = max(0, match.start() - 24)
                end = min(len(raw), match.end() + 24)
                context = self._compact_text(raw[start:end], max_len=120)
                key = "|".join([metric_name, value, unit, comparator, source])
                if key in seen:
                    continue
                seen.add(key)
                items.append(
                    {
                        "data_type": self._guess_data_type(metric_name, unit=unit, context=context),
                        "metric_name": metric_name,
                        "value": value,
                        "unit": unit,
                        "comparator": comparator,
                        "context": context,
                        "evidence": context,
                        "source": source,
                    }
                )
        return items

    def _build_fallback_medical_key_entities(self, payload: Dict[str, Any]) -> List[Dict[str, str]]:
        items: List[Dict[str, str]] = []
        extracted_entities = payload.get("extracted_entities", {}) if isinstance(payload.get("extracted_entities", {}), dict) else {}
        structured_sections = extracted_entities.get("structured_sections", {}) if isinstance(extracted_entities.get("structured_sections", {}), dict) else {}
        for field_name in ["试液与试药", "仪器与用具", "仪器与设备", "试剂", "结果"]:
            value = str(structured_sections.get(field_name, "") or "").strip()
            if not value:
                continue
            items.append(
                {
                    "entity_type": "section_block",
                    "entity_name": field_name,
                    "normalized_name": field_name,
                    "value": value,
                    "unit": "",
                    "context": self._compact_text(value, max_len=120),
                    "evidence": self._compact_text(value, max_len=120),
                    "source": "extracted_entities",
                }
            )
        operation_methods = structured_sections.get("操作方法", {})
        if isinstance(operation_methods, dict):
            for method_name, content in operation_methods.items():
                text = str(content or "").strip()
                if not text:
                    continue
                items.append(
                    {
                        "entity_type": "operation_method",
                        "entity_name": f"操作方法-{str(method_name).strip()}",
                        "normalized_name": str(method_name).strip(),
                        "value": text,
                        "unit": "",
                        "context": self._compact_text(text, max_len=120),
                        "evidence": self._compact_text(text, max_len=120),
                        "source": "extracted_entities",
                    }
                )
        elif str(operation_methods or "").strip():
            text = str(operation_methods or "").strip()
            items.append(
                {
                    "entity_type": "operation_method",
                    "entity_name": "操作方法",
                    "normalized_name": "操作方法",
                    "value": text,
                    "unit": "",
                    "context": self._compact_text(text, max_len=120),
                    "evidence": self._compact_text(text, max_len=120),
                    "source": "extracted_entities",
                }
            )
        if items:
            return self._normalize_medical_key_entities(items)
        section_name = str(payload.get("section_name", "") or "").strip()
        if section_name:
            items.append(
                {
                    "entity_type": "section_object",
                    "entity_name": section_name,
                    "normalized_name": section_name,
                    "value": "",
                    "unit": "",
                    "context": self._compact_text(str(payload.get("raw_text", "") or ""), max_len=120),
                    "evidence": "",
                    "source": "task_context",
                }
            )
        return self._normalize_medical_key_entities(items)

    def _build_fallback_medical_key_data_points(self, payload: Dict[str, Any]) -> List[Dict[str, str]]:
        extracted_entities = payload.get("extracted_entities", {}) if isinstance(payload.get("extracted_entities", {}), dict) else {}
        structured_sections = extracted_entities.get("structured_sections", {}) if isinstance(extracted_entities.get("structured_sections", {}), dict) else {}
        if structured_sections:
            return []
        items = self._extract_measurement_candidates(str(payload.get("raw_text", "") or ""), source="raw_text", limit=6)
        if len(items) < 8:
            for material in payload.get("retrieved_materials", []) if isinstance(payload.get("retrieved_materials", []), list) else []:
                if not isinstance(material, dict):
                    continue
                content = str(material.get("content", "") or material.get("excerpt", "") or "").strip()
                if not content:
                    continue
                items.extend(self._extract_measurement_candidates(content, source="retrieved_material", limit=max(0, 8 - len(items))))
                if len(items) >= 8:
                    break
        return self._normalize_medical_key_data_points(items)

    def _build_problem_basis_advice_items(self, payload: Dict[str, Any], data: Dict[str, Any]) -> List[Dict[str, str]]:
        items = self._normalize_problem_basis_advice_items(data.get("problem_basis_advice_items", []))
        if items:
            return items[:8]
        for item in data.get("rule_findings", []):
            if not isinstance(item, dict):
                continue
            problem = str(item.get("issue", "") or item.get("requirement_point", "") or item.get("rule_text", "") or "").strip()
            if not problem:
                continue
            basis_parts = []
            rule_code = str(item.get("rule_code", "") or "").strip()
            rule_text = str(item.get("rule_text", "") or "").strip()
            requirement = str(item.get("requirement_point", "") or "").strip()
            evidence = str(item.get("evidence", "") or "").strip()
            location = str(item.get("location", "") or "").strip()
            if rule_code or rule_text:
                basis_parts.append(f"规则依据：{':'.join([x for x in [rule_code, rule_text] if x])}")
            if requirement:
                basis_parts.append(f"要求点：{requirement}")
            if evidence:
                basis_parts.append(f"证据：{evidence}")
            elif location:
                basis_parts.append(f"位置：{location}")
            items.append(
                {
                    "status": "issue",
                    "problem": problem,
                    "basis": "；".join([part for part in basis_parts if part]),
                    "advice": str(item.get("suggested_fix", "") or "").strip() or self._build_concrete_requested_action(
                        basis=requirement or problem,
                        location=location,
                        issue=problem,
                    ),
                    "evidence": evidence,
                    "rule_code": rule_code,
                    "rule_text": rule_text,
                    "issue_type": str(item.get("issue_type", "") or "").strip(),
                }
            )
        for item in data.get("questions", []):
            if not isinstance(item, dict):
                continue
            issue = str(item.get("issue", "") or "").strip()
            basis = str(item.get("basis", "") or "").strip()
            advice = str(item.get("requested_action", "") or "").strip()
            if not issue:
                continue
            items.append(
                {
                    "status": "question",
                    "problem": issue,
                    "basis": basis,
                    "advice": advice or self._build_concrete_requested_action(basis=basis or issue, issue=issue),
                    "evidence": basis,
                    "rule_code": "",
                    "rule_text": "",
                    "issue_type": "question",
                }
            )
        if not items:
            for item in data.get("task_verdicts", []):
                if not isinstance(item, dict):
                    continue
                status = str(item.get("status", "") or "").strip().lower()
                mapped_status = "supported" if status == "supported" else ("question" if status == "insufficient_information" else "issue")
                problem = str(item.get("problem", "") or item.get("task_question", "") or "").strip()
                basis = str(item.get("basis", "") or "").strip()
                advice = str(item.get("advice", "") or "").strip()
                if not problem:
                    continue
                items.append(
                    {
                        "status": mapped_status,
                        "problem": problem,
                        "basis": basis,
                        "advice": advice or self._build_concrete_requested_action(basis=basis or problem, issue=problem),
                        "evidence": basis,
                        "rule_code": "",
                        "rule_text": "",
                        "issue_type": mapped_status,
                    }
                )
        if not items and str(data.get("pre_review_conclusion", "") or "").strip() == "supported":
            evidence_refs = self._safe_list(data.get("evidence_refs", []))
            linked_rules = self._safe_list(data.get("linked_rules", []))
            for point in self._safe_list(data.get("supported_points", []))[:5]:
                basis = str(point or "").strip()
                if linked_rules:
                    basis = f"{basis}；关联规则：{linked_rules[0]}"
                if evidence_refs:
                    basis = f"{basis}；证据：{evidence_refs[0]}"
                items.append(
                    {
                        "status": "supported",
                        "problem": str(point or "").strip() or "当前任务满足相关要求",
                        "basis": basis,
                        "advice": "保持当前申报方式，并继续维持结论与证据链的对应关系。",
                        "evidence": evidence_refs[0] if evidence_refs else "",
                        "rule_code": "",
                        "rule_text": "",
                        "issue_type": "supported",
                    }
                )
        if not items:
            linked_rules = self._safe_list(data.get("linked_rules", [])) or self._safe_list(payload.get("section_rules", []))
            for point in (self._safe_list(data.get("unsupported_points", []))[:3] + self._safe_list(data.get("missing_points", []))[:3]):
                text = str(point or "").strip()
                if not text:
                    continue
                items.append(
                    {
                        "status": "issue",
                        "problem": text,
                        "basis": linked_rules[0] if linked_rules else "",
                        "advice": self._build_concrete_requested_action(basis=text, issue=text),
                        "evidence": "",
                        "rule_code": "",
                        "rule_text": "",
                        "issue_type": "issue",
                    }
                )
        return self._normalize_problem_basis_advice_items(items)[:8]

    def _fallback_plan(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        section_id = str(payload.get("section_id", "") or "")
        section_name = str(payload.get("section_name", "") or "")
        registration_class = self._compact_text(str(payload.get("registration_class", "") or ""), max_len=48)
        review_domain = self._compact_text(str(payload.get("review_domain", "") or ""), max_len=32)
        product_type = self._compact_text(str(payload.get("product_type", "") or ""), max_len=32)
        facts = self._extract_labeled_facts(str(payload.get("raw_text", "") or ""))
        focus_points = self._normalize_query_list(payload.get("focus_points", []), max_len=48)
        compliance_targets = self._normalize_query_list(payload.get("compliance_targets", []), max_len=64)
        issue_hypotheses = self._normalize_query_list(payload.get("issue_hypotheses", []), max_len=64)
        evidence_requirements = self._normalize_query_list(payload.get("evidence_requirements", []), max_len=64)
        medical_entity_requirements = self._normalize_query_list(payload.get("medical_entity_requirements", []), max_len=64)
        medical_data_requirements = self._normalize_query_list(payload.get("medical_data_requirements", []), max_len=64)
        base_terms = [x for x in [section_name, product_type, review_domain, registration_class] if x]
        review_tasks = self._normalize_review_tasks(payload.get("review_tasks", []))
        if not review_tasks:
            raw_section_rules = self._safe_list(payload.get("section_rules", []))
            decision_rule_candidates: List[str] = []
            for rule in raw_section_rules:
                for fragment in re.split(r"[；;\n\r]+", str(rule or "").strip()):
                    text = str(fragment or "").strip().strip("。；;，,：: ")
                    if not text:
                        continue
                    if any(marker in text for marker in ["优先寻找", "可直接定位到章节原文", "二手转述", "不要只复述规则原文"]):
                        continue
                    if any(marker in text for marker in ["确认章节", "核对资料是否", "核对章节结论是否", "完成申报所需"]):
                        continue
                    if text not in decision_rule_candidates:
                        decision_rule_candidates.append(text)
            candidates = decision_rule_candidates or focus_points or compliance_targets
            for index, item in enumerate(candidates[:4], start=1):
                text = str(item or "").strip()
                if not text:
                    continue
                review_tasks.append(
                    {
                        "task_code": f"task_{index}",
                        "task_question": f"当前章节中，{section_name or '当前审评对象'}是否满足“{text}”要求？",
                        "review_object": section_name or text,
                        "rule_targets": [text],
                        "required_rule_types": [],
                        "reference_targets": [],
                        "comparison_axes": [section_name] if section_name else [],
                        "expected_evidence": evidence_requirements[:3],
                        "completion_criteria": evidence_requirements[:3],
                    }
                )
        normalized_tasks: List[Dict[str, Any]] = []
        for task in review_tasks[:8]:
            row = dict(task)
            row["task_question"] = self._build_explicit_task_question(
                task_question=str(row.get("task_question", "") or ""),
                review_object=str(row.get("review_object", "") or ""),
                rule_targets=self._safe_list(row.get("rule_targets", [])),
                comparison_axes=self._safe_list(row.get("comparison_axes", [])),
                facts=facts,
            )
            if not str(row.get("review_object", "") or "").strip():
                row["review_object"] = "中文名命名规范" if "中文名" in facts else (section_name or "当前审评对象")
            if not self._safe_list(row.get("required_rule_types", [])):
                row["required_rule_types"] = self._infer_required_rule_types(row, facts)
            if not self._safe_list(row.get("reference_targets", [])):
                row["reference_targets"] = self._build_reference_targets(row, facts)
            if not self._safe_list(row.get("comparison_axes", [])) and facts:
                row["comparison_axes"] = list(facts.keys())[:4]
            if not self._safe_list(row.get("expected_evidence", [])):
                row["expected_evidence"] = evidence_requirements[:3] or ["规则依据", "对象标准", "原文事实"]
            if not self._safe_list(row.get("completion_criteria", [])):
                row["completion_criteria"] = evidence_requirements[:3] or ["存在明确规则依据", "存在对象标准或药典记录", "存在可比对原文事实"]
            normalized_tasks.append(row)
        review_tasks = self._normalize_review_tasks(normalized_tasks) or normalized_tasks

        query_list = self._build_rule_grounded_queries(base_terms=base_terms, review_tasks=review_tasks, facts=facts)
        for point in (focus_points + compliance_targets + issue_hypotheses + medical_entity_requirements + medical_data_requirements)[:8]:
            query = self._compact_text(" ".join(base_terms + [point]), max_len=96)
            if query and query not in query_list:
                query_list.append(query)
        if not query_list:
            query_list = [self._compact_text(" ".join([x for x in [section_name, product_type, review_domain] if x]), max_len=96)]
        query_list = [item for item in query_list if item][:8]
        if not query_list:
            query_list = [self._compact_text(section_id or section_name or "章节检索", max_len=96)]
        retrieval_blueprint = self._build_retrieval_blueprint(
            query_list=query_list,
            review_tasks=review_tasks,
            facts=facts,
        )
        return {
            "section_id": section_id,
            "query_list": query_list,
            "retrieval_plan": [
                {"source_type": "指导原则", "purpose": "核对章节规范要求和研究重点", "query_subset": query_list[:2]},
                {"source_type": "ICH", "purpose": "核对国际技术要求", "query_subset": query_list[:1]},
                {"source_type": "法律法规", "purpose": "核对注册法规和申报要求", "query_subset": [self._compact_text(section_id or section_name, max_len=64)]},
                {"source_type": "药典数据", "purpose": "核对药典标准和检查项目", "query_subset": query_list[:1]},
                {"source_type": "历史经验", "purpose": "补充历史高风险问题和经验提醒", "query_subset": [self._compact_text(section_name or section_id, max_len=64)]},
            ],
            "priority_sources": ["指导原则", "ICH", "法律法规", "药典数据", "历史经验"],
            "expected_evidence_types": evidence_requirements[:6] or compliance_targets[:5] or focus_points[:5],
            "entity_extraction_focus": medical_entity_requirements[:6] or focus_points[:5],
            "data_extraction_focus": medical_data_requirements[:6] or evidence_requirements[:5],
            "missing_info_flags": [],
            "retrieval_blueprint": retrieval_blueprint,
            "review_tasks": review_tasks,
        }

    def plan(self, payload: Dict[str, Any], prompt_config: Dict[str, Any] | None = None) -> Dict[str, Any]:
        prompt_payload = self._build_planner_prompt_payload(payload)
        prompt = self.prompts.render("chapter_planner.j2", prompt_payload, prompt_config=prompt_config or {})
        default_data = self._fallback_plan(payload)
        raw = self.llm.chat(
            messages=self.envelopes.build("planner", prompt),
            default=json.dumps(default_data, ensure_ascii=False),
        )
        parsed = self.llm.extract_json(raw)
        validate_plan_output(parsed)
        llm_execution = self._build_llm_execution_meta(
            agent_name="planner",
            raw_text=raw,
            parsed_payload=parsed,
        )
        data = parsed
        data["llm_execution"] = llm_execution
        data.setdefault("section_id", str(payload.get("section_id", "") or ""))
        data["query_list"] = self._normalize_query_list(data.get("query_list", []), max_len=96)[:8]
        if not data["query_list"]:
            data["query_list"] = default_data["query_list"]
        retrieval_plan = data.get("retrieval_plan", [])
        if not isinstance(retrieval_plan, list):
            data["retrieval_plan"] = default_data["retrieval_plan"]
        else:
            normalized_plan = []
            for item in retrieval_plan:
                if not isinstance(item, dict):
                    continue
                normalized_plan.append(
                    {
                        "source_type": str(item.get("source_type", "") or "").strip(),
                        "purpose": self._compact_text(str(item.get("purpose", "") or ""), max_len=64),
                        "query_subset": self._normalize_query_list(item.get("query_subset", []), max_len=96)[:4] or data["query_list"][:2],
                    }
                )
            data["retrieval_plan"] = normalized_plan or default_data["retrieval_plan"]
        data["priority_sources"] = self._safe_list(data.get("priority_sources", [])) or default_data["priority_sources"]
        retrieval_blueprint = data.get("retrieval_blueprint", {})
        if not isinstance(retrieval_blueprint, dict):
            retrieval_blueprint = {}
        default_blueprint = default_data.get("retrieval_blueprint", {})
        data["retrieval_blueprint"] = {
            "required_rule_types": self._safe_list(retrieval_blueprint.get("required_rule_types", []))
            or self._safe_list(default_blueprint.get("required_rule_types", [])),
            "rule_queries": self._normalize_query_list(retrieval_blueprint.get("rule_queries", []), max_len=96)[:8]
            or self._normalize_query_list(default_blueprint.get("rule_queries", []), max_len=96)[:8],
            "reference_queries": self._normalize_query_list(retrieval_blueprint.get("reference_queries", []), max_len=96)[:8]
            or self._normalize_query_list(default_blueprint.get("reference_queries", []), max_len=96)[:8],
            "comparison_queries": self._normalize_query_list(retrieval_blueprint.get("comparison_queries", []), max_len=96)[:8]
            or self._normalize_query_list(default_blueprint.get("comparison_queries", []), max_len=96)[:8],
            "task_blueprints": [
                {
                    "task_code": str(item.get("task_code", "") or "").strip(),
                    "required_rule_types": self._safe_list(item.get("required_rule_types", [])),
                    "reference_targets": self._safe_list(item.get("reference_targets", [])),
                    "rule_queries": self._normalize_query_list(item.get("rule_queries", []), max_len=96)[:4],
                    "reference_queries": self._normalize_query_list(item.get("reference_queries", []), max_len=96)[:4],
                    "comparison_queries": self._normalize_query_list(item.get("comparison_queries", []), max_len=96)[:4],
                }
                for item in retrieval_blueprint.get("task_blueprints", [])
                if isinstance(retrieval_blueprint.get("task_blueprints", []), list) and isinstance(item, dict)
            ]
            or [
                {
                    "task_code": str(item.get("task_code", "") or "").strip(),
                    "required_rule_types": self._safe_list(item.get("required_rule_types", [])),
                    "reference_targets": self._safe_list(item.get("reference_targets", [])),
                    "rule_queries": self._normalize_query_list(item.get("rule_queries", []), max_len=96)[:4],
                    "reference_queries": self._normalize_query_list(item.get("reference_queries", []), max_len=96)[:4],
                    "comparison_queries": self._normalize_query_list(item.get("comparison_queries", []), max_len=96)[:4],
                }
                for item in default_blueprint.get("task_blueprints", [])
                if isinstance(default_blueprint.get("task_blueprints", []), list) and isinstance(item, dict)
            ],
        }
        data["expected_evidence_types"] = (
            self._safe_list(data.get("expected_evidence_types", []))
            or self._safe_list(payload.get("evidence_requirements", []))
            or self._safe_list(payload.get("compliance_targets", []))
        )
        data["entity_extraction_focus"] = (
            self._safe_list(data.get("entity_extraction_focus", []))
            or self._safe_list(payload.get("medical_entity_requirements", []))
            or default_data.get("entity_extraction_focus", [])
        )
        data["data_extraction_focus"] = (
            self._safe_list(data.get("data_extraction_focus", []))
            or self._safe_list(payload.get("medical_data_requirements", []))
            or default_data.get("data_extraction_focus", [])
        )
        data["missing_info_flags"] = self._safe_list(data.get("missing_info_flags", []))
        data["review_tasks"] = self._normalize_review_tasks(data.get("review_tasks", [])) or default_data.get("review_tasks", [])
        return data

    def _fallback_review(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        section_id = str(payload.get("section_id", "") or "")
        raw_text = str(payload.get("raw_text", "") or "").strip()
        facts = self._extract_labeled_facts(raw_text)
        focus_points = self._safe_list(payload.get("focus_points", []))
        compliance_targets = self._safe_list(payload.get("compliance_targets", []))
        section_rules = self._safe_list(payload.get("section_rules", []))
        retrieved_materials = payload.get("retrieved_materials", []) if isinstance(payload.get("retrieved_materials", []), list) else []
        coverage_by_task = payload.get("coverage_by_task", {}) if isinstance(payload.get("coverage_by_task", {}), dict) else {}
        retrieval_blueprint = payload.get("retrieval_blueprint", {}) if isinstance(payload.get("retrieval_blueprint", {}), dict) else {}
        classified_materials = payload.get("classified_materials", {}) if isinstance(payload.get("classified_materials", {}), dict) else {}
        evidence_bundles_by_task = payload.get("evidence_bundles_by_task", {}) if isinstance(payload.get("evidence_bundles_by_task", {}), dict) else {}
        missing_rows = payload.get("missing_evidence_by_task", []) if isinstance(payload.get("missing_evidence_by_task", []), list) else []
        missing_by_task = {
            str(item.get("task_code", "") or "").strip(): self._safe_list(item.get("missing_information", []))
            for item in missing_rows
            if isinstance(item, dict) and str(item.get("task_code", "") or "").strip()
        }
        evidence_refs: List[str] = []
        for item in retrieved_materials[:5]:
            if not isinstance(item, dict):
                continue
            title = self._material_display_name(item)
            if title:
                evidence_refs.append(title)
        medical_key_entities = self._build_fallback_medical_key_entities(payload)
        medical_key_data_points = self._build_fallback_medical_key_data_points(payload)
        task_questions = payload.get("task_questions", []) if isinstance(payload.get("task_questions", []), list) else []
        review_tasks = self._normalize_review_tasks(payload.get("review_tasks", []))
        reasoning_chain_items: List[Dict[str, str]] = []
        task_verdicts: List[Dict[str, str]] = []
        source_tasks = task_questions or review_tasks
        supported_points: List[str] = []
        missing_points: List[str] = []
        unsupported_points: List[str] = []
        risk_points: List[str] = []
        rule_findings: List[Dict[str, str]] = []
        if source_tasks:
            for index, item in enumerate(source_tasks[:6], start=1):
                task_code = str(item.get("task_code", "") or f"task_{index}").strip() or f"task_{index}"
                review_object = str(item.get("review_object", "") or "").strip()
                task_question = self._build_explicit_task_question(
                    task_question=str(item.get("task_question", "") or ""),
                    review_object=review_object,
                    rule_targets=self._safe_list(item.get("judgment_basis", [])) or self._safe_list(item.get("rule_targets", [])),
                    comparison_axes=self._safe_list(item.get("comparison_axes", [])),
                    facts=facts,
                )
                rule_requirement = "；".join(
                    self._safe_list(item.get("judgment_basis", []))
                    or self._safe_list(item.get("rule_targets", []))
                    or self._safe_list(retrieval_blueprint.get("required_rule_types", []))
                    or section_rules[:2]
                )
                coverage = coverage_by_task.get(task_code, {}) if isinstance(coverage_by_task.get(task_code, {}), dict) else {}
                evidence_bundle = (
                    evidence_bundles_by_task.get(task_code, {})
                    if isinstance(evidence_bundles_by_task.get(task_code, {}), dict)
                    else {}
                )
                approved_subset = (
                    self._safe_list(item.get("supporting_evidence_ids", []))
                    or self._safe_list(evidence_bundle.get("supporting_evidence_ids", []))
                    or self._safe_list(coverage.get("approved_evidence_ids", []))
                )
                approved_labels = self._resolve_evidence_labels(
                    self._safe_list(evidence_bundle.get("rule_evidence_ids", []))
                    + self._safe_list(evidence_bundle.get("reference_evidence_ids", []))
                    + self._safe_list(evidence_bundle.get("fact_evidence_ids", []))
                    + self._safe_list(evidence_bundle.get("experience_evidence_ids", []))
                    + approved_subset,
                    retrieved_materials,
                    limit=4,
                    allow_unmapped_text=False,
                ) or evidence_refs[:2]
                missing_information = self._safe_list(item.get("missing_information", [])) or self._safe_list(missing_by_task.get(task_code, []))
                covered_axes = self._safe_list(item.get("covered_axes", []))
                missing_axes = self._safe_list(item.get("missing_axes", []))
                coverage_ratio = item.get("coverage_ratio", 0.0)
                material_fact = self._summarize_material_facts(facts, raw_text)
                evidence_support = "；".join(approved_subset[:2] or evidence_refs[:2])
                status = "supported" if approved_labels and not missing_information and not missing_axes else "insufficient_information"
                comparison = (
                    "规则要求、原文事实与当前证据之间未见直接冲突，可形成基础判断链。"
                    if status == "supported"
                    else "当前已有规则或对象线索，但仍缺少完成判断所需的直接比较信息。"
                )
                evidence_support = "ï¼›".join(
                    (
                        self._safe_list(evidence_bundle.get("rule_evidence_ids", []))
                        + self._safe_list(evidence_bundle.get("reference_evidence_ids", []))
                        + self._safe_list(evidence_bundle.get("fact_evidence_ids", []))
                        + self._safe_list(evidence_bundle.get("experience_evidence_ids", []))
                        + approved_subset[:2]
                    )[:4]
                    or evidence_refs[:2]
                )
                evidence_support = "；".join(approved_labels)
                comparison = (
                    f"è§„åˆ™è¯æ® {len(self._safe_list(evidence_bundle.get('rule_evidence_ids', [])))} æ¡ã€�å‚ç…§è¯æ® {len(self._safe_list(evidence_bundle.get('reference_evidence_ids', [])))} æ¡ã€�äº‹å®žè¯æ® {len(self._safe_list(evidence_bundle.get('fact_evidence_ids', [])))} æ¡ï¼Œå½“å‰å¯å½¢æˆåŸºç¡€åˆ¤æ–­é“¾ã€‚"
                    if status == "supported"
                    else "å½“å‰å·²æœ‰è§„åˆ™æˆ–å¯¹è±¡çº¿ç´¢ï¼Œä½†ä»ç¼ºå°‘å®Œæˆåˆ¤æ–­æ‰€éœ€çš„è§„åˆ™è¯æ®ã€�å‚ç…§æ ‡å‡†æˆ–ç›´æŽ¥äº‹å®žè¯æ®ã€‚"
                )
                comparison = self._build_task_comparison(
                    status=status,
                    covered_axes=covered_axes,
                    missing_axes=missing_axes,
                    coverage_ratio=coverage_ratio,
                )
                judgment_reason = self._build_task_reason(
                    status=status,
                    task_question=task_question,
                    review_object=review_object,
                    rule_requirement=rule_requirement,
                    facts=facts,
                    missing_information=missing_information,
                    evidence_support=evidence_support,
                    covered_axes=covered_axes,
                    missing_axes=missing_axes,
                    coverage_ratio=coverage_ratio,
                )
                advice = self._build_task_advice(
                    status=status,
                    review_object=review_object,
                    task_question=task_question,
                    rule_requirement=rule_requirement,
                )
                statement = self._build_task_statement(
                    status=status,
                    review_object=review_object,
                    task_question=task_question,
                    rule_requirement=rule_requirement,
                    facts=facts,
                )
                reasoning_chain_items.append(
                    {
                        "task_code": task_code,
                        "rule_requirement": rule_requirement,
                        "material_fact": material_fact,
                        "evidence_support": evidence_support,
                        "comparison": comparison,
                        "judgment_reason": judgment_reason,
                    }
                )
                task_verdicts.append(
                    {
                        "task_code": task_code,
                        "status": status,
                        "task_question": task_question,
                        "problem": statement,
                        "basis": rule_requirement or evidence_support,
                        "reason": judgment_reason,
                        "advice": advice,
                    }
                )
                if status == "supported":
                    supported_points.append(statement)
                elif status == "unsupported":
                    unsupported_points.append(statement)
                    risk_points.append(rule_requirement or task_question)
                    rule_findings.append(
                        {
                            "location": "原文与证据对比后发现冲突",
                            "rule_code": f"fallback_rule_{index}",
                            "rule_text": rule_requirement,
                            "requirement_point": rule_requirement or task_question,
                            "issue_type": "unsupported",
                            "issue": statement,
                            "evidence": evidence_support,
                            "suggested_fix": advice,
                        }
                    )
                else:
                    missing_points.extend(missing_information[:3] or [task_question])
                    risk_points.append(rule_requirement or task_question)
                    rule_findings.append(
                        {
                            "location": "全文未见充分支撑",
                            "rule_code": f"fallback_rule_{index}",
                            "rule_text": rule_requirement,
                            "requirement_point": rule_requirement or task_question,
                            "issue_type": "missing",
                            "issue": statement,
                            "evidence": evidence_support,
                            "suggested_fix": advice,
                        }
                    )
        else:
            basis_points = compliance_targets or focus_points or section_rules
            missing_points = (basis_points + section_rules)[:5]
            for idx, item in enumerate(missing_points[:3], start=1):
                rule_text = section_rules[idx - 1] if idx - 1 < len(section_rules) else item
                rule_findings.append(
                    {
                        "location": "全文未见",
                        "rule_code": f"fallback_rule_{idx}",
                        "rule_text": rule_text,
                        "requirement_point": item,
                        "issue_type": "missing",
                        "issue": f"当前无法判断是否满足“{item}”",
                        "evidence": "",
                        "suggested_fix": f"请补充与“{item}”直接对应的研究资料、方法说明或法规依据。",
                    }
                )

        supported_points = self._safe_list(supported_points)
        unsupported_points = self._safe_list(unsupported_points)
        missing_points = self._safe_list(missing_points)
        risk_points = self._safe_list(risk_points or missing_points[:3])
        if task_verdicts and all(str(item.get("status", "") or "") == "supported" for item in task_verdicts):
            conclusion = "supported"
        elif task_verdicts and any(str(item.get("status", "") or "") == "unsupported" for item in task_verdicts):
            conclusion = "unsupported"
        else:
            conclusion = "insufficient_information"

        return {
            "section_id": section_id,
            "section_summary": raw_text[:240],
            "supported_points": supported_points,
            "unsupported_points": unsupported_points,
            "missing_points": missing_points,
            "risk_points": risk_points,
            "pre_review_conclusion": conclusion,
            "rule_findings": [] if conclusion == "supported" else rule_findings,
            "questions": [
                {
                    "issue": item,
                    "basis": item,
                    "requested_action": f"请补充与“{item}”直接相关的原始研究内容、方法依据或法规支持。",
                }
                for item in missing_points[:3]
            ],
            "evidence_refs": evidence_refs,
            "linked_rules": section_rules[:5],
            "section_rules": section_rules[:5],
            "fact_basis": {
                "explicit_in_text": [f"{key}：{value}" for key, value in facts.items()][:6],
                "inferred_from_evidence": evidence_refs,
                "experience_warning": [],
                "not_stated_or_uncertain": missing_points[:5],
            },
            "task_verdicts": task_verdicts,
            "reasoning_chain_items": reasoning_chain_items,
            "medical_key_entities": medical_key_entities,
            "medical_key_data_points": medical_key_data_points,
            "confidence": "low",
        }

    def _fallback_review_v2(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        section_id = str(payload.get("section_id", "") or "")
        raw_text = str(payload.get("raw_text", "") or "").strip()
        facts = self._extract_labeled_facts(raw_text)
        focus_points = self._safe_list(payload.get("focus_points", []))
        compliance_targets = self._safe_list(payload.get("compliance_targets", []))
        section_rules = self._safe_list(payload.get("section_rules", []))
        retrieved_materials = payload.get("retrieved_materials", []) if isinstance(payload.get("retrieved_materials", []), list) else []
        coverage_by_task = payload.get("coverage_by_task", {}) if isinstance(payload.get("coverage_by_task", {}), dict) else {}
        retrieval_blueprint = payload.get("retrieval_blueprint", {}) if isinstance(payload.get("retrieval_blueprint", {}), dict) else {}
        evidence_bundles_by_task = payload.get("evidence_bundles_by_task", {}) if isinstance(payload.get("evidence_bundles_by_task", {}), dict) else {}
        missing_rows = payload.get("missing_evidence_by_task", []) if isinstance(payload.get("missing_evidence_by_task", []), list) else []
        task_questions = payload.get("task_questions", []) if isinstance(payload.get("task_questions", []), list) else []
        review_tasks = self._normalize_review_tasks(payload.get("review_tasks", []))
        source_tasks = task_questions or review_tasks

        missing_by_task = {
            str(item.get("task_code", "") or "").strip(): self._safe_list(item.get("missing_information", []))
            for item in missing_rows
            if isinstance(item, dict) and str(item.get("task_code", "") or "").strip()
        }

        evidence_refs: List[str] = []
        for item in retrieved_materials[:5]:
            if not isinstance(item, dict):
                continue
            title = self._material_display_name(item)
            if title:
                evidence_refs.append(title)

        def normalize_subject(text: str) -> str:
            value = str(text or "").strip()
            if not value or self._looks_meta_review_object(value):
                if "中文名" in facts:
                    return "中文名"
                if "药品名称" in facts:
                    return "药品名称"
                if "英文化学名" in facts or "中文化学名" in facts:
                    return "化学结构信息"
                return "当前章节材料"
            return value

        def normalize_requirement(item: Dict[str, Any]) -> str:
            raw_values = (
                self._safe_list(item.get("judgment_basis", []))
                or self._safe_list(item.get("rule_targets", []))
                or self._safe_list(retrieval_blueprint.get("required_rule_types", []))
                or section_rules[:2]
            )
            fragments: List[str] = []
            for raw in raw_values:
                for fragment in re.split(r"[；;\n\r]+", str(raw or "").strip()):
                    text = str(fragment or "").strip().strip("。；;，,：: ")
                    if not text:
                        continue
                    if any(marker in text for marker in ["优先寻找", "可直接定位到章节原文", "二手转述", "不要只复述规则原文"]):
                        continue
                    if any(marker in text for marker in ["确认章节", "核对资料是否", "核对章节结论是否", "完成申报所需"]):
                        continue
                    if text not in fragments:
                        fragments.append(text)
            return fragments[0] if fragments else ""

        medical_key_entities = self._build_fallback_medical_key_entities(payload)
        medical_key_data_points = self._build_fallback_medical_key_data_points(payload)
        reasoning_chain_items: List[Dict[str, str]] = []
        task_verdicts: List[Dict[str, str]] = []
        supported_points: List[str] = []
        missing_points: List[str] = []
        unsupported_points: List[str] = []
        risk_points: List[str] = []
        rule_findings: List[Dict[str, str]] = []
        questions: List[Dict[str, str]] = []
        seen_signatures = set()

        if source_tasks:
            for index, item in enumerate(source_tasks[:8], start=1):
                task_code = str(item.get("task_code", "") or f"task_{index}").strip() or f"task_{index}"
                rule_requirement = normalize_requirement(item)
                subject = normalize_subject(str(item.get("review_object", "") or ""))
                coverage = coverage_by_task.get(task_code, {}) if isinstance(coverage_by_task.get(task_code, {}), dict) else {}
                evidence_bundle = evidence_bundles_by_task.get(task_code, {}) if isinstance(evidence_bundles_by_task.get(task_code, {}), dict) else {}
                approved_subset = (
                    self._safe_list(item.get("supporting_evidence_ids", []))
                    or self._safe_list(evidence_bundle.get("supporting_evidence_ids", []))
                    or self._safe_list(coverage.get("approved_evidence_ids", []))
                )
                approved_labels = self._resolve_evidence_labels(
                    self._safe_list(evidence_bundle.get("rule_evidence_ids", []))
                    + self._safe_list(evidence_bundle.get("reference_evidence_ids", []))
                    + self._safe_list(evidence_bundle.get("fact_evidence_ids", []))
                    + self._safe_list(evidence_bundle.get("experience_evidence_ids", []))
                    + approved_subset,
                    retrieved_materials,
                    limit=4,
                    allow_unmapped_text=False,
                ) or evidence_refs[:2]
                missing_information = self._safe_list(item.get("missing_information", [])) or self._safe_list(missing_by_task.get(task_code, []))
                covered_axes = self._safe_list(item.get("covered_axes", []))
                missing_axes = self._safe_list(item.get("missing_axes", []))
                coverage_ratio = item.get("coverage_ratio", 0.0)
                evidence_support = "；".join(
                    (
                        self._safe_list(evidence_bundle.get("rule_evidence_ids", []))
                        + self._safe_list(evidence_bundle.get("reference_evidence_ids", []))
                        + self._safe_list(evidence_bundle.get("fact_evidence_ids", []))
                        + self._safe_list(evidence_bundle.get("experience_evidence_ids", []))
                        + approved_subset
                    )[:4]
                )
                evidence_support = "；".join(approved_labels)
                material_fact = self._summarize_material_facts(facts, raw_text)
                status = "supported" if approved_labels and not missing_information and not missing_axes else "insufficient_information"
                signature = "|".join(
                    [
                        status,
                        self._compact_text(subject, max_len=64),
                        self._compact_text(rule_requirement, max_len=96),
                    ]
                )
                if signature in seen_signatures:
                    continue
                seen_signatures.add(signature)

                if status == "supported":
                    problem = f"{subject}满足“{rule_requirement or '当前规则要求'}”"
                    comparison = "规则依据、对照信息与章节事实能够形成直接支撑判断的证据组合。"
                else:
                    problem = f"当前证据不足，无法判断{subject}是否满足“{rule_requirement or '当前规则要求'}”"
                    comparison = "已检索到部分规则或线索，但缺少能够直接完成比对判断的章节事实、公开标准信息或原始支撑资料。"

                task_question = str(item.get("task_question", "") or "").strip() or problem
                comparison = self._build_task_comparison(
                    status=status,
                    covered_axes=covered_axes,
                    missing_axes=missing_axes,
                    coverage_ratio=coverage_ratio,
                )
                reason = self._build_task_reason(
                    status=status,
                    task_question=task_question,
                    review_object=subject,
                    rule_requirement=rule_requirement,
                    facts=facts,
                    missing_information=missing_information,
                    evidence_support=evidence_support,
                    covered_axes=covered_axes,
                    missing_axes=missing_axes,
                    coverage_ratio=coverage_ratio,
                )
                advice = self._build_task_advice(
                    status=status,
                    review_object=subject,
                    task_question=task_question,
                    rule_requirement=rule_requirement,
                )

                reasoning_chain_items.append(
                    {
                        "task_code": task_code,
                        "rule_requirement": rule_requirement,
                        "material_fact": material_fact,
                        "evidence_support": evidence_support,
                        "comparison": comparison,
                        "judgment_reason": reason,
                    }
                )
                task_verdicts.append(
                    {
                        "task_code": task_code,
                        "status": status,
                        "task_question": task_question,
                        "problem": problem,
                        "basis": rule_requirement or evidence_support,
                        "reason": reason,
                        "advice": advice,
                    }
                )

                if status == "supported":
                    supported_points.append(problem)
                else:
                    missing_points.extend(missing_information[:3] or [rule_requirement or subject])
                    risk_points.append(rule_requirement or subject)
                    rule_findings.append(
                        {
                            "location": "全文未见充分支撑",
                            "rule_code": str(item.get("rule_code", "") or task_code),
                            "rule_text": rule_requirement,
                            "requirement_point": rule_requirement or subject,
                            "issue_type": "missing",
                            "issue": problem,
                            "evidence": evidence_support,
                            "suggested_fix": advice,
                        }
                    )
                    questions.append(
                        {
                            "issue": problem,
                            "basis": rule_requirement or subject,
                            "requested_action": advice,
                        }
                    )
        else:
            basis_points = compliance_targets or focus_points or section_rules
            for idx, item in enumerate((basis_points + section_rules)[:3], start=1):
                problem = f"当前证据不足，无法判断当前章节材料是否满足“{item}”"
                advice = f"请围绕“{item}”补充可直接支撑判断的章节事实、公开标准信息或原始研究资料，不要只复述规则原文。"
                missing_points.append(item)
                risk_points.append(item)
                rule_findings.append(
                    {
                        "location": "全文未见充分支撑",
                        "rule_code": f"fallback_task_{idx}",
                        "rule_text": item,
                        "requirement_point": item,
                        "issue_type": "missing",
                        "issue": problem,
                        "evidence": "",
                        "suggested_fix": advice,
                    }
                )
                questions.append({"issue": problem, "basis": item, "requested_action": advice})

        task_verdicts = self._normalize_task_verdicts(task_verdicts)
        reasoning_chain_items = self._normalize_reasoning_chain_items(reasoning_chain_items)
        rule_findings = self._normalize_rule_findings(rule_findings)
        questions = self._refine_questions(questions, rule_findings)
        supported_points = self._safe_list(supported_points)
        unsupported_points = self._safe_list(unsupported_points)
        missing_points = self._safe_list(missing_points)
        risk_points = self._safe_list(risk_points or missing_points[:3])

        if task_verdicts and all(str(item.get("status", "") or "") == "supported" for item in task_verdicts):
            conclusion = "supported"
        elif task_verdicts and any(str(item.get("status", "") or "") == "unsupported" for item in task_verdicts):
            conclusion = "unsupported"
        else:
            conclusion = "insufficient_information"

        return {
            "section_id": section_id,
            "section_summary": raw_text[:240],
            "supported_points": supported_points,
            "unsupported_points": unsupported_points,
            "missing_points": missing_points,
            "risk_points": risk_points,
            "pre_review_conclusion": conclusion,
            "rule_findings": [] if conclusion == "supported" else rule_findings,
            "questions": questions,
            "evidence_refs": self._safe_list(evidence_refs),
            "linked_rules": section_rules[:5],
            "section_rules": section_rules[:5],
            "fact_basis": {
                "explicit_in_text": [f"{key}：{value}" for key, value in facts.items()][:6],
                "inferred_from_evidence": self._safe_list(evidence_refs),
                "experience_warning": [],
                "not_stated_or_uncertain": missing_points[:5],
            },
            "task_verdicts": task_verdicts,
            "reasoning_chain_items": reasoning_chain_items,
            "medical_key_entities": medical_key_entities,
            "medical_key_data_points": medical_key_data_points,
            "confidence": "medium" if evidence_refs else "low",
        }

    def review(self, payload: Dict[str, Any], prompt_config: Dict[str, Any] | None = None) -> Dict[str, Any]:
        prompt_payload = self._build_review_prompt_payload(payload)
        prompt = self.prompts.render("chapter_reviewer.j2", prompt_payload, prompt_config=prompt_config or {})
        default_data = self._fallback_review_v2(payload)
        raw = self.llm.chat(
            messages=self.envelopes.build("reviewer", prompt),
            default=json.dumps(default_data, ensure_ascii=False),
        )
        parsed = self.llm.extract_json(raw)
        validate_review_output(parsed, 'chapter_reviewer')
        llm_execution = self._build_llm_execution_meta(
            agent_name="reviewer",
            raw_text=raw,
            parsed_payload=parsed,
        )
        data = parsed
        data["llm_execution"] = llm_execution
        data.setdefault("section_id", str(payload.get("section_id", "") or ""))
        for key in ["supported_points", "unsupported_points", "missing_points", "risk_points", "evidence_refs", "linked_rules", "section_rules"]:
            data[key] = self._safe_list(data.get(key, []))
        data["rule_findings"] = self._refine_rule_findings(data.get("rule_findings", []))

        questions = []
        if isinstance(data.get("questions", []), list):
            for item in data.get("questions", []):
                if not isinstance(item, dict):
                    continue
                issue = str(item.get("issue", "") or "").strip()
                basis = str(item.get("basis", "") or "").strip()
                requested_action = str(item.get("requested_action", "") or "").strip()
                if issue and basis and requested_action:
                    questions.append({
                        "issue": issue,
                        "basis": basis,
                        "requested_action": requested_action,
                    })
        data["questions"] = self._refine_questions(questions, data["rule_findings"])

        derived_points = self._derive_negative_points_from_rule_findings(data["rule_findings"])
        if not data["missing_points"]:
            data["missing_points"] = derived_points["missing_points"]
        if not data["unsupported_points"]:
            data["unsupported_points"] = derived_points["unsupported_points"]
        if not data["risk_points"]:
            data["risk_points"] = derived_points["risk_points"]
        if not data["questions"] and data["rule_findings"]:
            data["questions"] = self._derive_questions_from_rule_findings(data["rule_findings"])

        fact_basis = data.get("fact_basis", {})
        if not isinstance(fact_basis, dict):
            fact_basis = {}
        data["fact_basis"] = {
            "explicit_in_text": self._safe_list(fact_basis.get("explicit_in_text", [])),
            "inferred_from_evidence": self._safe_list(
                fact_basis.get("inferred_from_evidence", []) or fact_basis.get("supported_by_retrieval", [])
            ),
            "experience_warning": self._safe_list(
                fact_basis.get("experience_warning", []) or fact_basis.get("based_on_experience_warning", [])
            ),
            "not_stated_or_uncertain": self._safe_list(fact_basis.get("not_stated_or_uncertain", [])),
        }
        data["medical_key_entities"] = self._normalize_medical_key_entities(data.get("medical_key_entities", []))
        if not data["medical_key_entities"]:
            data["medical_key_entities"] = self._build_fallback_medical_key_entities(payload)
        data["medical_key_data_points"] = self._normalize_medical_key_data_points(data.get("medical_key_data_points", []))
        if not data["medical_key_data_points"]:
            data["medical_key_data_points"] = self._build_fallback_medical_key_data_points(payload)
        data["task_verdicts"] = self._normalize_task_verdicts(data.get("task_verdicts", [])) or default_data.get("task_verdicts", [])
        data["reasoning_chain_items"] = self._normalize_reasoning_chain_items(data.get("reasoning_chain_items", [])) or default_data.get("reasoning_chain_items", [])
        self._apply_task_question_reasoning_overrides(payload, data)
        self._enforce_rule_finding_task_consistency(data)

        if not data["linked_rules"] and data["rule_findings"]:
            linked_rules: List[str] = []
            for item in data["rule_findings"]:
                rule_code = str(item.get("rule_code", "") or "").strip()
                rule_text = str(item.get("rule_text", "") or "").strip()
                linked = f"{rule_code}:{rule_text}" if rule_code and rule_text else (rule_code or rule_text)
                if linked:
                    linked_rules.append(linked)
            data["linked_rules"] = self._safe_list(linked_rules)
        if not data["evidence_refs"]:
            data["evidence_refs"] = self._derive_evidence_refs(payload, data["rule_findings"])

        confidence = str(data.get("confidence", "medium") or "medium").strip().lower()
        if confidence not in {"low", "medium", "high"}:
            confidence = "medium"
        data["confidence"] = confidence

        task_statuses = [
            str(item.get("status", "") or "").strip().lower()
            for item in data.get("task_verdicts", [])
            if isinstance(item, dict)
        ]
        has_task_verdicts = bool(task_statuses)
        has_unsupported_tasks = any(status == "unsupported" for status in task_statuses)
        has_insufficient_tasks = any(status == "insufficient_information" for status in task_statuses)
        hard_fail_rule_findings = any(
            isinstance(item, dict) and str(item.get("issue_type", "") or "").strip().lower() in {"unsupported", "conflict"}
            for item in data.get("rule_findings", [])
        )
        conclusion = str(data.get("pre_review_conclusion", "") or "").strip()
        if conclusion == "partially_supported":
            if data["unsupported_points"] or has_unsupported_tasks or hard_fail_rule_findings or (not has_task_verdicts and data["rule_findings"]):
                conclusion = "unsupported"
            elif data["missing_points"] or data["questions"] or data["rule_findings"] or has_insufficient_tasks:
                conclusion = "insufficient_information"
            elif data["supported_points"]:
                conclusion = "supported"
            else:
                conclusion = "insufficient_information"
        if conclusion == "supported" and (
            data["unsupported_points"] or data["missing_points"] or data["questions"] or data["rule_findings"] or has_unsupported_tasks or has_insufficient_tasks
        ):
            if data["unsupported_points"] or has_unsupported_tasks or hard_fail_rule_findings or (not has_task_verdicts and data["rule_findings"]):
                conclusion = "unsupported"
            else:
                conclusion = "insufficient_information"
        if conclusion not in {"supported", "unsupported", "insufficient_information"}:
            if data["unsupported_points"] or has_unsupported_tasks or hard_fail_rule_findings or (not has_task_verdicts and data["rule_findings"]):
                conclusion = "unsupported"
            elif data["missing_points"] or data["questions"] or data["rule_findings"] or has_insufficient_tasks:
                conclusion = "insufficient_information"
            elif data["supported_points"]:
                conclusion = "supported"
            else:
                conclusion = default_data["pre_review_conclusion"]
        data["pre_review_conclusion"] = conclusion
        if conclusion == "supported":
            if not data["supported_points"]:
                derived_supported = [
                    str(item.get("problem", "") or "").strip()
                    for item in data.get("task_verdicts", [])
                    if isinstance(item, dict) and str(item.get("status", "") or "") == "supported"
                ]
                if derived_supported:
                    data["supported_points"] = self._safe_list(derived_supported)
                else:
                    basis_points = (
                        self._safe_list(payload.get("compliance_targets", []))
                        or self._safe_list(payload.get("focus_points", []))
                        or self._safe_list(payload.get("section_rules", []))
                    )
                    data["supported_points"] = [
                        f"申报资料已对“{item}”给出对应材料、数据或论证说明。"
                        for item in basis_points[:5]
                        if str(item or "").strip()
                    ]
            data["rule_findings"] = []
            data["unsupported_points"] = []
            data["missing_points"] = []
            data["questions"] = []
        if not data["task_verdicts"]:
            facts = self._extract_labeled_facts(str(payload.get("raw_text", "") or ""))
            source_tasks = self._normalize_review_tasks(payload.get("review_tasks", []))
            source_questions = [
                dict(item)
                for item in payload.get("task_questions", [])
                if isinstance(payload.get("task_questions", []), list) and isinstance(item, dict)
            ]
            for index, item in enumerate(source_questions or source_tasks, start=1):
                task_code = str(item.get("task_code", "") or f"task_{index}").strip() or f"task_{index}"
                review_object = str(item.get("review_object", "") or "").strip()
                task_question = self._build_explicit_task_question(
                    task_question=str(item.get("task_question", "") or ""),
                    review_object=review_object,
                    rule_targets=self._safe_list(item.get("judgment_basis", [])) or self._safe_list(item.get("rule_targets", [])),
                    comparison_axes=self._safe_list(item.get("comparison_axes", [])),
                    facts=facts,
                ) or f"当前章节是否满足任务 {task_code} 要求？"
                basis = "；".join(self._safe_list(item.get("judgment_basis", [])) or self._safe_list(item.get("rule_targets", [])) or data.get("linked_rules", [])[:2])
                problem = self._build_task_statement(
                    status=conclusion,
                    review_object=review_object,
                    task_question=task_question,
                    rule_requirement=basis,
                    facts=facts,
                )
                data["task_verdicts"].append(
                    {
                        "task_code": task_code,
                        "status": conclusion,
                        "task_question": task_question,
                        "problem": problem,
                        "basis": basis,
                        "reason": "章节原文与通过证据能够支撑当前任务判断。" if conclusion == "supported" else "当前任务缺少直接支撑或存在规则-事实不匹配。",
                        "advice": self._build_task_advice(
                            status=conclusion,
                            review_object=review_object,
                            task_question=task_question,
                            rule_requirement=basis,
                        ),
                    }
                )
        if not data["reasoning_chain_items"] and data["task_verdicts"]:
            for item in data["task_verdicts"][:8]:
                if not isinstance(item, dict):
                    continue
                data["reasoning_chain_items"].append(
                    {
                        "task_code": str(item.get("task_code", "") or "").strip(),
                        "rule_requirement": str(item.get("basis", "") or "").strip(),
                        "material_fact": "；".join(data.get("fact_basis", {}).get("explicit_in_text", [])[:2]) if isinstance(data.get("fact_basis", {}), dict) else "",
                        "evidence_support": "；".join(data.get("evidence_refs", [])[:2]),
                        "comparison": "当前任务的规则要求、章节事实与证据支持能够闭合。" if str(item.get("status", "") or "") == "supported" else "当前任务的规则要求、章节事实与证据支持尚未闭合。",
                        "judgment_reason": str(item.get("reason", "") or "").strip(),
                    }
                )
        data["problem_basis_advice_items"] = self._build_problem_basis_advice_items(payload, data)
        return data

    def _summarize_reference_examples(self, values: Any, limit: int = 2) -> List[Dict[str, str]]:
        out: List[Dict[str, str]] = []
        if not isinstance(values, list):
            return out
        for item in values:
            if not isinstance(item, dict):
                continue
            title = self._compact_text(
                str(
                    item.get("title", "")
                    or item.get("reference_title", "")
                    or item.get("example_title", "")
                    or item.get("task_question", "")
                    or ""
                ),
                max_len=72,
            )
            example = self._compact_text(
                str(
                    item.get("content", "")
                    or item.get("reference_content", "")
                    or item.get("revised_output", "")
                    or item.get("expected_output", "")
                    or ""
                ),
                max_len=180,
            )
            if not title and not example:
                continue
            out.append({"title": title, "example": example})
            if len(out) >= limit:
                break
        return out

    def _summarize_historical_experience(self, values: Any, limit: int = 4) -> List[str]:
        out: List[str] = []
        seen = set()
        if not isinstance(values, list):
            return out
        for item in values:
            if isinstance(item, dict):
                text = str(item.get("content", "") or item.get("experience", "") or item.get("summary", "") or "").strip()
            else:
                text = str(item or "").strip()
            compact = self._compact_text(text, max_len=140)
            if not compact or compact in seen:
                continue
            seen.add(compact)
            out.append(compact)
            if len(out) >= limit:
                break
        return out

    def _build_task_comparison(
        self,
        *,
        status: str,
        covered_axes: List[str] | None = None,
        missing_axes: List[str] | None = None,
        coverage_ratio: Any = None,
    ) -> str:
        covered_text = self._format_axis_text(covered_axes or [])
        missing_text = self._format_axis_text(missing_axes or [])
        ratio = self._safe_float(coverage_ratio)
        ratio_text = ""
        if ratio is not None:
            ratio_text = f"已覆盖约 {int(round(max(0.0, min(1.0, ratio)) * 100))}% 的判断维度。"

        if covered_text or missing_text:
            if status == "supported":
                segments = []
                if covered_text:
                    segments.append(f"章节已直接给出{covered_text}等与本条判断相关的关键信息")
                if ratio_text:
                    segments.append(ratio_text)
                segments.append("这些已覆盖维度能够与规则要求逐项对应，足以支撑当前判断。")
                return "，".join(segment.strip("。") for segment in segments if segment)
            segments = []
            if covered_text:
                segments.append(f"章节目前仅见{covered_text}")
            if missing_text:
                segments.append(f"但未见{missing_text}")
            if ratio_text:
                segments.append(ratio_text)
            segments.append("因此目前只能形成部分维度覆盖，尚不足以直接得出“已满足”结论。")
            return "，".join(segment.strip("。") for segment in segments if segment)

        return "当前任务的规则要求、章节事实与证据支持尚未形成具体的维度化对比结果。"

    def _build_task_reason(
        self,
        *,
        status: str,
        task_question: str,
        review_object: str,
        rule_requirement: str,
        facts: Dict[str, str],
        missing_information: List[str],
        evidence_support: str,
        covered_axes: List[str] | None = None,
        missing_axes: List[str] | None = None,
        coverage_ratio: Any = None,
    ) -> str:
        covered_text = self._format_axis_text(covered_axes or [])
        missing_text = self._format_axis_text(missing_axes or [])
        ratio = self._safe_float(coverage_ratio)
        if status == "supported":
            if self._is_naming_task(review_object, task_question, rule_requirement) and facts.get("中文名") and facts.get("英文名"):
                chinese_name = facts.get("中文名", "")
                english_name = facts.get("英文名", "")
                return (
                    f"章节原文记载中文名“{chinese_name}”和英文名“{english_name}”，"
                    f"结合“{rule_requirement or '相关命名要求'}”及现有证据“{evidence_support or '已检索到的规则/标准资料'}”，"
                    "当前能够形成对命名判断的直接支撑。"
                )
            if covered_text:
                if evidence_support:
                    return (
                        f"章节中已能找到{covered_text}等直接信息，且这些内容可以与证据“{evidence_support}”逐项对应，"
                        "说明支撑当前判断所需的关键维度已经闭合。"
                    )
                return f"章节已提供{covered_text}等直接信息，这些内容能够与“{rule_requirement or '相关要求'}”逐项对应，因此可以支撑当前判断。"
            fact_summary = self._summarize_material_facts(facts, "")
            if fact_summary and evidence_support:
                return (
                    f"章节事实“{fact_summary}”与证据“{evidence_support}”能够相互印证，"
                    f"可为“{rule_requirement or '相关要求'}”提供直接支撑。"
                )
            if fact_summary:
                return f"章节事实“{fact_summary}”已能直接回应当前规则要求，因而可支持本条判断。"
            if evidence_support:
                return f"结合证据“{evidence_support}”，当前已形成对“{rule_requirement or '相关要求'}”的直接支撑。"
            return f"当前已形成对“{rule_requirement or '相关要求'}”的直接支撑。"
        if status == "unsupported":
            return f"现有证据显示当前任务与“{rule_requirement or '相关要求'}”存在直接不匹配。"
        if covered_text or missing_text:
            if covered_text and missing_text:
                return f"章节目前仅见{covered_text}，但未见{missing_text}相关信息或直接支撑，因此不足以判断已覆盖主要审评维度。"
            if missing_text:
                return f"章节未见{missing_text}相关信息或直接支撑，当前不足以完成“{rule_requirement or '相关要求'}”的判断。"
            if covered_text:
                return f"章节仅能说明{covered_text}等部分维度，仍缺少完成全面判断所需的规则或对照信息。"
        if ratio is not None and ratio < 0.75:
            return f"当前仅覆盖约 {int(round(max(0.0, min(1.0, ratio)) * 100))}% 的判断维度，尚不足以得出稳定结论。"
        if missing_information:
            return f"缺少足以完成判断的信息：{'；'.join(missing_information[:3])}"
        return "缺少足以完成判断的直接证据。"

    @classmethod
    def _summarize_material_facts(cls, facts: Dict[str, str], raw_text: str) -> str:
        if facts:
            summary = "；".join(
                [
                    f"{label}：{value}"
                    for label, value in facts.items()
                    if str(value or "").strip()
                ][:4]
            )
            if summary:
                return cls._compact_text(summary, max_len=180)
        text = str(raw_text or "").strip()
        if not text:
            return ""
        labeled_segments: List[str] = []
        seen = set()
        for match in re.finditer(r"([^\n\r：:]{1,18}[：:][^\n\r；。]{1,120})", text):
            segment = re.sub(r"\s+", " ", str(match.group(1) or "").strip())
            if (
                not segment
                or segment.startswith("[")
                or segment.startswith("#")
                or "CTD 路径" in segment
                or "章节摘要" in segment
            ):
                continue
            if segment in seen:
                continue
            seen.add(segment)
            labeled_segments.append(segment)
            if len(labeled_segments) >= 3:
                break
        if labeled_segments:
            return cls._compact_text("；".join(labeled_segments), max_len=180)
        clean_lines: List[str] = []
        for line in re.split(r"[\r\n]+", text):
            line = re.sub(r"\s+", " ", str(line or "").strip())
            if not line or line.startswith("#") or line.startswith("[") or "CTD 路径" in line:
                continue
            clean_lines.append(line)
            if len(clean_lines) >= 2:
                break
        return cls._compact_text("；".join(clean_lines), max_len=180)

    def _apply_task_question_reasoning_overrides(self, payload: Dict[str, Any], data: Dict[str, Any]) -> None:
        source_questions = payload.get("task_questions", []) if isinstance(payload.get("task_questions", []), list) else []
        if not source_questions or not isinstance(data, dict):
            return

        task_meta: Dict[str, Dict[str, Any]] = {}
        for index, item in enumerate(source_questions, start=1):
            if not isinstance(item, dict):
                continue
            task_code = str(item.get("task_code", "") or f"task_{index}").strip() or f"task_{index}"
            task_meta[task_code] = dict(item)

        if not task_meta or not isinstance(data.get("task_verdicts", []), list):
            return

        complex_question_types = {
            "physicochemical_conformance",
            "impurity_control_conformance",
            "structure_conformance",
        }
        facts = self._extract_labeled_facts(str(payload.get("raw_text", "") or ""))
        reasoning_map: Dict[str, Dict[str, Any]] = {}
        for row in data.get("reasoning_chain_items", []):
            if not isinstance(row, dict):
                continue
            task_code = str(row.get("task_code", "") or "").strip()
            if task_code:
                reasoning_map[task_code] = row

        derived_supported: List[str] = []
        derived_missing: List[str] = []
        derived_unsupported: List[str] = []
        derived_risk: List[str] = []
        derived_findings: List[Dict[str, str]] = []
        derived_questions: List[Dict[str, str]] = []
        touched = False

        for index, item in enumerate(data.get("task_verdicts", []), start=1):
            if not isinstance(item, dict):
                continue
            task_code = str(item.get("task_code", "") or f"task_{index}").strip() or f"task_{index}"
            meta = task_meta.get(task_code)
            if not meta:
                continue

            covered_axes = self._safe_list(meta.get("covered_axes", []))
            missing_axes = self._safe_list(meta.get("missing_axes", []))
            coverage_ratio = meta.get("coverage_ratio", 0.0)
            question_type = str(meta.get("question_type", "") or "").strip()
            if not covered_axes and not missing_axes and question_type not in complex_question_types:
                continue

            current_status = str(item.get("status", "") or "").strip().lower()
            ratio = self._safe_float(coverage_ratio)
            if current_status == "supported":
                if missing_axes:
                    item["status"] = "insufficient_information"
                    current_status = "insufficient_information"
                    touched = True
                elif question_type in complex_question_types and ratio is not None and ratio < 0.75:
                    item["status"] = "insufficient_information"
                    current_status = "insufficient_information"
                    touched = True

            rule_requirement = str(item.get("basis", "") or "").strip() or "；".join(self._safe_list(meta.get("judgment_basis", []))[:1])
            review_object = str(meta.get("review_object", "") or "").strip()
            task_question = str(item.get("task_question", "") or meta.get("task_question", "") or "").strip()
            reasoning_row = reasoning_map.get(task_code)
            evidence_support = ""
            if isinstance(reasoning_row, dict):
                evidence_support = str(reasoning_row.get("evidence_support", "") or "").strip()

            comparison = self._build_task_comparison(
                status=current_status,
                covered_axes=covered_axes,
                missing_axes=missing_axes,
                coverage_ratio=coverage_ratio,
            )
            reason = self._build_task_reason(
                status=current_status,
                task_question=task_question,
                review_object=review_object,
                rule_requirement=rule_requirement,
                facts=facts,
                missing_information=self._safe_list(meta.get("missing_information", [])),
                evidence_support=evidence_support,
                covered_axes=covered_axes,
                missing_axes=missing_axes,
                coverage_ratio=coverage_ratio,
            )

            item["problem"] = self._build_task_statement(
                status=current_status,
                review_object=review_object,
                task_question=task_question,
                rule_requirement=rule_requirement,
                facts=facts,
            )
            item["basis"] = rule_requirement
            item["reason"] = reason
            item["advice"] = self._build_task_advice(
                status=current_status,
                review_object=review_object,
                task_question=task_question,
                rule_requirement=rule_requirement,
            )

            if isinstance(reasoning_row, dict):
                reasoning_row["rule_requirement"] = rule_requirement
                reasoning_row["comparison"] = comparison
                reasoning_row["judgment_reason"] = reason
                if not str(reasoning_row.get("material_fact", "") or "").strip():
                    reasoning_row["material_fact"] = self._summarize_material_facts(facts, str(payload.get("raw_text", "") or ""))

            if current_status == "supported":
                derived_supported.append(str(item.get("problem", "") or "").strip())
            elif current_status == "unsupported":
                derived_unsupported.append(str(item.get("problem", "") or "").strip())
                derived_risk.append(rule_requirement or task_question)
                derived_findings.append(
                    {
                        "location": "原文与对照信息不一致",
                        "rule_code": task_code,
                        "rule_text": rule_requirement,
                        "requirement_point": rule_requirement or task_question,
                        "issue_type": "unsupported",
                        "issue": str(item.get("problem", "") or "").strip(),
                        "evidence": evidence_support,
                        "suggested_fix": str(item.get("advice", "") or "").strip(),
                    }
                )
            else:
                derived_missing.extend(missing_axes[:4] or self._safe_list(meta.get("missing_information", []))[:4] or [rule_requirement or task_question])
                derived_risk.append(rule_requirement or task_question)
                derived_findings.append(
                    {
                        "location": "全文未见充分支撑",
                        "rule_code": task_code,
                        "rule_text": rule_requirement,
                        "requirement_point": rule_requirement or task_question,
                        "issue_type": "missing",
                        "issue": str(item.get("problem", "") or "").strip(),
                        "evidence": evidence_support,
                        "suggested_fix": str(item.get("advice", "") or "").strip(),
                    }
                )
                derived_questions.append(
                    {
                        "issue": str(item.get("problem", "") or "").strip(),
                        "basis": rule_requirement or task_question,
                        "requested_action": str(item.get("advice", "") or "").strip(),
                    }
                )

        if not touched and not derived_findings and not derived_supported and not derived_missing and not derived_unsupported:
            return

        data["supported_points"] = self._safe_list(derived_supported)
        data["missing_points"] = self._safe_list(derived_missing)
        data["unsupported_points"] = self._safe_list(derived_unsupported)
        data["risk_points"] = self._safe_list(derived_risk)
        data["rule_findings"] = self._refine_rule_findings(derived_findings)
        data["questions"] = self._refine_questions(derived_questions, data["rule_findings"])

    def _build_planner_prompt_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        task_definition = payload.get("task_definition", {}) if isinstance(payload.get("task_definition", {}), dict) else {}
        section_review_profile = payload.get("section_review_profile", {}) if isinstance(payload.get("section_review_profile", {}), dict) else {}
        decision_rules: List[str] = []
        for values in (
            payload.get("section_rules", []),
            payload.get("compliance_targets", []),
            payload.get("focus_points", []),
        ):
            for raw in self._safe_list(values):
                for fragment in re.split(r"[\uff1b;\n\r]+", str(raw or "").strip()):
                    text = str(fragment or "").strip().strip("\u3002\uff1b;\uff0c,\uff1a: ")
                    if not text:
                        continue
                    if any(marker in text for marker in ["\u4f18\u5148\u5bfb\u627e", "\u53ef\u76f4\u63a5\u5b9a\u4f4d\u5230\u7ae0\u8282\u539f\u6587", "\u4e8c\u624b\u8f6c\u8ff0", "\u4e0d\u8981\u53ea\u590d\u8ff0\u89c4\u5219\u539f\u6587"]):
                        continue
                    if text not in decision_rules:
                        decision_rules.append(text)
        facts = self._extract_labeled_facts(str(payload.get("raw_text", "") or ""))
        return {
            "task_id": str(payload.get("task_id", "") or "").strip(),
            "section_id": str(payload.get("section_id", "") or "").strip(),
            "section_name": str(payload.get("section_name", "") or "").strip(),
            "document_context": {
                "registration_class": self._compact_text(str(payload.get("registration_class", "") or ""), max_len=32),
                "review_domain": self._compact_text(str(payload.get("review_domain", "") or ""), max_len=32),
                "product_type": self._compact_text(str(payload.get("product_type", "") or ""), max_len=32),
            },
            "raw_text": str(payload.get("raw_text", "") or ""),
            "known_facts": facts,
            "section_review_profile": {
                "chapter_role": str(section_review_profile.get("chapter_role", "") or "").strip(),
                "core_review_question": str(section_review_profile.get("core_review_question", "") or "").strip(),
                "must_answer_questions": self._safe_list(section_review_profile.get("must_answer_questions", []))[:6],
                "core_review_principles": self._safe_list(section_review_profile.get("core_review_principles", []))[:6],
                "common_risks": self._safe_list(section_review_profile.get("common_risks", []))[:6],
                "reviewer_mindset": self._safe_list(section_review_profile.get("reviewer_mindset", []))[:5],
                "task_generation_rules": self._safe_list(section_review_profile.get("task_generation_rules", []))[:4],
            },
            "task_definition": {
                "chapter_role": str(task_definition.get("chapter_role", "") or "").strip(),
                "core_review_question": str(task_definition.get("core_review_question", "") or task_definition.get("review_goal", "") or "").strip(),
                "must_answer_questions": self._safe_list(task_definition.get("must_answer_questions", []))[:6],
                "reasoning_principles": self._safe_list(task_definition.get("reasoning_principles", []))[:6],
                "common_risks": self._safe_list(task_definition.get("common_risks", []))[:6],
                "reviewer_mindset": self._safe_list(task_definition.get("reviewer_mindset", []))[:5],
                "task_generation_rules": self._safe_list(task_definition.get("task_generation_rules", []))[:4],
                "compliance_targets": self._safe_list(payload.get("compliance_targets", []))[:6],
                "issue_hypotheses": self._safe_list(payload.get("issue_hypotheses", []))[:6],
            },
            "decision_rules": decision_rules[:8],
            "evidence_requirements": self._safe_list(payload.get("evidence_requirements", []))[:6],
            "entity_extraction_focus": self._safe_list(payload.get("medical_entity_requirements", []))[:6],
            "data_extraction_focus": self._safe_list(payload.get("medical_data_requirements", []))[:6],
            "reference_examples": self._summarize_reference_examples(payload.get("reference_examples", []), limit=2),
            "historical_lessons": self._summarize_historical_experience(payload.get("historical_experience", []), limit=4),
        }

    def _build_review_prompt_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        def _clean_judgment_basis(values: Any) -> List[str]:
            out: List[str] = []
            for raw in self._safe_list(values):
                for fragment in re.split(r"[\uff1b;\n\r]+", str(raw or "").strip()):
                    text = str(fragment or "").strip().strip("\u3002\uff1b;\uff0c,\uff1a: ")
                    if not text:
                        continue
                    if any(marker in text for marker in ["\u4f18\u5148\u5bfb\u627e", "\u53ef\u76f4\u63a5\u5b9a\u4f4d\u5230\u7ae0\u8282\u539f\u6587", "\u4e8c\u624b\u8f6c\u8ff0", "\u4e0d\u8981\u53ea\u590d\u8ff0\u89c4\u5219\u539f\u6587"]):
                        continue
                    if any(marker in text for marker in ["\u786e\u8ba4\u7ae0\u8282", "\u6838\u5bf9\u8d44\u6599\u662f\u5426", "\u6838\u5bf9\u7ae0\u8282\u7ed3\u8bba\u662f\u5426", "\u5b8c\u6210\u7533\u62a5\u6240\u9700"]):
                        continue
                    if text not in out:
                        out.append(text)
            return out

        task_questions = []
        source_questions = payload.get("task_questions", []) if isinstance(payload.get("task_questions", []), list) else []
        for item in source_questions[:8]:
            if not isinstance(item, dict):
                continue
            judgment_basis = _clean_judgment_basis(item.get("judgment_basis", []))
            if judgment_basis:
                judgment_basis = judgment_basis[:1]
            task_questions.append(
                {
                    "task_code": str(item.get("task_code", "") or "").strip(),
                    "task_question": str(item.get("task_question", "") or "").strip(),
                    "review_object": str(item.get("review_object", "") or "").strip(),
                    "question_type": str(item.get("question_type", "") or "").strip(),
                    "judgment_basis": judgment_basis,
                    "comparison_axes": self._safe_list(item.get("comparison_axes", []))[:4],
                    "comparison_targets": self._safe_list(item.get("comparison_targets", []))[:4],
                    "covered_axes": self._safe_list(item.get("covered_axes", []))[:6],
                    "missing_axes": self._safe_list(item.get("missing_axes", []))[:6],
                    "coverage_ratio": item.get("coverage_ratio", 0.0),
                    "supporting_evidence_ids": self._safe_list(item.get("supporting_evidence_ids", []))[:4],
                    "rule_evidence_ids": self._safe_list(item.get("rule_evidence_ids", []))[:3],
                    "reference_evidence_ids": self._safe_list(item.get("reference_evidence_ids", []))[:3],
                    "fact_evidence_ids": self._safe_list(item.get("fact_evidence_ids", []))[:3],
                    "missing_information": self._safe_list(item.get("missing_information", []))[:3],
                }
            )

        approved_materials = payload.get("retrieved_materials", []) if isinstance(payload.get("retrieved_materials", []), list) else []
        condensed_materials = []
        for item in approved_materials[:10]:
            if not isinstance(item, dict):
                continue
            condensed_materials.append(
                {
                    "evidence_id": str(item.get("evidence_id", "") or "").strip(),
                    "source_type": str(item.get("source_type", "") or "").strip(),
                    "title": str(item.get("title", "") or "").strip(),
                    "content": self._compact_text(str(item.get("content", "") or ""), max_len=240),
                    "comprehensive_score": item.get("comprehensive_score"),
                }
            )

        evidence_bundles_by_task = {}
        raw_bundles = payload.get("evidence_bundles_by_task", {}) if isinstance(payload.get("evidence_bundles_by_task", {}), dict) else {}
        for task_code, bundle in list(raw_bundles.items())[:8]:
            if not isinstance(bundle, dict):
                continue
            evidence_bundles_by_task[str(task_code)] = {
                "required_rule_types": self._safe_list(bundle.get("required_rule_types", []))[:3],
                "rule_evidence_ids": self._safe_list(bundle.get("rule_evidence_ids", []))[:3],
                "reference_evidence_ids": self._safe_list(bundle.get("reference_evidence_ids", []))[:3],
                "fact_evidence_ids": self._safe_list(bundle.get("fact_evidence_ids", []))[:3],
                "supporting_evidence_ids": self._safe_list(bundle.get("supporting_evidence_ids", []))[:4],
                "reference_targets": self._safe_list(bundle.get("reference_targets", []))[:4],
            }

        task_definition = payload.get("task_definition", {}) if isinstance(payload.get("task_definition", {}), dict) else {}
        section_review_profile = payload.get("section_review_profile", {}) if isinstance(payload.get("section_review_profile", {}), dict) else {}
        return {
            "task_id": str(payload.get("task_id", "") or "").strip(),
            "section_id": str(payload.get("section_id", "") or "").strip(),
            "section_name": str(payload.get("section_name", "") or "").strip(),
            "raw_text": str(payload.get("raw_text", "") or ""),
            "task_definition": {
                "chapter_role": str(task_definition.get("chapter_role", "") or "").strip(),
                "core_review_question": str(task_definition.get("core_review_question", "") or task_definition.get("review_goal", "") or "").strip(),
                "must_answer_questions": self._safe_list(task_definition.get("must_answer_questions", []))[:6],
                "reasoning_principles": self._safe_list(task_definition.get("reasoning_principles", []))[:6],
                "common_risks": self._safe_list(task_definition.get("common_risks", []))[:6],
                "reviewer_mindset": self._safe_list(task_definition.get("reviewer_mindset", []))[:5],
                "evidence_judgment_rules": self._safe_list(task_definition.get("evidence_judgment_rules", []))[:4],
                "conclusion_style_rules": self._safe_list(task_definition.get("conclusion_style_rules", []))[:4],
            },
            "section_review_profile": {
                "chapter_role": str(section_review_profile.get("chapter_role", "") or "").strip(),
                "core_review_question": str(section_review_profile.get("core_review_question", "") or "").strip(),
                "must_answer_questions": self._safe_list(section_review_profile.get("must_answer_questions", []))[:6],
                "core_review_principles": self._safe_list(section_review_profile.get("core_review_principles", []))[:6],
                "common_risks": self._safe_list(section_review_profile.get("common_risks", []))[:6],
                "reviewer_mindset": self._safe_list(section_review_profile.get("reviewer_mindset", []))[:5],
                "evidence_judgment_rules": self._safe_list(section_review_profile.get("evidence_judgment_rules", []))[:4],
                "conclusion_style_rules": self._safe_list(section_review_profile.get("conclusion_style_rules", []))[:4],
            },
            "task_questions": task_questions,
            "approved_materials": condensed_materials,
            "evidence_bundles_by_task": evidence_bundles_by_task,
            "extracted_entities": payload.get("extracted_entities", {}) if isinstance(payload.get("extracted_entities", {}), dict) else {},
            "method_judgment": payload.get("method_judgment", {}) if isinstance(payload.get("method_judgment", {}), dict) else {},
            "reference_examples": self._summarize_reference_examples(payload.get("reference_examples", []), limit=2),
            "entity_extraction_focus": self._safe_list(payload.get("medical_entity_requirements", []))[:6],
            "data_extraction_focus": self._safe_list(payload.get("medical_data_requirements", []))[:6],
            "historical_experience": self._summarize_historical_experience(payload.get("historical_experience", []), limit=4),
        }
