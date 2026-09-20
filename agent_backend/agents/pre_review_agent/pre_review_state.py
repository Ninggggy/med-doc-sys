from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class ReviewFinding:
    title: str
    problem_type: str = ""
    severity: str = "low"
    evidence: str = ""
    recommendation: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "problem_type": self.problem_type,
            "severity": self.severity,
            "evidence": self.evidence,
            "recommendation": self.recommendation,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ReviewFinding":
        payload = data if isinstance(data, dict) else {}
        return cls(
            title=str(payload.get("title", "") or "").strip(),
            problem_type=str(payload.get("problem_type", "") or "").strip(),
            severity=str(payload.get("severity", "low") or "low").strip().lower(),
            evidence=str(payload.get("evidence", "") or "").strip(),
            recommendation=str(payload.get("recommendation", "") or "").strip(),
            metadata=payload.get("metadata", {}) if isinstance(payload.get("metadata", {}), dict) else {},
        )


@dataclass
class SectionReviewPacket:
    project_id: str
    run_id: str
    doc_id: str
    section_id: str
    section_code: str
    section_title: str
    section_text: str
    title_path: List[str] = field(default_factory=list)
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    unit_type: str = ""
    section_rules: List[str] = field(default_factory=list)
    reference_examples: List[Dict[str, Any]] = field(default_factory=list)
    retrieval_context: Dict[str, Any] = field(default_factory=dict)
    related_rules: List[Dict[str, Any]] = field(default_factory=list)
    coordination_payload: Dict[str, Any] = field(default_factory=dict)
    memory_metadata_filter: Dict[str, Any] = field(default_factory=dict)
    run_config: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "project_id": self.project_id,
            "run_id": self.run_id,
            "doc_id": self.doc_id,
            "section_id": self.section_id,
            "section_code": self.section_code,
            "section_title": self.section_title,
            "section_text": self.section_text,
            "title_path": list(self.title_path),
            "page_start": self.page_start,
            "page_end": self.page_end,
            "unit_type": self.unit_type,
            "section_rules": list(self.section_rules),
            "reference_examples": list(self.reference_examples),
            "retrieval_context": dict(self.retrieval_context),
            "related_rules": list(self.related_rules),
            "coordination_payload": dict(self.coordination_payload),
            "memory_metadata_filter": dict(self.memory_metadata_filter),
            "run_config": dict(self.run_config),
        }


@dataclass
class PreReviewState:
    doc_id: str
    section_id: str
    content: str
    project_id: str = ""
    run_id: str = ""
    section_title: str = ""
    section_code: str = ""
    title_path: List[str] = field(default_factory=list)
    memory_context: str = ""
    retrieval_context: Dict[str, Any] = field(default_factory=dict)
    findings: List[ReviewFinding] = field(default_factory=list)
    score: float = 0.0
    conclusion: str = ""
    trace_logs: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def add_trace(self, step: str, payload: Dict[str, Any]) -> None:
        self.trace_logs.append(
            {
                "step": step,
                "timestamp": datetime.utcnow().isoformat(),
                "payload": payload,
            }
        )

    def finalize(self, output: Dict[str, Any]) -> None:
        self.findings = []
        for item in list(output.get("findings") or []):
            if isinstance(item, ReviewFinding):
                self.findings.append(item)
            elif isinstance(item, dict):
                self.findings.append(ReviewFinding.from_dict(item))
            else:
                title = str(item or "").strip()
                if title:
                    self.findings.append(ReviewFinding(title=title))
        try:
            self.score = float(output.get("score", 0.0))
        except Exception:
            self.score = 0.0
        self.conclusion = str(output.get("conclusion", "") or "")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "section_id": self.section_id,
            "content": self.content,
            "project_id": self.project_id,
            "run_id": self.run_id,
            "section_title": self.section_title,
            "section_code": self.section_code,
            "title_path": list(self.title_path),
            "memory_context": self.memory_context,
            "retrieval_context": dict(self.retrieval_context),
            "findings": [item.to_dict() for item in self.findings],
            "score": self.score,
            "conclusion": self.conclusion,
            "trace_logs": list(self.trace_logs),
            "metadata": dict(self.metadata),
        }
