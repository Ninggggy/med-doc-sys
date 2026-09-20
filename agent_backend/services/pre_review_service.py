import json
import hashlib
import os
import logging
import re
import threading
import uuid
import zipfile
from collections import OrderedDict
from copy import deepcopy
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from docx import Document
from docx.oxml.ns import qn
from sqlalchemy import and_, desc, func, or_, text
from werkzeug.utils import secure_filename

from agent.agent_backend.agents.pre_review_agent.pre_review_state import SectionReviewPacket
from agent.agent_backend.agents.review.consistency_agent import ConsistencyAgent
from agent.agent_backend.llm.errors import LLMContextError, LLMExecutionError
from agent.agent_backend.agents.review.feedback_agent import FeedbackAgent
from agent.agent_backend.agents.review.lead_reviewer_agent import LeadReviewerAgent
from agent.agent_backend.agents.review.meta_reflection_agent import MetaReflectionAgent
from agent.agent_backend.agents.review.p52_feedback_optimizer_agent import P52FeedbackOptimizerAgent
from agent.agent_backend.agents.review.p52_reviewer_agent import P52ReviewerAgent
from agent.agent_backend.agents.review.planner_reviewer_agent import PlannerReviewerAgent
from agent.agent_backend.agents.review.qa_agent import QAAgent
from agent.agent_backend.agents.review.retrieval_evaluator_agent import RetrievalEvaluatorAgent
from agent.agent_backend.agents.review.task_question_agent import TaskQuestionAgent
from agent.agent_backend.agents.review.run_review_workflow import RunReviewWorkflow
from agent.agent_backend.agents.review.submission_parser_agent import SubmissionParserAgent
from agent.agent_backend.common_tools.builtin.memory_tool import MemoryTool
from agent.agent_backend.config.settings import settings
from agent.agent_backend.database.mysql.db_model import (
    FileInfo,
    RuntimeTask,
    PreReviewFeedback,
    PreReviewExecutionAudit,
    PreReviewProject,
    PreReviewRun,
    PreReviewPromptRule,
    PreReviewSectionRule,
    PreReviewSectionExample,
    PreReviewSectionConclusion,
    PreReviewSectionOutput,
    PreReviewFeedbackAnalysisResult,
    PreReviewPatchRegistry,
    PreReviewProjectSection,
    PreReviewSubmissionSectionContent,
    PreReviewSubmissionFile,
    PreReviewUploadTask,
    PreReviewExperienceMemory,
    PreReviewSectionTrace,
)
from agent.agent_backend.database.mysql.mysql_conn import MysqlConnection
from agent.agent_backend.feedback.optimization.prompt_version_registry import PromptVersionRegistry
from agent.agent_backend.feedback.evaluation.metrics_calculator import MetricsCalculator
from agent.agent_backend.infrastructure.repositories.pre_review_repository import (
    PreReviewRepository,
    SectionConclusionRecord,
    SectionTraceRecord,
)
from agent.agent_backend.agentic_rl.reward_functions import feedback_metrics
from agent.agent_backend.services.ctd_section_service import CTDSectionService
from agent.agent_backend.services.knowledge_service import KnowledgeService
from agent.agent_backend.services.memory_governance_service import MemoryGovernanceService
from agent.agent_backend.services.pharmacopeia_service import PharmacopeiaService
from agent.agent_backend.services.pre_review_prompt_rule_service import PreReviewPromptRuleService, TASK_TEMPLATE_MAP
from agent.agent_backend.prompts.prompt_envelope_builder import PromptEnvelopeBuilder
from agent.agent_backend.services.pre_review_agent_contracts import (
    PreReviewAgentContractBuilder,
)
from agent.agent_backend.services.pre_review_runtime_context_service import PreReviewRuntimeContextService
from agent.agent_backend.services.execution_audit_service import ExecutionAuditService
from agent.agent_backend.services.pre_review_projection_service import PreReviewProjectionService
from agent.agent_backend.services.pre_review_run_orchestrator import PreReviewRunOrchestrator
from agent.agent_backend.services.p52_rule_review_orchestrator import P52RuleReviewOrchestrator
from agent.agent_backend.services.p52_feedback_optimize_orchestrator import P52FeedbackOptimizeOrchestrator
from agent.agent_backend.services.p52_patch_apply_service import P52PatchApplyService
from agent.agent_backend.services.p52_rule_patch_persist_service import P52RulePatchPersistService
from agent.agent_backend.services.p52_meta_reflection_orchestrator import P52MetaReflectionOrchestrator
from agent.agent_backend.services.p52_ablation_orchestrator import P52AblationOrchestrator
from agent.agent_backend.services.section_review_orchestrator import SectionReviewOrchestrator
from agent.agent_backend.services.feedback_optimize_orchestrator import FeedbackOptimizeOrchestrator
from agent.agent_backend.services.pre_review_submission_ctd_service import PreReviewSubmissionCTDService
from agent.agent_backend.services.pre_review_submission_service import PreReviewSubmissionService
from agent.agent_backend.utils.agent_logging import log_agent_flow
from agent.agent_backend.utils.file_util import ensure_dir_exists
from agent.agent_backend.memory.storage.vector_store import VectorStore
from agent.agent_backend.utils.parser import ParserManager
from agent.agent_backend.utils.parser.review_rule_json_parser import ReviewRuleJsonParser


def _load_parse_submission_pdf_to_payload():
    from agent.agent_backend.utils.parser.ctd_paser import parse_submission_pdf_to_payload

    return parse_submission_pdf_to_payload


SUBMISSION_UPLOAD_DIR = settings.submission_upload_dir
SUBMISSION_PARSED_DIR = settings.submission_parse_dir
SUBMISSION_EDIT_DIR = str(Path(__file__).resolve().parents[1] / "data" / "submission_edits")
REPORT_DIR = settings.report_dir
RAW_DATA_DIR = str(Path(__file__).resolve().parents[1] / "data" / "raw_data")
CTD_RULES_FILE = Path(settings.rule_data_dir) / "ctd_rules.json"
GLOBAL_SECTION_RULE_PROJECT_ID = "__global_section_rules__"
GLOBAL_SECTION_RULE_PROJECT_NAME = "GLOBAL_SECTION_RULES"
ensure_dir_exists(SUBMISSION_UPLOAD_DIR)
ensure_dir_exists(SUBMISSION_PARSED_DIR)
ensure_dir_exists(SUBMISSION_EDIT_DIR)
ensure_dir_exists(REPORT_DIR)

logger = logging.getLogger("agent.pre_review.service")
SUBMISSION_SECTION_CONTENT_MAX_BYTES = 60000
SUBMISSION_SECTION_CONTENT_CHUNK_MAX_CHARS = 12000
SECTION_REVIEW_WINDOW_TRIGGER_CHARS = 6500
SECTION_REVIEW_WINDOW_MAX_CHARS = 3600
SECTION_REVIEW_WINDOW_OVERLAP_PARAGRAPHS = 1
FEEDBACK_ANALYSIS_JSON_MAX_BYTES = 60000
P52_FEEDBACK_BUNDLE_CACHE_MAX_ITEMS = 32


class _CrossProcessProjectTaskLock:
    """同时保护线程和多 Worker 进程的可重入项目任务锁。"""

    def __init__(self, lock_path: Path) -> None:
        self.lock_path = Path(lock_path)
        self._thread_lock = threading.RLock()
        self._local = threading.local()

    def __enter__(self):
        self._thread_lock.acquire()
        depth = int(getattr(self._local, "depth", 0) or 0)
        if depth > 0:
            self._local.depth = depth + 1
            return self
        fd: Optional[int] = None
        try:
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(self.lock_path), os.O_CREAT | os.O_RDWR, 0o600)
            if os.name == "nt":  # pragma: no cover - Windows 部署兼容
                import msvcrt

                if os.path.getsize(self.lock_path) == 0:
                    os.write(fd, b"0")
                    os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX)
            self._local.fd = fd
            self._local.depth = 1
            return self
        except Exception:
            if fd is not None:
                os.close(fd)
            self._thread_lock.release()
            raise

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        depth = int(getattr(self._local, "depth", 1) or 1) - 1
        self._local.depth = depth
        try:
            if depth == 0:
                fd = int(self._local.fd)
                if os.name == "nt":  # pragma: no cover - Windows 部署兼容
                    import msvcrt

                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)
                del self._local.fd
        finally:
            self._thread_lock.release()


SINGLE_SECTION_PRE_REVIEW_V2 = "single_section_pre_review_v2"
RETRIEVAL_SOURCE_GUIDANCE = "指导原则"
RETRIEVAL_SOURCE_ICH = "ICH"
RETRIEVAL_SOURCE_REGULATION = "法律法规"
RETRIEVAL_SOURCE_PHARMACOPOEIA = "药典数据"
RETRIEVAL_SOURCE_EXPERIENCE = "历史经验"

_RETRIEVAL_SOURCE_ALIASES = {
    RETRIEVAL_SOURCE_GUIDANCE: {
        RETRIEVAL_SOURCE_GUIDANCE,
        "guidance",
        "guidance_principles",
        "guideline",
        "guidelines",
    },
    RETRIEVAL_SOURCE_ICH: {
        RETRIEVAL_SOURCE_ICH,
        "ich",
    },
    RETRIEVAL_SOURCE_REGULATION: {
        RETRIEVAL_SOURCE_REGULATION,
        "法规",
        "法律",
        "regulation",
        "regulations",
        "law",
        "law_and_regulation",
    },
    RETRIEVAL_SOURCE_PHARMACOPOEIA: {
        RETRIEVAL_SOURCE_PHARMACOPOEIA,
        "药典",
        "pharmacopeia",
        "pharmacopoeia",
    },
    RETRIEVAL_SOURCE_EXPERIENCE: {
        RETRIEVAL_SOURCE_EXPERIENCE,
        "experience",
        "historical_experience",
    },
}

RETRIEVAL_FALSE_NEGATIVE_ERRORS = {
    "query_miss",
    "historical_experience_missing",
}

RETRIEVAL_FALSE_POSITIVE_ERRORS = {
    "retrieval_scope_error",
    "retrieval_ranking_error",
}
def _safe_ratio(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return float(numerator) / float(denominator)


def _safe_f1(precision: float, recall: float) -> float:
    if precision + recall <= 0:
        return 0.0
    return 2.0 * precision * recall / (precision + recall)


class PreReviewService:
    _PROJECT_TASK_LOCKS: Dict[str, _CrossProcessProjectTaskLock] = {}
    _PROJECT_TASK_LOCKS_GUARD = threading.Lock()

    def __init__(self):
        self.db_conn = MysqlConnection()
        self.memory_tool = MemoryTool()
        self.planner_reviewer_agent = PlannerReviewerAgent()
        self.p52_reviewer_agent = P52ReviewerAgent()
        self.p52_feedback_optimizer_agent = P52FeedbackOptimizerAgent()
        self.retrieval_evaluator_agent = RetrievalEvaluatorAgent()
        self.task_question_agent = TaskQuestionAgent()
        self.feedback_agent = FeedbackAgent()
        self.meta_reflection_agent = MetaReflectionAgent()
        self.consistency_agent = ConsistencyAgent()
        self.qa_agent = QAAgent()
        self.lead_reviewer_agent = LeadReviewerAgent()
        self.submission_parser_agent = SubmissionParserAgent()
        self.run_review_workflow = RunReviewWorkflow(
            consistency_agent=self.consistency_agent,
            qa_agent=self.qa_agent,
            lead_reviewer_agent=self.lead_reviewer_agent,
        )
        self.knowledge = KnowledgeService()
        self.pharmacopeia = PharmacopeiaService()
        self.ctd_sections = CTDSectionService(raw_data_dir=RAW_DATA_DIR)
        self.prompt_registry = PromptVersionRegistry()
        self.metrics_calculator = MetricsCalculator()
        self.prompt_rule_service = PreReviewPromptRuleService()
        self.runtime_context_service = PreReviewRuntimeContextService(self)
        self.memory_governance = MemoryGovernanceService(self)
        self.execution_audit_service = ExecutionAuditService(self)
        self.projection_service = PreReviewProjectionService(self)
        self.submission_vector_store = VectorStore()
        self._submission_payload_cache: Dict[str, Any] = {}
        self._structured_project_payload_cache: Dict[str, Any] = {}
        self._project_section_catalog_cache: Dict[str, Any] = {}
        self._ctd_section_rule_seed_map: Optional[Dict[str, List[str]]] = None
        self._p52_feedback_bundle_cache: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()
        self._p52_feedback_bundle_cache_lock = threading.Lock()
        self.p52_patch_apply_service = P52PatchApplyService(self.db_conn)
        self.p52_rule_patch_persist_service = P52RulePatchPersistService()
        self.section_review_orchestrator = SectionReviewOrchestrator(self)
        self.p52_rule_review_orchestrator = P52RuleReviewOrchestrator(self)
        self.p52_feedback_optimize_orchestrator = P52FeedbackOptimizeOrchestrator(self)
        self.p52_meta_reflection_orchestrator = P52MetaReflectionOrchestrator(self)
        self.p52_ablation_orchestrator = P52AblationOrchestrator(self)
        self.feedback_optimize_orchestrator = FeedbackOptimizeOrchestrator(self)
        self.run_orchestrator = PreReviewRunOrchestrator(self)
        self.SUBMISSION_PARSED_DIR = SUBMISSION_PARSED_DIR
        self.ctd_submission_service = PreReviewSubmissionCTDService(self)
        self.submission_service = PreReviewSubmissionService(self)

    @staticmethod
    def _now() -> datetime:
        return datetime.now()

    @staticmethod
    def _new_project_id() -> str:
        return f"prj_{uuid.uuid4().hex[:12]}"

    @staticmethod
    def _new_run_id() -> str:
        return f"run_{uuid.uuid4().hex[:12]}"

    @staticmethod
    def _new_submission_doc_id() -> str:
        return f"sub_{uuid.uuid4().hex[:16]}"

    @classmethod
    def project_task_creation_lock(cls, project_id: str) -> _CrossProcessProjectTaskLock:
        """将项目删除与后台任务的校验、落库及启动串行化。"""
        normalized_project_id = str(project_id or "").strip()
        if not normalized_project_id:
            raise ValueError("project_id is required")
        with cls._PROJECT_TASK_LOCKS_GUARD:
            lock = cls._PROJECT_TASK_LOCKS.get(normalized_project_id)
            if lock is None:
                lock_name = hashlib.sha256(normalized_project_id.encode("utf-8")).hexdigest() + ".lock"
                lock_root = Path(SUBMISSION_UPLOAD_DIR).resolve().parent / ".pre_review_task_locks"
                lock = _CrossProcessProjectTaskLock(lock_root / lock_name)
                cls._PROJECT_TASK_LOCKS[normalized_project_id] = lock
            return lock

    def validate_project_task_target(
        self,
        project_id: str,
        source_doc_id: str = "",
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """仅查询任务启动所需的项目与文档标识，不读取正文。"""
        normalized_project_id = str(project_id or "").strip()
        normalized_doc_id = str(source_doc_id or "").strip()
        if not normalized_project_id:
            return False, "project_id is required", None
        session = self.db_conn.get_session()
        try:
            project_exists = (
                session.query(PreReviewProject.project_id)
                .filter(
                    PreReviewProject.project_id == normalized_project_id,
                    PreReviewProject.is_deleted == 0,
                )
                .first()
            )
            if project_exists is None:
                return False, "project not found", None
            if normalized_doc_id:
                document_exists = (
                    session.query(PreReviewSubmissionFile.doc_id)
                    .filter(
                        PreReviewSubmissionFile.project_id == normalized_project_id,
                        PreReviewSubmissionFile.doc_id == normalized_doc_id,
                        PreReviewSubmissionFile.is_deleted == 0,
                    )
                    .first()
                )
                if document_exists is None:
                    return False, "submission file not found", None
            return True, "success", {
                "project_id": normalized_project_id,
                "source_doc_id": normalized_doc_id,
            }
        finally:
            session.close()

    @staticmethod
    def _safe_display_name(file_name: str) -> str:
        return os.path.basename(str(file_name or "")).strip()

    @staticmethod
    def _submission_storage_name(doc_id: str, display_name: str) -> str:
        ext = ""
        if "." in display_name:
            ext = display_name.rsplit(".", 1)[-1].lower().strip()
        safe = secure_filename(display_name) or "submission"
        if ext and "." not in safe:
            safe = f"{safe}.{ext}"
        return f"{doc_id}_{safe}"

    @staticmethod
    def _submission_payload_cache_key(project_id: str, doc_id: str) -> str:
        return f"{str(project_id or '').strip()}::{str(doc_id or '').strip()}"

    @staticmethod
    def _structured_project_payload_cache_key(
        project_id: str,
        doc_id: str,
        compact: bool = False,
    ) -> str:
        base_key = f"{str(project_id or '').strip()}::{str(doc_id or '').strip()}"
        return f"{base_key}::compact" if compact else base_key

    @staticmethod
    def _compact_structured_project_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
        """构造章节浏览器使用的只读精简 DTO。

        完整结构包含审评单元、附件、段落锚点以及带正文的章节树，供审评流程使用；
        若原样返回给章节浏览器，同一长文本会被重复传输多次。因此浏览接口只保留
        页面实际需要显示的两种文本。
        """
        compact_sections: List[Dict[str, Any]] = []
        for item in payload.get("sections", []) if isinstance(payload, dict) else []:
            if not isinstance(item, dict):
                continue
            raw_content = str(item.get("raw_content") or item.get("content") or "")
            cleaned_markdown = str(item.get("cleaned_markdown") or "")
            display_content = str(item.get("display_content") or "")
            if not cleaned_markdown and display_content and display_content != raw_content:
                cleaned_markdown = display_content
            if cleaned_markdown == raw_content:
                cleaned_markdown = ""
            compact_sections.append(
                {
                    "section_id": str(item.get("section_id") or "").strip(),
                    "section_code": str(item.get("section_code") or item.get("code") or "").strip(),
                    "section_name": str(item.get("section_name") or item.get("title") or "").strip(),
                    "title_path": list(item.get("title_path") or []),
                    "parent_section_id": str(item.get("parent_section_id") or "").strip(),
                    "raw_content": raw_content,
                    "cleaned_markdown": cleaned_markdown,
                    "content_preview": str(item.get("content_preview") or ""),
                    "char_count": int(item.get("char_count", 0) or len(cleaned_markdown or raw_content)),
                }
            )
        statistics = payload.get("statistics", {}) if isinstance(payload, dict) else {}
        return {
            "sections": compact_sections,
            "statistics": dict(statistics) if isinstance(statistics, dict) else {},
            "response_mode": "compact",
        }

    def _invalidate_structured_project_payload_cache(self, project_id: str = "", doc_id: str = "") -> None:
        project_key = str(project_id or "").strip()
        doc_key = str(doc_id or "").strip()
        if project_key and doc_key:
            for compact in (False, True):
                self._structured_project_payload_cache.pop(
                    self._structured_project_payload_cache_key(
                        project_key,
                        doc_key,
                        compact=compact,
                    ),
                    None,
                )
            return
        if project_key:
            prefix = f"{project_key}::"
            self._structured_project_payload_cache = {
                key: value
                for key, value in self._structured_project_payload_cache.items()
                if not str(key).startswith(prefix)
            }
            return
        self._structured_project_payload_cache = {}

    @staticmethod
    def _project_section_catalog_cache_key(project_id: str) -> str:
        return str(project_id or "").strip()

    def _invalidate_project_section_catalog_cache(self, project_id: str = "") -> None:
        project_key = str(project_id or "").strip()
        global_rule_project_key = str(GLOBAL_SECTION_RULE_PROJECT_ID or "").strip()
        if project_key:
            self._project_section_catalog_cache.pop(
                self._project_section_catalog_cache_key(project_key),
                None,
            )
            if project_key == global_rule_project_key:
                self._project_section_catalog_cache = {}
            return
        self._project_section_catalog_cache = {}

    @staticmethod
    def _is_zip_file_name(file_name: str) -> bool:
        return str(file_name or "").lower().endswith(".zip")

    @staticmethod
    def _decode_zip_name(raw_name: str) -> str:
        text = str(raw_name or "")
        if any("\u4e00" <= ch <= "\u9fff" for ch in text):
            return text
        try:
            repaired = raw_name.encode("cp437").decode("gb18030")
            return repaired
        except Exception:
            return raw_name

    @staticmethod
    def _sanitize_member_name(raw_name: str) -> str:
        normalized = str(raw_name or "").replace("\\", "/").strip().strip("/")
        parts = [p for p in normalized.split("/") if p not in {"", ".", ".."}]
        return "/".join(parts)

    @staticmethod
    def _preview_text_blocks(blocks: List[str]) -> str:
        merged = "\n\n".join([str(x).strip() for x in blocks if str(x).strip()]).strip()
        return merged

    @staticmethod
    def _safe_json_list(value: Any) -> str:
        return json.dumps(value if isinstance(value, list) else [], ensure_ascii=False)

    @staticmethod
    def _format_datetime(value: Any) -> str:
        return value.strftime("%Y-%m-%d %H:%M:%S") if value else ""

    @staticmethod
    def _dedupe_text_list(values: List[Any]) -> List[str]:
        out: List[str] = []
        seen = set()
        for item in values or []:
            value = str(item or "").strip()
            if not value or value in seen:
                continue
            seen.add(value)
            out.append(value)
        return out

    @staticmethod
    def _submission_edit_path(doc_id: str) -> str:
        return os.path.join(SUBMISSION_EDIT_DIR, f"{doc_id}.txt")

    def _load_submission_edit(self, doc_id: str) -> str:
        path = self._submission_edit_path(doc_id)
        if not os.path.exists(path):
            return ""
        try:
            with open(path, "r", encoding="utf-8") as fh:
                return fh.read()
        except Exception:
            return ""

    def _save_submission_edit(self, doc_id: str, content: str) -> str:
        path = self._submission_edit_path(doc_id)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(str(content or ""))
        return path

    @staticmethod
    def _submission_asset_url(project_id: str, doc_id: str, asset_path: str) -> str:
        normalized_asset_path = str(asset_path or "").replace("\\", "/").lstrip("/").strip()
        if not normalized_asset_path:
            return ""
        return (
            f"/api/pre-review/projects/{quote(str(project_id or '').strip())}"
            f"/submissions/{quote(str(doc_id or '').strip())}"
            f"/assets/{quote(normalized_asset_path, safe='/')}"
        )

    def _rewrite_submission_markdown_asset_refs(self, project_id: str, doc_id: str, text: str) -> str:
        value = str(text or "").strip()
        if not value:
            return ""

        def _replace(match: re.Match) -> str:
            label = str(match.group(1) or "")
            target = str(match.group(2) or "").strip()
            if not target or re.match(r"^(?:[a-z]+:|/|#)", target, flags=re.IGNORECASE):
                return match.group(0)
            rewritten = self._submission_asset_url(project_id=project_id, doc_id=doc_id, asset_path=target)
            return f"![{label}]({rewritten})" if rewritten else match.group(0)

        return re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", _replace, value)

    def _ensure_global_rule_project(self, session) -> str:
        pending = next(
            (
                row
                for row in list(getattr(session, "new", []) or [])
                if isinstance(row, PreReviewProject)
                and str(getattr(row, "project_id", "") or "").strip() == GLOBAL_SECTION_RULE_PROJECT_ID
            ),
            None,
        )
        if pending is not None:
            return GLOBAL_SECTION_RULE_PROJECT_ID

        exists = (
            session.query(PreReviewProject.project_id)
            .filter(PreReviewProject.project_id == GLOBAL_SECTION_RULE_PROJECT_ID)
            .first()
        )
        if exists is not None:
            return GLOBAL_SECTION_RULE_PROJECT_ID

        now = self._now()
        bootstrap_session = self.db_conn.get_session()
        try:
            bootstrap_session.execute(
                text(
                    """
                    INSERT IGNORE INTO pre_review_project (
                        project_id,
                        project_name,
                        description,
                        registration_scope,
                        registration_path,
                        registration_leaf,
                        registration_description,
                        status,
                        progress,
                        owner,
                        create_time,
                        update_time,
                        is_deleted
                    ) VALUES (
                        :project_id,
                        :project_name,
                        :description,
                        :registration_scope,
                        :registration_path,
                        :registration_leaf,
                        :registration_description,
                        :status,
                        :progress,
                        :owner,
                        :create_time,
                        :update_time,
                        :is_deleted
                    )
                    """
                ),
                {
                    "project_id": GLOBAL_SECTION_RULE_PROJECT_ID,
                    "project_name": GLOBAL_SECTION_RULE_PROJECT_NAME,
                    "description": "Global section review rules registry",
                    "registration_scope": "system",
                    "registration_path": "[]",
                    "registration_leaf": "system",
                    "registration_description": "Global section review rules registry",
                    "status": "system",
                    "progress": 1.0,
                    "owner": "system",
                    "create_time": now,
                    "update_time": now,
                    "is_deleted": 0,
                },
            )
            bootstrap_session.commit()
        finally:
            bootstrap_session.close()
        return GLOBAL_SECTION_RULE_PROJECT_ID

    def _load_global_section_rule_map(
        self,
        session,
        *,
        source_types: Optional[List[str]] = None,
    ) -> Dict[str, List[str]]:
        global_project_id = self._ensure_global_rule_project(session)
        query = session.query(PreReviewSectionRule).filter(
            PreReviewSectionRule.project_id == global_project_id,
            PreReviewSectionRule.is_active == 1,
        )
        if source_types:
            query = query.filter(PreReviewSectionRule.source_type.in_(source_types))
        rows = query.order_by(PreReviewSectionRule.section_id.asc(), PreReviewSectionRule.id.asc()).all()
        out: Dict[str, List[str]] = {}
        for row in rows:
            section_id = str(getattr(row, "section_id", "") or "").strip()
            rule_text = str(getattr(row, "rule_text", "") or "").strip()
            if not section_id or not rule_text:
                continue
            out.setdefault(section_id, [])
            if rule_text not in out[section_id]:
                out[section_id].append(rule_text)
        return out

    def _load_manual_concern_map(self, session, project_id: str) -> Dict[str, List[str]]:
        return self._load_global_section_rule_map(session)

    def _load_manual_section_rule_map(self, session, project_id: str) -> Dict[str, List[str]]:
        return self._load_global_section_rule_map(session)

    def _load_ctd_section_rule_seed_map(self) -> Dict[str, List[str]]:
        session = self.db_conn.get_session()
        try:
            global_project_id = self._ensure_global_rule_project(session)
            rows = (
                session.query(PreReviewSectionRule)
                .filter(
                    PreReviewSectionRule.project_id == global_project_id,
                    PreReviewSectionRule.source_type.in_(["ctd_seed", "json_import"]),
                    PreReviewSectionRule.is_active == 1,
                )
                .order_by(PreReviewSectionRule.section_id.asc(), PreReviewSectionRule.rule_code.asc(), PreReviewSectionRule.id.asc())
                .all()
            )
            rule_map: Dict[str, List[str]] = {}
            for row in rows:
                section_id = str(getattr(row, "section_id", "") or "").strip()
                rule_text = str(getattr(row, "rule_text", "") or "").strip()
                if not section_id or not rule_text:
                    continue
                rule_map.setdefault(section_id, [])
                if rule_text not in rule_map[section_id]:
                    rule_map[section_id].append(rule_text)
            self._ctd_section_rule_seed_map = deepcopy(rule_map)
            return deepcopy(rule_map)
        finally:
            session.close()

    def _write_ctd_section_rule_seed_map(self, rule_map: Dict[str, List[str]]) -> None:
        session = self.db_conn.get_session()
        try:
            self._replace_global_seed_section_rule_map(session, rule_map, source_ref="uploaded_json")
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _replace_global_seed_section_rule_map(
        self,
        session,
        rule_map: Dict[str, List[str]],
        *,
        source_ref: str = "uploaded_json",
        source_type: str = "json_import",
        scope_metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        global_project_id = self._ensure_global_rule_project(session)
        normalized_source_type = str(source_type or "").strip() or "json_import"
        normalized_source_mode = "database_json_registry" if normalized_source_type == "json_import" else "database_seed_registry"
        normalized_scope_metadata = self._normalize_rule_scope_metadata(scope_metadata or {})
        scope_key = self._build_rule_scope_key(normalized_scope_metadata)
        section_name_map = {
            str(item.get("section_id", "") or "").strip(): str(item.get("section_name", "") or "").strip()
            for item in self.ctd_sections.get_catalog().get("all_sections", [])
            if isinstance(item, dict) and str(item.get("section_id", "") or "").strip()
        }
        normalized_map: Dict[str, List[str]] = {}
        for section_id, rules in (rule_map or {}).items():
            normalized_section_id = self.ctd_sections.normalize_section_id(section_id)
            if not normalized_section_id:
                continue
            normalized_rules = self._dedupe_text_list(rules or [])
            if normalized_rules:
                normalized_map[normalized_section_id] = normalized_rules
        normalized_section_ids = list(normalized_map.keys())
        rows = []
        if normalized_section_ids:
            rows = (
                session.query(PreReviewSectionRule)
                .filter(
                    PreReviewSectionRule.project_id == global_project_id,
                    PreReviewSectionRule.section_id.in_(normalized_section_ids),
                )
                .all()
            )
        existing_map = {
            (
                str(getattr(row, "section_id", "") or "").strip(),
                str(getattr(row, "rule_code", "") or "").strip(),
            ): row
            for row in rows
            if str(getattr(row, "section_id", "") or "").strip() and str(getattr(row, "rule_code", "") or "").strip()
        }
        expected_seed_keys = {
            (section_id, f"ctd_rule__{section_id.replace('.', '_')}__{scope_key}__{index:02d}")
            for section_id, rules in normalized_map.items()
            for index, _ in enumerate(rules or [], start=1)
            if str(section_id or "").strip()
        }
        for row in rows:
            row_key = (
                str(getattr(row, "section_id", "") or "").strip(),
                str(getattr(row, "rule_code", "") or "").strip(),
            )
            source_type = str(getattr(row, "source_type", "") or "").strip()
            try:
                row_payload = json.loads(getattr(row, "payload_json", "") or "{}")
            except Exception:
                row_payload = {}
            row_scope_key = self._build_rule_scope_key(
                self._extract_rule_scope_metadata_from_payload(row_payload, section_id=row_key[0])
            )
            if row_scope_key != scope_key:
                continue
            if row_key not in expected_seed_keys and source_type in {"ctd_seed", "json_import"}:
                session.delete(row)
                existing_map.pop(row_key, None)
        now = self._now()
        for section_id, rules in normalized_map.items():
            if not rules:
                continue
            section_name = section_name_map.get(section_id, section_id)
            for index, rule_text in enumerate(rules, start=1):
                rule_code = f"ctd_rule__{section_id.replace('.', '_')}__{scope_key}__{index:02d}"
                existing = existing_map.get((section_id, rule_code))
                if existing is None:
                    row = PreReviewSectionRule(
                        rule_id=f"section_rule_{uuid.uuid4().hex[:16]}",
                        project_id=global_project_id,
                        section_id=section_id,
                        section_name=section_name,
                        rule_code=rule_code,
                        rule_text=str(rule_text or "").strip(),
                        source_type=normalized_source_type,
                        source_ref=str(source_ref or "uploaded_json").strip() or "uploaded_json",
                        is_active=True,
                        payload_json=json.dumps(
                            {
                                "section_id": section_id,
                                "rule_code": rule_code,
                                "rule_text": str(rule_text or "").strip(),
                                "source_ref": str(source_ref or "uploaded_json").strip() or "uploaded_json",
                                "source_mode": normalized_source_mode,
                                "scope_metadata": normalized_scope_metadata,
                            },
                            ensure_ascii=False,
                        ),
                        create_time=now,
                        update_time=now,
                    )
                    session.add(row)
                    existing_map[(section_id, rule_code)] = row
                    continue
                existing.section_name = section_name
                existing.rule_text = str(rule_text or "").strip()
                existing.source_type = normalized_source_type
                existing.source_ref = str(source_ref or existing.source_ref or "uploaded_json").strip() or "uploaded_json"
                existing.is_active = True
                existing.payload_json = json.dumps(
                    {
                        "section_id": section_id,
                        "rule_code": rule_code,
                        "rule_text": str(rule_text or "").strip(),
                        "source_ref": str(existing.source_ref or source_ref or "uploaded_json").strip() or "uploaded_json",
                        "source_mode": normalized_source_mode,
                        "scope_metadata": normalized_scope_metadata,
                    },
                    ensure_ascii=False,
                )
                existing.update_time = now
        self._ctd_section_rule_seed_map = deepcopy(normalized_map)
        self._invalidate_project_section_catalog_cache(project_id=global_project_id)

    def _ensure_project_section_rules(self, session, project_id: str) -> None:
        _ = project_id
        self._ensure_global_rule_project(session)
        return

    def _load_project_section_rule_map(self, session, project_id: str) -> Dict[str, List[str]]:
        global_project_id = self._ensure_global_rule_project(session)
        self._ensure_project_section_rules(session, global_project_id)
        project = (
            session.query(PreReviewProject)
            .filter(and_(PreReviewProject.project_id == str(project_id or "").strip(), PreReviewProject.is_deleted == 0))
            .first()
        )
        rows = (
            session.query(PreReviewSectionRule)
            .filter(
                PreReviewSectionRule.is_active == 1,
                or_(
                    and_(
                        PreReviewSectionRule.project_id == global_project_id,
                        PreReviewSectionRule.source_type.in_(["ctd_seed", "manual", "json_import"]),
                    ),
                    and_(
                        PreReviewSectionRule.project_id == project_id,
                        PreReviewSectionRule.source_type.in_(["feedback_experience", "feedback_patch"]),
                    ),
                ),
            )
            .order_by(PreReviewSectionRule.section_id.asc(), PreReviewSectionRule.id.asc())
            .all()
        )
        out: Dict[str, List[str]] = {}
        for row in rows:
            section_id = str(getattr(row, "section_id", "") or "").strip()
            rule_text = str(getattr(row, "rule_text", "") or "").strip()
            if not section_id or not rule_text:
                continue
            source_type = str(getattr(row, "source_type", "") or "").strip()
            if project is not None and source_type in {"ctd_seed", "manual", "json_import"}:
                try:
                    payload = json.loads(getattr(row, "payload_json", "") or "{}")
                except Exception:
                    payload = {}
                rule_scope = self._extract_rule_scope_metadata_from_payload(payload, section_id=section_id)
                project_scope = self._build_project_rule_scope_metadata(project, section_id=section_id)
                if not self._rule_scope_matches(rule_scope, project_scope, allow_generic=True):
                    continue
            out.setdefault(section_id, [])
            if rule_text not in out[section_id]:
                out[section_id].append(rule_text)
        return out

    def _ensure_project_sections(self, session, project_id: str) -> None:
        project_key = str(project_id or "").strip()
        if not project_key:
            return
        pending_ids = {
            str(getattr(row, "section_id", "") or "").strip()
            for row in list(session.new)
            if isinstance(row, PreReviewProjectSection)
            and str(getattr(row, "project_id", "") or "").strip() == project_key
        }
        existing_ids = {
            str(section_id or "").strip()
            for (section_id,) in (
                session.query(PreReviewProjectSection.section_id)
                .filter(PreReviewProjectSection.project_id == project_key)
                .all()
            )
            if str(section_id or "").strip()
        }
        if pending_ids or existing_ids:
            return
        catalog = self.ctd_sections.get_catalog()
        now = self._now()
        catalog_sections = catalog.get("all_sections", []) or catalog.get("flat_sections", []) or []
        for item in catalog_sections:
            if not isinstance(item, dict):
                continue
            section_id = str(item.get("section_id", "") or "").strip()
            if not section_id or section_id in existing_ids or section_id in pending_ids:
                continue
            session.add(
                PreReviewProjectSection(
                    project_id=project_key,
                    section_id=section_id,
                    section_code=str(item.get("section_code", "") or section_id).strip() or section_id,
                    section_name=str(item.get("section_name", "") or section_id).strip() or section_id,
                    root_section_id=str(item.get("root_section_id", "") or self._branch_root_from_section_id(section_id)).strip(),
                    parent_section_id=str(item.get("parent_section_id", "") or "").strip(),
                    node_level=int(item.get("node_level", 0) or 0),
                    sort_order=int(item.get("sort_order", 0) or 0),
                    is_leaf=bool(item.get("is_leaf", True)),
                    title_path=self._safe_json_list(item.get("title_path", [])),
                    concern_points=self._safe_json_list(item.get("concern_points", [])),
                    create_time=now,
                    update_time=now,
                )
            )
            pending_ids.add(section_id)

    def _load_project_section_catalog(self, session, project_id: str) -> Dict[str, Any]:
        project_key = str(project_id or "").strip()
        if not project_key:
            return self.ctd_sections.get_catalog()
        cache_key = self._project_section_catalog_cache_key(project_key)
        cached_catalog = self._project_section_catalog_cache.get(cache_key)
        if isinstance(cached_catalog, dict) and cached_catalog:
            return deepcopy(cached_catalog)

        self._ensure_project_sections(session, project_key)
        self._ensure_project_section_rules(session, project_key)
        rows = (
            session.query(PreReviewProjectSection)
            .filter(PreReviewProjectSection.project_id == project_key)
            .order_by(
                PreReviewProjectSection.root_section_id.asc(),
                PreReviewProjectSection.parent_section_id.asc(),
                PreReviewProjectSection.sort_order.asc(),
                PreReviewProjectSection.section_id.asc(),
            )
            .all()
        )
        if not rows:
            catalog = self.ctd_sections.get_catalog()
            self._project_section_catalog_cache[cache_key] = deepcopy(catalog)
            return deepcopy(catalog)

        section_rule_map = self._load_project_section_rule_map(session, project_key)
        base_catalog = self.ctd_sections.get_catalog()
        base_section_map = (
            base_catalog.get("section_map", {})
            if isinstance(base_catalog.get("section_map", {}), dict)
            else {}
        )
        project_nodes: Dict[str, Dict[str, Any]] = {}
        ordered_ids: List[str] = []
        for row in rows:
            section_id = self.ctd_sections.normalize_section_id(getattr(row, "section_id", ""))
            if not section_id:
                continue
            parent_section_id = self.ctd_sections.normalize_section_id(getattr(row, "parent_section_id", ""))
            root_section_id = self.ctd_sections.normalize_section_id(getattr(row, "root_section_id", "")) or section_id
            base_node = base_section_map.get(section_id, {}) if isinstance(base_section_map.get(section_id, {}), dict) else {}
            default_name = str(getattr(row, "section_name", "") or base_node.get("section_name", "") or section_id).strip() or section_id
            default_code = str(getattr(row, "section_code", "") or base_node.get("section_code", "") or section_id).strip() or section_id
            title_path = self._parse_json_list(getattr(row, "title_path", "") or "")
            if not title_path:
                title_path = list(base_node.get("title_path") or [])
            node_payload = {
                "section_id": section_id,
                "section_code": default_code,
                "section_name": default_name,
                "title_path": title_path,
                "concern_points": self._parse_json_list(getattr(row, "concern_points", "") or ""),
                "section_rules": list(section_rule_map.get(section_id, [])),
                "root_section_id": root_section_id,
                "parent_section_id": parent_section_id,
                "node_level": int(getattr(row, "node_level", 0) or base_node.get("node_level", 0) or 0),
                "sort_order": int(getattr(row, "sort_order", 0) or base_node.get("sort_order", 0) or 0),
                "is_leaf": bool(getattr(row, "is_leaf", True)),
                "children_sections": [],
            }
            project_nodes[section_id] = node_payload
            ordered_ids.append(section_id)

        chapter_structure: List[Dict[str, Any]] = []

        def sort_nodes(nodes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            sorted_nodes = sorted(
                [node for node in (nodes or []) if isinstance(node, dict)],
                key=lambda item: (
                    int(item.get("sort_order", 0) or 0),
                    str(item.get("section_id", "") or "").strip(),
                ),
            )
            for node in sorted_nodes:
                node["children_sections"] = sort_nodes(node.get("children_sections") or [])
            return sorted_nodes

        for sid in ordered_ids:
            node = project_nodes.get(sid, {})
            if not isinstance(node, dict) or not node:
                continue
            parent_section_id = str(node.get("parent_section_id", "") or "").strip()
            parent_node = project_nodes.get(parent_section_id, {}) if parent_section_id else {}
            if isinstance(parent_node, dict) and parent_node and parent_section_id != sid:
                parent_node.setdefault("children_sections", [])
                parent_node["children_sections"].append(node)
                parent_node["is_leaf"] = False
            elif node not in chapter_structure:
                chapter_structure.append(node)
        chapter_structure = sort_nodes(chapter_structure)

        def finalize(nodes: List[Dict[str, Any]], parent_path: Optional[List[str]] = None, inherited_root: str = "") -> None:
            parent_path = list(parent_path or [])
            for index, node in enumerate(nodes or [], start=1):
                if not isinstance(node, dict):
                    continue
                section_id = str(node.get("section_id", "") or "").strip()
                section_name = str(node.get("section_name", "") or section_id).strip() or section_id
                current_title_path = [
                    str(item).strip()
                    for item in (node.get("title_path", []) if isinstance(node.get("title_path", []), list) else [])
                    if str(item).strip()
                ]
                if not current_title_path:
                    current_title_path = parent_path + [section_name]
                root_section_id = str(node.get("root_section_id", "") or "").strip() or inherited_root or section_id
                children = [item for item in (node.get("children_sections") or []) if isinstance(item, dict)]
                node["section_id"] = section_id
                node["section_code"] = str(node.get("section_code", "") or section_id).strip() or section_id
                node["section_name"] = section_name
                node["title_path"] = current_title_path
                node["root_section_id"] = root_section_id
                node["node_level"] = int(node.get("node_level", len(section_id.split(".")) - 1) or 0)
                node["sort_order"] = int(node.get("sort_order", index) or index)
                node["is_leaf"] = not bool(children)
                node["section_rules"] = list(section_rule_map.get(section_id, []))
                node["children_sections"] = children
                finalize(children, parent_path=current_title_path, inherited_root=root_section_id)

        finalize(chapter_structure, parent_path=[], inherited_root="")
        all_sections = self.ctd_sections.flatten_nodes(chapter_structure, leaf_only=False)
        flat_sections = self.ctd_sections.flatten_nodes(chapter_structure, leaf_only=True)
        for item in all_sections:
            sid = str(item.get("section_id", "") or "").strip()
            item["section_rules"] = list(section_rule_map.get(sid, []))
        for item in flat_sections:
            sid = str(item.get("section_id", "") or "").strip()
            item["section_rules"] = list(section_rule_map.get(sid, []))
        catalog = {
            "chapter_structure": chapter_structure,
            "all_sections": all_sections,
            "flat_sections": flat_sections,
            "section_map": {item["section_id"]: item for item in all_sections if item.get("section_id")},
            "leaf_section_map": {item["section_id"]: item for item in flat_sections if item.get("section_id")},
        }
        self._project_section_catalog_cache[cache_key] = deepcopy(catalog)
        return deepcopy(catalog)

    def _merge_catalog_with_manual_concerns(self, catalog: Dict[str, Any], manual_map: Dict[str, List[str]]) -> Dict[str, Any]:
        chapter_structure = deepcopy(catalog.get("chapter_structure", []))
        section_rule_map = {
            str(item.get("section_id", "") or "").strip(): list(item.get("section_rules") or [])
            for item in (catalog.get("all_sections", []) if isinstance(catalog.get("all_sections", []), list) else [])
            if isinstance(item, dict) and str(item.get("section_id", "") or "").strip()
        }

        def attach(nodes: List[Dict[str, Any]]):
            for node in nodes or []:
                sid = str(node.get("section_id", "")).strip()
                merged = self._dedupe_text_list(list(node.get("concern_points") or []) + manual_map.get(sid, []))
                node["concern_points"] = merged
                node["section_rules"] = list(section_rule_map.get(sid, []))
                attach(node.get("children_sections") or [])

        attach(chapter_structure)
        all_sections = self.ctd_sections.flatten_nodes(chapter_structure, leaf_only=False)
        flat_sections = self.ctd_sections.flatten_nodes(chapter_structure, leaf_only=True)
        for item in all_sections:
            sid = str(item.get("section_id", "") or "").strip()
            item["section_rules"] = list(section_rule_map.get(sid, []))
        for item in flat_sections:
            sid = str(item.get("section_id", "") or "").strip()
            item["section_rules"] = list(section_rule_map.get(sid, []))
        return {
            "chapter_structure": chapter_structure,
            "all_sections": all_sections,
            "flat_sections": flat_sections,
            "section_map": {item["section_id"]: item for item in all_sections},
            "leaf_section_map": {item["section_id"]: item for item in flat_sections},
        }

    @staticmethod
    def _normalize_registration_scope(scope_value: Any) -> str:
        scope_text = str(scope_value or "").strip()
        if not scope_text:
            return ""
        lowered = scope_text.lower()
        if scope_text in {"化药", "化学药"}:
            return "化药"
        if "化学药" in scope_text or "化药" in scope_text:
            return "化药"
        if "chemical" in lowered:
            return "化药"
        return scope_text

    @staticmethod
    def _normalize_registration_path(path_value: Any) -> List[str]:
        if isinstance(path_value, list):
            return [str(item or "").strip() for item in path_value if str(item or "").strip()]
        if isinstance(path_value, str):
            try:
                decoded = json.loads(path_value)
                if isinstance(decoded, list):
                    return [str(item or "").strip() for item in decoded if str(item or "").strip()]
            except Exception:
                return [item.strip() for item in re.split(r"[>/\\\\|]+", path_value) if item.strip()]
        return []

    def _normalize_rule_scope_metadata(self, scope_metadata: Optional[Dict[str, Any]], section_id: str = "") -> Dict[str, str]:
        payload = scope_metadata if isinstance(scope_metadata, dict) else {}
        registration_scope = self._normalize_registration_scope(payload.get("registration_scope"))
        registration_class = str(payload.get("registration_class", "") or "").strip()
        registration_class_sub = str(payload.get("registration_class_sub", "") or "").strip()
        module = self.ctd_sections.normalize_section_id(payload.get("module"))
        section_path_value = payload.get("section_path_prefix") if "section_path_prefix" in payload else section_id
        section_path_prefix = self.ctd_sections.normalize_section_id(section_path_value)
        return {
            "registration_scope": registration_scope,
            "registration_class": registration_class,
            "registration_class_sub": registration_class_sub,
            "module": module,
            "section_path_prefix": section_path_prefix,
        }

    def _build_project_rule_scope_metadata(self, project: Optional[PreReviewProject], section_id: str = "") -> Dict[str, str]:
        if project is None:
            return self._normalize_rule_scope_metadata({}, section_id=section_id)
        registration_path = self._normalize_registration_path(getattr(project, "registration_path", "") or "")
        registration_scope = self._normalize_registration_scope(getattr(project, "registration_scope", "") or "")
        registration_class = registration_path[1] if len(registration_path) >= 2 else ""
        registration_class_sub = registration_path[2] if len(registration_path) >= 3 else ""
        module = self._branch_root_from_section_id(section_id)
        return self._normalize_rule_scope_metadata(
            {
                "registration_scope": registration_scope,
                "registration_class": registration_class,
                "registration_class_sub": registration_class_sub,
                "module": module,
                "section_path_prefix": section_id,
            },
            section_id=section_id,
        )

    def _extract_rule_scope_metadata_from_payload(self, payload: Any, section_id: str = "") -> Dict[str, str]:
        if not isinstance(payload, dict):
            return self._normalize_rule_scope_metadata({}, section_id=section_id)
        scope_payload = payload.get("scope_metadata", {}) if isinstance(payload.get("scope_metadata", {}), dict) else payload
        return self._normalize_rule_scope_metadata(scope_payload, section_id=section_id)

    @staticmethod
    def _build_rule_scope_key(scope_metadata: Dict[str, str]) -> str:
        normalized = {
            "registration_scope": str(scope_metadata.get("registration_scope", "") or "").strip(),
            "registration_class": str(scope_metadata.get("registration_class", "") or "").strip(),
            "registration_class_sub": str(scope_metadata.get("registration_class_sub", "") or "").strip(),
            "module": str(scope_metadata.get("module", "") or "").strip(),
            "section_path_prefix": str(scope_metadata.get("section_path_prefix", "") or "").strip(),
        }
        raw = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]

    @staticmethod
    def _scope_without_section_prefix(scope_metadata: Optional[Dict[str, Any]]) -> Dict[str, str]:
        payload = scope_metadata if isinstance(scope_metadata, dict) else {}
        return {
            "registration_scope": str(payload.get("registration_scope", "") or "").strip(),
            "registration_class": str(payload.get("registration_class", "") or "").strip(),
            "registration_class_sub": str(payload.get("registration_class_sub", "") or "").strip(),
            "module": str(payload.get("module", "") or "").strip(),
        }

    def _scope_group_matches(self, rule_scope: Optional[Dict[str, Any]], target_scope: Optional[Dict[str, Any]]) -> bool:
        normalized_rule_scope = self._scope_without_section_prefix(self._normalize_rule_scope_metadata(rule_scope or {}))
        normalized_target_scope = self._scope_without_section_prefix(self._normalize_rule_scope_metadata(target_scope or {}))
        return normalized_rule_scope == normalized_target_scope

    def _scope_module_section_ids(self, module_section_id: str) -> List[str]:
        module_key = self.ctd_sections.normalize_section_id(module_section_id)
        if not module_key:
            return []
        catalog = self.ctd_sections.get_catalog()
        all_sections = catalog.get("all_sections", []) if isinstance(catalog.get("all_sections", []), list) else []
        matched: List[str] = []
        for item in all_sections:
            if not isinstance(item, dict):
                continue
            section_id = self.ctd_sections.normalize_section_id(item.get("section_id"))
            if not section_id:
                continue
            if section_id == module_key or section_id.startswith(f"{module_key}."):
                matched.append(section_id)
        return matched

    def _scope_selector_matches_rule(
        self,
        selector_scope: Dict[str, str],
        rule_scope: Dict[str, str],
        *,
        row_section_id: str,
        allowed_section_ids: Optional[set] = None,
    ) -> bool:
        normalized_selector = self._normalize_rule_scope_metadata(selector_scope or {})
        normalized_rule = self._normalize_rule_scope_metadata(rule_scope or {}, section_id=row_section_id)
        normalized_row_section_id = self.ctd_sections.normalize_section_id(row_section_id)
        if allowed_section_ids is not None and normalized_row_section_id not in allowed_section_ids:
            return False
        for key in ["registration_scope", "registration_class", "registration_class_sub"]:
            expected = str(normalized_selector.get(key, "") or "").strip()
            actual = str(normalized_rule.get(key, "") or "").strip()
            if not expected:
                continue
            if actual and actual != expected:
                return False
        selector_module = self.ctd_sections.normalize_section_id(normalized_selector.get("module"))
        rule_module = self.ctd_sections.normalize_section_id(normalized_rule.get("module"))
        if selector_module:
            if normalized_row_section_id and not (
                normalized_row_section_id == selector_module or normalized_row_section_id.startswith(f"{selector_module}.")
            ):
                return False
            if rule_module and not (
                rule_module == selector_module
                or selector_module.startswith(f"{rule_module}.")
                or rule_module.startswith(f"{selector_module}.")
            ):
                return False
        return True

    def _rule_scope_matches(
        self,
        rule_scope: Dict[str, str],
        target_scope: Dict[str, str],
        *,
        allow_generic: bool = True,
    ) -> bool:
        normalized_rule_scope = self._normalize_rule_scope_metadata(rule_scope)
        normalized_target_scope = self._normalize_rule_scope_metadata(target_scope)
        for key in ["registration_scope", "registration_class", "registration_class_sub", "module"]:
            expected = str(normalized_target_scope.get(key, "") or "").strip()
            actual = str(normalized_rule_scope.get(key, "") or "").strip()
            if not expected:
                continue
            if not actual:
                if allow_generic:
                    continue
                return False
            if key == "module":
                if not (
                    actual == expected
                    or expected.startswith(f"{actual}.")
                    or actual.startswith(f"{expected}.")
                ):
                    return False
                continue
            if actual != expected:
                return False
        expected_prefix = str(normalized_target_scope.get("section_path_prefix", "") or "").strip()
        actual_prefix = str(normalized_rule_scope.get("section_path_prefix", "") or "").strip()
        if expected_prefix and actual_prefix and not expected_prefix.startswith(actual_prefix):
            return False
        if expected_prefix and not actual_prefix and not allow_generic:
            return False
        return True

    @classmethod
    def _is_ctd_structure_project(cls, project: PreReviewProject) -> bool:
        normalized_scope = cls._normalize_registration_scope(getattr(project, "registration_scope", "") or "")
        return normalized_scope in {"化药", "化学药"}

    @staticmethod
    def _normalize_review_domain_code(value: Any) -> str:
        text = str(value or "").strip()
        lowered = text.lower()
        if not text:
            return ""
        if "药学" in text or lowered in {"pharmacy", "chemistry"}:
            return "pharmacy"
        if ("临床" in text and "非" not in text) or lowered == "clinical":
            return "clinical"
        if "非临床" in text or lowered in {"nonclinical", "non_clinical"}:
            return "nonclinical"
        if text == "Ò©Ñ§":
            return "pharmacy"
        return ""

    @staticmethod
    def _review_domain_label(code: str) -> str:
        mapping = {
            "pharmacy": "药学",
            "clinical": "临床",
            "nonclinical": "非临床",
        }
        return mapping.get(str(code or "").strip().lower(), str(code or "").strip())

    @staticmethod
    def _is_pharmacy_material_category(material_category: Any) -> bool:
        return PreReviewService._normalize_review_domain_code(material_category) == "pharmacy"

    def _default_branch_root_for_review_domain(self, review_domain: Any) -> str:
        normalized = self._normalize_review_domain_code(review_domain)
        preferred_by_domain = {
            "pharmacy": "3.2",
            "nonclinical": "4",
            "clinical": "5",
        }
        preferred_root = preferred_by_domain.get(normalized, "")
        if preferred_root and self.ctd_sections.get_section(preferred_root):
            return preferred_root
        return self.ctd_sections.default_branch_root()

    def _normalize_ctd_upload_section_id(self, section_id: str) -> str:
        value = self.ctd_sections.normalize_section_id(section_id)
        if not value:
            return ""
        if value == "3" and self.ctd_sections.get_section("3.2"):
            return "3.2"
        return value


    def _branch_root_from_section_id(self, section_id: str) -> str:
        value = self._normalize_ctd_upload_section_id(section_id)
        if not value:
            return ""
        section_meta = self.ctd_sections.get_section(value)
        if not section_meta:
            inferred_section_id = self.ctd_sections.infer_section_id_from_path(value, leaf_only=False)
            if inferred_section_id:
                section_meta = self.ctd_sections.get_section(inferred_section_id)
        if isinstance(section_meta, dict):
            children = section_meta.get("children_sections") or []
            parent_section_id = str(section_meta.get("parent_section_id", "") or "").strip()
            node_level = int(section_meta.get("node_level", 0) or 0)
            if value == "3.2":
                return "3.2"
            if node_level <= 2:
                return value
            if isinstance(children, list) and children:
                return value
            if parent_section_id:
                return self.ctd_sections.normalize_section_id(parent_section_id)
            if self.ctd_sections.normalize_section_id(section_meta.get("section_id")) == value:
                return value
        return value if self.ctd_sections.get_section(value) else ""

    def _is_supported_ctd_split_root(self, section_id: str) -> bool:
        normalized = self._normalize_ctd_upload_section_id(section_id)
        if not normalized:
            return False
        section_meta = self.ctd_sections.get_section(normalized, leaf_only=False)
        if isinstance(section_meta, dict):
            node_level = int(section_meta.get("node_level", 0) or 0)
            has_children = bool(section_meta.get("children_sections") or [])
            return node_level <= 2 or has_children or not str(section_meta.get("parent_section_id", "") or "").strip()
        return normalized in {"1", "2", "3", "4", "5", "3.2"}

    def _infer_submission_branch_root(
        self,
        material_category: str,
        explicit_section_id: str,
        section_meta: Optional[Dict[str, Any]] = None,
        display_name: str = "",
        relative_path: str = "",
    ) -> str:
        for candidate in [
            explicit_section_id,
            str((section_meta or {}).get("section_id", "") or ""),
            material_category,
            relative_path,
            display_name,
        ]:
            root = self._branch_root_from_section_id(candidate)
            if root:
                return root
        material_text = str(material_category or "")
        lowered = material_text.strip().lower()
        if "api" in lowered or "原料" in material_text:
            return "3.2"
        if "fpp" in lowered or "制剂" in material_text:
            return "3.2"
        return self._default_branch_root_for_review_domain(material_category)

    @staticmethod
    def _normalize_path_tokens(raw_path: str) -> List[str]:
        path = str(raw_path or "").replace("\\", "/").strip().strip("/")
        if not path:
            return []
        tokens: List[str] = []
        for item in path.split("/"):
            value = str(item or "").strip()
            if not value:
                continue
            stem = value.rsplit(".", 1)[0] if "." in value else value
            if stem:
                tokens.append(stem.lower())
            tokens.append(value.lower())
        return tokens

    @staticmethod
    def _extract_ctd_section_candidates(raw_text: Any) -> List[str]:
        text = str(raw_text or "").replace("\\", "/").strip()
        if not text:
            return []
        # Accept CTD codes from parent to deep leaf, e.g. 3.2.p / 3.2.p.1 / 3.2.p.1.1.1
        pattern = re.compile(r"3\.2\.[A-Za-z](?:\.[A-Za-z0-9_-]+){0,8}", flags=re.IGNORECASE)
        matches = pattern.findall(text)
        if not matches:
            return []
        known_ext = {
            "pdf",
            "doc",
            "docx",
            "md",
            "txt",
            "rtf",
            "xml",
            "json",
            "xls",
            "xlsx",
            "csv",
            "png",
            "jpg",
            "jpeg",
            "tif",
            "tiff",
            "zip",
        }
        out: List[str] = []
        for item in matches:
            normalized = CTDSectionService.normalize_section_id(item)
            if not normalized:
                continue
            parts = [part for part in normalized.split(".") if part]
            while len(parts) > 3 and parts[-1].lower() in known_ext:
                parts.pop()
            if len(parts) < 3:
                continue
            out.append(".".join(parts))
        if not out:
            return []
        deduped = list(dict.fromkeys(out))
        deduped.sort(key=lambda x: (len(x.split(".")), len(x)), reverse=True)
        return deduped

    @staticmethod
    def _dynamic_section_name_from_id(section_id: str) -> str:
        sid = str(section_id or "").strip()
        if not sid:
            return ""
        return sid.rsplit(".", 1)[-1].strip() or sid

    def _upsert_dynamic_project_section(
        self,
        session,
        project_id: str,
        section_id: str,
        section_name_hint: str = "",
    ) -> Optional[Dict[str, Any]]:
        project_key = str(project_id or "").strip()
        target_section_id = self.ctd_sections.normalize_section_id(section_id)
        if not project_key or not target_section_id:
            return None
        target_lower = target_section_id.lower()

        def _pending_row(normalized_section_id: str):
            target_norm = self.ctd_sections.normalize_section_id(normalized_section_id)
            for row in list(getattr(session, "new", []) or []):
                if not isinstance(row, PreReviewProjectSection):
                    continue
                if str(getattr(row, "project_id", "") or "").strip() != project_key:
                    continue
                row_sid = self.ctd_sections.normalize_section_id(getattr(row, "section_id", ""))
                if row_sid and row_sid == target_norm:
                    return row
            return None

        catalog = self._load_project_section_catalog(session, project_key)
        section_map = catalog.get("section_map", {}) if isinstance(catalog.get("section_map", {}), dict) else {}
        if target_section_id in section_map:
            return section_map.get(target_section_id, {})
        pending_target = _pending_row(target_section_id)
        if pending_target is not None:
            return {
                "section_id": target_section_id,
                "section_code": str(getattr(pending_target, "section_code", "") or target_section_id).strip() or target_section_id,
                "section_name": str(getattr(pending_target, "section_name", "") or target_section_id).strip() or target_section_id,
                "title_path": self._parse_json_list(getattr(pending_target, "title_path", "") or ""),
                "concern_points": self._parse_json_list(getattr(pending_target, "concern_points", "") or ""),
                "root_section_id": self.ctd_sections.normalize_section_id(getattr(pending_target, "root_section_id", "")) or "",
                "parent_section_id": self.ctd_sections.normalize_section_id(getattr(pending_target, "parent_section_id", "")) or "",
                "node_level": int(getattr(pending_target, "node_level", 0) or 0),
                "sort_order": int(getattr(pending_target, "sort_order", 0) or 0),
                "is_leaf": bool(getattr(pending_target, "is_leaf", True)),
                "children_sections": [],
            }
        existed_target_row = (
            session.query(PreReviewProjectSection)
            .filter(
                PreReviewProjectSection.project_id == project_key,
                func.lower(PreReviewProjectSection.section_id) == target_lower,
            )
            .first()
        )
        if existed_target_row is not None:
            return {
                "section_id": target_section_id,
                "section_code": str(getattr(existed_target_row, "section_code", "") or target_section_id).strip() or target_section_id,
                "section_name": str(getattr(existed_target_row, "section_name", "") or target_section_id).strip() or target_section_id,
                "title_path": self._parse_json_list(getattr(existed_target_row, "title_path", "") or ""),
                "concern_points": self._parse_json_list(getattr(existed_target_row, "concern_points", "") or ""),
                "root_section_id": self.ctd_sections.normalize_section_id(getattr(existed_target_row, "root_section_id", "")) or "",
                "parent_section_id": self.ctd_sections.normalize_section_id(getattr(existed_target_row, "parent_section_id", "")) or "",
                "node_level": int(getattr(existed_target_row, "node_level", 0) or 0),
                "sort_order": int(getattr(existed_target_row, "sort_order", 0) or 0),
                "is_leaf": bool(getattr(existed_target_row, "is_leaf", True)),
                "children_sections": [],
            }

        parts = target_section_id.split(".")
        if len(parts) < 3 or parts[0] != "3" or parts[1] != "2":
            return None

        anchor = ""
        for size in range(len(parts) - 1, 0, -1):
            candidate = ".".join(parts[:size])
            if candidate in section_map:
                anchor = candidate
                break
        if not anchor:
            return None

        now = self._now()
        parent_section_id = anchor
        for size in range(len(anchor.split(".")) + 1, len(parts) + 1):
            current_id = ".".join(parts[:size])
            if current_id in section_map:
                parent_section_id = current_id
                continue
            current_lower = current_id.lower()
            pending_row = _pending_row(current_id)
            if pending_row is not None:
                resolved_id = self.ctd_sections.normalize_section_id(getattr(pending_row, "section_id", "")) or current_id
                section_map[current_id] = {
                    "section_id": resolved_id,
                    "section_code": str(getattr(pending_row, "section_code", "") or resolved_id).strip() or resolved_id,
                    "section_name": str(getattr(pending_row, "section_name", "") or resolved_id).strip() or resolved_id,
                    "title_path": self._parse_json_list(getattr(pending_row, "title_path", "") or ""),
                    "concern_points": self._parse_json_list(getattr(pending_row, "concern_points", "") or ""),
                    "root_section_id": self.ctd_sections.normalize_section_id(getattr(pending_row, "root_section_id", "")) or "",
                    "parent_section_id": self.ctd_sections.normalize_section_id(getattr(pending_row, "parent_section_id", "")) or "",
                    "node_level": int(getattr(pending_row, "node_level", len(current_id.split(".")) - 1) or 0),
                    "sort_order": int(getattr(pending_row, "sort_order", 1) or 1),
                    "is_leaf": bool(getattr(pending_row, "is_leaf", True)),
                    "children_sections": [],
                }
                parent_section_id = current_id
                continue
            existed_row = (
                session.query(PreReviewProjectSection)
                .filter(
                    PreReviewProjectSection.project_id == project_key,
                    func.lower(PreReviewProjectSection.section_id) == current_lower,
                )
                .first()
            )
            if existed_row is not None:
                resolved_id = self.ctd_sections.normalize_section_id(getattr(existed_row, "section_id", "")) or current_id
                section_map[current_id] = {
                    "section_id": resolved_id,
                    "section_code": str(getattr(existed_row, "section_code", "") or resolved_id).strip() or resolved_id,
                    "section_name": str(getattr(existed_row, "section_name", "") or resolved_id).strip() or resolved_id,
                    "title_path": self._parse_json_list(getattr(existed_row, "title_path", "") or ""),
                    "concern_points": self._parse_json_list(getattr(existed_row, "concern_points", "") or ""),
                    "root_section_id": self.ctd_sections.normalize_section_id(getattr(existed_row, "root_section_id", "")) or "",
                    "parent_section_id": self.ctd_sections.normalize_section_id(getattr(existed_row, "parent_section_id", "")) or "",
                    "node_level": int(getattr(existed_row, "node_level", len(current_id.split(".")) - 1) or 0),
                    "sort_order": int(getattr(existed_row, "sort_order", 1) or 1),
                    "is_leaf": bool(getattr(existed_row, "is_leaf", True)),
                    "children_sections": [],
                }
                parent_section_id = current_id
                continue

            parent_meta = section_map.get(parent_section_id, {}) if isinstance(section_map.get(parent_section_id, {}), dict) else {}
            parent_title_path = [
                str(item or "").strip()
                for item in (parent_meta.get("title_path") or [])
                if str(item or "").strip()
            ]
            default_name = self._dynamic_section_name_from_id(current_id)
            section_name = (
                str(section_name_hint or "").strip()
                if size == len(parts) and str(section_name_hint or "").strip()
                else default_name
            )
            sibling_sort_orders = [
                int(item.get("sort_order", 0) or 0)
                for item in section_map.values()
                if isinstance(item, dict)
                and str(item.get("parent_section_id", "") or "").strip() == parent_section_id
            ]
            sort_order = (max(sibling_sort_orders) + 1) if sibling_sort_orders else 1
            root_section_id = (
                str(parent_meta.get("root_section_id", "") or "").strip()
                or self._branch_root_from_section_id(parent_section_id)
                or parent_section_id
            )
            title_path = parent_title_path + [section_name]

            session.add(
                PreReviewProjectSection(
                    project_id=project_key,
                    section_id=current_id,
                    section_code=current_id,
                    section_name=section_name,
                    root_section_id=root_section_id,
                    parent_section_id=parent_section_id,
                    node_level=len(current_id.split(".")) - 1,
                    sort_order=sort_order,
                    is_leaf=True,
                    title_path=self._safe_json_list(title_path),
                    concern_points=self._safe_json_list([]),
                    create_time=now,
                    update_time=now,
                )
            )
            parent_row = (
                session.query(PreReviewProjectSection)
                .filter(
                    PreReviewProjectSection.project_id == project_key,
                    PreReviewProjectSection.section_id == parent_section_id,
                )
                .first()
            )
            if parent_row is not None and bool(getattr(parent_row, "is_leaf", True)):
                parent_row.is_leaf = False
                parent_row.update_time = now

            section_map[current_id] = {
                "section_id": current_id,
                "section_code": current_id,
                "section_name": section_name,
                "title_path": title_path,
                "concern_points": [],
                "root_section_id": root_section_id,
                "parent_section_id": parent_section_id,
                "node_level": len(current_id.split(".")) - 1,
                "sort_order": sort_order,
                "is_leaf": True,
                "children_sections": [],
            }
            if isinstance(parent_meta, dict):
                parent_meta["is_leaf"] = False
            parent_section_id = current_id

        return section_map.get(target_section_id, {})

    def _match_catalog_section_from_archive_path(
        self,
        archive_path: str,
        catalog: Dict[str, Any],
        branch_root: str = "",
    ) -> Optional[Dict[str, Any]]:
        section_map = catalog.get("section_map", {}) if isinstance(catalog.get("section_map", {}), dict) else {}
        section_id = self.ctd_sections.infer_section_id_from_path(archive_path, leaf_only=False)
        if section_id and section_id in section_map:
            section_meta = section_map.get(section_id, {})
            if not branch_root or str(section_meta.get("root_section_id", "") or "").strip() in {"", branch_root} or section_id == branch_root:
                return section_meta

        leaf_map = catalog.get("leaf_section_map", {}) if isinstance(catalog.get("leaf_section_map", {}), dict) else {}
        if not section_id:
            section_id = self.ctd_sections.infer_section_id_from_path(archive_path, leaf_only=True)
            if section_id and section_id in leaf_map:
                section_meta = leaf_map.get(section_id, {})
                if not branch_root or str(section_meta.get("root_section_id", "") or "").strip() == branch_root:
                    return section_meta

        tokens = self._normalize_path_tokens(archive_path)
        if not tokens:
            return None
        root_name_tokens = {
            str(node.get("section_name", "") or "").strip().lower()
            for node in (catalog.get("chapter_structure", []) or [])
            if isinstance(node, dict) and str(node.get("section_name", "") or "").strip()
        }
        explicit_root_names = {name for name in root_name_tokens if name in tokens}
        candidates = []
        for item in leaf_map.values():
            if not isinstance(item, dict):
                continue
            root_section_id = str(item.get("root_section_id", "") or "").strip()
            if branch_root and root_section_id != branch_root:
                continue
            title_path = [str(x or "").strip().lower() for x in (item.get("title_path") or []) if str(x or "").strip()]
            if explicit_root_names and title_path and title_path[0] not in explicit_root_names:
                continue
            section_id = str(item.get("section_id", "") or "").strip()
            section_name = str(item.get("section_name", "") or "").strip().lower()
            score = 0
            if section_id and section_id.lower() in tokens:
                score += 100
            if section_name and section_name in tokens:
                score += 30
            if title_path:
                matched = 0
                cursor = 0
                for name in title_path:
                    try:
                        idx = tokens.index(name, cursor)
                        matched += 1
                        cursor = idx + 1
                    except ValueError:
                        break
                if matched == len(title_path):
                    score += 20 + len(title_path) * 10
                elif matched >= 2:
                    score += matched * 8
            if score > 0:
                candidates.append((score, len(title_path), item))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return candidates[0][2]

    def _resolve_catalog_section_id(self, section_value: Any) -> str:
        candidate = self.ctd_sections.normalize_section_id(section_value)
        if not candidate:
            return ""
        if self.ctd_sections.get_section(candidate):
            return candidate
        match = re.search(r"3\.2\.[A-Za-z](?:\.[A-Za-z0-9_-]+){0,6}", candidate, re.IGNORECASE)
        if not match:
            return ""
        normalized = self.ctd_sections.normalize_section_id(match.group(0))
        if self.ctd_sections.get_section(normalized):
            return normalized
        return ""

    def _resolve_or_mount_dynamic_project_section(
        self,
        session,
        project_id: str,
        *,
        explicit_section_id: str = "",
        relative_path: str = "",
        display_name: str = "",
        section_meta: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        current_meta = section_meta if isinstance(section_meta, dict) else {}
        current_section_id = self.ctd_sections.normalize_section_id(current_meta.get("section_id"))
        if current_section_id:
            return current_meta

        section_name_hint = str(current_meta.get("section_name", "") or "").strip()
        candidates: List[str] = []
        for source in [explicit_section_id, relative_path, display_name]:
            candidates.extend(self._extract_ctd_section_candidates(source))
        candidates = list(dict.fromkeys([item for item in candidates if str(item or "").strip()]))
        for candidate in candidates:
            mounted = self._upsert_dynamic_project_section(
                session=session,
                project_id=project_id,
                section_id=candidate,
                section_name_hint=section_name_hint,
            )
            if isinstance(mounted, dict) and str(mounted.get("section_id", "") or "").strip():
                return mounted
        return {}

    def _collect_dynamic_sections_from_payload(self, payload: Any) -> List[Dict[str, str]]:
        out: List[Dict[str, str]] = []
        seen = set()

        def add_item(section_id: Any, section_name: Any = "") -> None:
            normalized_section_id = self.ctd_sections.normalize_section_id(section_id)
            if not normalized_section_id:
                return
            parts = normalized_section_id.split(".")
            if len(parts) < 3 or not normalized_section_id.startswith("3.2."):
                return
            if normalized_section_id in seen:
                return
            seen.add(normalized_section_id)
            out.append(
                {
                    "section_id": normalized_section_id,
                    "section_name": str(section_name or "").strip(),
                }
            )

        if isinstance(payload, dict):
            detected_items = payload.get("detected_ctd_sections", [])
            if isinstance(detected_items, list):
                for item in detected_items:
                    if not isinstance(item, dict):
                        continue
                    add_item(item.get("section_id"), item.get("section_name"))
            for key in ["sections", "review_units"]:
                values = payload.get(key, [])
                if not isinstance(values, list):
                    continue
                for item in values:
                    if not isinstance(item, dict):
                        continue
                    add_item(
                        item.get("section_id") or item.get("section_code") or item.get("code"),
                        item.get("section_name") or item.get("title"),
                    )
                    # Fallback: extract inline CTD section ids from parsed text blocks.
                    for text_key in ["title", "section_name", "content", "text", "content_preview"]:
                        for candidate in self._extract_ctd_section_candidates(item.get(text_key)):
                            add_item(candidate, "")
        return out

    @staticmethod
    def _chemistry_branch_root_from_id(section_id: Any) -> str:
        normalized = CTDSectionService.normalize_section_id(section_id)
        parts = [part for part in normalized.split(".") if part]
        if len(parts) >= 3 and parts[0] == "3" and parts[1] == "2" and parts[2] in {"s", "p", "a", "r"}:
            return ".".join(parts[:3])
        if normalized == "3.2":
            return normalized
        return ""

    def _collect_project_tree_nodes_from_payload(self, payload: Any) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        base_catalog = self.ctd_sections.get_catalog()
        base_section_map = (
            base_catalog.get("section_map", {})
            if isinstance(base_catalog.get("section_map", {}), dict)
            else {}
        )

        def add_node(section_id: Any, section_name: Any = "", parent_section_id: Any = "") -> None:
            normalized_section_id = self.ctd_sections.normalize_section_id(section_id)
            if not normalized_section_id or not normalized_section_id.startswith("3.2."):
                return
            branch_root = self._chemistry_branch_root_from_id(normalized_section_id)
            if not branch_root:
                return
            base_meta = base_section_map.get(normalized_section_id, {}) if isinstance(base_section_map.get(normalized_section_id, {}), dict) else {}
            parts = [part for part in normalized_section_id.split(".") if part]
            node = out.setdefault(
                normalized_section_id,
                {
                    "section_id": normalized_section_id,
                    "section_name": "",
                    "parent_section_id": "",
                    "root_section_id": branch_root,
                    "node_level": max(0, len(parts) - 1),
                },
            )
            resolved_name = (
                str(section_name or "").strip()
                or str(base_meta.get("section_name", "") or "").strip()
                or self._dynamic_section_name_from_id(normalized_section_id)
            )
            resolved_parent = (
                self.ctd_sections.normalize_section_id(parent_section_id)
                or self.ctd_sections.normalize_section_id(base_meta.get("parent_section_id"))
                or ".".join(parts[:-1])
            )
            if not node.get("section_name") or base_meta:
                node["section_name"] = resolved_name
            if resolved_parent:
                node["parent_section_id"] = resolved_parent
            node["root_section_id"] = (
                self._chemistry_branch_root_from_id(normalized_section_id)
                or str(base_meta.get("root_section_id", "") or "").strip()
                or branch_root
            )
            node["node_level"] = int(node.get("node_level", max(0, len(parts) - 1)) or 0)

            for size in range(3, len(parts)):
                ancestor_id = ".".join(parts[:size])
                ancestor_meta = base_section_map.get(ancestor_id, {}) if isinstance(base_section_map.get(ancestor_id, {}), dict) else {}
                ancestor_parts = [part for part in ancestor_id.split(".") if part]
                ancestor_branch_root = self._chemistry_branch_root_from_id(ancestor_id) or branch_root
                ancestor_node = out.setdefault(
                    ancestor_id,
                    {
                        "section_id": ancestor_id,
                        "section_name": "",
                        "parent_section_id": "",
                        "root_section_id": ancestor_branch_root,
                        "node_level": max(0, len(ancestor_parts) - 1),
                    },
                )
                if not ancestor_node.get("section_name"):
                    ancestor_node["section_name"] = (
                        str(ancestor_meta.get("section_name", "") or "").strip()
                        or self._dynamic_section_name_from_id(ancestor_id)
                    )
                if not ancestor_node.get("parent_section_id"):
                    ancestor_node["parent_section_id"] = (
                        self.ctd_sections.normalize_section_id(ancestor_meta.get("parent_section_id"))
                        or ".".join(ancestor_parts[:-1])
                    )

        def walk_tree(nodes: List[Dict[str, Any]], inherited_parent: str = "") -> None:
            for node in nodes or []:
                if not isinstance(node, dict):
                    continue
                sid = node.get("section_id")
                parent_id = node.get("parent_section_id") or inherited_parent
                add_node(sid, node.get("section_name") or node.get("title"), parent_id)
                walk_tree(node.get("children_sections") or [], sid if sid else inherited_parent)

        if isinstance(payload, dict):
            detected_items = payload.get("detected_ctd_sections", [])
            if isinstance(detected_items, list):
                for item in detected_items:
                    if not isinstance(item, dict):
                        continue
                    add_node(item.get("section_id"), item.get("section_name"), item.get("parent_section_id"))
            for key in ["sections", "review_units"]:
                values = payload.get(key, [])
                if not isinstance(values, list):
                    continue
                for item in values:
                    if not isinstance(item, dict):
                        continue
                    add_node(
                        item.get("section_id") or item.get("section_code") or item.get("code"),
                        item.get("section_name") or item.get("title"),
                        item.get("parent_section_id"),
                    )
            walk_tree(payload.get("chapter_structure", []) if isinstance(payload.get("chapter_structure", []), list) else [])
        return out

    def _rebuild_project_ctd_sections_from_payload(
        self,
        session,
        project_id: str,
        payload: Any,
        *,
        prune_missing_dynamic: bool = False,
    ) -> Dict[str, int]:
        project_key = str(project_id or "").strip()
        if not project_key:
            return {"created": 0, "updated": 0, "deleted": 0}
        self._invalidate_structured_project_payload_cache(project_id=project_key)
        self._invalidate_project_section_catalog_cache(project_id=project_key)

        desired_nodes = self._collect_project_tree_nodes_from_payload(payload)
        if not desired_nodes:
            return {"created": 0, "updated": 0, "deleted": 0}

        base_catalog = self.ctd_sections.get_catalog()
        base_section_map = (
            base_catalog.get("section_map", {})
            if isinstance(base_catalog.get("section_map", {}), dict)
            else {}
        )
        affected_roots = {
            self._chemistry_branch_root_from_id(item.get("section_id"))
            for item in desired_nodes.values()
            if isinstance(item, dict) and self._chemistry_branch_root_from_id(item.get("section_id"))
        }
        affected_roots = {item for item in affected_roots if item in {"3.2.s", "3.2.p", "3.2.a", "3.2.r"}}
        if not affected_roots:
            return {"created": 0, "updated": 0, "deleted": 0}

        all_rows = (
            session.query(PreReviewProjectSection)
            .filter(PreReviewProjectSection.project_id == project_key)
            .all()
        )
        row_map: Dict[str, PreReviewProjectSection] = {}
        for row in all_rows:
            sid = self.ctd_sections.normalize_section_id(getattr(row, "section_id", ""))
            if sid:
                row_map[sid] = row

        desired_dynamic_ids = {
            sid
            for sid in desired_nodes
            if sid not in base_section_map and self._chemistry_branch_root_from_id(sid) in affected_roots
        }
        deleted = 0
        if prune_missing_dynamic:
            stale_rows: List[PreReviewProjectSection] = []
            for sid, row in list(row_map.items()):
                branch_root = self._chemistry_branch_root_from_id(sid)
                if branch_root not in affected_roots:
                    continue
                if sid in base_section_map:
                    continue
                if sid in desired_dynamic_ids:
                    continue
                stale_rows.append(row)
            for row in stale_rows:
                sid = self.ctd_sections.normalize_section_id(getattr(row, "section_id", ""))
                session.delete(row)
                deleted += 1
                if sid in row_map:
                    row_map.pop(sid, None)
            if stale_rows:
                session.flush()

        current_catalog = self._load_project_section_catalog(session, project_key)
        current_section_map = (
            current_catalog.get("section_map", {})
            if isinstance(current_catalog.get("section_map", {}), dict)
            else {}
        )
        current_meta_map: Dict[str, Dict[str, Any]] = {
            self.ctd_sections.normalize_section_id(key): value
            for key, value in current_section_map.items()
            if self.ctd_sections.normalize_section_id(key)
        }

        child_map: Dict[str, List[str]] = {}
        for sid, meta in desired_nodes.items():
            parent_id = self.ctd_sections.normalize_section_id(meta.get("parent_section_id"))
            if parent_id:
                child_map.setdefault(parent_id, []).append(sid)

        created = 0
        updated = 0
        now = self._now()
        ordered_ids = sorted(
            desired_dynamic_ids,
            key=lambda item: (len(item.split(".")), item),
        )

        for sid in ordered_ids:
            desired_meta = desired_nodes.get(sid, {})
            parent_id = self.ctd_sections.normalize_section_id(desired_meta.get("parent_section_id"))
            parent_meta = current_meta_map.get(parent_id, {}) if parent_id else {}
            base_meta = base_section_map.get(sid, {}) if isinstance(base_section_map.get(sid, {}), dict) else {}
            parent_title_path = [
                str(item).strip()
                for item in (
                    parent_meta.get("title_path", [])
                    if isinstance(parent_meta.get("title_path", []), list)
                    else []
                )
                if str(item).strip()
            ]
            section_name = (
                str(desired_meta.get("section_name", "") or "").strip()
                or str(base_meta.get("section_name", "") or "").strip()
                or self._dynamic_section_name_from_id(sid)
            )
            title_path = parent_title_path + [section_name]
            last_token = sid.rsplit(".", 1)[-1]
            if str(last_token).isdigit():
                sort_order = int(last_token)
            else:
                existing_sort = int(parent_meta.get("sort_order", 0) or 0)
                sibling_sorts = [
                    int(meta.get("sort_order", 0) or 0)
                    for meta in current_meta_map.values()
                    if isinstance(meta, dict)
                    and self.ctd_sections.normalize_section_id(meta.get("parent_section_id")) == parent_id
                ]
                sort_order = max(sibling_sorts or [existing_sort, 0]) + 1
            root_section_id = (
                self._chemistry_branch_root_from_id(sid)
                or str(parent_meta.get("root_section_id", "") or "").strip()
                or self._branch_root_from_section_id(parent_id)
                or sid
            )
            is_leaf = not bool(child_map.get(sid))
            row = row_map.get(sid)
            if row is None:
                row = PreReviewProjectSection(
                    project_id=project_key,
                    section_id=sid,
                    section_code=sid,
                    section_name=section_name,
                    root_section_id=root_section_id,
                    parent_section_id=parent_id,
                    node_level=int(desired_meta.get("node_level", len(sid.split(".")) - 1) or 0),
                    sort_order=sort_order,
                    is_leaf=is_leaf,
                    title_path=self._safe_json_list(title_path),
                    concern_points=self._safe_json_list([]),
                    create_time=now,
                    update_time=now,
                )
                session.add(row)
                row_map[sid] = row
                created += 1
            else:
                changed = False
                for attr, value in [
                    ("section_code", sid),
                    ("section_name", section_name),
                    ("root_section_id", root_section_id),
                    ("parent_section_id", parent_id),
                    ("node_level", int(desired_meta.get("node_level", len(sid.split(".")) - 1) or 0)),
                    ("sort_order", sort_order),
                    ("is_leaf", is_leaf),
                    ("title_path", self._safe_json_list(title_path)),
                ]:
                    if getattr(row, attr) != value:
                        setattr(row, attr, value)
                        changed = True
                if changed:
                    row.update_time = now
                    updated += 1
            current_meta_map[sid] = {
                "section_id": sid,
                "section_name": section_name,
                "root_section_id": root_section_id,
                "parent_section_id": parent_id,
                "node_level": int(desired_meta.get("node_level", len(sid.split(".")) - 1) or 0),
                "sort_order": sort_order,
                "title_path": title_path,
                "is_leaf": is_leaf,
            }
            if parent_id:
                parent_row = row_map.get(parent_id)
                if parent_row is not None and bool(getattr(parent_row, "is_leaf", True)):
                    parent_row.is_leaf = False
                    parent_row.update_time = now

        return {"created": created, "updated": updated, "deleted": deleted}

    def _replace_project_ctd_sections(
        self,
        session,
        project_id: str,
        desired_nodes: Dict[str, Dict[str, Any]],
    ) -> Dict[str, int]:
        project_key = str(project_id or "").strip()
        if not project_key or not desired_nodes:
            return {"created": 0, "updated": 0, "deleted": 0}

        base_catalog = self.ctd_sections.get_catalog()
        base_section_map = (
            base_catalog.get("section_map", {})
            if isinstance(base_catalog.get("section_map", {}), dict)
            else {}
        )
        rows = (
            session.query(PreReviewProjectSection)
            .filter(PreReviewProjectSection.project_id == project_key)
            .all()
        )
        deleted = 0
        for row in rows:
            session.delete(row)
            deleted += 1
        session.flush()

        child_map: Dict[str, List[str]] = {}
        for sid, meta in desired_nodes.items():
            parent_id = self.ctd_sections.normalize_section_id(meta.get("parent_section_id"))
            if parent_id:
                child_map.setdefault(parent_id, []).append(sid)

        sibling_order_counter: Dict[str, int] = {}
        inserted_meta: Dict[str, Dict[str, Any]] = {}
        now = self._now()
        created = 0

        def _sort_order_for_node(section_id: str, parent_id: str, base_meta: Dict[str, Any]) -> int:
            last_token = str(section_id or "").strip().split(".")[-1]
            if isinstance(base_meta, dict) and int(base_meta.get("sort_order", 0) or 0) > 0:
                return int(base_meta.get("sort_order", 0) or 0)
            if last_token.isdigit():
                return int(last_token)
            if section_id in {"3.2.s", "3.2.p", "3.2.a", "3.2.r"}:
                branch_priority = {"3.2.s": 1, "3.2.p": 2, "3.2.a": 3, "3.2.r": 4}
                return branch_priority.get(section_id, 99)
            sibling_order_counter[parent_id] = sibling_order_counter.get(parent_id, 0) + 1
            return sibling_order_counter[parent_id]

        for sid in sorted(desired_nodes.keys(), key=lambda item: (len(item.split(".")), item)):
            desired_meta = desired_nodes.get(sid, {})
            base_meta = base_section_map.get(sid, {}) if isinstance(base_section_map.get(sid, {}), dict) else {}
            parent_id = self.ctd_sections.normalize_section_id(desired_meta.get("parent_section_id"))
            parent_inserted = inserted_meta.get(parent_id, {}) if parent_id else {}
            section_name = (
                str(desired_meta.get("section_name", "") or "").strip()
                or str(base_meta.get("section_name", "") or "").strip()
                or self._dynamic_section_name_from_id(sid)
            )
            title_path = []
            if isinstance(parent_inserted.get("title_path", []), list):
                title_path.extend([str(x).strip() for x in parent_inserted.get("title_path", []) if str(x).strip()])
            elif isinstance(base_meta.get("title_path", []), list):
                title_path.extend([str(x).strip() for x in base_meta.get("title_path", [])[:-1] if str(x).strip()])
            title_path.append(section_name)
            root_section_id = (
                self._chemistry_branch_root_from_id(sid)
                or str(desired_meta.get("root_section_id", "") or "").strip()
                or self._branch_root_from_section_id(parent_id)
                or sid
            )
            sort_order = _sort_order_for_node(sid, parent_id, base_meta)
            node_level = int(desired_meta.get("node_level", max(0, len(sid.split(".")) - 1)) or 0)
            is_leaf = not bool(child_map.get(sid))
            session.add(
                PreReviewProjectSection(
                    project_id=project_key,
                    section_id=sid,
                    section_code=str(desired_meta.get("section_code", "") or sid).strip() or sid,
                    section_name=section_name,
                    root_section_id=root_section_id,
                    parent_section_id=parent_id,
                    node_level=node_level,
                    sort_order=sort_order,
                    is_leaf=is_leaf,
                    title_path=self._safe_json_list(title_path),
                    concern_points=self._safe_json_list([]),
                    create_time=now,
                    update_time=now,
                )
            )
            inserted_meta[sid] = {
                "section_id": sid,
                "section_name": section_name,
                "title_path": title_path,
            }
            created += 1
        return {"created": created, "updated": 0, "deleted": deleted}

    def rebuild_project_sections_from_existing_submissions(self, project_id: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        project_key = str(project_id or "").strip()
        if not project_key:
            return False, "project_id is required", None
        session = self.db_conn.get_session()
        try:
            project = (
                session.query(PreReviewProject)
                .filter(and_(PreReviewProject.project_id == project_key, PreReviewProject.is_deleted == 0))
                .first()
            )
            if project is None:
                return False, "project not found", None
            if not self._is_ctd_structure_project(project):
                return False, "project is not CTD structure", None
            rows = (
                session.query(PreReviewSubmissionFile)
                .filter(
                    and_(
                        PreReviewSubmissionFile.project_id == project_key,
                        PreReviewSubmissionFile.is_deleted == 0,
                    )
                )
                .order_by(PreReviewSubmissionFile.create_time.asc(), PreReviewSubmissionFile.id.asc())
                .all()
            )
            if not rows:
                return False, "project has no submissions", None
            desired_nodes: Dict[str, Dict[str, Any]] = {}
            for row in rows:
                payload = None
                parsed_path = os.path.join(self.SUBMISSION_PARSED_DIR, f"{row.doc_id}.json")
                if os.path.exists(parsed_path):
                    try:
                        with open(parsed_path, "r", encoding="utf-8") as fh:
                            payload = json.load(fh)
                    except Exception:
                        payload = None
                if payload is None:
                    ok_payload, msg, payload = self._load_submission_parsed_payload(project_id=project_key, doc_id=str(row.doc_id))
                    if not ok_payload:
                        return False, msg, None
                payload_nodes = self._collect_project_tree_nodes_from_payload(payload)
                for sid, meta in payload_nodes.items():
                    if sid not in desired_nodes:
                        desired_nodes[sid] = dict(meta)
                        continue
                    existing = desired_nodes[sid]
                    if not str(existing.get("section_name", "") or "").strip() and str(meta.get("section_name", "") or "").strip():
                        existing["section_name"] = str(meta.get("section_name", "") or "").strip()
                    if not str(existing.get("parent_section_id", "") or "").strip() and str(meta.get("parent_section_id", "") or "").strip():
                        existing["parent_section_id"] = str(meta.get("parent_section_id", "") or "").strip()
            if not desired_nodes:
                return False, "no CTD sections detected from submissions", None
            stats = self._replace_project_ctd_sections(session, project_key, desired_nodes)
            session.commit()
            self._submission_payload_cache = {
                key: value
                for key, value in self._submission_payload_cache.items()
                if not str(key or "").startswith(f"{project_key}:")
            }
            return True, "project sections rebuilt", {
                **stats,
                "project_id": project_key,
                "section_count": len(desired_nodes),
                "section_ids": sorted(desired_nodes.keys()),
            }
        except Exception as exc:
            session.rollback()
            return False, str(exc), None
        finally:
            session.close()

    def _mount_project_sections_from_payload(
        self,
        session,
        project_id: str,
        payload: Any,
    ) -> int:
        project_key = str(project_id or "").strip()
        if not project_key:
            return 0
        created = 0
        candidates = self._collect_dynamic_sections_from_payload(payload)
        if not candidates:
            return 0
        existing_catalog = self._load_project_section_catalog(session, project_key)
        existing_ids = set(
            str(item.get("section_id", "") or "").strip()
            for item in (existing_catalog.get("all_sections", []) if isinstance(existing_catalog.get("all_sections", []), list) else [])
            if isinstance(item, dict) and str(item.get("section_id", "") or "").strip()
        )
        for item in candidates:
            section_id = str(item.get("section_id", "") or "").strip()
            if not section_id or section_id in existing_ids:
                continue
            mounted = self._upsert_dynamic_project_section(
                session=session,
                project_id=project_key,
                section_id=section_id,
                section_name_hint=str(item.get("section_name", "") or "").strip(),
            )
            if isinstance(mounted, dict) and str(mounted.get("section_id", "") or "").strip():
                created += 1
                existing_ids.add(section_id)
        return created

    def _leaf_section_ids_by_root(self, root_section_id: str) -> List[str]:
        root = str(root_section_id or "").strip()
        if not root:
            return []
        catalog = self.ctd_sections.get_catalog()
        roots = catalog.get("chapter_structure", []) if isinstance(catalog, dict) else []

        def _find_node(nodes: List[Dict[str, Any]], needle: str) -> Optional[Dict[str, Any]]:
            for node in nodes or []:
                if not isinstance(node, dict):
                    continue
                if str(node.get("section_id", "")).strip() == needle:
                    return node
                found = _find_node(node.get("children_sections") or [], needle)
                if isinstance(found, dict):
                    return found
            return None

        target_root = _find_node(roots, root)
        if not isinstance(target_root, dict):
            target_root = self.ctd_sections.get_section(root, leaf_only=False)
        if not isinstance(target_root, dict):
            return []

        out: List[str] = []

        def walk(nodes: List[Dict[str, Any]]) -> None:
            for node in nodes or []:
                if not isinstance(node, dict):
                    continue
                sid = str(node.get("section_id", "")).strip()
                if not sid:
                    continue
                children = [child for child in (node.get("children_sections") or []) if isinstance(child, dict)]
                if not children:
                    out.append(sid)
                    continue
                walk(children)

        walk(target_root.get("children_sections") or [])
        if not out:
            out.append(root)
        return out

    def _ctd_payload_leaf_unit_count(self, payload: Any, root_section_id: str) -> int:
        leaf_ids = set(self._leaf_section_ids_by_root(root_section_id))
        if not leaf_ids:
            return 0
        section_rows = self._extract_section_content_rows_from_payload(payload)
        count = 0
        for row in section_rows:
            if not isinstance(row, dict):
                continue
            sid = str(row.get("section_id", "")).strip()
            text = str(row.get("content", "")).strip()
            if sid in leaf_ids and text:
                count += 1
        return count

    def _normalize_ctd_payload_to_leaf_sections(self, payload: Any, root_section_id: str) -> Any:
        if not isinstance(payload, dict):
            return payload
        root = str(root_section_id or "").strip()
        if not self._is_supported_ctd_split_root(root):
            return payload

        leaf_ids = self._leaf_section_ids_by_root(root)
        if not leaf_ids:
            return payload
        leaf_set = set(leaf_ids)
        by_section_id: Dict[str, Dict[str, Any]] = {}
        preserved_section_entries: Dict[str, Dict[str, Any]] = {}

        def upsert_entry(section_id: str, text: str, item: Dict[str, Any]) -> None:
            sid = self._resolve_catalog_section_id(section_id)
            if not sid:
                return
            clean_text = str(text or "").strip()
            if not clean_text:
                return
            target_map = by_section_id if sid in leaf_set else preserved_section_entries
            entry = target_map.setdefault(
                sid,
                {
                    "texts": [],
                    "title_path": [],
                    "page_start": None,
                    "page_end": None,
                    "source_item": dict(item),
                },
            )
            if clean_text not in entry["texts"]:
                entry["texts"].append(clean_text)
            path = item.get("title_path", []) if isinstance(item.get("title_path", []), list) else []
            if not entry["title_path"] and path:
                entry["title_path"] = [str(x).strip() for x in path if str(x).strip()]
            page_start = item.get("page_start") if item.get("page_start") is not None else item.get("page")
            page_end = item.get("page_end") if item.get("page_end") is not None else page_start
            try:
                page_start_num = int(page_start) if page_start is not None else None
            except Exception:
                page_start_num = None
            try:
                page_end_num = int(page_end) if page_end is not None else None
            except Exception:
                page_end_num = None
            if page_start_num is not None:
                existing_start = entry.get("page_start")
                entry["page_start"] = page_start_num if existing_start is None else min(existing_start, page_start_num)
            if page_end_num is not None:
                existing_end = entry.get("page_end")
                entry["page_end"] = page_end_num if existing_end is None else max(existing_end, page_end_num)

        for section_item in payload.get("sections", []) if isinstance(payload.get("sections", []), list) else []:
            if not isinstance(section_item, dict):
                continue
            sid = (
                section_item.get("section_id")
                or section_item.get("section_code")
                or section_item.get("code")
                or ""
            )
            upsert_entry(
                section_id=str(sid or "").strip(),
                text=str(section_item.get("content", "") or "").strip(),
                item=section_item,
            )

        for review_unit in payload.get("review_units", []) if isinstance(payload.get("review_units", []), list) else []:
            if not isinstance(review_unit, dict):
                continue
            sid = (
                review_unit.get("section_id")
                or review_unit.get("section_code")
                or review_unit.get("chunk_id")
                or ""
            )
            upsert_entry(
                section_id=str(sid or "").strip(),
                text=str(review_unit.get("text", "") or review_unit.get("content", "") or "").strip(),
                item=review_unit,
            )

        if not by_section_id and not preserved_section_entries:
            return payload

        catalog = self.ctd_sections.get_catalog()
        section_map = catalog.get("section_map", {}) if isinstance(catalog, dict) else {}
        sections_out: List[Dict[str, Any]] = []
        review_units_out: List[Dict[str, Any]] = []
        order = 0
        for sid in leaf_ids:
            entry = by_section_id.get(sid)
            if not isinstance(entry, dict):
                continue
            merged_text = self._preview_text_blocks(entry.get("texts", []))
            if not merged_text:
                continue
            order += 1
            section_meta = section_map.get(sid, {}) if isinstance(section_map, dict) else {}
            section_name = str(section_meta.get("section_name", "") or sid).strip() or sid
            title_path = list(section_meta.get("title_path") or entry.get("title_path") or [section_name])
            parent_section_id = str(section_meta.get("parent_section_id", "") or "").strip()
            page_start = entry.get("page_start")
            page_end = entry.get("page_end")
            sections_out.append(
                {
                    "section_id": sid,
                    "section_code": str(section_meta.get("section_code", "") or sid).strip() or sid,
                    "section_name": section_name,
                    "title_path": title_path,
                    "parent_section_id": parent_section_id,
                    "page_start": page_start,
                    "page_end": page_end,
                    "content": merged_text,
                    "content_preview": self._preview(merged_text, 320),
                    "char_count": len(merged_text),
                    "tables": [],
                    "children_sections": [],
                }
            )
            review_units_out.append(
                {
                    "chunk_id": sid,
                    "section_id": sid,
                    "section_code": str(section_meta.get("section_code", "") or sid).strip() or sid,
                    "section_name": section_name,
                    "parent_section_id": parent_section_id,
                    "page": page_start,
                    "page_start": page_start,
                    "page_end": page_end,
                    "text": merged_text,
                    "title_path": title_path,
                    "char_count": len(merged_text),
                    "unit_order": order,
                    "unit_type": "ctd_leaf_section",
                    "pipeline": "ctd_leaf_normalized",
                }
            )

        # 原文可能把正文直接放在 CTD 中间节点，而标准目录还定义了更细的叶子章节。
        # 这种情况下无法可靠选择某一个子章节，因此保留原始章节作为可展示、可回放
        # 的内容单元，避免在叶子归一化时静默丢失正文。
        for sid, entry in preserved_section_entries.items():
            merged_text = self._preview_text_blocks(entry.get("texts", []))
            if not merged_text:
                continue
            order += 1
            source_item = entry.get("source_item", {}) if isinstance(entry.get("source_item", {}), dict) else {}
            section_meta = section_map.get(sid, {}) if isinstance(section_map, dict) else {}
            section_name = str(
                section_meta.get("section_name", "")
                or source_item.get("section_name", "")
                or source_item.get("title", "")
                or sid
            ).strip() or sid
            title_path = list(section_meta.get("title_path") or entry.get("title_path") or [section_name])
            parent_section_id = str(
                section_meta.get("parent_section_id", "")
                or source_item.get("parent_section_id", "")
                or ""
            ).strip()
            page_start = entry.get("page_start")
            page_end = entry.get("page_end")
            sections_out.append(
                {
                    "section_id": sid,
                    "section_code": str(section_meta.get("section_code", "") or sid).strip() or sid,
                    "section_name": section_name,
                    "title_path": title_path,
                    "parent_section_id": parent_section_id,
                    "page_start": page_start,
                    "page_end": page_end,
                    "content": merged_text,
                    "content_preview": self._preview(merged_text, 320),
                    "char_count": len(merged_text),
                    "tables": [],
                    "children_sections": [],
                }
            )
            review_units_out.append(
                {
                    "chunk_id": sid,
                    "section_id": sid,
                    "section_code": str(section_meta.get("section_code", "") or sid).strip() or sid,
                    "section_name": section_name,
                    "parent_section_id": parent_section_id,
                    "page": page_start,
                    "page_start": page_start,
                    "page_end": page_end,
                    "text": merged_text,
                    "title_path": title_path,
                    "char_count": len(merged_text),
                    "unit_order": order,
                    "unit_type": "ctd_intermediate_section_preserved",
                    "pipeline": "ctd_catalog_aligned",
                }
            )

        if not sections_out:
            return payload

        normalized = dict(payload)
        normalized["root_section_id"] = root
        normalized["structure_type"] = str(payload.get("structure_type", "") or "ctd_payload") + "_catalog_aligned_v2"
        normalized["sections"] = sections_out
        normalized["review_units"] = review_units_out
        normalized["leaf_sibling_groups"] = payload.get("leaf_sibling_groups", []) if isinstance(payload.get("leaf_sibling_groups", []), list) else []
        normalized["statistics"] = {
            **(payload.get("statistics", {}) if isinstance(payload.get("statistics", {}), dict) else {}),
            "leaf_section_total": len(by_section_id),
            "preserved_intermediate_section_total": len(preserved_section_entries),
            "review_unit_total": len(review_units_out),
        }
        return normalized

    def _extract_section_content_rows_from_payload(
        self,
        payload: Any,
        fallback_section_id: str = "",
        fallback_section_name: str = "",
    ) -> List[Dict[str, Any]]:
        sections: List[Dict[str, Any]] = []
        if isinstance(payload, dict) and isinstance(payload.get("sections"), list):
            sections = [item for item in payload.get("sections", []) if isinstance(item, dict)]
        elif isinstance(payload, dict) and isinstance(payload.get("review_units"), list):
            for item in payload.get("review_units", []):
                if not isinstance(item, dict):
                    continue
                sections.append(
                    {
                        "section_id": item.get("section_id") or item.get("chunk_id"),
                        "code": item.get("section_code") or item.get("section_id") or item.get("chunk_id"),
                        "section_name": item.get("section_name") or item.get("title"),
                        "content": item.get("text") or item.get("content") or "",
                    }
                )
        elif isinstance(payload, list):
            merged_text = self._preview_text_blocks(
                [str(item.get("text", "")).strip() for item in payload if isinstance(item, dict)]
            )
            if fallback_section_id and merged_text:
                sections = [
                    {
                        "section_id": fallback_section_id,
                        "code": fallback_section_id,
                        "section_name": fallback_section_name or fallback_section_id,
                        "content": merged_text,
                    }
                ]

        out: List[Dict[str, Any]] = []
        seen = set()
        for item in sections:
            section_id = str(item.get("section_id", "") or "").strip()
            if not section_id:
                continue
            content = self._strip_markdown_images(item.get("content") or item.get("text") or "")
            if not content:
                continue
            key = (section_id, content)
            if key in seen:
                continue
            seen.add(key)
            out.append(
                {
                    "section_id": section_id,
                    "section_code": str(item.get("code") or item.get("section_code") or section_id).strip() or section_id,
                    "section_name": str(item.get("section_name") or item.get("title") or section_id).strip() or section_id,
                    "content": content,
                    "content_preview": self._preview(content, 320),
                }
            )
        return out

    def _coerce_payload_to_explicit_section_binding(
        self,
        payload: Any,
        section_id: str,
        section_name: str,
    ) -> Any:
        bound_section_id = str(section_id or "").strip()
        if not bound_section_id:
            return payload
        bound_section_name = str(section_name or bound_section_id).strip() or bound_section_id

        def _iter_ids(obj: Any) -> List[str]:
            ids: List[str] = []
            if isinstance(obj, dict):
                for key in ("sections", "review_units"):
                    values = obj.get(key, [])
                    if not isinstance(values, list):
                        continue
                    for item in values:
                        if not isinstance(item, dict):
                            continue
                        candidate = str(
                            item.get("section_id")
                            or item.get("section_code")
                            or item.get("code")
                            or ""
                        ).strip()
                        if candidate:
                            ids.append(candidate)
            elif isinstance(obj, list):
                for item in obj:
                    if not isinstance(item, dict):
                        continue
                    candidate = str(
                        item.get("section_id")
                        or item.get("section_code")
                        or item.get("code")
                        or ""
                    ).strip()
                    if candidate:
                        ids.append(candidate)
            return ids

        existing_ids = _iter_ids(payload)
        if bound_section_id in existing_ids:
            return payload

        has_ctd_binding = any(self._branch_root_from_section_id(item) for item in existing_ids)
        if has_ctd_binding:
            return payload

        section_rows = self._extract_section_content_rows_from_payload(
            payload=payload,
            fallback_section_id=bound_section_id,
            fallback_section_name=bound_section_name,
        )
        merged_text = self._preview_text_blocks([str(item.get("content", "")).strip() for item in section_rows])
        if not merged_text and isinstance(payload, dict):
            merged_text = self._preview_text_blocks(
                [str(item.get("text", "")).strip() for item in payload.get("review_units", []) if isinstance(item, dict)]
            )
        if not merged_text:
            return payload

        payload_dict = dict(payload) if isinstance(payload, dict) else {}
        return {
            **payload_dict,
            "structure_type": str(payload_dict.get("structure_type", "") or "explicit_section_binding_v1"),
            "sections": [
                {
                    "section_id": bound_section_id,
                    "code": bound_section_id,
                    "title": bound_section_name,
                    "section_name": bound_section_name,
                    "title_path": [bound_section_name],
                    "parent_section_id": self._branch_root_from_section_id(bound_section_id),
                    "page_start": None,
                    "page_end": None,
                    "content": merged_text,
                    "content_preview": self._preview(merged_text, 320),
                    "char_count": len(merged_text),
                }
            ],
            "review_units": [
                {
                    "chunk_id": bound_section_id,
                    "section_id": bound_section_id,
                    "section_code": bound_section_id,
                    "section_name": bound_section_name,
                    "parent_section_id": self._branch_root_from_section_id(bound_section_id),
                    "page": None,
                    "page_start": None,
                    "page_end": None,
                    "text": merged_text,
                    "title_path": [bound_section_name],
                    "unit_order": 1,
                    "unit_type": "bound_section_content",
                }
            ],
        }

    def _sync_submission_section_content_rows(
        self,
        session,
        file_row: PreReviewSubmissionFile,
        payload: Any,
    ) -> None:
        session.query(PreReviewSubmissionSectionContent).filter(
            PreReviewSubmissionSectionContent.doc_id == file_row.doc_id
        ).delete(synchronize_session=False)

        section_rows = self._extract_section_content_rows_from_payload(
            payload=payload,
            fallback_section_id=str(getattr(file_row, "section_id", "") or ""),
            fallback_section_name=str(getattr(file_row, "section_name", "") or ""),
        )
        payload_type = str(payload.get("structure_type", "")).strip() if isinstance(payload, dict) else ""
        payload_parser_mode = str(payload.get("parser_mode", "")).strip().lower() if isinstance(payload, dict) else ""
        payload_source_parser = str(payload.get("source_parser", "")).strip().lower() if isinstance(payload, dict) else ""
        if payload_source_parser == "ctd_deepseek_ocr" or payload_parser_mode == "detailed":
            parser_name = "ctd_deepseek_ocr"
        elif payload_source_parser in {"ctd_docx_markdown", "bound_section_docx_markdown"}:
            parser_name = payload_source_parser
        elif payload_source_parser == "manual_markdown_edit":
            parser_name = "manual_markdown_edit"
        elif payload_type == "ctd_api_markdown_json":
            parser_name = "ctd_api_markdown"
        elif payload_type in {"ctd_docx_leaf_section_payload_v1", "bound_section_docx_markdown_payload_v1"}:
            parser_name = "ctd_docx_markdown"
        elif payload_type == "ctd_fixed_outline_coarse_payload_v1":
            parser_name = "ctd_outline_coarse"
        elif payload_type == "generic_heading_based_markdown_json_v2":
            parser_name = "submission_pdf_markdown"
        else:
            parser_name = "strict_ctd"
        now = self._now()
        for item in section_rows:
            section_id = str(item.get("section_id", "")).strip()
            section_code = str(item.get("section_code", "")).strip()
            section_name = str(item.get("section_name", "")).strip()
            content_preview = str(item.get("content_preview", "")).strip()
            source_parser = parser_name if section_id.startswith("3.2.") else "generic"
            content_chunks = self._split_submission_content_for_storage(item.get("content", ""))
            for chunk_index, chunk_text in enumerate(content_chunks, start=1):
                session.add(
                    PreReviewSubmissionSectionContent(
                        doc_id=file_row.doc_id,
                        project_id=file_row.project_id,
                        section_id=section_id,
                        section_code=section_code,
                        section_name=section_name,
                        chunk_index=chunk_index,
                        content=chunk_text,
                        content_preview=content_preview if chunk_index == 1 else "",
                        source_parser=source_parser,
                        create_time=now,
                        update_time=now,
                    )
                )

    def _build_submission_vector_units(
        self,
        payload: Any,
        fallback_section_id: str = "",
        fallback_section_name: str = "",
    ) -> List[Dict[str, Any]]:
        units = payload.get("review_units", []) if isinstance(payload, dict) else []
        if isinstance(units, list) and units:
            out: List[Dict[str, Any]] = []
            for item in units:
                if not isinstance(item, dict):
                    continue
                out.extend(self._split_submission_vector_unit(item))
            return out
        section_rows = self._extract_section_content_rows_from_payload(
            payload=payload,
            fallback_section_id=fallback_section_id,
            fallback_section_name=fallback_section_name,
        )
        out: List[Dict[str, Any]] = []
        for idx, item in enumerate(section_rows, start=1):
            text = str(item.get("content", "")).strip()
            if not text:
                continue
            section_id = str(item.get("section_id", "")).strip()
            section_code = str(item.get("section_code", "")).strip() or section_id
            section_name = str(item.get("section_name", "")).strip() or section_code
            out.append(
                {
                    "chunk_id": f"{section_id}_content",
                    "section_id": section_id,
                    "section_code": section_code,
                    "section_name": section_name,
                    "page": None,
                    "page_start": None,
                    "page_end": None,
                    "char_count": len(text),
                    "text": text,
                    "unit_type": "section_content",
                    "source_section_codes": [section_code],
                    "title_path": [section_name],
                    "unit_order": idx,
                }
            )
        return out

    def _index_submission_payload(
        self,
        file_row: PreReviewSubmissionFile,
        payload: Any,
        force_reindex: bool = False,
    ) -> Tuple[bool, str, int]:
        units = self._build_submission_vector_units(
            payload=payload,
            fallback_section_id=str(getattr(file_row, "section_id", "") or ""),
            fallback_section_name=str(getattr(file_row, "section_name", "") or ""),
        )
        if not units:
            return True, "no review units to index", 0
        try:
            doc_id = str(file_row.doc_id or "").strip()
            if not force_reindex and self.submission_vector_store.has_doc(doc_id):
                return True, "already indexed", len(units)
            self.submission_vector_store.delete_by_doc(doc_id)
            for idx, unit in enumerate(units, start=1):
                text = str(unit.get("text", "")).strip()
                if not text:
                    continue
                chunk_id = str(unit.get("chunk_id") or f"{doc_id}_chunk_{idx}").strip()
                vec_id = f"{doc_id}:{chunk_id}"
                metadata = {
                    "doc_id": doc_id,
                    "chunk_id": chunk_id,
                    "chunk_order": int(unit.get("unit_order") or idx),
                    "item_type": "submission_chunk",
                    "classification": "submission_material",
                    "project_id": str(getattr(file_row, "project_id", "") or ""),
                    "source_domain": "pre_review_submission",
                    "material_category": str(getattr(file_row, "material_category", "") or "other"),
                    "section_id": str(unit.get("section_id", "") or ""),
                    "section_code": str(unit.get("section_code", "") or ""),
                    "section_name": str(unit.get("section_name", "") or ""),
                    "unit_type": str(unit.get("unit_type", "") or ""),
                    "page": unit.get("page"),
                    "page_start": unit.get("page_start"),
                    "page_end": unit.get("page_end"),
                    "title_path": list(unit.get("title_path") or []),
                    "source_parser": str(payload.get("structure_type", "")) if isinstance(payload, dict) else "",
                    "summary": self._preview(text, 240),
                    "keywords": self._dedupe_text_list(
                        [
                            str(unit.get("section_code", "")).strip(),
                            str(unit.get("section_name", "")).strip(),
                            str(getattr(file_row, "material_category", "") or "").strip(),
                        ]
                    ),
                }
                self.submission_vector_store.add_text(vec_id=vec_id, text=text, metadata=metadata)
            return True, "ok", len(units)
        except Exception as exc:
            return False, f"submission vector indexing failed: {str(exc)}", 0

    @staticmethod
    def _rule_label(rule_item: Dict[str, Any]) -> str:
        """
        Semantic retrieval currently returns doc_id/content/score fields.
        Build a stable human-readable label for linkage display.
        """
        doc_id = str(rule_item.get("doc_id", "")).strip()
        classification = str(rule_item.get("classification", "")).strip()
        score = rule_item.get("score", None)
        parts = [p for p in [doc_id, classification] if p]
        label = "/".join(parts) if parts else "rule"
        if score is not None:
            try:
                label = f"{label}(score={float(score):.3f})"
            except Exception:
                pass
        return label

    @staticmethod
    def _preview(value: Any, max_len: int = 180) -> str:
        text = str(value or "").replace("\n", " ").strip()
        return text if len(text) <= max_len else f"{text[:max_len]}..."

    @staticmethod
    def _strip_markdown_images(value: Any) -> str:
        text = str(value or "")
        if not text:
            return ""
        text = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", "", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    @staticmethod
    def _trim_text_to_max_bytes(value: Any, max_bytes: int = SUBMISSION_SECTION_CONTENT_MAX_BYTES) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        raw = text.encode("utf-8")
        if len(raw) <= max_bytes:
            return text
        clipped = raw[:max_bytes]
        while clipped:
            try:
                return clipped.decode("utf-8").rstrip()
            except UnicodeDecodeError:
                clipped = clipped[:-1]
        return ""

    @staticmethod
    def _split_submission_content_for_storage(
        value: Any,
        max_chars: int = SUBMISSION_SECTION_CONTENT_CHUNK_MAX_CHARS,
    ) -> List[str]:
        text = str(value or "")
        if not text:
            return [""]
        chunk_size = max(int(max_chars or 0), 1)
        return [text[idx : idx + chunk_size] for idx in range(0, len(text), chunk_size)]

    def _merge_submission_section_content_rows(self, rows: List[Any]) -> List[Dict[str, Any]]:
        grouped: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for row in rows or []:
            doc_id = str(getattr(row, "doc_id", "") or "").strip()
            section_id = str(getattr(row, "section_id", "") or "").strip()
            if not doc_id or not section_id:
                continue
            key = (doc_id, section_id)
            current = grouped.get(key)
            if current is None:
                current = {
                    "doc_id": doc_id,
                    "project_id": str(getattr(row, "project_id", "") or "").strip(),
                    "section_id": section_id,
                    "section_code": str(getattr(row, "section_code", "") or "").strip() or section_id,
                    "section_name": str(getattr(row, "section_name", "") or "").strip(),
                    "chunk_index": int(getattr(row, "chunk_index", 1) or 1),
                    "content_preview": str(getattr(row, "content_preview", "") or "").strip(),
                    "source_parser": str(getattr(row, "source_parser", "") or "").strip(),
                    "create_time": getattr(row, "create_time", None),
                    "update_time": getattr(row, "update_time", None),
                    "_parts": [],
                }
                grouped[key] = current
            current["_parts"].append(
                (
                    int(getattr(row, "chunk_index", 1) or 1),
                    str(getattr(row, "content", "") or ""),
                )
            )
            if not current.get("content_preview"):
                current["content_preview"] = str(getattr(row, "content_preview", "") or "").strip()
            if not current.get("section_name"):
                current["section_name"] = str(getattr(row, "section_name", "") or "").strip()
            if not current.get("source_parser"):
                current["source_parser"] = str(getattr(row, "source_parser", "") or "").strip()
            if not current.get("update_time"):
                current["update_time"] = getattr(row, "update_time", None)

        out: List[Dict[str, Any]] = []
        for item in grouped.values():
            parts = sorted(item.pop("_parts", []), key=lambda value: int(value[0] or 1))
            merged_text = self._strip_markdown_images("".join(part_text for _, part_text in parts))
            item["content"] = merged_text
            item["content_preview"] = str(item.get("content_preview", "") or "").strip() or self._preview(merged_text, 320)
            out.append(item)
        return out

    @staticmethod
    def _parse_json_list(raw_value: Any) -> List[str]:
        if isinstance(raw_value, list):
            return [str(x).strip() for x in raw_value if str(x).strip()]
        text = str(raw_value or "").strip()
        if not text:
            return []
        try:
            data = json.loads(text)
            if isinstance(data, list):
                return [str(x).strip() for x in data if str(x).strip()]
        except Exception:
            pass
        return [text]

    @staticmethod
    def _code_sort_key(code: str) -> Tuple:
        parts = str(code or "").split(".")
        key = []
        for p in parts:
            if p.isdigit():
                key.append((0, int(p)))
            else:
                key.append((1, p))
        return tuple(key)

    def _order_review_units(self, chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return sorted(
            chunks or [],
            key=lambda x: (
                int(x.get("unit_order") or 10**9),
                self._code_sort_key(str(x.get("section_code", ""))),
                int(x.get("page_start") or x.get("page") or 10**9),
                str(x.get("section_id") or x.get("chunk_id") or ""),
            ),
        )

    def _build_section_query(
        self,
        section_name: str,
        section_code: str,
        text: str,
        title_path: List[Any],
        concern_points: Optional[List[Any]] = None,
        section_summary: Optional[Dict[str, Any]] = None,
    ) -> str:
        path = " > ".join([str(x).strip() for x in (title_path or []) if str(x).strip()])
        query_parts = [f"章节: {section_code} {section_name}".strip()]
        if path:
            query_parts.append(f"标题路径: {path}")
        concerns = [str(x).strip() for x in (concern_points or []) if str(x).strip()]
        if concerns:
            query_parts.append(f"关注点: {'; '.join(concerns[:8])}")
        if isinstance(section_summary, dict):
            summary_text = str(section_summary.get("structured_summary", "") or "").strip()
            if summary_text:
                query_parts.append(f"结构化摘要: {self._preview(summary_text, 240)}")
            key_facts = [str(x).strip() for x in section_summary.get("key_facts", []) if str(x).strip()] if isinstance(section_summary.get("key_facts", []), list) else []
            if key_facts:
                query_parts.append(f"关键事实: {'; '.join(key_facts[:8])}")
        query_parts.append(f"原文片段: {self._preview(text, 260)}")
        return "\n".join(query_parts)

    def _merge_retrieval_queries_with_focus_points(
        self,
        query_list: List[Any],
        focus_points: Optional[List[Any]] = None,
        max_queries: int = 6,
    ) -> List[str]:
        merged: List[str] = []
        seen = set()
        for item in self._normalize_text_list(query_list):
            text = self._sanitize_retrieval_query(item)
            if not text or text in seen:
                continue
            seen.add(text)
            merged.append(text)
            if len(merged) >= max_queries:
                return merged
        for point in self._normalize_text_list(focus_points or []):
            compact_point = self._sanitize_retrieval_query(point, max_len=64)
            if not compact_point:
                continue
            variants = [compact_point]
            for base_query in merged[:2]:
                combined = self._sanitize_retrieval_query(f"{base_query} 关注点 {compact_point}".strip(), max_len=120)
                if combined:
                    variants.append(combined)
            for variant in variants:
                text = str(variant or "").strip()
                if not text or text in seen:
                    continue
                seen.add(text)
                merged.append(text)
                if len(merged) >= max_queries:
                    return merged
        return merged

    def _normalize_planner_result(
        self,
        planner_result: Dict[str, Any],
        section_name: str,
        product_type: str,
        registration_class: str,
        focus_points: Optional[List[Any]] = None,
    ) -> Dict[str, Any]:
        normalized_focus_points = self._normalize_text_list(focus_points or [])
        section_title = str(section_name or "").strip()
        product_text = str(product_type or "").strip()
        registration_text = str(registration_class or "").strip()
        result = dict(planner_result if isinstance(planner_result, dict) else {})

        # 1) Normalize query_list to short, deduped retrieval phrases.
        query_list = self._merge_retrieval_queries_with_focus_points(
            result.get("query_list", []),
            normalized_focus_points,
            max_queries=8,
        )
        fallback_queries = [
            self._sanitize_retrieval_query(f"{section_title} {product_text}".strip(), max_len=80),
            self._sanitize_retrieval_query(f"{section_title} {registration_text}".strip(), max_len=80),
            self._sanitize_retrieval_query(f"{section_title} 申报要求".strip(), max_len=80),
        ]
        for item in fallback_queries:
            text = str(item or "").strip()
            if not text or text in query_list:
                continue
            query_list.append(text)
            if len(query_list) >= 8:
                break
        query_list = [self._sanitize_retrieval_query(item, max_len=80) for item in query_list]
        query_list = [item for item in self._dedupe_text_list(query_list) if item]
        if normalized_focus_points and not any(
            any(fp in query for fp in normalized_focus_points[:3]) for query in query_list
        ):
            focus_query = self._sanitize_retrieval_query(
                f"{section_title} {normalized_focus_points[0]} {product_text}".strip(),
                max_len=80,
            )
            if focus_query:
                query_list.insert(0, focus_query)
                query_list = self._dedupe_text_list(query_list)
        if len(query_list) < 3:
            while len(query_list) < 3:
                seed = self._sanitize_retrieval_query(
                    f"{section_title} 核查点{len(query_list) + 1} {product_text}".strip(),
                    max_len=80,
                )
                if seed and seed not in query_list:
                    query_list.append(seed)
                else:
                    break
        query_list = query_list[:8] if query_list else [self._sanitize_retrieval_query(section_title or "章节审评", max_len=80)]

        # 2) Normalize retrieval_plan, ensure all 5 sources are present.
        default_purpose = {
            RETRIEVAL_SOURCE_GUIDANCE: "核对指导原则与章节要求",
            RETRIEVAL_SOURCE_ICH: "核对 ICH 技术要求",
            RETRIEVAL_SOURCE_REGULATION: "核对法律法规与申报依据",
            RETRIEVAL_SOURCE_PHARMACOPOEIA: "核对药典标准与检查项目",
            RETRIEVAL_SOURCE_EXPERIENCE: "补充历史经验风险提醒",
        }
        ordered_sources = [
            RETRIEVAL_SOURCE_GUIDANCE,
            RETRIEVAL_SOURCE_ICH,
            RETRIEVAL_SOURCE_REGULATION,
            RETRIEVAL_SOURCE_PHARMACOPOEIA,
            RETRIEVAL_SOURCE_EXPERIENCE,
        ]
        source_plan_map: Dict[str, Dict[str, Any]] = {}
        for step in result.get("retrieval_plan", []) if isinstance(result.get("retrieval_plan", []), list) else []:
            if not isinstance(step, dict):
                continue
            source_type = self._canonicalize_source_type(step.get("source_type", ""))
            if not source_type:
                continue
            query_subset = self._merge_retrieval_queries_with_focus_points(
                step.get("query_subset", []),
                normalized_focus_points,
                max_queries=4,
            ) or query_list[:2]
            query_subset = [self._sanitize_retrieval_query(item, max_len=80) for item in query_subset]
            query_subset = [item for item in self._dedupe_text_list(query_subset) if item]
            source_plan_map[source_type] = {
                "source_type": source_type,
                "purpose": str(step.get("purpose", "") or default_purpose.get(source_type, "")).strip(),
                "query_subset": query_subset[:4] if query_subset else query_list[:2],
            }
        for source_type in ordered_sources:
            if source_type in source_plan_map:
                continue
            source_plan_map[source_type] = {
                "source_type": source_type,
                "purpose": default_purpose[source_type],
                "query_subset": query_list[:2] if source_type != RETRIEVAL_SOURCE_EXPERIENCE else query_list[:1],
            }
        retrieval_plan = [source_plan_map[source] for source in ordered_sources]

        result["section_id"] = str(result.get("section_id", "") or "")
        result["query_list"] = query_list
        result["retrieval_plan"] = retrieval_plan
        result["priority_sources"] = ordered_sources
        result["expected_evidence_types"] = self._normalize_text_list(result.get("expected_evidence_types", []))
        result["missing_info_flags"] = self._normalize_text_list(result.get("missing_info_flags", []))
        result["retrieval_blueprint"] = (
            dict(result.get("retrieval_blueprint", {}))
            if isinstance(result.get("retrieval_blueprint", {}), dict)
            else {}
        )
        result["review_tasks"] = [
            dict(item)
            for item in result.get("review_tasks", [])
            if isinstance(result.get("review_tasks", []), list) and isinstance(item, dict)
        ]
        return result


    def _normalize_finding_dicts(self, findings: List[Any]) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        for item in findings or []:
            if isinstance(item, dict):
                title = str(item.get("title", "") or "").strip()
                if not title:
                    continue
                normalized.append(
                    {
                        "title": title,
                        "problem_type": str(item.get("problem_type", "") or "").strip(),
                        "severity": str(item.get("severity", "low") or "low").strip().lower(),
                        "evidence": str(item.get("evidence", "") or "").strip(),
                        "recommendation": str(item.get("recommendation", "") or "").strip(),
                        "metadata": item.get("metadata", {}) if isinstance(item.get("metadata", {}), dict) else {},
                    }
                )
                continue
            title = str(item or "").strip()
            if title:
                normalized.append(
                    {
                        "title": title,
                        "problem_type": "",
                        "severity": "low",
                        "evidence": "",
                        "recommendation": "",
                        "metadata": {},
                    }
                )
        return normalized

    def _findings_to_titles(self, findings: List[Any]) -> List[str]:
        return [item.get("title", "") for item in self._normalize_finding_dicts(findings) if item.get("title", "")]

    def _render_findings_summary(self, findings: List[Any]) -> str:
        titles = self._findings_to_titles(findings)
        return "；".join(titles) if titles else "未发现明显问题"

    @staticmethod
    def _emit_progress(progress_callback, stage: str, message: str, **payload: Any) -> None:
        if not callable(progress_callback):
            return
        data = {"stage": str(stage or "").strip(), "message": str(message or "").strip()}
        data.update(payload or {})
        try:
            progress_callback(data)
        except Exception:
            return

    def _build_paragraph_anchors(self, text: str, section_id: str, section_code: str) -> List[Dict[str, Any]]:
        normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
        raw_blocks = [item.strip() for item in re.split(r"\n{2,}", normalized) if item.strip()]
        if not raw_blocks and normalized.strip():
            raw_blocks = [normalized.strip()]
        anchors: List[Dict[str, Any]] = []
        cursor = 0
        for index, paragraph in enumerate(raw_blocks, start=1):
            start = normalized.find(paragraph, cursor)
            if start < 0:
                start = cursor
            end = start + len(paragraph)
            cursor = end
            anchors.append(
                {
                    "anchor_id": f"{section_id}:p{index}",
                    "anchor_label": f"{section_code or section_id}-P{index}",
                    "paragraph_index": index,
                    "text": paragraph,
                    "span_start": start,
                    "span_end": end,
                    "char_count": len(paragraph),
                }
            )
        return anchors

    def _bind_findings_to_paragraphs(
        self,
        findings: List[Dict[str, Any]],
        paragraph_blocks: List[Dict[str, Any]],
        section_id: str,
        section_code: str,
    ) -> List[Dict[str, Any]]:
        if not findings:
            return []
        anchors = paragraph_blocks or []
        bound: List[Dict[str, Any]] = []
        for finding in findings:
            metadata = finding.get("metadata", {}) if isinstance(finding.get("metadata", {}), dict) else {}
            metadata = dict(metadata)
            metadata["section_id"] = section_id
            metadata["section_code"] = section_code
            evidence_text = " ".join(
                [
                    str(finding.get("title", "") or ""),
                    str(finding.get("evidence", "") or ""),
                    str(finding.get("recommendation", "") or ""),
                ]
            ).strip()
            tokens = [
                token.strip()
                for token in re.split(r"[\s,锛屻€傦紱;銆?锛?)锛堬級]+", evidence_text)
                if token.strip() and len(token.strip()) >= 2
            ][:8]
            matched: List[Dict[str, Any]] = []
            for anchor in anchors:
                paragraph_text = str(anchor.get("text", "") or "")
                if not paragraph_text:
                    continue
                if tokens and any(token in paragraph_text for token in tokens):
                    matched.append(
                        {
                            "anchor_id": anchor.get("anchor_id", ""),
                            "anchor_label": anchor.get("anchor_label", ""),
                            "paragraph_index": anchor.get("paragraph_index"),
                            "span_start": anchor.get("span_start"),
                            "span_end": anchor.get("span_end"),
                            "snippet": self._preview(paragraph_text, 160),
                        }
                    )
                if len(matched) >= 3:
                    break
            metadata["anchors"] = matched
            bound.append({**finding, "metadata": metadata})
        return bound

    def _classify_risk_level(self, findings: List[Any], score: float) -> str:
        normalized = self._normalize_finding_dicts(findings)
        text = " ".join(
            [
                " ".join(
                    [
                        item.get("title", ""),
                        item.get("problem_type", ""),
                        item.get("evidence", ""),
                        item.get("recommendation", ""),
                    ]
                )
                for item in normalized
            ]
        ).lower()
        severities = {item.get("severity", "low") for item in normalized}
        high_markers = ["绂佸繉", "涓ラ噸", "鑷村懡", "black box", "contraindication", "fatal", "high risk", "critical"]
        medium_markers = ["鍓傞噺", "涓嶈壇鍙嶅簲", "鐩戞祴", "鐩镐簰浣滅敤", "warning", "adverse", "interaction", "monitor"]
        if "high" in severities:
            return "high"
        if "medium" in severities and score >= 0.2:
            return "medium"
        if any(k in text for k in high_markers):
            return "high"
        if score >= 0.65:
            return "high"
        if any(k in text for k in medium_markers):
            return "medium"
        if score >= 0.25:
            return "medium"
        return "low"

    def _apply_active_prompt_version(self, run_config: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        merged = dict(run_config or {})
        prompt_config = merged.get("prompt_config", {}) if isinstance(merged.get("prompt_config", {}), dict) else {}
        if prompt_config.get("prompt_bundle_path") and prompt_config.get("prompt_version_id"):
            merged["prompt_config"] = prompt_config
            return merged
        active_prompt_config = self.prompt_registry.resolve_active_prompt_config()
        if not active_prompt_config:
            return merged
        prompt_config = dict(prompt_config)
        prompt_config.update(active_prompt_config)
        merged["prompt_config"] = prompt_config
        return merged

    def _compose_runtime_prompt_config(
        self,
        project_id: str,
        task_type: str,
        base_prompt_config: Optional[Dict[str, Any]] = None,
        section_id: str = "",
        section_name: str = "",
        review_domain: str = "",
        product_type: str = "",
        registration_class: str = "",
    ) -> Dict[str, Any]:
        return self.runtime_context_service.compose_prompt_config(
            project_id=project_id,
            task_type=task_type,
            base_prompt_config=base_prompt_config,
            section_id=section_id,
            section_name=section_name,
            review_domain=review_domain,
            product_type=product_type,
            registration_class=registration_class,
        )

    @staticmethod
    def _extract_active_rule_refs(prompt_config: Optional[Dict[str, Any]], task_type: str) -> List[Dict[str, Any]]:
        return ExecutionAuditService.extract_active_rule_refs(prompt_config, task_type)

    @staticmethod
    def _build_execution_tool_signatures(stage: str) -> Dict[str, str]:
        return ExecutionAuditService.build_execution_tool_signatures(stage)

    def _build_execution_version_snapshot(
        self,
        *,
        task_type: str,
        prompt_config: Optional[Dict[str, Any]] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        audit_service = getattr(self, "execution_audit_service", None) or ExecutionAuditService(self)
        return audit_service.build_execution_version_snapshot(
            task_type=task_type,
            prompt_config=prompt_config,
            extra=extra,
        )

    def _build_execution_audit_row(
        self,
        *,
        run_id: str,
        section_id: str,
        stage: str,
        agent_name: str,
        input_digest: Optional[Dict[str, Any]] = None,
        output_digest: Optional[Dict[str, Any]] = None,
        prompt_config: Optional[Dict[str, Any]] = None,
        status: str = "completed",
        attempt_no: int = 1,
        error_message: str = "",
        extra_version: Optional[Dict[str, Any]] = None,
    ) -> PreReviewExecutionAudit:
        audit_service = getattr(self, "execution_audit_service", None) or ExecutionAuditService(self)
        return audit_service.build_execution_audit_row(
            run_id=run_id,
            section_id=section_id,
            stage=stage,
            agent_name=agent_name,
            input_digest=input_digest,
            output_digest=output_digest,
            prompt_config=prompt_config,
            status=status,
            attempt_no=attempt_no,
            error_message=error_message,
            extra_version=extra_version,
        )

    @staticmethod
    def _serialize_execution_audit_row(row: PreReviewExecutionAudit) -> Dict[str, Any]:
        return ExecutionAuditService.serialize_execution_audit_row(row)

    @staticmethod
    def _summarize_execution_audit_items(items: List[Dict[str, Any]]) -> Dict[str, Dict[str, int]]:
        return ExecutionAuditService.summarize_execution_audit_items(items)

    @staticmethod
    def _build_execution_audit_diff(
        current_items: List[Dict[str, Any]],
        previous_items: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        return ExecutionAuditService.build_execution_audit_diff(current_items, previous_items)

    def _build_coordination_payload(
        self,
        project_id: str,
        run_id: str,
        source_doc_id: str,
        section_meta: Dict[str, Any],
        related_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        hits = related_result.get("list", []) if isinstance(related_result, dict) else []
        grouped_docs = related_result.get("grouped_docs", []) if isinstance(related_result, dict) else []
        hit_digest = []
        for h in hits[:5]:
            if not isinstance(h, dict):
                continue
            hit_digest.append(
                {
                    "doc_id": h.get("doc_id", ""),
                    "chunk_id": h.get("chunk_id", ""),
                    "score": h.get("score", 0.0),
                    "classification": h.get("classification", ""),
                    "summary": self._preview(h.get("summary", "")),
                    "content_preview": self._preview(h.get("content", "")),
                }
            )
        grouped_digest = []
        for g in grouped_docs[:3]:
            if not isinstance(g, dict):
                continue
            grouped_digest.append(
                {
                    "doc_id": g.get("doc_id", ""),
                    "doc_summary": self._preview(g.get("doc_summary", "")),
                    "doc_keywords": g.get("doc_keywords", []),
                    "matched_count": len(g.get("matched_hits", []) or []),
                    "related_chunk_count": len(g.get("related_chunks", []) or []),
                }
            )
        return {
            "coordination_version": "v1",
            "project_id": project_id,
            "run_id": run_id,
            "source_doc_id": source_doc_id,
            "section_meta": section_meta,
            "retrieval": {
                "query": section_meta.get("query", ""),
                "hit_count": len(hits),
                "grouped_doc_count": len(grouped_docs),
                "hits": hit_digest,
                "grouped_docs": grouped_digest,
            },
            "memory_strategy": {
                "metadata_filter": {"project_id": project_id},
                "types": ["episodic", "semantic", "working"],
                "top_k": 8,
            },
        }

    def _seed_submission_structure_memory(
        self,
        project_id: str,
        source_doc_id: str,
        review_units: List[Dict[str, Any]],
    ) -> None:
        """
        Inject section skeleton into semantic memory to stabilize cross-section consistency.
        """
        self.memory_governance.remember_submission_outline(
            project_id=project_id,
            source_doc_id=source_doc_id,
            review_units=review_units,
        )

    def _run_post_review_agents(
        self,
        project: PreReviewProject,
        run_id: str,
        source_doc_id: str,
        section_results: List[Dict[str, Any]],
        run_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        prompt_config = (run_config or {}).get("prompt_config", {}) if isinstance((run_config or {}).get("prompt_config", {}), dict) else {}
        return self.run_review_workflow.run(
            project_meta={
                "project_id": project.project_id,
                "project_name": project.project_name,
                "registration_scope": getattr(project, "registration_scope", "") or "",
                "source_doc_id": source_doc_id,
                "run_id": run_id,
            },
            section_results=section_results,
            prompt_config=prompt_config,
        )

    def _build_section_review_packet(
        self,
        project_id: str,
        run_id: str,
        source_doc_id: str,
        chunk: Dict[str, Any],
        section_meta: Dict[str, Any],
        related_result: Dict[str, Any],
        coordination_payload: Dict[str, Any],
        run_config: Optional[Dict[str, Any]] = None,
    ) -> SectionReviewPacket:
        related_rules = related_result.get("list", []) if isinstance(related_result, dict) else []
        title_path = chunk.get("title_path", []) if isinstance(chunk.get("title_path", []), list) else []
        return SectionReviewPacket(
            project_id=project_id,
            run_id=run_id,
            doc_id=source_doc_id,
            section_id=str(section_meta.get("section_id", "")),
            section_code=str(section_meta.get("section_code", "")),
            section_title=str(section_meta.get("section_name", "")),
            section_text=str(chunk.get("text", "") or ""),
            title_path=[str(x).strip() for x in title_path if str(x).strip()],
            page_start=chunk.get("page_start"),
            page_end=chunk.get("page_end"),
            unit_type=str(chunk.get("unit_type", "") or ""),
            section_rules=self._normalize_text_list(section_meta.get("section_rules", [])),
            reference_examples=section_meta.get("reference_examples", []) if isinstance(section_meta.get("reference_examples", []), list) else [],
            retrieval_context=related_result if isinstance(related_result, dict) else {},
            related_rules=related_rules if isinstance(related_rules, list) else [],
            coordination_payload=coordination_payload,
            memory_metadata_filter={"project_id": project_id},
            run_config=dict(run_config or {}),
        )

    @staticmethod
    def _normalize_text_list(values: Any) -> List[str]:
        out: List[str] = []
        if not isinstance(values, list):
            return out
        seen = set()
        for item in values:
            text = str(item or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            out.append(text)
        return out

    def _resolve_chunk_focus_points(
        self,
        project_id: str,
        section_id: str,
        chunk: Optional[Dict[str, Any]] = None,
        run_config: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        merged = self._dedupe_text_list(
            self._normalize_text_list((chunk or {}).get("concern_points", []))
            + self._normalize_text_list((chunk or {}).get("focus_points", []))
        )
        config = run_config if isinstance(run_config, dict) else {}

        def _extract_override_points(value: Any) -> List[str]:
            if isinstance(value, dict):
                return self._dedupe_text_list(
                    self._normalize_text_list(value.get("focus_points", []))
                    + self._normalize_text_list(value.get("concern_points", []))
                    + self._normalize_text_list(value.get("points", []))
                )
            return self._normalize_text_list(value)

        override_map = (
            config.get("section_focus_overrides", {})
            or config.get("focus_points_map", {})
            or config.get("section_focus_points", {})
        )
        if isinstance(override_map, dict):
            key_candidates = self._dedupe_text_list(
                [
                    str(section_id or "").strip(),
                    str((chunk or {}).get("section_code", "") or "").strip(),
                    str((chunk or {}).get("code", "") or "").strip(),
                    str((chunk or {}).get("section_name", "") or "").strip(),
                ]
            )
            for key in key_candidates:
                if not key:
                    continue
                merged = self._dedupe_text_list(merged + _extract_override_points(override_map.get(key, [])))

        # Compatibility: support run-level generic focus fields
        merged = self._dedupe_text_list(
            merged
            + self._normalize_text_list(config.get("focus_points", []))
            + self._normalize_text_list(config.get("concern_points", []))
        )
        if merged:
            return merged
        if not project_id or not section_id:
            return merged
        session = self.db_conn.get_session()
        try:
            manual_rule_map = self._load_manual_section_rule_map(session, project_id)
            manual_rules = manual_rule_map.get(section_id, [])
            if manual_rules:
                return self._dedupe_text_list(merged + manual_rules)
            return merged
        finally:
            session.close()

    def _split_submission_vector_unit(
        self,
        unit: Dict[str, Any],
        max_chars: int = 3200,
        overlap: int = 320,
    ) -> List[Dict[str, Any]]:
        text = self._strip_markdown_images(unit.get("text", ""))
        if not text:
            return []
        if len(text) <= max_chars:
            chunk_unit = dict(unit)
            chunk_unit["text"] = text
            chunk_unit["char_count"] = len(text)
            return [chunk_unit]
        overlap = max(0, min(overlap, max_chars // 2))
        chunk_rows: List[Dict[str, Any]] = []
        start = 0
        part_no = 1
        text_len = len(text)
        base_chunk_id = str(unit.get("chunk_id") or unit.get("section_id") or "submission_chunk").strip() or "submission_chunk"
        base_order = int(unit.get("unit_order") or 0)
        while start < text_len:
            end = min(text_len, start + max_chars)
            if end < text_len:
                split_at = max(
                    text.rfind("\n", start, end),
                    text.rfind("。", start, end),
                    text.rfind("；", start, end),
                    text.rfind(";", start, end),
                )
                if split_at > start + max_chars // 2:
                    end = split_at + 1
            chunk_text = text[start:end].strip()
            if chunk_text:
                chunk_unit = dict(unit)
                chunk_unit["chunk_id"] = f"{base_chunk_id}_part_{part_no}"
                chunk_unit["unit_type"] = str(unit.get("unit_type") or "submission_chunk")
                chunk_unit["unit_order"] = base_order * 100 + part_no if base_order else part_no
                chunk_unit["text"] = chunk_text
                chunk_unit["char_count"] = len(chunk_text)
                chunk_unit["source_chunk_id"] = base_chunk_id
                chunk_unit["chunk_part"] = part_no
                chunk_rows.append(chunk_unit)
                part_no += 1
            if end >= text_len:
                break
            start = max(0, end - overlap)
        return chunk_rows

    @staticmethod
    def _compact_text(text: Any, max_len: int = 120) -> str:
        value = re.sub(r"\s+", " ", str(text or "").strip())
        if not value:
            return ""
        if len(value) <= max_len:
            return value
        for sep in ["。", "；", ";", "，", ",", " ", "\n"]:
            idx = value.find(sep)
            if 0 < idx <= max_len:
                value = value[:idx]
                break
        return value[:max_len].strip(" ,;，；。")

    def _sanitize_retrieval_query(self, query: Any, max_len: int = 96) -> str:
        text = self._compact_text(query, max_len=max_len)
        if not text:
            return ""
        text = text.lstrip(" ._-:/\\")
        for pattern in [r"^\d+(\.\d+)+\s*", r"^第[一二三四五六七八九十百]+[章节部分]\s*"]:
            text = re.sub(pattern, "", text).strip()
        text = text.lstrip(" ._-:/\\").strip()
        if self._is_low_quality_retrieval_query(text):
            return ""
        return text

    @staticmethod
    def _is_low_quality_retrieval_query(text: Any) -> bool:
        value = str(text or "").strip()
        if not value:
            return True
        if len(value) <= 2:
            return True
        low = value.lower()
        generic_terms = {
            "关注点", "章节", "药学", "化学药", "法规", "指导原则", "ich", "申报要求", "要求", "说明",
        }
        if low in generic_terms:
            return True
        code_like_patterns = [
            r"[A-Za-z]?(?:\.\d+)+(?:\.\d+)?",
            r"(?:\d+|[A-Za-z])(?:\.\d+)+",
            r"3\.2\.[A-Za-z](?:\.\d+){1,4}",
            r"\.?[A-Za-z]\.\d+(?:\.\d+){0,3}",
        ]
        for pattern in code_like_patterns:
            if re.fullmatch(pattern, value, flags=re.IGNORECASE):
                return True
        compact = re.sub(r"\s+", "", value)
        if len(compact) >= 16:
            unique_ratio = len(set(compact)) / max(len(compact), 1)
            if unique_ratio < 0.22:
                return True
            for ch in set(compact):
                if ch.strip() and ch * 6 in compact:
                    return True
        return False


    @staticmethod
    def _infer_registration_class(project: PreReviewProject) -> str:
        for value in [
            getattr(project, "registration_leaf", ""),
            getattr(project, "registration_scope", ""),
            getattr(project, "registration_description", ""),
        ]:
            text = str(value or "").strip()
            if text:
                return PreReviewService._compact_text(text, max_len=64)
        return "药品"

    @staticmethod
    def _infer_review_domain(project: PreReviewProject) -> str:
        for value in [
            getattr(project, "registration_scope", ""),
            getattr(project, "registration_leaf", ""),
            getattr(project, "registration_description", ""),
        ]:
            text = str(value or "").strip()
            if not text:
                continue
            if "中药" in text:
                return "中药"
            if "化学" in text:
                return "化学药"
            if "生物" in text:
                return "生物制品"
        return "药学"

    @staticmethod
    def _resolve_pre_review_mode(run_config: Optional[Dict[str, Any]] = None) -> str:
        config = run_config if isinstance(run_config, dict) else {}
        strategy = str(config.get("strategy", "") or "").strip()
        workflow_mode = str(config.get("workflow_mode", "") or "").strip()
        if strategy == SINGLE_SECTION_PRE_REVIEW_V2 or workflow_mode == SINGLE_SECTION_PRE_REVIEW_V2:
            return SINGLE_SECTION_PRE_REVIEW_V2
        return "legacy"

    @staticmethod
    def _canonicalize_source_type(source_type: str) -> str:
        text = str(source_type or "").strip()
        if not text:
            return ""
        normalized = text.lower()
        for canonical, aliases in _RETRIEVAL_SOURCE_ALIASES.items():
            if text == canonical:
                return canonical
            if text in aliases or normalized in {str(x).lower() for x in aliases}:
                return canonical
        return text

    @staticmethod
    def _pharmacopeia_affect_range(review_domain: str) -> str:
        return "中药" if "中药" in str(review_domain or "") else "化学药"

    @staticmethod
    def _infer_product_type(project: Optional[PreReviewProject], section_name: str = "") -> str:
        for value in [
            getattr(project, "registration_leaf", "") if project is not None else "",
            getattr(project, "registration_description", "") if project is not None else "",
            section_name,
        ]:
            text = str(value or "").strip()
            if not text:
                continue
            if "注射" in text:
                return "注射剂"
            if "片" in text:
                return "片剂"
            if "胶囊" in text:
                return "胶囊剂"
            if "原料药" in text:
                return "原料药"
        return "化学药"

    def _load_historical_experience(
        self,
        session,
        section_id: str,
        product_type: str,
        project_id: str = "",
        limit: int = 12,
    ) -> List[Dict[str, Any]]:
        return self.memory_governance.load_historical_experience(
            session,
            project_id=project_id,
            section_id=section_id,
            product_type=product_type,
            limit=limit,
        )

    def _load_historical_bad_retrievals(
        self,
        session,
        project_id: str,
        section_id: str,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        return self.memory_governance.load_historical_bad_retrievals(
            session,
            project_id=project_id,
            section_id=section_id,
            limit=limit,
        )

    @staticmethod
    def _classification_from_source_type(source_type: str) -> str:
        canonical = PreReviewService._canonicalize_source_type(source_type)
        mapping = {
            RETRIEVAL_SOURCE_GUIDANCE: RETRIEVAL_SOURCE_GUIDANCE,
            RETRIEVAL_SOURCE_ICH: RETRIEVAL_SOURCE_ICH,
            RETRIEVAL_SOURCE_REGULATION: RETRIEVAL_SOURCE_REGULATION,
        }
        return mapping.get(canonical, "")

    @staticmethod
    def _retrieval_source_quota(max_materials: int) -> Dict[str, int]:
        total = max(5, int(max_materials or 12))
        quotas = {
            RETRIEVAL_SOURCE_GUIDANCE: 3,
            RETRIEVAL_SOURCE_ICH: 2,
            RETRIEVAL_SOURCE_REGULATION: 2,
            RETRIEVAL_SOURCE_PHARMACOPOEIA: 2,
            RETRIEVAL_SOURCE_EXPERIENCE: 1,
        }
        base_total = sum(quotas.values())
        if total <= base_total:
            scale = total / base_total
            scaled = {key: max(1, int(round(value * scale))) for key, value in quotas.items()}
            ordered_keys = [
                RETRIEVAL_SOURCE_GUIDANCE,
                RETRIEVAL_SOURCE_ICH,
                RETRIEVAL_SOURCE_REGULATION,
                RETRIEVAL_SOURCE_PHARMACOPOEIA,
                RETRIEVAL_SOURCE_EXPERIENCE,
            ]
            while sum(scaled.values()) > total:
                for key in ordered_keys:
                    if sum(scaled.values()) <= total:
                        break
                    if scaled[key] > 1:
                        scaled[key] -= 1
            return scaled
        quotas[RETRIEVAL_SOURCE_GUIDANCE] += total - base_total
        return quotas

    @staticmethod
    def _score_retrieval_material(item: Dict[str, Any], query_terms: List[str]) -> float:
        source_type = str(item.get("source_type", "") or "").strip()
        title = str(item.get("title", "") or "").strip()
        content = str(item.get("content", "") or "").strip()
        metadata = item.get("metadata", {}) if isinstance(item.get("metadata", {}), dict) else {}
        text = f"{title}\n{content}\n{metadata.get('section_path_text', '')}".strip().lower()
        score = 0.0
        for term in query_terms:
            if term and term.lower() in text:
                score += 1.0
        score += {
            RETRIEVAL_SOURCE_GUIDANCE: 0.50,
            RETRIEVAL_SOURCE_ICH: 0.40,
            RETRIEVAL_SOURCE_REGULATION: 0.35,
            RETRIEVAL_SOURCE_PHARMACOPOEIA: 0.30,
            RETRIEVAL_SOURCE_EXPERIENCE: 0.20,
        }.get(source_type, 0.0)
        if title:
            score += min(len(title) / 200.0, 0.2)
        return score

    def _finalize_retrieval_materials(
        self,
        materials: List[Dict[str, Any]],
        query_list: List[str],
        max_materials: int = 12,
    ) -> List[Dict[str, Any]]:
        if not materials:
            return []
        quotas = self._retrieval_source_quota(max_materials=max_materials)
        query_terms: List[str] = []
        for query in query_list:
            for term in re.findall(r"[A-Za-z0-9_./\\-]+|[\u4e00-\u9fff]{2,}", str(query or "")):
                text = str(term or "").strip()
                if text and text not in query_terms:
                    query_terms.append(text)

        grouped: Dict[str, List[Dict[str, Any]]] = {}
        for item in materials:
            if not isinstance(item, dict):
                continue
            normalized = dict(item)
            normalized["source_type"] = self._canonicalize_source_type(item.get("source_type", ""))
            normalized["_score"] = self._score_retrieval_material(normalized, query_terms)
            grouped.setdefault(str(normalized.get("source_type", "") or ""), []).append(normalized)

        ordered_sources = [
            RETRIEVAL_SOURCE_GUIDANCE,
            RETRIEVAL_SOURCE_ICH,
            RETRIEVAL_SOURCE_REGULATION,
            RETRIEVAL_SOURCE_PHARMACOPOEIA,
            RETRIEVAL_SOURCE_EXPERIENCE,
        ]
        selected: List[Dict[str, Any]] = []
        selected_ids = set()
        for source_type in ordered_sources:
            bucket = sorted(
                grouped.get(source_type, []),
                key=lambda x: (
                    -float(x.get("_score", 0.0) or 0.0),
                    str(x.get("title", "") or ""),
                    str(x.get("evidence_id", "") or ""),
                ),
            )
            for item in bucket[: quotas.get(source_type, 0)]:
                evidence_id = str(item.get("evidence_id", "") or "").strip()
                if not evidence_id or evidence_id in selected_ids:
                    continue
                selected_ids.add(evidence_id)
                selected.append(item)

        if len(selected) < max_materials:
            leftovers: List[Dict[str, Any]] = []
            for bucket in grouped.values():
                leftovers.extend(bucket)
            leftovers = sorted(
                leftovers,
                key=lambda x: (
                    -float(x.get("_score", 0.0) or 0.0),
                    str(x.get("title", "") or ""),
                    str(x.get("evidence_id", "") or ""),
                ),
            )
            for item in leftovers:
                evidence_id = str(item.get("evidence_id", "") or "").strip()
                if not evidence_id or evidence_id in selected_ids:
                    continue
                selected_ids.add(evidence_id)
                selected.append(item)
                if len(selected) >= max_materials:
                    break

        finalized: List[Dict[str, Any]] = []
        for item in selected[:max_materials]:
            row = dict(item)
            row.pop("_score", None)
            doc_id = str(
                row.get("doc_id", "")
                or ((row.get("metadata", {}) if isinstance(row.get("metadata", {}), dict) else {}).get("doc_id", ""))
                or ""
            ).strip()
            chunk_id = str(
                row.get("chunk_id", "")
                or ((row.get("metadata", {}) if isinstance(row.get("metadata", {}), dict) else {}).get("chunk_id", ""))
                or ""
            ).strip()
            content = str(row.get("content", "") or "").strip()
            if doc_id:
                row["doc_id"] = doc_id
            if chunk_id:
                row["chunk_id"] = chunk_id
            row["content_preview"] = self._preview(content, 220)
            row["context_snippet"] = str(row.get("content_preview", "") or "")
            finalized.append(row)
        return finalized

    def _resolve_doc_file_name_map(self, session, doc_ids: List[str]) -> Dict[str, str]:
        normalized_ids = [str(item or "").strip() for item in doc_ids if str(item or "").strip()]
        if not normalized_ids:
            return {}
        out: Dict[str, str] = {}
        kb_rows = (
            session.query(FileInfo.doc_id, FileInfo.file_name, FileInfo.file_path)
            .filter(FileInfo.doc_id.in_(normalized_ids))
            .all()
        )
        for row in kb_rows:
            doc_id = str(getattr(row, "doc_id", "") or "").strip()
            file_name = self._resolve_display_file_name(
                doc_id=doc_id,
                file_name=str(getattr(row, "file_name", "") or "").strip(),
                file_path=str(getattr(row, "file_path", "") or "").strip(),
            )
            if doc_id and file_name:
                out[doc_id] = file_name
        sub_rows = (
            session.query(
                PreReviewSubmissionFile.doc_id,
                PreReviewSubmissionFile.file_name,
                PreReviewSubmissionFile.file_path,
            )
            .filter(PreReviewSubmissionFile.doc_id.in_(normalized_ids))
            .all()
        )
        for row in sub_rows:
            doc_id = str(getattr(row, "doc_id", "") or "").strip()
            file_name = self._resolve_display_file_name(
                doc_id=doc_id,
                file_name=str(getattr(row, "file_name", "") or "").strip(),
                file_path=str(getattr(row, "file_path", "") or "").strip(),
            )
            if doc_id and file_name and doc_id not in out:
                out[doc_id] = file_name
        return out

    @staticmethod
    def _looks_like_placeholder_name(value: str, doc_id: str = "") -> bool:
        text = str(value or "").strip()
        if not text:
            return True
        normalized_doc_id = str(doc_id or "").strip()
        if normalized_doc_id and text == normalized_doc_id:
            return True
        if re.fullmatch(r"[0-9a-f]{12,64}", text.lower()):
            return True
        return False

    def _resolve_display_file_name(self, doc_id: str, file_name: str, file_path: str = "") -> str:
        normalized_doc_id = str(doc_id or "").strip()
        candidate = str(file_name or "").strip()
        if candidate:
            if normalized_doc_id and candidate.startswith(f"{normalized_doc_id}_"):
                candidate = candidate[len(normalized_doc_id) + 1 :].strip()
            elif normalized_doc_id and candidate.startswith(f"{normalized_doc_id}-"):
                candidate = candidate[len(normalized_doc_id) + 1 :].strip()
            if candidate and not self._looks_like_placeholder_name(candidate, normalized_doc_id):
                return candidate
        basename = os.path.basename(str(file_path or "").strip())
        if basename:
            if normalized_doc_id and basename.startswith(f"{normalized_doc_id}_"):
                inferred = basename[len(normalized_doc_id) + 1 :].strip()
                if inferred and not self._looks_like_placeholder_name(inferred, normalized_doc_id):
                    return inferred
            if normalized_doc_id and basename.startswith(f"{normalized_doc_id}-"):
                inferred = basename[len(normalized_doc_id) + 1 :].strip()
                if inferred and not self._looks_like_placeholder_name(inferred, normalized_doc_id):
                    return inferred
            if not self._looks_like_placeholder_name(basename, normalized_doc_id):
                return basename
        if normalized_doc_id:
            inferred = self._lookup_storage_display_name(normalized_doc_id)
            if inferred:
                return inferred
        return ""

    def _lookup_storage_display_name(self, doc_id: str) -> str:
        normalized_doc_id = str(doc_id or "").strip()
        if not normalized_doc_id:
            return ""
        search_roots = [
            settings.upload_dir,
            settings.submission_upload_dir,
            RAW_DATA_DIR,
        ]
        for root in search_roots:
            base = Path(str(root or "").strip())
            if not base.exists():
                continue
            for pattern in [f"{normalized_doc_id}_*", f"{normalized_doc_id}-*"]:
                try:
                    match = next(base.rglob(pattern), None)
                except Exception:
                    match = None
                if not match:
                    continue
                inferred = self._resolve_display_file_name(normalized_doc_id, "", str(match))
                if inferred:
                    return inferred
        return ""

    def _resolve_display_title(
        self,
        doc_id: str,
        title: str,
        doc_title: str = "",
        file_name: str = "",
        section_name: str = "",
        section_path_text: str = "",
        file_path: str = "",
    ) -> str:
        normalized_doc_id = str(doc_id or "").strip()
        for candidate in [
            str(title or "").strip(),
            str(section_name or "").strip(),
            str(section_path_text or "").strip(),
        ]:
            if candidate and not self._looks_like_placeholder_name(candidate, normalized_doc_id):
                return candidate
        for candidate in [
            self._resolve_display_file_name(normalized_doc_id, str(doc_title or "").strip(), file_path),
            self._resolve_display_file_name(normalized_doc_id, str(file_name or "").strip(), file_path),
        ]:
            if candidate:
                return candidate
        return ""

    def _enrich_retrieved_materials(self, session, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not isinstance(items, list) or not items:
            return []
        doc_ids: List[str] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            metadata = item.get("metadata", {}) if isinstance(item.get("metadata", {}), dict) else {}
            doc_id = str(item.get("doc_id", "") or metadata.get("doc_id", "") or "").strip()
            if doc_id and doc_id not in doc_ids:
                doc_ids.append(doc_id)
        file_name_map = self._resolve_doc_file_name_map(session, doc_ids)
        enriched: List[Dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            row = dict(item)
            metadata = row.get("metadata", {}) if isinstance(row.get("metadata", {}), dict) else {}
            doc_id = str(row.get("doc_id", "") or metadata.get("doc_id", "") or "").strip()
            chunk_id = str(row.get("chunk_id", "") or metadata.get("chunk_id", "") or "").strip()
            file_path = str(
                row.get("file_path", "")
                or metadata.get("file_path", "")
                or ""
            ).strip()
            raw_file_name_sources = [
                ("display_name", row.get("display_name", "")),
                ("file_name_zh", row.get("file_name_zh", "")),
                ("file_name", row.get("file_name", "")),
                ("metadata.file_name", metadata.get("file_name", "")),
                ("metadata.source_file_name", metadata.get("source_file_name", "")),
                ("file_info_lookup", file_name_map.get(doc_id, "")),
            ]
            file_name = ""
            file_name_source = ""
            for source_key, source_value in raw_file_name_sources:
                candidate = str(source_value or "").strip()
                if not candidate:
                    continue
                if source_key != "file_info_lookup":
                    candidate = self._resolve_display_file_name(
                        doc_id=doc_id,
                        file_name=candidate,
                        file_path=file_path,
                    )
                    if not candidate:
                        continue
                file_name = candidate
                file_name_source = source_key
                break
            doc_title = self._resolve_display_file_name(
                doc_id=doc_id,
                file_name=str(row.get("doc_title", "") or metadata.get("doc_title", "") or "").strip(),
                file_path=file_path,
            )
            resolved_title = self._resolve_display_title(
                doc_id=doc_id,
                title=str(row.get("title", "") or metadata.get("title", "") or "").strip(),
                doc_title=doc_title,
                file_name=file_name,
                section_name=str(row.get("section_name", "") or metadata.get("section_name", "") or "").strip(),
                section_path_text=str(
                    row.get("section_path_text", "")
                    or metadata.get("section_path_text", "")
                    or ""
                ).strip(),
                file_path=file_path,
            )
            content = str(row.get("content", "") or "").strip()
            if doc_id:
                row["doc_id"] = doc_id
            if chunk_id:
                row["chunk_id"] = chunk_id
            if file_name:
                row["file_name"] = file_name
                row["file_name_zh"] = file_name
            if doc_title:
                row["doc_title"] = doc_title
            if resolved_title:
                row["title"] = resolved_title
            row["resolved_file_name_source"] = file_name_source or "unresolved"
            row["display_name"] = str(
                file_name
                or doc_title
                or resolved_title
                or metadata.get("source_file_name", "")
                or row.get("title", "")
                or row.get("doc_title", "")
                or row.get("section_name", "")
                or doc_id
                or "-"
            ).strip() or "-"
            row["display_name_source"] = file_name_source or ("fallback" if row["display_name"] != doc_id else "doc_id")
            if content:
                row["content_preview"] = str(row.get("content_preview", "") or self._preview(content, 220))
                row["context_snippet"] = str(row.get("context_snippet", "") or row.get("content_preview", ""))
            elif str(row.get("doc_summary", "") or "").strip():
                row["content_preview"] = self._preview(str(row.get("doc_summary", "") or ""), 220)
                row["context_snippet"] = row["content_preview"]
            enriched.append(row)
        return enriched

    @staticmethod
    def _normalize_example_record(row: Any) -> Dict[str, Any]:
        payload = {}
        input_payload = {}
        output_payload = {}
        try:
            payload = json.loads(getattr(row, "payload_json", "") or "{}")
        except Exception:
            payload = {}
        try:
            input_payload = json.loads(getattr(row, "input_json", "") or "{}")
        except Exception:
            input_payload = {}
        try:
            output_payload = json.loads(getattr(row, "output_json", "") or "{}")
        except Exception:
            output_payload = {}
        return {
            "example_id": str(getattr(row, "example_id", "") or "").strip(),
            "project_id": str(getattr(row, "project_id", "") or "").strip(),
            "run_id": str(getattr(row, "run_id", "") or "").strip(),
            "section_id": str(getattr(row, "section_id", "") or "").strip(),
            "doc_id": str(getattr(row, "doc_id", "") or "").strip(),
            "example_type": str(getattr(row, "example_type", "") or "").strip(),
            "title": str(getattr(row, "title", "") or "").strip(),
            "content": str(getattr(row, "content", "") or "").strip(),
            "input": input_payload if isinstance(input_payload, dict) else {},
            "output": output_payload if isinstance(output_payload, dict) else {},
            "source_feedback_key": str(getattr(row, "source_feedback_key", "") or "").strip(),
            "payload": payload if isinstance(payload, dict) else {},
            "create_time": row.create_time.strftime("%Y-%m-%d %H:%M:%S") if getattr(row, "create_time", None) else "",
            "update_time": row.update_time.strftime("%Y-%m-%d %H:%M:%S") if getattr(row, "update_time", None) else "",
        }

    def _load_section_reference_examples(
        self,
        session,
        project_id: str,
        section_id: str,
        limit: int = 3,
    ) -> List[Dict[str, Any]]:
        return self.memory_governance.load_section_reference_examples(
            session,
            project_id=project_id,
            section_id=section_id,
            limit=limit,
        )

    def _upsert_section_example(
        self,
        session,
        project_id: str,
        section_id: str,
        doc_id: str,
        example_type: str,
        title: str,
        content: str,
        input_payload: Optional[Dict[str, Any]] = None,
        output_payload: Optional[Dict[str, Any]] = None,
        source_feedback_key: str = "",
        payload: Optional[Dict[str, Any]] = None,
        run_id: str = "",
    ) -> Dict[str, Any]:
        project_key = str(project_id or "").strip()
        section_key = str(section_id or "").strip()
        content_text = str(content or "").strip()
        if not project_key or not section_key or not content_text:
            return {}
        example_kind = str(example_type or "reference").strip() or "reference"
        title_text = str(title or f"{section_key} 示例").strip() or f"{section_key} 示例"
        source_key = str(source_feedback_key or "").strip()
        row = None
        if source_key:
            row = (
                session.query(PreReviewSectionExample)
                .filter(
                    PreReviewSectionExample.project_id == project_key,
                    PreReviewSectionExample.section_id == section_key,
                    PreReviewSectionExample.example_type == example_kind,
                    PreReviewSectionExample.source_feedback_key == source_key,
                )
                .order_by(PreReviewSectionExample.id.desc())
                .first()
            )
        now = self._now()
        if row is None:
            row = PreReviewSectionExample(
                example_id=f"example_{uuid.uuid4().hex[:12]}",
                project_id=project_key,
                run_id=str(run_id or "").strip() or None,
                section_id=section_key,
                doc_id=str(doc_id or "").strip() or None,
                example_type=example_kind,
                title=title_text,
                content=content_text,
                input_json=json.dumps(input_payload or {}, ensure_ascii=False),
                output_json=json.dumps(output_payload or {}, ensure_ascii=False),
                source_feedback_key=source_key or None,
                is_active=True,
                payload_json=json.dumps(payload or {}, ensure_ascii=False),
                create_time=now,
                update_time=now,
            )
            session.add(row)
        else:
            row.run_id = str(run_id or "").strip() or row.run_id
            row.doc_id = str(doc_id or "").strip() or row.doc_id
            row.title = title_text
            row.content = content_text
            row.input_json = json.dumps(input_payload or {}, ensure_ascii=False)
            row.output_json = json.dumps(output_payload or {}, ensure_ascii=False)
            row.is_active = True
            row.payload_json = json.dumps(payload or {}, ensure_ascii=False)
            row.update_time = now
        return self._normalize_example_record(row)

    def _execute_retrieval_plan(
        self,
        planner_result: Dict[str, Any],
        historical_experience: List[Dict[str, Any]],
        review_domain: str = "",
        focus_points: Optional[List[Any]] = None,
        section_id: str = "",
        section_name: str = "",
        progress_callback=None,
        top_k_per_query: int = 3,
        max_materials: int = 12,
    ) -> List[Dict[str, Any]]:
        def _source_titles(items: List[Dict[str, Any]], current_source: str, limit: int = 5) -> List[str]:
            titles: List[str] = []
            for item in items:
                if str(item.get("source_type", "") or "") != current_source:
                    continue
                title = str(item.get("title", "") or "").strip()
                if not title or title in titles:
                    continue
                titles.append(title)
                if len(titles) >= limit:
                    break
            return titles

        materials: List[Dict[str, Any]] = []
        seen = set()
        retrieval_plan = planner_result.get("retrieval_plan", []) if isinstance(planner_result.get("retrieval_plan", []), list) else []
        normalized_focus_points = self._normalize_text_list(focus_points or [])
        query_list = self._merge_retrieval_queries_with_focus_points(
            planner_result.get("query_list", []),
            normalized_focus_points,
        )
        if not retrieval_plan:
            retrieval_plan = [
                {"source_type": RETRIEVAL_SOURCE_GUIDANCE, "purpose": "基础规范检索", "query_subset": query_list[:2]},
                {"source_type": RETRIEVAL_SOURCE_ICH, "purpose": "ICH 技术要求检索", "query_subset": query_list[:1]},
                {"source_type": RETRIEVAL_SOURCE_REGULATION, "purpose": "法律法规依据检索", "query_subset": query_list[:1]},
                {"source_type": RETRIEVAL_SOURCE_PHARMACOPOEIA, "purpose": "药典结构化数据检索", "query_subset": query_list[:1]},
                {"source_type": RETRIEVAL_SOURCE_EXPERIENCE, "purpose": "历史经验提醒", "query_subset": query_list[:1]},
            ]
        for step in retrieval_plan:
            if not isinstance(step, dict):
                continue
            source_type = self._canonicalize_source_type(step.get("source_type", ""))
            self._emit_progress(
                progress_callback,
                "retrieval_source_start",
                f"开始检索{source_type or '未知来源'}",
                section_id=section_id,
                section_name=section_name,
                source_type=source_type,
                purpose=str(step.get("purpose", "") or "").strip(),
            )
            if source_type == RETRIEVAL_SOURCE_EXPERIENCE:
                for item in historical_experience[:3]:
                    evidence_id = f"exp:{item.get('experience_id', uuid.uuid4().hex[:8])}"
                    if evidence_id in seen:
                        continue
                    seen.add(evidence_id)
                    materials.append(
                        {
                            "source_type": RETRIEVAL_SOURCE_EXPERIENCE,
                            "title": str(item.get("knowledge_category", "") or item.get("experience_type", "") or RETRIEVAL_SOURCE_EXPERIENCE),
                            "file_name": str(item.get("knowledge_category", "") or item.get("experience_type", "") or RETRIEVAL_SOURCE_EXPERIENCE),
                            "content": str(item.get("content", "") or ""),
                            "evidence_id": evidence_id,
                            "doc_id": "",
                            "chunk_id": "",
                            "metadata": {
                                "experience_id": str(item.get("experience_id", "") or ""),
                                "scope_type": str(item.get("scope_type", "") or ""),
                                "scope_key": str(item.get("scope_key", "") or ""),
                                "knowledge_category": str(item.get("knowledge_category", "") or ""),
                                "optimization_target": str(item.get("optimization_target", "") or ""),
                            },
                        }
                    )
                self._emit_progress(
                    progress_callback,
                    "retrieval_source_done",
                    f"{source_type or '历史经验'} 检索完成",
                    section_id=section_id,
                    section_name=section_name,
                    source_type=source_type,
                    hit_count=len([x for x in materials if str(x.get("source_type", "") or "") == RETRIEVAL_SOURCE_EXPERIENCE]),
                    hit_titles=_source_titles(materials, RETRIEVAL_SOURCE_EXPERIENCE),
                )
                continue
            step_queries = self._merge_retrieval_queries_with_focus_points(
                step.get("query_subset", []) or query_list[:2],
                normalized_focus_points,
                max_queries=4,
            ) or query_list[:2]
            sanitized_step_queries: List[str] = []
            seen_queries = set()
            for raw_query in step_queries:
                cleaned = self._sanitize_retrieval_query(raw_query, max_len=120)
                if not cleaned or cleaned in seen_queries:
                    continue
                seen_queries.add(cleaned)
                sanitized_step_queries.append(cleaned)
            if not sanitized_step_queries:
                fallback_query = self._sanitize_retrieval_query(section_name, max_len=64)
                if fallback_query:
                    sanitized_step_queries = [fallback_query]
            if not sanitized_step_queries:
                self._emit_progress(
                    progress_callback,
                    "retrieval_source_done",
                    f"{source_type or '未知来源'} 检索跳过：无有效 query",
                    section_id=section_id,
                    section_name=section_name,
                    source_type=source_type,
                    hit_count=len([x for x in materials if str(x.get("source_type", "") or "") == source_type]),
                    hit_titles=_source_titles(materials, source_type),
                )
                continue
            step_queries = sanitized_step_queries
            if source_type == RETRIEVAL_SOURCE_PHARMACOPOEIA:
                affect_range = self._pharmacopeia_affect_range(review_domain)
                for query in step_queries[:3]:
                    self._emit_progress(
                        progress_callback,
                        "retrieval_query",
                        f"正在检索{source_type}：{query}",
                        section_id=section_id,
                        section_name=section_name,
                        source_type=source_type,
                        query=query,
                    )
                    result_rows = self.pharmacopeia.search_entries(query=query, affect_range=affect_range, top_k=top_k_per_query)
                    for hit in result_rows:
                        evidence_id = f"pharm:{hit.get('entry_id', '')}"
                        if not str(evidence_id).strip() or evidence_id in seen:
                            continue
                        seen.add(evidence_id)
                        materials.append(
                            {
                                "source_type": RETRIEVAL_SOURCE_PHARMACOPOEIA,
                                "title": str(hit.get("drug_name", "") or "药典条目").strip(),
                                "file_name": str(hit.get("source_file_name", "") or hit.get("drug_name", "") or "药典条目").strip(),
                                "content": str(hit.get("retrieval_text", "") or "").strip(),
                                "evidence_id": evidence_id,
                                "doc_id": str(hit.get("doc_id", "") or f"pharmacopeia:{hit.get('affect_range', '')}:{hit.get('entry_id', '')}").strip(),
                                "chunk_id": "",
                                "score": float(hit.get("score", 0.0) or 0.0),
                                "vector_score": float(hit.get("vector_score", 0.0) or 0.0),
                                "lexical_score": float(hit.get("lexical_score", 0.0) or 0.0),
                                "metadata": {
                                    "entry_id": str(hit.get("entry_id", "") or ""),
                                    "drug_name": str(hit.get("drug_name", "") or ""),
                                    "affect_range": str(hit.get("affect_range", "") or ""),
                                    "source_file_name": str(hit.get("source_file_name", "") or ""),
                                },
                            }
                        )
                self._emit_progress(
                    progress_callback,
                    "retrieval_source_done",
                    f"{source_type} 检索完成",
                    section_id=section_id,
                    section_name=section_name,
                    source_type=source_type,
                    hit_count=len([x for x in materials if str(x.get("source_type", "") or "") == RETRIEVAL_SOURCE_PHARMACOPOEIA]),
                    hit_titles=_source_titles(materials, RETRIEVAL_SOURCE_PHARMACOPOEIA),
                )
                continue
            classification = self._classification_from_source_type(source_type)
            if not classification:
                continue
            for query in step_queries[:3]:
                self._emit_progress(
                    progress_callback,
                    "retrieval_query",
                    f"正在检索{source_type}：{query}",
                    section_id=section_id,
                    section_name=section_name,
                    source_type=source_type,
                    query=query,
                )
                result = self.knowledge.semantic_query(
                    query=query,
                    top_k=top_k_per_query,
                    classification=classification,
                    min_score=0.0,
                )
                for hit in result.get("list", []) if isinstance(result, dict) else []:
                    evidence_id = f"{hit.get('doc_id', '')}:{hit.get('chunk_id', '')}"
                    if not str(evidence_id).strip() or evidence_id in seen:
                        continue
                    seen.add(evidence_id)
                    raw_doc_id = str(hit.get("doc_id", "") or "").strip()
                    raw_file_name = str(hit.get("file_name", "") or hit.get("doc_title", "") or "").strip()
                    raw_section_name = str(hit.get("section_name", "") or "").strip()
                    raw_section_path_text = str(hit.get("section_path_text", "") or "").strip()
                    materials.append(
                        {
                            "source_type": source_type or self._canonicalize_source_type(hit.get("classification", "")) or RETRIEVAL_SOURCE_GUIDANCE,
                            "title": raw_section_name or raw_section_path_text or raw_file_name or raw_doc_id,
                            "file_name": raw_file_name,
                            "content": str(hit.get("content", "") or "").strip(),
                            "evidence_id": evidence_id,
                            "doc_id": raw_doc_id,
                            "chunk_id": str(hit.get("chunk_id", "") or "").strip(),
                            "score": float(hit.get("score", 0.0) or 0.0),
                            "vector_score": float(hit.get("vector_score", 0.0) or 0.0),
                            "lexical_score": float(hit.get("lexical_score", 0.0) or 0.0),
                            "metadata": {
                                "doc_id": raw_doc_id,
                                "chunk_id": str(hit.get("chunk_id", "") or ""),
                                "classification": str(hit.get("classification", "") or ""),
                                "section_path_text": raw_section_path_text,
                                "file_name": raw_file_name,
                                "doc_title": raw_file_name,
                            },
                        }
                    )
            self._emit_progress(
                progress_callback,
                "retrieval_source_done",
                f"{source_type} 检索完成",
                section_id=section_id,
                section_name=section_name,
                source_type=source_type,
                hit_count=len([x for x in materials if str(x.get("source_type", "") or "") == source_type]),
                hit_titles=_source_titles(materials, source_type),
            )
        return self._finalize_retrieval_materials(
            materials=materials,
            query_list=query_list,
            max_materials=max_materials,
        )

    def _normalize_retrieval_evaluation_result(
        self,
        evaluation_result: Dict[str, Any],
        retrieved_materials: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        if not isinstance(evaluation_result, dict):
            evaluation_result = {}
        normalized = dict(evaluation_result)
        approved_ids = self._normalize_text_list(normalized.get("approved_evidence_ids", []))
        rejected_ids = self._normalize_text_list(normalized.get("rejected_evidence_ids", []))
        approved_rows = [dict(item) for item in normalized.get("approved_materials", []) if isinstance(item, dict)]
        rejected_rows = [dict(item) for item in normalized.get("rejected_materials", []) if isinstance(item, dict)]
        if not approved_ids:
            approved_ids = self._normalize_text_list([item.get("evidence_id", "") for item in approved_rows if isinstance(item, dict)])
        if not rejected_ids:
            rejected_ids = self._normalize_text_list([item.get("evidence_id", "") for item in rejected_rows if isinstance(item, dict)])
        normalized["approved_evidence_ids"] = approved_ids
        normalized["rejected_evidence_ids"] = rejected_ids
        normalized["approved_materials"] = approved_rows
        normalized["rejected_materials"] = rejected_rows
        normalized["coverage_by_task"] = (
            dict(normalized.get("coverage_by_task", {}))
            if isinstance(normalized.get("coverage_by_task", {}), dict)
            else {}
        )
        normalized["classified_materials"] = (
            dict(normalized.get("classified_materials", {}))
            if isinstance(normalized.get("classified_materials", {}), dict)
            else {}
        )
        normalized["evidence_bundles_by_task"] = (
            dict(normalized.get("evidence_bundles_by_task", {}))
            if isinstance(normalized.get("evidence_bundles_by_task", {}), dict)
            else {}
        )
        normalized["missing_evidence_by_task"] = [
            dict(item)
            for item in normalized.get("missing_evidence_by_task", [])
            if isinstance(normalized.get("missing_evidence_by_task", []), list) and isinstance(item, dict)
        ]
        normalized["judgment_ready"] = bool(normalized.get("judgment_ready", False))
        normalized["source_breakdown"] = dict(normalized.get("source_breakdown", {})) if isinstance(normalized.get("source_breakdown", {}), dict) else {}
        normalized["rejection_breakdown"] = dict(normalized.get("rejection_breakdown", {})) if isinstance(normalized.get("rejection_breakdown", {}), dict) else {}
        confidence = str(normalized.get("confidence", "medium") or "medium").strip().lower()
        normalized["confidence"] = confidence if confidence in {"low", "medium", "high"} else "medium"
        return normalized

    def _apply_retrieval_evaluation_result(
        self,
        retrieved_materials: List[Dict[str, Any]],
        evaluation_result: Dict[str, Any],
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        normalized = self._normalize_retrieval_evaluation_result(evaluation_result, retrieved_materials)

        def _passes_downstream_threshold(row: Dict[str, Any]) -> bool:
            score = row.get("score", 0.0)
            try:
                numeric = float(score or 0.0)
            except Exception:
                numeric = 0.0
            return numeric >= 0.5

        material_map: Dict[str, Dict[str, Any]] = {}
        for item in retrieved_materials:
            if not isinstance(item, dict):
                continue
            evidence_id = str(item.get("evidence_id", "") or "").strip() or f"{item.get('doc_id', '')}:{item.get('chunk_id', '')}"
            if evidence_id:
                material_map[evidence_id] = dict(item)
        approved: List[Dict[str, Any]] = []
        rejected: List[Dict[str, Any]] = []
        for item in normalized.get("approved_materials", []):
            evidence_id = str(item.get("evidence_id", "") or "").strip()
            base = dict(material_map.get(evidence_id, {}))
            if not base:
                continue
            if not _passes_downstream_threshold(base):
                continue
            base["evaluation_keep_reason"] = str(item.get("keep_reason", "") or "").strip()
            base["evaluation_relevance"] = str(item.get("relevance", "") or "").strip()
            approved.append(base)
        if not approved:
            for evidence_id in normalized.get("approved_evidence_ids", []):
                base = dict(material_map.get(str(evidence_id or "").strip(), {}))
                if base and _passes_downstream_threshold(base):
                    approved.append(base)
        for item in normalized.get("rejected_materials", []):
            evidence_id = str(item.get("evidence_id", "") or "").strip()
            base = dict(material_map.get(evidence_id, {}))
            if not base:
                base = {
                    "doc_id": str(item.get("doc_id", "") or "").strip(),
                    "chunk_id": str(item.get("chunk_id", "") or "").strip(),
                    "source_type": str(item.get("source_type", "") or "").strip(),
                }
            base["evaluation_reject_type"] = str(item.get("reject_type", "") or "").strip()
            base["evaluation_reject_reason"] = str(item.get("reject_reason", "") or "").strip()
            rejected.append(base)
        if not rejected:
            for evidence_id in normalized.get("rejected_evidence_ids", []):
                base = dict(material_map.get(str(evidence_id or "").strip(), {}))
                if base:
                    rejected.append(base)
        return approved, rejected

    @staticmethod
    def _map_review_result_to_legacy(review_result: Dict[str, Any]) -> Tuple[str, List[Dict[str, Any]], List[str], str]:
        section_summary = str(review_result.get("section_summary", "") or "").strip()
        supported_points = review_result.get("supported_points", []) if isinstance(review_result.get("supported_points", []), list) else []
        missing_points = review_result.get("missing_points", []) if isinstance(review_result.get("missing_points", []), list) else []
        unsupported_points = review_result.get("unsupported_points", []) if isinstance(review_result.get("unsupported_points", []), list) else []
        rule_findings = review_result.get("rule_findings", []) if isinstance(review_result.get("rule_findings", []), list) else []
        questions = review_result.get("questions", []) if isinstance(review_result.get("questions", []), list) else []
        highlighted = []
        for item in rule_findings[:5]:
            if not isinstance(item, dict):
                continue
            issue = str(item.get("issue", "") or item.get("requirement_point", "") or item.get("rule_text", "") or "").strip()
            if not issue:
                continue
            issue_type = str(item.get("issue_type", "") or "").strip().lower()
            metadata = item.get("metadata", {}) if isinstance(item.get("metadata", {}), dict) else {}
            trace = item.get("trace", {}) if isinstance(item.get("trace", {}), dict) else {}
            rule_code = str(item.get("rule_code", "") or metadata.get("rule_code", "") or trace.get("rule_code", "") or "").strip()
            rule_text = str(item.get("rule_text", "") or metadata.get("rule_text", "") or trace.get("rule_text", "") or "").strip()
            requirement_point = str(item.get("requirement_point", "") or metadata.get("requirement_point", "") or trace.get("requirement_point", "") or "").strip()
            location = str(item.get("location", "") or metadata.get("location", "") or trace.get("location", "") or "").strip()
            highlighted.append(
                {
                    "title": issue,
                    "problem_type": f"rule_{issue_type or 'violation'}",
                    "issue_type": issue_type or "non_compliance",
                    "source_type": "指南" if issue_type in {"unsupported", "conflict"} else "CTD申报资料",
                    "severity": "high" if issue_type in {"unsupported", "conflict"} else "medium",
                    "risk_level": "high" if issue_type in {"unsupported", "conflict"} else "medium",
                    "confidence": "high" if issue_type not in {"unsupported", "conflict"} else "medium",
                    "rule_code": rule_code,
                    "rule_text": rule_text,
                    "requirement_point": requirement_point,
                    "location": location,
                    "evidence": str(item.get("evidence", "") or location or "").strip(),
                    "recommendation": str(item.get("suggested_fix", "") or requirement_point or "").strip(),
                    "metadata": {
                        "location": location,
                        "rule_code": rule_code,
                        "rule_text": rule_text,
                        "requirement_point": requirement_point,
                    },
                    "trace": {
                        "location": location,
                        "rule_code": rule_code,
                        "rule_text": rule_text,
                        "requirement_point": requirement_point,
                    },
                }
            )
        for item in questions:
            if not isinstance(item, dict):
                continue
            highlighted.append(
                {
                    "title": str(item.get("issue", "") or "").strip(),
                    "problem_type": "chapter_question",
                    "issue_type": str(item.get("issue_type", "") or "missing").strip() or "missing",
                    "source_type": "CTD申报资料",
                    "severity": "medium" if unsupported_points else "low",
                    "risk_level": "medium" if unsupported_points else "low",
                    "confidence": "low" if not str(item.get("basis", "") or "").strip() else "medium",
                    "evidence": str(item.get("basis", "") or "").strip(),
                    "recommendation": str(item.get("requested_action", "") or "").strip(),
                    "metadata": {},
                    "trace": {
                        "basis": str(item.get("basis", "") or "").strip(),
                    },
                }
            )
        if not highlighted:
            for item in unsupported_points[:3] + missing_points[:3]:
                text = str(item or "").strip()
                if text:
                    highlighted.append(
                        {
                            "title": text,
                            "problem_type": "missing_or_unsupported",
                            "severity": "medium",
                            "evidence": "",
                            "recommendation": "",
                            "metadata": {},
                        }
                    )
        linked_rules = [str(x).strip() for x in review_result.get("linked_rules", []) if str(x).strip()]
        if not linked_rules and rule_findings:
            linked_rules = [
                f"{str(item.get('rule_code', '') or '').strip()}:{str(item.get('rule_text', '') or '').strip()}".strip(":")
                for item in rule_findings
                if isinstance(item, dict) and (str(item.get("rule_code", "") or "").strip() or str(item.get("rule_text", "") or "").strip())
            ]
        risk_points = review_result.get("risk_points", []) if isinstance(review_result.get("risk_points", []), list) else []
        risk_level = "low"
        if unsupported_points or len(risk_points) >= 3 or any(
            isinstance(item, dict) and str(item.get("issue_type", "") or "").strip().lower() in {"unsupported", "conflict"}
            for item in rule_findings
        ):
            risk_level = "high"
        elif missing_points or risk_points:
            risk_level = "medium"
        conclusion = section_summary or "；".join([str(x).strip() for x in supported_points[:2] + missing_points[:2] if str(x).strip()])
        return conclusion[:1000], highlighted, linked_rules, risk_level

    @staticmethod
    def _dedupe_jsonable_list(values: Any) -> List[Any]:
        if not isinstance(values, list):
            return []
        out: List[Any] = []
        seen = set()
        for item in values:
            try:
                key = json.dumps(item, ensure_ascii=False, sort_keys=True)
            except Exception:
                key = str(item)
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(item)
        return out

    def _split_chunk_into_review_windows(
        self,
        chunk: Dict[str, Any],
        max_chars: int = SECTION_REVIEW_WINDOW_MAX_CHARS,
        overlap_paragraphs: int = SECTION_REVIEW_WINDOW_OVERLAP_PARAGRAPHS,
    ) -> List[Dict[str, Any]]:
        text = str(chunk.get("text", "") or "").strip()
        if not text:
            return []
        section_id = str(chunk.get("section_id") or chunk.get("chunk_id", "") or "").strip()
        section_code = str(chunk.get("section_code") or section_id).strip() or section_id
        paragraph_blocks = chunk.get("paragraph_blocks", []) if isinstance(chunk.get("paragraph_blocks", []), list) else []
        if not paragraph_blocks:
            paragraph_blocks = self._build_paragraph_anchors(text=text, section_id=section_id, section_code=section_code)
        paragraphs = [item for item in paragraph_blocks if isinstance(item, dict) and str(item.get("text", "") or "").strip()]
        if not paragraphs:
            return [dict(chunk)]
        max_chars = max(int(max_chars or 0), 1)
        overlap_paragraphs = max(int(overlap_paragraphs or 0), 0)
        windows: List[Dict[str, Any]] = []
        start = 0
        total = len(paragraphs)
        while start < total:
            current_texts: List[str] = []
            current_blocks: List[Dict[str, Any]] = []
            current_chars = 0
            end = start
            while end < total:
                paragraph_text = str(paragraphs[end].get("text", "") or "").strip()
                if not paragraph_text:
                    end += 1
                    continue
                addition = len(paragraph_text) + (2 if current_texts else 0)
                if current_blocks and current_chars + addition > max_chars:
                    break
                current_texts.append(paragraph_text)
                current_blocks.append(dict(paragraphs[end]))
                current_chars += addition
                end += 1
                if current_chars >= max_chars:
                    break
            if not current_blocks and start < total:
                paragraph_text = str(paragraphs[start].get("text", "") or "").strip()
                current_texts = [paragraph_text]
                current_blocks = [dict(paragraphs[start])]
                end = start + 1
            if not current_blocks:
                break
            window_text = self._preview_text_blocks(current_texts)
            window_index = len(windows) + 1
            window_chunk = dict(chunk)
            window_chunk["chunk_id"] = f"{section_id}__window_{window_index}"
            window_chunk["text"] = window_text
            window_chunk["char_count"] = len(window_text)
            window_chunk["paragraph_blocks"] = current_blocks
            window_chunk["window_index"] = window_index
            window_chunk["window_count"] = 0
            window_chunk["unit_type"] = f"{str(chunk.get('unit_type', '') or 'section')}_window"
            window_chunk["window_span"] = {
                "start_paragraph_index": current_blocks[0].get("paragraph_index"),
                "end_paragraph_index": current_blocks[-1].get("paragraph_index"),
            }
            windows.append(window_chunk)
            if end >= total:
                break
            next_start = end - overlap_paragraphs
            start = next_start if next_start > start else end
        total_windows = len(windows)
        for item in windows:
            item["window_count"] = total_windows
        return windows

    def _should_window_section_chunk(
        self,
        chunk: Dict[str, Any],
        run_config: Optional[Dict[str, Any]] = None,
    ) -> bool:
        config = dict(run_config or {})
        text = str(chunk.get("text", "") or "")
        char_count = int(chunk.get("char_count", 0) or 0) or len(text)
        if char_count <= 0 or bool(config.get("disable_section_windowing", False)):
            return False
        trigger_chars = int(config.get("section_window_trigger_chars", SECTION_REVIEW_WINDOW_TRIGGER_CHARS) or SECTION_REVIEW_WINDOW_TRIGGER_CHARS)
        return char_count > max(trigger_chars, 1)

    def _aggregate_window_review_results(
        self,
        project_id: str,
        run_id: str,
        source_doc_id: str,
        chunk: Dict[str, Any],
        previous_section_meta: Optional[Dict[str, Any]],
        window_chunks: List[Dict[str, Any]],
        window_results: List[Dict[str, Any]],
        run_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        section_id = str(chunk.get("section_id") or chunk.get("chunk_id", "")).strip()
        section_code = str(chunk.get("section_code") or section_id).strip() or section_id
        section_name = str(chunk.get("section_name") or chunk.get("title") or section_code).strip() or section_code
        full_text = str(chunk.get("text", "") or "").strip()
        full_paragraph_blocks = chunk.get("paragraph_blocks", []) if isinstance(chunk.get("paragraph_blocks", []), list) else []
        if not full_paragraph_blocks:
            full_paragraph_blocks = self._build_paragraph_anchors(full_text, section_id=section_id, section_code=section_code)

        merged_review_result: Dict[str, Any] = {
            "pre_review_conclusion": "supported",
            "section_summary": "",
            "supported_points": [],
            "unsupported_points": [],
            "missing_points": [],
            "risk_points": [],
            "questions": [],
            "linked_rules": [],
            "rule_findings": [],
            "task_verdicts": [],
            "reasoning_chain_items": [],
            "problem_basis_advice_items": [],
            "medical_key_entities": [],
            "medical_key_data_points": [],
            "window_count": len(window_results),
            "windowed_review": True,
            "window_summaries": [],
        }
        merged_findings: List[Dict[str, Any]] = []
        merged_linked_rules: List[str] = []
        merged_scores: List[float] = []
        child_trace_summaries: List[Dict[str, Any]] = []
        child_summary_blocks: List[str] = []
        merged_task_questions: List[Any] = []
        merged_review_tasks: List[Any] = []
        merged_checkpoints: List[str] = []
        merged_focus_points: List[str] = []
        merged_section_rules: List[str] = []
        merged_examples: List[Any] = []
        conclusion_priority = {"supported": 1, "insufficient_information": 2, "unsupported": 3}
        highest_priority = 0

        for index, child in enumerate(window_results, start=1):
            review_result = child.get("review_result", {}) if isinstance(child.get("review_result", {}), dict) else {}
            current_conclusion = str(review_result.get("pre_review_conclusion", "") or "").strip().lower()
            highest_priority = max(highest_priority, conclusion_priority.get(current_conclusion, 0))
            for key in ["supported_points", "unsupported_points", "missing_points", "risk_points", "linked_rules"]:
                merged_review_result[key] = self._dedupe_text_list(
                    list(merged_review_result.get(key, []))
                    + self._normalize_text_list(review_result.get(key, []))
                )
            for key in [
                "questions",
                "rule_findings",
                "task_verdicts",
                "reasoning_chain_items",
                "problem_basis_advice_items",
                "medical_key_entities",
                "medical_key_data_points",
            ]:
                merged_review_result[key] = self._dedupe_jsonable_list(
                    list(merged_review_result.get(key, []))
                    + (review_result.get(key, []) if isinstance(review_result.get(key, []), list) else [])
                )
            window_summary = str(review_result.get("section_summary", "") or "").strip()
            if window_summary:
                child_summary_blocks.append(f"窗口{index}: {window_summary}")
                merged_review_result["window_summaries"].append(
                    {
                        "window_index": index,
                        "summary": window_summary,
                        "char_count": int((window_chunks[index - 1] if index - 1 < len(window_chunks) else {}).get("char_count", 0) or 0),
                    }
                )
            merged_findings.extend(child.get("findings", []) if isinstance(child.get("findings", []), list) else [])
            merged_linked_rules.extend(child.get("linked_rules", []) if isinstance(child.get("linked_rules", []), list) else [])
            if isinstance(child.get("score", None), (int, float)):
                merged_scores.append(float(child.get("score")))
            child_meta = child.get("section_meta", {}) if isinstance(child.get("section_meta", {}), dict) else {}
            merged_task_questions = self._dedupe_jsonable_list(
                merged_task_questions + (child_meta.get("task_questions", []) if isinstance(child_meta.get("task_questions", []), list) else [])
            )
            merged_review_tasks = self._dedupe_jsonable_list(
                merged_review_tasks + (child_meta.get("review_tasks", []) if isinstance(child_meta.get("review_tasks", []), list) else [])
            )
            merged_checkpoints = self._dedupe_text_list(merged_checkpoints + self._normalize_text_list(child_meta.get("review_checkpoints", [])))
            merged_focus_points = self._dedupe_text_list(merged_focus_points + self._normalize_text_list(child_meta.get("concern_points", [])))
            merged_section_rules = self._dedupe_text_list(merged_section_rules + self._normalize_text_list(child_meta.get("section_rules", [])))
            merged_examples = self._dedupe_jsonable_list(
                merged_examples + (child_meta.get("reference_examples", []) if isinstance(child_meta.get("reference_examples", []), list) else [])
            )
            child_trace_summaries.append(
                {
                    "window_index": index,
                    "char_count": int((window_chunks[index - 1] if index - 1 < len(window_chunks) else {}).get("char_count", 0) or 0),
                    "pre_review_conclusion": current_conclusion,
                    "section_summary": window_summary,
                    "risk_level": str(child.get("risk_level", "") or "").strip(),
                    "linked_rules": child.get("linked_rules", []) if isinstance(child.get("linked_rules", []), list) else [],
                }
            )

        merged_review_result["pre_review_conclusion"] = {3: "unsupported", 2: "insufficient_information", 1: "supported"}.get(highest_priority, "supported")
        aggregate_summary = self._preview_text_blocks(child_summary_blocks)
        if not aggregate_summary:
            summary_parts = []
            if merged_review_result["unsupported_points"]:
                summary_parts.append(f"发现{len(merged_review_result['unsupported_points'])}项不满足点")
            if merged_review_result["missing_points"]:
                summary_parts.append(f"发现{len(merged_review_result['missing_points'])}项待补充点")
            if merged_review_result["risk_points"]:
                summary_parts.append(f"识别{len(merged_review_result['risk_points'])}项风险点")
            aggregate_summary = "；".join(summary_parts) or f"章节分为 {len(window_results)} 个窗口完成审评。"
        merged_review_result["section_summary"] = self._preview(aggregate_summary, 1600)
        merged_review_result["linked_rules"] = self._dedupe_text_list(merged_review_result.get("linked_rules", []) + merged_linked_rules)
        merged_findings = self._dedupe_jsonable_list(merged_findings)
        merged_linked_rules = self._dedupe_text_list(merged_linked_rules or merged_review_result.get("linked_rules", []))
        score = sum(merged_scores) / len(merged_scores) if merged_scores else 0.5
        findings = self._bind_findings_to_paragraphs(
            findings=merged_findings,
            paragraph_blocks=full_paragraph_blocks,
            section_id=section_id,
            section_code=section_code,
        )
        conclusion, _, _, risk_level = self._map_review_result_to_legacy(merged_review_result)
        section_summary = {
            "structured_summary": merged_review_result["section_summary"],
            "key_facts": self._dedupe_text_list(
                [
                    str(item.get("fact", "") or item.get("title", "") or "").strip()
                    for item in merged_review_result.get("reasoning_chain_items", [])
                    if isinstance(item, dict)
                ]
            )[:12],
            "missing_items": self._normalize_text_list(merged_review_result.get("missing_points", [])),
            "draft_risks": self._normalize_text_list(merged_review_result.get("risk_points", [])),
            "review_tasks": merged_review_tasks,
            "task_questions": merged_task_questions,
            "task_verdicts": merged_review_result.get("task_verdicts", []),
            "reasoning_chain_items": merged_review_result.get("reasoning_chain_items", []),
            "problem_basis_advice_items": merged_review_result.get("problem_basis_advice_items", []),
            "medical_key_entities": merged_review_result.get("medical_key_entities", []),
            "medical_key_data_points": merged_review_result.get("medical_key_data_points", []),
            "window_summaries": merged_review_result.get("window_summaries", []),
            "source_files": [str(item.get("file_name", "") or "").strip() for item in chunk.get("attached_files", []) if isinstance(item, dict)],
        }
        section_meta = {
            "section_id": section_id,
            "section_code": section_code,
            "section_name": section_name,
            "query": "",
            "text_preview": self._preview(full_text, max_len=220),
            "char_count": len(full_text),
            "page": chunk.get("page"),
            "page_start": chunk.get("page_start"),
            "page_end": chunk.get("page_end"),
            "unit_order": chunk.get("unit_order"),
            "unit_type": chunk.get("unit_type"),
            "parent_section_id": chunk.get("parent_section_id"),
            "parent_code": chunk.get("parent_code"),
            "title_path": chunk.get("title_path", []) if isinstance(chunk.get("title_path", []), list) else [],
            "concern_points": merged_focus_points,
            "section_rules": merged_section_rules,
            "review_checkpoints": merged_checkpoints,
            "review_tasks": merged_review_tasks,
            "task_questions": merged_task_questions,
            "attached_files": chunk.get("attached_files", []),
            "paragraph_blocks": full_paragraph_blocks,
            "section_summary": section_summary,
            "reference_examples": merged_examples,
            "previous_section_id": str((previous_section_meta or {}).get("section_id", "") or ""),
            "previous_conclusion_preview": str((previous_section_meta or {}).get("conclusion_preview", "") or ""),
            "windowing": {
                "enabled": True,
                "window_count": len(window_results),
                "trigger_chars": int((run_config or {}).get("section_window_trigger_chars", SECTION_REVIEW_WINDOW_TRIGGER_CHARS) or SECTION_REVIEW_WINDOW_TRIGGER_CHARS),
                "max_window_chars": int((run_config or {}).get("section_window_max_chars", SECTION_REVIEW_WINDOW_MAX_CHARS) or SECTION_REVIEW_WINDOW_MAX_CHARS),
            },
        }
        trace_payload = {
            "workflow_mode": self._resolve_pre_review_mode(run_config),
            "section_id": section_id,
            "section_name": section_name,
            "windowed_review": True,
            "windowing": {
                "window_count": len(window_results),
                "section_char_count": len(full_text),
                "windows": child_trace_summaries,
            },
            "summary": {
                "pre_review_conclusion": merged_review_result["pre_review_conclusion"],
                "section_summary": merged_review_result["section_summary"],
                "risk_level": risk_level,
                "linked_rule_count": len(merged_linked_rules),
                "finding_count": len(findings),
            },
            "review_result": merged_review_result,
            "findings": findings,
            "trace_note": "section reviewed in paragraph windows and aggregated into one section-level conclusion",
        }
        conclusion_row = SectionConclusionRecord(
            run_id=run_id,
            section_id=section_id,
            section_name=f"{section_code} {section_name}".strip(),
            conclusion=conclusion,
            highlighted_issues=findings,
            linked_rules=merged_linked_rules,
            risk_level=risk_level,
            create_time=self._now(),
        ).to_entity()
        trace_row = SectionTraceRecord(
            run_id=run_id,
            section_id=section_id,
            trace_payload=trace_payload,
            create_time=self._now(),
        ).to_entity()
        return {
            "success": True,
            "section_id": section_id,
            "section_meta": section_meta,
            "findings": findings,
            "score": score,
            "linked_rules": merged_linked_rules,
            "risk_level": risk_level,
            "section_summary": section_summary,
            "planner_result": {"windowed_review": True, "window_count": len(window_results)},
            "task_question_result": {"windowed_review": True, "task_questions": merged_task_questions},
            "review_result": merged_review_result,
            "task_questions": merged_task_questions,
            "task_verdicts": merged_review_result.get("task_verdicts", []),
            "reasoning_chain_items": merged_review_result.get("reasoning_chain_items", []),
            "problem_basis_advice_items": merged_review_result.get("problem_basis_advice_items", []),
            "medical_key_entities": merged_review_result.get("medical_key_entities", []),
            "medical_key_data_points": merged_review_result.get("medical_key_data_points", []),
            "conclusion_row": conclusion_row,
            "trace_row": trace_row,
            "trace_payload": trace_payload,
            "audit_rows": [],
            "previous_section_meta": {
                "section_id": section_id,
                "conclusion_preview": self._preview(conclusion),
            },
        }

    def _review_single_chunk(
        self,
        project: PreReviewProject,
        project_id: str,
        run_id: str,
        source_doc_id: str,
        chunk: Dict[str, Any],
        previous_section_meta: Optional[Dict[str, Any]] = None,
        run_config: Optional[Dict[str, Any]] = None,
        progress_callback=None,
    ) -> Dict[str, Any]:
        if self._should_use_p52_rule_review(chunk):
            return self.p52_rule_review_orchestrator.review_single_chunk(
                project=project,
                project_id=project_id,
                run_id=run_id,
                source_doc_id=source_doc_id,
                chunk=chunk,
                previous_section_meta=previous_section_meta,
                run_config=run_config,
                progress_callback=progress_callback,
            )
        if not self._should_window_section_chunk(chunk, run_config=run_config):
            return self.section_review_orchestrator.review_single_chunk(
                project=project,
                project_id=project_id,
                run_id=run_id,
                source_doc_id=source_doc_id,
                chunk=chunk,
                previous_section_meta=previous_section_meta,
                run_config=run_config,
                progress_callback=progress_callback,
            )
        section_id = str(chunk.get("section_id") or chunk.get("chunk_id", "") or "").strip()
        section_code = str(chunk.get("section_code") or section_id).strip() or section_id
        section_name = str(chunk.get("section_name") or chunk.get("title") or section_code).strip() or section_code
        window_chunks = self._split_chunk_into_review_windows(
            chunk=chunk,
            max_chars=int((run_config or {}).get("section_window_max_chars", SECTION_REVIEW_WINDOW_MAX_CHARS) or SECTION_REVIEW_WINDOW_MAX_CHARS),
            overlap_paragraphs=int((run_config or {}).get("section_window_overlap_paragraphs", SECTION_REVIEW_WINDOW_OVERLAP_PARAGRAPHS) or SECTION_REVIEW_WINDOW_OVERLAP_PARAGRAPHS),
        )
        if len(window_chunks) <= 1:
            return self.section_review_orchestrator.review_single_chunk(
                project=project,
                project_id=project_id,
                run_id=run_id,
                source_doc_id=source_doc_id,
                chunk=chunk,
                previous_section_meta=previous_section_meta,
                run_config=run_config,
                progress_callback=progress_callback,
            )
        self._emit_progress(
            progress_callback,
            "section_windowing",
            f"章节 {section_code} 内容较长，拆分为 {len(window_chunks)} 个窗口分批审评".strip(),
            section_id=section_id,
            section_code=section_code,
            section_name=section_name,
            window_count=len(window_chunks),
            section_char_count=len(str(chunk.get("text", "") or "")),
        )
        child_results: List[Dict[str, Any]] = []
        current_previous_meta = dict(previous_section_meta or {})
        for index, window_chunk in enumerate(window_chunks, start=1):
            window_run_config = dict(run_config or {})
            window_run_config["disable_section_windowing"] = True
            self._emit_progress(
                progress_callback,
                "section_window_start",
                f"开始审评窗口 {index}/{len(window_chunks)}".strip(),
                section_id=section_id,
                section_code=section_code,
                section_name=section_name,
                window_index=index,
                window_count=len(window_chunks),
                window_char_count=int(window_chunk.get("char_count", 0) or 0),
            )
            child_result = self.section_review_orchestrator.review_single_chunk(
                project=project,
                project_id=project_id,
                run_id=run_id,
                source_doc_id=source_doc_id,
                chunk=window_chunk,
                previous_section_meta=current_previous_meta,
                run_config=window_run_config,
                progress_callback=progress_callback,
            )
            if not child_result.get("success"):
                return child_result
            child_results.append(child_result)
            current_previous_meta = dict(child_result.get("previous_section_meta", {}) or current_previous_meta)
        aggregate_result = self._aggregate_window_review_results(
            project_id=project_id,
            run_id=run_id,
            source_doc_id=source_doc_id,
            chunk=chunk,
            previous_section_meta=previous_section_meta,
            window_chunks=window_chunks,
            window_results=child_results,
            run_config=run_config,
        )
        self._emit_progress(
            progress_callback,
            "section_window_aggregate_done",
            f"章节 {section_code} 分批审评完成，已汇总为章节结论".strip(),
            section_id=section_id,
            section_code=section_code,
            section_name=section_name,
            window_count=len(window_chunks),
            conclusion=str((aggregate_result.get("review_result", {}) or {}).get("pre_review_conclusion", "") or ""),
        )
        return aggregate_result

    def _review_single_chunk_impl(
        self,
        project: PreReviewProject,
        project_id: str,
        run_id: str,
        source_doc_id: str,
        chunk: Dict[str, Any],
        previous_section_meta: Optional[Dict[str, Any]] = None,
        run_config: Optional[Dict[str, Any]] = None,
        progress_callback=None,
    ) -> Dict[str, Any]:
        return self.section_review_orchestrator.review_single_chunk(
            project=project,
            project_id=project_id,
            run_id=run_id,
            source_doc_id=source_doc_id,
            chunk=chunk,
            previous_section_meta=previous_section_meta,
            run_config=run_config,
            progress_callback=progress_callback,
        )

    @staticmethod
    def _should_use_p52_rule_review(chunk: Dict[str, Any]) -> bool:
        section_id = str((chunk or {}).get("section_id") or (chunk or {}).get("chunk_id", "") or "").strip().lower()
        if not section_id:
            return False
        return section_id == "3.2.p.5.2" or section_id.startswith("3.2.p.5.2.")

    @staticmethod
    def _is_p52_section_id(section_id: str) -> bool:
        normalized = str(section_id or "").strip().lower()
        if not normalized:
            return False
        return normalized == "3.2.p.5.2" or normalized.startswith("3.2.p.5.2.")

    @staticmethod
    def _build_p52_workflow_id(workflow_kind: str, run_id: str, section_id: str) -> str:
        kind = str(workflow_kind or "p52").strip().lower()
        run_key = str(run_id or "").strip() or "run"
        section_key = str(section_id or "").strip().lower() or "section"
        return f"{kind}:{run_key}:{section_key}:{uuid.uuid4().hex[:8]}"

    def _log_p52_workflow_event(
        self,
        workflow_kind: str,
        event: str,
        *,
        workflow_id: str,
        run_id: str = "",
        section_id: str = "",
        **fields: Any,
    ) -> None:
        log_agent_flow(
            "p52_workflow",
            event,
            workflow_kind=str(workflow_kind or "").strip(),
            workflow_id=str(workflow_id or "").strip(),
            run_id=str(run_id or "").strip(),
            section_id=str(section_id or "").strip(),
            **fields,
        )

    def mark_p52_experience_usage(
        self,
        *,
        experience_ids: List[str],
        success: bool = False,
    ) -> None:
        ids = [str(item or "").strip() for item in experience_ids if str(item or "").strip()]
        if not ids:
            return
        session = self.db_conn.get_session()
        try:
            rows = (
                session.query(PreReviewExperienceMemory)
                .filter(PreReviewExperienceMemory.experience_id.in_(ids))
                .all()
            )
            now = self._now()
            for row in rows:
                row.usage_count = int(getattr(row, "usage_count", 0) or 0) + 1
                if success:
                    row.success_count = int(getattr(row, "success_count", 0) or 0) + 1
                row.update_time = now
            session.commit()
        except Exception:
            session.rollback()
        finally:
            session.close()

    def create_project(
        self,
        project_name: str,
        description: str = "",
        owner: str = "",
        registration_scope: str = "",
        registration_path: Optional[List[str]] = None,
        registration_leaf: str = "",
        registration_description: str = "",
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        if not project_name:
            return False, "project_name is required", None
        session = self.db_conn.get_session()
        try:
            entity = PreReviewProject(
                project_id=self._new_project_id(),
                project_name=project_name,
                description=description,
                registration_scope=str(registration_scope or "").strip(),
                registration_path=json.dumps(registration_path or [], ensure_ascii=False),
                registration_leaf=str(registration_leaf or "").strip(),
                registration_description=str(registration_description or "").strip(),
                status="created",
                progress=0.0,
                owner=owner,
                create_time=self._now(),
                update_time=self._now(),
                is_deleted=False,
            )
            session.add(entity)
            session.flush()
            self.prompt_rule_service.ensure_project_rules(session, entity.project_id)
            if self._is_ctd_structure_project(entity):
                self._ensure_project_sections(session, entity.project_id)
            session.commit()
            return True, "project created", {
                "project_id": entity.project_id,
                "project_name": entity.project_name,
                "registration_scope": entity.registration_scope,
                "registration_path": registration_path or [],
                "registration_leaf": entity.registration_leaf,
                "registration_description": entity.registration_description,
            }
        except Exception as e:
            session.rollback()
            return False, f"create project failed: {str(e)}", None
        finally:
            session.close()

    def delete_project(self, project_id: str) -> Tuple[bool, str]:
        if str(project_id or "").strip() == GLOBAL_SECTION_RULE_PROJECT_ID:
            return False, "system project cannot be deleted"
        with self.project_task_creation_lock(project_id):
            return self._delete_project_once(project_id)

    def _delete_project_once(self, project_id: str) -> Tuple[bool, str]:
        session = self.db_conn.get_session()
        try:
            row = (
                session.query(PreReviewProject)
                .filter(and_(PreReviewProject.project_id == project_id, PreReviewProject.is_deleted == 0))
                .first()
            )
            if row is None:
                return False, "project not found"
            active_task = (
                session.query(RuntimeTask.task_id)
                .filter(
                    RuntimeTask.domain == "pre_review",
                    RuntimeTask.project_id == str(project_id or "").strip(),
                    RuntimeTask.status.in_(["pending", "running"]),
                )
                .first()
            )
            if active_task is not None:
                return False, "项目仍有后台任务运行中，请等待任务结束后再删除"
            self._purge_project_submission_files(session=session, project_id=project_id)
            self._purge_project_runtime_records(session=session, project_id=project_id)
            row.is_deleted = True
            row.status = "archived"
            row.update_time = self._now()
            session.commit()
            return True, "project deleted"
        except Exception as e:
            session.rollback()
            return False, f"delete project failed: {str(e)}"
        finally:
            session.close()

    def batch_delete_projects(self, project_ids: Optional[List[Any]] = None) -> Tuple[bool, str, Dict[str, Any]]:
        normalized_ids = self._dedupe_text_list(project_ids or [])
        if not normalized_ids:
            return False, "project_ids is required", {"deleted": [], "failed": []}
        deleted: List[str] = []
        failed: List[Dict[str, str]] = []
        for project_id in normalized_ids:
            ok, msg = self.delete_project(project_id)
            if ok:
                deleted.append(project_id)
            else:
                failed.append({"project_id": project_id, "message": msg})
        return not failed, "success" if not failed else "partial success", {
            "deleted": deleted,
            "failed": failed,
        }

    @staticmethod
    def _serialize_upload_task_row(row: Optional[PreReviewUploadTask]) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        result = None
        payload = None
        try:
            result = json.loads(getattr(row, "result_json", "") or "null")
        except Exception:
            result = None
        try:
            payload = json.loads(getattr(row, "payload_json", "") or "null")
        except Exception:
            payload = None
        return {
            "task_id": str(getattr(row, "task_id", "") or "").strip(),
            "project_id": str(getattr(row, "project_id", "") or "").strip(),
            "section_id": str(getattr(row, "section_id", "") or "").strip(),
            "task_type": str(getattr(row, "task_type", "") or "upload_submission").strip() or "upload_submission",
            "status": str(getattr(row, "status", "") or "pending").strip() or "pending",
            "file_count": int(getattr(row, "file_count", 0) or 0),
            "message": str(getattr(row, "message", "") or "").strip(),
            "result": result,
            "error_message": str(getattr(row, "error_message", "") or "").strip(),
            "payload": payload,
            "create_time": row.create_time.strftime("%Y-%m-%d %H:%M:%S") if getattr(row, "create_time", None) else "",
            "update_time": row.update_time.strftime("%Y-%m-%d %H:%M:%S") if getattr(row, "update_time", None) else "",
            "finish_time": row.finish_time.strftime("%Y-%m-%d %H:%M:%S") if getattr(row, "finish_time", None) else "",
        }

    def create_upload_task_record(
        self,
        task_id: str,
        project_id: str,
        section_id: str = "",
        file_count: int = 0,
        payload: Optional[Dict[str, Any]] = None,
        status: str = "pending",
        message: str = "",
    ) -> Optional[Dict[str, Any]]:
        session = self.db_conn.get_session()
        try:
            now = self._now()
            row = (
                session.query(PreReviewUploadTask)
                .filter(PreReviewUploadTask.task_id == str(task_id or "").strip())
                .first()
            )
            payload_json = json.dumps(payload or {}, ensure_ascii=False)
            if row is None:
                row = PreReviewUploadTask(
                    task_id=str(task_id or "").strip(),
                    project_id=str(project_id or "").strip(),
                    section_id=str(section_id or "").strip(),
                    task_type="upload_submission",
                    status=str(status or "pending").strip() or "pending",
                    file_count=max(int(file_count or 0), 0),
                    message=str(message or "").strip(),
                    result_json="",
                    error_message="",
                    payload_json=payload_json,
                    create_time=now,
                    update_time=now,
                    finish_time=None,
                )
                session.add(row)
            else:
                row.project_id = str(project_id or "").strip()
                row.section_id = str(section_id or "").strip()
                row.task_type = "upload_submission"
                row.status = str(status or row.status or "pending").strip() or "pending"
                row.file_count = max(int(file_count or 0), 0)
                row.message = str(message or "").strip()
                row.error_message = ""
                row.result_json = ""
                row.payload_json = payload_json
                row.update_time = now
                row.finish_time = None
            session.commit()
            return self._serialize_upload_task_row(row)
        except Exception:
            session.rollback()
            logger.exception("create_upload_task_record failed task_id=%s project_id=%s", task_id, project_id)
            return None
        finally:
            session.close()

    def update_upload_task_record(
        self,
        task_id: str,
        *,
        status: Optional[str] = None,
        message: Optional[str] = None,
        result: Any = None,
        error_message: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        session = self.db_conn.get_session()
        try:
            row = (
                session.query(PreReviewUploadTask)
                .filter(PreReviewUploadTask.task_id == str(task_id or "").strip())
                .first()
            )
            if row is None:
                return None
            now = self._now()
            if status is not None:
                row.status = str(status or "").strip() or row.status
            if message is not None:
                row.message = str(message or "").strip()
            if error_message is not None:
                row.error_message = str(error_message or "").strip()
            if result is not None:
                row.result_json = json.dumps(result, ensure_ascii=False)
            row.update_time = now
            row.finish_time = now if str(row.status or "").strip() in {"completed", "failed"} else None
            session.commit()
            return self._serialize_upload_task_row(row)
        except Exception:
            session.rollback()
            logger.exception("update_upload_task_record failed task_id=%s", task_id)
            return None
        finally:
            session.close()

    def get_upload_task_progress_snapshot(self, task_id: str, cursor: int = 0) -> Optional[Dict[str, Any]]:
        session = self.db_conn.get_session()
        try:
            row = (
                session.query(PreReviewUploadTask)
                .filter(PreReviewUploadTask.task_id == str(task_id or "").strip())
                .first()
            )
            if row is None:
                return None
            result = None
            try:
                result = json.loads(getattr(row, "result_json", "") or "null")
            except Exception:
                result = None
            try:
                start = max(0, int(cursor))
            except Exception:
                start = 0
            return {
                "task_id": str(getattr(row, "task_id", "") or "").strip(),
                "project_id": str(getattr(row, "project_id", "") or "").strip(),
                "source_doc_id": "",
                "section_id": str(getattr(row, "section_id", "") or "").strip(),
                "task_type": str(getattr(row, "task_type", "") or "upload_submission").strip() or "upload_submission",
                "status": str(getattr(row, "status", "") or "pending").strip() or "pending",
                "logs": [],
                "cursor": start,
                "next_cursor": start,
                "done": str(getattr(row, "status", "") or "").strip() in {"completed", "failed"},
                "result": result,
                "error_message": str(getattr(row, "error_message", "") or "").strip(),
                "created_at": row.create_time.timestamp() if getattr(row, "create_time", None) else 0,
                "updated_at": row.update_time.timestamp() if getattr(row, "update_time", None) else 0,
                "finished_at": row.finish_time.timestamp() if getattr(row, "finish_time", None) else 0,
            }
        except Exception:
            logger.exception("get_upload_task_progress_snapshot failed task_id=%s", task_id)
            return None
        finally:
            session.close()

    def _build_active_upload_task_map(self, session, project_ids: Optional[List[str]] = None) -> Dict[str, Dict[str, Any]]:
        task_list_map = self._build_active_upload_task_list_map(session, project_ids)
        return {
            project_id: items[0]
            for project_id, items in task_list_map.items()
            if isinstance(items, list) and items
        }

    def _build_active_upload_task_list_map(self, session, project_ids: Optional[List[str]] = None) -> Dict[str, List[Dict[str, Any]]]:
        normalized_ids = [str(item or "").strip() for item in (project_ids or []) if str(item or "").strip()]
        if not normalized_ids:
            return {}
        rows = (
            session.query(PreReviewUploadTask)
            .filter(
                PreReviewUploadTask.project_id.in_(normalized_ids),
                PreReviewUploadTask.task_type == "upload_submission",
                PreReviewUploadTask.status.in_(["pending", "running"]),
            )
            .order_by(desc(PreReviewUploadTask.update_time), desc(PreReviewUploadTask.id))
            .all()
        )
        task_map: Dict[str, List[Dict[str, Any]]] = {}
        for row in rows:
            project_id = str(getattr(row, "project_id", "") or "").strip()
            if not project_id:
                continue
            serialized = self._serialize_upload_task_row(row)
            if serialized:
                task_map.setdefault(project_id, [])
                task_map[project_id].append(serialized)
        return task_map

    def _purge_project_submission_files(self, session, project_id: str) -> None:
        rows = (
            session.query(PreReviewSubmissionFile)
            .filter(PreReviewSubmissionFile.project_id == project_id)
            .all()
        )
        for row in rows:
            doc_id = str(getattr(row, "doc_id", "") or "").strip()
            file_path = str(getattr(row, "file_path", "") or "").strip()
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except Exception:
                    pass
            parsed_path = os.path.join(SUBMISSION_PARSED_DIR, f"{doc_id}.json")
            if doc_id and os.path.exists(parsed_path):
                try:
                    os.remove(parsed_path)
                except Exception:
                    pass
            edit_path = self._submission_edit_path(doc_id)
            if doc_id and os.path.exists(edit_path):
                try:
                    os.remove(edit_path)
                except Exception:
                    pass
            if doc_id:
                try:
                    self.submission_vector_store.delete_by_doc(doc_id)
                except Exception:
                    pass
                self._submission_payload_cache.pop(self._submission_payload_cache_key(project_id, doc_id), None)
            row.is_deleted = True
            row.is_chunked = False
            row.chunk_ids = ""
            row.chunk_size = 0

        session.query(PreReviewProjectSection).filter(
            PreReviewProjectSection.project_id == project_id
        ).delete(synchronize_session=False)

        session.query(PreReviewSubmissionSectionContent).filter(
            PreReviewSubmissionSectionContent.project_id == project_id
        ).delete(synchronize_session=False)

        shadow_rows = (
            session.query(FileInfo)
            .filter(
                and_(
                    FileInfo.classification == "submission_material",
                    FileInfo.affect_range == "pre_review",
                    FileInfo.doc_id.in_([str(getattr(item, "doc_id", "") or "") for item in rows if str(getattr(item, "doc_id", "") or "").strip()]),
                )
            )
            .all()
        )
        for shadow in shadow_rows:
            shadow.is_deleted = True
            shadow.is_chunked = False
            shadow.chunk_ids = ""
            shadow.chunk_size = 0

    def _purge_project_runtime_records(self, session, project_id: str) -> None:
        normalized_project_id = str(project_id or "").strip()
        if not normalized_project_id or normalized_project_id == GLOBAL_SECTION_RULE_PROJECT_ID:
            return
        run_ids = [
            str(item.run_id or "").strip()
            for item in session.query(PreReviewRun.run_id).filter(PreReviewRun.project_id == normalized_project_id).all()
            if str(item.run_id or "").strip()
        ]
        if run_ids:
            session.query(PreReviewExecutionAudit).filter(
                PreReviewExecutionAudit.run_id.in_(run_ids)
            ).delete(synchronize_session=False)
            session.query(PreReviewSectionConclusion).filter(
                PreReviewSectionConclusion.run_id.in_(run_ids)
            ).delete(synchronize_session=False)
            session.query(PreReviewSectionTrace).filter(
                PreReviewSectionTrace.run_id.in_(run_ids)
            ).delete(synchronize_session=False)
            session.query(PreReviewFeedback).filter(
                PreReviewFeedback.run_id.in_(run_ids)
            ).delete(synchronize_session=False)
            session.query(PreReviewSectionOutput).filter(
                PreReviewSectionOutput.run_id.in_(run_ids)
            ).delete(synchronize_session=False)
            session.query(PreReviewFeedbackAnalysisResult).filter(
                PreReviewFeedbackAnalysisResult.run_id.in_(run_ids)
            ).delete(synchronize_session=False)
            session.query(PreReviewPatchRegistry).filter(
                PreReviewPatchRegistry.run_id.in_(run_ids)
            ).delete(synchronize_session=False)
            session.query(PreReviewRun).filter(
                PreReviewRun.run_id.in_(run_ids)
            ).delete(synchronize_session=False)

        session.query(PreReviewPromptRule).filter(
            PreReviewPromptRule.project_id == normalized_project_id
        ).delete(synchronize_session=False)
        session.query(PreReviewSectionRule).filter(
            PreReviewSectionRule.project_id == normalized_project_id
        ).delete(synchronize_session=False)
        session.query(PreReviewSectionExample).filter(
            PreReviewSectionExample.project_id == normalized_project_id
        ).delete(synchronize_session=False)
        session.query(PreReviewExperienceMemory).filter(
            PreReviewExperienceMemory.scope_key == normalized_project_id
        ).delete(synchronize_session=False)
        session.query(PreReviewUploadTask).filter(
            PreReviewUploadTask.project_id == normalized_project_id
        ).delete(synchronize_session=False)
        session.query(RuntimeTask).filter(
            RuntimeTask.domain == "pre_review",
            RuntimeTask.project_id == normalized_project_id,
        ).delete(synchronize_session=False)

    def _compute_project_module_upload_status(
        self,
        session,
        project_id: str,
        uploaded_leaf_ids: Optional[set] = None,
    ) -> Dict[str, Any]:
        catalog = self.ctd_sections.get_catalog()
        roots = catalog.get("chapter_structure", []) if isinstance(catalog, dict) else []
        leaf_sections = catalog.get("flat_sections", []) if isinstance(catalog, dict) else []
        leaf_ids = {
            str(item.get("section_id", "") or "").strip()
            for item in leaf_sections
            if isinstance(item, dict) and str(item.get("section_id", "") or "").strip()
        }
        leaves_by_module: Dict[str, List[str]] = {}
        for root in roots:
            if not isinstance(root, dict):
                continue
            root_id = str(root.get("section_id", "") or "").strip()
            if not root_id:
                continue
            leaves_by_module[root_id] = [
                sid for sid in leaf_ids if sid == root_id or sid.startswith(f"{root_id}.")
            ]
        if uploaded_leaf_ids is None:
            effective_uploaded_leaf_ids = {
                str(getattr(row, "section_id", "") or "").strip()
                for row in session.query(PreReviewSubmissionSectionContent.section_id)
                .filter(
                    PreReviewSubmissionSectionContent.project_id == project_id,
                    PreReviewSubmissionSectionContent.section_id.isnot(None),
                )
                .all()
                if str(getattr(row, "section_id", "") or "").strip() in leaf_ids
            }
            effective_uploaded_leaf_ids.update(
                {
                    str(getattr(row, "section_id", "") or "").strip()
                    for row in session.query(PreReviewSubmissionFile.section_id)
                    .filter(
                        PreReviewSubmissionFile.project_id == project_id,
                        PreReviewSubmissionFile.is_deleted == 0,
                        PreReviewSubmissionFile.section_id.isnot(None),
                    )
                    .all()
                    if str(getattr(row, "section_id", "") or "").strip() in leaf_ids
                }
            )
        else:
            effective_uploaded_leaf_ids = {
                str(item or "").strip()
                for item in uploaded_leaf_ids
                if str(item or "").strip() in leaf_ids
            }
        module_status: List[Dict[str, Any]] = []
        total_missing = 0
        for root in roots:
            if not isinstance(root, dict):
                continue
            root_id = str(root.get("section_id", "") or "").strip()
            if not root_id:
                continue
            module_leaves = leaves_by_module.get(root_id, [])
            uploaded_count = len([sid for sid in module_leaves if sid in effective_uploaded_leaf_ids])
            total_count = len(module_leaves)
            missing_count = max(total_count - uploaded_count, 0)
            total_missing += missing_count
            module_status.append(
                {
                    "module_id": root_id,
                    "module_name": str(root.get("section_name", "") or root_id).strip() or root_id,
                    "total_count": total_count,
                    "uploaded_count": uploaded_count,
                    "missing_count": missing_count,
                    "status_text": f"还差 {missing_count} 个章节",
                }
            )
        return {
            "module_status": module_status,
            "missing_total": total_missing,
            "status_text": "全部模块已上传" if total_missing == 0 else "部分章节待上传",
        }

    def list_projects(
        self,
        page: int = 1,
        page_size: int = 10,
        status: str = "",
        project_name: str = "",
        registration_scope: str = "",
        registration_leaf: str = "",
    ) -> Dict[str, Any]:
        session = self.db_conn.get_session()
        try:
            query = session.query(PreReviewProject).filter(
                PreReviewProject.is_deleted == 0,
                PreReviewProject.project_id != GLOBAL_SECTION_RULE_PROJECT_ID,
            )
            if status:
                query = query.filter(PreReviewProject.status == status)
            if str(project_name or "").strip():
                query = query.filter(PreReviewProject.project_name.ilike(f"%{str(project_name).strip()}%"))
            if str(registration_scope or "").strip():
                query = query.filter(PreReviewProject.registration_scope == str(registration_scope).strip())
            if str(registration_leaf or "").strip():
                query = query.filter(PreReviewProject.registration_leaf.ilike(f"%{str(registration_leaf).strip()}%"))
            total = query.count()
            page = max(1, int(page))
            page_size = max(1, int(page_size))
            rows = (
                query.order_by(desc(PreReviewProject.id))
                .offset((page - 1) * page_size)
                .limit(page_size)
                .all()
            )
            active_upload_task_list_map = self._build_active_upload_task_list_map(
                session,
                [str(getattr(r, "project_id", "") or "").strip() for r in rows],
            )
            project_ids = [str(getattr(r, "project_id", "") or "").strip() for r in rows]
            version_stats_map: Dict[str, Tuple[int, int]] = {}
            uploaded_section_map: Dict[str, set] = {project_id: set() for project_id in project_ids}
            if project_ids:
                version_stat_rows = (
                    session.query(
                        PreReviewRun.project_id,
                        func.max(PreReviewRun.version_no),
                        func.count(PreReviewRun.id),
                    )
                    .filter(PreReviewRun.project_id.in_(project_ids))
                    .group_by(PreReviewRun.project_id)
                    .all()
                )
                version_stats_map = {
                    str(project_id or "").strip(): (int(max_version or 0), int(version_count or 0))
                    for project_id, max_version, version_count in version_stat_rows
                }
                content_section_rows = (
                    session.query(
                        PreReviewSubmissionSectionContent.project_id,
                        PreReviewSubmissionSectionContent.section_id,
                    )
                    .filter(
                        PreReviewSubmissionSectionContent.project_id.in_(project_ids),
                        PreReviewSubmissionSectionContent.section_id.isnot(None),
                    )
                    .distinct()
                    .all()
                )
                file_section_rows = (
                    session.query(
                        PreReviewSubmissionFile.project_id,
                        PreReviewSubmissionFile.section_id,
                    )
                    .filter(
                        PreReviewSubmissionFile.project_id.in_(project_ids),
                        PreReviewSubmissionFile.is_deleted == 0,
                        PreReviewSubmissionFile.section_id.isnot(None),
                    )
                    .distinct()
                    .all()
                )
                for project_id, section_id in [*content_section_rows, *file_section_rows]:
                    project_key = str(project_id or "").strip()
                    section_key = str(section_id or "").strip()
                    if project_key and section_key:
                        uploaded_section_map.setdefault(project_key, set()).add(section_key)
            return {
                "list": [
                    {
                        **{
                            "project_id": r.project_id,
                            "project_name": r.project_name,
                            "description": r.description,
                            "registration_scope": r.registration_scope,
                            "registration_path": self._parse_json_list(r.registration_path),
                            "registration_leaf": r.registration_leaf,
                            "registration_description": r.registration_description,
                            "status": r.status,
                            "progress": r.progress,
                            "owner": r.owner,
                            "create_time": self._format_datetime(getattr(r, "create_time", None)),
                            "update_time": self._format_datetime(getattr(r, "update_time", None)),
                        },
                        "version_count": int(version_stats_map.get(str(r.project_id or "").strip(), (0, 0))[1]),
                        "current_version": int(version_stats_map.get(str(r.project_id or "").strip(), (0, 0))[0]),
                        **self._compute_project_module_upload_status(
                            session,
                            r.project_id,
                            uploaded_leaf_ids=uploaded_section_map.get(str(r.project_id or "").strip(), set()),
                        ),
                        "upload_tasks": active_upload_task_list_map.get(str(r.project_id or "").strip(), []),
                        "upload_task": (active_upload_task_list_map.get(str(r.project_id or "").strip(), []) or [None])[0],
                    }
                    for r in rows
                ],
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": (total + page_size - 1) // page_size,
            }
        finally:
            session.close()

    def get_project_detail(self, project_id: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        session = self.db_conn.get_session()
        try:
            row = (
                session.query(PreReviewProject)
                .filter(and_(PreReviewProject.project_id == project_id, PreReviewProject.is_deleted == 0))
                .first()
            )
            if row is None:
                return False, "project not found", None
            return True, "success", {
                "project_id": row.project_id,
                "project_name": row.project_name,
                "description": row.description,
                "registration_scope": row.registration_scope,
                "registration_path": self._parse_json_list(row.registration_path),
                "registration_leaf": row.registration_leaf,
                "registration_description": row.registration_description,
                "status": row.status,
                "progress": row.progress,
                "owner": row.owner,
                "create_time": self._format_datetime(getattr(row, "create_time", None)),
                "update_time": self._format_datetime(getattr(row, "update_time", None)),
            }
        finally:
            session.close()

    def upload_submission(
        self,
        project_id: str,
        file_obj,
        material_category: str = "other",
        section_id: str = "",
        upload_mode: str = "",
        parse_mode: str = "",
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]: 
        return self.submission_service.upload_submission(
            project_id=project_id,
            file_obj=file_obj,
            material_category=material_category,
            section_id=section_id,
            upload_mode=upload_mode,
            parse_mode=parse_mode,
        )

    def list_submissions(self, project_id: str, page: int = 1, page_size: int = 20) -> Dict[str, Any]:
        session = self.db_conn.get_session()
        try:
            query = session.query(PreReviewSubmissionFile).filter(
                and_(
                    PreReviewSubmissionFile.project_id == project_id,
                    PreReviewSubmissionFile.is_deleted == 0,
                )
            )
            total = query.count()
            page = max(1, int(page))
            page_size = max(1, int(page_size))
            rows = (
                query.order_by(desc(PreReviewSubmissionFile.id))
                .offset((page - 1) * page_size)
                .limit(page_size)
                .all()
            )
            items = []
            for r in rows:
                material_category = getattr(r, "material_category", "other") or "other"
                section_id = getattr(r, "section_id", "") or ""
                review_domain = self._normalize_review_domain_code(material_category)
                if not review_domain and str(section_id).startswith("3.2."):
                    review_domain = "pharmacy"
                branch_root = self._branch_root_from_section_id(str(section_id or ""))
                items.append(
                    {
                        "doc_id": r.doc_id,
                        "project_id": r.project_id,
                        "file_name": r.file_name,
                        "file_type": r.file_type,
                        "material_category": material_category,
                        "review_domain": review_domain,
                        "review_domain_label": self._review_domain_label(review_domain),
                        "branch_root": branch_root,
                        "section_id": section_id,
                        "section_code": getattr(r, "section_code", "") or "",
                        "section_name": getattr(r, "section_name", "") or "",
                        "section_path": self._parse_json_list(getattr(r, "section_path", "") or ""),
                        "is_chunked": bool(r.is_chunked),
                        "chunk_size": r.chunk_size or 0,
                        "create_time": self._format_datetime(getattr(r, "create_time", None)),
                    }
                )
            return {
                "list": items,
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": (total + page_size - 1) // page_size,
            }
        finally:
            session.close()

    def get_submission_content(
        self,
        project_id: str,
        doc_id: str,
        compact: bool = False,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        return self.submission_service.get_submission_content(
            project_id=project_id,
            doc_id=doc_id,
            compact=compact,
        )

    def save_submission_content(
        self,
        project_id: str,
        doc_id: str,
        content: str,
        section_id: str = "",
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        return self.submission_service.save_submission_content(
            project_id=project_id,
            doc_id=doc_id,
            content=content,
            section_id=section_id,
        )

    def get_submission_file_info(self, project_id: str, doc_id: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        return self.submission_service.get_submission_file_info(project_id=project_id, doc_id=doc_id)

    def get_submission_asset_file_info(
        self,
        project_id: str,
        doc_id: str,
        asset_path: str,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        return self.submission_service.get_submission_asset_file_info(
            project_id=project_id,
            doc_id=doc_id,
            asset_path=asset_path,
        )

    def get_submission_sections(
        self,
        project_id: str,
        doc_id: str,
        compact: bool = False,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        return self.submission_service.get_submission_sections(
            project_id=project_id,
            doc_id=doc_id,
            compact=compact,
        )

    def get_submission_section_overview(
        self,
        project_id: str,
        doc_id: str,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        return self.submission_service.get_submission_section_overview(
            project_id=project_id,
            doc_id=doc_id,
        )

    def get_submission_section_diagnostics(self, project_id: str, doc_id: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        return self.submission_service.get_submission_section_diagnostics(project_id=project_id, doc_id=doc_id)

    def _load_doc_chunks(self, project_id: str, doc_id: str) -> Tuple[bool, str, List[Dict[str, Any]]]:
        return self.submission_service.load_doc_chunks(project_id=project_id, doc_id=doc_id)

    def _load_submission_parsed_payload(self, project_id: str, doc_id: str) -> Tuple[bool, str, Any]:
        return self.submission_service.load_submission_parsed_payload(project_id=project_id, doc_id=doc_id)

    def _parse_submission_file(self, file_path: str, ext_hint: str = "") -> List[Dict[str, Any]]:
        """
        Submission parsing pipeline for pre-review only.
        Keep it isolated from knowledge-base semantic indexing pipeline.
        """
        ext = (ext_hint or "").strip().lower()
        if not ext and "." in os.path.basename(file_path):
            ext = os.path.basename(file_path).rsplit(".", 1)[-1].lower()
        # For PDF submissions, use heading-based markdown parser.
        if ext == "pdf":
            parse_submission_pdf_to_payload = _load_parse_submission_pdf_to_payload()
            parsed = parse_submission_pdf_to_payload(file_path=file_path)
            return parsed.get("review_units", []) if isinstance(parsed, dict) else []

        raw_chunks = ParserManager.parse(file_path, ext_hint=ext_hint)
        out: List[Dict[str, Any]] = []
        for idx, item in enumerate(raw_chunks, start=1):
            if not isinstance(item, dict):
                continue
            text = str(item.get("text", "")).strip()
            if not text:
                continue
            out.append(
                {
                    "chunk_id": str(item.get("chunk_id", f"sub_{idx}")),
                    "section_id": str(item.get("chunk_id", f"sub_{idx}")),
                    "section_code": str(item.get("chunk_id", f"sub_{idx}")),
                    "section_name": str(item.get("section_name") or item.get("title") or f"section-{idx}"),
                    "page": item.get("page"),
                    "text": text,
                    "pipeline": "submission_pre_review",
                }
            )
        return out

    def _next_version(self, session, project_id: str) -> int:
        max_ver = session.query(func.max(PreReviewRun.version_no)).filter(PreReviewRun.project_id == project_id).scalar()
        return int(max_ver or 0) + 1

    def _mark_project_failed(self, project_id: str, run_id: str = "", failure_summary=None) -> bool:
        session = self.db_conn.get_session()
        try:
            should_commit = False
            summary_saved = failure_summary is None
            row = (
                session.query(PreReviewProject)
                .filter(and_(PreReviewProject.project_id == project_id, PreReviewProject.is_deleted == 0))
                .first()
            )
            if row is not None:
                self._set_project_runtime_status(session, row, status="failed", progress=0.0)
                should_commit = True
            target_run = None
            run_key = str(run_id or "").strip()
            if run_key:
                target_run = (
                    session.query(PreReviewRun)
                    .filter(PreReviewRun.run_id == run_key, PreReviewRun.project_id == project_id)
                    .with_for_update()
                    .first()
                )
            elif row is not None:
                target_run = (
                    session.query(PreReviewRun)
                    .filter(
                        and_(
                            PreReviewRun.project_id == project_id,
                            PreReviewRun.finish_time.is_(None),
                        )
                    )
                    .order_by(desc(PreReviewRun.create_time))
                    .first()
                )
            if target_run is not None and target_run.finish_time is None:
                target_run.finish_time = self._now()
                if failure_summary is not None:
                    target_run.summary = json.dumps(failure_summary, ensure_ascii=False)
                    summary_saved = True
                should_commit = True
            if should_commit:
                session.commit()
            # 不覆盖已经结束的历史轮次，不能把未写入的失败摘要称为已保存。
            return should_commit and summary_saved
        except Exception:
            session.rollback()
            return False
        finally:
            session.close()

    def _set_project_runtime_status(
        self,
        session,
        project: Optional[PreReviewProject],
        status: str,
        progress: Optional[float] = None,
    ) -> None:
        if project is None:
            return
        project.status = str(status or "").strip() or project.status
        if progress is not None:
            project.progress = float(progress)
        project.update_time = self._now()

    def _recover_project_running_state(self, session, project: Optional[PreReviewProject], stale_after_seconds: int = 900) -> bool:
        if project is None or str(project.status or "").strip() != "running":
            return False
        latest_run = (
            session.query(PreReviewRun)
            .filter(PreReviewRun.project_id == project.project_id)
            .order_by(desc(PreReviewRun.create_time))
            .first()
        )
        unfinished_run = (
            session.query(PreReviewRun)
            .filter(
                and_(
                    PreReviewRun.project_id == project.project_id,
                    PreReviewRun.finish_time.is_(None),
                )
            )
            .order_by(desc(PreReviewRun.create_time))
            .first()
        )
        now = self._now()
        if unfinished_run is None:
            recovered_status = "completed" if latest_run is not None and latest_run.finish_time is not None else "created"
            self._set_project_runtime_status(
                session,
                project,
                status=recovered_status,
                progress=1.0 if recovered_status == "completed" else 0.0,
            )
            session.commit()
            return True
        reference_time = getattr(project, "update_time", None) or getattr(unfinished_run, "create_time", None)
        if reference_time is None:
            return False
        age_seconds = max(0.0, (now - reference_time).total_seconds())
        if age_seconds < float(stale_after_seconds):
            return False
        unfinished_run.finish_time = now
        summary_text = str(getattr(unfinished_run, "summary", "") or "").strip()
        stale_note = json.dumps(
            {
                "recovered_as": "failed",
                "reason": "stale_running_state",
                "recovered_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            },
            ensure_ascii=False,
        )
        unfinished_run.summary = f"{summary_text}\n{stale_note}".strip() if summary_text else stale_note
        self._set_project_runtime_status(session, project, status="failed", progress=0.0)
        session.commit()
        return True

    def run_pre_review(
        self,
        project_id: str,
        source_doc_id: str,
        run_config: Optional[Dict[str, Any]] = None,
        progress_callback=None,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        return self.run_orchestrator.run_pre_review(
            project_id=project_id,
            source_doc_id=source_doc_id,
            run_config=run_config,
            progress_callback=progress_callback,
        )

    def _run_pre_review_impl(
        self,
        project_id: str,
        source_doc_id: str,
        run_config: Optional[Dict[str, Any]] = None,
        progress_callback=None,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        session = self.db_conn.get_session()
        run_id = ""
        try:
            run_config = self._apply_active_prompt_version(run_config)
            workflow_mode = self._resolve_pre_review_mode(run_config)
            self._emit_progress(
                progress_callback,
                "run_start",
                "开始预审任务。",
                project_id=project_id,
                source_doc_id=source_doc_id,
                workflow_mode=workflow_mode,
                strategy=str(run_config.get("strategy", "") or ""),
            )
            project = (
                session.query(PreReviewProject)
                .filter(and_(PreReviewProject.project_id == project_id, PreReviewProject.is_deleted == 0))
                .first()
            )
            if project is None:
                return False, "project not found", None
            self._recover_project_running_state(session, project)
            if str(project.status or "").strip() == "running":
                return False, "project is already running", None

            ok, msg, chunks = self._load_doc_chunks(project_id=project_id, doc_id=source_doc_id)
            if not ok:
                return False, msg, None
            if not chunks:
                return False, "no chunk data for pre-review", None
            self._emit_progress(
                progress_callback,
                "chunks_loaded",
                f"已加载 {len(chunks)} 个章节单元。",
                project_id=project_id,
                source_doc_id=source_doc_id,
                chunk_count=len(chunks),
            )

            run_id = self._new_run_id()
            version = self._next_version(session, project_id)

            run_row = PreReviewRun(
                run_id=run_id,
                project_id=project_id,
                version_no=version,
                source_doc_id=source_doc_id,
                strategy=str(run_config.get("strategy", "multi_agent_plan_and_solve+reflection") or "multi_agent_plan_and_solve+reflection"),
                accuracy=None,
                summary="",
                create_time=self._now(),
                finish_time=None,
            )
            session.add(run_row)
            self._emit_progress(
                progress_callback,
                "run_created",
                f"已创建运行记录 {run_id}。",
                project_id=project_id,
                source_doc_id=source_doc_id,
                run_id=run_id,
                version_no=version,
            )

            self._set_project_runtime_status(session, project, status="running", progress=0.1)
            session.flush()
            session.commit()

            # Seed memory before section review:
            # 1) historical feedback memory (episodic)
            self._seed_historical_feedback_memory(project_id=project_id)
            # 2) submission structure memory (semantic)
            ordered_chunks = self._order_review_units(chunks)
            self._seed_submission_structure_memory(
                project_id=project_id,
                source_doc_id=source_doc_id,
                review_units=ordered_chunks,
            )
            self._emit_progress(
                progress_callback,
                "section_queue",
                f"待审章节数：{len(ordered_chunks)}。",
                project_id=project_id,
                source_doc_id=source_doc_id,
                run_id=run_id,
                section_count=len(ordered_chunks),
                section_ids=[str((chunk or {}).get("section_id", "") or "") for chunk in ordered_chunks if isinstance(chunk, dict)],
            )

            conclusions = []
            section_results: List[Dict[str, Any]] = []
            skipped_empty = 0
            previous_section_meta: Dict[str, Any] = {}
            for chunk in ordered_chunks:
                review_stage = 'section_review'
                section_result = self.section_review_orchestrator.review_single_chunk(
                    project=project,
                    project_id=project_id,
                    run_id=run_id,
                    source_doc_id=source_doc_id,
                    chunk=chunk,
                    previous_section_meta=previous_section_meta,
                    run_config=run_config,
                    progress_callback=progress_callback,
                )
                if not section_result.get("success"):
                    skipped_empty += 1
                    self._emit_progress(
                        progress_callback,
                        "section_skipped",
                        "章节无可用内容，已跳过。",
                        run_id=run_id,
                        section_id=str((chunk or {}).get("section_id", "") or ""),
                        section_name=str((chunk or {}).get("section_name", "") or ""),
                    )
                    continue
                conclusions.append(section_result["conclusion_row"])
                self._persist_section_review_result(session, run_id, section_result)
                session.commit()
                section_results.append(section_result)
                previous_section_meta = section_result.get("previous_section_meta", previous_section_meta)
                self._emit_progress(
                    progress_callback,
                    "section_done",
                    "章节预审完成。",
                    run_id=run_id,
                    section_id=str(section_result.get("section_id", "") or ""),
                    section_name=str(section_result.get("section_meta", {}).get("section_name", "") or ""),
                    conclusion=str((section_result.get("review_result", {}) or {}).get("pre_review_conclusion", "") or ""),
                )

            if not conclusions:
                raise ValueError("no non-empty sections after parsing")

            if workflow_mode == SINGLE_SECTION_PRE_REVIEW_V2:
                post_review = {
                    "mode": SINGLE_SECTION_PRE_REVIEW_V2,
                    "consistency_result": {},
                    "run_qa_result": {},
                    "lead_result": {},
                    "trace": [],
                    "skipped": ["consistency", "run_qa", "lead_summary"],
                }
            else:
                review_stage = 'post_review'
                post_review = self._run_post_review_agents(
                    project=project,
                    run_id=run_id,
                    source_doc_id=source_doc_id,
                    section_results=section_results,
                    run_config=run_config,
                )
            run_row.summary = json.dumps(
                {
                    "mode": workflow_mode,
                    "run_config_summary": {
                        "domain": str(run_config.get("domain", "") or ""),
                        "branch": str(run_config.get("branch", "") or ""),
                        "strategy": str(run_config.get("strategy", "") or ""),
                        "workflow_mode": str(workflow_mode or ""),
                        "feedback_loop_mode": str(run_config.get("feedback_loop_mode", "") or "feedback_optimize"),
                        "enable_feedback_optimize": bool(run_config.get("enable_feedback_optimize", True)),
                        "prompt_config": dict(run_config.get("prompt_config", {}) or {}) if isinstance(run_config.get("prompt_config", {}), dict) else {},
                    },
                    "section_conclusion_count": len(conclusions),
                    "skipped_empty_count": skipped_empty,
                    "consistency_result": post_review.get("consistency_result", {}),
                    "run_qa_result": post_review.get("run_qa_result", {}),
                    "lead_result": post_review.get("lead_result", {}),
                    "metrics": {},
                    "metrics_schema": "run_metrics_v2",
                },
                ensure_ascii=False,
            )
            self._refresh_run_metrics_summary(session, run_id)
            run_row.finish_time = self._now()

            self._set_project_runtime_status(session, project, status="completed", progress=1.0)

            session.commit()
            result = {
                "run_id": run_id,
                "version_no": version,
                "conclusion_count": len(conclusions),
                "section_output_count": len(section_results),
                "section_output_section_ids": [str(item.get("section_id", "") or "") for item in section_results],
                "schema_version": "chapter_review_v2",
                "workflow_mode": workflow_mode,
                "skipped_empty_count": skipped_empty,
                "post_review": post_review,
            }
            self._emit_progress(
                progress_callback,
                "run_done",
                "预审任务完成。",
                project_id=project_id,
                source_doc_id=source_doc_id,
                run_id=run_id,
                section_output_count=len(section_results),
                skipped_empty_count=skipped_empty,
                workflow_mode=workflow_mode,
            )
            return True, "pre-review completed", result
        except Exception as e:
            session.rollback()
            failure_result = None
            if isinstance(e, (LLMContextError, LLMExecutionError)):
                if e.stage in {"chat", "parse"}:
                    e.stage = locals().get('review_stage', 'pre_review_prepare')
                completed_ids = [str(item.get("section_id", "")) for item in locals().get("section_results", [])]
                failure_result = {
                    "run_id": run_id, "review_complete": False, "execution_status": "failed", "error": e.as_dict(),
                    "completed_section_ids": completed_ids,
                    "incomplete_section_ids": [str(item.get("section_id", "")) for item in locals().get("ordered_chunks", [])
                        if str(item.get("section_id", "")) not in completed_ids],
                    "partial_result": getattr(e, 'partial_result', {}),
                    "completed_stages": getattr(e, 'completed_stages', []),
                    "incomplete_stages": getattr(e, 'incomplete_stages', [e.stage, 'complete_report']),
                }
            persisted = self._mark_project_failed(project_id, run_id=run_id, failure_summary=failure_result)
            if failure_result is not None and not persisted:
                failure_result['persistence_status'] = 'unconfirmed'
            self._emit_progress(
                progress_callback,
                "run_failed",
                f"预审任务失败：{str(e)}",
                project_id=project_id,
                source_doc_id=source_doc_id,
                error=str(e),
            )
            if failure_result is not None:
                return False, str(e), failure_result
            return False, f"run pre-review failed: {str(e)}", None
        finally:
            session.close()

    def _build_structured_project_payload(
        self,
        session,
        project_id: str,
        source_doc_id: str = "",
        compact: bool = False,
    ) -> Dict[str, Any]:
        source_doc_key = str(source_doc_id or "").strip()
        if source_doc_key:
            cache_key = self._structured_project_payload_cache_key(
                project_id,
                source_doc_key,
                compact=compact,
            )
            cached_payload = self._structured_project_payload_cache.get(cache_key)
            if isinstance(cached_payload, dict):
                return deepcopy(cached_payload)
        catalog = self._load_project_section_catalog(session, project_id)
        if not compact:
            catalog = self._merge_catalog_with_manual_concerns(
                catalog,
                self._load_manual_concern_map(session, project_id),
            )
        chapter_structure = [] if compact else deepcopy(catalog.get("chapter_structure", []))
        flat_sections = catalog.get("flat_sections", [])
        # 解析结果以原文真实存在的标题为准。Word 可能在中间 CTD 节点（如 3.2.p.2）
        # 直接包含正文，同时标准目录又定义了 3.2.p.2.1 至 3.2.p.2.6 等叶子章节。
        # 若这里只使用叶子节点，已正确解析的父章节正文会从页面和回放诊断中消失。
        # 因此展示与回放使用完整目录层级；确实只需要叶子的数据仍保留在
        # `flat_sections` 中供相应流程使用。
        content_sections = catalog.get("all_sections", [])
        if not isinstance(content_sections, list) or not content_sections:
            content_sections = flat_sections if isinstance(flat_sections, list) else []
        section_map = catalog.get("section_map", {})
        file_query = session.query(PreReviewSubmissionFile).filter(
            and_(
                PreReviewSubmissionFile.project_id == project_id,
                PreReviewSubmissionFile.is_deleted == 0,
                PreReviewSubmissionFile.section_id.isnot(None),
            )
        )
        if source_doc_key:
            file_query = file_query.filter(PreReviewSubmissionFile.doc_id == source_doc_key)
        rows = file_query.order_by(PreReviewSubmissionFile.id.asc()).all()

        section_content_query = session.query(PreReviewSubmissionSectionContent).filter(
            PreReviewSubmissionSectionContent.project_id == project_id
        )
        if source_doc_key:
            section_content_query = section_content_query.filter(
                PreReviewSubmissionSectionContent.doc_id == source_doc_key
            )
        section_content_rows = section_content_query.order_by(
            PreReviewSubmissionSectionContent.doc_id.asc(),
            PreReviewSubmissionSectionContent.section_id.asc(),
            PreReviewSubmissionSectionContent.chunk_index.asc(),
            PreReviewSubmissionSectionContent.id.asc(),
        ).all()
        merged_section_content_rows = self._merge_submission_section_content_rows(section_content_rows)
        file_by_doc_id = {str(row.doc_id): row for row in rows}
        doc_ids_with_section_content = {
            str(
                (
                    item.get("doc_id", "")
                    if isinstance(item, dict)
                    else getattr(item, "doc_id", "")
                )
                or ""
            ).strip()
            for item in merged_section_content_rows
            if str(
                (
                    item.get("doc_id", "")
                    if isinstance(item, dict)
                    else getattr(item, "doc_id", "")
                )
                or ""
            ).strip()
        }
        attachments_by_section: Dict[str, List[Dict[str, Any]]] = {}
        content_unavailable_doc_ids: List[str] = []
        if merged_section_content_rows:
            for content_row in merged_section_content_rows:
                sid = str(content_row.get("section_id", "") or "").strip()
                if not sid:
                    continue
                file_row = file_by_doc_id.get(str(content_row.get("doc_id", "") or "").strip())
                if file_row is None:
                    continue
                raw_text = str(content_row.get("content", "") or "").strip()
                source_parser = str(content_row.get("source_parser", "") or "").strip().lower()
                is_markdown_source = source_parser in {
                    "ctd_deepseek_ocr",
                    "ctd_docx_markdown",
                    "bound_section_docx_markdown",
                    "manual_markdown_edit",
                }
                display_text = self._rewrite_submission_markdown_asset_refs(
                    project_id=project_id,
                    doc_id=str(getattr(file_row, "doc_id", "") or "").strip(),
                    text=raw_text,
                ) if is_markdown_source else raw_text
                attachments_by_section.setdefault(sid, []).append(
                    {
                        "doc_id": file_row.doc_id,
                        "file_name": file_row.file_name,
                        "file_type": file_row.file_type,
                        "material_category": getattr(file_row, "material_category", "other") or "other",
                        "section_id": sid,
                        "section_code": content_row.get("section_code", "") or sid,
                        "section_name": content_row.get("section_name", "") or "",
                        "section_path": self._parse_json_list(getattr(file_row, "section_path", "") or ""),
                        "text": raw_text,
                        "raw_text": raw_text,
                        "cleaned_markdown": display_text if is_markdown_source else "",
                        "display_text": display_text,
                        "source_parser": source_parser,
                    }
                )
        for row in rows:
            sid = str(getattr(row, "section_id", "") or "").strip()
            if not sid or sid in attachments_by_section or str(row.doc_id) in doc_ids_with_section_content:
                continue
            text_blocks: List[str] = []
            edited_text = self._load_submission_edit(row.doc_id)
            if edited_text.strip():
                text_blocks.append(edited_text.strip())
            if compact:
                ok_payload, _, payload = self.submission_service.read_submission_parsed_payload(
                    project_id=project_id,
                    doc_id=row.doc_id,
                )
            else:
                ok_payload, _, payload = self._load_submission_parsed_payload(
                    project_id=project_id,
                    doc_id=row.doc_id,
                )
            if compact and not ok_payload and not text_blocks:
                unavailable_doc_id = str(row.doc_id or "").strip()
                if unavailable_doc_id and unavailable_doc_id not in content_unavailable_doc_ids:
                    content_unavailable_doc_ids.append(unavailable_doc_id)
            if ok_payload and not text_blocks:
                units: List[Dict[str, Any]] = []
                if isinstance(payload, dict):
                    if isinstance(payload.get("review_units"), list):
                        units = payload.get("review_units") or []
                    elif isinstance(payload.get("sections"), list):
                        units = payload.get("sections") or []
                elif isinstance(payload, list):
                    units = payload
                for unit in units:
                    if not isinstance(unit, dict):
                        continue
                    text_value = str(unit.get("text") or unit.get("content") or "").strip()
                    if text_value:
                        text_blocks.append(text_value)
            raw_text = self._preview_text_blocks(text_blocks)
            display_text = self._rewrite_submission_markdown_asset_refs(
                project_id=project_id,
                doc_id=str(getattr(row, "doc_id", "") or "").strip(),
                text=raw_text,
            )
            attachments_by_section.setdefault(sid, []).append(
                {
                    "doc_id": row.doc_id,
                    "file_name": row.file_name,
                    "file_type": row.file_type,
                    "material_category": getattr(row, "material_category", "other") or "other",
                    "section_id": sid,
                    "section_code": getattr(row, "section_code", "") or sid,
                    "section_name": getattr(row, "section_name", "") or "",
                    "section_path": self._parse_json_list(getattr(row, "section_path", "") or ""),
                    "text": raw_text,
                    "raw_text": raw_text,
                    "cleaned_markdown": display_text if str(getattr(row, "file_type", "") or "").strip().lower() in {"doc", "docx", "md"} else "",
                    "display_text": display_text,
                }
            )

        sections: List[Dict[str, Any]] = []
        review_units: List[Dict[str, Any]] = []
        for index, base in enumerate(content_sections, start=1):
            sid = str(base.get("section_id", "")).strip()
            attached_files = attachments_by_section.get(sid, [])
            if compact and not attached_files:
                continue
            concern_points = [] if compact else list(base.get("concern_points") or [])
            section_rules = [] if compact else self._normalize_text_list(base.get("section_rules", []))
            raw_blocks: List[str] = []
            display_blocks: List[str] = []
            markdown_blocks: List[str] = []
            for item in attached_files:
                file_name = str(item.get("file_name", "") or "").strip()
                raw_text = str(item.get("raw_text", "") or "").strip()
                display_text = str(item.get("display_text", "") or "").strip()
                cleaned_markdown = str(item.get("cleaned_markdown", "") or "").strip()
                source_parser = str(item.get("source_parser", "") or "").strip().lower()
                is_markdown_source = source_parser == "ctd_deepseek_ocr"
                if raw_text:
                    raw_blocks.append(raw_text if is_markdown_source else f"[{file_name}]\n{raw_text}")
                if display_text:
                    display_blocks.append(display_text if is_markdown_source else f"[{file_name}]\n{display_text}")
                if cleaned_markdown:
                    markdown_blocks.append(cleaned_markdown)
            merged_raw_text = self._preview_text_blocks(raw_blocks)
            merged_display_text = self._preview_text_blocks(display_blocks)
            merged_cleaned_markdown = self._preview_text_blocks(markdown_blocks)
            content_for_display = merged_display_text or merged_raw_text
            if compact:
                # 章节浏览器只需要原始文本和渲染文本各一份。不要返回附件全文、
                # 段落锚点、审评单元或另一棵带正文的目录树；长 Word 中这些字段会
                # 多次复制同一正文，并可能超过反向代理的响应时限。
                compact_markdown = merged_cleaned_markdown
                if compact_markdown == merged_raw_text:
                    compact_markdown = ""
                section_item = {
                    "section_id": sid,
                    "section_code": str(base.get("section_code", sid) or sid),
                    "section_name": str(base.get("section_name", sid) or sid),
                    "title_path": list(base.get("title_path") or []),
                    "parent_section_id": str(base.get("parent_section_id", "") or ""),
                    "raw_content": merged_raw_text,
                    "cleaned_markdown": compact_markdown,
                    "content_preview": self._preview(content_for_display, 320),
                    "char_count": len(content_for_display),
                }
            else:
                section_item = {
                    "section_id": sid,
                    "code": base.get("section_code", sid),
                    "title": base.get("section_name", sid),
                    "section_name": base.get("section_name", sid),
                    "title_path": list(base.get("title_path") or []),
                    "parent_section_id": base.get("parent_section_id", ""),
                    "concern_points": concern_points,
                    "section_rules": section_rules,
                    "attached_files": attached_files,
                    "file_count": len(attached_files),
                    "content": merged_raw_text,
                    "raw_content": merged_raw_text,
                    "cleaned_markdown": merged_cleaned_markdown,
                    "display_content": content_for_display,
                    "content_preview": self._preview(content_for_display, 320),
                    "char_count": len(content_for_display),
                    "paragraph_blocks": self._build_paragraph_anchors(
                        text=content_for_display,
                        section_id=sid,
                        section_code=str(base.get("section_code", sid)),
                    ),
                }
            sections.append(section_item)
            if compact:
                continue
            if merged_raw_text:
                review_units.append(
                    {
                        "chunk_id": sid,
                        "section_id": sid,
                        "section_code": base.get("section_code", sid),
                        "section_name": base.get("section_name", sid),
                        "parent_section_id": base.get("parent_section_id", ""),
                        "parent_code": section_map.get(base.get("parent_section_id", ""), {}).get("section_code", ""),
                        "page": None,
                        "page_start": None,
                        "page_end": None,
                        "text": merged_raw_text,
                        "title_path": list(base.get("title_path") or []),
                        "unit_order": index,
                        "unit_type": "ctd_section_bundle",
                        "attached_files": attached_files,
                        "concern_points": concern_points,
                        "section_rules": section_rules,
                        "paragraph_blocks": section_item["paragraph_blocks"],
                    }
                )

        section_content_map = {str(item.get("section_id", "")): item for item in sections if str(item.get("section_id", ""))}

        def attach_content(nodes: List[Dict[str, Any]]) -> None:
            for node in nodes or []:
                sid = str(node.get("section_id", "")).strip()
                matched = section_content_map.get(sid, {})
                node["content"] = str(matched.get("content", "") or "")
                node["raw_content"] = str(matched.get("raw_content", "") or matched.get("content", "") or "")
                node["cleaned_markdown"] = str(matched.get("cleaned_markdown", "") or "")
                node["display_content"] = str(matched.get("display_content", "") or matched.get("content", "") or "")
                node["content_preview"] = str(matched.get("content_preview", "") or "")
                node["char_count"] = int(matched.get("char_count", 0) or 0)
                node["section_rules"] = self._normalize_text_list(matched.get("section_rules", []) or node.get("section_rules", []))
                attach_content(node.get("children_sections") or [])

        if not compact:
            attach_content(chapter_structure)

        payload = {
            "sections": sections,
            "statistics": {
                "section_total": len(sections),
                "review_unit_total": (
                    sum(1 for item in sections if int(item.get("char_count", 0) or 0) > 0)
                    if compact
                    else len(review_units)
                ),
                "attached_file_total": sum(len(v) for v in attachments_by_section.values()),
            },
            "response_mode": "compact" if compact else "full",
            "content_ready": not content_unavailable_doc_ids,
            "content_status": "ready" if not content_unavailable_doc_ids else "reparse_required",
            "content_unavailable_doc_ids": content_unavailable_doc_ids,
        }
        if not compact:
            payload.update(
                {
                    "chapter_structure": chapter_structure,
                    "leaf_sibling_groups": [],
                    "review_units": review_units,
                }
            )
        if source_doc_key:
            self._structured_project_payload_cache[cache_key] = deepcopy(payload)
        return payload

    def get_ctd_section_catalog(self, project_id: str = "") -> Dict[str, Any]:
        catalog = self.ctd_sections.get_catalog()
        session = self.db_conn.get_session()
        try:
            if project_id:
                catalog = self._load_project_section_catalog(session, project_id)
            catalog = self._merge_catalog_with_manual_concerns(
                catalog,
                self._load_manual_concern_map(session, project_id),
            )
        finally:
            session.close()
        return {
            "chapter_structure": catalog.get("chapter_structure", []),
            "root_sections": catalog.get("chapter_structure", []),
            "flat_sections": catalog.get("flat_sections", []),
            "catalog_meta": catalog.get("catalog_meta", {}),
        }

    def refresh_global_ctd_section_catalog(self) -> Dict[str, Any]:
        catalog = self.ctd_sections.refresh_catalog()
        return {
            "chapter_structure": catalog.get("chapter_structure", []),
            "root_sections": catalog.get("chapter_structure", []),
            "flat_sections": catalog.get("flat_sections", []),
            "catalog_meta": catalog.get("catalog_meta", {}),
        }

    def _create_submission_row(
        self,
        session,
        project_id: str,
        display_name: str,
        file_bytes: bytes,
        material_category: str,
        section_meta: Optional[Dict[str, Any]] = None,
        relative_path: str = "",
        explicit_section_id: str = "",
        strict_ctd_mapping: bool = False,
    ) -> Dict[str, Any]:
        section_meta = section_meta or {}
        file_type = display_name.rsplit(".", 1)[-1].lower() if "." in display_name else ""
        if not ParserManager.is_supported(file_type):
            raise ValueError(f"unsupported file type: {file_type or 'unknown'}")

        doc_id = self._new_submission_doc_id()
        storage_name = self._submission_storage_name(doc_id, display_name)
        file_path = os.path.join(SUBMISSION_UPLOAD_DIR, storage_name)
        with open(file_path, "wb") as fh:
            fh.write(file_bytes)

        if strict_ctd_mapping:
            catalog = self._load_project_section_catalog(session, project_id)
            section_map = catalog.get("section_map", {}) if isinstance(catalog.get("section_map", {}), dict) else {}
            explicit_section_key = self.ctd_sections.normalize_section_id(explicit_section_id)
            normalized_relative_path = str(relative_path or "").replace("\\", "/").strip().strip("/")
            has_nested_archive_path = "/" in normalized_relative_path
            if explicit_section_key and explicit_section_key in section_map:
                section_meta = section_map.get(explicit_section_key, {})
            branch_root = self._infer_submission_branch_root(
                material_category=material_category,
                explicit_section_id=explicit_section_id,
                section_meta=section_meta,
                display_name=display_name,
                relative_path=relative_path or display_name,
            )
            if str(section_meta.get("section_id", "") or "").strip():
                pass
            elif self._is_supported_ctd_split_root(branch_root) and (
                not has_nested_archive_path and (not explicit_section_key or explicit_section_key == branch_root)
            ):
                section_meta = section_map.get(branch_root, {})
            else:
                parsed_payload: Optional[Dict[str, Any]] = None
                if file_type == "pdf":
                    try:
                        parse_submission_pdf_to_payload = _load_parse_submission_pdf_to_payload()
                        parsed_payload = parse_submission_pdf_to_payload(file_path=file_path)
                    except Exception:
                        parsed_payload = None
                mapped = self.submission_parser_agent.map_file(
                    display_name=display_name,
                    relative_path=relative_path or display_name,
                    parsed_payload=parsed_payload,
                    catalog=catalog,
                    explicit_section_id=explicit_section_key or str(section_meta.get("section_id", "") or ""),
                )
                section_meta = mapped.get("section_meta", {}) if isinstance(mapped.get("section_meta", {}), dict) else {}
                if (
                    not str(section_meta.get("section_id", "") or "").strip()
                    and self._is_supported_ctd_split_root(branch_root)
                    and not has_nested_archive_path
                ):
                    # Fallback to branch root for strict mapping, avoids hard fail when parser/mapping misses leaf section.
                    section_meta = section_map.get(branch_root, {})
                if not str(section_meta.get("section_id", "") or "").strip():
                    mounted_meta = self._resolve_or_mount_dynamic_project_section(
                        session=session,
                        project_id=project_id,
                        explicit_section_id=explicit_section_id,
                        relative_path=relative_path,
                        display_name=display_name,
                        section_meta=section_meta,
                    )
                    if mounted_meta:
                        section_meta = mounted_meta
                    else:
                        raise ValueError(
                            f"strict ctd mapping failed for submission file: {display_name}, path={relative_path or display_name}"
                        )
            if explicit_section_key and explicit_section_key not in section_map:
                mounted_meta = self._resolve_or_mount_dynamic_project_section(
                    session=session,
                    project_id=project_id,
                    explicit_section_id=explicit_section_key,
                    relative_path=relative_path,
                    display_name=display_name,
                    section_meta=section_meta,
                )
                if mounted_meta:
                    section_meta = mounted_meta

        section_id = self.ctd_sections.normalize_section_id(section_meta.get("section_id")) or None
        section_code = self.ctd_sections.normalize_section_id(section_meta.get("section_code")) or section_id
        section_name = str(section_meta.get("section_name", "")).strip() or None
        section_path = self._safe_json_list(section_meta.get("title_path", [])) if section_id else None

        row = PreReviewSubmissionFile(
            doc_id=doc_id,
            project_id=project_id,
            file_name=display_name,
            file_path=file_path,
            file_type=file_type,
            material_category=str(material_category or "other").strip() or "other",
            section_id=section_id,
            section_code=section_code,
            section_name=section_name,
            section_path=section_path,
            is_chunked=False,
            chunk_ids="",
            chunk_size=0,
            is_deleted=False,
            create_time=self._now(),
        )
        session.add(row)
        shadow = session.query(FileInfo).filter(FileInfo.doc_id == doc_id).first()
        if shadow is None:
            session.add(
                FileInfo(
                    doc_id=doc_id,
                    file_name=display_name,
                    file_path=file_path,
                    file_type=file_type,
                    classification="submission_material",
                    affect_range="pre_review",
                    is_chunked=0,
                    chunk_ids="",
                    chunk_size=0,
                    is_deleted=1,
                    create_time=self._now().strftime("%Y-%m-%d %H:%M:%S"),
                    review_status=0,
                    review_time=None,
                )
            )
        return {
            "doc_id": doc_id,
            "project_id": project_id,
            "file_name": display_name,
            "file_type": file_type,
            "material_category": str(material_category or "other").strip() or "other",
            "section_id": section_id or "",
            "section_code": section_code or "",
            "section_name": section_name or "",
            "section_path": json.loads(section_path) if section_path else [],
            "file_path": file_path,
        }

    def _extract_zip_submission_items(
        self,
        zip_bytes: bytes,
        catalog: Optional[Dict[str, Any]] = None,
        branch_root: str = "",
    ) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        effective_catalog = catalog if isinstance(catalog, dict) else self.ctd_sections.get_catalog()
        with zipfile.ZipFile(BytesIO(zip_bytes)) as archive:
            for member in archive.infolist():
                if member.is_dir():
                    continue
                raw_name = self._decode_zip_name(member.filename)
                safe_name = self._sanitize_member_name(raw_name)
                if not safe_name:
                    continue
                display_name = os.path.basename(safe_name)
                if not display_name:
                    continue
                suffix = display_name.rsplit(".", 1)[-1].lower() if "." in display_name else ""
                if not ParserManager.is_supported(suffix):
                    continue
                path_parts = [str(x or "").strip() for x in safe_name.split("/") if str(x or "").strip()]
                inferred_branch_root = str(branch_root or "").strip()
                if not inferred_branch_root and path_parts:
                    first_part = path_parts[0]
                    for root_node in effective_catalog.get("chapter_structure", []) or []:
                        if not isinstance(root_node, dict):
                            continue
                        root_name = str(root_node.get("section_name", "") or "").strip()
                        root_id = str(root_node.get("section_id", "") or "").strip()
                        if root_name and first_part == root_name and self._is_supported_ctd_split_root(root_id):
                            inferred_branch_root = root_id
                            break
                section_meta = self._match_catalog_section_from_archive_path(
                    archive_path=safe_name,
                    catalog=effective_catalog,
                    branch_root=inferred_branch_root,
                )
                section_id = str((section_meta or {}).get("section_id", "") or "").strip()
                if not section_id:
                    dynamic_candidates = self._extract_ctd_section_candidates(safe_name)
                    section_id = dynamic_candidates[0] if dynamic_candidates else ""
                payload = archive.read(member)
                items.append(
                    {
                        "display_name": display_name,
                        "path": safe_name,
                        "file_bytes": payload,
                        "section_meta": section_meta or {},
                        "explicit_section_id": section_id or "",
                    }
                )
        return items

    def run_section_replay(
        self,
        project_id: str,
        source_doc_id: str,
        section_id: str,
        run_config: Optional[Dict[str, Any]] = None,
        progress_callback=None,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        return self.section_review_orchestrator.run_section_replay(
            project_id=project_id,
            source_doc_id=source_doc_id,
            section_id=section_id,
            run_config=run_config,
            progress_callback=progress_callback,
        )

    def _collect_leaf_descendant_section_ids(self, root_section_id: str) -> List[str]:
        root = str(root_section_id or "").strip()
        if not root:
            return []
        root_node = self.ctd_sections.get_section(root, leaf_only=False)
        if not isinstance(root_node, dict):
            return []
        out: List[str] = []

        def walk(node: Dict[str, Any]) -> None:
            if not isinstance(node, dict):
                return
            sid = str(node.get("section_id", "") or "").strip()
            children = [child for child in (node.get("children_sections") or []) if isinstance(child, dict)]
            if not children:
                if sid:
                    out.append(sid)
                return
            for child in children:
                walk(child)

        walk(root_node)
        return self._dedupe_text_list(out)

    def _build_replay_chunk_from_section_item(self, item: Dict[str, Any], unit_order: int = 1) -> Optional[Dict[str, Any]]:
        if not isinstance(item, dict):
            return None
        current_section_id = str(item.get("section_id", "") or "").strip()
        if not current_section_id:
            return None
        text = str(
            item.get("content", "")
            or item.get("raw_content", "")
            or item.get("cleaned_markdown", "")
            or item.get("display_content", "")
            or ""
        ).strip()
        if not text:
            return None
        return {
            "chunk_id": current_section_id,
            "section_id": current_section_id,
            "section_code": str(item.get("section_code", "") or current_section_id).strip() or current_section_id,
            "section_name": str(item.get("section_name", "") or current_section_id).strip() or current_section_id,
            "page": item.get("page_start"),
            "page_start": item.get("page_start"),
            "page_end": item.get("page_end"),
            "text": text,
            "title_path": list(
                item.get("title_path")
                or [str(item.get("section_name", "") or current_section_id).strip() or current_section_id]
            ),
            "unit_order": int(unit_order or 1),
            "unit_type": "section_replay_fallback",
            "concern_points": self._normalize_text_list(item.get("concern_points", [])),
            "section_rules": self._normalize_text_list(item.get("section_rules", [])),
        }

    @staticmethod
    def _section_within_scope(section_id: str, scope_section_id: str) -> bool:
        current = str(section_id or "").strip()
        scope = str(scope_section_id or "").strip()
        if not current or not scope:
            return False
        return current == scope or current.startswith(f"{scope}.") or scope.startswith(f"{current}.")

    def run_module_replay(
        self,
        project_id: str,
        source_doc_id: str,
        module_section_id: str,
        run_config: Optional[Dict[str, Any]] = None,
        progress_callback=None,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        run_config = dict(run_config or {})
        run_config.setdefault("workflow_mode", "module_replay")
        run_config.setdefault("strategy", "module_replay")
        return self._run_module_replay_impl(
            project_id=project_id,
            source_doc_id=source_doc_id,
            module_section_id=module_section_id,
            run_config=run_config,
            progress_callback=progress_callback,
        )

    def _run_section_replay_impl(
        self,
        project_id: str,
        source_doc_id: str,
        section_id: str,
        run_config: Optional[Dict[str, Any]] = None,
        progress_callback=None,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        run_config = self._apply_active_prompt_version(run_config)
        target_section_id = str(section_id or "").strip().lower()
        descendant_leaf_ids = self._collect_leaf_descendant_section_ids(target_section_id)
        if len(descendant_leaf_ids) > 1:
            scoped_run_config = dict(run_config or {})
            scoped_run_config.setdefault("workflow_mode", "section_scope_replay")
            scoped_run_config.setdefault("strategy", "section_scope_replay")
            return self._run_module_replay_impl(
                project_id=project_id,
                source_doc_id=source_doc_id,
                module_section_id=target_section_id,
                run_config=scoped_run_config,
                progress_callback=progress_callback,
            )
        session = self.db_conn.get_session()
        try:
            project = (
                session.query(PreReviewProject)
                .filter(and_(PreReviewProject.project_id == project_id, PreReviewProject.is_deleted == 0))
                .first()
            )
        finally:
            session.close()
        if project is None:
            return False, "project not found", None
        ok, msg, chunks = self._load_doc_chunks(project_id=project_id, doc_id=source_doc_id)
        if not ok:
            return False, msg, None
        ordered_chunks = self._order_review_units(chunks)
        target_chunk: Optional[Dict[str, Any]] = None
        previous_section_meta: Dict[str, Any] = {}
        for chunk in ordered_chunks:
            current_section_id = str(chunk.get("section_id") or chunk.get("chunk_id", "")).strip()
            if current_section_id == target_section_id:
                target_chunk = chunk
                break
            text = str(chunk.get("text", "")).strip()
            if text:
                section_name = str(chunk.get("section_name") or chunk.get("title") or current_section_id)
                previous_section_meta = {
                    "section_id": current_section_id,
                    "conclusion_preview": self._preview(f"{current_section_id} {section_name}", max_len=180),
                }
        if target_chunk is None:
            ok_sections, _, payload = self.get_submission_sections(project_id=project_id, doc_id=source_doc_id)
            if ok_sections and isinstance(payload, dict):
                for item in payload.get("sections", []) if isinstance(payload.get("sections", []), list) else []:
                    if not isinstance(item, dict):
                        continue
                    current_section_id = str(item.get("section_id", "") or "").strip()
                    if current_section_id != target_section_id:
                        continue
                    text = str(
                        item.get("content", "")
                        or item.get("raw_content", "")
                        or item.get("cleaned_markdown", "")
                        or item.get("display_content", "")
                        or ""
                    ).strip()
                    if not text:
                        break
                    target_chunk = {
                        "chunk_id": current_section_id,
                        "section_id": current_section_id,
                        "section_code": str(item.get("section_code", "") or current_section_id).strip() or current_section_id,
                        "section_name": str(item.get("section_name", "") or current_section_id).strip() or current_section_id,
                        "page": item.get("page_start"),
                        "page_start": item.get("page_start"),
                        "page_end": item.get("page_end"),
                        "text": text,
                        "title_path": list(item.get("title_path") or [str(item.get("section_name", "") or current_section_id).strip() or current_section_id]),
                        "unit_order": 1,
                        "unit_type": "section_replay_fallback",
                        "concern_points": self._normalize_text_list(item.get("concern_points", [])),
                        "section_rules": self._normalize_text_list(item.get("section_rules", [])),
                    }
                    break
        if target_chunk is None:
            available_section_ids = []
            for chunk in ordered_chunks:
                sid = str(chunk.get("section_id") or chunk.get("chunk_id", "")).strip()
                if sid:
                    available_section_ids.append(sid)
            return False, (
                f"section not found in doc payload: {target_section_id}; "
                f"available sections: {', '.join(available_section_ids[:12])}"
            ), None

        replay_run_id = str(run_config.get("replay_run_id", "") or self._new_run_id())
        self._seed_historical_feedback_memory(project_id=project_id)
        self._seed_submission_structure_memory(
            project_id=project_id,
            source_doc_id=source_doc_id,
            review_units=ordered_chunks,
        )
        section_result = self._review_single_chunk(
            project=project,
            project_id=project_id,
            run_id=replay_run_id,
            source_doc_id=source_doc_id,
            chunk=target_chunk,
            previous_section_meta=previous_section_meta,
            run_config=run_config,
            progress_callback=progress_callback,
        )
        if not section_result.get("success"):
            return False, str(section_result.get("message", "section replay failed")), None

        session = self.db_conn.get_session()
        conclusion_record = {}
        trace_record = {}
        try:
            version = self._next_version(session, project_id)
            replay_run = PreReviewRun(
                run_id=replay_run_id,
                project_id=project_id,
                version_no=version,
                source_doc_id=source_doc_id,
                strategy=str(run_config.get("strategy", "section_replay") or "section_replay"),
                accuracy=None,
                summary="",
                create_time=self._now(),
                finish_time=None,
            )
            session.add(replay_run)
            self._persist_section_review_result(session, replay_run_id, section_result)
            replay_run.summary = json.dumps(
                {
                    "mode": "section_replay",
                    "run_config_summary": {
                        "domain": str(run_config.get("domain", "") or ""),
                        "branch": str(run_config.get("branch", "") or ""),
                        "strategy": str(run_config.get("strategy", "section_replay") or "section_replay"),
                        "workflow_mode": "section_replay",
                        "feedback_loop_mode": str(run_config.get("feedback_loop_mode", "") or "feedback_optimize"),
                        "enable_feedback_optimize": bool(run_config.get("enable_feedback_optimize", True)),
                        "prompt_config": dict(run_config.get("prompt_config", {}) or {}) if isinstance(run_config.get("prompt_config", {}), dict) else {},
                    },
                    "section_conclusion_count": 1,
                    "skipped_empty_count": 0,
                    "metrics": {},
                    "metrics_schema": "run_metrics_v2",
                },
                ensure_ascii=False,
            )
            self._refresh_run_metrics_summary(session, replay_run_id)
            replay_run.finish_time = self._now()
            session.flush()
            conclusion_record = SectionConclusionRecord.from_entity(section_result["conclusion_row"]).to_dict()
            trace_record = SectionTraceRecord.from_entity(section_result["trace_row"]).to_dict()
            session.commit()
        except Exception as exc:
            session.rollback()
            return False, f"section replay persist failed: {str(exc)}", None
        finally:
            session.close()

        return True, "section replay completed", {
            "run_id": replay_run_id,
            "project_id": project_id,
            "source_doc_id": source_doc_id,
            "section_id": target_section_id,
            "strategy": str(run_config.get("strategy", "section_replay") or "section_replay"),
            "section_meta": section_result.get("section_meta", {}),
            "conclusion": conclusion_record,
            "trace": trace_record,
        }

    def _run_module_replay_impl(
        self,
        project_id: str,
        source_doc_id: str,
        module_section_id: str,
        run_config: Optional[Dict[str, Any]] = None,
        progress_callback=None,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        run_config = self._apply_active_prompt_version(run_config)
        target_root = str(module_section_id or "").strip()
        if not target_root:
            return False, "module_section_id is required", None
        target_leaf_ids = self._collect_leaf_descendant_section_ids(target_root)
        if not target_leaf_ids:
            target_leaf_ids = [target_root]

        session = self.db_conn.get_session()
        try:
            project = (
                session.query(PreReviewProject)
                .filter(and_(PreReviewProject.project_id == project_id, PreReviewProject.is_deleted == 0))
                .first()
            )
        finally:
            session.close()
        if project is None:
            return False, "project not found", None

        ok, msg, chunks = self._load_doc_chunks(project_id=project_id, doc_id=source_doc_id)
        if not ok:
            return False, msg, None
        ordered_chunks = self._order_review_units(chunks)
        target_leaf_set = set(target_leaf_ids)
        target_chunks: List[Dict[str, Any]] = []
        seen_section_ids = set()
        for chunk in ordered_chunks:
            if not isinstance(chunk, dict):
                continue
            current_section_id = str(chunk.get("section_id") or chunk.get("chunk_id", "")).strip()
            if not current_section_id or current_section_id not in target_leaf_set:
                continue
            text = str(chunk.get("text", "") or "").strip()
            if not text:
                continue
            if current_section_id in seen_section_ids:
                continue
            seen_section_ids.add(current_section_id)
            target_chunks.append(chunk)

        if len(target_chunks) < len(target_leaf_ids):
            ok_sections, _, payload = self.get_submission_sections(project_id=project_id, doc_id=source_doc_id)
            if ok_sections and isinstance(payload, dict):
                for idx, item in enumerate(payload.get("sections", []) if isinstance(payload.get("sections", []), list) else [], start=1):
                    if not isinstance(item, dict):
                        continue
                    current_section_id = str(item.get("section_id", "") or "").strip()
                    if not current_section_id or current_section_id not in target_leaf_set or current_section_id in seen_section_ids:
                        continue
                    fallback_chunk = self._build_replay_chunk_from_section_item(item, unit_order=idx)
                    if fallback_chunk is None:
                        continue
                    seen_section_ids.add(current_section_id)
                    target_chunks.append(fallback_chunk)

        if not target_chunks:
            for chunk in ordered_chunks:
                if not isinstance(chunk, dict):
                    continue
                current_section_id = str(chunk.get("section_id") or chunk.get("chunk_id", "")).strip()
                if not self._section_within_scope(current_section_id, target_root) or current_section_id in seen_section_ids:
                    continue
                text = str(chunk.get("text", "") or "").strip()
                if not text:
                    continue
                seen_section_ids.add(current_section_id)
                target_chunks.append(chunk)

        if not target_chunks:
            ok_sections, _, payload = self.get_submission_sections(project_id=project_id, doc_id=source_doc_id)
            if ok_sections and isinstance(payload, dict):
                for idx, item in enumerate(payload.get("sections", []) if isinstance(payload.get("sections", []), list) else [], start=1):
                    if not isinstance(item, dict):
                        continue
                    current_section_id = str(item.get("section_id", "") or "").strip()
                    if (
                        not self._section_within_scope(current_section_id, target_root)
                        or current_section_id in seen_section_ids
                    ):
                        continue
                    fallback_chunk = self._build_replay_chunk_from_section_item(item, unit_order=idx)
                    if fallback_chunk is None:
                        continue
                    seen_section_ids.add(current_section_id)
                    target_chunks.append(fallback_chunk)

        if not target_chunks:
            return False, f"no replayable leaf sections found under module: {target_root}", None

        target_chunks.sort(key=lambda item: int(item.get("unit_order", 0) or 0))
        replay_run_id = str(run_config.get("replay_run_id", "") or self._new_run_id())
        self._seed_historical_feedback_memory(project_id=project_id)
        self._seed_submission_structure_memory(
            project_id=project_id,
            source_doc_id=source_doc_id,
            review_units=ordered_chunks,
        )
        self._emit_progress(
            progress_callback,
            "module_start",
            f"开始模块审评：{target_root}",
            project_id=project_id,
            source_doc_id=source_doc_id,
            run_id=replay_run_id,
            module_section_id=target_root,
            section_ids=target_leaf_ids,
            section_count=len(target_chunks),
        )

        session = self.db_conn.get_session()
        conclusion_records: List[Any] = []
        previous_section_meta: Dict[str, Any] = {}
        section_results: List[Dict[str, Any]] = []
        skipped_empty = 0
        try:
            version = self._next_version(session, project_id)
            replay_run = PreReviewRun(
                run_id=replay_run_id,
                project_id=project_id,
                version_no=version,
                source_doc_id=source_doc_id,
                strategy=str(run_config.get("strategy", "module_replay") or "module_replay"),
                accuracy=None,
                summary="",
                create_time=self._now(),
                finish_time=None,
            )
            session.add(replay_run)
            for chunk in target_chunks:
                section_result = self._review_single_chunk(
                    project=project,
                    project_id=project_id,
                    run_id=replay_run_id,
                    source_doc_id=source_doc_id,
                    chunk=chunk,
                    previous_section_meta=previous_section_meta,
                    run_config=run_config,
                    progress_callback=progress_callback,
                )
                if not section_result.get("success"):
                    skipped_empty += 1
                    continue
                conclusion_records.append(section_result["conclusion_row"])
                self._persist_section_review_result(session, replay_run_id, section_result)
                session.commit()
                section_results.append(section_result)
                previous_section_meta = section_result.get("previous_section_meta", previous_section_meta)
                self._emit_progress(
                    progress_callback,
                    "section_done",
                    "模块内章节审评完成。",
                    run_id=replay_run_id,
                    section_id=str(section_result.get("section_id", "") or ""),
                    section_name=str(section_result.get("section_meta", {}).get("section_name", "") or ""),
                    module_section_id=target_root,
                    conclusion=str((section_result.get("review_result", {}) or {}).get("pre_review_conclusion", "") or ""),
                )

            if not conclusion_records:
                raise ValueError("no non-empty sections after module replay")

            replay_run.summary = json.dumps(
                {
                    "mode": "module_replay",
                    "run_config_summary": {
                        "domain": str(run_config.get("domain", "") or ""),
                        "branch": str(run_config.get("branch", "") or ""),
                        "strategy": str(run_config.get("strategy", "module_replay") or "module_replay"),
                        "workflow_mode": "module_replay",
                        "feedback_loop_mode": str(run_config.get("feedback_loop_mode", "") or "feedback_optimize"),
                        "enable_feedback_optimize": bool(run_config.get("enable_feedback_optimize", True)),
                        "prompt_config": dict(run_config.get("prompt_config", {}) or {}) if isinstance(run_config.get("prompt_config", {}), dict) else {},
                        "module_section_id": target_root,
                        "module_leaf_section_ids": target_leaf_ids,
                    },
                    "section_conclusion_count": len(conclusion_records),
                    "skipped_empty_count": skipped_empty,
                    "metrics": {},
                    "metrics_schema": "run_metrics_v2",
                },
                ensure_ascii=False,
            )
            self._refresh_run_metrics_summary(session, replay_run_id)
            replay_run.finish_time = self._now()
            session.flush()
            session.commit()
            result = {
                "run_id": replay_run_id,
                "project_id": project_id,
                "source_doc_id": source_doc_id,
                "module_section_id": target_root,
                "module_leaf_section_ids": target_leaf_ids,
                "strategy": str(run_config.get("strategy", "module_replay") or "module_replay"),
                "section_output_count": len(section_results),
                "section_output_section_ids": [str(item.get("section_id", "") or "") for item in section_results],
                "skipped_empty_count": skipped_empty,
            }
            self._emit_progress(
                progress_callback,
                "module_done",
                "模块审评完成。",
                project_id=project_id,
                source_doc_id=source_doc_id,
                run_id=replay_run_id,
                module_section_id=target_root,
                section_output_count=len(section_results),
                skipped_empty_count=skipped_empty,
            )
            return True, "module replay completed", result
        except Exception as exc:
            session.rollback()
            return False, f"module replay failed: {str(exc)}", None
        finally:
            session.close()

    def _persist_section_review_result(
        self,
        session,
        run_id: str,
        section_result: Dict[str, Any],
    ) -> None:
        session.add(section_result["conclusion_row"])
        session.add(
            PreReviewSectionOutput(
                run_id=run_id,
                section_id=str(section_result.get("section_id", "") or ""),
                section_name=str(section_result.get("section_meta", {}).get("section_name", "") or ""),
                schema_version="chapter_review_v2",
                output_json=json.dumps(section_result.get("review_result", {}), ensure_ascii=False),
                create_time=self._now(),
            )
        )
        session.add(section_result["trace_row"])
        for audit_row in section_result.get("audit_rows", []) if isinstance(section_result.get("audit_rows", []), list) else []:
            session.add(audit_row)
        session.flush()

    def _seed_historical_feedback_memory(self, project_id: str) -> None:
        self.memory_governance.seed_project_feedback_memory(project_id)

    def get_run_task_metadata(
        self,
        run_id: str,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """只返回创建后台任务所需的标识字段。"""
        run_key = str(run_id or "").strip()
        if not run_key:
            return False, "run_id is required", None
        session = self.db_conn.get_session()
        try:
            row = (
                session.query(
                    PreReviewRun.run_id,
                    PreReviewRun.project_id,
                    PreReviewRun.source_doc_id,
                )
                .join(
                    PreReviewProject,
                    PreReviewProject.project_id == PreReviewRun.project_id,
                )
                .filter(
                    PreReviewRun.run_id == run_key,
                    PreReviewProject.is_deleted == 0,
                )
                .first()
            )
            if row is None:
                return False, "run not found", None
            return True, "success", {
                "run_id": str(row[0] or run_key),
                "project_id": str(row[1] or ""),
                "source_doc_id": str(row[2] or ""),
            }
        finally:
            session.close()

    def get_run_history(self, project_id: str) -> List[Dict[str, Any]]:
        session = self.db_conn.get_session()
        try:
            rows = (
                session.query(PreReviewRun)
                .filter(PreReviewRun.project_id == project_id)
                .order_by(desc(PreReviewRun.id))
                .all()
            )
            run_ids = [str(r.run_id or "").strip() for r in rows if str(r.run_id or "").strip()]
            source_doc_ids = [
                str(r.source_doc_id or "").strip()
                for r in rows
                if str(r.source_doc_id or "").strip()
            ]
            conclusions_by_run: Dict[str, List[Any]] = {run_id: [] for run_id in run_ids}
            feedback_by_run_section: Dict[str, Dict[str, int]] = {run_id: {} for run_id in run_ids}
            feedback_count_by_run: Dict[str, int] = {run_id: 0 for run_id in run_ids}
            source_file_name_by_doc: Dict[str, str] = {}
            if run_ids:
                conclusion_rows = (
                    session.query(PreReviewSectionConclusion)
                    .filter(PreReviewSectionConclusion.run_id.in_(run_ids))
                    .order_by(PreReviewSectionConclusion.id.asc())
                    .all()
                )
                for item in conclusion_rows:
                    conclusions_by_run.setdefault(str(item.run_id or "").strip(), []).append(item)
                feedback_rows = (
                    session.query(
                        PreReviewFeedback.run_id,
                        PreReviewFeedback.section_id,
                        func.count(PreReviewFeedback.id),
                    )
                    .filter(PreReviewFeedback.run_id.in_(run_ids))
                    .group_by(PreReviewFeedback.run_id, PreReviewFeedback.section_id)
                    .all()
                )
                for feedback_run_id, sid, count in feedback_rows:
                    run_key = str(feedback_run_id or "").strip()
                    section_key = str(sid or "").strip()
                    count_value = int(count or 0)
                    feedback_count_by_run[run_key] = feedback_count_by_run.get(run_key, 0) + count_value
                    if section_key:
                        feedback_by_run_section.setdefault(run_key, {})[section_key] = count_value
            if source_doc_ids:
                source_rows = (
                    session.query(PreReviewSubmissionFile.doc_id, PreReviewSubmissionFile.file_name)
                    .filter(
                        PreReviewSubmissionFile.project_id == project_id,
                        PreReviewSubmissionFile.doc_id.in_(source_doc_ids),
                    )
                    .all()
                )
                source_file_name_by_doc = {
                    str(doc_id or "").strip(): str(file_name or "")
                    for doc_id, file_name in source_rows
                    if str(doc_id or "").strip()
                }
            out = []
            for r in rows:
                try:
                    summary_payload = json.loads(r.summary) if r.summary else {}
                except Exception:
                    summary_payload = {}
                conclusion_rows = conclusions_by_run.get(str(r.run_id or "").strip(), [])
                reviewed_section_ids = []
                section_result_digest: Dict[str, Dict[str, Any]] = {}
                for item in conclusion_rows:
                    sid = str(getattr(item, "section_id", "") or "").strip()
                    if not sid:
                        continue
                    reviewed_section_ids.append(sid)
                    section_result_digest[sid] = {
                        "section_id": sid,
                        "section_name": str(getattr(item, "section_name", "") or sid).strip() or sid,
                        "conclusion": str(getattr(item, "conclusion", "") or "").strip(),
                        "risk_level": str(getattr(item, "risk_level", "") or "").strip(),
                        "has_feedback": False,
                        "feedback_count": 0,
                    }
                section_feedback_map = feedback_by_run_section.get(str(r.run_id or "").strip(), {})
                for section_id, count in section_feedback_map.items():
                    if not section_id:
                        continue
                    digest = section_result_digest.setdefault(
                        section_id,
                        {
                            "section_id": section_id,
                            "section_name": section_id,
                            "conclusion": "",
                            "risk_level": "",
                            "has_feedback": False,
                            "feedback_count": 0,
                        },
                    )
                    digest["has_feedback"] = bool(count)
                    digest["feedback_count"] = int(count or 0)
                feedback_count = feedback_count_by_run.get(str(r.run_id or "").strip(), 0)
                out.append(
                    {
                        "run_id": r.run_id,
                        "project_id": r.project_id,
                        "version_no": r.version_no,
                        "source_doc_id": r.source_doc_id,
                        "source_file_name": source_file_name_by_doc.get(str(r.source_doc_id or "").strip(), ""),
                        "strategy": r.strategy,
                        "accuracy": r.accuracy,
                        "metrics": (summary_payload.get("metrics", {}) if isinstance(summary_payload.get("metrics", {}), dict) else {}),
                        "feedback_loop_mode": str(
                            (
                                summary_payload.get("run_config_summary", {})
                                if isinstance(summary_payload.get("run_config_summary", {}), dict)
                                else {}
                            ).get("feedback_loop_mode", "")
                            or "feedback_optimize"
                        ),
                        "summary": r.summary,
                        "summary_payload": summary_payload if isinstance(summary_payload, dict) else {},
                        "feedback_count": feedback_count,
                        "reviewed_section_ids": reviewed_section_ids,
                        "reviewed_section_count": len(reviewed_section_ids),
                        "section_result_digest": section_result_digest,
                        "create_time": r.create_time.strftime("%Y-%m-%d %H:%M:%S"),
                        "finish_time": r.finish_time.strftime("%Y-%m-%d %H:%M:%S") if r.finish_time else None,
                    }
                )
            return out
        finally:
            session.close()

    def get_section_conclusions(self, run_id: str, section_id: str = "") -> List[Dict[str, Any]]:
        session = self.db_conn.get_session()
        try:
            def _safe_json_load(raw: Optional[str]) -> List[Any]:
                if not raw:
                    return []
                try:
                    data = json.loads(raw)
                    return data if isinstance(data, list) else []
                except Exception:
                    return []

            run_row = session.query(PreReviewRun).filter(PreReviewRun.run_id == run_id).first()
            query = session.query(PreReviewSectionConclusion).filter(PreReviewSectionConclusion.run_id == run_id)
            if section_id:
                query = query.filter(
                    and_(
                        PreReviewSectionConclusion.run_id == run_id,
                        (
                            (PreReviewSectionConclusion.section_id == section_id)
                            | (PreReviewSectionConclusion.section_name.like(f"{section_id}%"))
                        ),
                    )
                )
            rows = query.order_by(PreReviewSectionConclusion.id.asc()).all()
            output_rows = (
                session.query(PreReviewSectionOutput)
                .filter(PreReviewSectionOutput.run_id == run_id)
                .all()
            )
            output_by_section: Dict[str, Dict[str, Any]] = {}
            for row in output_rows:
                try:
                    output_by_section[str(row.section_id)] = json.loads(row.output_json or "{}")
                except Exception:
                    output_by_section[str(row.section_id)] = {}
            out = [
                {
                    **SectionConclusionRecord.from_entity(r).to_dict(),
                    "standard_output": output_by_section.get(str(r.section_id), {}),
                }
                for r in rows
            ]
            if (not section_id) and run_row is not None:
                # 结论查询只需要章节顺序，不应因为缓存缺失而触发
                # Word/PDF/OCR 重解析，也不应构造含多份正文副本的完整 DTO。
                ok, _, payload = self.get_submission_section_overview(
                    project_id=run_row.project_id,
                    doc_id=run_row.source_doc_id,
                )
                if ok and isinstance(payload, dict):
                    ordered_sections = payload.get("sections", [])
                    if isinstance(ordered_sections, list):
                        order_map: Dict[str, int] = {}
                        for i, item in enumerate(ordered_sections, start=1):
                            if not isinstance(item, dict):
                                continue
                            sid = str(item.get("section_id") or item.get("section_code") or "").strip()
                            if sid and sid not in order_map:
                                order_map[sid] = i
                        if order_map:
                            out = sorted(
                                out,
                                key=lambda x: (
                                    int(order_map.get(str(x.get("section_id", "")), 10**9)),
                                    str(x.get("section_id", "")),
                                ),
                            )
            return out
        finally:
            session.close()

    def get_standardized_section_outputs(self, run_id: str, section_id: str = "") -> List[Dict[str, Any]]:
        session = self.db_conn.get_session()
        try:
            query = session.query(PreReviewSectionOutput).filter(PreReviewSectionOutput.run_id == run_id)
            if section_id:
                query = query.filter(PreReviewSectionOutput.section_id == section_id)
            rows = query.order_by(PreReviewSectionOutput.id.asc()).all()
            out: List[Dict[str, Any]] = []
            for row in rows:
                try:
                    payload = json.loads(row.output_json or "{}")
                except Exception:
                    payload = {}
                out.append(
                    {
                        "run_id": row.run_id,
                        "section_id": row.section_id,
                        "section_name": row.section_name,
                        "schema_version": row.schema_version,
                        "output": payload if isinstance(payload, dict) else {},
                        "create_time": row.create_time.strftime("%Y-%m-%d %H:%M:%S") if row.create_time else "",
                    }
                )
            return out
        finally:
            session.close()

    def _normalize_section_review_output(
        self,
        output_payload: Dict[str, Any],
        *,
        section_id: str = "",
        section_name: str = "",
        fallback_conclusion: str = "",
        fallback_risk_level: str = "",
        fallback_linked_rules: Optional[List[Any]] = None,
        fallback_focus_points: Optional[List[Any]] = None,
        fallback_section_rules: Optional[List[Any]] = None,
    ) -> Dict[str, Any]:
        return self.projection_service.normalize_section_review_output(
            output_payload,
            section_id=section_id,
            section_name=section_name,
            fallback_conclusion=fallback_conclusion,
            fallback_risk_level=fallback_risk_level,
            fallback_linked_rules=fallback_linked_rules,
            fallback_focus_points=fallback_focus_points,
            fallback_section_rules=fallback_section_rules,
        )

    def get_run_section_overview(self, run_id: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        session = self.db_conn.get_session()
        try:
            run = session.query(PreReviewRun).filter(PreReviewRun.run_id == run_id).first()
            if run is None:
                return False, "run not found", None
            if not session.query(PreReviewProject).filter(
                PreReviewProject.project_id == run.project_id,
                PreReviewProject.is_deleted == 0,
            ).first():
                return False, "project not found", None
            try:
                run_summary = json.loads(run.summary) if run.summary else {}
            except Exception:
                run_summary = {}

            # 概览只消费章节元数据和短摘要。读取端点必须走紧凑、
            # 无重解析路径，避免大 Word 在查看历史结果时再次占用 OCR。
            ok, msg, section_payload = self.get_submission_section_overview(
                project_id=run.project_id,
                doc_id=run.source_doc_id,
            )
            if not ok or not bool((section_payload or {}).get("content_ready", False)):
                # 历史审评结果已经持久化，不应因原始 Word/PDF 被移走、
                # 旧解析缓存缺失就变成不可读。只用数据库中的项目目录构造
                # 无正文概览，明确告警，严禁回退到 OCR/重解析。
                if isinstance(section_payload, dict) and isinstance(section_payload.get("sections", []), list):
                    catalog_sections = section_payload.get("sections", [])
                else:
                    catalog_payload = self.get_ctd_section_catalog(project_id=run.project_id)
                    catalog_sections = (
                        catalog_payload.get("flat_sections", [])
                        if isinstance(catalog_payload, dict)
                        and isinstance(catalog_payload.get("flat_sections", []), list)
                        else []
                    )
                section_payload = {
                    "sections": [
                        {
                            "section_id": str(item.get("section_id", "") or "").strip(),
                            "section_code": str(item.get("section_code", "") or item.get("section_id", "") or "").strip(),
                            "section_name": str(item.get("section_name", "") or item.get("title", "") or "").strip(),
                            "title_path": list(item.get("title_path") or [])
                            if isinstance(item.get("title_path", []), list)
                            else [],
                            "parent_section_id": str(item.get("parent_section_id", "") or "").strip(),
                            "content_preview": "",
                            "char_count": 0,
                        }
                        for item in catalog_sections
                        if isinstance(item, dict) and str(item.get("section_id", "") or "").strip()
                    ],
                    "statistics": {"section_total": len(catalog_sections), "review_unit_total": 0},
                    "chapter_structure": [],
                    "leaf_sibling_groups": [],
                    "review_units": [],
                    "response_mode": "history_fallback",
                }
                source_content_available = False
                overview_warnings = [
                    {
                        "code": "source_content_unavailable",
                        "message": "原始资料内容当前不可用，已使用持久化审评结果和项目目录恢复历史概览。",
                        "detail": str(
                            msg
                            if not ok
                            else "submission content not ready; persisted metadata fallback used"
                        ).strip(),
                    }
                ]
            else:
                source_content_available = True
                overview_warnings = []

            conclusions = self.get_section_conclusions(run_id=run_id, section_id="")
            section_outputs = self.get_standardized_section_outputs(run_id=run_id, section_id="")
            conclusion_by_section = {str(x.get("section_id")): x for x in conclusions}
            if not source_content_available:
                fallback_sections = (
                    section_payload.get("sections", [])
                    if isinstance(section_payload.get("sections", []), list)
                    else []
                )
                fallback_section_ids = {
                    str(item.get("section_id", "") or "").strip()
                    for item in fallback_sections
                    if isinstance(item, dict) and str(item.get("section_id", "") or "").strip()
                }
                persisted_section_rows = [
                    *[item for item in conclusions if isinstance(item, dict)],
                    *[item for item in section_outputs if isinstance(item, dict)],
                ]
                for persisted_item in persisted_section_rows:
                    persisted_section_id = str(persisted_item.get("section_id", "") or "").strip()
                    if not persisted_section_id or persisted_section_id in fallback_section_ids:
                        continue
                    fallback_section_ids.add(persisted_section_id)
                    fallback_sections.append(
                        {
                            "section_id": persisted_section_id,
                            "section_code": persisted_section_id,
                            "section_name": str(
                                persisted_item.get("section_name", "") or persisted_section_id
                            ).strip()
                            or persisted_section_id,
                            "title_path": [],
                            "parent_section_id": "",
                            "content_preview": "",
                            "char_count": 0,
                        }
                    )
                section_payload["sections"] = fallback_sections
                section_payload.setdefault("statistics", {})["section_total"] = len(fallback_sections)
            focus_points_by_section = {
                str(item.get("section_id", "") or "").strip(): self._normalize_text_list(item.get("concern_points", []))
                for item in (section_payload.get("sections", []) if isinstance(section_payload.get("sections", []), list) else [])
                if isinstance(item, dict) and str(item.get("section_id", "") or "").strip()
            }
            section_rules_by_section = {
                str(item.get("section_id", "") or "").strip(): self._normalize_text_list(item.get("section_rules", []))
                for item in (section_payload.get("sections", []) if isinstance(section_payload.get("sections", []), list) else [])
                if isinstance(item, dict) and str(item.get("section_id", "") or "").strip()
            }
            section_output_by_section = {}
            for x in conclusions:
                sid = str(x.get("section_id", "") or "").strip()
                if not sid:
                    continue
                section_output_by_section[sid] = self._normalize_section_review_output(
                    x.get("standard_output", {}) if isinstance(x.get("standard_output", {}), dict) else {},
                    section_id=sid,
                    section_name=str(x.get("section_name", "") or "").strip(),
                    fallback_conclusion=str(x.get("conclusion", "") or "").strip(),
                    fallback_risk_level=str(x.get("risk_level", "") or "").strip(),
                    fallback_linked_rules=x.get("linked_rules", []) if isinstance(x.get("linked_rules", []), list) else [],
                    fallback_focus_points=focus_points_by_section.get(sid, []),
                    fallback_section_rules=section_rules_by_section.get(sid, []),
                )
            standardized_output_by_section = {
                str(x.get("section_id")): self._normalize_section_review_output(
                    x.get("output", {}) if isinstance(x.get("output", {}), dict) else {},
                    section_id=str(x.get("section_id", "") or "").strip(),
                    section_name=str(x.get("section_name", "") or "").strip(),
                    fallback_conclusion=str((conclusion_by_section.get(str(x.get("section_id")) or "", {}) or {}).get("conclusion", "") or "").strip(),
                    fallback_risk_level=str((conclusion_by_section.get(str(x.get("section_id")) or "", {}) or {}).get("risk_level", "") or "").strip(),
                    fallback_linked_rules=((conclusion_by_section.get(str(x.get("section_id")) or "", {}) or {}).get("linked_rules", []) if isinstance((conclusion_by_section.get(str(x.get("section_id")) or "", {}) or {}).get("linked_rules", []), list) else []),
                    fallback_focus_points=focus_points_by_section.get(str(x.get("section_id", "") or "").strip(), []),
                    fallback_section_rules=section_rules_by_section.get(str(x.get("section_id", "") or "").strip(), []),
                )
                for x in section_outputs
            }
            reviewed_section_ids = sorted([sid for sid in conclusion_by_section.keys() if sid])
            all_section_ids = [
                str(item.get("section_id", "") or "").strip()
                for item in (section_payload.get("sections", []) if isinstance(section_payload.get("sections", []), list) else [])
                if isinstance(item, dict) and str(item.get("section_id", "") or "").strip()
            ]
            pending_section_ids = [sid for sid in all_section_ids if sid not in conclusion_by_section]
            chapter_conclusion_summary = []
            for item in (section_payload.get("sections", []) if isinstance(section_payload.get("sections", []), list) else []):
                if not isinstance(item, dict):
                    continue
                sid = str(item.get("section_id", "") or "").strip()
                if not sid:
                    continue
                conclusion_item = conclusion_by_section.get(sid, {}) if isinstance(conclusion_by_section.get(sid, {}), dict) else {}
                chapter_conclusion_summary.append(
                    {
                        "section_id": sid,
                        "section_name": str(item.get("section_name", "") or item.get("title", "") or sid).strip() or sid,
                        "status": "reviewed" if sid in conclusion_by_section else "pending",
                        "risk_level": str(conclusion_item.get("risk_level", "") or "").strip(),
                        "conclusion": str(conclusion_item.get("conclusion", "") or "").strip(),
                    }
                )
            # 这里只需要 trace 摘要。完整 trace 可包含全部检索原文和
            # 外部 artifact，再读一遍会与前端详情请求叠加，形成同类 504。
            traces = self.get_section_traces(run_id=run_id, section_id="", compact=True)
            trace_digest_by_section = {}
            for t in traces:
                sid = str(t.get("section_id", ""))
                if not sid:
                    continue
                coordination = t.get("coordination", {}) if isinstance(t.get("coordination"), dict) else {}
                retrieval = coordination.get("retrieval", {}) if isinstance(coordination.get("retrieval"), dict) else {}
                agent_meta = t.get("agent", {}) if isinstance(t.get("agent"), dict) else {}
                memory_meta = t.get("memory", {}) if isinstance(t.get("memory"), dict) else {}
                trace_digest_by_section[sid] = {
                    "trace_schema": t.get("trace_schema", "legacy"),
                    "retrieval_hit_count": retrieval.get("hit_count", 0),
                    "grouped_doc_count": retrieval.get("grouped_doc_count", 0),
                    "memory_hit_count": memory_meta.get("hit_count", 0),
                    "findings_count": agent_meta.get("findings_count", 0),
                    "score": agent_meta.get("score", 0.0),
                    "source_breakdown": t.get("retrieval_detail", {}).get("source_breakdown", {}) if isinstance(t.get("retrieval_detail", {}), dict) else {},
                    "retrieval_metrics": t.get("retrieval_detail", {}).get("metrics", {}) if isinstance(t.get("retrieval_detail", {}), dict) else {},
                    "retrieval_error_breakdown": t.get("retrieval_detail", {}).get("error_breakdown", {}) if isinstance(t.get("retrieval_detail", {}), dict) else {},
                }

            return True, "success", {
                "run_id": run_id,
                "project_id": run.project_id,
                "source_doc_id": run.source_doc_id,
                "strategy": str(run.strategy or ""),
                "workflow_mode": str((run_summary if isinstance(run_summary, dict) else {}).get("mode", "") or self._resolve_pre_review_mode({"strategy": str(run.strategy or "")})),
                "run_config": {
                    "strategy": str(
                        (
                            (run_summary.get("run_config_summary", {}) if isinstance(run_summary.get("run_config_summary", {}), dict) else {})
                            .get("strategy", "")
                        )
                        or run.strategy
                        or ""
                    ),
                    "workflow_mode": str(
                        (
                            (run_summary.get("run_config_summary", {}) if isinstance(run_summary.get("run_config_summary", {}), dict) else {})
                            .get("workflow_mode", "")
                        )
                        or (run_summary if isinstance(run_summary, dict) else {}).get("mode", "")
                        or self._resolve_pre_review_mode({"strategy": str(run.strategy or "")})
                    ),
                    "domain": str(
                        (
                            (run_summary.get("run_config_summary", {}) if isinstance(run_summary.get("run_config_summary", {}), dict) else {})
                            .get("domain", "")
                        )
                        or ""
                    ),
                    "branch": str(
                        (
                            (run_summary.get("run_config_summary", {}) if isinstance(run_summary.get("run_config_summary", {}), dict) else {})
                            .get("branch", "")
                        )
                        or ""
                    ),
                    "feedback_loop_mode": str(
                        (
                            (run_summary.get("run_config_summary", {}) if isinstance(run_summary.get("run_config_summary", {}), dict) else {})
                            .get("feedback_loop_mode", "")
                        )
                        or "feedback_optimize"
                    ),
                    "enable_feedback_optimize": bool(
                        (
                            (run_summary.get("run_config_summary", {}) if isinstance(run_summary.get("run_config_summary", {}), dict) else {})
                            .get("enable_feedback_optimize", True)
                        )
                    ),
                    "prompt_config": dict(
                        (
                            (run_summary.get("run_config_summary", {}) if isinstance(run_summary.get("run_config_summary", {}), dict) else {})
                            .get("prompt_config", {})
                        )
                        or {}
                    ),
                },
                "chapter_structure": section_payload.get("chapter_structure", []),
                "sections": section_payload.get("sections", []),
                "leaf_sibling_groups": section_payload.get("leaf_sibling_groups", []),
                "review_units": section_payload.get("review_units", []),
                "conclusion_by_section_id": conclusion_by_section,
                "section_output_by_section_id": section_output_by_section,
                "standardized_output_by_section_id": standardized_output_by_section,
                "section_outputs": section_outputs,
                "schema_version": "chapter_review_v2",
                "trace_digest_by_section_id": trace_digest_by_section,
                "conclusions": conclusions,
                "reviewed_section_ids": reviewed_section_ids,
                "pending_section_ids": pending_section_ids,
                "chapter_conclusion_summary": chapter_conclusion_summary,
                "full_document_summary": {
                    "reviewed_section_total": len(reviewed_section_ids),
                    "pending_section_total": len(pending_section_ids),
                    "section_total": len(all_section_ids),
                    "reviewed_sections": reviewed_section_ids,
                    "pending_sections": pending_section_ids,
                    "chapter_conclusion_summary": chapter_conclusion_summary,
                },
                "run_summary": run_summary if isinstance(run_summary, dict) else {},
                "source_content_available": source_content_available,
                "overview_warnings": overview_warnings,
            }
        finally:
            session.close()

    def get_section_traces(
        self,
        run_id: str,
        section_id: str = "",
        compact: bool = False,
    ) -> List[Dict[str, Any]]:
        session = self.db_conn.get_session()
        try:
            query = session.query(PreReviewSectionTrace).filter(PreReviewSectionTrace.run_id == run_id)
            if section_id:
                query = query.filter(PreReviewSectionTrace.section_id == section_id)
            rows = query.order_by(PreReviewSectionTrace.id.asc()).all()

            out: List[Dict[str, Any]] = []
            for r in rows:
                raw_trace = PreReviewRepository.load_trace_payload(r.trace_json)
                if isinstance(raw_trace, dict) and str(raw_trace.get("trace_schema", "") or "").strip() == "chapter_review_v1":
                    trace_schema = "chapter_review_v1"
                    coordination = raw_trace.get("coordination", {}) if isinstance(raw_trace.get("coordination"), dict) else {}
                    memory = raw_trace.get("memory", {}) if isinstance(raw_trace.get("memory"), dict) else {}
                    trace = raw_trace.get("trace", {}) if isinstance(raw_trace.get("trace"), dict) else {}
                    agent = raw_trace.get("agent", {}) if isinstance(raw_trace.get("agent"), dict) else {}
                elif isinstance(raw_trace, dict) and "trace" in raw_trace:
                    trace_schema = "coordination_v1"
                    coordination = raw_trace.get("coordination", {})
                    memory = raw_trace.get("memory", {})
                    trace = raw_trace.get("trace", {})
                    agent = raw_trace.get("agent", {})
                else:
                    trace_schema = "legacy"
                    coordination = {}
                    memory = {}
                    trace = raw_trace if isinstance(raw_trace, dict) else {}
                    agent = {}
                if compact:
                    # 概览/列表路径禁止读取可能很大的 artifact 文件，也禁止
                    # 对每条检索材料再做数据库富化。保留构建摘要所需的小字段。
                    retrieval_detail = (
                        raw_trace.get("retrieval_detail", {})
                        if isinstance(raw_trace, dict)
                        and isinstance(raw_trace.get("retrieval_detail", {}), dict)
                        else {}
                    )
                    raw_retrieved_materials = (
                        raw_trace.get("raw_retrieved_materials", [])
                        if isinstance(raw_trace, dict)
                        and isinstance(raw_trace.get("raw_retrieved_materials", []), list)
                        else []
                    )
                    retrieved_materials = (
                        raw_trace.get("retrieved_materials", [])
                        if isinstance(raw_trace, dict)
                        and isinstance(raw_trace.get("retrieved_materials", []), list)
                        else []
                    )
                    retrieval_meta = (
                        coordination.get("retrieval", {})
                        if isinstance(coordination, dict)
                        and isinstance(coordination.get("retrieval", {}), dict)
                        else {}
                    )

                    def _compact_scalar_map(value: Any, max_items: int = 40) -> Dict[str, Any]:
                        if not isinstance(value, dict):
                            return {}
                        compact_map: Dict[str, Any] = {}
                        for key, item_value in list(value.items())[:max_items]:
                            normalized_key = str(key or "").strip()
                            if not normalized_key:
                                continue
                            if isinstance(item_value, (bool, int, float)) or item_value is None:
                                compact_map[normalized_key] = item_value
                            elif isinstance(item_value, str):
                                compact_map[normalized_key] = item_value[:200]
                        return compact_map

                    def _safe_int(value: Any) -> int:
                        try:
                            return int(value or 0)
                        except (TypeError, ValueError):
                            return 0

                    def _safe_float(value: Any) -> float:
                        try:
                            return float(value or 0.0)
                        except (TypeError, ValueError):
                            return 0.0

                    out.append(
                        {
                            "run_id": r.run_id,
                            "section_id": r.section_id,
                            "trace_schema": trace_schema,
                            "coordination": {
                                "retrieval": {
                                    "hit_count": _safe_int(retrieval_meta.get("hit_count", 0)),
                                    "grouped_doc_count": _safe_int(retrieval_meta.get("grouped_doc_count", 0)),
                                }
                            },
                            "memory": {
                                "hit_count": _safe_int(memory.get("hit_count", 0))
                                if isinstance(memory, dict)
                                else 0,
                            },
                            "agent": {
                                "findings_count": _safe_int(agent.get("findings_count", 0))
                                if isinstance(agent, dict)
                                else 0,
                                "score": _safe_float(agent.get("score", 0.0))
                                if isinstance(agent, dict)
                                else 0.0,
                            },
                            "retrieval_detail": {
                                "source_breakdown": _compact_scalar_map(
                                    retrieval_detail.get("source_breakdown", {})
                                    if isinstance(retrieval_detail.get("source_breakdown", {}), dict)
                                    else self._summarize_source_breakdown(retrieved_materials)
                                ),
                                "metrics": _compact_scalar_map(retrieval_detail.get("metrics", {})),
                                "error_breakdown": _compact_scalar_map(
                                    retrieval_detail.get("error_breakdown", {})
                                ),
                            },
                            "retrieval_evaluation": {
                                "candidate_count": len(raw_retrieved_materials or retrieved_materials),
                            },
                            "create_time": r.create_time.strftime("%Y-%m-%d %H:%M:%S"),
                            "response_mode": "compact",
                        }
                    )
                    continue
                retrieved_materials = raw_trace.get("retrieved_materials", []) if isinstance(raw_trace.get("retrieved_materials", []), list) else []
                raw_retrieved_materials = raw_trace.get("raw_retrieved_materials", []) if isinstance(raw_trace.get("raw_retrieved_materials", []), list) else []
                retrieval_evaluation = raw_trace.get("retrieval_evaluation", {}) if isinstance(raw_trace.get("retrieval_evaluation", {}), dict) else {}
                trace_artifact = raw_trace.get("trace_artifact", {}) if isinstance(raw_trace.get("trace_artifact", {}), dict) else {}
                artifact_path = str(trace_artifact.get("file_path", "") or "").strip()
                if artifact_path and os.path.exists(artifact_path):
                    try:
                        artifact_payload = json.loads(Path(artifact_path).read_text(encoding="utf-8"))
                    except Exception:
                        artifact_payload = {}
                    raw_retrieved = artifact_payload.get("retrieved_materials", []) if isinstance(artifact_payload.get("retrieved_materials", []), list) else []
                    raw_materials_from_artifact = artifact_payload.get("raw_retrieved_materials", []) if isinstance(artifact_payload.get("raw_retrieved_materials", []), list) else []
                    retrieval_evaluation = artifact_payload.get("retrieval_evaluation", retrieval_evaluation) if isinstance(artifact_payload.get("retrieval_evaluation", retrieval_evaluation), dict) else retrieval_evaluation
                    if raw_retrieved:
                        retrieved_materials = raw_retrieved
                    if raw_materials_from_artifact:
                        raw_retrieved_materials = raw_materials_from_artifact
                retrieved_materials = self._enrich_retrieved_materials(session, retrieved_materials)
                raw_retrieved_materials = self._enrich_retrieved_materials(session, raw_retrieved_materials)
                candidate_materials = raw_retrieved_materials or retrieved_materials
                retrieval_evaluation = self._normalize_retrieval_evaluation_result(
                    retrieval_evaluation,
                    candidate_materials,
                )
                _, rejected_materials = self._apply_retrieval_evaluation_result(
                    candidate_materials,
                    retrieval_evaluation,
                )
                rejected_materials = self._enrich_retrieved_materials(session, rejected_materials)
                retrieval_evaluation["rejected_count"] = len(rejected_materials)
                retrieval_evaluation.pop("rejected_materials", None)
                retrieval_detail = self._compute_retrieval_feedback_detail(session, run_id=run_id, section_id=r.section_id)
                retrieval_detail["source_breakdown"] = self._summarize_source_breakdown(retrieved_materials)
                out.append(
                    {
                        "run_id": r.run_id,
                        "section_id": r.section_id,
                        "trace_schema": trace_schema,
                        "coordination": coordination,
                        "memory": memory,
                        "agent": agent,
                        "planner_result": raw_trace.get("planner_result", {}) if isinstance(raw_trace.get("planner_result", {}), dict) else {},
                        "raw_retrieved_materials": raw_retrieved_materials,
                        "retrieved_materials": retrieved_materials,
                        "retrieval_evaluation": retrieval_evaluation,
                        "retrieval_detail": retrieval_detail,
                        "trace_artifact": trace_artifact,
                        "prompt_rules": raw_trace.get("prompt_rules", {}) if isinstance(raw_trace.get("prompt_rules", {}), dict) else {},
                        "standardized_output": raw_trace.get("standardized_output", {}) if isinstance(raw_trace.get("standardized_output", {}), dict) else {},
                        "section_packet": raw_trace.get("section_packet", {}) if isinstance(raw_trace.get("section_packet", {}), dict) else {},
                        "trace": trace,
                        "create_time": r.create_time.strftime("%Y-%m-%d %H:%M:%S"),
                    }
                )
            return out
        finally:
            session.close()

    def get_execution_audits(self, run_id: str, section_id: str = "") -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        run_key = str(run_id or "").strip()
        section_key = str(section_id or "").strip()
        if not run_key:
            return False, "run_id is required", None
        session = self.db_conn.get_session()
        try:
            query = session.query(PreReviewExecutionAudit).filter(PreReviewExecutionAudit.run_id == run_key)
            if section_key:
                query = query.filter(PreReviewExecutionAudit.section_id == section_key)
            rows = query.order_by(PreReviewExecutionAudit.id.asc()).all()
            items = [self._serialize_execution_audit_row(row) for row in rows]
            breakdowns = self._summarize_execution_audit_items(items)

            compare_run_id = ""
            compare_items: List[Dict[str, Any]] = []
            if section_key:
                current_run = session.query(PreReviewRun).filter(PreReviewRun.run_id == run_key).first()
                if current_run is not None:
                    previous_runs = (
                        session.query(PreReviewRun)
                        .filter(
                            PreReviewRun.project_id == current_run.project_id,
                            PreReviewRun.create_time < current_run.create_time,
                        )
                        .order_by(PreReviewRun.create_time.desc(), PreReviewRun.id.desc())
                        .all()
                    )
                    previous_run_ids = [
                        str(previous_run.run_id or "").strip()
                        for previous_run in previous_runs
                        if str(previous_run.run_id or "").strip()
                    ]
                    previous_audits_by_run: Dict[str, List[Any]] = {
                        previous_run_id: [] for previous_run_id in previous_run_ids
                    }
                    if previous_run_ids:
                        previous_audit_rows = (
                            session.query(PreReviewExecutionAudit)
                            .filter(
                                PreReviewExecutionAudit.run_id.in_(previous_run_ids),
                                PreReviewExecutionAudit.section_id == section_key,
                            )
                            .order_by(PreReviewExecutionAudit.id.asc())
                            .all()
                        )
                        for previous_audit_row in previous_audit_rows:
                            previous_audits_by_run.setdefault(
                                str(previous_audit_row.run_id or "").strip(), []
                            ).append(previous_audit_row)
                    for previous_run in previous_runs:
                        previous_rows = previous_audits_by_run.get(
                            str(previous_run.run_id or "").strip(), []
                        )
                        if not previous_rows:
                            continue
                        compare_run_id = str(previous_run.run_id or "").strip()
                        compare_items = [self._serialize_execution_audit_row(row) for row in previous_rows]
                        break

            comparison = {
                "compare_run_id": compare_run_id,
                "items": compare_items,
                "diff": self._build_execution_audit_diff(items, compare_items) if compare_items else {"stage_diffs": [], "summary": {"stage_total": 0, "changed_stage_count": 0, "changed_field_count": 0}},
            }
            feedback_history = self._build_feedback_history(session, run_id=run_key, section_id=section_key) if section_key else {"summary": {}}
            feedback_summary = feedback_history.get("summary", {}) if isinstance(feedback_history.get("summary", {}), dict) else {}
            return True, "success", {
                "run_id": run_key,
                "section_id": section_key,
                "items": items,
                "stage_breakdown": breakdowns["stage_breakdown"],
                "status_breakdown": breakdowns["status_breakdown"],
                "envelope_breakdown": breakdowns["envelope_breakdown"],
                "protocol_breakdown": breakdowns["protocol_breakdown"],
                "comparison": comparison,
                "feedback_summary": feedback_summary,
                "summary": {
                    "total": len(items),
                    "completed_count": int(breakdowns["status_breakdown"].get("completed", 0)),
                    "failed_count": int(breakdowns["status_breakdown"].get("failed", 0)),
                    "p52_optimize_event_total": int(feedback_summary.get("p52_optimize_event_total", 0) or 0),
                    "p52_meta_reflection_event_total": int(feedback_summary.get("p52_meta_reflection_event_total", 0) or 0),
                    "p52_ablation_event_total": int(feedback_summary.get("p52_ablation_event_total", 0) or 0),
                },
            }
        finally:
            session.close()

    def _resolve_chunk_section_rules(
        self,
        project_id: str,
        section_id: str,
        chunk: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        merged = self._dedupe_text_list(
            self._normalize_text_list((chunk or {}).get("section_rules", []))
            + self._normalize_text_list((chunk or {}).get("rules", []))
        )
        if merged or not project_id or not section_id:
            return merged
        session = self.db_conn.get_session()
        try:
            rule_map = self._load_project_section_rule_map(session, project_id)
            normalized_section_id = self.ctd_sections.normalize_section_id(section_id)
            current_id = normalized_section_id
            while current_id:
                inherited = self._normalize_text_list(rule_map.get(current_id, []))
                if inherited:
                    return inherited
                parent_meta = (
                    self.ctd_sections.get_section(current_id, leaf_only=False)
                    or {}
                )
                parent_id = self.ctd_sections.normalize_section_id(parent_meta.get("parent_section_id"))
                if not parent_id and "." in current_id:
                    parent_id = self.ctd_sections.normalize_section_id(".".join(current_id.split(".")[:-1]))
                if not parent_id or parent_id == current_id:
                    break
                current_id = parent_id
            return []
        finally:
            session.close()

    def get_section_trace_artifact(self, run_id: str, section_id: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        session = self.db_conn.get_session()
        try:
            row = (
                session.query(PreReviewSectionTrace)
                .filter(
                    PreReviewSectionTrace.run_id == str(run_id or "").strip(),
                    PreReviewSectionTrace.section_id == str(section_id or "").strip(),
                )
                .order_by(PreReviewSectionTrace.id.desc())
                .first()
            )
            if row is None:
                return False, "section trace not found", None
            raw_trace = PreReviewRepository.load_trace_payload(row.trace_json)
            artifact = raw_trace.get("trace_artifact", {}) if isinstance(raw_trace.get("trace_artifact", {}), dict) else {}
            file_path = str(artifact.get("file_path", "") or "").strip()
            if not file_path:
                return False, "trace artifact not found", None
            if not os.path.exists(file_path):
                return False, "trace artifact file not exists", None
            try:
                content = Path(file_path).read_text(encoding="utf-8")
            except Exception as exc:
                return False, f"read trace artifact failed: {str(exc)}", None
            return True, "success", {
                "run_id": str(run_id or "").strip(),
                "section_id": str(section_id or "").strip(),
                "trace_artifact": artifact,
                "content": content,
                "content_length": len(content),
            }
        finally:
            session.close()

    def get_section_patch_candidates(self, run_id: str, section_id: str = "") -> Tuple[bool, str, Optional[List[Dict[str, Any]]]]:
        session = self.db_conn.get_session()
        try:
            run = session.query(PreReviewRun).filter(PreReviewRun.run_id == run_id).first()
            if run is None:
                return False, "run not found", None

            related_run_ids = [
                str(item.run_id or "").strip()
                for item in session.query(PreReviewRun.run_id).filter(PreReviewRun.project_id == run.project_id).all()
                if str(item.run_id or "").strip()
            ]
            if not related_run_ids:
                related_run_ids = [str(run_id)]

            query = session.query(PreReviewPatchRegistry).filter(
                PreReviewPatchRegistry.run_id.in_(related_run_ids)
            )
            if str(section_id or "").strip():
                query = query.filter(PreReviewPatchRegistry.section_id == str(section_id).strip())

            rows = query.order_by(PreReviewPatchRegistry.update_time.desc(), PreReviewPatchRegistry.id.desc()).all()
            out: List[Dict[str, Any]] = []
            for row in rows:
                try:
                    payload = json.loads(row.payload_json) if row.payload_json else {}
                except Exception:
                    payload = {}
                out.append(
                    {
                        "patch_id": str(row.patch_id or ""),
                        "run_id": str(row.run_id or ""),
                        "section_id": str(row.section_id or ""),
                        "patch_type": str(row.patch_type or ""),
                        "target_agent": str(row.target_agent or ""),
                        "target_scope": str(row.target_scope or ""),
                        "trigger_condition": str(row.trigger_condition or ""),
                        "patch_content": str(row.patch_content or ""),
                        "source_feedback_key": str(row.source_feedback_key or ""),
                        "status": str(row.status or ""),
                        "version": int(row.version or 1),
                        "payload": payload if isinstance(payload, dict) else {},
                        "create_time": row.create_time.strftime("%Y-%m-%d %H:%M:%S") if row.create_time else "",
                        "update_time": row.update_time.strftime("%Y-%m-%d %H:%M:%S") if row.update_time else "",
                    }
                )
            return True, "success", out
        finally:
            session.close()

    def _query_effective_section_rule_rows(
        self,
        session,
        section_id: str,
        project_id: str = "",
        scope_metadata: Optional[Dict[str, Any]] = None,
    ) -> List[PreReviewSectionRule]:
        section_key = str(section_id or "").strip()
        project_key = str(project_id or "").strip()
        if not section_key:
            return []
        global_project_id = self._ensure_global_rule_project(session)
        self._ensure_project_section_rules(session, global_project_id)
        filters = [
            and_(
                PreReviewSectionRule.project_id == global_project_id,
                PreReviewSectionRule.source_type.in_(["ctd_seed", "manual", "json_import"]),
            )
        ]
        if project_key:
            filters.append(
                and_(
                    PreReviewSectionRule.project_id == project_key,
                    PreReviewSectionRule.source_type.in_(["feedback_experience", "feedback_patch"]),
                )
            )
        rows = (
            session.query(PreReviewSectionRule)
            .filter(
                PreReviewSectionRule.section_id == section_key,
                PreReviewSectionRule.is_active == 1,
                or_(*filters),
            )
            .order_by(PreReviewSectionRule.source_type.asc(), PreReviewSectionRule.id.asc())
            .all()
        )
        if not project_key and not isinstance(scope_metadata, dict):
            return rows
        project_scope = None
        if project_key:
            project = (
                session.query(PreReviewProject)
                .filter(and_(PreReviewProject.project_id == project_key, PreReviewProject.is_deleted == 0))
                .first()
            )
            project_scope = self._build_project_rule_scope_metadata(project, section_id=section_key)
        explicit_scope_input = scope_metadata if isinstance(scope_metadata, dict) else {}
        explicit_scope = self._normalize_rule_scope_metadata(explicit_scope_input, section_id=section_key) if isinstance(scope_metadata, dict) else {}
        has_explicit_scope = any(
            str(explicit_scope_input.get(key, "") or "").strip()
            for key in ["registration_scope", "registration_class", "registration_class_sub", "module", "section_path_prefix"]
        )
        filtered_rows: List[PreReviewSectionRule] = []
        for row in rows:
            source_type = str(getattr(row, "source_type", "") or "").strip()
            try:
                payload = json.loads(getattr(row, "payload_json", "") or "{}")
            except Exception:
                payload = {}
            rule_scope = self._extract_rule_scope_metadata_from_payload(payload, section_id=section_key)
            if project_scope and source_type in {"ctd_seed", "manual", "json_import"}:
                if not self._rule_scope_matches(rule_scope, project_scope, allow_generic=True):
                    continue
            if has_explicit_scope:
                if not self._rule_scope_matches(rule_scope, explicit_scope, allow_generic=True):
                    continue
            filtered_rows.append(row)
        return filtered_rows

    def _find_inherited_rule_section_id(self, session, section_id: str, project_id: str = "") -> str:
        current_id = self.ctd_sections.normalize_section_id(section_id)
        project_key = str(project_id or "").strip()
        while current_id:
            rows = self._query_effective_section_rule_rows(session, current_id, project_key)
            if rows:
                return current_id
            parent_meta = self.ctd_sections.get_section(current_id, leaf_only=False) or {}
            parent_id = self.ctd_sections.normalize_section_id(parent_meta.get("parent_section_id"))
            if not parent_id and "." in current_id:
                parent_id = self.ctd_sections.normalize_section_id(".".join(current_id.split(".")[:-1]))
            if not parent_id or parent_id == current_id:
                break
            current_id = parent_id
        return ""

    def _build_section_rule_response(
        self,
        session,
        section_id: str,
        project_id: str = "",
        scope_metadata: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        section_key = str(section_id or "").strip()
        project_key = str(project_id or "").strip()
        if not section_key:
            return False, "section_id is required", None
        section_meta = {}
        if project_key:
            project_catalog = self._load_project_section_catalog(session, project_key)
            project_section_map = (
                project_catalog.get("section_map", {})
                if isinstance(project_catalog.get("section_map", {}), dict)
                else {}
            )
            normalized_map = {
                self.ctd_sections.normalize_section_id(key): value
                for key, value in project_section_map.items()
                if self.ctd_sections.normalize_section_id(key)
            }
            normalized_section_key = self.ctd_sections.normalize_section_id(section_key)
            section_meta = (
                normalized_map.get(normalized_section_key, {})
                if isinstance(normalized_map.get(normalized_section_key, {}), dict)
                else {}
            )
        if not isinstance(section_meta, dict) or not section_meta:
            section_meta = self.ctd_sections.get_section(section_key, leaf_only=False)
        if not isinstance(section_meta, dict):
            return False, f"section not found: {section_key}", None
        effective_rule_section_id = self._find_inherited_rule_section_id(session, section_key, project_key) or self.ctd_sections.normalize_section_id(section_key)
        rows = self._query_effective_section_rule_rows(session, effective_rule_section_id, project_key, scope_metadata=scope_metadata)
        normalized_scope_metadata = self._normalize_rule_scope_metadata(scope_metadata or {}, section_id=section_key)
        items: List[Dict[str, Any]] = []
        source_breakdown: Dict[str, int] = {}
        for row in rows:
            source_type = str(getattr(row, "source_type", "") or "").strip() or "unknown"
            source_breakdown[source_type] = int(source_breakdown.get(source_type, 0)) + 1
            try:
                payload = json.loads(getattr(row, "payload_json", "") or "{}")
            except Exception:
                payload = {}
            items.append(
                {
                    "rule_id": str(getattr(row, "rule_id", "") or "").strip(),
                    "section_id": str(getattr(row, "section_id", "") or "").strip(),
                    "section_name": str(getattr(row, "section_name", "") or "").strip(),
                    "rule_code": str(getattr(row, "rule_code", "") or "").strip(),
                    "rule_text": str(getattr(row, "rule_text", "") or "").strip(),
                    "source_type": source_type,
                    "source_ref": str(getattr(row, "source_ref", "") or "").strip(),
                    "payload": payload if isinstance(payload, dict) else {},
                    "scope_metadata": self._extract_rule_scope_metadata_from_payload(payload, section_id=section_key),
                    "update_time": row.update_time.strftime("%Y-%m-%d %H:%M:%S") if getattr(row, "update_time", None) else "",
                    "can_delete": source_type in {"manual", "ctd_seed", "json_import"},
                }
            )
        has_explicit_scope = isinstance(scope_metadata, dict) and any(
            str((scope_metadata or {}).get(key, "") or "").strip()
            for key in ["registration_scope", "registration_class", "registration_class_sub", "module", "section_path_prefix"]
        )
        current_scope_key = self._build_rule_scope_key(normalized_scope_metadata)
        editable_items = []
        for item in items:
            if str(item.get("source_type", "") or "").strip() != "manual":
                continue
            item_scope_key = self._build_rule_scope_key(item.get("scope_metadata", {}) if isinstance(item.get("scope_metadata", {}), dict) else {})
            if has_explicit_scope and item_scope_key != current_scope_key:
                continue
            editable_items.append(item)
        import_items = [item for item in items if str(item.get("source_type", "") or "").strip() in {"ctd_seed", "json_import"}]
        return True, "success", {
            "project_id": project_key,
            "section_id": section_key,
            "section_name": str(section_meta.get("section_name", "") or section_key).strip() or section_key,
            "items": items,
            "editable_items": editable_items,
            "import_items": import_items,
            "seed_items": import_items,
            "manual_rule_texts": [
                str(item.get("rule_text", "") or "").strip()
                for item in editable_items
                if str(item.get("rule_text", "") or "").strip()
            ],
            "import_rule_texts": [
                str(item.get("rule_text", "") or "").strip()
                for item in import_items
                if str(item.get("rule_text", "") or "").strip()
            ],
            "seed_rule_texts": [
                str(item.get("rule_text", "") or "").strip()
                for item in import_items
                if str(item.get("rule_text", "") or "").strip()
            ],
            "can_delete_import_rules": bool(import_items),
            "can_delete_seed_rules": bool(import_items),
            "source_breakdown": source_breakdown,
            "scope_metadata": normalized_scope_metadata,
            "effective_rule_section_id": effective_rule_section_id,
            "rules_inherited": effective_rule_section_id != self.ctd_sections.normalize_section_id(section_key),
            "summary": {
                "total": len(items),
                "import_rule_count": int(source_breakdown.get("ctd_seed", 0)) + int(source_breakdown.get("json_import", 0)),
                "seed_rule_count": int(source_breakdown.get("ctd_seed", 0)),
                "manual_rule_count": int(source_breakdown.get("manual", 0)),
                "json_import_count": int(source_breakdown.get("json_import", 0)),
                "feedback_experience_count": int(source_breakdown.get("feedback_experience", 0)),
                "feedback_patch_count": int(source_breakdown.get("feedback_patch", 0)),
            },
        }

    def get_section_rules(
        self,
        section_id: str,
        scope_metadata: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        session = self.db_conn.get_session()
        try:
            return self._build_section_rule_response(
                session,
                section_id=section_id,
                project_id="",
                scope_metadata=scope_metadata,
            )
        finally:
            session.close()

    def save_seed_section_rules(
        self,
        section_id: str,
        rule_texts: Optional[List[Any]] = None,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        section_key = str(section_id or "").strip()
        if not section_key:
            return False, "section_id is required", None
        normalized_rule_texts = self._dedupe_text_list(rule_texts or [])
        session = self.db_conn.get_session()
        try:
            section_meta = self.ctd_sections.get_section(section_key, leaf_only=False)
            if not isinstance(section_meta, dict):
                return False, f"section not found: {section_key}", None
            seed_map = self._load_ctd_section_rule_seed_map()
            if normalized_rule_texts:
                seed_map[section_key] = normalized_rule_texts
            else:
                seed_map.pop(section_key, None)
            self._replace_global_seed_section_rule_map(session, seed_map, source_ref="seed_rule_save")
            session.commit()
            return self.get_section_rules(section_key)
        except Exception as exc:
            session.rollback()
            return False, f"save seed section rules failed: {str(exc)}", None
        finally:
            session.close()

    def delete_seed_section_rules(
        self,
        section_id: str,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        section_key = str(section_id or "").strip()
        if not section_key:
            return False, "section_id is required", None
        session = self.db_conn.get_session()
        try:
            section_meta = self.ctd_sections.get_section(section_key, leaf_only=False)
            if not isinstance(section_meta, dict):
                return False, f"section not found: {section_key}", None
            seed_map = self._load_ctd_section_rule_seed_map()
            if section_key not in seed_map:
                return False, "seed section rules not found", None
            seed_map.pop(section_key, None)
            self._replace_global_seed_section_rule_map(session, seed_map, source_ref="seed_rule_delete")
            session.commit()
            return self.get_section_rules(section_key)
        except Exception as exc:
            session.rollback()
            return False, f"delete seed section rules failed: {str(exc)}", None
        finally:
            session.close()

    def delete_seed_section_rule(
        self,
        section_id: str,
        rule_code: str,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        return self.delete_section_rule(section_id=section_id, rule_code=rule_code)

    def delete_section_rule(
        self,
        section_id: str,
        rule_code: str,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        section_key = str(section_id or "").strip()
        normalized_rule_code = str(rule_code or "").strip()
        if not section_key or not normalized_rule_code:
            return False, "section_id and rule_code are required", None
        session = self.db_conn.get_session()
        try:
            section_meta = self.ctd_sections.get_section(section_key, leaf_only=False)
            if not isinstance(section_meta, dict):
                return False, f"section not found: {section_key}", None
            global_project_id = self._ensure_global_rule_project(session)
            row = (
                session.query(PreReviewSectionRule)
                .filter(
                    PreReviewSectionRule.project_id == global_project_id,
                    PreReviewSectionRule.section_id == section_key,
                    PreReviewSectionRule.rule_code == normalized_rule_code,
                )
                .first()
            )
            if row is None:
                return False, "section rule not found", None
            session.delete(row)
            session.commit()
            self._invalidate_project_section_catalog_cache(project_id=global_project_id)
            return self.get_section_rules(section_key)
        except Exception as exc:
            session.rollback()
            return False, f"delete section rule failed: {str(exc)}", None
        finally:
            session.close()

    def get_project_section_rules(self, project_id: str, section_id: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        project_key = str(project_id or "").strip()
        section_key = str(section_id or "").strip()
        if not project_key or not section_key:
            return False, "project_id and section_id are required", None
        session = self.db_conn.get_session()
        try:
            project = (
                session.query(PreReviewProject)
                .filter(and_(PreReviewProject.project_id == project_key, PreReviewProject.is_deleted == 0))
                .first()
            )
            if project is None:
                return False, "project not found", None
            return self._build_section_rule_response(session, section_id=section_key, project_id=project_key)
        finally:
            session.close()

    def get_project_section_examples(self, project_id: str, section_id: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        project_key = str(project_id or "").strip()
        section_key = str(section_id or "").strip()
        if not project_key or not section_key:
            return False, "project_id and section_id are required", None
        session = self.db_conn.get_session()
        try:
            rows = (
                session.query(PreReviewSectionExample)
                .filter(
                    PreReviewSectionExample.project_id == project_key,
                    PreReviewSectionExample.section_id == section_key,
                    PreReviewSectionExample.is_active == 1,
                )
                .order_by(PreReviewSectionExample.update_time.desc(), PreReviewSectionExample.id.desc())
                .all()
            )
            items = [self._normalize_example_record(row) for row in rows]
            type_breakdown: Dict[str, int] = {}
            for item in items:
                example_type = str(item.get("example_type", "") or "unknown").strip() or "unknown"
                type_breakdown[example_type] = int(type_breakdown.get(example_type, 0)) + 1
            return True, "success", {
                "project_id": project_key,
                "section_id": section_key,
                "items": items,
                "type_breakdown": type_breakdown,
                "summary": {
                    "total": len(items),
                    "reference_count": int(type_breakdown.get("reference", 0)),
                    "few_shot_count": int(type_breakdown.get("few_shot", 0)),
                    "evaluation_case_count": int(type_breakdown.get("evaluation_case", 0)),
                },
            }
        finally:
            session.close()

    def save_section_rules(
        self,
        section_id: str,
        rule_texts: Optional[List[Any]] = None,
        scope_metadata: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        section_key = str(section_id or "").strip()
        if not section_key:
            return False, "section_id is required", None
        normalized_rule_texts = self._dedupe_text_list(rule_texts or [])
        normalized_scope_metadata = self._normalize_rule_scope_metadata(scope_metadata or {}, section_id=section_key)
        scope_key = self._build_rule_scope_key(normalized_scope_metadata)
        session = self.db_conn.get_session()
        try:
            section_meta = self.ctd_sections.get_section(section_key, leaf_only=False)
            if not isinstance(section_meta, dict):
                return False, f"section not found: {section_key}", None
            section_name = str(section_meta.get("section_name", "") or section_key).strip() or section_key
            global_project_id = self._ensure_global_rule_project(session)
            self._ensure_project_section_rules(session, global_project_id)
            now = self._now()

            existing_rows = (
                session.query(PreReviewSectionRule)
                .filter(
                    PreReviewSectionRule.project_id == global_project_id,
                    PreReviewSectionRule.section_id == section_key,
                    PreReviewSectionRule.source_type == "manual",
                )
                .order_by(PreReviewSectionRule.id.asc())
                .all()
            )
            scoped_existing_rows: List[PreReviewSectionRule] = []
            for row in existing_rows:
                try:
                    payload = json.loads(getattr(row, "payload_json", "") or "{}")
                except Exception:
                    payload = {}
                row_scope = self._extract_rule_scope_metadata_from_payload(payload, section_id=section_key)
                if self._build_rule_scope_key(row_scope) == scope_key:
                    scoped_existing_rows.append(row)
            existing_by_code = {
                str(getattr(row, "rule_code", "") or "").strip(): row
                for row in scoped_existing_rows
                if str(getattr(row, "rule_code", "") or "").strip()
            }
            expected_codes = {
                f"manual_rule__{section_key.replace('.', '_')}__{scope_key}__{index:02d}"
                for index, _ in enumerate(normalized_rule_texts, start=1)
            }

            for row in scoped_existing_rows:
                code = str(getattr(row, "rule_code", "") or "").strip()
                if code and code not in expected_codes:
                    session.delete(row)

            for index, rule_text in enumerate(normalized_rule_texts, start=1):
                rule_code = f"manual_rule__{section_key.replace('.', '_')}__{scope_key}__{index:02d}"
                payload = {
                    "section_id": section_key,
                    "section_name": section_name,
                    "rule_code": rule_code,
                    "rule_text": rule_text,
                    "source_type": "manual",
                    "scope_metadata": normalized_scope_metadata,
                }
                row = existing_by_code.get(rule_code)
                if row is None:
                    row = PreReviewSectionRule(
                        rule_id=f"section_rule_{uuid.uuid4().hex[:16]}",
                        project_id=global_project_id,
                        section_id=section_key,
                        section_name=section_name,
                        rule_code=rule_code,
                        rule_text=rule_text,
                        source_type="manual",
                        source_ref="manual_rule_editor",
                        is_active=True,
                        payload_json=json.dumps(payload, ensure_ascii=False),
                        create_time=now,
                        update_time=now,
                    )
                    session.add(row)
                else:
                    row.section_name = section_name
                    row.rule_text = rule_text
                    row.source_type = "manual"
                    row.source_ref = "manual_rule_editor"
                    row.is_active = True
                    row.payload_json = json.dumps(payload, ensure_ascii=False)
                    row.update_time = now

            session.commit()
            self._invalidate_project_section_catalog_cache(project_id=global_project_id)
            return self.get_section_rules(section_key, scope_metadata=normalized_scope_metadata)
        except Exception as exc:
            session.rollback()
            return False, f"save section rules failed: {str(exc)}", None
        finally:
            session.close()

    def batch_delete_section_rules(
        self,
        section_id: str,
        rule_codes: Optional[List[Any]] = None,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        section_key = str(section_id or "").strip()
        normalized_rule_codes = [
            str(item or "").strip()
            for item in (rule_codes or [])
            if str(item or "").strip()
        ]
        if not section_key or not normalized_rule_codes:
            return False, "section_id and rule_codes are required", None
        session = self.db_conn.get_session()
        try:
            section_meta = self.ctd_sections.get_section(section_key, leaf_only=False)
            if not isinstance(section_meta, dict):
                return False, f"section not found: {section_key}", None
            global_project_id = self._ensure_global_rule_project(session)
            rows = (
                session.query(PreReviewSectionRule)
                .filter(
                    PreReviewSectionRule.project_id == global_project_id,
                    PreReviewSectionRule.section_id == section_key,
                    PreReviewSectionRule.rule_code.in_(normalized_rule_codes),
                )
                .all()
            )
            if not rows:
                return False, "section rules not found", None
            for row in rows:
                session.delete(row)
            session.commit()
            self._invalidate_project_section_catalog_cache(project_id=global_project_id)
            return self.get_section_rules(section_key)
        except Exception as exc:
            session.rollback()
            return False, f"batch delete section rules failed: {str(exc)}", None
        finally:
            session.close()

    def delete_section_rules_by_scope(
        self,
        scope_metadata: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        ok, msg, preview = self.preview_delete_section_rules_by_scope(scope_metadata=scope_metadata)
        if not ok:
            return ok, msg, preview
        normalized_scope_metadata = (
            preview.get("scope_metadata", {})
            if isinstance(preview, dict)
            else self._normalize_rule_scope_metadata(scope_metadata or {})
        )
        matched_rule_ids = (
            set(preview.get("rule_ids", []))
            if isinstance(preview, dict) and isinstance(preview.get("rule_ids", []), list)
            else set()
        )
        session = self.db_conn.get_session()
        try:
            if not matched_rule_ids:
                return False, "scope rules not found", None
            rows = (
                session.query(PreReviewSectionRule)
                .filter(PreReviewSectionRule.rule_id.in_(list(matched_rule_ids)))
                .all()
            )
            if not rows:
                return False, "scope rules not found", None
            for row in rows:
                session.delete(row)
            session.commit()
            self._invalidate_project_section_catalog_cache(project_id=GLOBAL_SECTION_RULE_PROJECT_ID)
            preview_payload = dict(preview or {})
            preview_payload.pop("rule_ids", None)
            preview_payload["deleted_rule_count"] = int(preview_payload.get("matched_rule_count", 0) or 0)
            preview_payload["deleted_section_count"] = int(preview_payload.get("matched_section_count", 0) or 0)
            return True, "success", preview_payload
        except Exception as exc:
            session.rollback()
            return False, f"delete scope section rules failed: {str(exc)}", None
        finally:
            session.close()

    def preview_delete_section_rules_by_scope(
        self,
        scope_metadata: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        normalized_scope_metadata = self._normalize_rule_scope_metadata(scope_metadata or {})
        scope_selector = self._scope_without_section_prefix(normalized_scope_metadata)
        if not any(str(value or "").strip() for value in scope_selector.values()):
            return False, "scope_metadata is required", None
        session = self.db_conn.get_session()
        try:
            global_project_id = self._ensure_global_rule_project(session)
            selector_module = self.ctd_sections.normalize_section_id(normalized_scope_metadata.get("module"))
            allowed_section_ids = (
                set(self._scope_module_section_ids(selector_module))
                if selector_module
                else None
            )
            rows = (
                session.query(PreReviewSectionRule)
                .filter(
                    PreReviewSectionRule.project_id == global_project_id,
                    PreReviewSectionRule.source_type.in_(["ctd_seed", "json_import", "manual"]),
                )
                .all()
            )
            matched_rows: List[PreReviewSectionRule] = []
            for row in rows:
                try:
                    payload = json.loads(getattr(row, "payload_json", "") or "{}")
                except Exception:
                    payload = {}
                row_section_id = str(getattr(row, "section_id", "") or "").strip()
                row_scope = self._extract_rule_scope_metadata_from_payload(
                    payload if isinstance(payload, dict) else {},
                    section_id=row_section_id,
                )
                if self._scope_selector_matches_rule(
                    normalized_scope_metadata,
                    row_scope,
                    row_section_id=row_section_id,
                    allowed_section_ids=allowed_section_ids,
                ):
                    matched_rows.append(row)
            if not matched_rows:
                return False, "scope rules not found", None
            matched_section_ids = sorted(
                {
                    str(getattr(row, "section_id", "") or "").strip()
                    for row in matched_rows
                    if str(getattr(row, "section_id", "") or "").strip()
                }
            )
            matched_rule_codes = sorted(
                {
                    str(getattr(row, "rule_code", "") or "").strip()
                    for row in matched_rows
                    if str(getattr(row, "rule_code", "") or "").strip()
                }
            )
            return True, "success", {
                "scope_metadata": normalized_scope_metadata,
                "matched_rule_count": len(matched_rows),
                "matched_section_count": len(matched_section_ids),
                "section_ids": matched_section_ids,
                "rule_codes": matched_rule_codes,
                "rule_ids": [
                    str(getattr(row, "rule_id", "") or "").strip()
                    for row in matched_rows
                    if str(getattr(row, "rule_id", "") or "").strip()
                ],
            }
        except Exception as exc:
            return False, f"preview scope section rules failed: {str(exc)}", None
        finally:
            session.close()

    def save_project_section_rules(
        self,
        project_id: str,
        section_id: str,
        rule_texts: Optional[List[Any]] = None,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        project_key = str(project_id or "").strip()
        section_key = str(section_id or "").strip()
        if not project_key or not section_key:
            return False, "project_id and section_id are required", None
        session = self.db_conn.get_session()
        try:
            project = (
                session.query(PreReviewProject)
                .filter(and_(PreReviewProject.project_id == project_key, PreReviewProject.is_deleted == 0))
                .first()
            )
            if project is None:
                return False, "project not found", None
        finally:
            session.close()
        return self.save_section_rules(section_id=section_key, rule_texts=rule_texts)

    def import_section_rules_json(
        self,
        file_bytes: bytes,
        file_name: str = "",
        scope_metadata: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        session = self.db_conn.get_session()
        try:
            parser = ReviewRuleJsonParser(self.ctd_sections)
            normalized_rows = parser.parse_bytes(file_bytes=file_bytes, file_name=file_name)
            global_project_id = self._ensure_global_rule_project(session)
            normalized_scope_metadata = self._normalize_rule_scope_metadata(scope_metadata or {})
            has_registration_scope = any(
                str(normalized_scope_metadata.get(key, "") or "").strip()
                for key in ["registration_scope", "registration_class", "registration_class_sub"]
            )
            if not has_registration_scope or not str(normalized_scope_metadata.get("module", "") or "").strip():
                return False, "import json rules requires explicit scope and module", None
            scope_key = self._build_rule_scope_key(normalized_scope_metadata)
            imported_rule_map = {
                str(item.get("section_id", "") or "").strip(): self._dedupe_text_list(item.get("review_rules", []))
                for item in normalized_rows
                if str(item.get("section_id", "") or "").strip()
            }
            imported_section_ids = list(imported_rule_map.keys())
            if imported_section_ids:
                existing_rows = (
                    session.query(PreReviewSectionRule)
                    .filter(
                        PreReviewSectionRule.project_id == global_project_id,
                        PreReviewSectionRule.section_id.in_(imported_section_ids),
                        PreReviewSectionRule.source_type.in_(["ctd_seed", "json_import"]),
                    )
                    .all()
                )
                for row in existing_rows:
                    try:
                        payload = json.loads(getattr(row, "payload_json", "") or "{}")
                    except Exception:
                        payload = {}
                    row_scope_key = self._build_rule_scope_key(
                        self._extract_rule_scope_metadata_from_payload(
                            payload,
                            section_id=str(getattr(row, "section_id", "") or "").strip(),
                        )
                    )
                    if row_scope_key == scope_key:
                        session.delete(row)
            self._replace_global_seed_section_rule_map(
                session,
                imported_rule_map,
                source_ref=file_name or "uploaded_json",
                source_type="json_import",
                scope_metadata=normalized_scope_metadata,
            )
            session.commit()
            self._invalidate_project_section_catalog_cache(project_id=global_project_id)
            return True, "section rules imported", {
                "imported_section_count": len(normalized_rows),
                "imported_rule_count": sum(len(item.get("review_rules", [])) for item in normalized_rows),
                "source_type": "json_import",
                "scope_metadata": normalized_scope_metadata,
                "items": normalized_rows,
            }
        except Exception as exc:
            session.rollback()
            return False, f"import section rules failed: {str(exc)}", None
        finally:
            session.close()

    def list_seed_prompt_rules(self, task_type: str = "") -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        normalized_task_type = str(task_type or "").strip()
        session = self.db_conn.get_session()
        try:
            self.prompt_rule_service.bootstrap_system_rules(session)
            session.commit()
            items = self.prompt_rule_service.list_seed_rules(normalized_task_type)
            task_breakdown: Dict[str, int] = {}
            for item in items:
                task_key = str(item.get("task_type", "") or "").strip() or "unknown"
                task_breakdown[task_key] = int(task_breakdown.get(task_key, 0)) + 1
            return True, "success", {
                "task_type": normalized_task_type,
                "items": items,
                "task_breakdown": task_breakdown,
                "summary": {
                    "total": len(items),
                },
            }
        except Exception as exc:
            session.rollback()
            return False, f"list seed prompt rules failed: {str(exc)}", None
        finally:
            session.close()

    def save_seed_prompt_rule(self, task_type: str, rule_payload: Dict[str, Any]) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        normalized_task_type = str(task_type or "").strip()
        if not normalized_task_type:
            return False, "task_type is required", None
        session = self.db_conn.get_session()
        try:
            updated_rule = self.prompt_rule_service.upsert_seed_rule(
                normalized_task_type,
                rule_payload if isinstance(rule_payload, dict) else {},
            )
            self.prompt_rule_service.bootstrap_system_rules(session)
            session.commit()
            ok, msg, data = self.list_seed_prompt_rules(normalized_task_type)
            if not ok:
                return False, msg, None
            return True, "seed prompt rule saved", {
                **(data or {}),
                "updated_rule": updated_rule,
            }
        except Exception as exc:
            session.rollback()
            return False, f"save seed prompt rule failed: {str(exc)}", None
        finally:
            session.close()

    def delete_seed_prompt_rule(self, task_type: str, rule_code: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        normalized_task_type = str(task_type or "").strip()
        normalized_rule_code = str(rule_code or "").strip()
        if not normalized_task_type or not normalized_rule_code:
            return False, "task_type and rule_code are required", None
        session = self.db_conn.get_session()
        try:
            changed = self.prompt_rule_service.delete_seed_rule(normalized_task_type, normalized_rule_code)
            if not changed:
                return False, "seed prompt rule not found", None
            self.prompt_rule_service.bootstrap_system_rules(session)
            session.commit()
            ok, msg, data = self.list_seed_prompt_rules(normalized_task_type)
            if not ok:
                return False, msg, None
            return True, "seed prompt rule deleted", data
        except Exception as exc:
            session.rollback()
            return False, f"delete seed prompt rule failed: {str(exc)}", None
        finally:
            session.close()

    def get_project_section_prompt_rules(self, project_id: str, section_id: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        project_key = str(project_id or "").strip()
        section_key = str(section_id or "").strip()
        if not project_key or not section_key:
            return False, "project_id and section_id are required", None
        session = self.db_conn.get_session()
        try:
            project = (
                session.query(PreReviewProject)
                .filter(and_(PreReviewProject.project_id == project_key, PreReviewProject.is_deleted == 0))
                .first()
            )
            if project is None:
                return False, "project not found", None
            self.prompt_rule_service.ensure_project_rules(session, project_key)
            catalog = self._load_project_section_catalog(session, project_key)
            section_map = catalog.get("section_map", {}) if isinstance(catalog.get("section_map", {}), dict) else {}
            section_meta = section_map.get(section_key, {}) if isinstance(section_map.get(section_key, {}), dict) else {}
            section_name = str(section_meta.get("section_name", "") or section_key).strip() or section_key
            review_domain = self._infer_review_domain(project)
            product_type = self._infer_product_type(project, section_name=section_name)
            registration_class = self._infer_registration_class(project)
            task_types = [
                "planner",
                "retrieval_evaluator",
                "task_question",
                "reviewer",
                "feedback_analyzer",
                "feedback_optimizer",
                "meta_reflector",
            ]
            items: List[Dict[str, Any]] = []
            task_breakdown: Dict[str, int] = {}
            source_breakdown: Dict[str, int] = {}
            for task_type in task_types:
                rows = self.prompt_rule_service.resolve_rules(
                    session=session,
                    project_id=project_key,
                    task_type=task_type,
                    section_id=section_key,
                    section_name=section_name,
                    review_domain=review_domain,
                    product_type=product_type,
                    registration_class=registration_class,
                )
                task_breakdown[task_type] = len(rows)
                for row in rows:
                    source_type = str(getattr(row, "source_type", "") or "").strip() or "unknown"
                    source_breakdown[source_type] = int(source_breakdown.get(source_type, 0)) + 1
                    try:
                        payload = json.loads(getattr(row, "payload_json", "") or "{}")
                    except Exception:
                        payload = {}
                    items.append(
                        {
                            "rule_id": str(getattr(row, "rule_id", "") or "").strip(),
                            "task_type": str(getattr(row, "task_type", "") or "").strip(),
                            "template_name": str(getattr(row, "template_name", "") or "").strip(),
                            "route_key": str(getattr(row, "route_key", "") or "").strip(),
                            "scope_type": str(getattr(row, "scope_type", "") or "").strip(),
                            "rule_code": str(getattr(row, "rule_code", "") or "").strip(),
                            "rule_name": str(getattr(row, "rule_name", "") or "").strip(),
                            "rule_text": str(getattr(row, "rule_text", "") or "").strip(),
                            "source_type": source_type,
                            "priority": int(getattr(row, "priority", 0) or 0),
                            "payload": payload if isinstance(payload, dict) else {},
                            "update_time": row.update_time.strftime("%Y-%m-%d %H:%M:%S") if getattr(row, "update_time", None) else "",
                        }
                    )
            return True, "success", {
                "project_id": project_key,
                "section_id": section_key,
                "section_name": section_name,
                "items": items,
                "task_breakdown": task_breakdown,
                "source_breakdown": source_breakdown,
                "summary": {
                    "total": len(items),
                    "seed_count": int(source_breakdown.get("seed", 0)),
                    "feedback_patch_count": int(source_breakdown.get("feedback_patch", 0)),
                },
            }
        finally:
            session.close()

    def get_project_section_experience_memory(self, project_id: str, section_id: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        project_key = str(project_id or "").strip()
        section_key = str(section_id or "").strip()
        if not project_key or not section_key:
            return False, "project_id and section_id are required", None
        session = self.db_conn.get_session()
        try:
            project = (
                session.query(PreReviewProject)
                .filter(and_(PreReviewProject.project_id == project_key, PreReviewProject.is_deleted == 0))
                .first()
            )
            if project is None:
                return False, "project not found", None
            catalog = self._load_project_section_catalog(session, project_key)
            section_map = catalog.get("section_map", {}) if isinstance(catalog.get("section_map", {}), dict) else {}
            section_meta = section_map.get(section_key, {}) if isinstance(section_map.get(section_key, {}), dict) else {}
            section_name = str(section_meta.get("section_name", "") or section_key).strip() or section_key
            product_type = self._infer_product_type(project, section_name=section_name)
            rows = (
                session.query(PreReviewExperienceMemory)
                .filter(PreReviewExperienceMemory.status == "active")
                .order_by(PreReviewExperienceMemory.update_time.desc(), PreReviewExperienceMemory.id.desc())
                .all()
            )
            items: List[Dict[str, Any]] = []
            scope_breakdown: Dict[str, int] = {}
            type_breakdown: Dict[str, int] = {}
            knowledge_category_breakdown: Dict[str, int] = {}
            for row in rows:
                scope_type = str(getattr(row, "scope_type", "") or "").strip()
                scope_key = str(getattr(row, "scope_key", "") or "").strip()
                matched = False
                if scope_type == "section" and scope_key == section_key:
                    matched = True
                elif scope_type == "product_type" and scope_key == product_type:
                    matched = True
                elif scope_type == "project" and scope_key == project_key:
                    matched = True
                if not matched:
                    continue
                try:
                    payload = json.loads(getattr(row, "payload_json", "") or "{}")
                except Exception:
                    payload = {}
                payload_dict = payload if isinstance(payload, dict) else {}
                experience_type = str(getattr(row, "experience_type", "") or "").strip() or "unknown"
                knowledge_category = str(payload_dict.get("knowledge_category", "") or "").strip() or "unknown"
                scope_breakdown[scope_type or "unknown"] = int(scope_breakdown.get(scope_type or "unknown", 0)) + 1
                type_breakdown[experience_type] = int(type_breakdown.get(experience_type, 0)) + 1
                knowledge_category_breakdown[knowledge_category] = int(knowledge_category_breakdown.get(knowledge_category, 0)) + 1
                items.append(
                    {
                        "experience_id": str(getattr(row, "experience_id", "") or "").strip(),
                        "scope_type": scope_type,
                        "scope_key": scope_key,
                        "experience_type": experience_type,
                        "knowledge_category": knowledge_category,
                        "optimization_target": str(payload_dict.get("optimization_target", "") or "").strip(),
                        "source_error_type": str(payload_dict.get("source_error_type", "") or "").strip(),
                        "content": str(getattr(row, "content", "") or "").strip(),
                        "source_feedback_ids": self._parse_json_list(getattr(row, "source_feedback_ids", "") or ""),
                        "trigger_conditions": self._parse_json_list(getattr(row, "trigger_conditions", "") or ""),
                        "usage_count": int(getattr(row, "usage_count", 0) or 0),
                        "success_count": int(getattr(row, "success_count", 0) or 0),
                        "payload": payload_dict,
                        "update_time": row.update_time.strftime("%Y-%m-%d %H:%M:%S") if getattr(row, "update_time", None) else "",
                    }
                )
            return True, "success", {
                "project_id": project_key,
                "section_id": section_key,
                "section_name": section_name,
                "product_type": product_type,
                "items": items,
                "scope_breakdown": scope_breakdown,
                "type_breakdown": type_breakdown,
                "knowledge_category_breakdown": knowledge_category_breakdown,
                "summary": {
                    "total": len(items),
                    "section_scope_count": int(scope_breakdown.get("section", 0)),
                    "product_scope_count": int(scope_breakdown.get("product_type", 0)),
                    "project_scope_count": int(scope_breakdown.get("project", 0)),
                },
            }
        finally:
            session.close()

    def _build_feedback_replay_bundle(
        self,
        run_id: str,
        section_id: str,
        feedback_payload: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        run_key = str(run_id or "").strip()
        section_key = str(section_id or "").strip()
        if not run_key or not section_key:
            return False, "run_id and section_id are required", None
        session = self.db_conn.get_session()
        try:
            run = session.query(PreReviewRun).filter(PreReviewRun.run_id == run_key).first()
            if run is None:
                return False, "run not found", None
            traces = self.get_section_traces(run_id=run_key, section_id=section_key)
            run_trace = traces[-1] if isinstance(traces, list) and traces else {}
            overview_ok, _, overview = self.get_run_section_overview(run_id=run_key)
            overview = overview if overview_ok and isinstance(overview, dict) else {}
            run_config = dict(overview.get("run_config", {}) or {}) if isinstance(overview.get("run_config", {}), dict) else {}
            prompt_config = dict(run_config.get("prompt_config", {}) or {}) if isinstance(run_config.get("prompt_config", {}), dict) else {}
            payloads = self._load_feedback_analysis_payloads(session, run_id=run_key, section_id=section_key)
            latest_payload = payloads[-1] if payloads else {}
            latest_meta = self._extract_feedback_meta(latest_payload if isinstance(latest_payload, dict) else {})
            latest_feedback = (
                session.query(PreReviewFeedback)
                .filter(
                    PreReviewFeedback.run_id == run_key,
                    PreReviewFeedback.section_id == section_key,
                )
                .order_by(PreReviewFeedback.id.desc())
                .first()
            )
            issue_feedback_rows = self._normalize_issue_feedback_rows(
                (feedback_payload or {}).get("issue_feedback", latest_meta.get("issue_feedback", []))
            )
            missing_item_feedback = (feedback_payload or {}).get("missing_item_feedback", latest_meta.get("missing_item_feedback", {}))
            if not isinstance(missing_item_feedback, dict):
                missing_item_feedback = {}
            feedback_text_value = str((feedback_payload or {}).get("feedback_text", "") or getattr(latest_feedback, "feedback_text", "") or "")
            if not feedback_text_value.strip() and issue_feedback_rows:
                feedback_text_value = "；".join(
                    [
                        str(item.get("feedback_text", "") or item.get("issue", "") or item.get("task_question", "") or "").strip()
                        for item in issue_feedback_rows[:4]
                        if str(item.get("feedback_text", "") or item.get("issue", "") or item.get("task_question", "") or "").strip()
                    ]
                )
            feedback_record = {
                "run_id": run_key,
                "section_id": section_key,
                "decision": str((feedback_payload or {}).get("decision", "") or latest_meta.get("decision", "") or ""),
                "feedback_type": str((feedback_payload or {}).get("feedback_type", "") or latest_meta.get("feedback_type", "") or ""),
                "chain_mode": str((feedback_payload or {}).get("chain_mode", "") or latest_meta.get("chain_mode", "") or "feedback_optimize"),
                "manual_modified": bool((feedback_payload or {}).get("manual_modified", latest_meta.get("manual_modified", False))),
                "labels": (feedback_payload or {}).get("labels", latest_meta.get("labels", [])) if isinstance((feedback_payload or {}).get("labels", latest_meta.get("labels", [])), list) else [],
                "evidence_feedback": (feedback_payload or {}).get("evidence_feedback", latest_meta.get("evidence_feedback", [])) if isinstance((feedback_payload or {}).get("evidence_feedback", latest_meta.get("evidence_feedback", [])), list) else [],
                "retrieval_feedback": str((feedback_payload or {}).get("retrieval_feedback", "") or latest_meta.get("retrieval_feedback", "") or ""),
                "conclusion_feedback": str((feedback_payload or {}).get("conclusion_feedback", "") or latest_meta.get("conclusion_feedback", "") or ""),
                "reference_example": (feedback_payload or {}).get("reference_example", latest_meta.get("reference_example", {})) if isinstance((feedback_payload or {}).get("reference_example", latest_meta.get("reference_example", {})), dict) else {},
                "original_output": (feedback_payload or {}).get("original_output", latest_meta.get("original_output", {})) if isinstance((feedback_payload or {}).get("original_output", latest_meta.get("original_output", {})), dict) else {},
                "revised_output": (feedback_payload or {}).get("revised_output", latest_meta.get("revised_output", {})) if isinstance((feedback_payload or {}).get("revised_output", latest_meta.get("revised_output", {})), dict) else {},
                "feedback_text": feedback_text_value,
                "suggestion": str((feedback_payload or {}).get("suggestion", "") or latest_meta.get("suggestion", "") or getattr(latest_feedback, "suggestion", "") or ""),
                "operator": str((feedback_payload or {}).get("operator", "") or getattr(latest_feedback, "operator", "") or ""),
                "feedback_key": str((feedback_payload or {}).get("feedback_key", "") or (latest_payload.get("feedback_key", "") if isinstance(latest_payload, dict) else "") or ""),
                "issue_feedback": issue_feedback_rows,
                "missing_item_feedback": missing_item_feedback,
            }
            return True, "success", {
                "feedback_record": feedback_record,
                "run_trace": run_trace if isinstance(run_trace, dict) else {},
                "run_context": {
                    "run_id": run_key,
                    "project_id": str(getattr(run, "project_id", "") or ""),
                    "source_doc_id": str(getattr(run, "source_doc_id", "") or ""),
                    "run_config": run_config,
                    "prompt_config": prompt_config,
                },
            }
        finally:
            session.close()

    @staticmethod
    def _normalize_issue_feedback_rows(rows: Any) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        rows_input: List[Dict[str, Any]] = []
        if isinstance(rows, list):
            rows_input = [item for item in rows if isinstance(item, dict)]
        elif isinstance(rows, dict):
            for issue_key, item in rows.items():
                if not isinstance(item, dict):
                    continue
                row_item = dict(item)
                if not str(row_item.get("issue_key", "") or "").strip():
                    row_item["issue_key"] = str(issue_key or "").strip()
                rows_input.append(row_item)
        for item in rows_input:
            if not isinstance(item, dict):
                continue
            issue_key = str(item.get("issue_key", "") or "").strip()
            task_code = str(item.get("task_code", "") or "").strip()
            task_question = str(item.get("task_question", "") or "").strip()
            issue = str(item.get("issue", "") or item.get("problem", "") or "").strip()
            feedback_text = str(item.get("feedback_text", "") or "").strip()
            if not any([issue_key, task_code, task_question, issue, feedback_text]):
                continue
            normalized.append(
                {
                    "issue_key": issue_key,
                    "feedback_kind": str(item.get("feedback_kind", "") or "risk_item").strip(),
                    "verdict": str(item.get("verdict", "") or "").strip(),
                    "feedback_text": feedback_text,
                    "error_reason": str(item.get("error_reason", "") or "").strip(),
                    "task_code": task_code,
                    "task_question": task_question,
                    "task_status": str(item.get("task_status", "") or "").strip(),
                    "rule_code": str(item.get("rule_code", "") or "").strip(),
                    "rule_text": str(item.get("rule_text", "") or "").strip(),
                    "requirement_point": str(item.get("requirement_point", "") or "").strip(),
                    "basis": str(item.get("basis", "") or "").strip(),
                    "material_fact": str(item.get("material_fact", "") or "").strip(),
                    "issue": issue,
                    "problem": str(item.get("problem", "") or item.get("issue", "") or "").strip(),
                    "advice": str(item.get("advice", "") or "").strip(),
                    "comparison": str(item.get("comparison", "") or "").strip(),
                    "location": str(item.get("location", "") or "").strip(),
                    "violating_text": str(item.get("violating_text", "") or "").strip(),
                    "evidence_files": item.get("evidence_files", []) if isinstance(item.get("evidence_files", []), list) else [],
                    "evidence_support": str(item.get("evidence_support", "") or "").strip(),
                    "judgment_reason": str(item.get("judgment_reason", "") or "").strip(),
                    "field_feedback": item.get("field_feedback", {}) if isinstance(item.get("field_feedback", {}), dict) else {},
                }
            )
        return normalized

    def _build_feedback_bundle_signature(
        self,
        feedback_payload: Optional[Dict[str, Any]] = None,
    ) -> str:
        payload = feedback_payload if isinstance(feedback_payload, dict) else {}
        signature_payload = {
            "feedback_key": str(payload.get("feedback_key", "") or "").strip(),
            "decision": str(payload.get("decision", "") or "").strip(),
            "feedback_type": str(payload.get("feedback_type", "") or "").strip(),
            "chain_mode": str(payload.get("chain_mode", "") or "").strip(),
            "feedback_text": str(payload.get("feedback_text", "") or "").strip(),
            "suggestion": str(payload.get("suggestion", "") or "").strip(),
            "labels": payload.get("labels", []) if isinstance(payload.get("labels", []), list) else [],
            "missing_item_feedback": payload.get("missing_item_feedback", {}) if isinstance(payload.get("missing_item_feedback", {}), dict) else {},
            "issue_feedback": self._normalize_issue_feedback_rows(payload.get("issue_feedback", [])),
        }
        try:
            return json.dumps(signature_payload, ensure_ascii=False, sort_keys=True)
        except Exception:
            return str(signature_payload)

    def _make_p52_feedback_bundle_cache_key(
        self,
        *,
        run_id: str,
        section_id: str,
        signature: str,
    ) -> str:
        signature_digest = hashlib.sha1(str(signature or "").encode("utf-8")).hexdigest()
        return "::".join(
            [
                str(run_id or "").strip(),
                str(section_id or "").strip().lower(),
                signature_digest,
            ]
        )

    @staticmethod
    def _filter_candidate_p52_patch_rows(patch_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        candidates: List[Dict[str, Any]] = []
        for item in patch_rows or []:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status", "") or "").strip().lower()
            if status == "candidate":
                candidates.append(item)
        return candidates

    def _build_p52_feedback_workflow_bundle(
        self,
        *,
        run_id: str,
        section_id: str,
        feedback_payload: Optional[Dict[str, Any]] = None,
        force_refresh: bool = False,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        if not self._is_p52_section_id(section_id):
            return False, "section_id is not under 3.2.P.5.2", None
        signature = self._build_feedback_bundle_signature(feedback_payload)
        cache_key = self._make_p52_feedback_bundle_cache_key(
            run_id=run_id,
            section_id=section_id,
            signature=signature,
        )
        if not force_refresh:
            with self._p52_feedback_bundle_cache_lock:
                cached_bundle = self._p52_feedback_bundle_cache.get(cache_key)
                if isinstance(cached_bundle, dict) and cached_bundle:
                    self._p52_feedback_bundle_cache.move_to_end(cache_key, last=True)
            if isinstance(cached_bundle, dict) and cached_bundle:
                return True, "success", deepcopy(cached_bundle)
        ok, msg, base_bundle = self._build_feedback_replay_bundle(
            run_id=run_id,
            section_id=section_id,
            feedback_payload=feedback_payload,
        )
        if not ok or not isinstance(base_bundle, dict):
            return False, msg, None
        patch_rows = self.p52_feedback_optimize_orchestrator.list_patches(
            run_id=run_id,
            section_id=section_id,
            status="",
        )
        workflow_bundle = {
            **base_bundle,
            "bundle_signature": signature,
            "patch_rows": patch_rows,
            "candidate_patch_rows": self._filter_candidate_p52_patch_rows(patch_rows),
        }
        with self._p52_feedback_bundle_cache_lock:
            self._p52_feedback_bundle_cache[cache_key] = deepcopy(workflow_bundle)
            self._p52_feedback_bundle_cache.move_to_end(cache_key, last=True)
            # 控制缓存总量，避免 run_trace 等大对象长期堆积导致内存上涨
            max_items = max(int(P52_FEEDBACK_BUNDLE_CACHE_MAX_ITEMS or 0), 8)
            while len(self._p52_feedback_bundle_cache) > max_items:
                self._p52_feedback_bundle_cache.popitem(last=False)
        return True, "success", workflow_bundle

    def _invalidate_p52_feedback_bundle_cache(
        self,
        *,
        run_id: str,
        section_id: str,
    ) -> None:
        run_key = str(run_id or "").strip()
        section_key = str(section_id or "").strip().lower()
        if not run_key or not section_key:
            return
        with self._p52_feedback_bundle_cache_lock:
            stale_keys = [
                cache_key
                for cache_key in list(self._p52_feedback_bundle_cache.keys())
                if cache_key.startswith(f"{run_key}::{section_key}::")
            ]
            for cache_key in stale_keys:
                self._p52_feedback_bundle_cache.pop(cache_key, None)

    def _persist_p52_meta_reflection_result(
        self,
        *,
        run_id: str,
        section_id: str,
        result: Dict[str, Any],
        patch_rows: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        meta_payload = result.get("meta_reflection", {}) if isinstance(result.get("meta_reflection", {}), dict) else {}
        persisted_patches = patch_rows if isinstance(patch_rows, list) else []
        self._persist_feedback_analysis_payload(
            run_id=run_id,
            section_id=section_id,
            feedback_key=f"p52_meta_reflection:{run_id}:{section_id}:{uuid.uuid4().hex[:8]}",
            payload={
                "section_id": section_id,
                "analysis_kind": "p52_meta_reflection",
                "workflow_mode": str(result.get("workflow_mode", "") or "p52_meta_reflection_v1"),
                "feedback_meta": {
                    "chain_mode": "p52_meta_reflection",
                    "feedback_optimize_status": "completed",
                    "candidate_register_status": "observed" if persisted_patches else "skipped",
                    "replay_status": "p52_meta_reflection_completed",
                    "p52_error_family": str(meta_payload.get("error_family", "") or "").strip(),
                },
                "meta_reflection": meta_payload,
                "patch_result": {"patches": persisted_patches},
            },
        )

    def _persist_p52_ablation_result(
        self,
        *,
        run_id: str,
        section_id: str,
        result: Dict[str, Any],
    ) -> None:
        summary = result.get("summary", {}) if isinstance(result.get("summary", {}), dict) else {}
        self._persist_feedback_analysis_payload(
            run_id=run_id,
            section_id=section_id,
            feedback_key=f"p52_ablation:{run_id}:{section_id}:{uuid.uuid4().hex[:8]}",
            payload={
                "section_id": section_id,
                "analysis_kind": "p52_ablation",
                "workflow_mode": str(result.get("workflow_mode", "") or "p52_ablation_v1"),
                "feedback_meta": {
                    "chain_mode": "p52_ablation",
                    "feedback_optimize_status": "completed",
                    "candidate_register_status": "n/a",
                    "replay_status": "p52_ablation_completed",
                },
                "ablation_result": result,
                "signal_summary": {
                    "variant_count": int(summary.get("variant_count", 0) or 0),
                    "best_variant_id": str(summary.get("best_variant_id", "") or "").strip(),
                },
            },
        )

    def _persist_p52_feedback_verify_result(
        self,
        *,
        run_id: str,
        section_id: str,
        result: Dict[str, Any],
        patch_rows: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        verification_payload = result.get("verification_result", {}) if isinstance(result.get("verification_result", {}), dict) else {}
        persisted_patches = patch_rows if isinstance(patch_rows, list) else []
        overall_verdict = str(verification_payload.get("overall_verdict", "") or "").strip()
        self._persist_feedback_analysis_payload(
            run_id=run_id,
            section_id=section_id,
            feedback_key=f"p52_feedback_verify:{run_id}:{section_id}:{uuid.uuid4().hex[:8]}",
            payload={
                "section_id": section_id,
                "analysis_kind": "p52_feedback_verify",
                "workflow_mode": str(result.get("workflow_mode", "") or "p52_feedback_replay_verify_v1"),
                "feedback_meta": {
                    "chain_mode": "p52_feedback_verify",
                    "feedback_optimize_status": "completed",
                    "candidate_register_status": "observed" if persisted_patches else "skipped",
                    "replay_status": "p52_feedback_verify_completed",
                    "p52_error_family": "",
                },
                "verification_result": verification_payload,
                "rule_merge_result": result.get("rule_merge_result", {}) if isinstance(result.get("rule_merge_result", {}), dict) else {},
                "patch_result": {"patches": persisted_patches},
                "feedback_projection": {
                    "issue_family_label": "回放验证",
                    "patch_count": len(persisted_patches),
                    "target_agent_labels": [
                        str(item.get("target_agent", "") or "").strip()
                        for item in persisted_patches
                        if isinstance(item, dict) and str(item.get("target_agent", "") or "").strip()
                    ],
                    "patch_types": [
                        str(item.get("patch_type", "") or "").strip()
                        for item in persisted_patches
                        if isinstance(item, dict) and str(item.get("patch_type", "") or "").strip()
                    ],
                    "verification_verdict": overall_verdict,
                },
            },
        )

    def _run_p52_feedback_workflow_stage(
        self,
        *,
        stage: str,
        run_id: str,
        section_id: str,
        feedback_payload: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        ok, msg, bundle = self._build_p52_feedback_workflow_bundle(
            run_id=run_id,
            section_id=section_id,
            feedback_payload=feedback_payload,
        )
        if not ok or not isinstance(bundle, dict):
            return False, msg, None
        feedback_record = bundle.get("feedback_record", {}) if isinstance(bundle.get("feedback_record", {}), dict) else {}
        run_trace = bundle.get("run_trace", {}) if isinstance(bundle.get("run_trace", {}), dict) else {}
        run_context = bundle.get("run_context", {}) if isinstance(bundle.get("run_context", {}), dict) else {}
        patch_rows = bundle.get("patch_rows", []) if isinstance(bundle.get("patch_rows", []), list) else []
        candidate_patch_rows = bundle.get("candidate_patch_rows", []) if isinstance(bundle.get("candidate_patch_rows", []), list) else []
        workflow_kind = f"p52_feedback_{stage}"
        workflow_id = self._build_p52_workflow_id(workflow_kind, run_id, section_id)
        self._log_p52_workflow_event(
            workflow_kind,
            "start",
            workflow_id=workflow_id,
            run_id=run_id,
            section_id=section_id,
            stage=stage,
            bundle_signature=str(bundle.get("bundle_signature", "") or "").strip(),
            patch_row_count=len(patch_rows),
            candidate_patch_row_count=len(candidate_patch_rows),
        )

        if stage == "optimize":
            result = self.p52_feedback_optimize_orchestrator.optimize(
                run_id=run_id,
                section_id=section_id,
                feedback_record=feedback_record,
                run_trace=run_trace,
                run_context=run_context,
                workflow_id=workflow_id,
            )
            if not bool(result.get("success", False)):
                self._log_p52_workflow_event(
                    workflow_kind,
                    "failed",
                    workflow_id=workflow_id,
                    run_id=run_id,
                    section_id=section_id,
                    stage=stage,
                    error_message=str(result.get("message", "p52 feedback optimize failed")),
                )
                return False, str(result.get("message", "p52 feedback optimize failed")), result
            result["workflow_id"] = workflow_id
            self._persist_p52_feedback_optimize_result(
                run_id=run_id,
                section_id=section_id,
                result=result,
            )
            self._invalidate_p52_feedback_bundle_cache(run_id=run_id, section_id=section_id)
            self._log_p52_workflow_event(
                workflow_kind,
                "completed",
                workflow_id=workflow_id,
                run_id=run_id,
                section_id=section_id,
                stage=stage,
                candidate_patch_count=len(result.get("candidate_patches", []) if isinstance(result.get("candidate_patches", []), list) else []),
            )
            return True, "p52 feedback optimize completed", result

        if stage == "verify":
            patch_id = str((feedback_payload or {}).get("patch_id", "") or "").strip()
            selected_patch_rows = patch_rows
            if patch_id:
                selected_patch_rows = [
                    item for item in patch_rows
                    if isinstance(item, dict) and str(item.get("patch_id", "") or "").strip() == patch_id
                ]
            else:
                selected_patch_rows = candidate_patch_rows
            result = self.p52_feedback_optimize_orchestrator.replay_verify(
                run_id=run_id,
                section_id=section_id,
                feedback_record=feedback_record,
                run_trace=run_trace,
                run_context=run_context,
                patch_rows=selected_patch_rows,
                workflow_id=workflow_id,
            )
            if not bool(result.get("success", False)):
                self._log_p52_workflow_event(
                    workflow_kind,
                    "failed",
                    workflow_id=workflow_id,
                    run_id=run_id,
                    section_id=section_id,
                    stage=stage,
                    error_message=str(result.get("message", "p52 feedback replay verify failed")),
                )
                return False, str(result.get("message", "p52 feedback replay verify failed")), result
            result["workflow_id"] = workflow_id
            verification_payload = result.get("verification_result", {}) if isinstance(result.get("verification_result", {}), dict) else {}
            overall_verdict = str(verification_payload.get("overall_verdict", "") or "").strip().lower()
            # 中文注释：当回放验证确认“improved”时，将本次参与验证的 candidate patch 自动升级为 approved，确保下一次新任务直接生效
            if overall_verdict == "improved":
                for patch in selected_patch_rows:
                    if not isinstance(patch, dict):
                        continue
                    patch_id = str(patch.get("patch_id", "") or "").strip()
                    if not patch_id:
                        continue
                    status = str(patch.get("status", "") or "").strip().lower()
                    if status != "candidate":
                        continue
                    upgraded = self.p52_feedback_optimize_orchestrator.approve_patch(
                        run_id=run_id,
                        section_id=section_id,
                        patch_id=patch_id,
                        operator="system_auto_verify",
                        comment="自动审批：回放验证结论 improved",
                    )
                    if isinstance(upgraded, dict):
                        patch.update(upgraded)
                self._invalidate_p52_feedback_bundle_cache(run_id=run_id, section_id=section_id)
                overlay_after_verify = self.p52_patch_apply_service.build_runtime_overlay(
                    run_id=run_id,
                    section_id=section_id,
                    include_approved=True,
                    include_candidate=False,
                )
                result["runtime_overlay_after_verify"] = self.p52_patch_apply_service.describe_runtime_overlay(overlay_after_verify)
                # 中文注释：验证通过时，把本次命中的经验标记为成功样本，用于后续经验排序
                trace_snapshot = run_trace.get("trace", {}) if isinstance(run_trace.get("trace", {}), dict) else run_trace
                method_context = trace_snapshot.get("method_context", {}) if isinstance(trace_snapshot.get("method_context", {}), dict) else {}
                used_experience_ids = [
                    str(item.get("experience_id", "") or "").strip()
                    for item in method_context.get("p52_historical_experience", [])
                    if isinstance(method_context.get("p52_historical_experience", []), list)
                    and isinstance(item, dict)
                    and str(item.get("experience_id", "") or "").strip()
                ]
                self.mark_p52_experience_usage(experience_ids=used_experience_ids, success=True)
            selected_rule_patches = [
                item for item in selected_patch_rows
                if isinstance(item, dict) and str(item.get("patch_type", "") or "").strip() == "rule_patch"
            ]
            if selected_rule_patches:
                merge_result = self.p52_rule_patch_persist_service.merge_verified_rule_patches(
                    run_id=run_id,
                    section_id=section_id,
                    patch_rows=selected_rule_patches,
                    verification_result=result.get("verification_result", {}) if isinstance(result.get("verification_result", {}), dict) else {},
                )
                result["rule_merge_result"] = merge_result
                if bool(merge_result.get("merged", False)):
                    self.p52_rule_review_orchestrator.rule_engine = self.p52_rule_review_orchestrator.rule_engine.__class__()
            self._persist_p52_feedback_verify_result(
                run_id=run_id,
                section_id=section_id,
                result=result,
                patch_rows=selected_patch_rows,
            )
            self._log_p52_workflow_event(
                workflow_kind,
                "completed",
                workflow_id=workflow_id,
                run_id=run_id,
                section_id=section_id,
                stage=stage,
                selected_patch_count=len(selected_patch_rows),
            )
            return True, "p52 feedback replay verify completed", result

        if stage == "meta_reflection":
            result = self.p52_meta_reflection_orchestrator.reflect(
                run_id=run_id,
                section_id=section_id,
                feedback_record=feedback_record,
                run_trace=run_trace,
                run_context=run_context,
                patch_rows=patch_rows,
                workflow_id=workflow_id,
            )
            if not bool(result.get("success", False)):
                self._log_p52_workflow_event(
                    workflow_kind,
                    "failed",
                    workflow_id=workflow_id,
                    run_id=run_id,
                    section_id=section_id,
                    stage=stage,
                    error_message=str(result.get("message", "p52 meta reflection failed")),
                )
                return False, str(result.get("message", "p52 meta reflection failed")), result
            result["workflow_id"] = workflow_id
            self._persist_p52_meta_reflection_result(
                run_id=run_id,
                section_id=section_id,
                result=result,
                patch_rows=patch_rows,
            )
            self._log_p52_workflow_event(
                workflow_kind,
                "completed",
                workflow_id=workflow_id,
                run_id=run_id,
                section_id=section_id,
                stage=stage,
                error_family=str((result.get("meta_reflection", {}) if isinstance(result.get("meta_reflection", {}), dict) else {}).get("error_family", "") or "").strip(),
            )
            return True, "p52 meta reflection completed", result

        if stage == "ablation":
            result = self.p52_ablation_orchestrator.run(
                run_id=run_id,
                section_id=section_id,
                run_trace=run_trace,
                feedback_record=feedback_record,
                workflow_id=workflow_id,
            )
            if not bool(result.get("success", False)):
                self._log_p52_workflow_event(
                    workflow_kind,
                    "failed",
                    workflow_id=workflow_id,
                    run_id=run_id,
                    section_id=section_id,
                    stage=stage,
                    error_message=str(result.get("message", "p52 ablation failed")),
                )
                return False, str(result.get("message", "p52 ablation failed")), result
            result["workflow_id"] = workflow_id
            self._persist_p52_ablation_result(
                run_id=run_id,
                section_id=section_id,
                result=result,
            )
            self._log_p52_workflow_event(
                workflow_kind,
                "completed",
                workflow_id=workflow_id,
                run_id=run_id,
                section_id=section_id,
                stage=stage,
                variant_count=int((result.get("summary", {}) if isinstance(result.get("summary", {}), dict) else {}).get("variant_count", 0) or 0),
            )
            return True, "p52 ablation completed", result

        self._log_p52_workflow_event(
            workflow_kind,
            "unsupported_stage",
            workflow_id=workflow_id,
            run_id=run_id,
            section_id=section_id,
            stage=stage,
        )
        return False, f"unsupported p52 feedback workflow stage: {stage}", None

    def _persist_p52_feedback_optimize_result(
        self,
        *,
        run_id: str,
        section_id: str,
        result: Dict[str, Any],
    ) -> None:
        feedback_key = str(result.get("feedback_key", "") or f"p52_feedback:{run_id}:{section_id}:{uuid.uuid4().hex[:8]}")
        analysis_result = result.get("analysis_result", {}) if isinstance(result.get("analysis_result", {}), dict) else {}
        error_classification = analysis_result.get("error_classification", {}) if isinstance(analysis_result.get("error_classification", {}), dict) else {}
        feedback_record = analysis_result.get("feedback_record", {}) if isinstance(analysis_result.get("feedback_record", {}), dict) else {}
        candidate_patches = result.get("candidate_patches", []) if isinstance(result.get("candidate_patches", []), list) else []
        issue_feedback_rows = self._normalize_issue_feedback_rows(feedback_record.get("issue_feedback", []))
        missing_item_feedback = feedback_record.get("missing_item_feedback", {}) if isinstance(feedback_record.get("missing_item_feedback", {}), dict) else {}
        payload = {
            "feedback_key": feedback_key,
            "section_id": section_id,
            "analysis_kind": "p52_feedback_optimize",
            "workflow_mode": str(result.get("workflow_mode", "") or "p52_feedback_optimize_v1"),
            "feedback_meta": {
                "chain_mode": "p52_feedback_optimize",
                "feedback_optimize_status": "completed",
                "candidate_register_status": "candidate_generated" if candidate_patches else "skipped",
                "replay_status": "",
                "candidate_patch_count": len(candidate_patches),
                "p52_error_family": str(error_classification.get("primary_error_type", "") or "").strip(),
                "feedback_type": str(feedback_record.get("feedback_type", "") or "").strip(),
                "decision": str(feedback_record.get("decision", "") or "").strip(),
                "labels": feedback_record.get("labels", []) if isinstance(feedback_record.get("labels", []), list) else [],
                "retrieval_feedback": str(feedback_record.get("retrieval_feedback", "") or "").strip(),
                "conclusion_feedback": str(feedback_record.get("conclusion_feedback", "") or "").strip(),
                "suggestion": str(feedback_record.get("suggestion", "") or "").strip(),
                "issue_feedback": issue_feedback_rows,
                "missing_item_feedback": missing_item_feedback,
            },
            "analysis_result": analysis_result,
            "patch_result": {
                "patches": candidate_patches,
            },
            "candidate_patches": candidate_patches,
            "verification_plan": result.get("verification_plan", {}) if isinstance(result.get("verification_plan", {}), dict) else {},
            "feedback_projection": {
                "issue_family_label": self._normalize_export_text(str(error_classification.get("primary_error_type", "") or ""), limit=80),
                "patch_count": len(candidate_patches),
                "target_agent_labels": [
                    str(item.get("target_agent", "") or "").strip()
                    for item in candidate_patches
                    if isinstance(item, dict) and str(item.get("target_agent", "") or "").strip()
                ],
                "patch_types": [
                    str(item.get("patch_type", "") or "").strip()
                    for item in candidate_patches
                    if isinstance(item, dict) and str(item.get("patch_type", "") or "").strip()
                ],
            },
        }
        self._persist_feedback_analysis_payload(run_id=run_id, section_id=section_id, payload=payload, feedback_key=feedback_key)

    def _persist_feedback_analysis_payload(
        self,
        *,
        run_id: str,
        section_id: str,
        payload: Dict[str, Any],
        feedback_key: str = "",
    ) -> None:
        session = self.db_conn.get_session()
        try:
            final_feedback_key = str(feedback_key or payload.get("feedback_key", "") or f"feedback_pipeline:{run_id}:{section_id or 'global'}:{uuid.uuid4().hex[:8]}")
            final_payload = dict(payload if isinstance(payload, dict) else {})
            final_payload["feedback_key"] = final_feedback_key
            analysis_json = self._serialize_feedback_analysis_payload(final_payload)
            session.add(
                PreReviewFeedbackAnalysisResult(
                    feedback_key=final_feedback_key,
                    run_id=run_id,
                    section_id=section_id or None,
                    analysis_json=analysis_json,
                    create_time=self._now(),
                )
            )
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _serialize_feedback_analysis_payload(
        self,
        payload: Dict[str, Any],
        max_bytes: int = FEEDBACK_ANALYSIS_JSON_MAX_BYTES,
    ) -> str:
        serialized = json.dumps(payload if isinstance(payload, dict) else {}, ensure_ascii=False, separators=(",", ":"))
        if len(serialized.encode("utf-8")) <= max(int(max_bytes or 0), 1024):
            return serialized
        compact_payload = self._build_compact_feedback_analysis_payload(payload, serialized)
        compact_serialized = json.dumps(compact_payload, ensure_ascii=False, separators=(",", ":"))
        if len(compact_serialized.encode("utf-8")) <= max(int(max_bytes or 0), 1024):
            return compact_serialized
        # 兜底：即使 compact 仍超长，也保证数据库可落库
        return json.dumps(
            {
                "feedback_key": str(payload.get("feedback_key", "") or "").strip(),
                "section_id": str(payload.get("section_id", "") or "").strip(),
                "analysis_kind": str(payload.get("analysis_kind", "") or "").strip() or "feedback_optimize",
                "analysis_compacted": True,
                "compact_reason": "analysis_json_payload_too_large",
                "storage_notice": "payload is trimmed to fit database column limit",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def _build_compact_feedback_analysis_payload(
        self,
        payload: Dict[str, Any],
        serialized_payload: str,
    ) -> Dict[str, Any]:
        final_payload = dict(payload if isinstance(payload, dict) else {})
        feedback_meta = final_payload.get("feedback_meta", {}) if isinstance(final_payload.get("feedback_meta", {}), dict) else {}
        analysis_result = final_payload.get("analysis_result", {}) if isinstance(final_payload.get("analysis_result", {}), dict) else {}
        error_classification = analysis_result.get("error_classification", {}) if isinstance(analysis_result.get("error_classification", {}), dict) else {}
        candidate_patches = final_payload.get("candidate_patches", []) if isinstance(final_payload.get("candidate_patches", []), list) else []
        patch_result = final_payload.get("patch_result", {}) if isinstance(final_payload.get("patch_result", {}), dict) else {}
        patch_rows = patch_result.get("patches", []) if isinstance(patch_result.get("patches", []), list) else []
        verification_result = final_payload.get("verification_result", {}) if isinstance(final_payload.get("verification_result", {}), dict) else {}
        optimize_eval = final_payload.get("optimize_evaluation", {}) if isinstance(final_payload.get("optimize_evaluation", {}), dict) else {}
        compact = {
            "feedback_key": str(final_payload.get("feedback_key", "") or "").strip(),
            "section_id": str(final_payload.get("section_id", "") or "").strip(),
            "analysis_kind": str(final_payload.get("analysis_kind", "") or "").strip() or "feedback_optimize",
            "workflow_mode": str(final_payload.get("workflow_mode", "") or "").strip(),
            "primary_error_type": str(
                final_payload.get("primary_error_type", "")
                or feedback_meta.get("p52_error_family", "")
                or error_classification.get("primary_error_type", "")
                or ""
            ).strip(),
            "feedback_meta": {
                "chain_mode": str(feedback_meta.get("chain_mode", "") or "").strip(),
                "feedback_optimize_status": str(feedback_meta.get("feedback_optimize_status", "") or "").strip(),
                "candidate_register_status": str(feedback_meta.get("candidate_register_status", "") or "").strip(),
                "replay_status": str(feedback_meta.get("replay_status", "") or "").strip(),
                "feedback_type": str(feedback_meta.get("feedback_type", "") or "").strip(),
                "decision": str(feedback_meta.get("decision", "") or "").strip(),
                "labels": feedback_meta.get("labels", []) if isinstance(feedback_meta.get("labels", []), list) else [],
                "retrieval_feedback": str(feedback_meta.get("retrieval_feedback", "") or "").strip(),
                "conclusion_feedback": str(feedback_meta.get("conclusion_feedback", "") or "").strip(),
                "suggestion": self._compact_text(feedback_meta.get("suggestion", ""), max_len=240),
                "issue_feedback": (feedback_meta.get("issue_feedback", []) if isinstance(feedback_meta.get("issue_feedback", []), list) else [])[:20],
                "missing_item_feedback": feedback_meta.get("missing_item_feedback", {})
                if isinstance(feedback_meta.get("missing_item_feedback", {}), dict)
                else {},
                "optimize_evaluation": optimize_eval,
            },
            "feedback_projection": final_payload.get("feedback_projection", {})
            if isinstance(final_payload.get("feedback_projection", {}), dict)
            else {},
            "verification_result": {
                "overall_verdict": str(verification_result.get("overall_verdict", "") or "").strip(),
                "summary": verification_result.get("summary", {}) if isinstance(verification_result.get("summary", {}), dict) else {},
            },
            "analysis_compacted": True,
            "compact_reason": "analysis_json_payload_too_large",
            "original_bytes": len(serialized_payload.encode("utf-8")),
            "compact_bytes_estimate": 0,
            "candidate_patch_count": len(candidate_patches),
            "patch_result_count": len(patch_rows),
        }
        compact["compact_bytes_estimate"] = len(json.dumps(compact, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        return compact

    def replay_feedback_optimize(
        self,
        run_id: str,
        section_id: str,
        feedback_payload: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        if self._is_p52_section_id(section_id):
            return False, "3.2.P.5.2 sections must use the dedicated P52 feedback optimize workflow", None
        ok, msg, bundle = self._build_feedback_replay_bundle(run_id=run_id, section_id=section_id, feedback_payload=feedback_payload)
        if not ok or not isinstance(bundle, dict):
            return False, msg, None
        result = self.feedback_optimize_orchestrator.replay_feedback_optimize(
            feedback_record=bundle.get("feedback_record", {}) if isinstance(bundle.get("feedback_record", {}), dict) else {},
            run_trace=bundle.get("run_trace", {}) if isinstance(bundle.get("run_trace", {}), dict) else {},
            run_context=bundle.get("run_context", {}) if isinstance(bundle.get("run_context", {}), dict) else {},
        )
        if not bool(result.get("success", False)):
            return False, str(result.get("error_message", "feedback optimize replay failed")), result
        return True, "feedback optimize replay completed", result

    def optimize_p52_feedback(
        self,
        run_id: str,
        section_id: str,
        feedback_payload: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        try:
            return self._run_p52_feedback_workflow_stage(
                stage="optimize",
                run_id=run_id,
                section_id=section_id,
                feedback_payload=feedback_payload,
            )
        except (LLMContextError, LLMExecutionError) as exc:
            return False, str(exc), {
                'success': False, 'execution_status': 'failed',
                'feedback_optimize_status': 'failed', 'candidate_register_status': 'skipped',
                'error': exc.as_dict(), 'candidate_patches': [],
            }

    def list_p52_feedback_patches(
        self,
        run_id: str,
        section_id: str,
        status: str = "",
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        if not self._is_p52_section_id(section_id):
            return False, "section_id is not under 3.2.P.5.2", None
        patches = self.p52_feedback_optimize_orchestrator.list_patches(
            run_id=run_id,
            section_id=section_id,
            status=status,
        )
        return True, "success", {
            "run_id": run_id,
            "section_id": section_id,
            "status": status,
            "items": patches,
            "total": len(patches),
        }

    def replay_verify_p52_feedback(
        self,
        run_id: str,
        section_id: str,
        feedback_payload: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        return self._run_p52_feedback_workflow_stage(
            stage="verify",
            run_id=run_id,
            section_id=section_id,
            feedback_payload=feedback_payload,
        )

    def approve_p52_feedback_patch(
        self,
        run_id: str,
        section_id: str,
        patch_id: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        if not self._is_p52_section_id(section_id):
            return False, "section_id is not under 3.2.P.5.2", None
        updated = self.p52_feedback_optimize_orchestrator.approve_patch(
            run_id=run_id,
            section_id=section_id,
            patch_id=patch_id,
            operator=str((payload or {}).get("operator", "") or "").strip(),
            comment=str((payload or {}).get("comment", "") or "").strip(),
        )
        if not isinstance(updated, dict):
            return False, "patch not found", None
        self._invalidate_p52_feedback_bundle_cache(run_id=run_id, section_id=section_id)
        staged_rule_patch = None
        if str(updated.get("patch_type", "") or "").strip() == "rule_patch":
            staged_rule_patch = self.p52_rule_patch_persist_service.stage_approved_rule_patch(
                run_id=run_id,
                section_id=section_id,
                patch_row=updated,
            )
            applied_rule_patch = self.p52_rule_patch_persist_service.apply_approved_rule_patch(
                run_id=run_id,
                section_id=section_id,
                patch_row=updated,
            )
            if isinstance(applied_rule_patch, dict):
                updated["rule_apply_result"] = applied_rule_patch
        overlay = self.p52_patch_apply_service.build_runtime_overlay(
            run_id=run_id,
            section_id=section_id,
            include_approved=True,
            include_candidate=False,
        )
        self._log_p52_workflow_event(
            "p52_feedback_optimize",
            "patch_applied",
            workflow_id="",
            run_id=run_id,
            section_id=section_id,
            patch_id=patch_id,
            applied_patch_count=len(overlay.get("source_patch_ids", []) if isinstance(overlay.get("source_patch_ids", []), list) else []),
        )
        updated["runtime_overlay"] = self.p52_patch_apply_service.describe_runtime_overlay(overlay)
        if isinstance(staged_rule_patch, dict):
            updated["candidate_rule_overlay"] = staged_rule_patch
        return True, "p52 feedback patch approved", updated

    def reject_p52_feedback_patch(
        self,
        run_id: str,
        section_id: str,
        patch_id: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        if not self._is_p52_section_id(section_id):
            return False, "section_id is not under 3.2.P.5.2", None
        updated = self.p52_feedback_optimize_orchestrator.reject_patch(
            run_id=run_id,
            section_id=section_id,
            patch_id=patch_id,
            operator=str((payload or {}).get("operator", "") or "").strip(),
            comment=str((payload or {}).get("comment", "") or "").strip(),
        )
        if not isinstance(updated, dict):
            return False, "patch not found", None
        self._invalidate_p52_feedback_bundle_cache(run_id=run_id, section_id=section_id)
        return True, "p52 feedback patch rejected", updated

    def replay_p52_meta_reflection(
        self,
        run_id: str,
        section_id: str,
        feedback_payload: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        return self._run_p52_feedback_workflow_stage(
            stage="meta_reflection",
            run_id=run_id,
            section_id=section_id,
            feedback_payload=feedback_payload,
        )

    def run_p52_ablation_study(
        self,
        run_id: str,
        section_id: str,
        feedback_payload: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        return self._run_p52_feedback_workflow_stage(
            stage="ablation",
            run_id=run_id,
            section_id=section_id,
            feedback_payload=feedback_payload,
        )

    def replay_meta_reflection(
        self,
        run_id: str,
        section_id: str,
        feedback_payload: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        if self._is_p52_section_id(section_id):
            return False, "3.2.P.5.2 sections must use the dedicated P52 meta reflection workflow", None
        ok, msg, bundle = self._build_feedback_replay_bundle(run_id=run_id, section_id=section_id, feedback_payload=feedback_payload)
        if not ok or not isinstance(bundle, dict):
            return False, msg, None
        result = self.feedback_optimize_orchestrator.replay_meta_reflection(
            feedback_record=bundle.get("feedback_record", {}) if isinstance(bundle.get("feedback_record", {}), dict) else {},
            run_trace=bundle.get("run_trace", {}) if isinstance(bundle.get("run_trace", {}), dict) else {},
            run_context=bundle.get("run_context", {}) if isinstance(bundle.get("run_context", {}), dict) else {},
        )
        if not bool(result.get("success", False)):
            return False, str(result.get("error_message", "meta reflection replay failed")), result
        return True, "meta reflection replay completed", result

    def save_section_reference_example(
        self,
        project_id: str,
        run_id: str,
        section_id: str,
        doc_id: str,
        title: str,
        content: str,
        expected_output: Optional[Dict[str, Any]] = None,
        source_feedback_key: str = "",
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        project_key = str(project_id or "").strip()
        section_key = str(section_id or "").strip()
        if not project_key or not section_key:
            return False, "project_id and section_id are required", None
        content_text = str(content or "").strip()
        if not content_text:
            return False, "reference example content is required", None
        session = self.db_conn.get_session()
        try:
            example = self._upsert_section_example(
                session=session,
                project_id=project_key,
                run_id=run_id,
                section_id=section_key,
                doc_id=doc_id,
                example_type="reference",
                title=title,
                content=content_text,
                input_payload={
                    "section_id": section_key,
                    "doc_id": str(doc_id or "").strip(),
                },
                output_payload=expected_output if isinstance(expected_output, dict) else {},
                source_feedback_key=source_feedback_key,
                payload={
                    "origin": "manual_feedback_reference",
                    "source_feedback_key": source_feedback_key,
                },
            )
            session.commit()
            return True, "success", example
        except Exception as exc:
            session.rollback()
            return False, f"save section reference example failed: {str(exc)}", None
        finally:
            session.close()

    def evaluate_feedback_optimize(
        self,
        run_id: str,
        feedback_key: str,
        section_id: str,
        evaluation_payload: Dict[str, Any],
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        run_key = str(run_id or "").strip()
        feedback_key = str(feedback_key or "").strip()
        section_key = str(section_id or "").strip()
        if not run_key or not feedback_key:
            return False, "run_id and feedback_key are required", None
        session = self.db_conn.get_session()
        try:
            row = (
                session.query(PreReviewFeedbackAnalysisResult)
                .filter(
                    PreReviewFeedbackAnalysisResult.run_id == run_key,
                    PreReviewFeedbackAnalysisResult.feedback_key == feedback_key,
                )
                .order_by(PreReviewFeedbackAnalysisResult.id.desc())
                .first()
            )
            if row is None:
                return False, "feedback analysis result not found", None
            try:
                payload = json.loads(row.analysis_json or "{}")
            except Exception:
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            optimize_evaluation = {
                "overall_verdict": str(evaluation_payload.get("overall_verdict", "") or "").strip(),
                "patch_acceptance": list(evaluation_payload.get("patch_acceptance", []) or []),
                "comment": str(evaluation_payload.get("comment", "") or "").strip(),
                "score": evaluation_payload.get("score", None),
                "update_time": self._now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            payload["optimize_evaluation"] = optimize_evaluation
            feedback_meta = payload.get("feedback_meta", {}) if isinstance(payload.get("feedback_meta", {}), dict) else {}
            feedback_meta["optimize_evaluation"] = optimize_evaluation
            payload["feedback_meta"] = feedback_meta
            row.analysis_json = self._serialize_feedback_analysis_payload(payload)
            if section_key:
                patch_rows = (
                    session.query(PreReviewPatchRegistry)
                    .filter(
                        PreReviewPatchRegistry.run_id == run_key,
                        PreReviewPatchRegistry.section_id == section_key,
                        PreReviewPatchRegistry.source_feedback_key == feedback_key,
                    )
                    .all()
                )
                acceptance_map = {
                    str(item.get("patch_id", "") or "").strip(): str(item.get("verdict", "") or "").strip()
                    for item in optimize_evaluation["patch_acceptance"]
                    if isinstance(item, dict) and str(item.get("patch_id", "") or "").strip()
                }
                for patch_row in patch_rows:
                    verdict = acceptance_map.get(str(getattr(patch_row, "patch_id", "") or "").strip(), "")
                    if verdict in {"accepted", "rejected", "partial"}:
                        patch_row.status = verdict
                        patch_row.update_time = self._now()
            session.commit()
            return True, "success", optimize_evaluation
        except Exception as exc:
            session.rollback()
            return False, f"evaluate feedback optimize failed: {str(exc)}", None
        finally:
            session.close()

    def get_dashboard_summary(self) -> Dict[str, Any]:
        session = self.db_conn.get_session()
        try:
            project_total = session.query(PreReviewProject).filter(PreReviewProject.is_deleted == 0).count()
            running_projects = (
                session.query(PreReviewProject)
                .filter(and_(PreReviewProject.is_deleted == 0, PreReviewProject.status == "running"))
                .count()
            )
            completed_projects = (
                session.query(PreReviewProject)
                .filter(and_(PreReviewProject.is_deleted == 0, PreReviewProject.status == "completed"))
                .count()
            )
            run_total = session.query(PreReviewRun).count()
            feedback_total = session.query(PreReviewFeedback).count()
            avg_acc = session.query(func.avg(PreReviewRun.accuracy)).scalar()

            recent_runs = (
                session.query(PreReviewRun)
                .order_by(desc(PreReviewRun.id))
                .limit(5)
                .all()
            )
            return {
                "project_total": project_total,
                "running_projects": running_projects,
                "completed_projects": completed_projects,
                "run_total": run_total,
                "feedback_total": feedback_total,
                "avg_accuracy": round(float(avg_acc), 4) if avg_acc is not None else None,
                "recent_runs": [
                    {
                        "run_id": r.run_id,
                        "project_id": r.project_id,
                        "source_doc_id": r.source_doc_id,
                        "accuracy": r.accuracy,
                        "create_time": r.create_time.strftime("%Y-%m-%d %H:%M:%S"),
                    }
                    for r in recent_runs
                ],
            }
        finally:
            session.close()


    @staticmethod
    def _run_has_incomplete_review(run) -> bool:
        try:
            summary = json.loads(getattr(run, "summary", None) or "{}")
        except (TypeError, ValueError):
            return True
        if not isinstance(summary, dict):
            return True
        return summary.get("review_complete") is False or summary.get("execution_status") in (
            "failed", "running", "pending", "cancelled", "interrupted",
        )

    def export_report_word(self, run_id: str) -> Tuple[bool, str, Optional[str]]:
        session = self.db_conn.get_session()
        try:
            run = session.query(PreReviewRun).filter(PreReviewRun.run_id == run_id).first()
            if run is None:
                return False, "run not found", None
            if not session.query(PreReviewProject).filter(
                PreReviewProject.project_id == run.project_id,
                PreReviewProject.is_deleted == 0,
            ).first():
                return False, "project not found", None

            if self._run_has_incomplete_review(run):
                return False, "本次审评未完成，不能导出完整审评报告", None

            sections = (
                session.query(PreReviewSectionConclusion)
                .filter(PreReviewSectionConclusion.run_id == run_id)
                .order_by(PreReviewSectionConclusion.id.asc())
                .all()
            )
            if not sections:
                return False, "no conclusions found", None

            doc = Document()
            doc.add_heading(f"Pre-review Report - {run.run_id}", 0)
            doc.add_paragraph(f"Project ID: {run.project_id}")
            doc.add_paragraph(f"Version: v{run.version_no}")
            doc.add_paragraph(f"Strategy: {run.strategy}")
            doc.add_paragraph(f"Generated At: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

            for sec in sections:
                doc.add_heading(f"{sec.section_name} ({sec.section_id})", level=2)
                doc.add_paragraph(f"Risk Level: {sec.risk_level}")
                doc.add_paragraph(f"Conclusion: {sec.conclusion}")
                issues = PreReviewRepository.load_findings(sec.highlighted_issues)
                rules = PreReviewRepository.load_string_list(sec.linked_rules)
                issue_titles = self._findings_to_titles(issues)
                doc.add_paragraph(f"Highlighted Issues: {', '.join(issue_titles) if issue_titles else 'None'}")
                doc.add_paragraph(f"Linked Rules: {', '.join(rules) if rules else 'None'}")

            output_path = os.path.join(REPORT_DIR, f"{run_id}.docx")
            doc.save(output_path)
            return True, "report exported", output_path
        except Exception as e:
            return False, f"export report failed: {str(e)}", None
        finally:
            session.close()

    @staticmethod
    def _markdown_heading(level: int, title: str) -> str:
        normalized_level = max(1, min(int(level or 1), 6))
        return f"{'#' * normalized_level} {str(title or '').strip()}".rstrip()

    @staticmethod
    def _markdown_bullet(label: str, value: Any) -> str:
        text = str(value or "").strip()
        return f"- **{label}**：{text or '-'}"

    @staticmethod
    def _normalize_pre_review_conclusion(value: Any) -> str:
        raw = str(value or "").strip().lower()
        mapping = {
            "supported": "满足",
            "unsupported": "不满足",
            "insufficient_information": "信息不足",
            "issue": "存在问题",
            "question": "待补充",
        }
        return mapping.get(raw, str(value or "").strip() or "-")

    @staticmethod
    def _sanitize_docx_file_name(value: str) -> str:
        text = str(value or "").strip()
        if not text:
            return "审评结论"
        text = re.sub(r'[\\/:*?"<>|]+', "_", text)
        return text.strip(" .") or "审评结论"

    @staticmethod
    def _normalize_export_text(value: Any, limit: int = 240) -> str:
        text = re.sub(r"\s+", " ", str(value or "").strip())
        if not text:
            return "-"
        if len(text) <= limit:
            return text
        return f"{text[:limit].rstrip()}..."

    @staticmethod
    def _split_export_sources(value: Any) -> List[str]:
        text = str(value or "").strip()
        if not text:
            return []
        parts = [segment.strip() for segment in re.split(r"[；;]", text) if str(segment or "").strip()]
        return list(dict.fromkeys(parts))

    @staticmethod
    def _normalize_export_source_key(value: Any) -> str:
        text = str(value or "").strip().lower()
        if not text:
            return ""
        text = re.sub(r"\.(pdf|docx|doc|md|json|txt)$", "", text)
        text = re.sub(r"[\s\-_:/\\()\[\]{}，。、“”‘’；;：:]+", "", text)
        return text

    @staticmethod
    def _set_docx_run_font(run, *, bold: bool = False) -> None:
        if run is None:
            return
        run.bold = bold
        run.font.name = "Microsoft YaHei"
        try:
            run._element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")
        except Exception:
            pass

    def _apply_docx_export_styles(self, doc: Document) -> None:
        for style_name in ["Normal", "Title", "Heading 1", "Heading 2", "Heading 3", "Heading 4", "Heading 5", "Heading 6", "List Number"]:
            try:
                style = doc.styles[style_name]
            except Exception:
                continue
            style.font.name = "Microsoft YaHei"
            try:
                style._element.rPr.rFonts.set(qn("w:eastAsia"), "微软雅黑")
            except Exception:
                pass

    def _add_docx_label_value_paragraph(self, doc: Document, label: str, value: Any) -> None:
        paragraph = doc.add_paragraph()
        label_run = paragraph.add_run(f"{str(label or '').strip()}：")
        self._set_docx_run_font(label_run, bold=True)
        value_run = paragraph.add_run(str(value or "-").strip() or "-")
        self._set_docx_run_font(value_run, bold=False)

    def _build_evidence_snippets_from_materials(self, materials: Any, limit: int = 3) -> List[Dict[str, str]]:
        if not isinstance(materials, list):
            return []
        snippets: List[Dict[str, str]] = []
        seen_labels: set[str] = set()
        for material in materials:
            if not isinstance(material, dict):
                continue
            label = str(
                material.get("display_name", "")
                or material.get("file_name_zh", "")
                or material.get("file_name", "")
                or material.get("title", "")
                or material.get("evidence_id", "")
                or ""
            ).strip()
            snippet = str(
                material.get("context_snippet", "")
                or material.get("snippet", "")
                or material.get("content", "")
                or material.get("text", "")
                or ""
            ).strip()
            if not label or not snippet or label in seen_labels:
                continue
            seen_labels.add(label)
            snippets.append(
                {
                    "source": label,
                    "snippet": self._normalize_export_text(snippet, limit=180),
                }
            )
            if len(snippets) >= max(1, int(limit or 1)):
                break
        return snippets

    def _match_evidence_excerpts(self, evidence_source: Any, evidence_snippets: List[Dict[str, str]]) -> List[str]:
        if not isinstance(evidence_snippets, list):
            evidence_snippets = []
        evidence_snippet_map = {
            str(item.get("source", "") or "").strip(): self._normalize_export_text(item.get("snippet", ""), limit=180)
            for item in evidence_snippets
            if isinstance(item, dict) and str(item.get("source", "") or "").strip()
        }
        normalized_snippet_map = {
            self._normalize_export_source_key(source): snippet
            for source, snippet in evidence_snippet_map.items()
            if self._normalize_export_source_key(source)
        }
        matched_snippets: List[str] = []
        for source in self._split_export_sources(evidence_source):
            snippet = evidence_snippet_map.get(source, "")
            if not snippet or snippet == "-":
                normalized_source = self._normalize_export_source_key(source)
                snippet = normalized_snippet_map.get(normalized_source, "")
                if (not snippet or snippet == "-") and normalized_source:
                    for normalized_label, candidate in normalized_snippet_map.items():
                        if not normalized_label:
                            continue
                        if normalized_source in normalized_label or normalized_label in normalized_source:
                            snippet = candidate
                            break
            if snippet and snippet != "-" and snippet not in matched_snippets:
                matched_snippets.append(snippet)
        if not matched_snippets:
            for snippet_item in evidence_snippets[:2]:
                fallback_snippet = str(snippet_item.get("snippet", "") or "").strip()
                if fallback_snippet and fallback_snippet != "-":
                    normalized_fallback = self._normalize_export_text(fallback_snippet, limit=180)
                    if normalized_fallback not in matched_snippets:
                        matched_snippets.append(normalized_fallback)
        return matched_snippets

    def _cache_review_result_evidence_payload(
        self,
        review_result: Dict[str, Any],
        approved_materials: Any,
    ) -> Dict[str, Any]:
        data = dict(review_result or {}) if isinstance(review_result, dict) else {}
        evidence_snippets = self._build_evidence_snippets_from_materials(approved_materials, limit=3)
        reasoning_rows = data.get("reasoning_chain_items", []) if isinstance(data.get("reasoning_chain_items", []), list) else []
        cached_reasoning_rows: List[Dict[str, Any]] = []
        for row in reasoning_rows:
            if not isinstance(row, dict):
                continue
            row_copy = dict(row)
            if not str(row_copy.get("evidence_excerpt", "") or "").strip():
                matched_snippets = self._match_evidence_excerpts(row_copy.get("evidence_support", ""), evidence_snippets)
                if matched_snippets:
                    row_copy["evidence_excerpt"] = "；".join(matched_snippets)
            cached_reasoning_rows.append(row_copy)
        data["reasoning_chain_items"] = cached_reasoning_rows
        data["export_evidence_snippets"] = evidence_snippets
        return data

    def _prune_export_chapter_structure(
        self,
        nodes: List[Dict[str, Any]],
        visible_section_ids: set[str],
    ) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for node in nodes or []:
            if not isinstance(node, dict):
                continue
            section_id = str(node.get("section_id", "") or "").strip()
            children = self._prune_export_chapter_structure(
                [child for child in (node.get("children_sections") or []) if isinstance(child, dict)],
                visible_section_ids,
            )
            if section_id not in visible_section_ids and not children:
                continue
            cloned = dict(node)
            cloned["children_sections"] = children
            out.append(cloned)
        return out

    def _build_export_trace_map_light(self, session, run_id: str) -> Dict[str, Dict[str, Any]]:
        rows = (
            session.query(PreReviewSectionTrace)
            .filter(PreReviewSectionTrace.run_id == run_id)
            .order_by(PreReviewSectionTrace.id.asc())
            .all()
        )
        out: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            section_id = str(getattr(row, "section_id", "") or "").strip()
            if not section_id:
                continue
            raw_trace = PreReviewRepository.load_trace_payload(getattr(row, "trace_json", "") or "")
            if not isinstance(raw_trace, dict):
                continue
            retrieved_materials = raw_trace.get("retrieved_materials", []) if isinstance(raw_trace.get("retrieved_materials", []), list) else []
            if not retrieved_materials:
                continue
            needs_enrich = any(
                not str(
                    material.get("display_name", "")
                    or material.get("file_name_zh", "")
                    or material.get("file_name", "")
                    or material.get("title", "")
                    or ""
                ).strip()
                for material in retrieved_materials
                if isinstance(material, dict)
            )
            normalized_materials = self._enrich_retrieved_materials(session, retrieved_materials) if needs_enrich else [
                dict(item) for item in retrieved_materials if isinstance(item, dict)
            ]
            out[section_id] = {
                "retrieved_materials": normalized_materials,
            }
        return out

    def _build_review_conclusion_export_context(
        self,
        session,
        run_id: str,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        run = session.query(PreReviewRun).filter(PreReviewRun.run_id == run_id).first()
        if run is None:
            return False, "run not found", None
        if self._run_has_incomplete_review(run):
            return False, "本次审评未完成，不能导出完整审评报告", None
        project = (
            session.query(PreReviewProject)
            .filter(PreReviewProject.project_id == run.project_id, PreReviewProject.is_deleted == 0)
            .first()
        )
        if project is None:
            return False, "project not found", None
        project_name = str(getattr(project, "project_name", "") or "").strip()
        catalog = self._merge_catalog_with_manual_concerns(
            self._load_project_section_catalog(session, run.project_id),
            self._load_manual_concern_map(session, run.project_id),
        )
        all_sections = catalog.get("all_sections", []) if isinstance(catalog.get("all_sections", []), list) else []
        section_meta_map = {
            str(item.get("section_id", "") or "").strip(): item
            for item in all_sections
            if isinstance(item, dict) and str(item.get("section_id", "") or "").strip()
        }
        focus_points_by_section = {
            sid: self._normalize_text_list(item.get("concern_points", []))
            for sid, item in section_meta_map.items()
        }
        section_rules_by_section = {
            sid: self._normalize_text_list(item.get("section_rules", []))
            for sid, item in section_meta_map.items()
        }
        conclusion_rows = (
            session.query(PreReviewSectionConclusion)
            .filter(PreReviewSectionConclusion.run_id == run_id)
            .order_by(PreReviewSectionConclusion.id.asc())
            .all()
        )
        output_rows = (
            session.query(PreReviewSectionOutput)
            .filter(PreReviewSectionOutput.run_id == run_id)
            .order_by(PreReviewSectionOutput.id.asc())
            .all()
        )
        if not conclusion_rows and not output_rows:
            return False, "no review conclusions found", None
        conclusion_by_section: Dict[str, Dict[str, Any]] = {}
        for row in conclusion_rows:
            sid = str(getattr(row, "section_id", "") or "").strip()
            if not sid or sid in conclusion_by_section:
                continue
            conclusion_by_section[sid] = {
                "section_id": sid,
                "section_name": str(getattr(row, "section_name", "") or "").strip(),
                "conclusion": str(getattr(row, "conclusion", "") or "").strip(),
                "risk_level": str(getattr(row, "risk_level", "") or "").strip(),
                "linked_rules": PreReviewRepository.load_string_list(getattr(row, "linked_rules", "") or ""),
            }
        output_payload_by_section: Dict[str, Dict[str, Any]] = {}
        for row in output_rows:
            sid = str(getattr(row, "section_id", "") or "").strip()
            if not sid or sid in output_payload_by_section:
                continue
            try:
                payload = json.loads(getattr(row, "output_json", "") or "{}")
            except Exception:
                payload = {}
            output_payload_by_section[sid] = payload if isinstance(payload, dict) else {}
        reviewed_section_ids = list(dict.fromkeys([
            *[sid for sid in conclusion_by_section.keys() if sid],
            *[sid for sid in output_payload_by_section.keys() if sid],
        ]))
        if not reviewed_section_ids:
            return False, "no reviewed sections found", None
        section_output_by_section: Dict[str, Dict[str, Any]] = {}
        for sid in reviewed_section_ids:
            meta = section_meta_map.get(sid, {})
            conclusion = conclusion_by_section.get(sid, {})
            section_output_by_section[sid] = self._normalize_section_review_output(
                output_payload_by_section.get(sid, {}),
                section_id=sid,
                section_name=str(conclusion.get("section_name", "") or meta.get("section_name", "") or sid).strip(),
                fallback_conclusion=str(conclusion.get("conclusion", "") or "").strip(),
                fallback_risk_level=str(conclusion.get("risk_level", "") or "").strip(),
                fallback_linked_rules=conclusion.get("linked_rules", []) if isinstance(conclusion.get("linked_rules", []), list) else [],
                fallback_focus_points=focus_points_by_section.get(sid, []),
                fallback_section_rules=section_rules_by_section.get(sid, []),
            )
        visible_section_ids = set(reviewed_section_ids)
        chapter_structure = self._prune_export_chapter_structure(
            deepcopy(catalog.get("chapter_structure", [])),
            visible_section_ids,
        )
        existing_ids = {
            str(node.get("section_id", "") or "").strip()
            for node in self.ctd_sections.flatten_nodes(chapter_structure, leaf_only=False)
            if isinstance(node, dict) and str(node.get("section_id", "") or "").strip()
        }
        for sid in reviewed_section_ids:
            if sid in existing_ids:
                continue
            meta = section_meta_map.get(sid, {})
            chapter_structure.append(
                {
                    "section_id": sid,
                    "section_name": str(meta.get("section_name", "") or (conclusion_by_section.get(sid, {}) or {}).get("section_name", "") or sid).strip(),
                    "children_sections": [],
                    "node_level": int(meta.get("node_level", 0) or 0),
                }
            )
        return True, "success", {
            "run_id": str(run.run_id or "").strip(),
            "source_doc_id": str(run.source_doc_id or "").strip(),
            "project_name": project_name,
            "finish_time": getattr(run, "finish_time", None),
            "chapter_structure": chapter_structure,
            "section_output_by_section_id": section_output_by_section,
            "trace_by_section": self._build_export_trace_map_light(session, str(run.run_id or "").strip()),
        }

    def _build_review_conclusion_export_rows(
        self,
        overview: Dict[str, Any],
        trace_by_section: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        chapter_structure = overview.get("chapter_structure", []) if isinstance(overview.get("chapter_structure", []), list) else []
        section_outputs = overview.get("section_output_by_section_id", {}) if isinstance(overview.get("section_output_by_section_id", {}), dict) else {}
        trace_map = trace_by_section if isinstance(trace_by_section, dict) else {}
        rows: List[Dict[str, Any]] = []
        seen_section_ids: set[str] = set()

        def _extract_evidence_snippets(section_id: str, output: Dict[str, Any]) -> List[Dict[str, str]]:
            cached_snippets = output.get("export_evidence_snippets", []) if isinstance(output.get("export_evidence_snippets", []), list) else []
            normalized_cached_snippets = [
                {
                    "source": str(item.get("source", "") or "").strip(),
                    "snippet": self._normalize_export_text(item.get("snippet", ""), limit=180),
                }
                for item in cached_snippets
                if isinstance(item, dict) and str(item.get("source", "") or "").strip() and str(item.get("snippet", "") or "").strip()
            ][:3]
            if normalized_cached_snippets:
                return normalized_cached_snippets
            trace_item = trace_map.get(section_id, {}) if isinstance(trace_map.get(section_id, {}), dict) else {}
            materials = trace_item.get("retrieved_materials", []) if isinstance(trace_item.get("retrieved_materials", []), list) else []
            snippets = self._build_evidence_snippets_from_materials(materials, limit=3)
            if snippets:
                return snippets
            fallback_refs = output.get("evidence_refs", []) if isinstance(output.get("evidence_refs", []), list) else []
            fallback = []
            for ref in fallback_refs[:3]:
                text = self._normalize_export_text(ref, limit=120)
                if text and text != "-":
                    fallback.append({"source": text, "snippet": "-"})
            return fallback

        def render_node(node: Dict[str, Any], depth: int) -> None:
            if not isinstance(node, dict):
                return
            section_id = str(node.get("section_id", "") or "").strip()
            section_name = str(node.get("section_name", "") or node.get("title", "") or section_id).strip() or section_id
            if not section_id or section_id in seen_section_ids:
                return
            seen_section_ids.add(section_id)
            output = section_outputs.get(section_id, {}) if isinstance(section_outputs.get(section_id, {}), dict) else {}
            reasoning_items = output.get("reasoning_chain_items", []) if isinstance(output.get("reasoning_chain_items", []), list) else []
            reasoning_map = {
                str(item.get("task_code", "") or "").strip(): item
                for item in reasoning_items
                if isinstance(item, dict) and str(item.get("task_code", "") or "").strip()
            }
            evidence_snippets = _extract_evidence_snippets(section_id, output)
            judgment_items: List[Dict[str, str]] = []
            for index, item in enumerate(output.get("task_verdicts", []) if isinstance(output.get("task_verdicts", []), list) else [], start=1):
                if not isinstance(item, dict):
                    continue
                task_code = str(item.get("task_code", "") or "").strip()
                reasoning = reasoning_map.get(task_code, {})
                title = str(item.get("task_question", "") or item.get("problem", "") or f"判断项 {index}").strip()
                if not title:
                    continue
                evidence_source = self._normalize_export_text(reasoning.get("evidence_support", ""), limit=160)
                matched_snippets = self._match_evidence_excerpts(
                    evidence_source,
                    evidence_snippets,
                )
                judgment_items.append(
                    {
                        "title": title,
                        "status": self._normalize_pre_review_conclusion(item.get("status", "")),
                        "basis": self._normalize_export_text(item.get("basis", "") or reasoning.get("rule_requirement", "")),
                        "reason": self._normalize_export_text(item.get("reason", "") or reasoning.get("judgment_reason", "")),
                        "evidence_source": evidence_source,
                        "evidence_excerpt": self._normalize_export_text(
                            reasoning.get("evidence_excerpt", "") or "；".join(matched_snippets),
                            limit=220,
                        ),
                    }
                )
            rows.append(
                {
                    "section_id": section_id,
                    "section_name": section_name,
                    "depth": max(1, min(int(depth or 1), 6)),
                    "conclusion": self._normalize_pre_review_conclusion(output.get("pre_review_conclusion", "")) if output else "当前运行未覆盖该章节",
                    "summary": self._normalize_export_text(((output.get("reviewer_outline", {}) or {}).get("summary", "")) if isinstance(output.get("reviewer_outline", {}), dict) else "", limit=220),
                    "judgment_items": judgment_items,
                    "evidence_snippets": evidence_snippets,
                }
            )
            for child in [child for child in (node.get("children_sections") or []) if isinstance(child, dict)]:
                render_node(child, min(depth + 1, 6))

        for node in chapter_structure:
            render_node(node, 1)
        return rows

    def _build_review_conclusions_markdown(
        self,
        overview: Dict[str, Any],
        trace_by_section: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> str:
        export_rows = self._build_review_conclusion_export_rows(overview, trace_by_section=trace_by_section)
        lines: List[str] = []
        run_id = str(overview.get("run_id", "") or "").strip()
        source_doc_id = str(overview.get("source_doc_id", "") or "").strip()
        lines.append(self._markdown_heading(1, f"预审结论导出 - {run_id or '当前运行'}"))
        lines.append("")
        lines.append(self._markdown_bullet("运行 ID", run_id))
        lines.append(self._markdown_bullet("源文档 ID", source_doc_id))
        lines.append(self._markdown_bullet("生成时间", datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        lines.append("")
        for row in export_rows:
            lines.append(self._markdown_heading(int(row.get("depth", 1) or 1), f"{row.get('section_id', '')} {row.get('section_name', '')}".strip()))
            lines.append("")
            lines.append(self._markdown_bullet("审评结论", row.get("conclusion", "")))
            if str(row.get("summary", "") or "").strip() and str(row.get("summary", "")).strip() != "-":
                lines.append(self._markdown_bullet("核心判断", row.get("summary", "")))
            judgment_items = row.get("judgment_items", []) if isinstance(row.get("judgment_items", []), list) else []
            if judgment_items:
                lines.append("")
                lines.append(self._markdown_heading(min(int(row.get("depth", 1) or 1) + 1, 6), "判断项"))
                lines.append("")
                for index, item in enumerate(judgment_items, start=1):
                    lines.append(f"{index}. **{item.get('title', '')}**")
                    lines.append(f"   - 状态：{item.get('status', '-')}")
                    lines.append(f"   - 规则依据：{item.get('basis', '-')}")
                    lines.append(f"   - 判断原因：{item.get('reason', '-')}")
                    lines.append(f"   - 判断依据来源：{item.get('evidence_source', '-')}")
                    lines.append(f"   - 依据来源原文片段：{item.get('evidence_excerpt', '-')}")
                    lines.append("")
            evidence_snippets = row.get("evidence_snippets", []) if isinstance(row.get("evidence_snippets", []), list) else []
            if evidence_snippets:
                lines.append(self._markdown_heading(min(int(row.get("depth", 1) or 1) + 1, 6), "参考证据片段"))
                lines.append("")
                for item in evidence_snippets:
                    lines.append(f"- **来源**：{item.get('source', '-')}")
                    lines.append(f"  - 片段：{item.get('snippet', '-')}")
                lines.append("")

        return "\n".join(lines).strip() + "\n"

    def export_review_conclusions_report(self, run_id: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        session = self.db_conn.get_session()
        try:
            ok, msg, context = self._build_review_conclusion_export_context(session, run_id)
            if not ok or not isinstance(context, dict):
                return False, msg, None
            project_name = str(context.get("project_name", "") or "").strip()
            safe_name = self._sanitize_docx_file_name(project_name or run_id)
            file_name = f"{safe_name}审评结论.docx"
            output_path = os.path.join(REPORT_DIR, f"{run_id}_review_conclusions_v3.docx")
            finish_time = context.get("finish_time")
            if os.path.exists(output_path) and isinstance(finish_time, datetime):
                cached_mtime = datetime.fromtimestamp(os.path.getmtime(output_path))
                if cached_mtime >= finish_time:
                    return True, "review conclusions exported", {
                        "run_id": run_id,
                        "report_path": output_path,
                        "file_name": file_name,
                    }
            export_rows = self._build_review_conclusion_export_rows(
                context,
                trace_by_section=context.get("trace_by_section", {}) if isinstance(context.get("trace_by_section", {}), dict) else {},
            )
            doc = Document()
            self._apply_docx_export_styles(doc)
            doc.add_heading(f"{project_name or run_id}审评结论", 0)
            self._add_docx_label_value_paragraph(doc, "运行 ID", run_id)
            self._add_docx_label_value_paragraph(doc, "源文档 ID", str(context.get('source_doc_id', '') or '').strip() or '-')
            self._add_docx_label_value_paragraph(doc, "生成时间", datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
            for row in export_rows:
                doc.add_heading(f"{row.get('section_id', '')} {row.get('section_name', '')}".strip(), level=max(1, min(int(row.get("depth", 1) or 1), 6)))
                self._add_docx_label_value_paragraph(doc, "审评结论", row.get('conclusion', '-'))
                if str(row.get("summary", "") or "").strip() and str(row.get("summary", "")).strip() != "-":
                    self._add_docx_label_value_paragraph(doc, "核心判断", row.get('summary', ''))
                judgment_items = row.get("judgment_items", []) if isinstance(row.get("judgment_items", []), list) else []
                if judgment_items:
                    paragraph = doc.add_paragraph()
                    run = paragraph.add_run("判断项：")
                    self._set_docx_run_font(run, bold=True)
                    for index, item in enumerate(judgment_items, start=1):
                        doc.add_paragraph(f"{index}. {item.get('title', '-')}", style="List Number")
                        self._add_docx_label_value_paragraph(doc, "状态", item.get('status', '-'))
                        self._add_docx_label_value_paragraph(doc, "规则依据", item.get('basis', '-'))
                        self._add_docx_label_value_paragraph(doc, "判断原因", item.get('reason', '-'))
                        self._add_docx_label_value_paragraph(doc, "判断依据来源", item.get('evidence_source', '-'))
                        self._add_docx_label_value_paragraph(doc, "依据来源原文片段", item.get('evidence_excerpt', '-'))
                evidence_snippets = row.get("evidence_snippets", []) if isinstance(row.get("evidence_snippets", []), list) else []
                if evidence_snippets:
                    paragraph = doc.add_paragraph()
                    run = paragraph.add_run("参考证据片段：")
                    self._set_docx_run_font(run, bold=True)
                    for item in evidence_snippets:
                        self._add_docx_label_value_paragraph(doc, "来源", item.get('source', '-'))
                        self._add_docx_label_value_paragraph(doc, "片段", item.get('snippet', '-'))
            doc.save(output_path)
            return True, "review conclusions exported", {
                "run_id": run_id,
                "report_path": output_path,
                "file_name": file_name,
            }
        except Exception as exc:
            return False, f"export review conclusions failed: {str(exc)}", None
        finally:
            session.close()

    def _compute_feedback_stats(self, session, run_id: str, section_id: str = "") -> Dict[str, Any]:
        query = session.query(PreReviewFeedback).filter(PreReviewFeedback.run_id == run_id)
        if section_id:
            query = query.filter(PreReviewFeedback.section_id == section_id)
        rows = query.all()

        metrics = feedback_metrics([x.feedback_type for x in rows])
        return {
            "run_id": run_id,
            "section_id": section_id or "",
            "feedback_total": int(metrics["feedback_total"]),
            "valid_count": int(metrics["tp_valid"]),
            "false_positive_count": int(metrics["fp_false_positive"]),
            "missed_count": int(metrics["fn_missed"]),
            "accuracy": metrics["accuracy"],
            "precision": metrics["precision"],
            "recall": metrics["recall"],
            "f1": metrics["f1"],
            "reward_score": metrics["reward_score"],
        }

    @staticmethod
    def _feedback_event_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
        event_payload = dict(payload if isinstance(payload, dict) else {})
        event_payload["analysis_kind"] = str(event_payload.get("analysis_kind", "") or "feedback_event")
        return event_payload

    @staticmethod
    def _extract_feedback_meta(payload: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            return {}
        meta = payload.get("feedback_meta", {}) if isinstance(payload.get("feedback_meta", {}), dict) else {}
        issue_feedback_rows: List[Dict[str, Any]] = []
        issue_feedback_raw = meta.get("issue_feedback", [])
        if isinstance(issue_feedback_raw, list):
            issue_feedback_rows = [item for item in issue_feedback_raw if isinstance(item, dict)]
        elif isinstance(issue_feedback_raw, dict):
            for issue_key, item in issue_feedback_raw.items():
                if not isinstance(item, dict):
                    continue
                row_item = dict(item)
                if not str(row_item.get("issue_key", "") or "").strip():
                    row_item["issue_key"] = str(issue_key or "").strip()
                issue_feedback_rows.append(row_item)
        chain_mode = str(meta.get("chain_mode", "") or payload.get("chain_mode", "") or "").strip()
        feedback_type = str(meta.get("feedback_type", "") or payload.get("feedback_type", "") or "").strip()
        decision = str(meta.get("decision", "") or payload.get("decision", "") or "").strip()
        return {
            "analysis_kind": str(payload.get("analysis_kind", "") or "feedback_optimize"),
            "chain_mode": chain_mode or "feedback_optimize",
            "feedback_type": feedback_type,
            "decision": decision,
            "manual_modified": bool(meta.get("manual_modified", False)),
            "diff_changed": bool(meta.get("diff_changed", False)),
            "labels": meta.get("labels", []) if isinstance(meta.get("labels", []), list) else [],
            "issue_feedback": issue_feedback_rows,
            "evidence_feedback": meta.get("evidence_feedback", []) if isinstance(meta.get("evidence_feedback", []), list) else [],
            "missing_item_feedback": meta.get("missing_item_feedback", {}) if isinstance(meta.get("missing_item_feedback", {}), dict) else {},
            "signal_summary": meta.get("signal_summary", {}) if isinstance(meta.get("signal_summary", {}), dict) else {},
            "retrieval_feedback": str(meta.get("retrieval_feedback", "") or payload.get("retrieval_feedback", "") or "").strip(),
            "conclusion_feedback": str(meta.get("conclusion_feedback", "") or payload.get("conclusion_feedback", "") or "").strip(),
            "feedback_optimize_status": str(meta.get("feedback_optimize_status", "") or payload.get("feedback_optimize_status", "") or "").strip(),
            "candidate_register_status": str(meta.get("candidate_register_status", "") or payload.get("candidate_register_status", "") or "").strip(),
            "replay_status": str(meta.get("replay_status", "") or payload.get("replay_status", "") or "").strip(),
            "p52_error_family": str(meta.get("p52_error_family", "") or payload.get("p52_error_family", "") or "").strip(),
            "error_message": str(meta.get("error_message", "") or payload.get("error_message", "") or "").strip(),
            "reference_example": payload.get("reference_example", {}) if isinstance(payload.get("reference_example", {}), dict) else {},
            "original_output": payload.get("original_output", {}) if isinstance(payload.get("original_output", {}), dict) else {},
            "revised_output": payload.get("revised_output", {}) if isinstance(payload.get("revised_output", {}), dict) else {},
            "suggestion": str(meta.get("suggestion", "") or payload.get("suggestion", "") or "").strip(),
            "optimize_evaluation": meta.get("optimize_evaluation", payload.get("optimize_evaluation", {}))
            if isinstance(meta.get("optimize_evaluation", payload.get("optimize_evaluation", {})), dict)
            else {},
            "ablation_result": payload.get("ablation_result", {}) if isinstance(payload.get("ablation_result", {}), dict) else {},
            "verification_result": payload.get("verification_result", {}) if isinstance(payload.get("verification_result", {}), dict) else {},
        }

    def record_feedback_pipeline_event(
        self,
        run_id: str,
        section_id: str,
        payload: Dict[str, Any],
    ) -> Tuple[bool, str]:
        session = self.db_conn.get_session()
        try:
            event_payload = self._feedback_event_payload(payload)
            feedback_key = str(
                event_payload.get("feedback_key", "") or f"feedback_pipeline:{run_id}:{section_id or 'global'}:{uuid.uuid4().hex[:8]}"
            )
            session.add(
                PreReviewFeedbackAnalysisResult(
                    feedback_key=feedback_key,
                    run_id=run_id,
                    section_id=section_id or None,
                    analysis_json=self._serialize_feedback_analysis_payload(event_payload),
                    create_time=self._now(),
                )
            )
            self._refresh_accuracy(session, run_id)
            session.commit()
            return True, "feedback pipeline event recorded"
        except Exception as e:
            session.rollback()
            return False, f"record feedback pipeline event failed: {str(e)}"
        finally:
            session.close()

    def _load_feedback_analysis_payloads(self, session, run_id: str, section_id: str = "") -> List[Dict[str, Any]]:
        query = session.query(PreReviewFeedbackAnalysisResult).filter(PreReviewFeedbackAnalysisResult.run_id == run_id)
        if section_id:
            query = query.filter(PreReviewFeedbackAnalysisResult.section_id == section_id)
        rows = query.order_by(PreReviewFeedbackAnalysisResult.id.asc()).all()
        payloads: List[Dict[str, Any]] = []
        for row in rows:
            try:
                data = json.loads(row.analysis_json or "{}")
            except Exception:
                data = {}
            if isinstance(data, dict):
                payloads.append(data)
        return payloads

    def _build_feedback_history(
        self,
        session,
        run_id: str,
        section_id: str = "",
        feedback_rows: Optional[List[Any]] = None,
        analysis_payloads: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        if feedback_rows is None:
            feedback_query = session.query(PreReviewFeedback).filter(PreReviewFeedback.run_id == run_id)
            if section_id:
                feedback_query = feedback_query.filter(PreReviewFeedback.section_id == section_id)
            feedback_rows = feedback_query.order_by(PreReviewFeedback.create_time.desc(), PreReviewFeedback.id.desc()).all()
        feedback_entries = [
            {
                "id": int(getattr(row, "id", 0) or 0),
                "run_id": str(getattr(row, "run_id", "") or ""),
                "section_id": str(getattr(row, "section_id", "") or ""),
                "feedback_type": str(getattr(row, "feedback_type", "") or ""),
                "feedback_text": str(getattr(row, "feedback_text", "") or ""),
                "suggestion": str(getattr(row, "suggestion", "") or ""),
                "operator": str(getattr(row, "operator", "") or ""),
                "create_time": row.create_time.strftime("%Y-%m-%d %H:%M:%S") if getattr(row, "create_time", None) else "",
            }
            for row in feedback_rows
        ]

        optimize_events = []
        signal_entries = []
        effective_analysis_payloads = (
            analysis_payloads
            if analysis_payloads is not None
            else self._load_feedback_analysis_payloads(session, run_id=run_id, section_id=section_id)
        )
        for payload in effective_analysis_payloads:
            meta = self._extract_feedback_meta(payload)
            analysis_payload = payload.get("analysis_result", {}) if isinstance(payload.get("analysis_result", {}), dict) else dict(payload)
            patch_payload = payload.get("patch_result", {}) if isinstance(payload.get("patch_result", {}), dict) else {}
            meta_reflection_payload = payload.get("meta_reflection", {}) if isinstance(payload.get("meta_reflection", {}), dict) else {}
            ablation_payload = meta.get("ablation_result", {}) if isinstance(meta.get("ablation_result", {}), dict) else {}
            verification_payload = meta.get("verification_result", {}) if isinstance(meta.get("verification_result", {}), dict) else {}
            feedback_projection = payload.get("feedback_projection", {}) if isinstance(payload.get("feedback_projection", {}), dict) else self._build_feedback_projection(
                analysis_result=analysis_payload,
                patch_result=patch_payload,
                meta_reflection_result={},
            )
            event_entry = {
                "feedback_key": str(payload.get("feedback_key", "") or ""),
                "section_id": str(payload.get("section_id", "") or section_id or ""),
                "chain_mode": str(meta.get("chain_mode", "") or ""),
                "feedback_type": str(meta.get("feedback_type", "") or ""),
                "decision": str(meta.get("decision", "") or ""),
                "manual_modified": bool(meta.get("manual_modified", False)),
                "labels": meta.get("labels", []) if isinstance(meta.get("labels", []), list) else [],
                "issue_feedback": meta.get("issue_feedback", []) if isinstance(meta.get("issue_feedback", []), list) else [],
                "retrieval_feedback": str(meta.get("retrieval_feedback", "") or ""),
                "conclusion_feedback": str(meta.get("conclusion_feedback", "") or ""),
                "evidence_feedback": meta.get("evidence_feedback", []) if isinstance(meta.get("evidence_feedback", []), list) else [],
                "missing_item_feedback": meta.get("missing_item_feedback", {}) if isinstance(meta.get("missing_item_feedback", {}), dict) else {},
                "signal_summary": meta.get("signal_summary", {}) if isinstance(meta.get("signal_summary", {}), dict) else {},
                "reference_example": meta.get("reference_example", {}) if isinstance(meta.get("reference_example", {}), dict) else {},
                "original_output": meta.get("original_output", {}) if isinstance(meta.get("original_output", {}), dict) else {},
                "revised_output": meta.get("revised_output", {}) if isinstance(meta.get("revised_output", {}), dict) else {},
                "suggestion": str(meta.get("suggestion", "") or ""),
                "analysis_result": analysis_payload,
                "patch_result": patch_payload,
                "meta_reflection": meta_reflection_payload,
                "ablation_result": ablation_payload,
                "verification_result": verification_payload,
                "analysis_llm_execution": analysis_payload.get("llm_execution", {}) if isinstance(analysis_payload.get("llm_execution", {}), dict) else {},
                "patch_llm_execution": patch_payload.get("llm_execution", {}) if isinstance(patch_payload.get("llm_execution", {}), dict) else {},
                "analysis_contract_diagnostics": analysis_payload.get("contract_diagnostics", {}) if isinstance(analysis_payload.get("contract_diagnostics", {}), dict) else {},
                "patch_contract_diagnostics": patch_payload.get("contract_diagnostics", {}) if isinstance(patch_payload.get("contract_diagnostics", {}), dict) else {},
                "optimize_evaluation": meta.get("optimize_evaluation", {}) if isinstance(meta.get("optimize_evaluation", {}), dict) else {},
                "feedback_optimize_status": str(meta.get("feedback_optimize_status", "") or ""),
                "candidate_register_status": str(meta.get("candidate_register_status", "") or ""),
                "replay_status": str(meta.get("replay_status", "") or ""),
                "p52_error_family": str(meta.get("p52_error_family", "") or ""),
                "error_message": str(meta.get("error_message", "") or ""),
                "analysis_kind": str(meta.get("analysis_kind", "") or ""),
                "feedback_projection": feedback_projection,
            }
            signal_entries.append(event_entry)
            optimize_events.append(event_entry)

        patch_query = session.query(PreReviewPatchRegistry).filter(PreReviewPatchRegistry.run_id == run_id)
        if section_id:
            patch_query = patch_query.filter(PreReviewPatchRegistry.section_id == section_id)
        patch_rows = patch_query.order_by(PreReviewPatchRegistry.update_time.desc(), PreReviewPatchRegistry.id.desc()).all()
        patch_entries = [
            {
                "patch_id": str(getattr(row, "patch_id", "") or ""),
                "run_id": str(getattr(row, "run_id", "") or ""),
                "section_id": str(getattr(row, "section_id", "") or ""),
                "patch_type": str(getattr(row, "patch_type", "") or ""),
                "target_agent": str(getattr(row, "target_agent", "") or ""),
                "status": str(getattr(row, "status", "") or ""),
                "patch_content": str(getattr(row, "patch_content", "") or ""),
                "source_feedback_key": str(getattr(row, "source_feedback_key", "") or ""),
                "version": int(getattr(row, "version", 1) or 1),
                "create_time": row.create_time.strftime("%Y-%m-%d %H:%M:%S") if getattr(row, "create_time", None) else "",
                "update_time": row.update_time.strftime("%Y-%m-%d %H:%M:%S") if getattr(row, "update_time", None) else "",
            }
            for row in patch_rows
        ]
        accepted_patch_count = sum(
            1 for item in patch_entries if str(item.get("status", "") or "").strip().lower() in {"accepted", "approved", "applied"}
        )
        p52_optimize_events = [
            item for item in optimize_events
            if str(item.get("analysis_kind", "") or "").strip() == "p52_feedback_optimize"
        ]
        p52_meta_reflection_events = [
            item for item in optimize_events
            if str(item.get("analysis_kind", "") or "").strip() == "p52_meta_reflection"
        ]
        p52_ablation_events = [
            item for item in optimize_events
            if str(item.get("analysis_kind", "") or "").strip() == "p52_ablation"
        ]
        p52_verify_events = [
            item for item in optimize_events
            if str(item.get("analysis_kind", "") or "").strip() == "p52_feedback_verify"
        ]
        last_p52_error_family = ""
        for item in reversed(optimize_events):
            error_family = str(item.get("p52_error_family", "") or "").strip()
            if error_family:
                last_p52_error_family = error_family
                break
        return {
            "feedback_entries": feedback_entries,
            "optimize_events": optimize_events,
            "patch_entries": patch_entries,
            "signal_entries": signal_entries,
            "summary": {
                "feedback_total": len(feedback_entries),
                "optimize_event_total": len(optimize_events),
                "patch_total": len(patch_entries),
                "accepted_patch_total": accepted_patch_count,
                "p52_optimize_event_total": len(p52_optimize_events),
                "p52_meta_reflection_event_total": len(p52_meta_reflection_events),
                "p52_ablation_event_total": len(p52_ablation_events),
                "p52_feedback_verify_event_total": len(p52_verify_events),
                "p52_last_error_family": last_p52_error_family,
            },
        }

    def _compute_run_metrics(
        self,
        session,
        run_id: str,
        section_id: str = "",
        feedback_rows: Optional[List[Any]] = None,
        analysis_payloads: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        if feedback_rows is None:
            feedback_query = session.query(PreReviewFeedback).filter(PreReviewFeedback.run_id == run_id)
            if section_id:
                feedback_query = feedback_query.filter(PreReviewFeedback.section_id == section_id)
            feedback_rows = feedback_query.all()
        if analysis_payloads is None:
            analysis_payloads = self._load_feedback_analysis_payloads(session, run_id=run_id, section_id=section_id)
        run_row = session.query(PreReviewRun).filter(PreReviewRun.run_id == run_id).first()
        previous_run_id = None
        previous_false_positive_count = None
        if run_row is not None:
            previous_run = (
                session.query(PreReviewRun)
                .filter(
                    and_(
                        PreReviewRun.project_id == run_row.project_id,
                        PreReviewRun.source_doc_id == run_row.source_doc_id,
                        PreReviewRun.id < run_row.id,
                    )
                )
                .order_by(desc(PreReviewRun.id))
                .first()
            )
            if previous_run is not None:
                previous_run_id = previous_run.run_id
                prev_summary = {}
                try:
                    prev_summary = json.loads(previous_run.summary) if previous_run.summary else {}
                except Exception:
                    prev_summary = {}
                prev_metrics = prev_summary.get("metrics", {}) if isinstance(prev_summary.get("metrics", {}), dict) else {}
                prev_review = prev_metrics.get("review", {}) if isinstance(prev_metrics.get("review", {}), dict) else {}
                prev_fp = prev_review.get("false_positive_count")
                if prev_fp is None:
                    prev_fp = self._compute_feedback_stats(session, previous_run.run_id).get("false_positive_count", 0)
                if prev_fp is not None:
                    previous_false_positive_count = int(prev_fp)

        metrics = self.metrics_calculator.calc_feedback_metrics(
            feedback_records=[{"feedback_type": str(getattr(row, "feedback_type", "") or "")} for row in feedback_rows],
            analysis_payloads=analysis_payloads,
            previous_false_positive_count=previous_false_positive_count,
        )
        trajectory = metrics.get("trajectory", {}) if isinstance(metrics.get("trajectory", {}), dict) else {}
        trajectory["previous_run_id"] = previous_run_id
        metrics["trajectory"] = trajectory
        metrics["run_id"] = run_id
        metrics["section_id"] = section_id or ""
        return metrics

    def _compute_retrieval_feedback_detail(self, session, run_id: str, section_id: str = "") -> Dict[str, Any]:
        analysis_payloads = self._load_feedback_analysis_payloads(session, run_id=run_id, section_id=section_id)
        feedback_metrics_payload = self.metrics_calculator.calc_feedback_metrics(
            feedback_records=[],
            analysis_payloads=analysis_payloads,
        )
        retrieval = feedback_metrics_payload.get("retrieval", {}) if isinstance(feedback_metrics_payload.get("retrieval", {}), dict) else {}
        return {
            "metrics": {
                "evaluated_feedback_count": int(retrieval.get("evaluated_feedback_count", 0) or 0),
                "true_positive_count": int(retrieval.get("true_positive_count", 0) or 0),
                "false_positive_count": int(retrieval.get("false_positive_count", 0) or 0),
                "false_negative_count": int(retrieval.get("false_negative_count", 0) or 0),
                "accuracy": float(retrieval.get("accuracy", 0.0) or 0.0),
                "precision": float(retrieval.get("precision", 0.0) or 0.0),
                "recall": float(retrieval.get("recall", 0.0) or 0.0),
                "f1": float(retrieval.get("f1", 0.0) or 0.0),
            },
            "error_breakdown": retrieval.get("error_breakdown", {}) if isinstance(retrieval.get("error_breakdown", {}), dict) else {},
        }

    @staticmethod
    def _summarize_source_breakdown(retrieved_materials: List[Dict[str, Any]]) -> Dict[str, int]:
        breakdown: Dict[str, int] = {}
        for item in retrieved_materials:
            if not isinstance(item, dict):
                continue
            source_type = str(item.get("source_type", "") or "").strip() or "鏈煡鏉ユ簮"
            breakdown[source_type] = int(breakdown.get(source_type, 0)) + 1
        return breakdown

    def _refresh_run_metrics_summary(self, session, run_id: str) -> Dict[str, Any]:
        metrics = self._compute_run_metrics(session, run_id=run_id, section_id="")
        run = session.query(PreReviewRun).filter(PreReviewRun.run_id == run_id).first()
        if run is None:
            return metrics
        try:
            summary_payload = json.loads(run.summary) if run.summary else {}
        except Exception:
            summary_payload = {}
        if not isinstance(summary_payload, dict):
            summary_payload = {}
        summary_payload["metrics"] = metrics
        summary_payload["metrics_schema"] = "run_metrics_v2"
        summary_payload["last_metrics_refresh_time"] = self._now().strftime("%Y-%m-%d %H:%M:%S")
        run.summary = json.dumps(summary_payload, ensure_ascii=False)
        return metrics

    def _refresh_accuracy(self, session, run_id: str) -> Dict[str, Any]:
        stats = self._compute_feedback_stats(session, run_id)
        run = session.query(PreReviewRun).filter(PreReviewRun.run_id == run_id).first()
        if run is not None:
            run.accuracy = stats["accuracy"] if stats["feedback_total"] > 0 else None
        self._refresh_run_metrics_summary(session, run_id)
        return stats

    @staticmethod
    def _decision_to_feedback_type(decision: str, labels: Optional[List[Any]] = None) -> str:
        normalized = str(decision or "").strip().lower()
        label_set = {str(x).strip().lower() for x in (labels or []) if str(x).strip()}
        if normalized in {"false_positive", "invalid", "rejected"}:
            return "false_positive"
        if normalized in {"missed", "missing_risk"} or {"missed", "missing_risk"} & label_set:
            return "missed"
        return "valid"

    def _optimize_from_feedback_memory(
        self,
        run_id: str,
        section_id: str,
        feedback_type: str,
        feedback_text: str,
        suggestion: str,
        operator: str,
    ) -> None:
        self.memory_governance.remember_feedback_event(
            run_id=run_id,
            section_id=section_id,
            feedback_type=feedback_type,
            feedback_text=feedback_text,
            suggestion=suggestion,
            operator=operator,
        )

    def add_feedback(
        self,
        run_id: str,
        section_id: str,
        feedback_type: str,
        feedback_text: str = "",
        suggestion: str = "",
        operator: str = "",
        feedback_meta: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        if feedback_type not in {"valid", "false_positive", "missed"}:
            return False, "feedback_type must be one of valid/false_positive/missed", None

        session = self.db_conn.get_session()
        try:
            run = session.query(PreReviewRun).filter(PreReviewRun.run_id == run_id).first()
            if run is None:
                return False, "run not found", None

            if section_id:
                section_exists = (
                    session.query(PreReviewSectionConclusion)
                    .filter(
                        and_(
                            PreReviewSectionConclusion.run_id == run_id,
                            PreReviewSectionConclusion.section_id == section_id,
                        )
                    )
                    .first()
                )
                if section_exists is None:
                    return False, "section_id does not belong to this run", None

            fb = PreReviewFeedback(
                run_id=run_id,
                section_id=section_id or None,
                feedback_type=feedback_type,
                feedback_text=feedback_text,
                suggestion=suggestion,
                operator=operator,
                create_time=self._now(),
            )
            session.add(fb)
            if isinstance(feedback_meta, dict) and feedback_meta.get("persist_event", False):
                feedback_key = str(feedback_meta.get("feedback_key", "") or f"feedback_event:{run_id}:{section_id or 'global'}:{uuid.uuid4().hex[:8]}")
                event_payload = self._feedback_event_payload(
                    {
                        "chain_mode": str(feedback_meta.get("chain_mode", "") or "feedback_only"),
                        "feedback_type": feedback_type,
                        "retrieval_feedback": str(feedback_meta.get("retrieval_feedback", "") or ""),
                        "conclusion_feedback": str(feedback_meta.get("conclusion_feedback", "") or ""),
                        "reference_example": feedback_meta.get("reference_example", {}) if isinstance(feedback_meta.get("reference_example", {}), dict) else {},
                        "original_output": feedback_meta.get("original_output", {}) if isinstance(feedback_meta.get("original_output", {}), dict) else {},
                        "revised_output": feedback_meta.get("revised_output", {}) if isinstance(feedback_meta.get("revised_output", {}), dict) else {},
                        "suggestion": str(feedback_meta.get("suggestion", "") or suggestion or ""),
                        "feedback_meta": {
                            "chain_mode": str(feedback_meta.get("chain_mode", "") or "feedback_only"),
                            "feedback_type": feedback_type,
                            "decision": str(feedback_meta.get("decision", "") or ""),
                            "manual_modified": bool(feedback_meta.get("manual_modified", False)),
                            "diff_changed": bool(feedback_meta.get("manual_modified", False)),
                            "labels": feedback_meta.get("labels", []) if isinstance(feedback_meta.get("labels", []), list) else [],
                            "issue_feedback": self._normalize_issue_feedback_rows(feedback_meta.get("issue_feedback", [])),
                            "evidence_feedback": feedback_meta.get("evidence_feedback", []) if isinstance(feedback_meta.get("evidence_feedback", []), list) else [],
                            "missing_item_feedback": feedback_meta.get("missing_item_feedback", {}) if isinstance(feedback_meta.get("missing_item_feedback", {}), dict) else {},
                            "retrieval_feedback": str(feedback_meta.get("retrieval_feedback", "") or ""),
                            "conclusion_feedback": str(feedback_meta.get("conclusion_feedback", "") or ""),
                            "suggestion": str(feedback_meta.get("suggestion", "") or suggestion or ""),
                        },
                    }
                )
                session.add(
                    PreReviewFeedbackAnalysisResult(
                        feedback_key=feedback_key,
                        run_id=run_id,
                        section_id=section_id or None,
                        analysis_json=self._serialize_feedback_analysis_payload(event_payload),
                        create_time=self._now(),
                    )
                )
            session.flush()
            stats = self._refresh_accuracy(session, run_id)
            session.commit()
            self._optimize_from_feedback_memory(
                run_id=run_id,
                section_id=section_id,
                feedback_type=feedback_type,
                feedback_text=feedback_text,
                suggestion=suggestion,
                operator=operator,
            )
            return True, "feedback added", stats
        except Exception as e:
            session.rollback()
            return False, f"add feedback failed: {str(e)}", None
        finally:
            session.close()

    def _load_current_template_text(self, template_name: str) -> str:
        template_path = Path(__file__).resolve().parents[1] / "prompts" / "pre_review_agent_prompt" / template_name
        if not template_path.exists():
            return ""
        try:
            return template_path.read_text(encoding="utf-8")
        except Exception:
            return ""

    def _load_feedback_section_context(
        self,
        session,
        run_id: str,
        section_id: str,
        run_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return self.runtime_context_service.load_feedback_context(
            session,
            run_id=run_id,
            section_id=section_id,
            run_context=run_context,
        ).to_dict()

    def _sync_feedback_section_rules(
        self,
        session,
        *,
        project_id: str,
        section_id: str,
        section_name: str,
        feedback_key: str,
        analysis_result: Dict[str, Any],
        patch_result: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        if not project_id or not section_id:
            return []
        rows = (
            session.query(PreReviewSectionRule)
            .filter(
                PreReviewSectionRule.project_id == project_id,
                PreReviewSectionRule.section_id == section_id,
            )
            .all()
        )
        existing_map = {
            str(getattr(row, "rule_code", "") or "").strip(): row
            for row in rows
            if str(getattr(row, "rule_code", "") or "").strip()
        }
        synced: List[Dict[str, Any]] = []
        now = self._now()

        def upsert(rule_code: str, rule_text: str, source_type: str, payload: Dict[str, Any]) -> None:
            normalized_code = str(rule_code or "").strip()
            normalized_text = str(rule_text or "").strip()
            if not normalized_code or not normalized_text:
                return
            row = existing_map.get(normalized_code)
            if row is None:
                row = PreReviewSectionRule(
                    rule_id=f"section_rule_{uuid.uuid4().hex[:16]}",
                    project_id=project_id,
                    section_id=section_id,
                    section_name=section_name,
                    rule_code=normalized_code,
                    rule_text=normalized_text,
                    source_type=source_type,
                    source_ref=feedback_key,
                    is_active=True,
                    payload_json=json.dumps(payload, ensure_ascii=False),
                    create_time=now,
                    update_time=now,
                )
                session.add(row)
                existing_map[normalized_code] = row
            else:
                row.section_name = section_name
                row.rule_text = normalized_text
                row.source_type = source_type
                row.source_ref = feedback_key
                row.is_active = True
                row.payload_json = json.dumps(payload, ensure_ascii=False)
                row.update_time = now
            synced.append(
                {
                    "rule_code": normalized_code,
                    "rule_text": normalized_text,
                    "source_type": source_type,
                }
            )

        for index, item in enumerate(analysis_result.get("new_experience", []) if isinstance(analysis_result, dict) else [], start=1):
            if not isinstance(item, dict):
                continue
            content = str(item.get("content", "") or "").strip()
            if not content:
                continue
            upsert(
                rule_code=f"feedback_experience__{section_id.replace('.', '_')}__{index:02d}",
                rule_text=content,
                source_type="feedback_experience",
                payload=item,
            )

        for index, item in enumerate(patch_result.get("patches", []) if isinstance(patch_result, dict) else [], start=1):
            if not isinstance(item, dict):
                continue
            content = str(item.get("patch_content", "") or "").strip()
            target_agent = str(item.get("target_agent", "") or "").strip()
            if not content or target_agent not in {"planner", "retrieval_evaluator", "task_question", "reviewer"}:
                continue
            upsert(
                rule_code=f"feedback_patch__{target_agent}__{section_id.replace('.', '_')}__{index:02d}",
                rule_text=content,
                source_type="feedback_patch",
                payload=item,
            )
        return synced

    @staticmethod
    def _extract_high_frequency_doc_ids(retrieved_materials: List[Dict[str, Any]], limit: int = 5) -> List[str]:
        counts: Dict[str, int] = {}
        for item in retrieved_materials:
            if not isinstance(item, dict):
                continue
            doc_id = str(item.get("doc_id", "") or "").strip()
            if not doc_id:
                continue
            counts[doc_id] = int(counts.get(doc_id, 0)) + 1
        return [key for key, _ in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[: max(1, int(limit or 5))]]

    @staticmethod
    def _normalize_feedback_knowledge_scope(scope_key: str, default_scope_type: str = "project") -> str:
        normalized = str(scope_key or "").strip()
        if normalized.startswith("3.2."):
            return "section"
        if normalized.lower() in {"global", "all", "*"}:
            return "global"
        if normalized:
            return str(default_scope_type or "project").strip() or "project"
        return str(default_scope_type or "project").strip() or "project"

    @staticmethod
    def _build_feedback_knowledge_profile(
        *,
        experience_type: str,
        primary_error_type: str,
        optimization_target: str,
    ) -> Dict[str, str]:
        normalized_exp = str(experience_type or "").strip()
        normalized_error = str(primary_error_type or "").strip()
        normalized_target = str(optimization_target or "").strip()

        if normalized_exp in {"wording_rule"} or normalized_error in {"wording_not_actionable", "unhelpful_question_to_applicant"}:
            return {
                "experience_type": "wording_rule",
                "knowledge_category": "wording",
                "optimization_target": normalized_target or "reviewer",
            }
        if normalized_exp in {"risk_pattern"} or normalized_error in {"under_identification", "wrong_severity"}:
            return {
                "experience_type": "risk_pattern",
                "knowledge_category": "risk",
                "optimization_target": normalized_target or "reviewer",
            }
        if normalized_exp in {"query_rule", "retrieval_knowledge"} or normalized_error in {
            "query_miss",
            "retrieval_scope_error",
            "retrieval_ranking_error",
            "historical_experience_missing",
        }:
            return {
                "experience_type": "retrieval_knowledge",
                "knowledge_category": "retrieval",
                "optimization_target": normalized_target or "planner",
            }
        if normalized_exp in {"rule_knowledge"} or normalized_error in {"rule_mapping_error", "missing_regulatory_basis"}:
            return {
                "experience_type": "rule_knowledge",
                "knowledge_category": "rule",
                "optimization_target": normalized_target or "task_question",
            }
        if normalized_exp in {"fact_knowledge"} or normalized_error in {"knowledge_understanding_error", "section_fact_extraction_error"}:
            return {
                "experience_type": "fact_knowledge",
                "knowledge_category": "fact",
                "optimization_target": normalized_target or "planner",
            }
        if normalized_exp in {"task_knowledge"} or normalized_error in {"task_question_error", "focus_point_miss"}:
            return {
                "experience_type": "task_knowledge",
                "knowledge_category": "task",
                "optimization_target": normalized_target or "task_question",
            }
        if normalized_exp in {"reasoning_knowledge", "review_rule", "meta_reflection"} or normalized_error in {
            "reasoning_chain_error",
            "evidence_interpretation_error",
            "over_inference",
        }:
            return {
                "experience_type": "reasoning_knowledge",
                "knowledge_category": "reasoning",
                "optimization_target": normalized_target or "reviewer",
            }
        return {
            "experience_type": normalized_exp or "meta_reflection",
            "knowledge_category": "generic",
            "optimization_target": normalized_target or "reviewer",
        }

    @staticmethod
    def _feedback_target_layer(primary_error_type: str, root_category: str = "") -> Dict[str, str]:
        normalized_error = str(primary_error_type or "").strip()
        normalized_root = str(root_category or "").strip()
        if normalized_error in {"query_miss", "retrieval_scope_error", "retrieval_ranking_error", "historical_experience_missing"} or normalized_root == "rag_issue":
            return {"code": "retrieval", "label": "检索层"}
        if normalized_error in {"rule_mapping_error", "missing_regulatory_basis"} or normalized_root == "rule_issue":
            return {"code": "rule", "label": "规则层"}
        if normalized_error in {"knowledge_understanding_error", "section_fact_extraction_error"}:
            return {"code": "fact", "label": "事实识别层"}
        if normalized_error in {"task_question_error", "focus_point_miss"}:
            return {"code": "task", "label": "任务提出层"}
        if normalized_error in {"reasoning_chain_error", "evidence_interpretation_error", "over_inference", "under_identification", "wrong_severity"} or normalized_root == "agent_issue":
            return {"code": "reasoning", "label": "推理链层"}
        if normalized_error in {"wording_not_actionable", "unhelpful_question_to_applicant"} or normalized_root == "prompt_issue":
            return {"code": "wording", "label": "表达层"}
        return {"code": "general", "label": "一般层"}

    @staticmethod
    def _feedback_patch_scope_label(patch_scope: str) -> str:
        mapping = {
            "section_retrieval": "章节检索策略",
            "section_rule_mapping": "章节规则映射",
            "section_fact_extraction": "章节事实抽取",
            "section_task_design": "章节任务设计",
            "section_reasoning": "章节推理判断",
            "review_output_style": "审评输出表达",
            "workflow_policy": "工作流策略",
            "section_general": "章节通用策略",
        }
        normalized = str(patch_scope or "").strip()
        return mapping.get(normalized, normalized or "-")

    def _build_feedback_knowledge_payload(
        self,
        *,
        origin: str,
        feedback_key: str,
        run_id: str,
        section_id: str,
        section_name: str,
        source_doc_id: str,
        applicable_scope: str,
        content: str,
        default_scope_type: str = "project",
        base_item: Optional[Dict[str, Any]] = None,
        analysis_result: Optional[Dict[str, Any]] = None,
        patch_result: Optional[Dict[str, Any]] = None,
        extra_payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        item = base_item if isinstance(base_item, dict) else {}
        analysis = analysis_result if isinstance(analysis_result, dict) else {}
        patch = patch_result if isinstance(patch_result, dict) else {}
        patches = patch.get("patches", []) if isinstance(patch.get("patches", []), list) else []
        first_patch = patches[0] if patches and isinstance(patches[0], dict) else {}
        source_error_type = str(item.get("source_error_type", "") or analysis.get("primary_error_type", "") or "").strip()
        optimization_target = str(
            item.get("optimization_target", "") or first_patch.get("target_agent", "") or ""
        ).strip()
        profile = self._build_feedback_knowledge_profile(
            experience_type=str(item.get("experience_type", "") or "").strip(),
            primary_error_type=source_error_type,
            optimization_target=optimization_target,
        )
        payload = {
            "origin": origin,
            "feedback_key": feedback_key,
            "run_id": run_id,
            "doc_id": source_doc_id,
            "section_id": section_id,
            "section_name": section_name,
            "applicable_scope": applicable_scope,
            "scope_type": self._normalize_feedback_knowledge_scope(applicable_scope, default_scope_type=default_scope_type),
            "experience_type": profile["experience_type"],
            "knowledge_category": profile["knowledge_category"],
            "optimization_target": profile["optimization_target"],
            "target_layer": self._feedback_target_layer(source_error_type, str(analysis.get("root_category", "") or analysis.get("category", "") or "")).get("code", "general"),
            "patch_scope": str(first_patch.get("patch_scope", "") or "").strip(),
            "source_error_type": source_error_type,
            "source_signal": str(item.get("source_signal", "") or "").strip(),
            "content_preview": str(content or "").strip()[:240],
        }
        if extra_payload:
            payload.update(extra_payload)
        return payload

    @staticmethod
    def _feedback_issue_family(primary_error_type: str, root_category: str = "") -> Dict[str, str]:
        normalized_error = str(primary_error_type or "").strip()
        normalized_root = str(root_category or "").strip()
        if normalized_error in {
            "query_miss",
            "retrieval_scope_error",
            "retrieval_ranking_error",
            "historical_experience_missing",
        } or normalized_root == "rag_issue":
            return {"code": "retrieval", "label": "检索问题"}
        if normalized_error in {"rule_mapping_error", "missing_regulatory_basis"} or normalized_root == "rule_issue":
            return {"code": "rule", "label": "规则问题"}
        if normalized_error in {"knowledge_understanding_error", "section_fact_extraction_error"}:
            return {"code": "fact", "label": "事实问题"}
        if normalized_error in {"task_question_error", "focus_point_miss"}:
            return {"code": "task", "label": "任务理解问题"}
        if normalized_error in {
            "reasoning_chain_error",
            "evidence_interpretation_error",
            "over_inference",
            "under_identification",
            "wrong_severity",
        } or normalized_root == "agent_issue":
            return {"code": "reasoning", "label": "推理链问题"}
        if normalized_error in {"wording_not_actionable", "unhelpful_question_to_applicant"} or normalized_root == "prompt_issue":
            return {"code": "wording", "label": "表达问题"}
        return {"code": "general", "label": "一般问题"}

    @staticmethod
    def _feedback_error_label(primary_error_type: str) -> str:
        mapping = {
            "query_miss": "检索查询缺失",
            "retrieval_scope_error": "检索范围错误",
            "retrieval_ranking_error": "检索排序错误",
            "historical_experience_missing": "历史经验缺失",
            "knowledge_understanding_error": "知识理解错误",
            "rule_mapping_error": "规则映射错误",
            "section_fact_extraction_error": "章节事实抽取错误",
            "focus_point_miss": "关注点遗漏",
            "task_question_error": "判断项构造错误",
            "reasoning_chain_error": "推理链错误",
            "evidence_interpretation_error": "证据解释错误",
            "over_inference": "过度推断",
            "under_identification": "问题识别不足",
            "wrong_severity": "风险等级判断错误",
            "missing_regulatory_basis": "法规依据缺失",
            "wording_not_actionable": "表述不可执行",
            "unhelpful_question_to_applicant": "问题表述无助于申请人整改",
        }
        normalized = str(primary_error_type or "").strip()
        return mapping.get(normalized, normalized or "-")

    @staticmethod
    def _feedback_target_agent_label(target_agent: str) -> str:
        mapping = {
            "planner": "planner（任务理解与检索规划）",
            "retrieval_evaluator": "retrieval_evaluator（证据筛选与准入）",
            "task_question": "task_question（判断项构造）",
            "reviewer": "reviewer（规则比对与结论生成）",
            "feedback_analyzer": "feedback_analyzer（反馈归因）",
            "feedback_optimizer": "feedback_optimizer（补丁生成）",
        }
        normalized = str(target_agent or "").strip()
        return mapping.get(normalized, normalized or "-")

    def _build_feedback_projection(
        self,
        *,
        analysis_result: Optional[Dict[str, Any]] = None,
        patch_result: Optional[Dict[str, Any]] = None,
        meta_reflection_result: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        analysis = analysis_result if isinstance(analysis_result, dict) else {}
        patch = patch_result if isinstance(patch_result, dict) else {}
        meta = meta_reflection_result if isinstance(meta_reflection_result, dict) else {}
        primary_error_type = str(analysis.get("primary_error_type", "") or "").strip()
        root_category = str(analysis.get("root_category", "") or analysis.get("category", "") or "").strip()
        issue_family = self._feedback_issue_family(primary_error_type, root_category)
        target_layer = self._feedback_target_layer(primary_error_type, root_category)
        patches = patch.get("patches", []) if isinstance(patch.get("patches", []), list) else []
        target_agents: List[str] = []
        patch_types: List[str] = []
        patch_scopes: List[str] = []
        for item in patches:
            if not isinstance(item, dict):
                continue
            target_agent = str(item.get("target_agent", "") or "").strip()
            patch_type = str(item.get("patch_type", "") or "").strip()
            patch_scope = str(item.get("patch_scope", "") or "").strip()
            if target_agent and target_agent not in target_agents:
                target_agents.append(target_agent)
            if patch_type and patch_type not in patch_types:
                patch_types.append(patch_type)
            if patch_scope and patch_scope not in patch_scopes:
                patch_scopes.append(patch_scope)
        knowledge_categories: List[str] = []
        for item in analysis.get("new_experience", []) if isinstance(analysis.get("new_experience", []), list) else []:
            if not isinstance(item, dict):
                continue
            category = str(item.get("knowledge_category", "") or "").strip()
            if category and category not in knowledge_categories:
                knowledge_categories.append(category)
        for item in meta.get("experiences", []) if isinstance(meta.get("experiences", []), list) else []:
            if not isinstance(item, dict):
                continue
            category = str(item.get("knowledge_category", "") or "").strip()
            if category and category not in knowledge_categories:
                knowledge_categories.append(category)
        analysis_llm_execution = analysis.get("llm_execution", {}) if isinstance(analysis.get("llm_execution", {}), dict) else {}
        patch_llm_execution = patch.get("llm_execution", {}) if isinstance(patch.get("llm_execution", {}), dict) else {}
        analysis_contract_diagnostics = analysis.get("contract_diagnostics", {}) if isinstance(analysis.get("contract_diagnostics", {}), dict) else {}
        patch_contract_diagnostics = patch.get("contract_diagnostics", {}) if isinstance(patch.get("contract_diagnostics", {}), dict) else {}
        diagnostic_errors = self._normalize_text_list(
            [
                str(analysis.get("error_message", "") or "").strip(),
                str(patch.get("error_message", "") or "").strip(),
                str(analysis_llm_execution.get("error_message", "") or "").strip(),
                str(patch_llm_execution.get("error_message", "") or "").strip(),
            ]
        )
        return {
            "issue_family": issue_family["code"],
            "issue_family_label": issue_family["label"],
            "root_category": root_category,
            "target_layer": target_layer["code"],
            "target_layer_label": target_layer["label"],
            "primary_error_type": primary_error_type,
            "primary_error_label": self._feedback_error_label(primary_error_type),
            "target_agents": target_agents,
            "target_agent_labels": [self._feedback_target_agent_label(item) for item in target_agents],
            "primary_route": issue_family["code"],
            "patch_types": patch_types,
            "patch_scopes": patch_scopes,
            "patch_scope_labels": [self._feedback_patch_scope_label(item) for item in patch_scopes],
            "patch_count": len(patches),
            "knowledge_categories": knowledge_categories,
            "primary_knowledge_category": knowledge_categories[0] if knowledge_categories else str(analysis.get("knowledge_category", "") or "").strip() or "generic",
            "analysis_llm_execution": analysis_llm_execution,
            "patch_llm_execution": patch_llm_execution,
            "analysis_contract_diagnostics": analysis_contract_diagnostics,
            "patch_contract_diagnostics": patch_contract_diagnostics,
            "diagnostic_errors": diagnostic_errors,
        }

    def _persist_meta_reflection_outputs(
        self,
        session,
        *,
        project_id: str,
        run_id: str,
        source_doc_id: str,
        section_id: str,
        section_name: str,
        feedback_key: str,
        meta_reflection: Dict[str, Any],
        reference_example: Dict[str, Any],
        review_output: Dict[str, Any],
        trace_payload: Dict[str, Any],
        iteration: int,
    ) -> Dict[str, Any]:
        if not isinstance(meta_reflection, dict):
            return {"experiences": [], "few_shot_example": {}}
        now = self._now()
        synced_experiences: List[Dict[str, Any]] = []
        for item in meta_reflection.get("distilled_experiences", []) if isinstance(meta_reflection.get("distilled_experiences", []), list) else []:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content", "") or "").strip()
            if not content:
                continue
            applicable_scope = str(item.get("applicable_scope", "") or section_id).strip() or section_id
            payload = self._build_feedback_knowledge_payload(
                origin="meta_reflection",
                feedback_key=feedback_key,
                run_id=run_id,
                section_id=section_id,
                section_name=section_name,
                source_doc_id=source_doc_id,
                applicable_scope=applicable_scope,
                content=content,
                default_scope_type="project",
                base_item=item,
                extra_payload={
                    "iteration": iteration,
                    "bad_case": bool(meta_reflection.get("bad_case", False)),
                    "high_frequency_doc_ids": meta_reflection.get("high_frequency_doc_ids", []),
                    "reference_example": reference_example,
                },
            )
            scope_type = str(payload.get("scope_type", "") or "project").strip() or "project"
            normalized_experience_type = str(payload.get("experience_type", "") or "meta_reflection").strip() or "meta_reflection"
            experience_row = PreReviewExperienceMemory(
                experience_id=f"exp_{uuid.uuid4().hex[:12]}",
                scope_type=scope_type,
                scope_key=applicable_scope,
                experience_type=normalized_experience_type,
                content=content,
                source_feedback_ids=json.dumps([feedback_key], ensure_ascii=False),
                trigger_conditions=json.dumps([f"section_id == '{section_id}'"], ensure_ascii=False),
                usage_count=0,
                success_count=0,
                status="active",
                payload_json=json.dumps(payload, ensure_ascii=False),
                create_time=now,
                update_time=now,
            )
            session.add(experience_row)
            synced_experiences.append(
                {
                    "experience_type": experience_row.experience_type,
                    "knowledge_category": str(payload.get("knowledge_category", "") or "").strip(),
                    "optimization_target": str(payload.get("optimization_target", "") or "").strip(),
                    "source_error_type": str(payload.get("source_error_type", "") or "").strip(),
                    "content": content,
                    "scope_type": scope_type,
                    "scope_key": applicable_scope,
                }
            )
        few_shot_example = meta_reflection.get("few_shot_example", {}) if isinstance(meta_reflection.get("few_shot_example", {}), dict) else {}
        saved_example = {}
        if (str(few_shot_example.get('title', '') or '').strip()
                and isinstance(few_shot_example.get('input_snapshot'), dict) and few_shot_example['input_snapshot']
                and isinstance(few_shot_example.get('expected_output'), dict) and few_shot_example['expected_output']):
            saved_example = self._upsert_section_example(
                session=session,
                project_id=project_id,
                run_id=run_id,
                section_id=section_id,
                doc_id=source_doc_id,
                example_type="few_shot",
                title=str(few_shot_example.get("title", "") or f"{section_id} few-shot").strip(),
                content=str(meta_reflection.get("reflection_summary", "") or "").strip() or str(reference_example.get("content", "") or ""),
                input_payload=few_shot_example.get("input_snapshot", {}) if isinstance(few_shot_example.get("input_snapshot", {}), dict) else {},
                output_payload=few_shot_example.get("expected_output", {}) if isinstance(few_shot_example.get("expected_output", {}), dict) else review_output,
                source_feedback_key=feedback_key,
                payload={
                    "origin": "meta_reflection",
                    "iteration": iteration,
                    "bad_case": bool(meta_reflection.get("bad_case", False)),
                    "high_frequency_doc_ids": meta_reflection.get("high_frequency_doc_ids", []),
                    "trace_section_id": section_id,
                    "trace_source_doc_id": source_doc_id,
                    "knowledge_category": "reasoning",
                    "optimization_target": "reviewer",
                },
            )
        self._upsert_section_example(
            session=session,
            project_id=project_id,
            run_id=run_id,
            section_id=section_id,
            doc_id=source_doc_id,
            example_type="evaluation_case",
            title=f"{section_id} 反馈评估样本",
            content=str(reference_example.get("content", "") or meta_reflection.get("reflection_summary", "") or "").strip() or str(review_output.get("section_summary", "") or "").strip(),
            input_payload={
                "section_id": section_id,
                "section_name": section_name,
                "source_doc_id": source_doc_id,
                "retrieved_materials": trace_payload.get("retrieved_materials", []) if isinstance(trace_payload.get("retrieved_materials", []), list) else [],
                "focus_points": trace_payload.get("section_packet", {}).get("focus_points", []) if isinstance(trace_payload.get("section_packet", {}), dict) else [],
            },
            output_payload={
                "reference_example": reference_example,
                "review_output": review_output,
                "meta_reflection": meta_reflection,
            },
            source_feedback_key=feedback_key,
            payload={
                "origin": "feedback_optimize_evaluation_case",
                "iteration": iteration,
                "knowledge_category": "evaluation",
                "optimization_target": "reviewer",
            },
        )
        return {
            "experiences": synced_experiences,
            "few_shot_example": saved_example,
        }

    def process_feedback_closed_loop(
        self,
        feedback_record: Dict[str, Any],
        run_trace: Dict[str, Any],
        run_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        section_id = str((feedback_record or {}).get("section_id", "") or "").strip()
        if self._is_p52_section_id(section_id):
            return {
                "success": False,
                "feedback_key": "",
                "analysis_result": None,
                "patch_result": None,
                "feedback_optimize_status": "skipped",
                "candidate_register_status": "skipped",
                "replay_status": "skipped",
                "error_message": "3.2.P.5.2 sections must use the dedicated P52 feedback optimize workflow",
            }
        return self.feedback_optimize_orchestrator.process_feedback_closed_loop(
            feedback_record=feedback_record,
            run_trace=run_trace,
            run_context=run_context,
        )

    def _process_feedback_closed_loop_impl(
        self,
        feedback_record: Dict[str, Any],
        run_trace: Dict[str, Any],
        run_context: Dict[str, Any],
    ) -> Dict[str, Any]:
        session = self.db_conn.get_session()
        try:
            run_id = str(feedback_record.get("run_id", "") or "")
            section_id = str(feedback_record.get("section_id", "") or "")
            context = self._load_feedback_section_context(
                session,
                run_id=run_id,
                section_id=section_id,
                run_context=run_context,
            )
            if not context:
                return {
                    "success": False,
                    "feedback_key": "",
                    "analysis_result": None,
                    "patch_result": None,
                    "feedback_optimize_status": "failed",
                    "candidate_register_status": "skipped",
                    "replay_status": "skipped",
                    "error_message": f"feedback context not found for run_id={run_id}, section_id={section_id}",
                }
            run = context.get("run")
            if run is None:
                return {
                    "success": False,
                    "feedback_key": "",
                    "analysis_result": None,
                    "patch_result": None,
                    "feedback_optimize_status": "failed",
                    "candidate_register_status": "skipped",
                    "replay_status": "skipped",
                    "error_message": f"run not found for run_id={run_id}",
                }
            section_item = context.get("section_item", {}) if isinstance(context.get("section_item", {}), dict) else {}
            standardized_output = context.get("standardized_output", {}) if isinstance(context.get("standardized_output", {}), dict) else {}
            trace_payload = context.get("trace_payload", {}) if isinstance(context.get("trace_payload", {}), dict) else {}
            historical_experience = context.get("historical_experience", []) if isinstance(context.get("historical_experience", []), list) else []
            section_rules = context.get("section_rules", []) if isinstance(context.get("section_rules", []), list) else []
            reference_examples = context.get("reference_examples", []) if isinstance(context.get("reference_examples", []), list) else []
            project = context.get("project")
            review_domain = str(context.get("review_domain", "") or "")
            product_type = str(context.get("product_type", "") or "")
            registration_class = str(context.get("registration_class", "") or "")
            source_doc_id = str(context.get("source_doc_id", "") or "")
            base_prompt_config = context.get("base_prompt_config", {}) if isinstance(context.get("base_prompt_config", {}), dict) else {}
            reference_example_input = feedback_record.get("reference_example", {}) if isinstance(feedback_record.get("reference_example", {}), dict) else {}
            saved_reference_example = {}
            if str(reference_example_input.get("content", "") or "").strip():
                saved_reference_example = self._upsert_section_example(
                    session=session,
                    project_id=run.project_id,
                    run_id=run_id,
                    section_id=section_id,
                    doc_id=source_doc_id,
                    example_type="reference",
                    title=str(reference_example_input.get("title", "") or f"{section_id} 参考示例").strip(),
                    content=str(reference_example_input.get("content", "") or "").strip(),
                    input_payload=reference_example_input.get("input", {}) if isinstance(reference_example_input.get("input", {}), dict) else {
                        "section_id": section_id,
                        "doc_id": source_doc_id,
                    },
                    output_payload=reference_example_input.get("expected_output", {}) if isinstance(reference_example_input.get("expected_output", {}), dict) else {},
                    payload={
                        "origin": "manual_feedback_reference",
                        "feedback_text": str(feedback_record.get("feedback_text", "") or ""),
                    },
                )
                reference_examples = [saved_reference_example] + [item for item in reference_examples if str(item.get("example_id", "") or "") != str(saved_reference_example.get("example_id", "") or "")]
            selected_reference_example = saved_reference_example or (reference_examples[0] if reference_examples else {})
            retrieved_materials_for_reflection = trace_payload.get("retrieved_materials", []) if isinstance(trace_payload.get("retrieved_materials", []), list) else []
            high_frequency_doc_ids = self._extract_high_frequency_doc_ids(retrieved_materials_for_reflection, limit=5)
            iteration = (
                session.query(func.count(PreReviewFeedbackAnalysisResult.id))
                .filter(
                    PreReviewFeedbackAnalysisResult.run_id == run_id,
                    PreReviewFeedbackAnalysisResult.section_id == section_id,
                )
                .scalar()
                or 0
            ) + 1
            feedback_key = f"{run_id}:{section_id}:{uuid.uuid4().hex[:8]}"
            feedback_signals = PreReviewAgentContractBuilder.feedback_signals(
                conclusion_feedback=str(feedback_record.get("conclusion_feedback", "") or ""),
                retrieval_feedback=str(feedback_record.get("retrieval_feedback", "") or ""),
                evidence_feedback=feedback_record.get("evidence_feedback", []) if isinstance(feedback_record.get("evidence_feedback", []), list) else [],
                knowledge_feedback=str(feedback_record.get("knowledge_feedback", "") or ""),
                rule_feedback=str(feedback_record.get("rule_feedback", "") or ""),
                reasoning_feedback=str(feedback_record.get("reasoning_feedback", "") or ""),
                issue_feedback=feedback_record.get("issue_feedback", []) if isinstance(feedback_record.get("issue_feedback", []), list) else [],
                missing_item_feedback=feedback_record.get("missing_item_feedback", {}) if isinstance(feedback_record.get("missing_item_feedback", {}), dict) else {},
                signal_summary=feedback_record.get("signal_summary", {}) if isinstance(feedback_record.get("signal_summary", {}), dict) else {},
            )
            analyzer_input = PreReviewAgentContractBuilder.feedback_analyzer_input(
                task_id=feedback_key,
                section_id=section_id,
                section_name=str(section_item.get("section_name", "") or ""),
                raw_text=str(section_item.get("content", "") or trace_payload.get("section_packet", {}).get("raw_text", "") or ""),
                focus_points=list(section_item.get("concern_points", []) or trace_payload.get("section_packet", {}).get("focus_points", []) or []),
                section_rules=section_rules,
                system_output=standardized_output,
                user_feedback_text=str(feedback_record.get("feedback_text", "") or ""),
                decision=str(feedback_record.get("decision", "") or ""),
                labels=list(feedback_record.get("labels", []) or []),
                feedback_signals=feedback_signals,
                issue_feedback=feedback_record.get("issue_feedback", []) if isinstance(feedback_record.get("issue_feedback", []), list) else [],
                missing_item_feedback=feedback_record.get("missing_item_feedback", {}) if isinstance(feedback_record.get("missing_item_feedback", {}), dict) else {},
                reference_inputs={
                    "retrieved_materials": trace_payload.get("retrieved_materials", []) if isinstance(trace_payload.get("retrieved_materials", []), list) else [],
                    "historical_experience": historical_experience,
                    "section_rules": section_rules,
                },
                reference_example=selected_reference_example,
                trace_payload=trace_payload,
                historical_experience=historical_experience,
            ).to_dict()
            analyzer_prompt_config = self._compose_runtime_prompt_config(
                project_id=run.project_id,
                task_type="feedback_analyzer",
                base_prompt_config=base_prompt_config,
                section_id=section_id,
                section_name=str(section_item.get("section_name", "") or ""),
                review_domain=review_domain,
                product_type=product_type,
                registration_class=registration_class,
            )
            analysis_result = self.feedback_agent.analyze_feedback(analyzer_input, prompt_config=analyzer_prompt_config)
            log_agent_flow(
                "feedback_analyzer",
                "done",
                run_id=run_id,
                section_id=section_id,
                primary_error_type=str((analysis_result or {}).get("primary_error_type", "") or ""),
                error_types=(analysis_result or {}).get("error_types", []) if isinstance((analysis_result or {}).get("error_types", []), list) else [],
            )
            optimizer_input = PreReviewAgentContractBuilder.feedback_optimizer_input(
                task_id=feedback_key,
                section_id=section_id,
                section_name=str(section_item.get("section_name", "") or ""),
                raw_text=analyzer_input["raw_text"],
                focus_points=analyzer_input["focus_points"],
                section_rules=section_rules,
                feedback_signals=feedback_signals,
                reference_examples=reference_examples,
                reference_example=selected_reference_example,
                issue_feedback=feedback_record.get("issue_feedback", []) if isinstance(feedback_record.get("issue_feedback", []), list) else [],
                missing_item_feedback=feedback_record.get("missing_item_feedback", {}) if isinstance(feedback_record.get("missing_item_feedback", {}), dict) else {},
                user_feedback_text=analyzer_input["user_feedback_text"],
                feedback_analysis_result=analysis_result,
                current_templates=self.runtime_context_service.build_template_snapshot(),
                current_prompt_rules=trace_payload.get("prompt_rules", {}) if isinstance(trace_payload.get("prompt_rules", {}), dict) else {},
                retrieved_materials=trace_payload.get("retrieved_materials", []) if isinstance(trace_payload.get("retrieved_materials", []), list) else [],
            ).to_dict()
            optimizer_prompt_config = self._compose_runtime_prompt_config(
                project_id=run.project_id,
                task_type="feedback_optimizer",
                base_prompt_config=base_prompt_config,
                section_id=section_id,
                section_name=str(section_item.get("section_name", "") or ""),
                review_domain=review_domain,
                product_type=product_type,
                registration_class=registration_class,
            )
            patch_result = self.feedback_agent.propose_patch(optimizer_input, prompt_config=optimizer_prompt_config)
            log_agent_flow(
                "feedback_optimizer",
                "done",
                run_id=run_id,
                section_id=section_id,
                patch_count=len((patch_result or {}).get("patches", [])) if isinstance((patch_result or {}).get("patches", []), list) else 0,
            )
            meta_reflection_input = PreReviewAgentContractBuilder.meta_reflection_input(
                section_id=section_id,
                section_name=str(section_item.get("section_name", "") or section_id).strip() or section_id,
                source_doc_id=source_doc_id,
                focus_points=analyzer_input.get("focus_points", []),
                section_rules=section_rules,
                retrieval_materials=retrieved_materials_for_reflection,
                review_output=standardized_output,
                reference_example=selected_reference_example,
                feedback_text=str(feedback_record.get("feedback_text", "") or ""),
                feedback_signals=feedback_signals,
                analysis_result=analysis_result,
                patch_result=patch_result,
                iteration=iteration,
                high_frequency_doc_ids=high_frequency_doc_ids,
            ).to_dict()
            meta_reflector_prompt_config = self._compose_runtime_prompt_config(
                project_id=run.project_id,
                task_type="meta_reflector",
                base_prompt_config=base_prompt_config,
                section_id=section_id,
                section_name=str(section_item.get("section_name", "") or ""),
                review_domain=review_domain,
                product_type=product_type,
                registration_class=registration_class,
            )
            meta_reflection = self.meta_reflection_agent.reflect(meta_reflection_input, prompt_config=meta_reflector_prompt_config)
            log_agent_flow(
                "meta_reflector",
                "done",
                run_id=run_id,
                section_id=section_id,
                bad_case=bool((meta_reflection or {}).get("bad_case", False)),
                experience_count=len((meta_reflection or {}).get("distilled_experiences", [])) if isinstance((meta_reflection or {}).get("distilled_experiences", []), list) else 0,
            )
            pipeline_trace = {
                "feedback_optimize_status": "completed",
                "candidate_register_status": "pending",
                "replay_status": "pending",
                "error_message": "",
            }
            feedback_projection = {}
            persisted_analysis = dict(analysis_result if isinstance(analysis_result, dict) else {})
            persisted_analysis["analysis_kind"] = "feedback_optimize"
            persisted_analysis["feedback_type"] = self._decision_to_feedback_type(
                decision=str(feedback_record.get("decision", "") or ""),
                labels=feedback_record.get("labels", []) if isinstance(feedback_record.get("labels", []), list) else [],
            )
            persisted_analysis["feedback_meta"] = {
                "chain_mode": str(feedback_record.get("chain_mode", "") or "feedback_optimize"),
                "decision": str(feedback_record.get("decision", "") or ""),
                "manual_modified": bool(feedback_record.get("manual_modified", False)),
                "diff_changed": bool(
                    (
                        (feedback_record.get("diff_result", {}) if isinstance(feedback_record.get("diff_result", {}), dict) else {})
                        .get("changed", False)
                    )
                ),
                "feedback_type": persisted_analysis["feedback_type"],
                "labels": feedback_record.get("labels", []) if isinstance(feedback_record.get("labels", []), list) else [],
                "issue_feedback": feedback_record.get("issue_feedback", []) if isinstance(feedback_record.get("issue_feedback", []), list) else [],
                "evidence_feedback": feedback_record.get("evidence_feedback", []) if isinstance(feedback_record.get("evidence_feedback", []), list) else [],
                "missing_item_feedback": feedback_record.get("missing_item_feedback", {}) if isinstance(feedback_record.get("missing_item_feedback", {}), dict) else {},
                "retrieval_feedback": str(feedback_record.get("retrieval_feedback", "") or ""),
                "conclusion_feedback": str(feedback_record.get("conclusion_feedback", "") or ""),
                "knowledge_feedback": str(feedback_record.get("knowledge_feedback", "") or ""),
                "rule_feedback": str(feedback_record.get("rule_feedback", "") or ""),
                "reasoning_feedback": str(feedback_record.get("reasoning_feedback", "") or ""),
                "signal_summary": feedback_record.get("signal_summary", {}) if isinstance(feedback_record.get("signal_summary", {}), dict) else {},
                "reference_example": selected_reference_example if isinstance(selected_reference_example, dict) else {},
                "feedback_optimize_status": pipeline_trace["feedback_optimize_status"],
                "candidate_register_status": pipeline_trace["candidate_register_status"],
                "replay_status": pipeline_trace["replay_status"],
                "error_message": pipeline_trace["error_message"],
            }
            persisted_analysis["analysis_result"] = analysis_result if isinstance(analysis_result, dict) else {}
            persisted_analysis["patch_result"] = patch_result if isinstance(patch_result, dict) else {}
            persisted_analysis["reference_example"] = selected_reference_example if isinstance(selected_reference_example, dict) else {}
            persisted_analysis["meta_reflection"] = meta_reflection if isinstance(meta_reflection, dict) else {}
            persisted_analysis["feedback_optimize_status"] = pipeline_trace["feedback_optimize_status"]
            persisted_analysis["candidate_register_status"] = pipeline_trace["candidate_register_status"]
            persisted_analysis["replay_status"] = pipeline_trace["replay_status"]
            persisted_analysis["error_message"] = pipeline_trace["error_message"]
            persisted_analysis["feedback_projection"] = feedback_projection
            for exp in analysis_result.get("new_experience", []) if isinstance(analysis_result.get("new_experience", []), list) else []:
                if not isinstance(exp, dict):
                    continue
                applicable_scope = str(exp.get("applicable_scope", "") or section_id).strip() or section_id
                payload = self._build_feedback_knowledge_payload(
                    origin="feedback_analysis",
                    feedback_key=feedback_key,
                    run_id=run_id,
                    section_id=section_id,
                    section_name=str(section_item.get("section_name", "") or section_id).strip() or section_id,
                    source_doc_id=source_doc_id,
                    applicable_scope=applicable_scope,
                    content=str(exp.get("content", "") or "").strip(),
                    default_scope_type="product_type",
                    base_item=exp,
                    analysis_result=analysis_result if isinstance(analysis_result, dict) else {},
                    patch_result=patch_result if isinstance(patch_result, dict) else {},
                    extra_payload={
                        "signal_summary": feedback_record.get("signal_summary", {}) if isinstance(feedback_record.get("signal_summary", {}), dict) else {},
                    },
                )
                scope_type = str(payload.get("scope_type", "") or "product_type").strip() or "product_type"
                session.add(
                    PreReviewExperienceMemory(
                        experience_id=f"exp_{uuid.uuid4().hex[:12]}",
                        scope_type=scope_type,
                        scope_key=applicable_scope,
                        experience_type=str(payload.get("experience_type", "") or "review_rule").strip() or "review_rule",
                        content=str(exp.get("content", "") or "").strip(),
                        source_feedback_ids=json.dumps([feedback_key], ensure_ascii=False),
                        trigger_conditions=json.dumps([f"section_id == '{section_id}'"], ensure_ascii=False),
                        usage_count=0,
                        success_count=0,
                        status="active",
                        payload_json=json.dumps(payload, ensure_ascii=False),
                        create_time=self._now(),
                        update_time=self._now(),
                    )
                )
            patch_version = 1
            for patch in patch_result.get("patches", []) if isinstance(patch_result.get("patches", []), list) else []:
                if not isinstance(patch, dict):
                    continue
                session.add(
                    PreReviewPatchRegistry(
                        patch_id=str(patch.get("patch_id", "") or f"patch_{uuid.uuid4().hex[:12]}"),
                        run_id=run_id,
                        section_id=section_id or None,
                        patch_type=str(patch.get("patch_type", "") or ""),
                        target_agent=str(patch.get("target_agent", "") or ""),
                        target_scope=str(patch.get("target_scope", "") or section_id),
                        trigger_condition=str(patch.get("trigger_condition", "") or ""),
                        patch_content=str(patch.get("patch_content", "") or "").strip(),
                        source_feedback_key=feedback_key,
                        status=str(patch.get("status", "candidate") or "candidate"),
                        version=patch_version,
                        payload_json=json.dumps(patch, ensure_ascii=False),
                        create_time=self._now(),
                        update_time=self._now(),
                    )
                )
                patch_version += 1
            synced_patch_rules = []
            synced_section_rules = []
            meta_reflection_result = {"experiences": [], "few_shot_example": {}}
            try:
                synced_patch_rules = self.prompt_rule_service.sync_patch_rules(
                    session=session,
                    project_id=run.project_id,
                    section_id=section_id,
                    review_domain=review_domain,
                    product_type=product_type,
                    registration_class=registration_class,
                    source_feedback_key=feedback_key,
                    patches=patch_result.get("patches", []) if isinstance(patch_result, dict) else [],
                )
                pipeline_trace["candidate_register_status"] = "completed" if synced_patch_rules else "skipped"
            except Exception as sync_exc:
                pipeline_trace["candidate_register_status"] = "failed"
                pipeline_trace["error_message"] = str(sync_exc)
            try:
                synced_section_rules = self._sync_feedback_section_rules(
                    session=session,
                    project_id=run.project_id,
                    section_id=section_id,
                    section_name=str(section_item.get("section_name", "") or section_id).strip() or section_id,
                    feedback_key=feedback_key,
                    analysis_result=analysis_result if isinstance(analysis_result, dict) else {},
                    patch_result=patch_result if isinstance(patch_result, dict) else {},
                )
            except Exception as sync_exc:
                error_parts = [str(pipeline_trace.get("error_message", "") or "").strip(), str(sync_exc or "").strip()]
                pipeline_trace["error_message"] = " | ".join([part for part in error_parts if part])
            try:
                meta_reflection_result = self._persist_meta_reflection_outputs(
                    session=session,
                    project_id=run.project_id,
                    run_id=run_id,
                    source_doc_id=source_doc_id,
                    section_id=section_id,
                    section_name=str(section_item.get("section_name", "") or section_id).strip() or section_id,
                    feedback_key=feedback_key,
                    meta_reflection=meta_reflection if isinstance(meta_reflection, dict) else {},
                    reference_example=selected_reference_example if isinstance(selected_reference_example, dict) else {},
                    review_output=standardized_output if isinstance(standardized_output, dict) else {},
                    trace_payload=trace_payload if isinstance(trace_payload, dict) else {},
                    iteration=iteration,
                )
            except Exception as reflection_exc:
                error_parts = [str(pipeline_trace.get("error_message", "") or "").strip(), str(reflection_exc or "").strip()]
                pipeline_trace["error_message"] = " | ".join([part for part in error_parts if part])
            feedback_projection = self._build_feedback_projection(
                analysis_result=analysis_result if isinstance(analysis_result, dict) else {},
                patch_result=patch_result if isinstance(patch_result, dict) else {},
                meta_reflection_result=meta_reflection_result if isinstance(meta_reflection_result, dict) else {},
            )
            persisted_analysis["feedback_projection"] = feedback_projection
            audit_bundle = self.feedback_optimize_orchestrator.build_feedback_stage_audit_bundle(
                run_id=run_id,
                section_id=section_id,
                source_doc_id=source_doc_id,
                feedback_key=feedback_key,
                analyzer_input=analyzer_input,
                analysis_result=analysis_result if isinstance(analysis_result, dict) else {},
                analyzer_prompt_config=analyzer_prompt_config,
                section_rules=section_rules,
                optimizer_input=optimizer_input,
                patch_result=patch_result if isinstance(patch_result, dict) else {},
                optimizer_prompt_config=optimizer_prompt_config,
                meta_reflection_input=meta_reflection_input if isinstance(meta_reflection_input, dict) else None,
                meta_reflection=meta_reflection if isinstance(meta_reflection, dict) else {},
                meta_reflector_prompt_config=meta_reflector_prompt_config,
                meta_reflection_result=meta_reflection_result if isinstance(meta_reflection_result, dict) else {},
            )
            feedback_audit_rows = audit_bundle.get("audit_rows", [])
            persisted_analysis["feedback_meta"]["candidate_register_status"] = pipeline_trace["candidate_register_status"]
            persisted_analysis["feedback_meta"]["error_message"] = pipeline_trace["error_message"]
            persisted_analysis["feedback_meta"]["optimize_evaluation"] = {}
            persisted_analysis["candidate_register_status"] = pipeline_trace["candidate_register_status"]
            persisted_analysis["error_message"] = pipeline_trace["error_message"]
            session.add(
                PreReviewFeedbackAnalysisResult(
                    feedback_key=feedback_key,
                    run_id=run_id,
                    section_id=section_id or None,
                    analysis_json=self._serialize_feedback_analysis_payload(persisted_analysis),
                    create_time=self._now(),
                )
            )
            for audit_row in feedback_audit_rows:
                session.add(audit_row)
            self._refresh_accuracy(session, run_id)
            session.commit()
            refreshed_optimizer_prompt_config = self._compose_runtime_prompt_config(
                project_id=run.project_id,
                task_type="feedback_optimizer",
                base_prompt_config=base_prompt_config,
                section_id=section_id,
                section_name=str(section_item.get("section_name", "") or ""),
                review_domain=review_domain,
                product_type=product_type,
                registration_class=registration_class,
            )
            return {
                "success": True,
                "feedback_key": feedback_key,
                "analysis_result": analysis_result,
                "patch_result": patch_result,
                "meta_reflection": meta_reflection,
                "reference_example": selected_reference_example,
                "project_patch_rules": synced_patch_rules,
                "project_section_rules": synced_section_rules,
                "meta_reflection_experiences": meta_reflection_result.get("experiences", []),
                "few_shot_example": meta_reflection_result.get("few_shot_example", {}),
                "feedback_projection": feedback_projection,
                "active_rules": {
                    "feedback_analyzer": ((analyzer_prompt_config.get("prompt_bundle", {}) if isinstance(analyzer_prompt_config.get("prompt_bundle", {}), dict) else {}).get("active_rules", {}) or {}).get("feedback_analyzer", []),
                    "feedback_optimizer": ((refreshed_optimizer_prompt_config.get("prompt_bundle", {}) if isinstance(refreshed_optimizer_prompt_config.get("prompt_bundle", {}), dict) else {}).get("active_rules", {}) or {}).get("feedback_optimizer", []),
                    "meta_reflector": ((meta_reflector_prompt_config.get("prompt_bundle", {}) if isinstance(meta_reflector_prompt_config.get("prompt_bundle", {}), dict) else {}).get("active_rules", {}) or {}).get("meta_reflector", []),
                },
                "feedback_optimize_status": pipeline_trace["feedback_optimize_status"],
                "candidate_register_status": pipeline_trace["candidate_register_status"],
                "replay_status": pipeline_trace["replay_status"],
                "error_message": pipeline_trace["error_message"],
                "io_contract": audit_bundle.get("io_contract", {}),
            }
        except Exception as exc:
            session.rollback()
            return {
                "success": False,
                "feedback_key": "",
                "analysis_result": None,
                "patch_result": None,
                "feedback_projection": {},
                "feedback_optimize_status": "failed",
                "candidate_register_status": "skipped",
                "replay_status": "skipped",
                "error_message": str(exc) if isinstance(exc, (LLMContextError, LLMExecutionError)) else "反馈优化未完成，请检查服务状态后重试。",
                "error": exc.as_dict() if isinstance(exc, (LLMContextError, LLMExecutionError)) else {'code': 'feedback_processing_failed'},
            }
        finally:
            session.close()

    def get_feedback_stats(self, run_id: str, section_id: str = "") -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        session = self.db_conn.get_session()
        try:
            run = session.query(PreReviewRun).filter(PreReviewRun.run_id == run_id).first()
            if run is None:
                return False, "run not found", None

            feedback_query = session.query(PreReviewFeedback).filter(PreReviewFeedback.run_id == run_id)
            if section_id:
                feedback_query = feedback_query.filter(PreReviewFeedback.section_id == section_id)
            feedback_rows = feedback_query.order_by(
                PreReviewFeedback.create_time.desc(),
                PreReviewFeedback.id.desc(),
            ).all()
            analysis_payloads = self._load_feedback_analysis_payloads(
                session,
                run_id=run_id,
                section_id=section_id,
            )
            stats = self._compute_run_metrics(
                session,
                run_id=run_id,
                section_id=section_id,
                feedback_rows=feedback_rows,
                analysis_payloads=analysis_payloads,
            )
            history = self._build_feedback_history(
                session,
                run_id=run_id,
                section_id=section_id,
                feedback_rows=feedback_rows,
                analysis_payloads=analysis_payloads,
            )
            examples = self._load_section_reference_examples(session, project_id=str(run.project_id or ""), section_id=section_id, limit=12) if section_id else []
            return True, "success", {
                **stats,
                "history": history,
                "reference_examples": examples,
            }
        except Exception as e:
            return False, f"get feedback stats failed: {str(e)}", None
        finally:
            session.close()
