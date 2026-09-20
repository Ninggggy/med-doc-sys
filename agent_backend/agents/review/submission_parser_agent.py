from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from agent.agent_backend.llm.client import LLMClient
from agent.agent_backend.prompts.template_manager import PromptTemplateManager


class SubmissionParserAgent:
    """Map submission materials to strict CTD sections during upload."""

    SECTION_CODE_RE = re.compile(r"3\.2\.[a-z](?:\.\d+){1,3}", re.IGNORECASE)

    def __init__(self) -> None:
        self.llm = LLMClient()
        self.prompts = PromptTemplateManager("pre_review_agent_prompt")

    def describe(self) -> Dict[str, Any]:
        return {
            "name": "submission_parser",
            "description": "Map one submission file into the nearest legal CTD section for pre-review uploads.",
            "inputs": ["display_name", "relative_path", "parsed_sections", "catalog"],
            "outputs": ["raw_detected_section_id", "mapped_section_id", "confidence", "reason"],
        }

    @staticmethod
    def _nearest_legal_section(section_id: str, valid_ids: set[str]) -> str:
        current = str(section_id or "").strip()
        while current:
            if current in valid_ids:
                return current
            if "." not in current:
                break
            current = current.rsplit(".", 1)[0]
        return ""

    def _collect_candidate_codes(
        self,
        display_name: str,
        relative_path: str,
        parsed_payload: Optional[Dict[str, Any]],
    ) -> List[str]:
        candidates: List[str] = []
        text_sources = [str(relative_path or ""), str(display_name or "")]
        for source in text_sources:
            candidates.extend(self.SECTION_CODE_RE.findall(source))
        if isinstance(parsed_payload, dict):
            for section in parsed_payload.get("sections", []) or []:
                if not isinstance(section, dict):
                    continue
                code = str(section.get("code", "") or "").strip()
                if code:
                    candidates.append(code)
                heading = str(section.get("heading", "") or "").strip()
                if heading:
                    candidates.extend(self.SECTION_CODE_RE.findall(heading))
                raw_heading = str(section.get("raw_heading_line", "") or "").strip()
                if raw_heading:
                    candidates.extend(self.SECTION_CODE_RE.findall(raw_heading))
        seen = set()
        out: List[str] = []
        for item in candidates:
            normalized = str(item or "").strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            out.append(normalized)
        out.sort(key=len, reverse=True)
        return out

    def _llm_map_section(
        self,
        display_name: str,
        relative_path: str,
        parsed_payload: Optional[Dict[str, Any]],
        catalog: Dict[str, Any],
    ) -> Dict[str, Any]:
        flat_sections = catalog.get("flat_sections", []) if isinstance(catalog, dict) else []
        legal_sections = [
            {
                "section_id": str(item.get("section_id", "")).strip(),
                "section_name": str(item.get("section_name", "")).strip(),
            }
            for item in flat_sections
            if isinstance(item, dict) and str(item.get("section_id", "")).strip()
        ]
        parsed_sections = []
        if isinstance(parsed_payload, dict):
            for item in (parsed_payload.get("sections", []) or [])[:12]:
                if not isinstance(item, dict):
                    continue
                parsed_sections.append(
                    {
                        "code": str(item.get("code", "") or "").strip(),
                        "title": str(item.get("title", "") or "").strip(),
                        "heading": str(item.get("heading", "") or "").strip(),
                    }
                )
        prompt = self.prompts.render(
            "submission_parser.j2",
            {
                "display_name": str(display_name or ""),
                "relative_path": str(relative_path or ""),
                "parsed_sections": parsed_sections,
                "legal_sections": legal_sections[:80],
            },
        )
        default = '{"raw_detected_section_id": "", "mapped_section_id": "", "confidence": 0.0, "reason": ""}'
        raw = self.llm.chat(
            messages=[
                {"role": "system", "content": "你是 CTD Module 3 申报资料目录解析器。只输出合法 JSON。"},
                {"role": "user", "content": prompt},
            ],
            default=default,
        )
        data = self.llm.extract_json(raw)
        return data if isinstance(data, dict) else {}

    def map_file(
        self,
        display_name: str,
        relative_path: str,
        parsed_payload: Optional[Dict[str, Any]],
        catalog: Dict[str, Any],
        explicit_section_id: str = "",
    ) -> Dict[str, Any]:
        section_map = catalog.get("section_map", {}) if isinstance(catalog, dict) else {}
        valid_ids = {str(k).strip() for k in section_map.keys() if str(k).strip()}
        explicit = str(explicit_section_id or "").strip()
        if explicit:
            mapped = self._nearest_legal_section(explicit, valid_ids)
            if mapped:
                return {
                    "file_name": display_name,
                    "relative_path": relative_path,
                    "raw_detected_section_id": explicit,
                    "mapped_section_id": mapped,
                    "confidence": 1.0 if mapped == explicit else 0.98,
                    "reason": "explicit_section_id",
                    "section_meta": section_map.get(mapped, {}),
                }

        candidates = self._collect_candidate_codes(display_name, relative_path, parsed_payload)
        for code in candidates:
            mapped = self._nearest_legal_section(code, valid_ids)
            if mapped:
                confidence = 0.96 if mapped == code else 0.9
                return {
                    "file_name": display_name,
                    "relative_path": relative_path,
                    "raw_detected_section_id": code,
                    "mapped_section_id": mapped,
                    "confidence": confidence,
                    "reason": "detected_from_path_or_headings",
                    "section_meta": section_map.get(mapped, {}),
                }

        llm_result = self._llm_map_section(display_name, relative_path, parsed_payload, catalog)
        raw_detected = str(llm_result.get("raw_detected_section_id", "") or "").strip()
        mapped = self._nearest_legal_section(str(llm_result.get("mapped_section_id", "") or raw_detected), valid_ids)
        if mapped:
            try:
                confidence = float(llm_result.get("confidence", 0.72) or 0.72)
            except Exception:
                confidence = 0.72
            return {
                "file_name": display_name,
                "relative_path": relative_path,
                "raw_detected_section_id": raw_detected,
                "mapped_section_id": mapped,
                "confidence": max(0.5, min(confidence, 0.89)),
                "reason": str(llm_result.get("reason", "") or "llm_inference").strip() or "llm_inference",
                "section_meta": section_map.get(mapped, {}),
            }

        return {
            "file_name": display_name,
            "relative_path": relative_path,
            "raw_detected_section_id": "",
            "mapped_section_id": "",
            "confidence": 0.0,
            "reason": "unmapped",
            "section_meta": {},
        }
