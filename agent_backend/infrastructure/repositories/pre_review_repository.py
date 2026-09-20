from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List

from agent.agent_backend.config.settings import settings
from agent.agent_backend.database.mysql.db_model import PreReviewSectionConclusion, PreReviewSectionTrace
from agent.agent_backend.utils.file_util import ensure_dir_exists


@dataclass
class SectionConclusionRecord:
    """Structured section conclusion payload used by repository adapters."""

    run_id: str
    section_id: str
    section_name: str
    conclusion: str
    highlighted_issues: List[Dict[str, Any]] = field(default_factory=list)
    linked_rules: List[str] = field(default_factory=list)
    risk_level: str = "low"
    create_time: datetime | None = None

    def to_entity(self) -> PreReviewSectionConclusion:
        """Build ORM entity for persistence."""
        return PreReviewSectionConclusion(
            run_id=self.run_id,
            section_id=self.section_id,
            section_name=self.section_name,
            conclusion=self.conclusion,
            highlighted_issues=json.dumps(self.highlighted_issues, ensure_ascii=False),
            linked_rules=json.dumps(self.linked_rules, ensure_ascii=False),
            risk_level=self.risk_level,
            create_time=self.create_time or datetime.now(),
        )

    @classmethod
    def from_entity(cls, entity: PreReviewSectionConclusion) -> "SectionConclusionRecord":
        """Convert ORM entity back to structured payload."""
        return cls(
            run_id=entity.run_id,
            section_id=entity.section_id,
            section_name=entity.section_name,
            conclusion=entity.conclusion,
            highlighted_issues=PreReviewRepository.load_findings(entity.highlighted_issues),
            linked_rules=PreReviewRepository.load_string_list(entity.linked_rules),
            risk_level=entity.risk_level or "low",
            create_time=entity.create_time,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize record for service/controller responses."""
        return {
            "run_id": self.run_id,
            "section_id": self.section_id,
            "section_name": self.section_name,
            "conclusion": self.conclusion,
            "highlighted_issues": list(self.highlighted_issues),
            "linked_rules": list(self.linked_rules),
            "risk_level": self.risk_level,
            "create_time": self.create_time.strftime("%Y-%m-%d %H:%M:%S") if self.create_time else "",
        }


@dataclass
class SectionTraceRecord:
    """Structured section trace payload used by repository adapters."""

    run_id: str
    section_id: str
    trace_payload: Dict[str, Any]
    create_time: datetime | None = None

    def to_entity(self) -> PreReviewSectionTrace:
        """Build ORM entity for persistence."""
        return PreReviewSectionTrace(
            run_id=self.run_id,
            section_id=self.section_id,
            trace_json=PreReviewRepository.dump_trace_payload(self.trace_payload),
            create_time=self.create_time or datetime.now(),
        )

    @classmethod
    def from_entity(cls, entity: PreReviewSectionTrace) -> "SectionTraceRecord":
        """Convert ORM entity back to structured trace payload."""
        return cls(
            run_id=entity.run_id,
            section_id=entity.section_id,
            trace_payload=PreReviewRepository.load_trace_payload(entity.trace_json),
            create_time=entity.create_time,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize trace record for responses."""
        payload = dict(self.trace_payload)
        payload.update(
            {
                "run_id": self.run_id,
                "section_id": self.section_id,
                "create_time": self.create_time.strftime("%Y-%m-%d %H:%M:%S") if self.create_time else "",
            }
        )
        return payload


class PreReviewRepository:
    """Structured serializer/deserializer for pre-review conclusions and traces."""

    TRACE_TEXT_LIMIT = 1200
    TRACE_JSON_LIMIT = 60000
    TRACE_JSON_BYTE_LIMIT = 18000
    TRACE_ARTIFACT_DIR = Path(settings.feedback_asset_dir) / "pre_review_trace"

    @staticmethod
    def normalize_finding(item: Any) -> Dict[str, Any]:
        """Normalize legacy string or dict finding to a structured dict."""
        if isinstance(item, dict):
            title = str(item.get("title", "") or "").strip()
            if not title:
                return {}
            return {
                "title": title,
                "problem_type": str(item.get("problem_type", "") or "").strip(),
                "severity": str(item.get("severity", "low") or "low").strip().lower(),
                "evidence": str(item.get("evidence", "") or "").strip(),
                "recommendation": str(item.get("recommendation", "") or "").strip(),
                "metadata": item.get("metadata", {}) if isinstance(item.get("metadata", {}), dict) else {},
            }
        title = str(item or "").strip()
        if not title:
            return {}
        return {
            "title": title,
            "problem_type": "",
            "severity": "low",
            "evidence": "",
            "recommendation": "",
            "metadata": {},
        }

    @classmethod
    def load_findings(cls, raw: str | None) -> List[Dict[str, Any]]:
        """Load structured findings from JSON text."""
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except Exception:
            return []
        findings: List[Dict[str, Any]] = []
        for item in data if isinstance(data, list) else []:
            normalized = cls.normalize_finding(item)
            if normalized:
                findings.append(normalized)
        return findings

    @staticmethod
    def load_string_list(raw: str | None) -> List[str]:
        """Load a list of strings from JSON text."""
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except Exception:
            return []
        return [str(item) for item in data] if isinstance(data, list) else []

    @classmethod
    def dump_findings(cls, findings: List[Any]) -> str:
        """Dump structured findings to JSON text."""
        normalized = []
        for item in findings or []:
            payload = cls.normalize_finding(item)
            if payload:
                normalized.append(payload)
        return json.dumps(normalized, ensure_ascii=False)

    @classmethod
    def _truncate_text(cls, value: Any, limit: int | None = None) -> str:
        text = str(value or "")
        max_len = int(limit or cls.TRACE_TEXT_LIMIT)
        if len(text) <= max_len:
            return text
        return f"{text[:max_len]}...(truncated)"

    @staticmethod
    def _json_size_bytes(payload: Any) -> int:
        try:
            return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        except Exception:
            return 0

    @staticmethod
    def _safe_name(value: Any, fallback: str) -> str:
        text = str(value or "").strip()
        if not text:
            return fallback
        safe = re.sub(r"[^0-9A-Za-z._-]+", "_", text)
        return safe[:80] or fallback

    @classmethod
    def _extract_trace_identity(cls, payload: Dict[str, Any]) -> Dict[str, str]:
        section_packet = payload.get("section_packet", {}) if isinstance(payload.get("section_packet"), dict) else {}
        trace = payload.get("trace", {}) if isinstance(payload.get("trace"), dict) else {}
        return {
            "run_id": cls._safe_name(
                trace.get("run_id", "")
                or payload.get("run_id", "")
                or payload.get("section_review_packet", {}).get("run_id", "") if isinstance(payload.get("section_review_packet", {}), dict) else "",
                "run",
            ),
            "section_id": cls._safe_name(
                section_packet.get("section_id", "")
                or payload.get("section_id", ""),
                "section",
            ),
        }

    @classmethod
    def _persist_raw_trace_artifact(cls, payload: Dict[str, Any]) -> Dict[str, Any]:
        ensure_dir_exists(str(cls.TRACE_ARTIFACT_DIR))
        identity = cls._extract_trace_identity(payload if isinstance(payload, dict) else {})
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        file_name = f"{identity['run_id']}__{identity['section_id']}__{timestamp}.json"
        file_path = cls.TRACE_ARTIFACT_DIR / file_name
        encoded = json.dumps(payload, ensure_ascii=False, indent=2)
        file_path.write_text(encoded, encoding="utf-8")
        return {
            "persisted": True,
            "file_name": file_name,
            "file_path": str(file_path),
            "raw_size": len(encoded),
        }

    @classmethod
    def _trim_paragraph_blocks(cls, blocks: Any) -> List[Dict[str, Any]]:
        trimmed: List[Dict[str, Any]] = []
        if not isinstance(blocks, list):
            return trimmed
        for item in blocks[:12]:
            if not isinstance(item, dict):
                continue
            trimmed.append(
                {
                    "anchor_id": str(item.get("anchor_id", "") or "").strip(),
                    "anchor_label": str(item.get("anchor_label", "") or "").strip(),
                    "paragraph_index": item.get("paragraph_index"),
                    "text_preview": cls._truncate_text(item.get("text", ""), 180),
                    "span_start": item.get("span_start"),
                    "span_end": item.get("span_end"),
                }
            )
        return trimmed

    @classmethod
    def _trim_retrieved_materials(cls, items: Any, keep_count: int = 8) -> List[Dict[str, Any]]:
        trimmed: List[Dict[str, Any]] = []
        if not isinstance(items, list):
            return trimmed
        for item in items[:keep_count]:
            if not isinstance(item, dict):
                continue
            trimmed.append(
                {
                    "evidence_id": str(item.get("evidence_id", "") or "").strip(),
                    "source_type": str(item.get("source_type", "") or item.get("classification", "") or "").strip(),
                    "title": str(item.get("title", "") or item.get("doc_title", "") or "").strip(),
                    "doc_id": str(item.get("doc_id", "") or "").strip(),
                    "chunk_id": str(item.get("chunk_id", "") or "").strip(),
                    "file_name": str(item.get("file_name", "") or item.get("file_name_zh", "") or "").strip(),
                    "classification": str(item.get("classification", "") or "").strip(),
                    "score": item.get("score"),
                    "vector_score": item.get("vector_score"),
                    "lexical_score": item.get("lexical_score"),
                    "content_preview": cls._truncate_text(item.get("content", ""), 240),
                    "summary": cls._truncate_text(item.get("summary", ""), 180),
                    "keywords": list(item.get("keywords", [])[:8]) if isinstance(item.get("keywords", []), list) else [],
                }
            )
        return trimmed

    @classmethod
    def _trim_rule_rows(cls, rows: Any) -> List[Dict[str, Any]]:
        trimmed: List[Dict[str, Any]] = []
        if not isinstance(rows, list):
            return trimmed
        for item in rows[:20]:
            if not isinstance(item, dict):
                continue
            trimmed.append(
                {
                    "rule_id": str(item.get("rule_id", "") or "").strip(),
                    "rule_code": str(item.get("rule_code", "") or "").strip(),
                    "rule_name": str(item.get("rule_name", "") or "").strip(),
                    "template_name": str(item.get("template_name", "") or "").strip(),
                    "priority": item.get("priority"),
                    "scope_type": str(item.get("scope_type", "") or "").strip(),
                    "source_type": str(item.get("source_type", "") or "").strip(),
                }
            )
        return trimmed

    @classmethod
    def _trim_trace_payload(cls, payload: Dict[str, Any]) -> Dict[str, Any]:
        data = copy.deepcopy(payload if isinstance(payload, dict) else {})
        section_packet = data.get("section_packet", {})
        if isinstance(section_packet, dict):
            section_packet["raw_text"] = cls._truncate_text(section_packet.get("raw_text", ""), 1200)
            section_packet["paragraph_blocks"] = cls._trim_paragraph_blocks(section_packet.get("paragraph_blocks", []))
            section_packet["title_path"] = list(section_packet.get("title_path", [])[:8]) if isinstance(section_packet.get("title_path", []), list) else []
            section_packet["focus_points"] = list(section_packet.get("focus_points", [])[:12]) if isinstance(section_packet.get("focus_points", []), list) else []
            data["section_packet"] = section_packet

        section_review_packet = data.get("section_review_packet", {})
        if isinstance(section_review_packet, dict):
            section_review_packet["section_text"] = cls._truncate_text(section_review_packet.get("section_text", ""), 1500)
            retrieval_context = section_review_packet.get("retrieval_context", {})
            if isinstance(retrieval_context, dict):
                retrieval_context["list"] = cls._trim_retrieved_materials(retrieval_context.get("list", []), keep_count=6)
                section_review_packet["retrieval_context"] = retrieval_context
            coordination_payload = section_review_packet.get("coordination_payload", {})
            if isinstance(coordination_payload, dict):
                retrieval = coordination_payload.get("retrieval", {})
                if isinstance(retrieval, dict):
                    retrieval["retrieved_materials"] = cls._trim_retrieved_materials(retrieval.get("retrieved_materials", []), keep_count=6)
                    coordination_payload["retrieval"] = retrieval
                section_review_packet["coordination_payload"] = coordination_payload
            data["section_review_packet"] = section_review_packet

        coordination = data.get("coordination", {})
        if isinstance(coordination, dict):
            retrieval = coordination.get("retrieval", {})
            if isinstance(retrieval, dict):
                retrieval["retrieved_materials"] = cls._trim_retrieved_materials(retrieval.get("retrieved_materials", []), keep_count=8)
                coordination["retrieval"] = retrieval
            data["coordination"] = coordination

        planner_result = data.get("planner_result", {})
        if isinstance(planner_result, dict):
            planner_result["query_list"] = list(planner_result.get("query_list", [])[:10]) if isinstance(planner_result.get("query_list", []), list) else []
            planner_result["priority_sources"] = list(planner_result.get("priority_sources", [])[:8]) if isinstance(planner_result.get("priority_sources", []), list) else []
            planner_result["expected_evidence_types"] = list(planner_result.get("expected_evidence_types", [])[:10]) if isinstance(planner_result.get("expected_evidence_types", []), list) else []
            planner_result["missing_info_flags"] = list(planner_result.get("missing_info_flags", [])[:10]) if isinstance(planner_result.get("missing_info_flags", []), list) else []
            retrieval_plan = planner_result.get("retrieval_plan", [])
            if isinstance(retrieval_plan, list):
                compact_plan = []
                for item in retrieval_plan[:8]:
                    if not isinstance(item, dict):
                        continue
                    compact_plan.append(
                        {
                            "source_type": str(item.get("source_type", "") or "").strip(),
                            "purpose": cls._truncate_text(item.get("purpose", ""), 120),
                            "query_subset": list(item.get("query_subset", [])[:4]) if isinstance(item.get("query_subset", []), list) else [],
                        }
                    )
                planner_result["retrieval_plan"] = compact_plan
            data["planner_result"] = planner_result

        data["retrieved_materials"] = cls._trim_retrieved_materials(data.get("retrieved_materials", []), keep_count=8)

        prompt_rules = data.get("prompt_rules", {})
        if isinstance(prompt_rules, dict):
            data["prompt_rules"] = {
                str(key): cls._trim_rule_rows(value)
                for key, value in prompt_rules.items()
            }

        io_contract = data.get("io_contract", {})
        if isinstance(io_contract, dict):
            retrieval_contract = io_contract.get("retrieval", {})
            if isinstance(retrieval_contract, dict):
                output = retrieval_contract.get("output", {})
                if isinstance(output, dict):
                    output.pop("retrieved_materials", None)
                    retrieval_contract["output"] = output
                io_contract["retrieval"] = retrieval_contract
            data["io_contract"] = io_contract

        return data

    @classmethod
    def _shrink_trace_to_fit(cls, payload: Dict[str, Any]) -> Dict[str, Any]:
        data = cls._trim_trace_payload(payload)
        encoded_size = cls._json_size_bytes(data)
        if encoded_size <= cls.TRACE_JSON_BYTE_LIMIT:
            return data

        data["retrieved_materials"] = cls._trim_retrieved_materials(data.get("retrieved_materials", []), keep_count=4)
        coordination = data.get("coordination", {})
        if isinstance(coordination, dict):
            retrieval = coordination.get("retrieval", {})
            if isinstance(retrieval, dict):
                retrieval["retrieved_materials"] = cls._trim_retrieved_materials(retrieval.get("retrieved_materials", []), keep_count=4)
                coordination["retrieval"] = retrieval
            data["coordination"] = coordination
        encoded_size = cls._json_size_bytes(data)
        if encoded_size <= cls.TRACE_JSON_BYTE_LIMIT:
            return data

        section_review_packet = data.get("section_review_packet", {})
        if isinstance(section_review_packet, dict):
            section_review_packet.pop("coordination_payload", None)
            section_review_packet["section_text"] = cls._truncate_text(section_review_packet.get("section_text", ""), 600)
            data["section_review_packet"] = section_review_packet
        section_packet = data.get("section_packet", {})
        if isinstance(section_packet, dict):
            section_packet["raw_text"] = cls._truncate_text(section_packet.get("raw_text", ""), 600)
            section_packet["paragraph_blocks"] = cls._trim_paragraph_blocks(section_packet.get("paragraph_blocks", [])[:6] if isinstance(section_packet.get("paragraph_blocks", []), list) else [])
            data["section_packet"] = section_packet
        encoded_size = cls._json_size_bytes(data)
        if encoded_size <= cls.TRACE_JSON_BYTE_LIMIT:
            return data

        data.pop("section_review_packet", None)
        data["trace_note"] = "trace payload truncated to fit persistence limit"
        return data

    @classmethod
    def _minimal_trace_payload(cls, payload: Dict[str, Any], artifact_meta: Dict[str, Any] | None = None) -> Dict[str, Any]:
        section_packet = payload.get("section_packet", {}) if isinstance(payload.get("section_packet"), dict) else {}
        planner_result = payload.get("planner_result", {}) if isinstance(payload.get("planner_result"), dict) else {}
        retrieval_detail = payload.get("retrieval_detail", {}) if isinstance(payload.get("retrieval_detail"), dict) else {}
        prompt_rules = payload.get("prompt_rules", {}) if isinstance(payload.get("prompt_rules"), dict) else {}
        minimal: Dict[str, Any] = {
            "trace_schema": str(payload.get("trace_schema", "") or "chapter_review_v1"),
            "section_packet": {
                "section_id": str(section_packet.get("section_id", "") or payload.get("section_id", "")).strip(),
                "section_code": str(section_packet.get("section_code", "") or "").strip(),
                "section_title": str(section_packet.get("section_title", "") or section_packet.get("section_name", "") or "").strip(),
                "focus_points": list(section_packet.get("focus_points", [])[:8]) if isinstance(section_packet.get("focus_points", []), list) else [],
            },
            "planner_result": {
                "query_list": list(planner_result.get("query_list", [])[:6]) if isinstance(planner_result.get("query_list", []), list) else [],
                "missing_info_flags": list(planner_result.get("missing_info_flags", [])[:8]) if isinstance(planner_result.get("missing_info_flags", []), list) else [],
            },
            "retrieval_detail": {
                "metrics": retrieval_detail.get("metrics", {}) if isinstance(retrieval_detail.get("metrics", {}), dict) else {},
                "source_breakdown": retrieval_detail.get("source_breakdown", {}) if isinstance(retrieval_detail.get("source_breakdown", {}), dict) else {},
                "error_breakdown": retrieval_detail.get("error_breakdown", {}) if isinstance(retrieval_detail.get("error_breakdown", {}), dict) else {},
            },
            "retrieved_materials": cls._trim_retrieved_materials(payload.get("retrieved_materials", []), keep_count=3),
            "prompt_rules": {
                str(key): cls._trim_rule_rows(value)[:8]
                for key, value in prompt_rules.items()
                if isinstance(prompt_rules, dict)
            },
            "trace_note": "trace payload stored as minimal summary; see trace_artifact for full payload",
        }
        if artifact_meta:
            minimal["trace_artifact"] = artifact_meta
        return minimal

    @classmethod
    def dump_trace_payload(cls, payload: Dict[str, Any]) -> str:
        """Dump trace payload and ensure `agent.findings` stays structured."""
        raw_payload = copy.deepcopy(payload if isinstance(payload, dict) else {})
        raw_encoded = json.dumps(raw_payload, ensure_ascii=False)
        artifact_meta = None
        if len(raw_encoded.encode("utf-8")) > cls.TRACE_JSON_BYTE_LIMIT:
            artifact_meta = cls._persist_raw_trace_artifact(raw_payload)
        data = cls._shrink_trace_to_fit(raw_payload)
        if artifact_meta:
            data = cls._minimal_trace_payload(data, artifact_meta)
        agent = data.get("agent", {})
        if isinstance(agent, dict):
            agent["findings"] = [cls.normalize_finding(item) for item in agent.get("findings", []) if cls.normalize_finding(item)]
            data["agent"] = agent
        encoded = json.dumps(data, ensure_ascii=False)
        if len(encoded.encode("utf-8")) > cls.TRACE_JSON_BYTE_LIMIT:
            if not artifact_meta:
                artifact_meta = cls._persist_raw_trace_artifact(raw_payload)
            data = cls._minimal_trace_payload(raw_payload, artifact_meta)
            encoded = json.dumps(data, ensure_ascii=False)
        if len(encoded.encode("utf-8")) > cls.TRACE_JSON_BYTE_LIMIT:
            ultra_minimal = {
                "trace_schema": str(raw_payload.get("trace_schema", "") or "chapter_review_v1"),
                "section_packet": {
                    "section_id": str((raw_payload.get("section_packet", {}) if isinstance(raw_payload.get("section_packet"), dict) else {}).get("section_id", "") or raw_payload.get("section_id", "")).strip(),
                },
                "trace_artifact": artifact_meta or {},
                "trace_note": "trace payload persisted to local artifact only",
            }
            encoded = json.dumps(ultra_minimal, ensure_ascii=False)
        return encoded

    @classmethod
    def load_trace_payload(cls, raw: str | None) -> Dict[str, Any]:
        """Load trace payload and normalize `agent.findings`."""
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except Exception:
            return {}
        agent = data.get("agent", {})
        if isinstance(agent, dict):
            agent["findings"] = [cls.normalize_finding(item) for item in agent.get("findings", []) if cls.normalize_finding(item)]
            data["agent"] = agent
        return data if isinstance(data, dict) else {}
