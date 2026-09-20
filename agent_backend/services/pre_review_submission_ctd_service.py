import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.agent_backend.utils.parser.bound_section_ocr_parser import (
    parse_bound_section_with_deepseek_ocr_to_payload,
)
from agent.agent_backend.utils.parser import ParserManager
from agent.agent_backend.utils.parser.ctd_ocr_parser import (
    parse_ctd_submission_with_deepseek_ocr_to_payload,
)


def _load_quick_ctd_parsers():
    from agent.agent_backend.utils.parser.ctd_paser import (
        parse_bound_section_pdf_to_payload,
        parse_ctd_submission_to_payload,
        parse_submission_pdf_to_payload,
    )

    return parse_bound_section_pdf_to_payload, parse_ctd_submission_to_payload, parse_submission_pdf_to_payload


def _load_docx_markdown_parser():
    from agent.agent_backend.utils.parser.docx_markdown_parser import parse_docx_to_markdown_json

    return parse_docx_to_markdown_json


class PreReviewSubmissionCTDService:
    """CTD-specific submission parsing helpers.

    This service keeps upload parsing deterministic:
    - single CTD file -> split by CTD leaf section
    - explicit chapter upload -> bind full extracted text to the selected section
    """

    def __init__(self, owner):
        self.owner = owner

    _CTD_SECTION_HEADING_RE = re.compile(
        r"^\s{0,3}(?:#{1,6}\s*)?(3\.2\.[a-z](?:\.\d+){0,6})\s*(.*?)\s*$",
        flags=re.IGNORECASE,
    )
    _CTD_SECTION_CODE_RE = re.compile(
        r"^\s{0,3}(?:#{1,6}\s*)?(3\s*\.\s*2\s*\.\s*[A-Za-z](?:(?:\s*[.,，、;；:：]\s*|\s*\.\s*)\d+){0,6})(.*)$",
        flags=re.IGNORECASE,
    )
    _SUPPORTED_SPLIT_ROOTS = ("1", "2", "3", "4", "5", "3.2", "3.2.s", "3.2.p", "3.2.a", "3.2.r")
    _MODULE3_DIRECTORY_ROOTS = {"3", "3.2"}
    _LEAF_SECTION_CACHE_BY_ROOT: Dict[str, List[Dict[str, Any]]] = {}

    @staticmethod
    def _normalize_file_type(file_path: str, file_type: str) -> str:
        normalized = str(file_type or "").strip().lower().lstrip(".")
        if normalized:
            return normalized
        return os.path.splitext(str(file_path or ""))[1].lstrip(".").lower()

    @staticmethod
    def _normalize_multiline_text(text: str) -> str:
        return str(text or "").replace("\ufeff", "", 1).replace("\r\n", "\n").replace("\r", "\n")

    @staticmethod
    def _normalize_body_lines(lines: List[str]) -> str:
        normalized: List[str] = []
        blank_streak = 0
        for raw_line in lines:
            line = str(raw_line or "").rstrip()
            if not line.strip():
                blank_streak += 1
                if blank_streak <= 1:
                    normalized.append("")
                continue
            blank_streak = 0
            normalized.append(line)
        return "\n".join(normalized).strip()

    @staticmethod
    def _normalize_markdown_text(text: str) -> str:
        return str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()

    @staticmethod
    def _preview_text_blocks(blocks: List[str]) -> str:
        cleaned = [str(item or "").strip() for item in blocks if str(item or "").strip()]
        return "\n\n".join(cleaned).strip()

    def _extract_ctd_heading(self, title: str) -> Dict[str, str]:
        normalized_title = self._normalize_multiline_text(title).split("\n", 1)[0].strip()
        match = self._CTD_SECTION_HEADING_RE.match(normalized_title)
        if not match:
            return {"section_id": "", "section_name": ""}
        section_id = self.owner.ctd_sections.normalize_section_id(match.group(1))
        section_name = str(match.group(2) or "").strip(" -—:：").strip()
        return {"section_id": section_id, "section_name": section_name}

    def _extract_ctd_heading_v2(self, title: str) -> Dict[str, str]:
        normalized_title = self._normalize_multiline_text(title).split("\n", 1)[0].strip()
        normalized_title = (
            normalized_title.replace("．", ".")
            .replace("。", ".")
            .replace("：", ":")
            .replace("，", ",")
            .replace("、", ",")
        )
        match = self._CTD_SECTION_CODE_RE.match(normalized_title)
        if not match:
            return {"section_id": "", "section_name": ""}
        normalized_code = re.sub(r"\s+", "", str(match.group(1) or ""))
        normalized_code = re.sub(r"[.,，、;；:：]+", ".", normalized_code)
        normalized_code = re.sub(r"\.+", ".", normalized_code).strip(".")
        section_id = self.owner.ctd_sections.normalize_section_id(normalized_code)
        section_name = str(match.group(2) or "").strip(" -—:：,，、.;；").strip()
        return {"section_id": section_id, "section_name": section_name}

    def _extract_ctd_heading_v3(self, title: str) -> Dict[str, str]:
        line = self._normalize_multiline_text(title).split("\n", 1)[0].strip()
        if not line:
            return {"section_id": "", "section_name": ""}
        # Prefer existing parser first to keep backward compatibility.
        parsed = self._extract_ctd_heading_v2(line)
        if str(parsed.get("section_id", "") or "").strip():
            return parsed
        fallback = re.match(
            r"^\s{0,3}(?:#{1,6}\s*)?(3\s*[.\u3002\uff0e]\s*2\s*[.\u3002\uff0e]\s*[A-Za-z](?:(?:\s*[.\u3002\uff0e,，、;；:：]\s*|\s+)[A-Za-z0-9_-]+){0,8})\s*(.*)$",
            line,
            flags=re.IGNORECASE,
        )
        if not fallback:
            return {"section_id": "", "section_name": ""}
        raw_code = str(fallback.group(1) or "")
        raw_name = str(fallback.group(2) or "")
        normalized_code = re.sub(r"\s+", "", raw_code)
        normalized_code = re.sub(r"[.\u3002\uff0e,，、;；:：]+", ".", normalized_code)
        normalized_code = re.sub(r"\.+", ".", normalized_code).strip(".")
        section_id = self.owner.ctd_sections.normalize_section_id(normalized_code)
        section_name = str(raw_name or "").strip().lstrip("-:：；;,.，、").strip()
        return {"section_id": section_id, "section_name": section_name}

    def _extract_inline_ctd_section_ids(self, text: str) -> List[str]:
        line = self._normalize_multiline_text(text)
        if not line.strip():
            return []
        matches = re.finditer(
            r"(3\s*[.\u3002\uff0e]\s*2\s*[.\u3002\uff0e]\s*[A-Za-z](?:(?:\s*[.\u3002\uff0e,，、;；:：]\s*|\s+)[A-Za-z0-9_-]+){2,8})",
            line,
            flags=re.IGNORECASE,
        )
        out: List[str] = []
        seen = set()
        for match in matches:
            raw_code = str(match.group(1) or "")
            normalized_code = re.sub(r"\s+", "", raw_code)
            normalized_code = re.sub(r"[.\u3002\uff0e,，、;；:：]+", ".", normalized_code)
            normalized_code = re.sub(r"\.+", ".", normalized_code).strip(".")
            section_id = self.owner.ctd_sections.normalize_section_id(normalized_code)
            if not section_id or not section_id.startswith("3.2.") or section_id in seen:
                continue
            seen.add(section_id)
            out.append(section_id)
        return out

    def _collect_detected_ctd_sections_from_text(self, raw_text: str) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        seen = set()
        text = self._normalize_multiline_text(raw_text)
        if not text.strip():
            return out
        for raw_line in text.split("\n"):
            line = str(raw_line or "").strip()
            if not line:
                continue
            heading = self._extract_ctd_heading_v3(line)
            candidates = []
            heading_id = self.owner.ctd_sections.normalize_section_id(heading.get("section_id"))
            if heading_id:
                candidates.append((heading_id, str(heading.get("section_name", "") or "").strip()))
            if not heading_id:
                for inline_id in self._extract_inline_ctd_section_ids(line):
                    candidates.append((inline_id, ""))
            for section_id, section_name in candidates:
                if not section_id or not section_id.startswith("3.2.") or section_id in seen:
                    continue
                seen.add(section_id)
                parts = [part for part in section_id.split(".") if part]
                out.append(
                    {
                        "section_id": section_id,
                        "section_name": section_name,
                        "parent_section_id": ".".join(parts[:-1]) if len(parts) > 1 else "",
                        "node_level": max(0, len(parts) - 1),
                    }
                )
        out.sort(
            key=lambda item: (
                int(item.get("node_level", 0) or 0),
                str(item.get("section_id", "") or "").strip(),
            )
        )
        return out

    def _collect_detected_ctd_sections_from_docx_nodes(
        self,
        nodes: List[Dict[str, Any]],
    ) -> List[Dict[str, str]]:
        out: List[Dict[str, str]] = []
        seen = set()

        def add_heading(raw_text: str) -> None:
            heading = self._extract_ctd_heading_v3(raw_text)
            section_id = self.owner.ctd_sections.normalize_section_id(heading.get("section_id"))
            if not section_id or not section_id.startswith("3.2.") or section_id in seen:
                return
            seen.add(section_id)
            parts = [part for part in section_id.split(".") if part]
            out.append(
                {
                    "section_id": section_id,
                    "section_name": str(heading.get("section_name", "") or "").strip(),
                    "parent_section_id": ".".join(parts[:-1]) if len(parts) > 1 else "",
                    "node_level": max(0, len(parts) - 1),
                }
            )

        def scan_content(content: str) -> None:
            text = self._normalize_multiline_text(content)
            if not text.strip():
                return
            for raw_line in text.split("\n"):
                line = str(raw_line or "").strip()
                if not line:
                    continue
                heading = self._extract_ctd_heading_v3(line)
                heading_id = self.owner.ctd_sections.normalize_section_id(heading.get("section_id"))
                add_heading(line)
                if not heading_id:
                    inline_ids = self._extract_inline_ctd_section_ids(line)
                    for inline_id in inline_ids:
                        add_heading(inline_id)

        def walk(items: List[Dict[str, Any]]) -> None:
            for node in items or []:
                if not isinstance(node, dict):
                    continue
                title = str(node.get("section_title") or node.get("title") or "").strip()
                add_heading(title)
                scan_content(str(node.get("content", "") or ""))
                children = node.get("children_sections") or []
                if isinstance(children, list):
                    walk(children)

        walk(nodes if isinstance(nodes, list) else [])
        out.sort(
            key=lambda item: (
                int(item.get("node_level", 0) or 0),
                str(item.get("section_id", "") or "").strip(),
            )
        )
        return out

    @staticmethod
    def _normalize_section_title_token_v2(text: Any) -> str:
        value = str(text or "").strip().lower()
        if not value:
            return ""
        value = re.sub(r"^\s*3\.2\.[a-z](?:\.\d+){0,6}\s*", "", value, flags=re.IGNORECASE)
        value = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", value)
        return value.strip()

    @staticmethod
    def _render_non_ctd_label_v2(title: str) -> str:
        return str(title or "").strip()

    @staticmethod
    def _strip_duplicate_title_from_body(body: str, title: str) -> str:
        normalized_body = str(body or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        normalized_title = str(title or "").replace("\r\n", "\n").replace("\r", "\n").strip()
        if not normalized_body or not normalized_title:
            return normalized_body

        body_lines = normalized_body.split("\n")
        first_non_empty_idx = next((idx for idx, line in enumerate(body_lines) if str(line or "").strip()), None)
        if first_non_empty_idx is None:
            return normalized_body

        first_line = str(body_lines[first_non_empty_idx] or "").strip()
        heading_variants = {
            normalized_title,
            f"# {normalized_title}",
            f"## {normalized_title}",
            f"### {normalized_title}",
            f"#### {normalized_title}",
            f"##### {normalized_title}",
            f"###### {normalized_title}",
        }
        if first_line in heading_variants or normalized_title in first_line:
            body_lines = body_lines[first_non_empty_idx + 1 :]
            return "\n".join(body_lines).strip()
        return normalized_body

    @staticmethod
    def _is_ctd_like_title(title: str) -> bool:
        value = str(title or "").strip().lower()
        return bool(value and re.search(r"3\.2\.[a-z]", value))

    @staticmethod
    def _is_external_numbered_heading(title: str) -> bool:
        value = str(title or "").strip()
        value = re.sub(r"^\s{0,3}#{1,6}\s*", "", value)
        if not value:
            return False
        # Example: 1.3.8.1.1 / 2.1 / 4.2.3
        return bool(re.match(r"^\d+(?:\.\d+){1,}\b", value))

    @staticmethod
    def _strip_markdown_images(text: str) -> str:
        value = str(text or "")
        if not value:
            return ""
        value = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", "", value)
        value = re.sub(r"\n{3,}", "\n\n", value)
        return value.strip()

    @staticmethod
    def _normalize_section_title_token(text: Any) -> str:
        value = str(text or "").strip().lower()
        if not value:
            return ""
        value = re.sub(r"^\s*3\.2\.[a-z](?:\.\d+){0,6}\s*", "", value, flags=re.IGNORECASE)
        value = re.sub(r"[\s\-_:/\\()\[\]（）【】、，,。；;：:]+", "", value)
        return value.strip()

    def _docx_title_candidate_ids(
        self,
        *,
        raw_title: str,
        allowed_section_ids: Dict[str, Dict[str, Any]],
        inherited_section_id: str = "",
    ) -> List[str]:
        heading = self._extract_ctd_heading_v3(raw_title)
        title_token = self._normalize_section_title_token_v2(heading.get("section_name") or raw_title)
        if not title_token:
            return []
        inherited = self.owner.ctd_sections.normalize_section_id(inherited_section_id)
        exact: List[str] = []
        partial: List[str] = []
        for section_id, meta in (allowed_section_ids or {}).items():
            if not isinstance(meta, dict):
                continue
            if inherited and not (section_id == inherited or section_id.startswith(f"{inherited}.")):
                continue
            tokens = {self._normalize_section_title_token_v2(meta.get("section_name"))}
            for item in meta.get("title_path", []) if isinstance(meta.get("title_path", []), list) else []:
                tokens.add(self._normalize_section_title_token_v2(item))
            tokens = {token for token in tokens if token}
            if title_token in tokens:
                exact.append(section_id)
            elif any(title_token in token or token in title_token for token in tokens if len(token) >= 2):
                partial.append(section_id)
        return exact or partial

    def _resolve_docx_catalog_section_id(
        self,
        *,
        raw_title: str,
        allowed_section_ids: Dict[str, Dict[str, Any]],
        inherited_section_id: str = "",
    ) -> str:
        if self._is_external_numbered_heading(raw_title) and not self._is_ctd_like_title(raw_title):
            return ""
        heading_info = self._extract_ctd_heading_v3(raw_title)
        explicit_section_id = str(heading_info.get("section_id", "") or "").strip()
        normalized_explicit_section_id = self.owner.ctd_sections.normalize_section_id(explicit_section_id)
        explicit_target = self._map_explicit_ctd_section_id(
            explicit_section_id=explicit_section_id,
            allowed_section_ids=list((allowed_section_ids or {}).keys()),
        )
        # A complete catalog id is the strongest possible signal.  Do not let a
        # similar title override it.
        if normalized_explicit_section_id in (allowed_section_ids or {}):
            return normalized_explicit_section_id
        explicit_target_is_descendant = bool(
            explicit_target
            and normalized_explicit_section_id
            and explicit_target != normalized_explicit_section_id
            and normalized_explicit_section_id.startswith(f"{explicit_target}.")
        )
        title_token = self._normalize_section_title_token_v2(heading_info.get("section_name") or raw_title)
        explicit_target_matches_title = False
        if explicit_target and title_token:
            explicit_meta = allowed_section_ids.get(explicit_target, {}) if isinstance(allowed_section_ids.get(explicit_target), dict) else {}
            explicit_tokens = {
                self._normalize_section_title_token_v2(explicit_meta.get("section_name")),
            }
            title_path = explicit_meta.get("title_path") or []
            if isinstance(title_path, list):
                for item in title_path:
                    explicit_tokens.add(self._normalize_section_title_token_v2(item))
            explicit_tokens = {item for item in explicit_tokens if item}
            explicit_target_matches_title = bool(
                title_token
                and any(
                    title_token == item or title_token in item or item in title_token
                    for item in explicit_tokens
                    if len(item) >= 2
                )
            )

        scored: List[tuple[int, str]] = []
        if title_token:
            for section_id, meta in (allowed_section_ids or {}).items():
                if not isinstance(meta, dict):
                    continue
                candidate_tokens = {
                    self._normalize_section_title_token_v2(meta.get("section_name")),
                }
                title_path = meta.get("title_path") or []
                if isinstance(title_path, list):
                    for item in title_path:
                        candidate_tokens.add(self._normalize_section_title_token_v2(item))
                candidate_tokens = {item for item in candidate_tokens if item}
                if not candidate_tokens:
                    continue
                score = 0
                if title_token in candidate_tokens:
                    score = 160
                elif any(title_token in item or item in title_token for item in candidate_tokens if len(item) >= 2):
                    score = 110
                if not score:
                    continue
                if bool(meta.get("is_leaf", False)):
                    score += 20
                normalized_inherited = self.owner.ctd_sections.normalize_section_id(inherited_section_id)
                if normalized_inherited and (
                    section_id == normalized_inherited or section_id.startswith(f"{normalized_inherited}.")
                ):
                    score += 35
                score += min(max(len(title_token), 0), 12)
                scored.append((score, section_id))
            scored.sort(key=lambda item: item[0], reverse=True)
        if explicit_target:
            if not scored:
                if not title_token or explicit_target_matches_title or explicit_target_is_descendant:
                    return explicit_target
                return inherited_section_id
            explicit_score = next((score for score, sid in scored if sid == explicit_target), 0)
            best_score, best_section_id = scored[0]
            if best_section_id != explicit_target and best_score >= max(140, explicit_score + 30):
                return best_section_id
            if not title_token or explicit_target_matches_title or explicit_score > 0 or explicit_target_is_descendant:
                return explicit_target
            return inherited_section_id
        if scored:
            best_score = scored[0][0]
            best_ids = [section_id for score, section_id in scored if score == best_score]
            if len(best_ids) == 1:
                return best_ids[0]
            # Duplicate section names are common in CTD trees.  Keep the content
            # on the reliable parent instead of silently choosing one sibling.
            return inherited_section_id
        return inherited_section_id

    def _map_explicit_ctd_section_id(
        self,
        *,
        explicit_section_id: str,
        allowed_section_ids: List[str],
    ) -> str:
        normalized_explicit = self.owner.ctd_sections.normalize_section_id(explicit_section_id)
        if not normalized_explicit:
            return ""
        allowed = {
            self.owner.ctd_sections.normalize_section_id(item)
            for item in allowed_section_ids or []
            if self.owner.ctd_sections.normalize_section_id(item)
        }
        if normalized_explicit in allowed:
            return normalized_explicit

        explicit_parts = normalized_explicit.split(".")
        while len(explicit_parts) > 3:
            explicit_parts = explicit_parts[:-1]
            candidate_section_id = self.owner.ctd_sections.normalize_section_id(".".join(explicit_parts))
            if candidate_section_id in allowed:
                return candidate_section_id
        return ""

    @staticmethod
    def _ctd_heading_level(section_id: str) -> int:
        return max(1, min(6, str(section_id or "").count(".") - 1))

    def _render_ctd_heading(self, section_id: str, section_name: str) -> str:
        normalized_id = self.owner.ctd_sections.normalize_section_id(section_id)
        normalized_name = str(section_name or "").strip()
        title = normalized_id
        if normalized_name and not normalized_name.startswith(normalized_id):
            title = f"{normalized_id} {normalized_name}".strip()
        elif normalized_name:
            title = normalized_name
        return f"{'#' * self._ctd_heading_level(normalized_id)} {title}".strip()

    @staticmethod
    def _render_non_ctd_label(title: str) -> str:
        normalized_title = str(title or "").strip()
        return f"【{normalized_title}】" if normalized_title else ""

    def _rewrite_docx_image_refs(self, text: str, images: List[Dict[str, Any]]) -> str:
        normalized = self._normalize_markdown_text(text)
        if not normalized:
            return ""
        image_map: Dict[str, str] = {}
        for item in images or []:
            if not isinstance(item, dict):
                continue
            ref = str(item.get("markdown_ref", "") or "").strip()
            if not ref:
                continue
            resolved = (
                str(item.get("image_path", "") or "").strip()
                or str(item.get("filename", "") or "").strip()
                or ref
            )
            if resolved:
                image_map[ref] = resolved
        for ref, resolved in image_map.items():
            normalized = normalized.replace(f"]({ref})", f"]({resolved})")
        return normalized

    def _render_docx_node_block(
        self,
        *,
        node: Dict[str, Any],
        current_ctd_section_id: str,
        current_ctd_section_name: str,
        current_title: str,
        explicit_ctd_heading: bool,
    ) -> str:
        body = self._rewrite_docx_image_refs(
            node.get("content", ""),
            node.get("images", []) if isinstance(node.get("images", []), list) else [],
        )
        body = self._strip_markdown_images(body)
        title_block = ""
        if explicit_ctd_heading and current_ctd_section_id:
            title_block = self._render_ctd_heading(current_ctd_section_id, current_ctd_section_name)
        elif current_title:
            node_level = int(node.get("level", 0) or 0)
            if node_level > 0:
                title_block = f"{'#' * min(node_level, 6)} {self._render_non_ctd_label_v2(current_title)}".strip()
            else:
                title_block = self._render_non_ctd_label_v2(current_title)
        body = self._strip_duplicate_title_from_body(body, current_title if current_title else current_ctd_section_name)
        return self._preview_text_blocks([title_block, body])

    def _split_docx_content_blocks(
        self,
        *,
        node: Dict[str, Any],
        allowed_section_ids: Dict[str, Dict[str, Any]],
        fallback_section_id: str = "",
        fallback_section_name: str = "",
        mapping_diagnostics: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, List[str]]:
        body = self._rewrite_docx_image_refs(
            node.get("content", ""),
            node.get("images", []) if isinstance(node.get("images", []), list) else [],
        )
        body = self._strip_markdown_images(body)
        normalized_body = self._normalize_markdown_text(body)
        if not normalized_body:
            return {}

        lines = normalized_body.split("\n")
        has_embedded_heading = False
        for raw_line in lines:
            line = str(raw_line or "").strip()
            if not line:
                continue
            heading = self._extract_ctd_heading_v3(line)
            explicit_section_id = str(heading.get("section_id", "") or "").strip()
            if explicit_section_id:
                has_embedded_heading = True
                break
            if self._is_external_numbered_heading(line) and not self._is_ctd_like_title(line):
                has_embedded_heading = True
                break
        if not has_embedded_heading:
            return {}

        out: Dict[str, List[str]] = {}
        current_section_id = ""
        current_section_name = ""
        current_lines: List[str] = []
        consumed_explicit_heading = False
        diagnostics = mapping_diagnostics if isinstance(mapping_diagnostics, list) else []

        def flush_current() -> None:
            nonlocal current_lines
            text = self._normalize_body_lines(current_lines)
            if current_section_id and text:
                out.setdefault(current_section_id, []).append(text)
            current_lines = []

        def ensure_fallback_started() -> None:
            nonlocal current_section_id, current_section_name, current_lines
            if not fallback_section_id:
                return
            if current_section_id:
                return
            current_section_id = fallback_section_id
            current_section_name = fallback_section_name
            current_lines = [self._render_ctd_heading(fallback_section_id, fallback_section_name or fallback_section_id)]

        def preserve_unmatched_heading(
            *,
            heading_text: str,
            line_index: int,
            diagnostic_type: str,
            message: str,
        ) -> None:
            nonlocal current_section_id, current_section_name, current_lines, consumed_explicit_heading
            flush_current()
            consumed_explicit_heading = True
            target_id = self.owner.ctd_sections.normalize_section_id(fallback_section_id)
            if target_id and target_id in (allowed_section_ids or {}):
                current_section_id = target_id
                current_section_name = (
                    fallback_section_name
                    or str(allowed_section_ids.get(target_id, {}).get("section_name", "") or "").strip()
                    or target_id
                )
                current_lines = [
                    "> [解析待确认] 以下标题未能唯一归入当前 CTD 目录，原文已保留在最近可靠父章节。",
                    f"### {heading_text}".strip(),
                ]
            else:
                current_section_id = ""
                current_section_name = ""
                current_lines = []
            diagnostics.append(
                {
                    "type": diagnostic_type,
                    "heading": heading_text,
                    "candidate_section_ids": [],
                    "preserved_under_section_id": current_section_id,
                    "source": {
                        **(node.get("source", {}) if isinstance(node.get("source", {}), dict) else {}),
                        "content_line_index": line_index,
                    },
                    "message": message,
                }
            )

        for line_index, raw_line in enumerate(lines):
            line = str(raw_line or "")
            stripped = line.strip()
            heading = self._extract_ctd_heading_v3(stripped)
            explicit_section_id = str(heading.get("section_id", "") or "").strip()
            normalized_explicit_section_id = self.owner.ctd_sections.normalize_section_id(explicit_section_id)
            explicit_mapped_section_id = self._map_explicit_ctd_section_id(
                explicit_section_id=explicit_section_id,
                allowed_section_ids=list((allowed_section_ids or {}).keys()),
            )
            explicit_section_is_exact = bool(
                normalized_explicit_section_id
                and normalized_explicit_section_id in (allowed_section_ids or {})
            )
            if explicit_section_is_exact:
                flush_current()
                consumed_explicit_heading = True
                current_section_id = normalized_explicit_section_id
                current_section_name = (
                    str(heading.get("section_name", "") or "").strip()
                    or str(allowed_section_ids.get(normalized_explicit_section_id, {}).get("section_name", "") or "").strip()
                    or normalized_explicit_section_id
                )
                current_lines = [self._render_ctd_heading(current_section_id, current_section_name)]
                continue
            if explicit_mapped_section_id and explicit_mapped_section_id in (allowed_section_ids or {}):
                # The source contains a real child heading that is deeper than the
                # currently mounted catalog. Keep the original child id/title in
                # the parent's body instead of truncating e.g. 3.2.s.3.1 to
                # 3.2.s.3. This preserves both the text and its source hierarchy.
                flush_current()
                consumed_explicit_heading = True
                current_section_id = (
                    fallback_section_id
                    if fallback_section_id in (allowed_section_ids or {})
                    else explicit_mapped_section_id
                )
                current_section_name = (
                    fallback_section_name
                    or str(allowed_section_ids.get(current_section_id, {}).get("section_name", "") or "").strip()
                    or current_section_id
                )
                original_heading_name = str(heading.get("section_name", "") or "").strip()
                original_heading_title = " ".join(
                    [item for item in [normalized_explicit_section_id, original_heading_name] if item]
                ).strip() or stripped
                current_lines = [
                    "> [解析待确认] 以下子章节比当前目录更深，原文已保留在最近可靠父章节。",
                    f"{'#' * self._ctd_heading_level(normalized_explicit_section_id)} {original_heading_title}".strip()
                ]
                diagnostics.append(
                    {
                        "type": "ctd_heading_outside_catalog_depth",
                        "heading": stripped,
                        "candidate_section_ids": [explicit_mapped_section_id],
                        "preserved_under_section_id": current_section_id,
                        "source": {
                            **(node.get("source", {}) if isinstance(node.get("source", {}), dict) else {}),
                            "content_line_index": line_index,
                        },
                        "message": f"章节“{stripped}”比当前目录更深，原文已保留在最近可靠父章节。",
                    }
                )
                continue
            if explicit_section_id:
                preserve_unmatched_heading(
                    heading_text=stripped,
                    line_index=line_index,
                    diagnostic_type="unmatched_ctd_heading",
                    message=f"章节“{stripped}”不在当前上传目录范围内，原文已保留在最近可靠父章节。",
                )
                continue
            if stripped and self._is_external_numbered_heading(stripped) and not self._is_ctd_like_title(stripped):
                preserve_unmatched_heading(
                    heading_text=stripped,
                    line_index=line_index,
                    diagnostic_type="non_ctd_numbered_heading",
                    message=f"标题“{stripped}”不是标准 CTD 编号，原文已保留且未自动猜测章节。",
                )
                continue
            if not current_section_id and not consumed_explicit_heading and fallback_section_id:
                ensure_fallback_started()
            if current_section_id:
                current_lines.append(line.rstrip())

        flush_current()
        return out

    def _collect_docx_blocks_by_ctd_section(
        self,
        *,
        nodes: List[Dict[str, Any]],
        allowed_section_ids: Dict[str, Dict[str, Any]],
        inherited_section_id: str = "",
        inherited_section_name: str = "",
        blocks_by_section: Optional[Dict[str, List[str]]] = None,
        mapping_diagnostics: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, List[str]]:
        out = blocks_by_section if isinstance(blocks_by_section, dict) else {}
        diagnostics = mapping_diagnostics if isinstance(mapping_diagnostics, list) else []
        for node in nodes or []:
            if not isinstance(node, dict):
                continue
            raw_title = str(node.get("section_title") or node.get("title") or "").strip()
            heading_info = self._extract_ctd_heading_v3(raw_title)
            explicit_section_id = str(heading_info.get("section_id", "") or "").strip()
            explicit_section_name = str(heading_info.get("section_name", "") or "").strip()
            target_section_id = inherited_section_id
            target_section_name = inherited_section_name
            explicit_target_section_id = ""
            attach_to_inherited = False
            normalized_explicit_section_id = self.owner.ctd_sections.normalize_section_id(explicit_section_id)
            explicit_section_in_allowed = bool(
                normalized_explicit_section_id
                and normalized_explicit_section_id in (allowed_section_ids or {})
            )
            resolved_section_id = self._resolve_docx_catalog_section_id(
                raw_title=raw_title,
                allowed_section_ids=allowed_section_ids,
                inherited_section_id=inherited_section_id,
            )
            title_candidates = self._docx_title_candidate_ids(
                raw_title=raw_title,
                allowed_section_ids=allowed_section_ids,
                inherited_section_id=inherited_section_id,
            ) if raw_title and not explicit_section_id else []
            if len(title_candidates) > 1:
                attach_to_inherited = True
                diagnostics.append(
                    {
                        "type": "ambiguous_heading",
                        "heading": raw_title,
                        "candidate_section_ids": title_candidates,
                        "preserved_under_section_id": inherited_section_id,
                        "source": node.get("source", {}) if isinstance(node.get("source", {}), dict) else {},
                        "message": f"标题“{raw_title}”可对应多个章节，内容已保留在最近可靠父章节，需人工确认。",
                    }
                )
            if resolved_section_id and resolved_section_id in allowed_section_ids:
                if explicit_section_in_allowed or not explicit_section_id:
                    target_section_id = resolved_section_id
                    target_section_name = (
                        explicit_section_name
                        or str(allowed_section_ids.get(resolved_section_id, {}).get("section_name", "") or "").strip()
                        or resolved_section_id
                    )
                    if explicit_section_id or resolved_section_id != inherited_section_id:
                        explicit_target_section_id = resolved_section_id
                else:
                    attach_to_inherited = True
            elif raw_title:
                if self._is_external_numbered_heading(raw_title) and not self._is_ctd_like_title(raw_title):
                    # Preserve non-CTD subheadings as source structure without
                    # pretending that their local numbers are CTD catalog ids.
                    attach_to_inherited = bool(inherited_section_id)
                    target_section_id = inherited_section_id
                    target_section_name = inherited_section_name
                    diagnostics.append(
                        {
                            "type": "non_ctd_numbered_heading",
                            "heading": raw_title,
                            "candidate_section_ids": [],
                            "preserved_under_section_id": inherited_section_id,
                            "source": node.get("source", {}) if isinstance(node.get("source", {}), dict) else {},
                            "message": f"标题“{raw_title}”不是标准 CTD 编号，原文已保留且未自动猜测章节。",
                        }
                    )
                else:
                    attach_to_inherited = True
            split_blocks = self._split_docx_content_blocks(
                node=node,
                allowed_section_ids=allowed_section_ids,
                fallback_section_id=target_section_id,
                fallback_section_name=target_section_name,
                mapping_diagnostics=diagnostics,
            )
            if split_blocks:
                for section_id, values in split_blocks.items():
                    for value in values or []:
                        if section_id and value:
                            out.setdefault(section_id, []).append(value)
                children = node.get("children_sections") or []
                if isinstance(children, list) and children:
                    inherited_for_children = target_section_id if target_section_id in (allowed_section_ids or {}) else inherited_section_id
                    inherited_name_for_children = target_section_name if inherited_for_children == target_section_id else inherited_section_name
                    self._collect_docx_blocks_by_ctd_section(
                        nodes=children,
                        allowed_section_ids=allowed_section_ids,
                        inherited_section_id=inherited_for_children,
                        inherited_section_name=inherited_name_for_children,
                        blocks_by_section=out,
                        mapping_diagnostics=diagnostics,
                    )
                continue
            block_text = self._render_docx_node_block(
                node=node,
                current_ctd_section_id=target_section_id,
                current_ctd_section_name=target_section_name,
                current_title="" if explicit_target_section_id else raw_title,
                explicit_ctd_heading=bool(explicit_target_section_id),
            )
            if attach_to_inherited and not explicit_target_section_id:
                target_section_id = inherited_section_id
                target_section_name = inherited_section_name
                if block_text and raw_title and not explicit_section_id:
                    block_text = self._preview_text_blocks(
                        [f"> [解析待确认] 标题“{raw_title}”未能唯一匹配，以下原文暂保留在本章节。", block_text]
                    )
            if target_section_id and block_text:
                out.setdefault(target_section_id, []).append(block_text)
            children = node.get("children_sections") or []
            if isinstance(children, list) and children:
                self._collect_docx_blocks_by_ctd_section(
                    nodes=children,
                    allowed_section_ids=allowed_section_ids,
                    inherited_section_id=target_section_id,
                    inherited_section_name=target_section_name,
                    blocks_by_section=out,
                    mapping_diagnostics=diagnostics,
                )
        return out

    def _render_docx_document_markdown(self, payload: Dict[str, Any]) -> str:
        sections = payload.get("sections") or []
        allowed: Dict[str, Dict[str, Any]] = {}
        blocks_by_section = self._collect_docx_blocks_by_ctd_section(
            nodes=sections if isinstance(sections, list) else [],
            allowed_section_ids=allowed,
        )
        if blocks_by_section:
            merged = []
            for values in blocks_by_section.values():
                merged.extend(values)
            return self._preview_text_blocks(merged)

        output_blocks: List[str] = []

        def walk(nodes: List[Dict[str, Any]]) -> None:
            for node in nodes or []:
                if not isinstance(node, dict):
                    continue
                title = str(node.get("section_title") or node.get("title") or "").strip()
                heading_info = self._extract_ctd_heading_v3(title)
                ctd_section_id = str(heading_info.get("section_id", "") or "").strip()
                ctd_section_name = str(heading_info.get("section_name", "") or "").strip()
                block = self._render_docx_node_block(
                    node=node,
                    current_ctd_section_id=ctd_section_id,
                    current_ctd_section_name=ctd_section_name or ctd_section_id,
                    current_title=title,
                    explicit_ctd_heading=bool(ctd_section_id),
                )
                if block:
                    output_blocks.append(block)
                children = node.get("children_sections") or []
                if isinstance(children, list):
                    walk(children)

        walk(sections if isinstance(sections, list) else [])
        return self._preview_text_blocks(output_blocks)

    @staticmethod
    def _build_ctd_docx_parse_quality(
        *,
        docx_payload: Dict[str, Any],
        detected_sections: List[Dict[str, Any]],
        sections: List[Dict[str, Any]],
        mapping_diagnostics: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        generic_quality = docx_payload.get("parse_quality", {}) if isinstance(docx_payload.get("parse_quality", {}), dict) else {}
        generic_warnings = docx_payload.get("parser_warnings", []) if isinstance(docx_payload.get("parser_warnings", []), list) else []
        diagnostic_messages = [
            str(item.get("message", "") or "").strip()
            for item in mapping_diagnostics or []
            if isinstance(item, dict) and str(item.get("message", "") or "").strip()
        ]
        parser_warnings = list(dict.fromkeys([str(item or "").strip() for item in generic_warnings + diagnostic_messages if str(item or "").strip()]))
        char_counts = [int(item.get("char_count", 0) or 0) for item in sections or [] if isinstance(item, dict)]
        total_char_count = sum(char_counts)
        largest_ratio = round(max(char_counts, default=0) / total_char_count, 4) if total_char_count else 0.0
        empty_section_count = sum(1 for value in char_counts if value <= 0)
        if mapping_diagnostics:
            status_code = "manual_review"
        elif parser_warnings:
            status_code = "warning"
        else:
            status_code = "ok"
        return {
            **generic_quality,
            "status_code": status_code,
            "parser": str((docx_payload.get("parser", {}) or {}).get("markdown_source", "word_ooxml_native")),
            "detected_heading_count": int(generic_quality.get("detected_heading_count", 0) or 0),
            "detected_ctd_heading_count": len(detected_sections or []),
            "mapped_section_count": len(sections or []),
            "unmapped_heading_count": len(mapping_diagnostics or []),
            "empty_section_count": empty_section_count,
            "content_char_count": total_char_count,
            "largest_section_char_ratio": largest_ratio,
            "unmapped_headings": mapping_diagnostics[:50],
            "warnings": parser_warnings,
        }

    def _build_single_ctd_docx_payload(
        self,
        *,
        file_path: str,
        root_section_id: str,
        catalog: Dict[str, Any],
    ) -> Dict[str, Any]:
        parse_docx_to_markdown_json = _load_docx_markdown_parser()
        docx_payload = parse_docx_to_markdown_json(
            input_path=Path(file_path).expanduser().resolve(),
            title=None,
            embed_images=False,
            skip_toc=True,
            filter_header_footer=True,
            merge_continuous_tables=True,
        )
        if not isinstance(docx_payload, dict):
            return {}
        detected_sections = self._collect_detected_ctd_sections_from_docx_nodes(
            docx_payload.get("sections", []) if isinstance(docx_payload.get("sections", []), list) else []
        )
        mapping_diagnostics: List[Dict[str, Any]] = []

        section_scope = self._catalog_section_maps_for_root(catalog=catalog, root_section_id=root_section_id)
        if not section_scope:
            return {}
        root_key = self.owner.ctd_sections.normalize_section_id(root_section_id)
        leaf_sections = self._leaf_sections_for_root(catalog=catalog, root_section_id=root_section_id)
        if not leaf_sections:
            return {}
        leaf_section_scope = self._leaf_section_maps_for_root(catalog=catalog, root_section_id=root_section_id)
        leaf_descendant_counts = self._count_leaf_descendants(leaf_sections=leaf_sections, section_scope=section_scope)

        detected_scope_items = [
            dict(item)
            for item in detected_sections
            if isinstance(item, dict)
            and str(item.get("section_id", "") or "").strip()
            and (
                self.owner.ctd_sections.normalize_section_id(item.get("section_id")) == root_key
                or self.owner.ctd_sections.normalize_section_id(item.get("section_id")).startswith(f"{root_key}.")
            )
        ]

        if detected_scope_items:
            detected_scope_map: Dict[str, Dict[str, Any]] = {}
            for item in detected_scope_items:
                sid = self.owner.ctd_sections.normalize_section_id(item.get("section_id"))
                if not sid:
                    continue
                detected_scope_map[sid] = {
                    "section_id": sid,
                    "section_code": sid,
                    "section_name": str(item.get("section_name", "") or sid).strip() or sid,
                    "parent_section_id": self.owner.ctd_sections.normalize_section_id(item.get("parent_section_id")) or "",
                    "node_level": int(item.get("node_level", max(0, len(sid.split(".")) - 1)) or 0),
                    "title_path": [],
                    "is_leaf": True,
                }
            for sid, item in list(detected_scope_map.items()):
                parent_id = str(item.get("parent_section_id", "") or "").strip()
                if parent_id and parent_id in detected_scope_map:
                    detected_scope_map[parent_id]["is_leaf"] = False

            def build_title_path(section_id: str) -> List[str]:
                seen_ids = set()
                path: List[str] = []
                current_id = self.owner.ctd_sections.normalize_section_id(section_id)
                while current_id and current_id not in seen_ids:
                    seen_ids.add(current_id)
                    meta = detected_scope_map.get(current_id, {}) if isinstance(detected_scope_map.get(current_id, {}), dict) else {}
                    if not meta:
                        meta = section_scope.get(current_id, {}) if isinstance(section_scope.get(current_id, {}), dict) else {}
                    label = str(meta.get("section_name", "") or current_id).strip() or current_id
                    path.append(label)
                    current_id = self.owner.ctd_sections.normalize_section_id(meta.get("parent_section_id"))
                path.reverse()
                return path

            for sid, item in detected_scope_map.items():
                item["title_path"] = build_title_path(sid)

            detected_blocks = self._collect_docx_blocks_by_ctd_section(
                nodes=docx_payload.get("sections", []) if isinstance(docx_payload.get("sections", []), list) else [],
                allowed_section_ids=detected_scope_map,
                inherited_section_id=root_key,
                inherited_section_name=str(section_scope.get(root_key, {}).get("section_name", "") or root_key),
                mapping_diagnostics=mapping_diagnostics,
            )
            if detected_blocks:
                ordered_detected = sorted(
                    detected_scope_map.values(),
                    key=lambda item: (
                        int(item.get("node_level", 0) or 0),
                        str(item.get("section_id", "") or "").strip(),
                    ),
                )
                sections: List[Dict[str, Any]] = []
                review_units: List[Dict[str, Any]] = []
                for order, item in enumerate(ordered_detected, start=1):
                    section_id = str(item.get("section_id", "") or "").strip()
                    block_values = detected_blocks.get(section_id, [])
                    merged_text = self._preview_text_blocks(block_values if isinstance(block_values, list) else [block_values])
                    if not merged_text:
                        continue
                    section_name = str(item.get("section_name", "") or section_id).strip() or section_id
                    title_path = list(item.get("title_path") or [section_name])
                    parent_section_id = str(item.get("parent_section_id", "") or "").strip()
                    sections.append(
                        {
                            "section_id": section_id,
                            "section_code": str(item.get("section_code", "") or section_id).strip() or section_id,
                            "section_name": section_name,
                            "title_path": title_path,
                            "parent_section_id": parent_section_id,
                            "page_start": None,
                            "page_end": None,
                            "content": merged_text,
                            "content_preview": self.owner._preview(merged_text, 320),
                            "char_count": len(merged_text),
                            "tables": [],
                            "children_sections": [],
                        }
                    )
                    review_units.append(
                        {
                            "chunk_id": section_id,
                            "section_id": section_id,
                            "section_code": str(item.get("section_code", "") or section_id).strip() or section_id,
                            "section_name": section_name,
                            "parent_section_id": parent_section_id,
                            "page": None,
                            "page_start": None,
                            "page_end": None,
                            "text": merged_text,
                            "title_path": title_path,
                            "char_count": len(merged_text),
                            "unit_order": order,
                            "unit_type": "ctd_docx_detected_section",
                            "pipeline": "ctd_single_file_docx_detected",
                        }
                    )
                if sections:
                    leaf_sibling_groups = self._build_leaf_sibling_groups(sections)
                    parse_quality = self._build_ctd_docx_parse_quality(
                        docx_payload=docx_payload,
                        detected_sections=detected_sections,
                        sections=sections,
                        mapping_diagnostics=mapping_diagnostics,
                    )
                    return {
                        "title": str(root_section_id or "").strip(),
                        "source_file": file_path,
                        "root_section_id": root_key,
                        "parser_mode": "quick",
                        "parser_name": "ctd_single_file_docx_detected",
                        "source_parser": "ctd_docx_markdown",
                        "structure_type": "ctd_docx_leaf_section_payload_v1",
                        "chapter_structure": self.owner.ctd_sections.build_tree_from_leaf_sections(sections),
                        "sections": sections,
                        "leaf_sibling_groups": leaf_sibling_groups,
                        "review_units": review_units,
                        "statistics": {
                            "section_total": len(sections),
                            "review_unit_total": len(review_units),
                            "leaf_sibling_group_count": len(leaf_sibling_groups),
                        },
                        "detected_ctd_sections": detected_sections,
                        "parse_quality": parse_quality,
                        "parser_warnings": parse_quality.get("warnings", []),
                    }

        blocks_by_section = self._collect_docx_blocks_by_ctd_section(
            nodes=docx_payload.get("sections", []) if isinstance(docx_payload.get("sections", []), list) else [],
            allowed_section_ids=leaf_section_scope,
            inherited_section_id=root_key,
            inherited_section_name=str(section_scope.get(root_key, {}).get("section_name", "") or root_key),
            mapping_diagnostics=mapping_diagnostics,
        )
        if not blocks_by_section:
            return {}

        sections: List[Dict[str, Any]] = []
        review_units: List[Dict[str, Any]] = []
        for order, leaf in enumerate(leaf_sections, start=1):
            section_id = str(leaf.get("section_id", "") or "").strip()
            if not section_id:
                continue
            merged_text = self._resolve_leaf_content_text(
                section_id=section_id,
                root_section_id=root_key,
                section_scope=section_scope,
                blocks_by_section=blocks_by_section,
                leaf_descendant_counts=leaf_descendant_counts,
            )
            if not merged_text:
                continue
            section_name = str(leaf.get("section_name", "") or section_id).strip() or section_id
            title_path = list(leaf.get("title_path") or [section_name])
            parent_section_id = str(leaf.get("parent_section_id", "") or "").strip()
            section_item = {
                "section_id": section_id,
                "section_code": str(leaf.get("section_code", "") or section_id).strip() or section_id,
                "section_name": section_name,
                "title_path": title_path,
                "parent_section_id": parent_section_id,
                "page_start": None,
                "page_end": None,
                "content": merged_text,
                "content_preview": self.owner._preview(merged_text, 320),
                "char_count": len(merged_text),
                "tables": [],
                "children_sections": [],
            }
            sections.append(section_item)
            review_units.append(
                {
                    "chunk_id": section_id,
                    "section_id": section_id,
                    "section_code": str(leaf.get("section_code", "") or section_id).strip() or section_id,
                    "section_name": section_name,
                    "parent_section_id": parent_section_id,
                    "page": None,
                    "page_start": None,
                    "page_end": None,
                    "text": merged_text,
                    "title_path": title_path,
                    "char_count": len(merged_text),
                    "unit_order": order,
                    "unit_type": "ctd_docx_leaf_section",
                    "pipeline": "ctd_single_file_docx_split",
                }
            )

        if not sections:
            return {}

        leaf_sibling_groups = self._build_leaf_sibling_groups(sections)
        parse_quality = self._build_ctd_docx_parse_quality(
            docx_payload=docx_payload,
            detected_sections=detected_sections,
            sections=sections,
            mapping_diagnostics=mapping_diagnostics,
        )
        return {
            "title": str(root_section_id or "").strip(),
            "source_file": file_path,
            "root_section_id": root_key,
            "parser_mode": "quick",
            "parser_name": "ctd_single_file_docx_markdown",
            "source_parser": "ctd_docx_markdown",
            "structure_type": "ctd_docx_leaf_section_payload_v1",
            "chapter_structure": self.owner.ctd_sections.build_tree_from_leaf_sections(sections),
            "sections": sections,
            "leaf_sibling_groups": leaf_sibling_groups,
            "review_units": review_units,
            "statistics": {
                "section_total": len(sections),
                "review_unit_total": len(review_units),
                "leaf_sibling_group_count": len(leaf_sibling_groups),
            },
            "detected_ctd_sections": detected_sections,
            "parse_quality": parse_quality,
            "parser_warnings": parse_quality.get("warnings", []),
        }

    def _build_bound_docx_payload(
        self,
        *,
        file_path: str,
        section_meta: Dict[str, Any],
    ) -> Dict[str, Any]:
        parse_docx_to_markdown_json = _load_docx_markdown_parser()
        payload = parse_docx_to_markdown_json(
            input_path=Path(file_path).expanduser().resolve(),
            title=None,
            embed_images=False,
            skip_toc=True,
            filter_header_footer=True,
            merge_continuous_tables=True,
        )
        detected_sections = self._collect_detected_ctd_sections_from_docx_nodes(
            payload.get("sections", []) if isinstance(payload, dict) and isinstance(payload.get("sections", []), list) else []
        )
        merged_text = self._render_docx_document_markdown(payload if isinstance(payload, dict) else {})
        section_id = str(section_meta.get("section_id", "") or "").strip()
        section_name = str(section_meta.get("section_name", "") or section_id).strip() or section_id
        parent_section_id = str(section_meta.get("parent_section_id", "") or "").strip()
        result = {
            "title": section_name,
            "source_file": file_path,
            "structure_type": "bound_section_docx_markdown_payload_v1",
            "source_parser": "bound_section_docx_markdown",
            "sections": [
                {
                    "section_id": section_id,
                    "section_code": section_id,
                    "section_name": section_name,
                    "title_path": [section_name],
                    "parent_section_id": parent_section_id,
                    "content": merged_text,
                    "content_preview": self.owner._preview(merged_text, 320),
                    "char_count": len(merged_text),
                    "page_start": None,
                    "page_end": None,
                }
            ],
            "review_units": [
                {
                    "chunk_id": section_id,
                    "section_id": section_id,
                    "section_code": section_id,
                    "section_name": section_name,
                    "parent_section_id": parent_section_id,
                    "page": None,
                    "page_start": None,
                    "page_end": None,
                    "text": merged_text,
                    "title_path": [section_name],
                    "unit_order": 1,
                    "unit_type": "bound_section_docx_markdown",
                    "char_count": len(merged_text),
                }
            ],
            "statistics": {
                "section_total": 1 if merged_text else 0,
                "review_unit_total": 1 if merged_text else 0,
            },
            "detected_ctd_sections": detected_sections,
        }
        parse_quality = self._build_ctd_docx_parse_quality(
            docx_payload=payload if isinstance(payload, dict) else {},
            detected_sections=detected_sections,
            sections=result["sections"],
            mapping_diagnostics=[],
        )
        result["parse_quality"] = parse_quality
        result["parser_warnings"] = parse_quality.get("warnings", [])
        return result

    def _load_single_ctd_source_text(self, *, file_path: str, file_type: str) -> str:
        normalized_file_type = self._normalize_file_type(file_path, file_type)
        if normalized_file_type in {"md", "txt"}:
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as fh:
                    return self._normalize_multiline_text(fh.read())
            except Exception:
                return ""

        if ParserManager.is_supported(normalized_file_type):
            units = ParserManager.parse(file_path, ext_hint=normalized_file_type)
            blocks = []
            for item in units:
                if not isinstance(item, dict):
                    continue
                text = str(item.get("text", "") or item.get("content", "") or "").strip()
                if text:
                    blocks.append(text)
            return self._normalize_multiline_text(self.owner._preview_text_blocks(blocks))
        return ""

    def _catalog_section_maps_for_root(
        self,
        *,
        catalog: Dict[str, Any],
        root_section_id: str,
    ) -> Dict[str, Dict[str, Any]]:
        root_key = self.owner.ctd_sections.normalize_section_id(root_section_id)
        section_map = catalog.get("section_map", {}) if isinstance(catalog.get("section_map", {}), dict) else {}
        return {
            str(item.get("section_id", "") or "").strip(): item
            for item in section_map.values()
            if isinstance(item, dict)
            and str(item.get("section_id", "") or "").strip()
            and (
                str(item.get("section_id", "") or "").strip() == root_key
                or str(item.get("root_section_id", "") or "").strip() == root_key
            )
        }

    def _leaf_sections_for_root(
        self,
        *,
        catalog: Dict[str, Any],
        root_section_id: str,
    ) -> List[Dict[str, Any]]:
        root_key = self.owner.ctd_sections.normalize_section_id(root_section_id)
        if not root_key:
            return []
        cache_key = root_key
        cached = self._LEAF_SECTION_CACHE_BY_ROOT.get(cache_key)
        if isinstance(cached, list) and cached:
            return [dict(item) for item in cached]
        flat_sections = catalog.get("flat_sections", []) if isinstance(catalog.get("flat_sections", []), list) else []
        leaf_sections = [
            dict(item)
            for item in flat_sections
            if isinstance(item, dict)
            and str(item.get("root_section_id", "") or "").strip() == root_key
        ]
        leaf_sections.sort(key=lambda item: str(item.get("section_id", "") or ""))
        self._LEAF_SECTION_CACHE_BY_ROOT[cache_key] = [dict(item) for item in leaf_sections]
        return leaf_sections

    def _leaf_section_maps_for_root(
        self,
        *,
        catalog: Dict[str, Any],
        root_section_id: str,
    ) -> Dict[str, Dict[str, Any]]:
        return {
            str(item.get("section_id", "") or "").strip(): item
            for item in self._leaf_sections_for_root(catalog=catalog, root_section_id=root_section_id)
            if str(item.get("section_id", "") or "").strip()
        }

    def _supported_split_roots_under(
        self,
        *,
        catalog: Dict[str, Any],
        section_id: str,
    ) -> List[str]:
        target_id = self.owner.ctd_sections.normalize_section_id(section_id)
        if not target_id:
            return []
        chapter_structure = catalog.get("chapter_structure", []) if isinstance(catalog.get("chapter_structure", []), list) else []

        def _find_node(nodes: List[Dict[str, Any]], needle: str) -> Optional[Dict[str, Any]]:
            for node in nodes or []:
                if not isinstance(node, dict):
                    continue
                sid = self.owner.ctd_sections.normalize_section_id(node.get("section_id"))
                if sid == needle:
                    return node
                child = _find_node(node.get("children_sections") or [], needle)
                if child is not None:
                    return child
            return None

        def _collect(node: Optional[Dict[str, Any]], out: List[str]) -> None:
            if not isinstance(node, dict):
                return
            for child in node.get("children_sections") or []:
                if not isinstance(child, dict):
                    continue
                sid = self.owner.ctd_sections.normalize_section_id(child.get("section_id"))
                if not sid:
                    continue
                if sid in self._SUPPORTED_SPLIT_ROOTS and sid not in self._MODULE3_DIRECTORY_ROOTS:
                    out.append(sid)
                _collect(child, out)

        target_node = _find_node(chapter_structure, target_id)
        collected: List[str] = []
        if target_id in self._MODULE3_DIRECTORY_ROOTS:
            _collect(target_node, collected)
        elif target_id in self._SUPPORTED_SPLIT_ROOTS:
            collected.append(target_id)
        else:
            _collect(target_node, collected)

        deduped: List[str] = []
        seen = set()
        for item in collected:
            normalized = self.owner.ctd_sections.normalize_section_id(item)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            deduped.append(normalized)
        if deduped:
            return deduped
        if target_id in self._SUPPORTED_SPLIT_ROOTS:
            return [target_id]
        return []

    def _count_root_heading_hits(self, *, raw_text: str, root_section_id: str) -> int:
        target_root = self.owner.ctd_sections.normalize_section_id(root_section_id)
        if not target_root:
            return 0
        prefix = f"{target_root}."
        count = 0
        for raw_line in self._normalize_multiline_text(raw_text).split("\n"):
            heading_info = self._extract_ctd_heading_v3(raw_line)
            section_id = self.owner.ctd_sections.normalize_section_id(heading_info.get("section_id"))
            if section_id == target_root or section_id.startswith(prefix):
                count += 1
        return count

    def _count_root_leaf_title_hits(
        self,
        *,
        raw_text: str,
        root_section_id: str,
        catalog: Dict[str, Any],
    ) -> int:
        target_root = self.owner.ctd_sections.normalize_section_id(root_section_id)
        if not target_root:
            return 0
        candidate_roots = [self.owner.ctd_sections.normalize_section_id(item) for item in self._SUPPORTED_SPLIT_ROOTS]
        token_root_map: Dict[str, set[str]] = {}
        for candidate_root in candidate_roots:
            for item in self._leaf_sections_for_root(catalog=catalog, root_section_id=candidate_root):
                token = self._normalize_section_title_token_v2(item.get("section_name"))
                if token and len(token) >= 2:
                    token_root_map.setdefault(token, set()).add(candidate_root)
        unique_tokens = {
            token
            for token, roots in token_root_map.items()
            if roots == {target_root}
        }
        if not unique_tokens:
            return 0
        count = 0
        for raw_line in self._normalize_multiline_text(raw_text).split("\n"):
            line_token = self._normalize_section_title_token_v2(raw_line)
            if not line_token:
                continue
            if any(token in line_token or line_token in token for token in unique_tokens):
                count += 1
        return count

    def resolve_single_ctd_split_root(
        self,
        *,
        file_path: str,
        requested_section_id: str,
        catalog: Dict[str, Any],
        file_type: str = "",
    ) -> str:
        requested_id = self.owner.ctd_sections.normalize_section_id(requested_section_id)
        candidate_roots = self._supported_split_roots_under(catalog=catalog, section_id=requested_id)
        if len(candidate_roots) == 1:
            return candidate_roots[0]
        if not candidate_roots and requested_id in self._SUPPORTED_SPLIT_ROOTS and requested_id not in self._MODULE3_DIRECTORY_ROOTS:
            return requested_id

        file_name = os.path.basename(str(file_path or "")).upper()
        for candidate in candidate_roots:
            if candidate.upper() in file_name:
                return candidate

        raw_text = self._load_single_ctd_source_text(file_path=file_path, file_type=file_type)
        if raw_text.strip():
            scored = [
                (
                    self._count_root_heading_hits(raw_text=raw_text, root_section_id=candidate)
                    + self._count_root_leaf_title_hits(raw_text=raw_text, root_section_id=candidate, catalog=catalog) * 2,
                    candidate,
                )
                for candidate in candidate_roots
            ]
            scored.sort(key=lambda item: item[0], reverse=True)
            if scored and scored[0][0] > 0:
                return scored[0][1]
        if candidate_roots:
            return candidate_roots[0]
        return requested_id if requested_id in self._SUPPORTED_SPLIT_ROOTS else ""

    def resolve_single_ctd_split_roots(
        self,
        *,
        file_path: str,
        requested_section_id: str,
        catalog: Dict[str, Any],
        file_type: str = "",
    ) -> List[str]:
        requested_id = self.owner.ctd_sections.normalize_section_id(requested_section_id)
        if not requested_id:
            return []

        candidate_roots = self._supported_split_roots_under(catalog=catalog, section_id=requested_id)
        if len(candidate_roots) == 1:
            return candidate_roots
        if not candidate_roots and requested_id in self._SUPPORTED_SPLIT_ROOTS and requested_id not in self._MODULE3_DIRECTORY_ROOTS:
            return [requested_id]

        raw_text = self._load_single_ctd_source_text(file_path=file_path, file_type=file_type)
        if raw_text.strip():
            scored = [
                (
                    self._count_root_heading_hits(raw_text=raw_text, root_section_id=candidate)
                    + self._count_root_leaf_title_hits(raw_text=raw_text, root_section_id=candidate, catalog=catalog) * 2,
                    candidate,
                )
                for candidate in candidate_roots
            ]
            positive = [item for item in scored if item[0] > 0]
            if positive:
                positive.sort(key=lambda item: item[0], reverse=True)
                return [candidate for _, candidate in positive]
        return list(candidate_roots)

    def merge_leaf_section_payloads(
        self,
        *,
        payloads: List[Dict[str, Any]],
        source_file: str,
        parser_mode: str,
    ) -> Dict[str, Any]:
        section_map: Dict[str, Dict[str, Any]] = {}
        review_units: List[Dict[str, Any]] = []
        seen_unit_ids = set()
        source_roots: List[str] = []
        detected_sections: List[Dict[str, Any]] = []
        seen_detected_ids = set()
        parser_warnings: List[str] = []
        parse_quality_items: List[Dict[str, Any]] = []

        def _common_section_prefix(section_ids: List[str]) -> str:
            cleaned = [self.owner.ctd_sections.normalize_section_id(item) for item in section_ids if self.owner.ctd_sections.normalize_section_id(item)]
            if not cleaned:
                return ""
            prefix = cleaned[0].split(".")
            for sid in cleaned[1:]:
                parts = sid.split(".")
                max_len = min(len(prefix), len(parts))
                idx = 0
                while idx < max_len and prefix[idx] == parts[idx]:
                    idx += 1
                prefix = prefix[:idx]
                if not prefix:
                    break
            return ".".join(prefix)

        for payload in payloads or []:
            if not isinstance(payload, dict):
                continue
            root_section_id = self.owner.ctd_sections.normalize_section_id(payload.get("root_section_id"))
            if root_section_id and root_section_id not in source_roots:
                source_roots.append(root_section_id)
            parser_warnings.extend(
                str(item or "").strip()
                for item in (payload.get("parser_warnings", []) or [])
                if str(item or "").strip()
            )
            if isinstance(payload.get("parse_quality", {}), dict):
                parse_quality_items.append(payload.get("parse_quality", {}))
            for detected in payload.get("detected_ctd_sections", []) if isinstance(payload.get("detected_ctd_sections", []), list) else []:
                if not isinstance(detected, dict):
                    continue
                detected_id = self.owner.ctd_sections.normalize_section_id(detected.get("section_id"))
                if not detected_id or not detected_id.startswith("3.2.") or detected_id in seen_detected_ids:
                    continue
                seen_detected_ids.add(detected_id)
                parts = [part for part in detected_id.split(".") if part]
                detected_sections.append(
                    {
                        "section_id": detected_id,
                        "section_name": str(detected.get("section_name", "") or "").strip(),
                        "parent_section_id": str(detected.get("parent_section_id", "") or ".".join(parts[:-1])).strip(),
                        "node_level": int(detected.get("node_level", max(0, len(parts) - 1)) or 0),
                    }
                )
            for section in payload.get("sections", []) or []:
                if not isinstance(section, dict):
                    continue
                section_id = str(section.get("section_id", "") or "").strip()
                if not section_id or section_id in section_map:
                    continue
                section_map[section_id] = section
            for unit in payload.get("review_units", []) or []:
                if not isinstance(unit, dict):
                    continue
                unit_id = str(unit.get("chunk_id", "") or unit.get("section_id", "") or "").strip()
                if not unit_id or unit_id in seen_unit_ids:
                    continue
                seen_unit_ids.add(unit_id)
                review_units.append(unit)

        sections = list(section_map.values())
        sections.sort(key=lambda item: str(item.get("section_id", "") or ""))
        review_units.sort(key=lambda item: str(item.get("section_id", "") or item.get("chunk_id", "") or ""))
        leaf_sibling_groups = self._build_leaf_sibling_groups(sections)
        root_section_id = _common_section_prefix(source_roots)
        if not root_section_id and source_roots:
            root_section_id = source_roots[0]
        quality_statuses = {str(item.get("status_code", "") or "") for item in parse_quality_items}
        parse_quality = {
            "status_code": "manual_review" if "manual_review" in quality_statuses else "warning" if ("warning" in quality_statuses or parser_warnings) else "ok",
            "parser": "ctd_single_file_multi_root",
            "detected_heading_count": sum(int(item.get("detected_heading_count", 0) or 0) for item in parse_quality_items),
            "detected_ctd_heading_count": len(detected_sections),
            "mapped_section_count": len(sections),
            "unmapped_heading_count": sum(int(item.get("unmapped_heading_count", 0) or 0) for item in parse_quality_items),
            "empty_section_count": sum(int(item.get("empty_section_count", 0) or 0) for item in parse_quality_items),
            "content_char_count": sum(int(item.get("content_char_count", 0) or 0) for item in parse_quality_items),
            "unmapped_headings": [
                detail
                for item in parse_quality_items
                for detail in (item.get("unmapped_headings", []) or [])
                if isinstance(detail, dict)
            ][:50],
            "warnings": list(dict.fromkeys(parser_warnings)),
        }
        return {
            "title": " / ".join(source_roots) or "3",
            "source_file": source_file,
            "root_section_id": root_section_id,
            "root_section_ids": source_roots,
            "parser_mode": parser_mode or "quick",
            "parser_name": "ctd_single_file_multi_root",
            "source_parser": "ctd_single_file_multi_root",
            "structure_type": "ctd_leaf_section_payload_v1",
            "chapter_structure": self.owner.ctd_sections.build_tree_from_leaf_sections(sections),
            "sections": sections,
            "leaf_sibling_groups": leaf_sibling_groups,
            "review_units": review_units,
            "detected_ctd_sections": detected_sections,
            "parse_quality": parse_quality,
            "parser_warnings": parse_quality["warnings"],
            "statistics": {
                "section_total": len(sections),
                "review_unit_total": len(review_units),
                "leaf_sibling_group_count": len(leaf_sibling_groups),
                "root_count": len(source_roots),
            },
        }

    def _segment_ctd_text_blocks(
        self,
        *,
        raw_text: str,
        allowed_section_ids: List[str],
    ) -> Dict[str, str]:
        lines = self._normalize_multiline_text(raw_text).split("\n")
        normalized_ids = {
            self.owner.ctd_sections.normalize_section_id(item): self.owner.ctd_sections.normalize_section_id(item)
            for item in allowed_section_ids or []
            if self.owner.ctd_sections.normalize_section_id(item)
        }
        headings: List[tuple[int, str]] = []
        for index, raw_line in enumerate(lines):
            heading_info = self._extract_ctd_heading_v3(raw_line)
            section_id = self._map_explicit_ctd_section_id(
                explicit_section_id=heading_info.get("section_id", ""),
                allowed_section_ids=list(normalized_ids.keys()),
            )
            if section_id in normalized_ids:
                headings.append((index, section_id))
        if not headings:
            return {}

        blocks: Dict[str, List[str]] = {}
        for pos, (line_index, section_id) in enumerate(headings):
            next_index = headings[pos + 1][0] if pos + 1 < len(headings) else len(lines)
            content = self._normalize_body_lines(lines[line_index + 1 : next_index])
            if not content:
                continue
            blocks.setdefault(section_id, []).append(content)
        return {
            section_id: self.owner._preview_text_blocks(values)
            for section_id, values in blocks.items()
            if self.owner._preview_text_blocks(values)
        }

    def _count_leaf_descendants(
        self,
        *,
        leaf_sections: List[Dict[str, Any]],
        section_scope: Dict[str, Dict[str, Any]],
    ) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for leaf in leaf_sections or []:
            if not isinstance(leaf, dict):
                continue
            current_id = str(leaf.get("section_id", "") or "").strip()
            while current_id:
                counts[current_id] = counts.get(current_id, 0) + 1
                current_meta = section_scope.get(current_id)
                if not isinstance(current_meta, dict):
                    break
                parent_id = str(current_meta.get("parent_section_id", "") or "").strip()
                if not parent_id:
                    break
                current_id = parent_id
        return counts

    def _resolve_leaf_content_text(
        self,
        *,
        section_id: str,
        root_section_id: str,
        section_scope: Dict[str, Dict[str, Any]],
        blocks_by_section: Dict[str, Any],
        leaf_descendant_counts: Dict[str, int],
    ) -> str:
        direct_value = blocks_by_section.get(section_id, [])
        if isinstance(direct_value, list):
            direct_text = self._preview_text_blocks(direct_value)
        else:
            direct_text = str(direct_value or "").strip()
        if direct_text:
            return direct_text

        root_key = self.owner.ctd_sections.normalize_section_id(root_section_id)
        current_id = str(section_id or "").strip()
        while current_id:
            current_meta = section_scope.get(current_id)
            if not isinstance(current_meta, dict):
                break
            parent_id = str(current_meta.get("parent_section_id", "") or "").strip()
            if not parent_id or parent_id == root_key:
                break
            if leaf_descendant_counts.get(parent_id, 0) == 1:
                parent_value = blocks_by_section.get(parent_id, [])
                if isinstance(parent_value, list):
                    parent_text = self._preview_text_blocks(parent_value)
                else:
                    parent_text = str(parent_value or "").strip()
                if parent_text:
                    return parent_text
            current_id = parent_id
        return ""

    def _build_leaf_sibling_groups(self, sections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        groups: List[Dict[str, Any]] = []
        by_parent: Dict[str, List[Dict[str, Any]]] = {}
        for item in sections or []:
            if not isinstance(item, dict):
                continue
            parent_section_id = str(item.get("parent_section_id", "") or "").strip()
            if not parent_section_id:
                continue
            by_parent.setdefault(parent_section_id, []).append(item)

        for index, (parent_section_id, children) in enumerate(by_parent.items(), start=1):
            if len(children) < 2:
                continue
            parent_meta = self.owner.ctd_sections.get_section(parent_section_id, leaf_only=False) or {}
            content_blocks: List[str] = []
            total_chars = 0
            child_codes: List[str] = []
            for child in children:
                section_code = str(child.get("section_code", "") or child.get("section_id", "") or "").strip()
                section_name = str(child.get("section_name", "") or section_code).strip() or section_code
                content = str(child.get("content", "") or "").strip()
                child_codes.append(section_code)
                total_chars += len(content)
                block = f"## {section_code} {section_name}".strip()
                if content:
                    block = f"{block}\n{content}"
                content_blocks.append(block)
            merged_content = self.owner._preview_text_blocks(content_blocks)
            groups.append(
                {
                    "group_id": f"group_{index}",
                    "parent_code": parent_section_id,
                    "parent_title": str(parent_meta.get("section_name", "") or parent_section_id).strip() or parent_section_id,
                    "parent_section_id": parent_section_id,
                    "child_count": len(children),
                    "child_codes": child_codes,
                    "page_start": None,
                    "page_end": None,
                    "char_count": total_chars,
                    "content": merged_content,
                    "content_preview": self.owner._preview(merged_content, 320),
                }
            )
        return groups

    def _build_single_ctd_text_payload(
        self,
        *,
        file_path: str,
        file_type: str,
        root_section_id: str,
        catalog: Dict[str, Any],
    ) -> Dict[str, Any]:
        normalized_file_type = self._normalize_file_type(file_path, file_type)
        source_text = self._load_single_ctd_source_text(file_path=file_path, file_type=normalized_file_type)
        if not source_text.strip():
            return {}
        detected_sections = self._collect_detected_ctd_sections_from_text(source_text)

        section_scope = self._catalog_section_maps_for_root(catalog=catalog, root_section_id=root_section_id)
        if not section_scope:
            return {}
        root_key = self.owner.ctd_sections.normalize_section_id(root_section_id)
        leaf_sections = self._leaf_sections_for_root(catalog=catalog, root_section_id=root_section_id)
        if not leaf_sections:
            return {}
        leaf_descendant_counts = self._count_leaf_descendants(leaf_sections=leaf_sections, section_scope=section_scope)

        section_blocks = self._segment_ctd_text_blocks(
            raw_text=source_text,
            allowed_section_ids=[str(item.get("section_id", "") or "").strip() for item in leaf_sections],
        )
        if not section_blocks:
            return {}

        sections: List[Dict[str, Any]] = []
        review_units: List[Dict[str, Any]] = []
        for order, leaf in enumerate(leaf_sections, start=1):
            section_id = str(leaf.get("section_id", "") or "").strip()
            if not section_id:
                continue
            merged_text = self._resolve_leaf_content_text(
                section_id=section_id,
                root_section_id=root_key,
                section_scope=section_scope,
                blocks_by_section=section_blocks,
                leaf_descendant_counts=leaf_descendant_counts,
            )
            if not merged_text:
                continue
            section_name = str(leaf.get("section_name", "") or section_id).strip() or section_id
            title_path = list(leaf.get("title_path") or [section_name])
            parent_section_id = str(leaf.get("parent_section_id", "") or "").strip()
            section_item = {
                "section_id": section_id,
                "section_code": str(leaf.get("section_code", "") or section_id).strip() or section_id,
                "section_name": section_name,
                "title_path": title_path,
                "parent_section_id": parent_section_id,
                "page_start": None,
                "page_end": None,
                "content": merged_text,
                "content_preview": self.owner._preview(merged_text, 320),
                "char_count": len(merged_text),
                "tables": [],
                "children_sections": [],
            }
            sections.append(section_item)
            review_units.append(
                {
                    "chunk_id": section_id,
                    "section_id": section_id,
                    "section_code": str(leaf.get("section_code", "") or section_id).strip() or section_id,
                    "section_name": section_name,
                    "parent_section_id": parent_section_id,
                    "page": None,
                    "page_start": None,
                    "page_end": None,
                    "text": merged_text,
                    "title_path": title_path,
                    "char_count": len(merged_text),
                    "unit_order": order,
                    "unit_type": f"ctd_text_leaf_section::{normalized_file_type or 'text'}",
                    "pipeline": "ctd_single_file_text_split",
                }
            )

        if not sections:
            return {}

        leaf_sibling_groups = self._build_leaf_sibling_groups(sections)
        return {
            "title": str(root_section_id or "").strip(),
            "source_file": file_path,
            "root_section_id": root_key,
            "parser_mode": "quick",
            "parser_name": f"ctd_single_file_text::{normalized_file_type or 'text'}",
            "structure_type": "ctd_leaf_section_payload_v1",
            "chapter_structure": self.owner.ctd_sections.build_tree_from_leaf_sections(sections),
            "sections": sections,
            "leaf_sibling_groups": leaf_sibling_groups,
            "review_units": review_units,
            "statistics": {
                "section_total": len(sections),
                "review_unit_total": len(review_units),
                "leaf_sibling_group_count": len(leaf_sibling_groups),
            },
            "detected_ctd_sections": detected_sections,
        }

    def parse_single_ctd_file(
        self,
        *,
        file_path: str,
        root_section_id: str,
        catalog: Dict[str, Any],
        file_type: str = "",
    ) -> Dict[str, Any]:
        normalized_file_type = self._normalize_file_type(file_path, file_type)
        if normalized_file_type == "pdf":
            _, parse_ctd_submission_to_payload, _ = _load_quick_ctd_parsers()
            return parse_ctd_submission_to_payload(
                file_path=file_path,
                catalog=catalog,
                root_section_id=root_section_id,
            )
        if normalized_file_type in {"doc", "docx"}:
            payload = self._build_single_ctd_docx_payload(
                file_path=file_path,
                root_section_id=root_section_id,
                catalog=catalog,
            )
            if isinstance(payload, dict) and isinstance(payload.get("review_units", []), list) and payload.get("review_units"):
                return payload

        payload = self._build_single_ctd_text_payload(
            file_path=file_path,
            file_type=normalized_file_type,
            root_section_id=root_section_id,
            catalog=catalog,
        )
        if isinstance(payload, dict) and isinstance(payload.get("review_units", []), list) and payload.get("review_units"):
            return payload

        section_map = catalog.get("section_map", {}) if isinstance(catalog.get("section_map", {}), dict) else {}
        section_meta = section_map.get(root_section_id, {}) if isinstance(section_map.get(root_section_id, {}), dict) else {}
        return self.parse_bound_section_file(
            file_path=file_path,
            file_type=normalized_file_type,
            section_meta=section_meta or {
                "section_id": root_section_id,
                "section_name": root_section_id,
                "parent_section_id": "",
            },
        )

    def parse_single_ctd_file_detailed(
        self,
        *,
        file_path: str,
        root_section_id: str,
        catalog: Dict[str, Any],
        file_type: str = "",
    ) -> Dict[str, Any]:
        normalized_file_type = self._normalize_file_type(file_path, file_type)
        if normalized_file_type != "pdf":
            return self.parse_single_ctd_file(
                file_path=file_path,
                root_section_id=root_section_id,
                catalog=catalog,
                file_type=normalized_file_type,
            )

        _, parse_ctd_submission_to_payload, _ = _load_quick_ctd_parsers()
        try:
            payload = parse_ctd_submission_with_deepseek_ocr_to_payload(
                file_path=file_path,
                catalog=catalog,
                root_section_id=root_section_id,
            )
            units = payload.get("review_units", []) if isinstance(payload, dict) else []
            if isinstance(units, list) and units:
                return payload
        except Exception:
            pass
        return parse_ctd_submission_to_payload(
            file_path=file_path,
            catalog=catalog,
            root_section_id=root_section_id,
        )

    def parse_bound_section_file(
        self,
        *,
        file_path: str,
        file_type: str,
        section_meta: Dict[str, Any],
    ) -> Dict[str, Any]:
        section_id = str(section_meta.get("section_id", "") or "").strip()
        section_name = str(section_meta.get("section_name", "") or section_id).strip() or section_id
        parent_section_id = str(section_meta.get("parent_section_id", "") or "").strip()
        normalized_file_type = str(file_type or "").strip().lower()
        parse_bound_section_pdf_to_payload, _, parse_submission_pdf_to_payload = _load_quick_ctd_parsers()
        if normalized_file_type == "pdf":
            return parse_bound_section_pdf_to_payload(
                file_path=file_path,
                section_id=section_id,
                section_name=section_name,
                parent_section_id=parent_section_id,
            )
        if normalized_file_type in {"doc", "docx"}:
            return self._build_bound_docx_payload(
                file_path=file_path,
                section_meta=section_meta,
            )

        if ParserManager.is_supported(normalized_file_type):
            units = ParserManager.parse(file_path)
            merged_text = self.owner._preview_text_blocks(
                [str(item.get("text", "")).strip() for item in units if isinstance(item, dict)]
            )
            return {
                "title": section_name,
                "source_file": file_path,
                "structure_type": "bound_section_file_payload_v1",
                "sections": [
                    {
                        "section_id": section_id,
                        "section_code": section_id,
                        "section_name": section_name,
                        "title_path": [section_name],
                        "parent_section_id": parent_section_id,
                        "content": merged_text,
                        "content_preview": self.owner._preview(merged_text, 320),
                        "char_count": len(merged_text),
                        "page_start": None,
                        "page_end": None,
                    }
                ],
                "review_units": [
                    {
                        "chunk_id": section_id,
                        "section_id": section_id,
                        "section_code": section_id,
                        "section_name": section_name,
                        "parent_section_id": parent_section_id,
                        "page": None,
                        "page_start": None,
                        "page_end": None,
                        "text": merged_text,
                        "title_path": [section_name],
                        "unit_order": 1,
                        "unit_type": "bound_section_content",
                        "char_count": len(merged_text),
                    }
                ],
                "statistics": {
                    "section_total": 1 if merged_text else 0,
                    "review_unit_total": 1 if merged_text else 0,
                },
            }

        return parse_submission_pdf_to_payload(file_path=file_path, title=section_name)

    def parse_bound_section_file_detailed(
        self,
        *,
        file_path: str,
        file_type: str,
        section_meta: Dict[str, Any],
    ) -> Dict[str, Any]:
        section_id = str(section_meta.get("section_id", "") or "").strip()
        section_name = str(section_meta.get("section_name", "") or section_id).strip() or section_id
        parent_section_id = str(section_meta.get("parent_section_id", "") or "").strip()
        normalized_file_type = str(file_type or "").strip().lower()
        if normalized_file_type == "pdf":
            payload = parse_bound_section_with_deepseek_ocr_to_payload(
                file_path=file_path,
                section_id=section_id,
                section_name=section_name,
                parent_section_id=parent_section_id,
            )
            units = payload.get("review_units", []) if isinstance(payload, dict) else []
            if isinstance(units, list) and units:
                return payload
            raise ValueError(
                f"deepseek ocr detailed parse returned empty result for bound section: {section_id or section_name}"
            )
        return self.parse_bound_section_file(
            file_path=file_path,
            file_type=file_type,
            section_meta=section_meta,
        )
