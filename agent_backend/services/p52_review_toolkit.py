from __future__ import annotations

import uuid
from typing import Any, Dict, List, TYPE_CHECKING

if TYPE_CHECKING:
    from agent.agent_backend.services.pre_review_service import PreReviewService


class P52ReviewToolkit:
    """为 3.2.P.5.2 规则判别链路提供跨章节与知识库访问能力。"""

    def __init__(self, service: "PreReviewService") -> None:
        self.service = service

    @staticmethod
    def _normalize_section_id(value: Any) -> str:
        return str(value or "").strip().lower()

    def load_submission_section_map(self, project_id: str, source_doc_id: str) -> Dict[str, Dict[str, Any]]:
        ok, _, payload = self.service.get_submission_sections(project_id=project_id, doc_id=source_doc_id)
        if not ok or not isinstance(payload, dict):
            return {}
        out: Dict[str, Dict[str, Any]] = {}
        sections = payload.get("sections", []) if isinstance(payload.get("sections", []), list) else []
        for item in sections:
            if not isinstance(item, dict):
                continue
            sid = self._normalize_section_id(item.get("section_id", ""))
            if not sid:
                continue
            out[sid] = dict(item)
        return out

    def get_section_content(self, section_map: Dict[str, Dict[str, Any]], section_id: str) -> Dict[str, Any]:
        sid = self._normalize_section_id(section_id)
        item = dict(section_map.get(sid, {}))
        item["section_id"] = sid
        item["content"] = str(
            item.get("content", "")
            or item.get("raw_content", "")
            or item.get("cleaned_markdown", "")
            or item.get("display_content", "")
            or ""
        ).strip()
        return item

    def get_quality_standard_section(self, section_map: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        for section_id in ["3.2.p.5.1", "3.2.p.5.1.1"]:
            payload = self.get_section_content(section_map, section_id)
            if str(payload.get("content", "") or "").strip():
                return payload
        related = self.get_related_sections(section_map, ["3.2.p.5.1", "3.2.p.5.1.*"], limit=1)
        return dict(related[0]) if related else {}

    def get_related_sections(
        self,
        section_map: Dict[str, Dict[str, Any]],
        patterns: List[str],
        *,
        exclude_section_id: str = "",
        limit: int = 8,
    ) -> List[Dict[str, Any]]:
        normalized_patterns = [self._normalize_section_id(item) for item in patterns if str(item or "").strip()]
        excluded = self._normalize_section_id(exclude_section_id)
        out: List[Dict[str, Any]] = []
        for sid, item in section_map.items():
            if not sid or sid == excluded:
                continue
            if not any(self._match_pattern(sid, pattern) for pattern in normalized_patterns):
                continue
            payload = self.get_section_content(section_map, sid)
            if not payload.get("content"):
                continue
            out.append(payload)
            if len(out) >= max(int(limit or 0), 1):
                break
        return out

    @staticmethod
    def _match_pattern(section_id: str, pattern: str) -> bool:
        sid = str(section_id or "").strip().lower()
        raw_pattern = str(pattern or "").strip().lower()
        if not sid or not raw_pattern:
            return False
        if raw_pattern.endswith(".*"):
            prefix = raw_pattern[:-2]
            return sid == prefix or sid.startswith(f"{prefix}.")
        return sid == raw_pattern or sid.startswith(f"{raw_pattern}.")

    @staticmethod
    def find_keyword_evidence(text: str, keywords: List[str], *, window: int = 60) -> str:
        content = str(text or "")
        lowered = content.lower()
        for keyword in keywords or []:
            raw_keyword = str(keyword or "").strip()
            if not raw_keyword:
                continue
            index = lowered.find(raw_keyword.lower())
            if index < 0:
                continue
            start = max(index - window, 0)
            end = min(index + len(raw_keyword) + window, len(content))
            return content[start:end].strip()
        return ""

    def search_knowledge_base(self, query: str, *, top_k: int = 3) -> List[Dict[str, Any]]:
        text = str(query or "").strip()
        if not text:
            return []
        try:
            result = self.service.knowledge.semantic_query(query=text, top_k=max(int(top_k or 0), 1), min_score=0.1)
        except Exception:
            return []
        hits = result.get("hits", []) if isinstance(result, dict) and isinstance(result.get("hits", []), list) else []
        out: List[Dict[str, Any]] = []
        for item in hits[: max(int(top_k or 0), 1)]:
            if not isinstance(item, dict):
                continue
            out.append(
                {
                    "doc_id": str(item.get("doc_id", "") or "").strip(),
                    "doc_title": str(item.get("doc_title", "") or item.get("file_name", "") or "").strip(),
                    "section_id": str(item.get("section_id", "") or "").strip(),
                    "section_name": str(item.get("section_name", "") or "").strip(),
                    "score": float(item.get("score", 0.0) or 0.0),
                    "content": str(item.get("content", "") or "").strip(),
                }
            )
        return out

    def search_submission_by_keywords(
        self,
        section_map: Dict[str, Dict[str, Any]],
        *,
        keywords: List[str],
        section_patterns: List[str] | None = None,
        exclude_section_id: str = "",
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        excluded = self._normalize_section_id(exclude_section_id)
        normalized_keywords = [str(item or "").strip() for item in keywords if str(item or "").strip()]
        normalized_patterns = [self._normalize_section_id(item) for item in section_patterns if str(item or "").strip()] if isinstance(section_patterns, list) else []
        out: List[Dict[str, Any]] = []
        for sid, item in section_map.items():
            if not sid or sid == excluded:
                continue
            if normalized_patterns and not any(self._match_pattern(sid, pattern) for pattern in normalized_patterns):
                continue
            payload = self.get_section_content(section_map, sid)
            content = str(payload.get("content", "") or "").strip()
            if not content:
                continue
            excerpt = self.find_keyword_evidence(content, normalized_keywords, window=80)
            if not excerpt:
                continue
            out.append(
                {
                    "section_id": sid,
                    "section_name": str(payload.get("section_name", "") or "").strip(),
                    "excerpt": excerpt,
                    "content": content,
                }
            )
            if len(out) >= max(int(limit or 0), 1):
                break
        return out

    @staticmethod
    def build_section_material(
        *,
        source_type: str,
        title: str,
        content: str,
        evidence_id: str = "",
        section_id: str = "",
        section_name: str = "",
        score: float = 0.88,
    ) -> Dict[str, Any]:
        normalized_content = str(content or "").strip()
        normalized_title = str(title or "").strip()
        normalized_evidence_id = str(evidence_id or "").strip() or f"ev_{uuid.uuid4().hex[:12]}"
        return {
            "evidence_id": normalized_evidence_id,
            "source_type": str(source_type or "").strip() or "CTD申报资料",
            "title": normalized_title or normalized_evidence_id,
            "content": normalized_content,
            "section_id": str(section_id or "").strip(),
            "section_name": str(section_name or "").strip(),
            "display_name": normalized_title or normalized_evidence_id,
            "doc_title": normalized_title or normalized_evidence_id,
            "comprehensive_score": float(score or 0.0),
        }

    def build_knowledge_material(self, hit: Dict[str, Any], *, score_floor: float = 0.55) -> Dict[str, Any]:
        return self.build_section_material(
            source_type="知识库",
            title=str(hit.get("doc_title", "") or "知识库资料").strip() or "知识库资料",
            content=str(hit.get("content", "") or "").strip(),
            evidence_id=f"kb:{str(hit.get('doc_id', '') or '').strip() or uuid.uuid4().hex[:8]}",
            section_id=str(hit.get("section_id", "") or "").strip(),
            section_name=str(hit.get("section_name", "") or "").strip(),
            score=max(float(hit.get("score", 0.0) or 0.0), float(score_floor)),
        )

    @staticmethod
    def dedupe_materials(materials: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        seen = set()
        for item in materials if isinstance(materials, list) else []:
            if not isinstance(item, dict):
                continue
            evidence_id = str(item.get("evidence_id", "") or "").strip()
            title = str(item.get("title", "") or "").strip()
            content = str(item.get("content", "") or "").strip()
            dedupe_key = evidence_id or f"{title}::{content[:120]}"
            if not content or dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            out.append(dict(item))
        return out
