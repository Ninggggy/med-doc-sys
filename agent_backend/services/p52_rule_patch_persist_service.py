from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Tuple


class P52RulePatchPersistService:
    """管理 P52 规则补丁的候选落盘与正式合并。"""

    RULES_FILE = Path(__file__).resolve().parents[1] / "data" / "rule" / "p52_method_review" / "rules.json"
    CANDIDATE_OVERLAY_FILE = Path(__file__).resolve().parents[1] / "data" / "rule" / "p52_method_review" / "candidate_rules_overlay.json"
    SUPPORTED_RULE_CHANGE_ACTIONS = {"add", "update", "delete"}
    SUPPORTED_RULE_CHANGE_SCOPES = {"general_rules", "method_profile_rules"}
    SCOPE_ALIASES = {
        "general_methods": "general_rules",
        "general_method_rules": "general_rules",
        "global_rules": "general_rules",
        "method_rules": "method_profile_rules",
        "profile_rules": "method_profile_rules",
    }
    SUPPORTED_RULE_CHECKS = {
        "missing_field",
        "missing_any",
        "missing_all",
        "missing_k_of_n",
        "related_substances_rrf",
        "assay_correction_logic",
        "uv_specificity_signal_missing",
        "single_uv_without_support",
        "standard_method_mismatch",
    }
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
        "min_present",
        "problem_item",
        "reasoning",
        "revision_suggestion",
        "evidence_keywords",
    }
    FIELD_TEXT_ALIAS = {
        "称样量": "sample_weight",
        "取样量": "sample_weight",
        "稀释步骤": "dilution_steps",
        "稀释": "dilution_steps",
        "定容体积": "final_volume",
        "定容": "final_volume",
        "最大吸收波长": "characteristic_max_wavelengths",
        "最小吸收波长": "characteristic_min_wavelengths",
        "吸光度比值": "absorbance_ratio",
        "专属性": "specificity_support",
        "测定介质": "solvent_or_medium",
        "溶剂": "solvent_or_medium",
        "仪器": "instrument_or_method_reference",
        "方法依据": "instrument_or_method_reference",
    }
    PROFILE_PREFIX_MAP = {
        "related_substances": "RS",
        "dissolution": "DIS",
        "microbial_limits": "MIC",
        "solubility": "SOL",
        "water_content": "WC",
        "assay_titration": "ASSAYT",
        "assay_hplc": "ASSAYH",
        "uv_identification": "UV",
    }

    @staticmethod
    def _safe_dict(value: Any) -> Dict[str, Any]:
        return dict(value) if isinstance(value, dict) else {}

    @staticmethod
    def _safe_list(value: Any) -> List[Any]:
        return list(value) if isinstance(value, list) else []

    @staticmethod
    def _split_text_tokens(value: Any) -> List[str]:
        text = str(value or "").strip()
        if not text:
            return []
        normalized = text.replace("，", ",").replace("；", ",").replace(";", ",").replace("|", ",")
        return [item.strip() for item in normalized.split(",") if item.strip()]

    def _load_json(self, path: Path, default_value: Dict[str, Any]) -> Dict[str, Any]:
        if not path.exists():
            return deepcopy(default_value)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return deepcopy(default_value)
        return data if isinstance(data, dict) else deepcopy(default_value)

    @staticmethod
    def _write_json(path: Path, payload: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _extract_patch_payload(row: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(row.get("payload", {})) if isinstance(row.get("payload", {}), dict) else {}
        nested_payload = dict(payload.get("payload", {})) if isinstance(payload.get("payload", {}), dict) else {}
        return nested_payload or payload

    def stage_approved_rule_patch(
        self,
        *,
        run_id: str,
        section_id: str,
        patch_row: Dict[str, Any],
    ) -> Dict[str, Any]:
        overlay_doc = self._load_json(
            self.CANDIDATE_OVERLAY_FILE,
            {"version": "1.0.0", "domain": "ctd_p52_method_review_rule_overlay", "items": []},
        )
        items = overlay_doc.get("items", []) if isinstance(overlay_doc.get("items", []), list) else []
        patch_payload = self._extract_patch_payload(patch_row)
        patch_id = str(patch_row.get("patch_id", "") or "").strip()
        staged_item = {
            "patch_id": patch_id,
            "run_id": str(run_id or "").strip(),
            "section_id": str(section_id or "").strip(),
            "status": "approved_pending_verify",
            "patch_type": str(patch_row.get("patch_type", "") or "").strip(),
            "target_agent": str(patch_row.get("target_agent", "") or "").strip(),
            "target_scope": str(patch_row.get("target_scope", "") or "").strip(),
            "patch_content": str(patch_row.get("patch_content", "") or "").strip(),
            "target_key": str(patch_payload.get("target_key", "") or "").strip(),
            "method_profiles": patch_payload.get("method_profiles", []) if isinstance(patch_payload.get("method_profiles", []), list) else [],
            "candidate_rule_ids": patch_payload.get("candidate_rule_ids", []) if isinstance(patch_payload.get("candidate_rule_ids", []), list) else [],
            "rule_change": patch_payload.get("rule_change", {}) if isinstance(patch_payload.get("rule_change", {}), dict) else {},
            "overlay": patch_payload.get("overlay", {}) if isinstance(patch_payload.get("overlay", {}), dict) else {},
        }
        replaced = False
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            if str(item.get("patch_id", "") or "").strip() != patch_id:
                continue
            items[index] = staged_item
            replaced = True
            break
        if not replaced:
            items.append(staged_item)
        overlay_doc["items"] = items
        self._write_json(self.CANDIDATE_OVERLAY_FILE, overlay_doc)
        return staged_item

    def merge_verified_rule_patches(
        self,
        *,
        run_id: str,
        section_id: str,
        patch_rows: List[Dict[str, Any]],
        verification_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        overall_verdict = str(verification_result.get("overall_verdict", "") or "").strip().lower()
        if overall_verdict != "improved":
            return {
                "merged": False,
                "reason": f"verification verdict is {overall_verdict or 'unknown'}",
                "merged_patch_ids": [],
                "skipped_patch_ids": [str(item.get("patch_id", "") or "").strip() for item in patch_rows if isinstance(item, dict)],
            }
        rules_doc = self._load_json(self.RULES_FILE, {"general_rules": [], "method_profiles": {}})
        overlay_doc = self._load_json(
            self.CANDIDATE_OVERLAY_FILE,
            {"version": "1.0.0", "domain": "ctd_p52_method_review_rule_overlay", "items": []},
        )
        merged_patch_ids: List[str] = []
        skipped_patch_ids: List[str] = []
        for row in patch_rows:
            if not isinstance(row, dict):
                continue
            if str(row.get("patch_type", "") or "").strip() != "rule_patch":
                skipped_patch_ids.append(str(row.get("patch_id", "") or "").strip())
                continue
            if str(row.get("status", "") or "").strip().lower() != "approved":
                skipped_patch_ids.append(str(row.get("patch_id", "") or "").strip())
                continue
            patch_payload = self._extract_patch_payload(row)
            rule_change = self._normalize_rule_change(
                rules_doc=rules_doc,
                value=patch_payload.get("rule_change", {}),
            )
            if not rule_change:
                rule_change = self._synthesize_rule_change_from_patch(
                    rules_doc=rules_doc,
                    patch_row=row,
                    patch_payload=patch_payload,
                )
            rule_change = self._reroute_rule_change_before_apply(
                rules_doc=rules_doc,
                rule_change=rule_change,
                patch_row=row,
                patch_payload=patch_payload,
            )
            applied = self._apply_rule_change(rules_doc, rule_change)
            patch_id = str(row.get("patch_id", "") or "").strip()
            if applied:
                merged_patch_ids.append(patch_id)
                self._mark_overlay_item_status(
                    overlay_doc=overlay_doc,
                    patch_id=patch_id,
                    status="merged",
                    verification_summary=self._build_verification_summary(verification_result),
                )
            else:
                skipped_patch_ids.append(patch_id)
        if merged_patch_ids:
            self._write_json(self.RULES_FILE, rules_doc)
            self._write_json(self.CANDIDATE_OVERLAY_FILE, overlay_doc)
        return {
            "merged": bool(merged_patch_ids),
            "reason": "" if merged_patch_ids else "no mergeable approved rule_patch with concrete rule_change",
            "merged_patch_ids": merged_patch_ids,
            "skipped_patch_ids": skipped_patch_ids,
        }

    @classmethod
    def _map_text_to_field(cls, text: str) -> str:
        value = str(text or "").strip()
        if not value:
            return ""
        for key, mapped in cls.FIELD_TEXT_ALIAS.items():
            if key in value:
                return mapped
        return ""

    @classmethod
    def _extract_fields_from_text(cls, text: str) -> List[str]:
        fields: List[str] = []
        for key, mapped in cls.FIELD_TEXT_ALIAS.items():
            if key in str(text or ""):
                fields.append(mapped)
        out: List[str] = []
        seen = set()
        for item in fields:
            if not item or item in seen:
                continue
            seen.add(item)
            out.append(item)
        return out

    @classmethod
    def _normalize_scope(cls, value: Any) -> str:
        scope = str(value or "").strip().lower()
        if not scope:
            return ""
        return cls.SCOPE_ALIASES.get(scope, scope)

    @staticmethod
    def _normalize_rule_id(value: Any) -> str:
        raw = str(value or "").strip()
        if not raw:
            return ""
        # 中文注释：优先提取标准规则编号（如 UV-001），避免把“UV-001 xxx标题”整体当作 rule_id
        std_match = re.search(r"\b([A-Za-z]+-\d{2,})\b", raw)
        if std_match:
            return std_match.group(1).upper()
        if "." in raw:
            raw = raw.split(".")[-1].strip()
        raw = raw.replace(" ", "_")
        raw = re.sub(r"[^A-Za-z0-9_-]", "_", raw)
        raw = re.sub(r"_+", "_", raw).strip("_-")
        return raw.upper()

    @classmethod
    def _normalize_rule_text_list(cls, value: Any) -> List[str]:
        if not isinstance(value, list):
            return []
        out: List[str] = []
        seen = set()
        for item in value:
            rid = cls._normalize_rule_id(item)
            if not rid or rid in seen:
                continue
            seen.add(rid)
            out.append(rid)
        return out

    @staticmethod
    def _sort_rules_in_place(rules: List[Dict[str, Any]]) -> None:
        def _key(item: Dict[str, Any]) -> tuple:
            rid = str((item or {}).get("rule_id", "") or "").strip().upper()
            return (rid, )
        rules.sort(key=_key)

    @staticmethod
    def _next_sequence_for_prefix(existing_rule_ids: List[str], prefix: str) -> int:
        pat = re.compile(rf"^{re.escape(prefix)}-(\d+)$", flags=re.IGNORECASE)
        max_no = 0
        for rid in existing_rule_ids:
            m = pat.match(str(rid or "").strip())
            if not m:
                continue
            try:
                max_no = max(max_no, int(m.group(1)))
            except Exception:
                continue
        return max_no + 1

    @staticmethod
    def _next_rule_id_from_tail(target_rules: List[Dict[str, Any]], fallback_prefix: str) -> str:
        """中文注释：按规则列表最后一个 rule_id 自增，满足“取最后一条+1”的编号诉求。"""
        if isinstance(target_rules, list) and target_rules:
            last = target_rules[-1] if isinstance(target_rules[-1], dict) else {}
            last_rule_id = str((last or {}).get("rule_id", "") or "").strip().upper()
            match = re.match(r"^([A-Z]+)-(\d+)$", last_rule_id)
            if match:
                prefix = match.group(1)
                seq = int(match.group(2)) + 1
                width = max(3, len(match.group(2)))
                return f"{prefix}-{seq:0{width}d}"
        prefix = str(fallback_prefix or "RULE").strip().upper()
        return f"{prefix}-001"

    def _build_auto_rule_id(
        self,
        *,
        rules_doc: Dict[str, Any],
        scope: str,
        method_profile: str,
        preferred_prefix: str,
    ) -> str:
        prefix = str(preferred_prefix or "").strip().upper()
        if not prefix:
            if scope == "method_profile_rules":
                prefix = self.PROFILE_PREFIX_MAP.get(str(method_profile or "").strip(), "RULE")
            else:
                prefix = "RULE"
        existing_ids: List[str] = []
        if scope == "general_rules":
            general_rules = rules_doc.get("general_rules", []) if isinstance(rules_doc.get("general_rules", []), list) else []
            tail_rule_id = self._next_rule_id_from_tail(general_rules, prefix)
            if tail_rule_id:
                return tail_rule_id
            existing_ids = [
                str(item.get("rule_id", "") or "").strip().upper()
                for item in (rules_doc.get("general_rules", []) if isinstance(rules_doc.get("general_rules", []), list) else [])
                if isinstance(item, dict)
            ]
        else:
            profiles = rules_doc.get("method_profiles", {}) if isinstance(rules_doc.get("method_profiles", {}), dict) else {}
            profile_doc = profiles.get(method_profile, {}) if isinstance(profiles.get(method_profile, {}), dict) else {}
            profile_rules = profile_doc.get("rules", []) if isinstance(profile_doc.get("rules", []), list) else []
            tail_rule_id = self._next_rule_id_from_tail(profile_rules, prefix)
            if tail_rule_id:
                return tail_rule_id
            existing_ids = [
                str(item.get("rule_id", "") or "").strip().upper()
                for item in (profile_doc.get("rules", []) if isinstance(profile_doc.get("rules", []), list) else [])
                if isinstance(item, dict)
            ]
        seq = self._next_sequence_for_prefix(existing_ids, prefix)
        return f"{prefix}-{seq:03d}"

    def _synthesize_rule_change_from_patch(
        self,
        *,
        rules_doc: Dict[str, Any],
        patch_row: Dict[str, Any],
        patch_payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        patch_content = str(patch_row.get("patch_content", "") or patch_payload.get("reason", "") or "").strip()
        feedback_text = str(patch_payload.get("source_feedback_text", "") or "").strip()
        routing_text = "\n".join([patch_content, feedback_text]).strip()
        target_key = str(patch_payload.get("target_key", "") or "").strip()
        method_profiles = [str(item).strip() for item in patch_payload.get("method_profiles", []) if str(item).strip()] if isinstance(patch_payload.get("method_profiles", []), list) else []
        candidate_rule_ids = self._normalize_rule_text_list(patch_payload.get("candidate_rule_ids", []))
        section_rules = self._normalize_rule_text_list(patch_payload.get("section_rules", []))
        if not section_rules:
            section_rules = self._normalize_rule_text_list(patch_row.get("section_rules", []))
        if not candidate_rule_ids:
            candidate_rule_ids = self._normalize_rule_text_list(patch_row.get("candidate_rule_ids", []))
        if section_rules:
            candidate_rule_ids = list(dict.fromkeys(candidate_rule_ids + section_rules))
        rule_id = target_key or (candidate_rule_ids[0] if candidate_rule_ids else "")
        if not rule_id:
            rule_id = f"RULE-{str(patch_row.get('patch_id', '') or '').replace('patch_', '').upper()[:24]}".strip("-")
        rule_id = self._normalize_rule_id(rule_id)

        fields = self._extract_fields_from_text(patch_content)
        if not fields:
            fields = self._extract_fields_from_text(
                str(
                    (patch_payload.get("source_feedback_text", "") or "")
                )
            )
        # 中文注释：当反馈是“样品制备不完整”这类描述型语句时，允许使用通用字段组兜底，避免 rule_change 合成失败
        prep_hint_text = f"{patch_content}\n{feedback_text}\n{rule_id}".strip().lower()
        prep_semantic_hit = (
            ("样品制备" in prep_hint_text)
            or ("配制" in prep_hint_text)
            or ("制备" in prep_hint_text and "不完整" in prep_hint_text)
            or ("prep" in prep_hint_text and "incomplete" in prep_hint_text)
        )
        if not fields and prep_semantic_hit:
            fields = ["sample_weight", "dilution_steps", "final_volume"]
        if not fields:
            return {}

        check = "missing_any"
        if "missing_k_of_n" in patch_content.lower():
            check = "missing_k_of_n"
        elif "missing_all" in patch_content.lower():
            check = "missing_all"
        elif "missing_field" in patch_content.lower() and len(fields) == 1:
            check = "missing_field"

        scope = self._normalize_scope(
            patch_payload.get("target_scope", "")
            or patch_row.get("target_scope", "")
        ) or "general_rules"
        method_profile = ""
        semantic_profile = self._infer_profile_from_semantic_hints(
            rules_doc=rules_doc,
            text="\n".join([routing_text, target_key, rule_id]).strip(),
        )
        anchored_profile = self._infer_profile_from_rule_ids(rules_doc=rules_doc, rule_ids=candidate_rule_ids)
        inferred_profiles = self._infer_method_profiles_from_patch_text(
            text=routing_text,
            fallback_profiles=method_profiles,
            rules_doc=rules_doc,
            candidate_rule_ids=candidate_rule_ids,
            fields=fields,
        )
        if semantic_profile:
            inferred_profiles = [semantic_profile] + [item for item in inferred_profiles if item != semantic_profile]
        if anchored_profile:
            inferred_profiles = [anchored_profile] + [item for item in inferred_profiles if item != anchored_profile]
        # 中文注释：优先使用规则库元数据进行通用路由，避免依赖特定方法硬编码
        if inferred_profiles:
            inferred_profile = inferred_profiles[0]
            if anchored_profile and inferred_profile == anchored_profile:
                scope = "method_profile_rules"
                method_profile = inferred_profile
            else:
                profile_confident = self._is_profile_inference_confident(
                    rules_doc=rules_doc,
                    patch_content=routing_text,
                    inferred_profile=inferred_profile,
                    fallback_profiles=method_profiles,
                    candidate_rule_ids=candidate_rule_ids,
                    fields=fields,
                )
                if scope == "method_profile_rules" or profile_confident:
                    scope = "method_profile_rules"
                    method_profile = inferred_profile

        rule_payload: Dict[str, Any] = {
            "rule_name": str(patch_payload.get("target_key", "") or "反馈新增规则").strip() or "反馈新增规则",
            "severity": "major",
            "check": check,
            "fields": fields,
            "problem_item": "关键方法字段缺失",
            "reasoning": "反馈指出当前方法描述存在关键字段缺失，影响方法可执行性与可复核性。",
            "revision_suggestion": "请补充规则要求的关键方法字段，确保审评闭环完整。",
        }
        if check == "missing_k_of_n":
            rule_payload["min_present"] = max(1, min(len(fields), 2))
        if "warning" in patch_content.lower():
            rule_payload["severity"] = "minor"
        if "critical" in patch_content.lower():
            rule_payload["severity"] = "critical"
        if ("样品制备" in patch_content) or ("配制" in patch_content) or prep_semantic_hit:
            rule_payload["rule_name"] = "样品制备完整性校验"
            rule_payload["problem_item"] = "样品制备关键步骤不完整"
            rule_payload["reasoning"] = "称样量、稀释步骤或定容体积缺失会导致方法不可复现。"
            rule_payload["revision_suggestion"] = "请补充称样量、稀释步骤与定容体积，确保样品制备闭环。"
        preferred_prefix = ""
        if prep_semantic_hit or {"sample_weight", "dilution_steps", "final_volume"} & set(fields):
            preferred_prefix = "PREP"
        elif method_profile:
            preferred_prefix = self.PROFILE_PREFIX_MAP.get(method_profile, "")
        if not re.match(r"^[A-Z]+-\d{3,}$", rule_id):
            rule_id = self._build_auto_rule_id(
                rules_doc=rules_doc,
                scope=scope if scope in self.SUPPORTED_RULE_CHANGE_SCOPES else "general_rules",
                method_profile=method_profile,
                preferred_prefix=preferred_prefix,
            )

        normalized_payload = self._normalize_rule_payload(rule_payload)
        if not normalized_payload:
            return {}
        return {
            "action": "add",
            "scope": scope if scope in self.SUPPORTED_RULE_CHANGE_SCOPES else "general_rules",
            "method_profile": method_profile,
            "rule_id": rule_id,
            "rule_payload": normalized_payload,
        }

    def _is_profile_inference_confident(
        self,
        *,
        rules_doc: Dict[str, Any],
        patch_content: str,
        inferred_profile: str,
        fallback_profiles: List[str],
        candidate_rule_ids: List[str],
        fields: List[str],
    ) -> bool:
        if fallback_profiles:
            return True
        best_profile, score = self._select_best_profile_by_metadata(
            rules_doc=rules_doc,
            text=patch_content,
            candidate_rule_ids=candidate_rule_ids,
            fields=fields,
            preferred_profiles=[inferred_profile] if inferred_profile else [],
        )
        return bool(best_profile) and best_profile == inferred_profile and score >= 2.0

    def _infer_method_profiles_from_patch_text(
        self,
        *,
        text: str,
        fallback_profiles: List[str],
        rules_doc: Dict[str, Any],
        candidate_rule_ids: List[str],
        fields: List[str],
    ) -> List[str]:
        raw = str(text or "")
        if fallback_profiles:
            return [item for item in fallback_profiles if item]
        profile_hits: List[str] = []
        profiles = self._safe_dict(rules_doc).get("method_profiles", {})
        if not isinstance(profiles, dict):
            profiles = {}
        lowered = raw.lower()
        for profile_key, profile in profiles.items():
            if not isinstance(profile, dict):
                continue
            keywords = self._get_profile_keyword_tokens(profile_key, profile)
            if any(keyword and keyword in lowered for keyword in keywords):
                profile_hits.append(str(profile_key).strip())
        best_profile, _ = self._select_best_profile_by_metadata(
            rules_doc=rules_doc,
            text=raw,
            candidate_rule_ids=candidate_rule_ids,
            fields=fields,
            preferred_profiles=profile_hits,
        )
        merged = list(dict.fromkeys([*profile_hits, best_profile]))
        return [item for item in merged if item]

    def _get_profile_keyword_tokens(self, profile_key: str, profile_doc: Dict[str, Any]) -> List[str]:
        tokens: List[str] = []
        for item in profile_doc.get("keywords", []) if isinstance(profile_doc.get("keywords", []), list) else []:
            text = str(item or "").strip().lower()
            if text:
                tokens.append(text)
        display_name = str(profile_doc.get("display_name", "") or "").strip().lower()
        if display_name:
            tokens.append(display_name)
        key = str(profile_key or "").strip().lower().replace("_", " ")
        if key:
            tokens.append(key)
        return list(dict.fromkeys(tokens))

    def _select_best_profile_by_metadata(
        self,
        *,
        rules_doc: Dict[str, Any],
        text: str,
        candidate_rule_ids: List[str],
        fields: List[str],
        preferred_profiles: List[str],
    ) -> Tuple[str, float]:
        profiles = self._safe_dict(rules_doc).get("method_profiles", {})
        if not isinstance(profiles, dict):
            return "", 0.0
        lowered = str(text or "").lower()
        field_set = {str(item or "").strip() for item in fields if str(item or "").strip()}
        rule_id_set = {str(item or "").strip().upper() for item in candidate_rule_ids if str(item or "").strip()}
        preferred_set = {str(item or "").strip() for item in preferred_profiles if str(item or "").strip()}
        best_profile = ""
        best_score = 0.0
        for profile_key, profile in profiles.items():
            if not isinstance(profile, dict):
                continue
            score = 0.0
            profile_name = str(profile_key or "").strip()
            if profile_name in preferred_set:
                score += 1.0
            keyword_tokens = self._get_profile_keyword_tokens(profile_name, profile)
            keyword_hit = sum(1 for token in keyword_tokens if token and token in lowered)
            if keyword_hit:
                score += min(3.0, float(keyword_hit) * 0.5)
            rules = profile.get("rules", []) if isinstance(profile.get("rules", []), list) else []
            profile_rule_ids = {
                str(item.get("rule_id", "") or "").strip().upper()
                for item in rules
                if isinstance(item, dict) and str(item.get("rule_id", "") or "").strip()
            }
            if profile_rule_ids and rule_id_set:
                overlap = profile_rule_ids & rule_id_set
                if overlap:
                    score += 2.0 + min(1.0, 0.3 * len(overlap))
            profile_fields = {
                str(field or "").strip()
                for item in rules
                if isinstance(item, dict)
                for field in (item.get("fields", []) if isinstance(item.get("fields", []), list) else [])
                if str(field or "").strip()
            }
            if profile_fields and field_set:
                field_overlap = profile_fields & field_set
                if field_overlap:
                    score += 1.5 + min(1.0, 0.2 * len(field_overlap))
            if score > best_score:
                best_profile = profile_name
                best_score = score
        return best_profile, best_score

    def _infer_profile_from_rule_ids(self, *, rules_doc: Dict[str, Any], rule_ids: List[str]) -> str:
        """中文注释：当候选规则编号已指向同一方法域时，直接作为路由锚点，避免文本噪声影响。"""
        if not rule_ids:
            return ""
        rule_index = self._build_rule_index(rules_doc)
        profile_hits: List[str] = []
        for raw in rule_ids:
            rid = self._normalize_rule_id(raw)
            if not rid:
                continue
            info = rule_index.get(rid, {})
            if not isinstance(info, dict):
                continue
            scope = str(info.get("scope", "") or "").strip()
            profile = str(info.get("method_profile", "") or "").strip()
            if scope == "method_profile_rules" and profile:
                profile_hits.append(profile)
        if not profile_hits:
            return ""
        uniq = list(dict.fromkeys(profile_hits))
        if len(uniq) == 1:
            return uniq[0]
        return ""

    def _infer_profile_from_semantic_hints(self, *, rules_doc: Dict[str, Any], text: str) -> str:
        """中文注释：基于语义提示词进行通用路由，解决无候选 rule_id 时的落域问题。"""
        raw = str(text or "").strip().lower()
        if not raw:
            return ""
        hint_tokens: List[str] = []
        if ("鉴别" in raw) or ("ident" in raw):
            hint_tokens.extend(["鉴别", "identification", "uv"])
        if ("紫外" in raw) or ("uv" in raw):
            hint_tokens.extend(["紫外", "uv"])
        if ("溶出" in raw) or ("dissolution" in raw):
            hint_tokens.extend(["溶出", "dissolution"])
        if ("有关物质" in raw) or ("杂质" in raw):
            hint_tokens.extend(["有关物质", "杂质", "related"])
        if ("微生物" in raw):
            hint_tokens.extend(["微生物", "菌"])
        if ("滴定" in raw):
            hint_tokens.extend(["滴定", "titration"])
        if ("液相" in raw) or ("hplc" in raw):
            hint_tokens.extend(["液相", "hplc"])
        if not hint_tokens:
            return ""
        profiles = self._safe_dict(rules_doc).get("method_profiles", {})
        if not isinstance(profiles, dict):
            return ""
        best_profile = ""
        best_score = 0
        for profile_key, profile_doc in profiles.items():
            if not isinstance(profile_doc, dict):
                continue
            tokens = self._get_profile_keyword_tokens(str(profile_key), profile_doc)
            score = 0
            for hint in hint_tokens:
                h = str(hint or "").strip().lower()
                if not h:
                    continue
                if any(h in token for token in tokens) or any(token in h for token in tokens):
                    score += 1
            if score > best_score:
                best_profile = str(profile_key or "").strip()
                best_score = score
        return best_profile if best_score > 0 else ""

    def _reroute_rule_change_before_apply(
        self,
        *,
        rules_doc: Dict[str, Any],
        rule_change: Dict[str, Any],
        patch_row: Dict[str, Any],
        patch_payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """中文注释：落库前统一重路由，避免 optimizer 提供的 general_rules 误入通用规则区。"""
        if not isinstance(rule_change, dict):
            return {}
        action = str(rule_change.get("action", "") or "").strip().lower()
        scope = str(rule_change.get("scope", "") or "").strip()
        method_profile = str(rule_change.get("method_profile", "") or "").strip()
        rule_id = self._normalize_rule_id(rule_change.get("rule_id", ""))
        if action not in {"add", "update"}:
            return rule_change
        if scope == "method_profile_rules" and method_profile:
            return rule_change
        # 中文注释：已有规则更新按原位置处理，不强制改路由
        rule_index = self._build_rule_index(rules_doc)
        if rule_id and rule_id in rule_index:
            return rule_change
        candidate_rule_ids = self._normalize_rule_text_list(patch_payload.get("candidate_rule_ids", []))
        section_rules = self._normalize_rule_text_list(patch_payload.get("section_rules", []))
        if not section_rules:
            section_rules = self._normalize_rule_text_list(patch_row.get("section_rules", []))
        all_rule_hints = list(dict.fromkeys(candidate_rule_ids + section_rules))
        anchored_profile = self._infer_profile_from_rule_ids(rules_doc=rules_doc, rule_ids=all_rule_hints)
        semantic_profile = self._infer_profile_from_semantic_hints(
            rules_doc=rules_doc,
            text="\n".join(
                [
                    str(patch_row.get("patch_content", "") or ""),
                    str(patch_payload.get("source_feedback_text", "") or ""),
                    str(patch_payload.get("target_key", "") or ""),
                    str(rule_id or ""),
                ]
            ).strip(),
        )
        selected_profile = anchored_profile or semantic_profile
        if not selected_profile:
            return rule_change
        patched = dict(rule_change)
        patched["scope"] = "method_profile_rules"
        patched["method_profile"] = selected_profile
        if action == "add" and not re.match(r"^[A-Z]+-\d{3,}$", str(rule_id or "").strip()):
            patched["rule_id"] = self._build_auto_rule_id(
                rules_doc=rules_doc,
                scope="method_profile_rules",
                method_profile=selected_profile,
                preferred_prefix=self.PROFILE_PREFIX_MAP.get(selected_profile, "RULE"),
            )
        return patched

    def apply_approved_rule_patch(
        self,
        *,
        run_id: str,
        section_id: str,
        patch_row: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not isinstance(patch_row, dict):
            return {"applied": False, "reason": "invalid patch_row"}
        if str(patch_row.get("patch_type", "") or "").strip() != "rule_patch":
            return {"applied": False, "reason": "patch_type is not rule_patch"}
        rules_doc = self._load_json(self.RULES_FILE, {"general_rules": [], "method_profiles": {}})
        patch_payload = self._extract_patch_payload(patch_row)
        rule_change = self._normalize_rule_change(
            rules_doc=rules_doc,
            value=patch_payload.get("rule_change", {}),
        )
        if not rule_change:
            rule_change = self._synthesize_rule_change_from_patch(
                rules_doc=rules_doc,
                patch_row=patch_row,
                patch_payload=patch_payload,
            )
        rule_change = self._reroute_rule_change_before_apply(
            rules_doc=rules_doc,
            rule_change=rule_change,
            patch_row=patch_row,
            patch_payload=patch_payload,
        )
        if not rule_change:
            return {"applied": False, "reason": "cannot synthesize rule_change"}
        if not self._apply_rule_change(rules_doc, rule_change):
            return {"applied": False, "reason": "apply rule_change failed", "rule_change": rule_change}
        self._write_json(self.RULES_FILE, rules_doc)
        return {
            "applied": True,
            "rule_id": str(rule_change.get("rule_id", "") or "").strip(),
            "scope": str(rule_change.get("scope", "") or "").strip(),
            "method_profile": str(rule_change.get("method_profile", "") or "").strip(),
            "rule_change": rule_change,
            "run_id": str(run_id or "").strip(),
            "section_id": str(section_id or "").strip(),
        }

    def _build_rule_index(self, rules_doc: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
        index: Dict[str, Dict[str, str]] = {}
        for item in rules_doc.get("general_rules", []) if isinstance(rules_doc.get("general_rules", []), list) else []:
            if not isinstance(item, dict):
                continue
            rule_id = str(item.get("rule_id", "") or "").strip()
            if not rule_id:
                continue
            for key in {rule_id, self._normalize_rule_id(rule_id)}:
                if not key:
                    continue
                index[key] = {"scope": "general_rules", "method_profile": ""}
        profiles = rules_doc.get("method_profiles", {}) if isinstance(rules_doc.get("method_profiles", {}), dict) else {}
        for profile_key, profile in profiles.items():
            if not isinstance(profile, dict):
                continue
            for item in profile.get("rules", []) if isinstance(profile.get("rules", []), list) else []:
                if not isinstance(item, dict):
                    continue
                rule_id = str(item.get("rule_id", "") or "").strip()
                if not rule_id:
                    continue
                for key in {rule_id, self._normalize_rule_id(rule_id)}:
                    if not key:
                        continue
                    index[key] = {
                        "scope": "method_profile_rules",
                        "method_profile": str(profile_key or "").strip(),
                    }
        return index

    @classmethod
    def _normalize_rule_payload(cls, value: Any) -> Dict[str, Any]:
        payload = dict(value) if isinstance(value, dict) else {}
        normalized: Dict[str, Any] = {}
        for key, item in payload.items():
            source_key = str(key or "").strip()
            if not source_key:
                continue
            target_key = cls.RULE_PAYLOAD_KEY_ALIASES.get(source_key, source_key)
            if target_key not in cls.ALLOWED_RULE_PAYLOAD_KEYS:
                continue
            normalized[target_key] = item
        if "severity" in normalized:
            severity = str(normalized.get("severity", "") or "").strip().lower()
            if severity:
                normalized["severity"] = severity
            else:
                normalized.pop("severity", None)
        if "check" in normalized:
            check = str(normalized.get("check", "") or "").strip()
            if check in cls.SUPPORTED_RULE_CHECKS:
                normalized["check"] = check
            else:
                normalized.pop("check", None)
        if "min_present" in normalized:
            try:
                value = int(normalized.get("min_present", 1))
            except Exception:
                value = 1
            normalized["min_present"] = max(1, value)
        if "fields" in normalized:
            fields_value = normalized.get("fields", [])
            if isinstance(fields_value, list):
                fields = [str(item or "").strip() for item in fields_value if str(item or "").strip()]
            else:
                fields = cls._split_text_tokens(fields_value)
            if fields:
                normalized["fields"] = fields
            else:
                normalized.pop("fields", None)
        if "evidence_keywords" in normalized:
            keywords_value = normalized.get("evidence_keywords", [])
            if isinstance(keywords_value, list):
                keywords = [str(item or "").strip() for item in keywords_value if str(item or "").strip()]
            else:
                keywords = cls._split_text_tokens(keywords_value)
            if keywords:
                normalized["evidence_keywords"] = keywords
            else:
                normalized.pop("evidence_keywords", None)
        for text_key in ["rule_name", "problem_item", "reasoning", "revision_suggestion"]:
            if text_key in normalized:
                text = str(normalized.get(text_key, "") or "").strip()
                if text:
                    normalized[text_key] = text
                else:
                    normalized.pop(text_key, None)
        return normalized

    def _normalize_rule_change(self, *, rules_doc: Dict[str, Any], value: Any) -> Dict[str, Any]:
        payload = self._safe_dict(value)
        action = str(payload.get("action", "") or "").strip().lower()
        scope = self._normalize_scope(payload.get("scope", ""))
        rule_id = self._normalize_rule_id(payload.get("rule_id", ""))
        method_profile = str(payload.get("method_profile", "") or "").strip()
        rule_index = self._build_rule_index(rules_doc)
        inferred = rule_index.get(rule_id, {})
        if isinstance(inferred, dict):
            inferred_scope = str(inferred.get("scope", "") or "").strip()
            inferred_profile = str(inferred.get("method_profile", "") or "").strip()
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

    def _apply_rule_change(self, rules_doc: Dict[str, Any], rule_change: Dict[str, Any]) -> bool:
        payload = self._safe_dict(rule_change)
        action = str(payload.get("action", "") or "").strip().lower()
        scope = str(payload.get("scope", "") or "").strip()
        rule_id = str(payload.get("rule_id", "") or "").strip()
        method_profile = str(payload.get("method_profile", "") or "").strip()
        rule_payload = self._safe_dict(payload.get("rule_payload"))
        if action not in self.SUPPORTED_RULE_CHANGE_ACTIONS or scope not in self.SUPPORTED_RULE_CHANGE_SCOPES or not rule_id:
            return False
        target_rules: List[Dict[str, Any]] | None = None
        if scope == "general_rules":
            rules_doc.setdefault("general_rules", [])
            target_rules = rules_doc["general_rules"] if isinstance(rules_doc.get("general_rules", []), list) else []
        else:
            profiles = rules_doc.setdefault("method_profiles", {})
            if not isinstance(profiles, dict) or not method_profile:
                return False
            profile_doc = profiles.setdefault(method_profile, {"display_name": method_profile, "keywords": [], "retrieval_sections": [], "knowledge_query": "", "rules": []})
            if not isinstance(profile_doc, dict):
                return False
            profile_doc.setdefault("rules", [])
            target_rules = profile_doc["rules"] if isinstance(profile_doc.get("rules", []), list) else []
        if target_rules is None:
            return False
        existing_index = next(
            (
                index
                for index, item in enumerate(target_rules)
                if isinstance(item, dict) and str(item.get("rule_id", "") or "").strip() == rule_id
            ),
            -1,
        )
        if action == "delete":
            if existing_index < 0:
                return False
            del target_rules[existing_index]
            return True
        if not rule_payload:
            return False
        rule_payload = deepcopy(rule_payload)
        rule_payload["rule_id"] = rule_id
        if action == "add":
            if existing_index >= 0:
                target_rules[existing_index] = rule_payload
            else:
                target_rules.append(rule_payload)
            self._sort_rules_in_place(target_rules)
            return True
        if existing_index < 0:
            return False
        merged_rule = deepcopy(target_rules[existing_index])
        merged_rule.update(rule_payload)
        merged_rule["rule_id"] = rule_id
        target_rules[existing_index] = merged_rule
        self._sort_rules_in_place(target_rules)
        return True

    @staticmethod
    def _build_verification_summary(verification_result: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "overall_verdict": str(verification_result.get("overall_verdict", "") or "").strip(),
            "overlay_summary": verification_result.get("overlay_summary", {}) if isinstance(verification_result.get("overlay_summary", {}), dict) else {},
        }

    @staticmethod
    def _mark_overlay_item_status(
        *,
        overlay_doc: Dict[str, Any],
        patch_id: str,
        status: str,
        verification_summary: Dict[str, Any],
    ) -> None:
        items = overlay_doc.get("items", []) if isinstance(overlay_doc.get("items", []), list) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            if str(item.get("patch_id", "") or "").strip() != str(patch_id or "").strip():
                continue
            item["status"] = status
            item["verification_summary"] = verification_summary
            break
