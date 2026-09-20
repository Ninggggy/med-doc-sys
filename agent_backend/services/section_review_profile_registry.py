from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List

from agent.agent_backend.config.settings import ROOT


def _dedupe_texts(values: List[Any]) -> List[str]:
    out: List[str] = []
    seen = set()
    for item in values or []:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _dedupe_profile_rows(values: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen = set()
    for item in values or []:
        if not isinstance(item, dict):
            continue
        profile_code = str(item.get("profile_code", "") or "").strip()
        match_keywords = tuple(
            str(keyword or "").strip()
            for keyword in item.get("match_keywords", [])
            if str(keyword or "").strip()
        )
        signature = (
            profile_code,
            str(item.get("question_type", "") or "").strip(),
            str(item.get("preferred_review_object", "") or "").strip(),
            match_keywords,
        )
        if signature in seen:
            continue
        seen.add(signature)
        out.append(dict(item))
    return out


class SectionReviewProfileRegistry:
    """Load section-level reviewer thinking profiles and merge them by section prefix."""

    def __init__(self, profile_path: str | None = None) -> None:
        self.profile_path = Path(profile_path or (ROOT / "data" / "raw_data" / "section_review_profiles.json"))

    @staticmethod
    def _normalize_section_id(value: Any) -> str:
        return str(value or "").strip().lower()

    @lru_cache(maxsize=1)
    def _load_document(self) -> Dict[str, Any]:
        if not self.profile_path.exists():
            return {}
        try:
            data = json.loads(self.profile_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        if isinstance(data, list):
            return {"profiles": data}
        if isinstance(data, dict):
            return data
        return {}

    @staticmethod
    def _normalize_rule_block(values: Any) -> List[str]:
        if isinstance(values, dict):
            values = values.get("values", [])
        if not isinstance(values, list):
            return []
        return _dedupe_texts(values)

    @staticmethod
    def _normalize_profile_block(values: Any) -> List[Dict[str, Any]]:
        if isinstance(values, dict):
            values = values.get("values", [])
        if not isinstance(values, list):
            return []
        rows: List[Dict[str, Any]] = []
        for item in values:
            if not isinstance(item, dict):
                continue
            rows.append(
                {
                    "profile_code": str(item.get("profile_code", "") or "").strip(),
                    "match_keywords": _dedupe_texts(item.get("match_keywords", []) or []),
                    "preferred_review_object": str(item.get("preferred_review_object", "") or "").strip(),
                    "question_type": str(item.get("question_type", "") or "").strip(),
                    "required_rule_types": _dedupe_texts(item.get("required_rule_types", []) or []),
                    "dimension_candidates": _dedupe_texts(item.get("dimension_candidates", []) or []),
                    "question_template": str(item.get("question_template", "") or "").strip(),
                    "reasoning_focus": _dedupe_texts(item.get("reasoning_focus", []) or []),
                }
            )
        return _dedupe_profile_rows(rows)

    @staticmethod
    def _build_default_profile(*, section_id: str, section_name: str = "") -> Dict[str, Any]:
        resolved_name = str(section_name or "").strip() or section_id
        return {
            "section_id": str(section_id or "").strip(),
            "section_name": resolved_name,
            "chapter_role": f"围绕章节“{resolved_name}”在注册论证链中的作用开展审评，判断资料是否完成该章节承担的申报任务。",
            "core_review_question": f"章节“{resolved_name}”是否提供了完成申报所需的关键资料、数据、结论和依据？",
            "core_review_principles": [],
            "must_answer_questions": [
                f"章节“{resolved_name}”是否围绕本章核心任务形成了直接、完整、可追溯的支撑？"
            ],
            "common_risks": [
                "只给出概括性描述，但缺少能直接支持结论的原文事实、研究数据或法规依据。",
                "章节事实、规则要求和检索证据之间没有真正形成可闭合的论证链。",
            ],
            "evidence_focus": [
                "优先寻找与本章直接适用的法规、指导原则、药典标准、原始研究数据和章节原文事实。",
            ],
            "medical_entity_focus": [],
            "medical_data_focus": [],
        }

    def _build_global_profile(self) -> Dict[str, Any]:
        document = self._load_document()
        framework = document.get("reasoning_framework", {}) if isinstance(document.get("reasoning_framework", {}), dict) else {}
        return {
            "reviewer_mindset": self._normalize_rule_block(framework.get("reviewer_mindset", {})),
            "task_generation_rules": self._normalize_rule_block(framework.get("task_generation_rules", {})),
            "evidence_judgment_rules": self._normalize_rule_block(framework.get("evidence_judgment_rules", {})),
            "conclusion_style_rules": self._normalize_rule_block(framework.get("conclusion_style_rules", {})),
            "feedback_optimization_focus": self._normalize_rule_block(framework.get("feedback_optimization_focus", {})),
            "rule_interpretation_profiles": self._normalize_profile_block(framework.get("rule_interpretation_profiles", {})),
        }

    @lru_cache(maxsize=1)
    def _load_rows(self) -> List[Dict[str, Any]]:
        document = self._load_document()
        data = document.get("profiles", []) if isinstance(document, dict) else []
        if not isinstance(data, list):
            return []
        rows: List[Dict[str, Any]] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            section_id = str(item.get("section_id", "") or "").strip()
            if not section_id:
                continue
            normalized = dict(item)
            normalized["section_id"] = self._normalize_section_id(section_id)
            rows.append(normalized)
        return rows

    @staticmethod
    def _merge_profiles(base: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any]:
        merged = dict(base or {})
        for key, value in (incoming or {}).items():
            if key == "section_id":
                merged[key] = str(value or "").strip()
                continue
            if isinstance(value, list):
                merged[key] = _dedupe_texts(list(merged.get(key, [])) + value)
                continue
            if isinstance(value, dict):
                current = merged.get(key, {})
                merged[key] = dict(current) if isinstance(current, dict) else {}
                merged[key].update(value)
                continue
            text = str(value or "").strip()
            if text:
                merged[key] = text
        return merged

    def resolve(self, *, section_id: str, section_name: str = "") -> Dict[str, Any]:
        target_id = self._normalize_section_id(section_id)
        if not target_id:
            return {}
        prefixes: List[str] = []
        parts = target_id.split(".")
        for index in range(1, len(parts) + 1):
            prefixes.append(".".join(parts[:index]))
        rows = self._load_rows()
        merged: Dict[str, Any] = self._merge_profiles(
            self._build_default_profile(section_id=target_id, section_name=section_name),
            self._build_global_profile(),
        )
        for prefix in prefixes:
            row = next((item for item in rows if self._normalize_section_id(item.get("section_id", "")) == prefix), None)
            if row:
                merged = self._merge_profiles(merged, row)
        if merged and section_name and not str(merged.get("section_name", "") or "").strip():
            merged["section_name"] = str(section_name or "").strip()
        return merged
