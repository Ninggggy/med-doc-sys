import json
import re
from typing import Any, Dict, List, Optional


class ReviewRuleJsonParser:
    """Parse uploaded review-rule JSON into normalized CTD section rows.

    Supported forms:
    - flat rows with explicit `section_id`
    - nested objects keyed by `section_id`, section name, or title path segments
    - objects/lists with `items` / `children` style nesting
    - leaf nodes carrying `rules`, `review_rules`, `concern_points`, `rule_texts`
    """

    RULE_LIST_KEYS = {
        "review_rules",
        "concern_points",
        "rule_texts",
        "manual_rule_texts",
    }
    RULE_SCALAR_KEYS = {"rule_text", "text", "value"}
    CHILD_LIST_KEYS = {"items", "children", "children_sections", "nodes"}
    META_SECTION_ID_KEYS = {"section_id", "sectionId"}
    META_NAME_KEYS = {"section_name", "sectionName", "name", "title", "label"}
    META_PATH_KEYS = {"title_path", "section_path", "path", "sectionPath"}
    RESERVED_KEYS = RULE_LIST_KEYS | RULE_SCALAR_KEYS | CHILD_LIST_KEYS | META_SECTION_ID_KEYS | META_NAME_KEYS | META_PATH_KEYS | {"rules"}

    def __init__(self, ctd_section_service):
        self.ctd_section_service = ctd_section_service
        catalog = self.ctd_section_service.get_catalog()
        all_sections = catalog.get("all_sections", []) if isinstance(catalog, dict) else []
        self._section_map: Dict[str, Dict[str, Any]] = {}
        self._section_name_index: Dict[str, List[str]] = {}
        self._section_path_index: Dict[str, str] = {}
        for item in all_sections or []:
            if not isinstance(item, dict):
                continue
            section_id = str(item.get("section_id", "") or "").strip()
            section_name = str(item.get("section_name", "") or section_id).strip() or section_id
            if not section_id:
                continue
            self._section_map[section_id] = item
            name_key = self._normalize_key(section_name)
            if name_key:
                self._section_name_index.setdefault(name_key, [])
                if section_id not in self._section_name_index[name_key]:
                    self._section_name_index[name_key].append(section_id)
            title_path = [str(part or "").strip() for part in (item.get("title_path") or []) if str(part or "").strip()]
            for start in range(0, len(title_path)):
                suffix = title_path[start:]
                suffix_key = self._normalize_tokens(suffix)
                if suffix_key and suffix_key not in self._section_path_index:
                    self._section_path_index[suffix_key] = section_id

    @staticmethod
    def _dedupe_texts(values: List[Any]) -> List[str]:
        out: List[str] = []
        seen = set()
        for item in values or []:
            if isinstance(item, dict):
                text = str(item.get("rule_text", "") or item.get("text", "") or item.get("value", "") or "").strip()
            else:
                text = str(item or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            out.append(text)
        return out

    @staticmethod
    def _normalize_key(value: Any) -> str:
        text = str(value or "").strip().lower()
        if not text:
            return ""
        return re.sub(r"[\s\-_./\\\\,:;，。：；、()（）\\[\\]{}]+", "", text)

    def _normalize_tokens(self, tokens: List[Any]) -> str:
        cleaned = [self._normalize_key(token) for token in (tokens or []) if self._normalize_key(token)]
        return "/".join(cleaned)

    def _collect_title_tokens(self, payload: Dict[str, Any]) -> List[str]:
        out: List[str] = []
        for key in self.META_SECTION_ID_KEYS | self.META_NAME_KEYS:
            value = payload.get(key)
            text = str(value or "").strip()
            if text:
                out.append(text)
        for key in self.META_PATH_KEYS:
            value = payload.get(key)
            if isinstance(value, list):
                out.extend([str(item or "").strip() for item in value if str(item or "").strip()])
            else:
                text = str(value or "").strip()
                if text:
                    out.extend([part for part in re.split(r"[>/\\\\]+", text) if str(part or "").strip()])
        return out

    @staticmethod
    def _looks_like_structured_section_id(value: Any) -> bool:
        text = str(value or "").strip()
        if not text:
            return False
        return bool(re.fullmatch(r"[0-9A-Za-z]+(?:\.[0-9A-Za-z_-]+){1,8}", text))

    def _infer_section_id(self, path_tokens: List[str], explicit_section_id: str = "") -> str:
        explicit = self.ctd_section_service.normalize_section_id(explicit_section_id)
        if explicit and isinstance(self.ctd_section_service.get_section(explicit, leaf_only=False), dict):
            return explicit
        if explicit and self._looks_like_structured_section_id(explicit):
            return explicit

        cleaned_tokens = [str(token or "").strip() for token in (path_tokens or []) if str(token or "").strip()]
        for width in range(len(cleaned_tokens), 0, -1):
            suffix_key = self._normalize_tokens(cleaned_tokens[-width:])
            section_id = self._section_path_index.get(suffix_key, "")
            if section_id:
                return section_id

        if cleaned_tokens:
            tail_key = self._normalize_key(cleaned_tokens[-1])
            matched = self._section_name_index.get(tail_key, [])
            if len(matched) == 1:
                return matched[0]

        joined = " / ".join(cleaned_tokens)
        inferred = self.ctd_section_service.infer_section_id_from_path(joined, leaf_only=False)
        if inferred:
            return inferred
        for token in reversed(cleaned_tokens):
            normalized_token = self.ctd_section_service.normalize_section_id(token)
            if normalized_token and self._looks_like_structured_section_id(normalized_token):
                return normalized_token
        return ""

    def _is_child_section_node(self, value: Any) -> bool:
        if not isinstance(value, dict):
            return False
        keys = set(value.keys())
        return bool(
            keys.intersection(
                self.META_SECTION_ID_KEYS
                | self.META_NAME_KEYS
                | self.META_PATH_KEYS
                | self.CHILD_LIST_KEYS
            )
        )

    def _partition_rule_list(self, values: Any) -> tuple[List[Any], List[Dict[str, Any]]]:
        """逐项区分本章规则与子章节节点，兼容二者混排的导出格式。"""
        if not isinstance(values, list):
            return [], []
        local_rules: List[Any] = []
        child_nodes: List[Dict[str, Any]] = []
        for item in values:
            if self._is_child_section_node(item):
                child_nodes.append(item)
            else:
                local_rules.append(item)
        return local_rules, child_nodes

    def _rules_list_is_child_nodes(self, values: Any) -> bool:
        _, child_nodes = self._partition_rule_list(values)
        return bool(child_nodes)

    def _extract_rule_texts(self, payload: Dict[str, Any]) -> List[str]:
        out: List[Any] = []
        for key in self.RULE_LIST_KEYS:
            value = payload.get(key)
            # Some exported rule trees put child section objects under fields
            # such as `review_rules` or `concern_points`, not only under
            # `rules`/`children`. Treat those lists as hierarchy containers so
            # a child's `rule_text` is never extracted into its parent section.
            local_rules, _ = self._partition_rule_list(value)
            out.extend(local_rules)
        rules_value = payload.get("rules")
        local_rules, _ = self._partition_rule_list(rules_value)
        out.extend(local_rules)
        for key in self.RULE_SCALAR_KEYS:
            value = payload.get(key)
            text = str(value or "").strip()
            if text:
                out.append(text)
        return self._dedupe_texts(out)

    def _append_rules(
        self,
        merged: Dict[str, Dict[str, Any]],
        path_tokens: List[str],
        review_rules: List[str],
        explicit_section_id: str = "",
    ) -> None:
        normalized_rules = self._dedupe_texts(review_rules)
        if not normalized_rules:
            return
        section_id = self._infer_section_id(path_tokens=path_tokens, explicit_section_id=explicit_section_id)
        if not section_id:
            joined_path = " / ".join([str(token or "").strip() for token in path_tokens if str(token or "").strip()])
            raise ValueError(f"unable to match review-rule section from json path: {joined_path or explicit_section_id or 'unknown'}")
        section_meta = self._section_map.get(section_id) or self.ctd_section_service.get_section(section_id, leaf_only=False) or {}
        section_name = str(section_meta.get("section_name", "") or section_id).strip() or section_id
        merged.setdefault(
            section_id,
            {
                "section_id": section_id,
                "section_name": section_name,
                "review_rules": [],
            },
        )
        merged[section_id]["review_rules"] = self._dedupe_texts(list(merged[section_id]["review_rules"]) + normalized_rules)

    def _walk_payload(
        self,
        payload: Any,
        merged: Dict[str, Dict[str, Any]],
        path_tokens: Optional[List[str]] = None,
    ) -> None:
        tokens = [str(token or "").strip() for token in (path_tokens or []) if str(token or "").strip()]

        if isinstance(payload, list):
            scalar_rules = [item for item in payload if not isinstance(item, (dict, list))]
            if scalar_rules:
                self._append_rules(merged, tokens, [str(item or "").strip() for item in scalar_rules])
            for item in payload:
                if isinstance(item, (dict, list)):
                    self._walk_payload(item, merged, tokens)
            return

        if not isinstance(payload, dict):
            return

        local_tokens = tokens + self._collect_title_tokens(payload)
        explicit_section_id = str(
            payload.get("section_id", "") or payload.get("sectionId", "") or ""
        ).strip()
        local_rules = self._extract_rule_texts(payload)
        if local_rules:
            self._append_rules(merged, local_tokens, local_rules, explicit_section_id=explicit_section_id)

        for key in self.CHILD_LIST_KEYS:
            value = payload.get(key)
            if isinstance(value, list):
                self._walk_payload(value, merged, local_tokens)

        for key in self.RULE_LIST_KEYS:
            value = payload.get(key)
            _, child_nodes = self._partition_rule_list(value)
            if child_nodes:
                self._walk_payload(child_nodes, merged, local_tokens)

        rules_value = payload.get("rules")
        _, child_nodes = self._partition_rule_list(rules_value)
        if child_nodes:
            self._walk_payload(child_nodes, merged, local_tokens)

        for key, value in payload.items():
            if key in self.RESERVED_KEYS:
                continue
            child_tokens = local_tokens + [str(key or "").strip()]
            if isinstance(value, (dict, list)):
                self._walk_payload(value, merged, child_tokens)
                continue
            text = str(value or "").strip()
            if not text:
                continue
            inferred_section_id = self._infer_section_id(child_tokens)
            if inferred_section_id:
                self._append_rules(merged, child_tokens, [text], explicit_section_id=inferred_section_id)

    def _normalize_items(self, payload: Any) -> List[Dict[str, Any]]:
        merged: Dict[str, Dict[str, Any]] = {}
        self._walk_payload(payload, merged, [])
        rows = [
            {
                "section_id": section_id,
                "section_name": str(item.get("section_name", "") or section_id).strip() or section_id,
                "review_rules": self._dedupe_texts(item.get("review_rules", [])),
            }
            for section_id, item in merged.items()
            if str(section_id or "").strip() and self._dedupe_texts(item.get("review_rules", []))
        ]
        rows.sort(key=lambda item: (len(str(item.get("section_id", "") or "").split(".")), str(item.get("section_id", "") or "")))
        return rows

    def parse_payload(self, payload: Any) -> List[Dict[str, Any]]:
        rows = self._normalize_items(payload)
        if not rows:
            raise ValueError("review rule json is empty or no CTD sections were matched")
        return rows

    def parse_text(self, text: str) -> List[Dict[str, Any]]:
        try:
            payload = json.loads(str(text or "").strip())
        except Exception as exc:
            raise ValueError(f"invalid review rule json: {str(exc)}") from exc
        return self.parse_payload(payload)

    def parse_bytes(self, file_bytes: bytes, file_name: str = "") -> List[Dict[str, Any]]:
        try:
            text = (file_bytes or b"").decode("utf-8-sig")
        except UnicodeDecodeError:
            text = (file_bytes or b"").decode("utf-8")
        normalized = self.parse_text(text)
        if not normalized:
            raise ValueError(f"review rule json is empty: {file_name or 'payload'}")
        return normalized
