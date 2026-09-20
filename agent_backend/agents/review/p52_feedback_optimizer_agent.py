from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agent.agent_backend.llm.client import LLMClient
from agent.agent_backend.llm.errors import LLMExecutionError
from agent.agent_backend.llm.review_output_validation import require_object, text
from agent.agent_backend.prompts.prompt_envelope_builder import PromptEnvelopeBuilder
from agent.agent_backend.prompts.template_manager import PromptTemplateManager


class P52FeedbackOptimizerAgent:
    """3.2.P.5.2 专用反馈优化智能体。"""

    PATCH_TYPES = {
        "profile_patch",
        "entity_patch",
        "quality_standard_patch",
        "rule_patch",
        "retrieval_patch",
        "reviewer_patch",
        "merge_patch",
        "frontend_patch",
    }
    RULE_FILE = Path(__file__).resolve().parents[2] / "data" / "rule" / "p52_method_review" / "rules.json"
    SUPPORTED_RULE_CHANGE_ACTIONS = {"add", "update", "delete"}
    SUPPORTED_RULE_CHANGE_SCOPES = {"general_rules", "method_profile_rules"}
    SUPPORTED_RULE_CHECKS = {"missing_field", "missing_any", "missing_all", "related_substances_rrf", "assay_correction_logic"}
    RULE_PAYLOAD_KEY_ALIASES = {
        "name": "rule_name",
        "title": "rule_name",
        "condition": "check",
        "rule_condition": "check",
        "field": "fields",
        "problem": "problem_item",
        "issue": "problem_item",
        "conclusion": "problem_item",
        "reason": "reasoning",
        "suggestion": "revision_suggestion",
        "recommendation": "revision_suggestion",
    }
    ALLOWED_RULE_PAYLOAD_KEYS = {
        "rule_name",
        "severity",
        "check",
        "fields",
        "problem_item",
        "reasoning",
        "revision_suggestion",
        "evidence_keywords",
    }

    def __init__(self) -> None:
        self.llm = LLMClient()
        self.envelopes = PromptEnvelopeBuilder()
        self.prompts = PromptTemplateManager("pre_review_agent_prompt")
        self.rule_index = self._load_rule_index()

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
    def _safe_dict(value: Any) -> Dict[str, Any]:
        return dict(value) if isinstance(value, dict) else {}

    @staticmethod
    def _split_text_tokens(value: Any) -> List[str]:
        text = str(value or "").strip()
        if not text:
            return []
        normalized = text.replace("，", ",").replace("；", ",").replace(";", ",").replace("|", ",")
        return [item.strip() for item in normalized.split(",") if item.strip()]

    @staticmethod
    def _compact_text(value: Any, max_len: int = 240) -> str:
        text = " ".join(str(value or "").strip().split())
        if len(text) <= max_len:
            return text
        return f"{text[:max_len].rstrip()}..."

    def _build_llm_execution_meta(self, *, raw_text: Any, parsed_payload: Any) -> Dict[str, Any]:
        used_default_fallback = not isinstance(parsed_payload, dict)
        failure_reason = ""
        if used_default_fallback:
            failure_reason = "empty_response" if not str(raw_text or "").strip() else "json_parse_failed_or_non_object"
        return {
            "agent": "p52_feedback_optimizer",
            "used_default_fallback": used_default_fallback,
            "used_model_output": not used_default_fallback,
            "json_parse_ok": isinstance(parsed_payload, dict),
            "failure_reason": failure_reason,
            "raw_preview": "",
            "output_chars": len(str(raw_text or "")),
        }

    def _load_rule_index(self) -> Dict[str, Dict[str, str]]:
        out: Dict[str, Dict[str, str]] = {}
        if not self.RULE_FILE.exists():
            return out
        try:
            payload = json.loads(self.RULE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return out
        if not isinstance(payload, dict):
            return out
        for item in payload.get("general_rules", []) if isinstance(payload.get("general_rules", []), list) else []:
            if not isinstance(item, dict):
                continue
            rule_id = str(item.get("rule_id", "") or "").strip()
            if not rule_id:
                continue
            out[rule_id] = {"scope": "general_rules", "method_profile": ""}
        profiles = payload.get("method_profiles", {}) if isinstance(payload.get("method_profiles", {}), dict) else {}
        for profile_key, profile in profiles.items():
            rules = profile.get("rules", []) if isinstance(profile, dict) and isinstance(profile.get("rules", []), list) else []
            for item in rules:
                if not isinstance(item, dict):
                    continue
                rule_id = str(item.get("rule_id", "") or "").strip()
                if not rule_id:
                    continue
                out[rule_id] = {"scope": "method_profile_rules", "method_profile": str(profile_key or "").strip()}
        return out

    def _infer_rule_scope(self, rule_id: str) -> Tuple[str, str]:
        data = self.rule_index.get(str(rule_id or "").strip(), {})
        if not isinstance(data, dict):
            return "", ""
        return str(data.get("scope", "") or "").strip(), str(data.get("method_profile", "") or "").strip()

    @classmethod
    def _normalize_rule_payload(cls, payload: Any) -> Dict[str, Any]:
        raw = dict(payload) if isinstance(payload, dict) else {}
        normalized: Dict[str, Any] = {}
        for key, value in raw.items():
            source_key = str(key or "").strip()
            if not source_key:
                continue
            target_key = cls.RULE_PAYLOAD_KEY_ALIASES.get(source_key, source_key)
            if target_key not in cls.ALLOWED_RULE_PAYLOAD_KEYS:
                continue
            normalized[target_key] = value
        if "severity" in normalized:
            normalized["severity"] = str(normalized.get("severity", "") or "").strip().lower()
            if not normalized["severity"]:
                normalized.pop("severity", None)
        if "check" in normalized:
            check = str(normalized.get("check", "") or "").strip()
            if check in cls.SUPPORTED_RULE_CHECKS:
                normalized["check"] = check
            else:
                normalized.pop("check", None)
        if "fields" in normalized:
            fields_value = normalized.get("fields", [])
            if isinstance(fields_value, list):
                fields = [str(item or "").strip() for item in fields_value if str(item or "").strip()]
            else:
                fields = cls._split_text_tokens(fields_value)
            normalized["fields"] = fields
            if not fields:
                normalized.pop("fields", None)
        if "evidence_keywords" in normalized:
            keyword_value = normalized.get("evidence_keywords", [])
            if isinstance(keyword_value, list):
                keywords = [str(item or "").strip() for item in keyword_value if str(item or "").strip()]
            else:
                keywords = cls._split_text_tokens(keyword_value)
            normalized["evidence_keywords"] = keywords
            if not keywords:
                normalized.pop("evidence_keywords", None)
        for text_key in ["rule_name", "problem_item", "reasoning", "revision_suggestion"]:
            if text_key in normalized:
                text_value = str(normalized.get(text_key, "") or "").strip()
                if text_value:
                    normalized[text_key] = text_value
                else:
                    normalized.pop(text_key, None)
        return normalized

    def _build_prompt_payload(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        classification = self._safe_dict(payload.get("classification"))
        trace_snapshot = self._safe_dict(payload.get("trace_snapshot"))
        localizations = payload.get("localizations", []) if isinstance(payload.get("localizations", []), list) else []
        feedback_record = self._safe_dict(payload.get("feedback_record"))
        review_result = self._safe_dict(trace_snapshot.get("review_result"))
        method_context = self._safe_dict(trace_snapshot.get("method_context"))
        retrieval_artifacts = self._safe_dict(trace_snapshot.get("retrieval_artifacts"))
        entity_extraction = self._safe_dict(trace_snapshot.get("entity_extraction"))
        method_judgment = self._safe_dict(trace_snapshot.get("method_judgment"))
        quality_standard_context = self._safe_dict(method_context.get("quality_standard_context"))
        candidate_findings = trace_snapshot.get("candidate_findings", []) if isinstance(trace_snapshot.get("candidate_findings", []), list) else []
        return {
            "run_id": str(payload.get("run_id", "") or "").strip(),
            "section_id": str(payload.get("section_id", "") or "").strip(),
            "section_name": str(payload.get("section_name", "") or "").strip(),
            "feedback_text": self._compact_text(feedback_record.get("feedback_text", ""), max_len=400),
            "suggestion": self._compact_text(feedback_record.get("suggestion", ""), max_len=240),
            "classification": {
                "primary_error_type": str(classification.get("primary_error_type", "") or "").strip(),
                "error_types": self._safe_list(classification.get("error_types", []))[:8],
                "reasons": self._safe_list(classification.get("reasons", []))[:8],
                "confidence": str(classification.get("confidence", "") or "").strip(),
                "method_profiles": self._safe_list(classification.get("method_profiles", []))[:6],
                "candidate_rule_ids": self._safe_list(classification.get("candidate_rule_ids", []))[:8],
            },
            "target_localization": [
                {
                    "error_type": str(item.get("error_type", "") or "").strip(),
                    "patch_type": str(item.get("patch_type", "") or "").strip(),
                    "target_agent": str(item.get("target_agent", "") or "").strip(),
                    "target_file": str(item.get("target_file", "") or "").strip(),
                }
                for item in localizations
                if isinstance(item, dict)
            ],
            "trace_snapshot": {
                "method_profiles": self._safe_list(method_context.get("method_profiles", []))[:6],
                "method_type": str(method_context.get("method_type", "") or "").strip(),
                "method_purpose": str(method_context.get("method_purpose", "") or "").strip(),
                "review_conclusion": str(review_result.get("pre_review_conclusion", "") or "").strip(),
                "section_summary": self._compact_text(review_result.get("section_summary", ""), max_len=360),
                "entity_keys": [
                    str(key).strip()
                    for key, value in entity_extraction.get("entities", {}).items()
                    if isinstance(entity_extraction.get("entities", {}), dict) and isinstance(value, dict) and str(key).strip()
                ][:12],
                "method_judgment_profiles": self._safe_list(method_judgment.get("method_profiles", []))[:6],
                "quality_standard_matches": self._safe_list(quality_standard_context.get("current_section_matches", []))[:6],
                "candidate_findings": [
                    {
                        "rule_id": str(item.get("rule_id", "") or "").strip(),
                        "rule_name": str(item.get("rule_name", "") or "").strip(),
                        "fields": self._safe_list(item.get("fields", []))[:6],
                    }
                    for item in candidate_findings[:8]
                    if isinstance(item, dict)
                ],
                "retrieved_material_titles": [
                    self._compact_text(item.get("title", ""), max_len=100)
                    for item in retrieval_artifacts.get("retrieved_materials", [])[:8]
                    if isinstance(retrieval_artifacts.get("retrieved_materials", []), list) and isinstance(item, dict)
                ],
            },
            "current_section_matches": self._safe_list(quality_standard_context.get("current_section_matches", []))[:8],
            "current_section_entities": entity_extraction.get("structured_sections", {}) if isinstance(entity_extraction.get("structured_sections", {}), dict) else {},
        }

    def _fallback_overlay(self, *, patch_type: str, target_agent: str, classification: Dict[str, Any]) -> Dict[str, Any]:
        primary_error_type = str(classification.get("primary_error_type", "") or "").strip()
        method_profiles = self._safe_list(classification.get("method_profiles", []))
        candidate_rule_ids = self._safe_list(classification.get("candidate_rule_ids", []))
        overlay: Dict[str, Any] = {
            "prompt_suffixes": {},
            "force_method_profiles": [],
            "exclude_method_profiles": [],
            "include_rule_ids": [],
            "exclude_rule_ids": [],
            "rule_keyword_hints": {},
            "retrieval_keyword_hints": [],
            "retrieval_section_patterns": [],
            "entity_hints": {},
        }
        if patch_type == "profile_patch":
            overlay["force_method_profiles"] = method_profiles[:3]
            overlay["prompt_suffixes"]["method_judgment"] = (
                f"当前反馈归因为 {primary_error_type}。请优先校正方法域识别，"
                f"避免把当前章节误判为无关方法域；若原文出现高效液相色谱、流动相、色谱柱等术语，应优先识别为色谱相关方法。"
            )
        elif patch_type == "entity_patch":
            overlay["entity_hints"] = {"focus_fields": ["mobile_phase", "column_name", "decision_rule", "linked_spec_item"]}
            overlay["prompt_suffixes"]["entity_extraction"] = (
                "本轮反馈提示实体抽取存在漏抽或误抽。请优先抽取真正用于审评判断的关键实体，"
                "避免把无关短语或上下文噪声当成实体。"
            )
        elif patch_type == "quality_standard_patch":
            overlay["prompt_suffixes"]["quality_standard_extractor"] = (
                "请优先根据 3.2.P.5.1 质量标准中的检验项目名称与当前章节主题做一一映射，"
                "不要只按当前 method profile 机械猜测。"
            )
        elif patch_type == "rule_patch":
            overlay["exclude_rule_ids"] = candidate_rule_ids[:4]
            overlay["prompt_suffixes"]["rule_filter"] = (
                "请收紧规则适用边界。只有章节事实与规则要求直接对应时才保留该规则候选。"
            )
        elif patch_type == "retrieval_patch":
            overlay["retrieval_section_patterns"] = ["3.2.p.5.1", "3.2.p.5.3.*", "3.2.p.5.6"]
            overlay["prompt_suffixes"]["retrieval"] = "检索时优先围绕同项目质量标准、方法学验证和相关控制项目补证，避免跨方法域泛化检索。"
        elif patch_type == "reviewer_patch":
            overlay["prompt_suffixes"]["reviewer"] = (
                "输出判断时必须直接引用章节事实与补证，不要写“已见部分…仍缺少直接支撑”这类空泛模板句。"
            )
        elif patch_type == "merge_patch":
            overlay["prompt_suffixes"]["result_merge"] = "结果聚合时以模型结构化输出为主，不要把 fallback 判断项混入已返回的实质结果。"
        return overlay

    def _fallback_result(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        localizations = payload.get("localizations", []) if isinstance(payload.get("localizations", []), list) else []
        classification = self._safe_dict(payload.get("classification"))
        section_id = str(payload.get("section_id", "") or "").strip()
        patches: List[Dict[str, Any]] = []
        for index, item in enumerate(localizations[:4], start=1):
            if not isinstance(item, dict):
                continue
            patch_type = str(item.get("patch_type", "") or "rule_patch").strip()
            target_agent = str(item.get("target_agent", "") or "p52_feedback_optimize").strip()
            patches.append(
                {
                    "patch_id": f"p52_patch_{uuid.uuid4().hex[:12]}",
                    "patch_type": patch_type,
                    "target_agent": target_agent,
                    "target_scope": section_id,
                    "trigger_condition": f"section_id startswith '3.2.p.5.2' and error_type == '{str(item.get('error_type', '') or '').strip()}'",
                    "patch_content": f"针对 {str(item.get('error_type', '') or '').strip()} 收紧 {target_agent} 在当前章节的判断逻辑。",
                    "status": "candidate",
                    "target_file": str(item.get("target_file", "") or "").strip(),
                    "target_key": str(item.get("error_type", "") or "").strip(),
                    "method_profiles": self._safe_list(classification.get("method_profiles", [])),
                    "candidate_rule_ids": self._safe_list(classification.get("candidate_rule_ids", [])),
                    "verification_focus": ["当前问题是否消失", "是否引入新的误报或漏报"],
                    "rule_change": {},
                    "overlay": self._fallback_overlay(
                        patch_type=patch_type,
                        target_agent=target_agent,
                        classification=classification,
                    ),
                }
            )
        return {
            "section_id": section_id,
            "primary_error_type": str(classification.get("primary_error_type", "") or "").strip(),
            "summary": "根据当前反馈与 trace 生成了最小运行时 overlay patch 候选。",
            "patches": patches,
        }

    def propose(self, payload: Dict[str, Any], prompt_config: Dict[str, Any] | None = None) -> Dict[str, Any]:
        prompt_payload = self._build_prompt_payload(payload)
        prompt = self.prompts.render("p52_feedback_optimizer.j2", prompt_payload, prompt_config=prompt_config or {})
        default_data = self._fallback_result(payload)
        raw = self.llm.chat(
            messages=self.envelopes.build("p52_feedback_optimizer", prompt),
            default=json.dumps(default_data, ensure_ascii=False),
        )
        parsed = self.llm.extract_json(raw)
        require_object(parsed, 'p52_feedback_optimizer')
        if (not text(parsed.get('summary')) or not isinstance(parsed.get('patches'), list)
                or any(not isinstance(item, dict) or not text(item.get('patch_type')) or item['patch_type'] not in self.PATCH_TYPES
                       or not text(item.get('target_agent')) or not text(item.get('patch_content'))
                       or not isinstance(item.get('overlay'), dict)
                       or item.get('status', 'candidate') != 'candidate'
                       for item in parsed['patches'])):
            raise LLMExecutionError('model_invalid_output', stage='p52_feedback_optimizer')
        llm_execution = self._build_llm_execution_meta(raw_text=raw, parsed_payload=parsed)
        data = dict(parsed)
        classification = self._safe_dict(payload.get("classification"))
        localizations = payload.get("localizations", []) if isinstance(payload.get("localizations", []), list) else []
        target_map = {
            str(item.get("patch_type", "") or "").strip(): item
            for item in localizations
            if isinstance(item, dict) and str(item.get("patch_type", "") or "").strip()
        }
        patches: List[Dict[str, Any]] = []
        for index, item in enumerate(data.get("patches", []) if isinstance(data.get("patches", []), list) else [], start=1):
            if not isinstance(item, dict):
                continue
            patch_type = str(item.get("patch_type", "") or "").strip()
            if patch_type not in self.PATCH_TYPES:
                continue
            target_meta = self._safe_dict(target_map.get(patch_type))
            target_agent = str(item.get("target_agent", "") or target_meta.get("target_agent", "") or "").strip()
            if not target_agent:
                continue
            overlay = self._safe_dict(item.get("overlay"))
            patches.append(
                {
                    "patch_id": str(item.get("patch_id", "") or f"p52_patch_{uuid.uuid4().hex[:12]}"),
                    "patch_type": patch_type,
                    "target_agent": target_agent,
                    "target_scope": str(item.get("target_scope", "") or payload.get("section_id", "") or "").strip(),
                    "trigger_condition": str(item.get("trigger_condition", "") or f"section_id startswith '3.2.p.5.2'").strip(),
                    "patch_content": str(item.get("patch_content", "") or "").strip() or f"针对 {patch_type} 的运行时 overlay patch。",
                    "status": str(item.get("status", "") or "candidate").strip() or "candidate",
                    "target_file": str(item.get("target_file", "") or target_meta.get("target_file", "") or "").strip(),
                    "target_key": str(item.get("target_key", "") or target_meta.get("error_type", "") or patch_type).strip(),
                    "method_profiles": self._safe_list(item.get("method_profiles", [])) or self._safe_list(classification.get("method_profiles", [])),
                    "candidate_rule_ids": self._safe_list(item.get("candidate_rule_ids", [])) or self._safe_list(classification.get("candidate_rule_ids", [])),
                    "verification_focus": self._safe_list(item.get("verification_focus", []))[:6],
                    "rule_change": self._normalize_rule_change(item.get("rule_change", {})),
                    "overlay": overlay,
                }
            )
            if len(patches) >= 6:
                break
        data["section_id"] = str(data.get("section_id", "") or payload.get("section_id", "") or "").strip()
        data["primary_error_type"] = str(data.get("primary_error_type", "") or classification.get("primary_error_type", "") or "").strip()
        data["summary"] = str(data.get("summary", "") or default_data.get("summary", "") or "").strip()
        data["patches"] = patches
        data["llm_execution"] = llm_execution
        return data

    def _normalize_rule_change(self, value: Any) -> Dict[str, Any]:
        payload = dict(value) if isinstance(value, dict) else {}
        action = str(payload.get("action", "") or "").strip().lower()
        scope = str(payload.get("scope", "") or "").strip()
        rule_id = str(payload.get("rule_id", "") or "").strip()
        method_profile = str(payload.get("method_profile", "") or "").strip()
        inferred_scope, inferred_profile = self._infer_rule_scope(rule_id)
        if inferred_scope:
            scope = inferred_scope
        if inferred_profile:
            method_profile = inferred_profile
        rule_payload = self._normalize_rule_payload(payload.get("rule_payload", {}))
        if action not in self.SUPPORTED_RULE_CHANGE_ACTIONS or scope not in self.SUPPORTED_RULE_CHANGE_SCOPES or not rule_id:
            return {}
        if scope == "method_profile_rules" and not method_profile:
            return {}
        if action in {"add", "update"} and not rule_payload:
            return {}
        return {
            "action": action,
            "scope": scope,
            "method_profile": method_profile,
            "rule_id": rule_id,
            "rule_payload": rule_payload,
        }
