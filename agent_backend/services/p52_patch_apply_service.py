from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from agent.agent_backend.services.p52_patch_registry_service import P52PatchRegistryService


class P52PatchApplyService:
    """将已批准或候选 patch 合成为运行时 overlay。"""

    def __init__(self, db_conn: Any) -> None:
        self.registry = P52PatchRegistryService(db_conn)

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

    @classmethod
    def _default_overlay(cls) -> Dict[str, Any]:
        return {
            "force_method_profiles": [],
            "exclude_method_profiles": [],
            "include_rule_ids": [],
            "exclude_rule_ids": [],
            "retrieval_keyword_hints": [],
            "retrieval_section_patterns": [],
            "prompt_suffixes": {
                "quality_standard_extractor": [],
                "entity_extraction": [],
                "method_judgment": [],
                "reviewer": [],
                "retrieval": [],
                "result_merge": [],
                "rule_filter": [],
            },
            "entity_hints": {
                "focus_fields": [],
                "exclude_entities": [],
                "field_keywords": {},
            },
            "rule_keyword_hints": {},
            "source_patch_ids": [],
            "applied_patches": [],
        }

    @classmethod
    def _extract_overlay_from_patch(cls, row: Dict[str, Any]) -> Dict[str, Any]:
        payload = cls._safe_dict(row.get("payload"))
        nested_payload = cls._safe_dict(payload.get("payload"))
        if cls._safe_dict(nested_payload.get("overlay")):
            return cls._safe_dict(nested_payload.get("overlay"))
        if cls._safe_dict(payload.get("overlay")):
            return cls._safe_dict(payload.get("overlay"))
        return {}

    def list_effective_patch_rows(
        self,
        *,
        run_id: str,
        section_id: str,
        patch_rows: List[Dict[str, Any]] | None = None,
        include_approved: bool = True,
        include_candidate: bool = False,
    ) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        if include_approved:
            rows.extend(self.registry.list_patches(run_id=run_id, section_id=section_id, status="approved"))
        if include_candidate:
            if isinstance(patch_rows, list):
                rows.extend([dict(item) for item in patch_rows if isinstance(item, dict)])
            else:
                rows.extend(self.registry.list_patches(run_id=run_id, section_id=section_id, status="candidate"))
        effective: List[Dict[str, Any]] = []
        seen = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            status = str(row.get("status", "") or "").strip().lower()
            if status == "approved" and include_approved:
                pass
            elif status == "candidate" and include_candidate:
                pass
            else:
                continue
            patch_id = str(row.get("patch_id", "") or "").strip()
            if patch_id in seen:
                continue
            seen.add(patch_id)
            effective.append(dict(row))
        return effective

    def build_runtime_overlay(
        self,
        *,
        run_id: str,
        section_id: str,
        patch_rows: List[Dict[str, Any]] | None = None,
        include_approved: bool = True,
        include_candidate: bool = False,
    ) -> Dict[str, Any]:
        overlay = self._default_overlay()
        effective_rows = self.list_effective_patch_rows(
            run_id=run_id,
            section_id=section_id,
            patch_rows=patch_rows,
            include_approved=include_approved,
            include_candidate=include_candidate,
        )
        for row in effective_rows:
            patch_id = str(row.get("patch_id", "") or "").strip()
            patch_type = str(row.get("patch_type", "") or "").strip()
            target_agent = str(row.get("target_agent", "") or "").strip()
            patch_overlay = self._extract_overlay_from_patch(row)
            overlay["source_patch_ids"].append(patch_id)
            overlay["applied_patches"].append(
                {
                    "patch_id": patch_id,
                    "patch_type": patch_type,
                    "target_agent": target_agent,
                    "status": str(row.get("status", "") or "").strip(),
                }
            )
            overlay["force_method_profiles"].extend(self._safe_list(patch_overlay.get("force_method_profiles", [])))
            overlay["exclude_method_profiles"].extend(self._safe_list(patch_overlay.get("exclude_method_profiles", [])))
            overlay["include_rule_ids"].extend(self._safe_list(patch_overlay.get("include_rule_ids", [])))
            overlay["exclude_rule_ids"].extend(self._safe_list(patch_overlay.get("exclude_rule_ids", [])))
            overlay["retrieval_keyword_hints"].extend(self._safe_list(patch_overlay.get("retrieval_keyword_hints", [])))
            overlay["retrieval_section_patterns"].extend(self._safe_list(patch_overlay.get("retrieval_section_patterns", [])))

            prompt_suffixes = self._safe_dict(patch_overlay.get("prompt_suffixes"))
            for key in overlay["prompt_suffixes"].keys():
                overlay["prompt_suffixes"][key].extend(self._safe_list(prompt_suffixes.get(key, [])))

            entity_hints = self._safe_dict(patch_overlay.get("entity_hints"))
            overlay["entity_hints"]["focus_fields"].extend(self._safe_list(entity_hints.get("focus_fields", [])))
            overlay["entity_hints"]["exclude_entities"].extend(self._safe_list(entity_hints.get("exclude_entities", [])))
            field_keywords = self._safe_dict(entity_hints.get("field_keywords"))
            for field_name, keywords in field_keywords.items():
                key = str(field_name or "").strip()
                if not key:
                    continue
                overlay["entity_hints"]["field_keywords"].setdefault(key, [])
                overlay["entity_hints"]["field_keywords"][key].extend(self._safe_list(keywords))

            rule_keyword_hints = self._safe_dict(patch_overlay.get("rule_keyword_hints"))
            for rule_id, keywords in rule_keyword_hints.items():
                key = str(rule_id or "").strip()
                if not key:
                    continue
                overlay["rule_keyword_hints"].setdefault(key, [])
                overlay["rule_keyword_hints"][key].extend(self._safe_list(keywords))

        overlay["force_method_profiles"] = self._safe_list(overlay["force_method_profiles"])
        overlay["exclude_method_profiles"] = self._safe_list(overlay["exclude_method_profiles"])
        overlay["include_rule_ids"] = self._safe_list(overlay["include_rule_ids"])
        overlay["exclude_rule_ids"] = self._safe_list(overlay["exclude_rule_ids"])
        overlay["retrieval_keyword_hints"] = self._safe_list(overlay["retrieval_keyword_hints"])
        overlay["retrieval_section_patterns"] = self._safe_list(overlay["retrieval_section_patterns"])
        overlay["source_patch_ids"] = self._safe_list(overlay["source_patch_ids"])
        overlay["entity_hints"]["focus_fields"] = self._safe_list(overlay["entity_hints"]["focus_fields"])
        overlay["entity_hints"]["exclude_entities"] = self._safe_list(overlay["entity_hints"]["exclude_entities"])
        for key, values in list(overlay["entity_hints"]["field_keywords"].items()):
            overlay["entity_hints"]["field_keywords"][key] = self._safe_list(values)
        for key, values in list(overlay["rule_keyword_hints"].items()):
            overlay["rule_keyword_hints"][key] = self._safe_list(values)
        for key, values in list(overlay["prompt_suffixes"].items()):
            overlay["prompt_suffixes"][key] = self._safe_list(values)
        return overlay

    def describe_runtime_overlay(self, overlay: Dict[str, Any]) -> Dict[str, Any]:
        value = deepcopy(overlay if isinstance(overlay, dict) else {})
        return {
            "patch_count": len(value.get("source_patch_ids", []) if isinstance(value.get("source_patch_ids", []), list) else []),
            "source_patch_ids": self._safe_list(value.get("source_patch_ids", [])),
            "force_method_profiles": self._safe_list(value.get("force_method_profiles", [])),
            "exclude_rule_ids": self._safe_list(value.get("exclude_rule_ids", [])),
            "include_rule_ids": self._safe_list(value.get("include_rule_ids", [])),
            "retrieval_section_patterns": self._safe_list(value.get("retrieval_section_patterns", [])),
            "prompt_suffix_targets": [
                key
                for key, rows in self._safe_dict(value.get("prompt_suffixes")).items()
                if self._safe_list(rows)
            ],
        }
