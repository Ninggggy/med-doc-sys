from agent.agent_backend.services.filing_change_result_service import result_from_rows
import json
import os
import hashlib
import shutil
import tempfile
import threading
import time
import uuid
import zipfile
import difflib
import re
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy import func

from agent.agent_backend.config.settings import settings
from agent.agent_backend.llm.errors import LLMContextError, LLMExecutionError
from agent.agent_backend.database.mysql.db_model import (
    FilingChangeApplicationForm,
    FilingChangeProject,
    FilingChangeReferenceMaterial,
    FilingChangeReviewReport,
    FilingChangeReviewResult,
    FilingChangeReviewRun,
    FilingChangeSubmissionFile,
    RuntimeTask,
)
from agent.agent_backend.database.mysql.mysql_conn import MysqlConnection
from agent.agent_backend.services.filing_change_form_parser_service import FilingChangeFormParserService
from agent.agent_backend.services.filing_change_material_service import FilingChangeMaterialService
from agent.agent_backend.services.filing_parse_outcome import outcome, form_outcome, failure, log_failure, log_diagnostics, parse_task_id, ParseFailure
from agent.agent_backend.services.filing_change_quality_check_service import FilingChangeQualityCheckService
from agent.agent_backend.services.filing_change_report_service import FilingChangeReportService
from agent.agent_backend.services.filing_change_review_orchestrator import FilingChangeReviewOrchestrator
from agent.agent_backend.services.filing_change_rule_service import FilingChangeRuleService
from agent.agent_backend.services.filing_change_stability_service import FilingChangeStabilityService


class _CrossProcessManifestLock:
    """同时保护线程和多 Worker 进程的可重入文件锁。"""

    def __init__(self, lock_path: Path) -> None:
        self.lock_path = Path(lock_path)
        self._thread_lock = threading.RLock()
        self._local = threading.local()

    def __enter__(self, blocking=True):
        if not self._thread_lock.acquire(blocking=blocking):
            return None
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
                msvcrt.locking(fd, msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            self._local.fd = fd
            self._local.depth = 1
            return self
        except Exception as exc:
            if fd is not None:
                os.close(fd)
            self._thread_lock.release()
            if not blocking and isinstance(exc, (BlockingIOError, PermissionError)):
                return None
            raise

    @contextmanager
    def try_lock(self):
        acquired = self.__enter__(blocking=False)
        try:
            yield acquired is not None
        finally:
            if acquired is not None:
                self.__exit__(None, None, None)

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


class FilingChangeReviewService:
    _MANIFEST_LOCKS: Dict[str, _CrossProcessManifestLock] = {}
    _MANIFEST_LOCKS_GUARD = threading.Lock()
    APPLICATION_FORM_EXTENSIONS = {".doc", ".docx", ".pdf"}
    SUBMISSION_CATALOG = [
        {"code": "application_info", "label": "申请信息", "required_level": "required", "children": []},
        {"code": "catalog", "label": "目录", "required_level": "recommended", "children": []},
        {"code": "1", "label": "1 药品批准证明文件", "required_level": "required", "children": []},
        {"code": "2", "label": "2 证明性文件", "required_level": "required", "children": []},
        {"code": "3", "label": "3 检查检验相关信息", "required_level": "recommended", "children": []},
        {"code": "4", "label": "4 质量标准及说明书等", "required_level": "required", "children": []},
        {"code": "5", "label": "5 药学研究资料", "required_level": "required", "children": []},
        {"code": "6", "label": "6 药理毒理研究资料", "required_level": "optional_na", "children": []},
        {"code": "7", "label": "7 临床研究资料", "required_level": "optional_na", "children": []},
        {"code": "8", "label": "8 其他资料", "required_level": "optional", "children": []},
        {"code": "generic_name_approval", "label": "通用名称核准资料", "required_level": "conditional", "children": []},
    ]
    REFERENCE_DRUG_CATEGORIES = ["中药", "化学药品", "生物制品", "原料药", "通用资料"]
    REFERENCE_MATERIAL_TYPES = [
        "法律法规类",
        "技术指导原则类",
        "申报资料要求类",
        "稳定性评价资料类",
        "质量标准与检验资料类",
        "历史案例类",
        "共性问题类",
        "报告模板类",
    ]
    REFERENCE_CHANGE_ITEMS = [
        "变更有效期和贮藏条件",
        "延长药品有效期",
        "质量标准比对",
        "稳定性研究评价",
        "资料完整性审查",
        "审评报告生成",
    ]

    def __init__(self) -> None:
        self.db_conn = MysqlConnection()
        self.root_dir = Path(settings.upload_dir).resolve().parent / "filing_change_review"
        self.form_parser = FilingChangeFormParserService()
        self.material_service = FilingChangeMaterialService()
        self.rule_service = FilingChangeRuleService(self.db_conn)
        self.stability_service = FilingChangeStabilityService()
        self.quality_service = FilingChangeQualityCheckService()
        self.report_service = FilingChangeReportService(self.root_dir)
        self.orchestrator = FilingChangeReviewOrchestrator(
            form_parser=self.form_parser,
            quality_service=self.quality_service,
            stability_service=self.stability_service,
        )
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        for p in [
            self.root_dir / "projects",
            self.root_dir / "reference_materials",
            self.root_dir / "rules",
            self.root_dir / "templates",
        ]:
            p.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _now() -> datetime:
        return datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False)

    @staticmethod
    def _safe_json_load(value: Any, default: Any) -> Any:
        if value is None:
            return default
        if isinstance(value, (dict, list)):
            return value
        try:
            return json.loads(str(value))
        except Exception:
            return default

    def _project_dir(self, project_id: str) -> Path:
        p = self._project_path(project_id)
        for child in ["application_form", "submissions", "parsed", "stability", "reports", "charts"]:
            (p / child).mkdir(parents=True, exist_ok=True)
        return p

    def _project_path(self, project_id: str) -> Path:
        """返回项目路径但不创建目录，避免查询已删除项目时又产生空目录。"""
        return self.root_dir / "projects" / project_id

    def _submission_manifest_path(self, project_id: str) -> Path:
        return self._project_path(project_id) / "submissions" / "_manifest.json"

    def _reference_manifest_path(self) -> Path:
        return self.root_dir / "reference_materials" / "_manifest.json"

    @classmethod
    def _manifest_lock(cls, path: Path) -> _CrossProcessManifestLock:
        key = str(Path(path).resolve())
        with cls._MANIFEST_LOCKS_GUARD:
            lock = cls._MANIFEST_LOCKS.get(key)
            if lock is None:
                lock_root = next(
                    (parent for parent in [Path(path).parent, *Path(path).parents] if parent.name == "filing_change_review"),
                    Path(path).parent,
                )
                lock_name = hashlib.sha256(key.encode("utf-8")).hexdigest() + ".lock"
                lock = _CrossProcessManifestLock(lock_root / ".manifest_locks" / lock_name)
                cls._MANIFEST_LOCKS[key] = lock
            return lock

    def _submission_manifest_lock(self, project_id: str) -> _CrossProcessManifestLock:
        return self._manifest_lock(self._submission_manifest_path(project_id))

    def _reference_manifest_lock(self) -> _CrossProcessManifestLock:
        return self._manifest_lock(self._reference_manifest_path())

    @staticmethod
    def _atomic_write_bytes(path: Path, content: bytes) -> None:
        """在同一目录写临时文件后原子替换，避免 Manifest/解析文件半写状态。"""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(str(temp_path), str(path))
        except Exception:
            temp_path.unlink(missing_ok=True)
            raise

    @classmethod
    def _atomic_write_json(cls, path: Path, data: Dict[str, Any]) -> None:
        cls._atomic_write_bytes(path, json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))

    @staticmethod
    def _snapshot_file(path: Path) -> Tuple[bool, bytes]:
        path = Path(path)
        if not path.exists():
            return False, b""
        return True, path.read_bytes()

    @classmethod
    def _restore_file_snapshot(cls, path: Path, snapshot: Tuple[bool, bytes]) -> None:
        existed, content = snapshot
        if existed:
            cls._atomic_write_bytes(path, content)
        else:
            Path(path).unlink(missing_ok=True)

    @staticmethod
    def _managed_file_path(raw_path: Any, managed_root: Path) -> Optional[Path]:
        """仅返回位于受管根目录下的文件路径。

        数据库中的空路径、相对路径、越界路径及指向目录的路径均不参与隔离，
        避免 Path("") 退化为当前工作目录，也避免历史异常数据越出业务目录。
        """
        value = str(raw_path or "").strip()
        if not value:
            return None
        candidate = Path(value)
        if not candidate.is_absolute():
            return None
        try:
            root = Path(managed_root).resolve(strict=False)
            resolved = candidate.resolve(strict=False)
        except (OSError, RuntimeError, ValueError):
            return None
        if resolved == root or root not in resolved.parents:
            return None
        try:
            if resolved.exists() and not resolved.is_file():
                return None
        except OSError:
            return None
        return resolved

    def _snapshot_report_files(self, project_id: str, run_id: str) -> Dict[Path, Tuple[bool, bytes]]:
        report_dir = self._project_path(project_id) / "reports"
        return {
            report_dir / f"{run_id}{suffix}": self._snapshot_file(report_dir / f"{run_id}{suffix}")
            for suffix in (".md", ".docx", ".txt")
        }

    def _restore_report_file_snapshots(self, snapshots: Dict[Path, Tuple[bool, bytes]]) -> None:
        for path, snapshot in snapshots.items():
            self._restore_file_snapshot(path, snapshot)

    @classmethod
    def _install_file_payloads(cls, payloads: List[Tuple[Path, bytes]]) -> List[Dict[str, Any]]:
        """安装一组文件并保留旧版备份，供数据库事务失败时回滚。"""
        state: List[Dict[str, Any]] = []
        try:
            for raw_path, content in payloads:
                path = Path(raw_path)
                path.parent.mkdir(parents=True, exist_ok=True)
                token = uuid.uuid4().hex
                temp_path = path.parent / f".{path.name}.{token}.pending"
                backup_path = path.parent / f".{path.name}.{token}.backup"
                item = {
                    "path": path,
                    "temp_path": temp_path,
                    "backup_path": backup_path,
                    "had_backup": False,
                    "installed": False,
                }
                state.append(item)
                cls._atomic_write_bytes(temp_path, bytes(content))
                if path.exists():
                    os.replace(str(path), str(backup_path))
                    item["had_backup"] = True
                os.replace(str(temp_path), str(path))
                item["installed"] = True
            return state
        except Exception:
            cls._rollback_file_payloads(state)
            raise

    @staticmethod
    def _rollback_file_payloads(state: List[Dict[str, Any]]) -> None:
        for item in reversed(state):
            path = Path(item["path"])
            temp_path = Path(item["temp_path"])
            backup_path = Path(item["backup_path"])
            if bool(item.get("installed")):
                path.unlink(missing_ok=True)
            if bool(item.get("had_backup")) and backup_path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                os.replace(str(backup_path), str(path))
            temp_path.unlink(missing_ok=True)

    @staticmethod
    def _finalize_file_payloads(state: List[Dict[str, Any]]) -> None:
        for item in state:
            Path(item["temp_path"]).unlink(missing_ok=True)
            Path(item["backup_path"]).unlink(missing_ok=True)

    def _quarantine_paths(self, paths: List[Path], label: str) -> Tuple[Path, List[Tuple[Path, Path]]]:
        quarantine_dir = self.root_dir / ".trash" / f"{label}-{uuid.uuid4().hex}"
        moved: List[Tuple[Path, Path]] = []
        if label == "application-form" and paths:
            # 在第一份原件移动前写入隔离位置；进程退出也能找到旧有效版本。
            project_id = Path(paths[0]).parent.parent.name
            manifest = self._load_submission_manifest(project_id)
            transaction = manifest.get('application_form_transaction')
            if transaction:
                transaction['quarantine_dir'] = str(quarantine_dir.relative_to(self.root_dir))
                transaction['moves'] = [
                    [str(Path(source).relative_to(self.root_dir)),
                     str((quarantine_dir / f'{index:04d}_{Path(source).name}').relative_to(self.root_dir))]
                    for index, source in enumerate(paths) if Path(source).exists()
                ]
                self._save_submission_manifest(project_id, manifest)
        try:
            for source in paths:
                source = Path(source)
                if not source.exists():
                    continue
                quarantine_dir.mkdir(parents=True, exist_ok=True)
                target = quarantine_dir / f"{len(moved):04d}_{source.name}"
                os.replace(str(source), str(target))
                moved.append((source, target))
            return quarantine_dir, moved
        except Exception:
            self._restore_quarantined_paths(moved)
            if quarantine_dir.exists():
                shutil.rmtree(quarantine_dir, ignore_errors=True)
            raise

    @staticmethod
    def _restore_quarantined_paths(moved: List[Tuple[Path, Path]]) -> None:
        for source, target in reversed(moved):
            if not target.exists():
                continue
            source.parent.mkdir(parents=True, exist_ok=True)
            os.replace(str(target), str(source))

    @staticmethod
    def _finalize_quarantine(quarantine_dir: Path) -> None:
        if quarantine_dir.exists():
            shutil.rmtree(quarantine_dir)
        parent = quarantine_dir.parent
        if parent.exists() and not any(parent.iterdir()):
            parent.rmdir()

    def _load_submission_manifest(self, project_id: str) -> Dict[str, Any]:
        path = self._submission_manifest_path(project_id)
        with self._manifest_lock(path):
            if not path.exists():
                return {"doc_meta": {}, "category_meta": {}}
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    return {"doc_meta": {}, "category_meta": {}}
                data.setdefault("doc_meta", {})
                data.setdefault("category_meta", {})
                return data
            except Exception:
                return {"doc_meta": {}, "category_meta": {}}

    def _save_submission_manifest(self, project_id: str, data: Dict[str, Any]) -> None:
        path = self._submission_manifest_path(project_id)
        with self._manifest_lock(path):
            self._atomic_write_json(path, data)

    def _load_reference_manifest(self) -> Dict[str, Any]:
        path = self._reference_manifest_path()
        with self._manifest_lock(path):
            if not path.exists():
                return {"doc_meta": {}}
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    return {"doc_meta": {}}
                data.setdefault("doc_meta", {})
                return data
            except Exception:
                return {"doc_meta": {}}

    def _save_reference_manifest(self, data: Dict[str, Any]) -> None:
        path = self._reference_manifest_path()
        with self._manifest_lock(path):
            self._atomic_write_json(path, data)

    @staticmethod
    def _build_catalog_index(catalog: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        for node in catalog:
            code = str(node.get("code", "")).strip()
            if code:
                out[code] = node
            for child in node.get("children", []) or []:
                c_code = str(child.get("code", "")).strip()
                if c_code:
                    out[c_code] = child
        return out

    def _catalog_index(self) -> Dict[str, Dict[str, Any]]:
        return self._build_catalog_index(self.SUBMISSION_CATALOG)

    def _auto_classify_submission(self, file_name: str) -> Tuple[str, str, List[str]]:
        name = str(file_name or "").lower()
        mapping = [
            (["申请表", "application", "补充申请"], ("application_info", "", ["申请信息"])),
            (["目录", "contents"], ("catalog", "", ["目录"])),
            (["批准", "批件", "注册证"], ("1", "", ["批准证明文件"])),
            (["许可证", "营业执照", "授权", "公证"], ("2", "", ["证明性文件"])),
            (["检验", "检查", "研制情况", "生产情况", "报告"], ("3", "", ["检验报告"])),
            (["质量标准", "说明书", "标签"], ("4", "", ["质量标准"])),
            (["稳定性", "accelerated", "long-term"], ("5", "", ["稳定性研究资料"])),
            (["工艺验证"], ("5", "", ["工艺验证资料"])),
            (["处方", "工艺对比"], ("5", "", ["处方与生产工艺对比"])),
            (["原辅料", "包材", "供应商"], ("5", "", ["原辅料控制资料"])),
            (["质量对比"], ("5", "", ["质量对比资料"])),
            (["变更原因"], ("5", "", ["变更原因及具体情况说明"])),
            (["药理", "毒理"], ("6", "", ["药理毒理"])),
            (["临床", "试验数据库"], ("7", "", ["临床"])),
        ]
        for keywords, result in mapping:
            if any(k in name for k in keywords):
                return result
        return "8", "", []

    @staticmethod
    def _extract_markdown_table_blocks(text: str) -> List[str]:
        blocks: List[str] = []
        current: List[str] = []
        for raw_line in str(text or "").splitlines():
            line = str(raw_line or "").rstrip()
            if "|" in line:
                current.append(line)
                continue
            if current:
                if len(current) >= 2:
                    blocks.append("\n".join(current).strip())
                current = []
        if current and len(current) >= 2:
            blocks.append("\n".join(current).strip())
        return blocks

    @staticmethod
    def _parse_markdown_table_block(block: str) -> Dict[str, Any]:
        rows: List[List[str]] = []
        for raw_line in str(block or "").splitlines():
            line = str(raw_line or "").strip()
            if not line or "|" not in line:
                continue
            parts = [part.strip() for part in line.strip("|").split("|")]
            if parts:
                rows.append(parts)
        if len(rows) < 2:
            return {"headers": [], "rows": []}
        headers = rows[0]
        data_start = 1
        if len(rows) >= 2 and all(re.fullmatch(r"[:\- ]+", cell or "") for cell in rows[1]):
            data_start = 2
        parsed_rows: List[List[str]] = []
        for row in rows[data_start:]:
            normalized = list(row[: len(headers)])
            if len(normalized) < len(headers):
                normalized.extend([""] * (len(headers) - len(normalized)))
            parsed_rows.append(normalized)
        return {"headers": headers, "rows": parsed_rows}

    @staticmethod
    def _looks_like_stability_content(file_name: str, parsed_chunks: List[Dict[str, Any]]) -> bool:
        low_name = str(file_name or "").lower()
        if any(token in low_name for token in ["稳定性", "stability", "long-term", "accelerated"]):
            return True
        for item in parsed_chunks or []:
            if not isinstance(item, dict):
                continue
            merged = "\n".join(
                [
                    str(item.get("section_name", "") or ""),
                    " ".join([str(x or "") for x in (item.get("section_path") or [])]),
                    str(item.get("text", "") or ""),
                ]
            )
            if any(token in merged for token in ["稳定性", "长期试验", "加速试验", "中间条件", "留样"]):
                return True
        return False

    @staticmethod
    def _required_flag_by_level(level: str) -> bool:
        return str(level) in {"required", "recommended"}

    def create_project(self, payload: Dict[str, Any]) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        project_name = str(payload.get("project_name", "")).strip()
        if not project_name:
            return False, "project_name is required", None
        now = self._now()
        project_id = f"fcrp_{uuid.uuid4().hex[:16]}"
        task_type = str(payload.get("task_type", "extend_validity_period")).strip() or "extend_validity_period"
        row = FilingChangeProject(
            project_id=project_id,
            project_name=project_name,
            registration_category=str(payload.get("registration_category", "")).strip() or "化学药品",
            registration_classification=str(payload.get("registration_classification", "")).strip() or "其他",
            task_type=task_type,
            review_status="not_started",
            report_status="not_generated",
            remark=str(payload.get("remark", "")).strip(),
            created_at=now,
            updated_at=now,
            deleted=False,
        )
        session = self.db_conn.get_session()
        try:
            session.add(row)
            session.commit()
            self._project_dir(project_id)
            return True, "success", self.get_project_detail(project_id)[2]
        except Exception as exc:
            session.rollback()
            return False, str(exc), None
        finally:
            session.close()

    def list_projects(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        page = max(int(payload.get("page", 1) or 1), 1)
        page_size = min(max(int(payload.get("page_size", 20) or 20), 1), 200)
        keyword = str(payload.get("project_name", "")).strip()
        review_status = str(payload.get("review_status", "")).strip()
        session = self.db_conn.get_session()
        try:
            query = session.query(FilingChangeProject).filter(FilingChangeProject.deleted == False)
            if keyword:
                query = query.filter(FilingChangeProject.project_name.like(f"%{keyword}%"))
            if review_status:
                query = query.filter(FilingChangeProject.review_status == review_status)
            total = query.count()
            rows = (
                query.order_by(FilingChangeProject.updated_at.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
                .all()
            )
            project_ids = [str(row.project_id or "").strip() for row in rows if str(row.project_id or "").strip()]
            project_stats: Dict[str, Dict[str, Any]] = {
                project_id: {"submission_count": 0, "application_form_status": "not_filled"}
                for project_id in project_ids
            }
            if project_ids:
                submission_count_rows = (
                    session.query(
                        FilingChangeSubmissionFile.project_id,
                        func.count(FilingChangeSubmissionFile.id),
                    )
                    .filter(FilingChangeSubmissionFile.project_id.in_(project_ids))
                    .group_by(FilingChangeSubmissionFile.project_id)
                    .all()
                )
                for project_id, count in submission_count_rows:
                    project_stats.setdefault(str(project_id or "").strip(), {})["submission_count"] = int(count or 0)
                form_rows = (
                    session.query(FilingChangeApplicationForm)
                    .filter(FilingChangeApplicationForm.project_id.in_(project_ids))
                    .order_by(
                        FilingChangeApplicationForm.project_id.asc(),
                        FilingChangeApplicationForm.updated_at.desc(),
                        FilingChangeApplicationForm.id.desc(),
                    )
                    .all()
                )
                seen_form_projects = set()
                for form_row in form_rows:
                    project_id = str(form_row.project_id or "").strip()
                    if not project_id or project_id in seen_form_projects:
                        continue
                    seen_form_projects.add(project_id)
                    project_stats.setdefault(project_id, {})["application_form_status"] = (
                        "imported" if form_row.original_file_id else "filled"
                    )
            data = [
                self._serialize_project(
                    row,
                    session,
                    prefetched_stats=project_stats.get(str(row.project_id or "").strip()),
                )
                for row in rows
            ]
            return {"list": data, "total": total, "page": page, "page_size": page_size}
        finally:
            session.close()

    def _serialize_project(
        self,
        row: FilingChangeProject,
        session,
        prefetched_stats: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if prefetched_stats is None:
            submission_count = (
                session.query(FilingChangeSubmissionFile)
                .filter(FilingChangeSubmissionFile.project_id == row.project_id)
                .count()
            )
            form_row = (
                session.query(FilingChangeApplicationForm)
                .filter(FilingChangeApplicationForm.project_id == row.project_id)
                .order_by(FilingChangeApplicationForm.updated_at.desc())
                .first()
            )
            form_status = "not_filled"
            if form_row:
                form_status = "imported" if form_row.original_file_id else "filled"
        else:
            submission_count = int(prefetched_stats.get("submission_count", 0) or 0)
            form_status = str(prefetched_stats.get("application_form_status", "not_filled") or "not_filled")
        return {
            "project_id": row.project_id,
            "project_name": row.project_name,
            "registration_category": row.registration_category,
            "registration_classification": row.registration_classification,
            "task_type": row.task_type,
            "review_status": row.review_status,
            "report_status": row.report_status,
            "application_form_status": form_status,
            "submission_count": submission_count,
            "remark": row.remark or "",
            "created_at": row.created_at.strftime("%Y-%m-%d %H:%M:%S") if row.created_at else "",
            "updated_at": row.updated_at.strftime("%Y-%m-%d %H:%M:%S") if row.updated_at else "",
        }

    def delete_project(self, project_id: str) -> Tuple[bool, str]:
        # 先把项目目录原子移到隔离区，再提交数据库硬删除。
        # 数据库失败时可将目录原路径恢复；提交成功后才永久清理隔离区。
        with self._submission_manifest_lock(project_id):
            session = self.db_conn.get_session()
            quarantine_dir = self.root_dir / ".trash" / f"project-{uuid.uuid4().hex}"
            moved: List[Tuple[Path, Path]] = []
            committed = False
            try:
                row = (
                    session.query(FilingChangeProject)
                    .filter(FilingChangeProject.project_id == project_id)
                    .with_for_update()
                    .first()
                )
                if row is None:
                    return False, "project not found"

                quarantine_dir, moved = self._quarantine_paths([self._project_path(project_id)], "project")
                session.query(FilingChangeReviewReport).filter(
                    FilingChangeReviewReport.project_id == project_id
                ).delete(synchronize_session=False)
                session.query(FilingChangeReviewResult).filter(
                    FilingChangeReviewResult.project_id == project_id
                ).delete(synchronize_session=False)
                session.query(FilingChangeReviewRun).filter(
                    FilingChangeReviewRun.project_id == project_id
                ).delete(synchronize_session=False)
                session.query(FilingChangeApplicationForm).filter(
                    FilingChangeApplicationForm.project_id == project_id
                ).delete(synchronize_session=False)
                session.query(FilingChangeSubmissionFile).filter(
                    FilingChangeSubmissionFile.project_id == project_id
                ).delete(synchronize_session=False)
                session.query(RuntimeTask).filter(
                    RuntimeTask.domain == "filing_change_review",
                    RuntimeTask.project_id == project_id,
                ).delete(synchronize_session=False)
                session.delete(row)
                session.commit()
                committed = True
            except Exception as exc:
                session.rollback()
                try:
                    self._restore_quarantined_paths(moved)
                    self._finalize_quarantine(quarantine_dir)
                except Exception as restore_exc:
                    return False, f"删除失败且项目文件恢复失败: {exc}; {restore_exc}"
                return False, str(exc)
            finally:
                session.close()

            if committed:
                try:
                    self._finalize_quarantine(quarantine_dir)
                except Exception as exc:
                    return False, f"项目数据已删除，但隔离文件清理失败: {exc}"
            return True, "success"

    def get_project_detail(self, project_id: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        session = self.db_conn.get_session()
        try:
            row = (
                session.query(FilingChangeProject)
                .filter(FilingChangeProject.project_id == project_id, FilingChangeProject.deleted == False)
                .first()
            )
            if row is None:
                return False, "project not found", None
            data = self._serialize_project(row, session)
            return True, "success", data
        finally:
            session.close()

    def save_application_form(self, project_id: str, payload: Dict[str, Any]) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        with self._submission_manifest_lock(project_id):
            self._recover_application_form_transaction(project_id)
            return self._save_application_form_once(project_id, payload)

    def _save_application_form_once(self, project_id: str, payload: Dict[str, Any]) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        from agent.agent_backend.services.filing_form_revision import save_revision
        form_json = payload.get("form_json", {})
        raw_text = str(payload.get("raw_text", "") or "")
        confidence = payload.get("confidence", None)
        now = self._now()
        session = self.db_conn.get_session()
        try:
            project = (
                session.query(FilingChangeProject)
                .filter(FilingChangeProject.project_id == project_id, FilingChangeProject.deleted == False)
                .first()
            )
            if project is None:
                return False, "project not found", None
            row = (
                session.query(FilingChangeApplicationForm)
                .filter(FilingChangeApplicationForm.project_id == project_id)
                .order_by(FilingChangeApplicationForm.updated_at.desc())
                .first()
            )
            if row is None:
                row = FilingChangeApplicationForm(
                    project_id=project_id,
                    original_file_id="",
                    form_json=self._json(save_revision({}, form_json, payload.get('resolutions'), payload.get('edited_paths'), payload.get('expected_values'), require_loaded=True)),
                    raw_text=raw_text,
                    parse_status="not_parsed",
                    confidence=None,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                current_form, _, _ = self._effective_form_revision(project_id, row)
                revised = save_revision(current_form, form_json, payload.get('resolutions'), payload.get('edited_paths'), payload.get('expected_values'), require_loaded=True)
                row.form_json = self._json(self._check_form_validity(project_id, revised, session))
                row.updated_at = now
            session.commit()
            return True, "success", self.get_application_form(project_id)[2]
        except Exception as exc:
            session.rollback()
            return False, str(exc), None
        finally:
            session.close()

    def get_application_form(self, project_id: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        lock = self._submission_manifest_lock(project_id)
        with lock.try_lock() as acquired:
            if not acquired:
                return self._get_application_form_once(project_id, readonly=True)
            self._recover_application_form_transaction(project_id)
            return self._get_application_form_once(project_id)

    def _get_application_form_once(self, project_id: str, readonly=False) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        from agent.agent_backend.services.filing_form_revision import legacy_party_notes
        session = self.db_conn.get_session()
        try:
            project = (
                session.query(FilingChangeProject)
                .filter(FilingChangeProject.project_id == project_id, FilingChangeProject.deleted == False)
                .first()
            )
            if project is None:
                return False, "project not found", None
            row = (
                session.query(FilingChangeApplicationForm)
                .filter(FilingChangeApplicationForm.project_id == project_id)
                .order_by(FilingChangeApplicationForm.updated_at.desc())
                .first()
            )
            if row is None:
                return True, "success", {
                    "project_id": project_id,
                    "form_json": FilingChangeFormParserService()._build_full_form(''),
                    "raw_text": "",
                    "parse_status": "not_parsed",
                    "latest_attempt": self._application_form_attempt(project_id),
                    "confidence": None,
                }
            try:
                effective_form, effective_text, resolution = self._effective_form_revision(project_id, row)
            except ValueError:
                # 展示读取保留原字段及人工输入；正式执行/保存仍使用严格修订读取。
                effective_form, effective_text = self._safe_json_load(row.form_json, {}), row.raw_text or ''
                resolution = {'manually_reviewed': False, 'original_status': row.parse_status,
                    'revision_error': {'code': 'parse_revision_invalid',
                        'message': '申请表已保存修订存在冲突，当前显示原始提取及已保存人工字段。请进入原页核对修正或撤销冲突修订，正式审评仍被阻止。'}}
                readonly = True
            return True, "success", {
                "project_id": project_id,
                "form_json": (FilingChangeFormParserService().normalize_form_json(effective_form) if readonly else self._check_form_validity(project_id, FilingChangeFormParserService().normalize_form_json(effective_form), session)),
                "raw_text": effective_text,
                "parse_resolution": resolution,
                "legacy_role_notes": legacy_party_notes(self._safe_json_load(row.form_json, {})),
                "parse_status": row.parse_status,
                "latest_attempt": self._application_form_attempt(project_id),
                "confidence": row.confidence,
                "original_file_id": row.original_file_id or "",
                "effective_source": self._application_form_source(project_id, row.original_file_id),
                "updated_at": row.updated_at.strftime("%Y-%m-%d %H:%M:%S") if row.updated_at else "",
            }
        finally:
            session.close()

    def _effective_form_revision(self, project_id, row):
        """原DB提取与人工字段保持原状，读取同来源的修订字段副本。"""
        original = self._safe_json_load(row.form_json, {})
        if not row.original_file_id:
            return original, row.raw_text or '', {}
        try:
            _, meta, source, path, chunks = self._parse_review_context(project_id, 'application_form', row.original_file_id)
        except ValueError:
            return original, row.raw_text or '', {}
        saved = meta.get('parse_revision') or {}
        if not self._parse_identity_matches(saved.get('source_identity'), self._parse_source_identity(source, path)) or 'form_json' not in saved:
            return original, row.raw_text or '', {}
        from agent.agent_backend.services.filing_form_revision import merge_parse
        from agent.agent_backend.services.filing_parse_readiness import source_issues
        _, resolved = self._apply_parse_revision(meta, source, 'application_form', path, chunks)
        pending = [i for i in source_issues(source, 'application_form', chunks) if i['issue_key'] not in resolved]
        merged = merge_parse(original, saved['form_json'], mode='replace')
        merged['_parse_attempt_id'] = original.get('_parse_attempt_id', '')
        merged['_parse_review_revision'] = saved['revision']
        return merged, saved.get('raw_text', row.raw_text or ''), {
            'revision': saved['revision'], 'manually_reviewed': not pending,
            'unresolved_count': len(pending), 'original_status': row.parse_status}

    def _application_form_source(self, project_id, file_id):
        if not file_id:
            return {}
        path = self._submission_manifest_path(project_id)
        manifest = self._safe_json_load(path.read_text(encoding='utf-8'), {}) if path.exists() else {}
        candidates = [manifest.get('application_form_source', {}), manifest.get('application_form_attempt', {})]
        candidates.extend(reversed(manifest.get('parse_attempts', [])))
        for source in candidates:
            if (source.get('source_file_id') == file_id and
                    source.get('content_status') in ('success', 'partial')):
                # 页级证据由专用核对接口按需读取，不随表单轮询/任务载荷重复传输。
                return {key: value for key, value in source.items() if key != 'parsed_pages'}
        # 历史记录不能对应有效 ID 时，名称只取实际落盘文件，绝不借用失败尝试。
        paths = [p for p in (self._project_path(project_id) / 'application_form').glob(f'{file_id}.*')
                 if p.suffix.lower() in self.APPLICATION_FORM_EXTENSIONS and p.is_file()]
        if len(paths) == 1:
            path = paths[0]
            return {'source_file_id': file_id, 'source_file_name': path.name,
                    'source_file_type': path.suffix.lower().lstrip('.'), 'source_name_recovered': False}
        return {}

    def import_application_form(self, project_id: str, upload_file) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        with self._submission_manifest_lock(project_id):
            self._recover_application_form_transaction(project_id)
            return self._import_application_form_once(project_id, upload_file)

    def _recover_application_form_transaction(self, project_id: str, failed_attempt=None):
        """调用方持项目锁；以新连接的 DB 提交结果恢复，最后才移除恢复记录。

        替换使用新文件 ID，重解析使用现有 form_json 中的尝试 ID。文件恢复、
        清理和尝试记录均可重复执行，恢复自身退出后仍可从同一 manifest 续做。
        """
        manifest = self._load_submission_manifest(project_id)
        transaction = manifest.get('application_form_transaction')
        if not transaction:
            return None
        if failed_attempt:
            transaction['failure'] = failed_attempt
            self._save_submission_manifest(project_id, manifest)
        session = self.db_conn.get_session()
        try:
            row = (session.query(FilingChangeApplicationForm)
                   .filter(FilingChangeApplicationForm.project_id == project_id)
                   .order_by(FilingChangeApplicationForm.updated_at.desc()).first())
            committed = (row is not None and row.original_file_id == transaction['source_file_id']
                         and self._safe_json_load(row.form_json, {}).get('_parse_attempt_id') == transaction['attempt_id'])
        finally:
            session.close()

        def managed(relative, root):
            path = self._managed_file_path((self.root_dir / relative).absolute(), root)
            if path is None:
                raise ValueError('invalid application form recovery path')
            return path

        form_dir = self._project_path(project_id) / 'application_form'
        quarantine_dir = None
        if transaction.get('quarantine_dir'):
            trash_root = (self.root_dir / '.trash').resolve()
            quarantine_dir = (self.root_dir / transaction['quarantine_dir']).resolve()
            if (quarantine_dir.parent != trash_root or not quarantine_dir.name.startswith('application-form-')
                    or (quarantine_dir.exists() and not quarantine_dir.is_dir())):
                raise ValueError('invalid application form quarantine directory')
        new_path = managed(transaction['new_path'], form_dir)
        if transaction.get('moves') and quarantine_dir is None:
            raise ValueError('missing application form quarantine directory')
        moves = [(managed(source, form_dir), managed(target, quarantine_dir))
                 for source, target in transaction.get('moves', [])]
        if committed:
            if not new_path.is_file():
                raise FileNotFoundError('committed application form is missing')
            manifest['application_form_source'] = transaction['source']
            attempt = transaction['attempt']
        else:
            # 必须先恢复旧原件，恢复中断时禁止先删除隔离目录或恢复记录。
            self._restore_quarantined_paths(moves)
            if transaction['mode'] != 'reparse':
                new_path.unlink(missing_ok=True)
            if transaction.get('previous_source') is None:
                manifest.pop('application_form_source', None)
            else:
                manifest['application_form_source'] = transaction['previous_source']
            attempt = {**failure(ParseFailure('task_interrupted'), 'persist'),
                       **(transaction.get('failure') or {}), 'source_file_id': transaction['source_file_id'],
                       'source_file_name': transaction['source_file_name']}
        attempt = {**attempt, 'task_id': transaction['task_id'],
                   'attempted_at': transaction['attempted_at'],
                   'attempt_id': transaction['attempt_id'], 'mode': transaction['mode']}
        # 新文件名独占本次尝试，清理安装中断留下的 pending/临时文件。
        for path in new_path.parent.glob(f'.{new_path.name}.*'):
            path.unlink(missing_ok=True)
        if quarantine_dir is not None:
            self._finalize_quarantine(quarantine_dir)
        manifest['application_form_attempt'] = attempt
        manifest.setdefault('parse_attempts', []).append(attempt)
        manifest.pop('application_form_transaction')
        self._save_submission_manifest(project_id, manifest)
        return committed

    def _check_form_validity(self, project_id, form, session):
        from agent.agent_backend.services.filing_form_revision import validity_cross_checks
        rows = session.query(FilingChangeSubmissionFile).filter(FilingChangeSubmissionFile.project_id == project_id).all()
        materials = []
        for row in rows:
            name = str(row.file_name or '')
            approval = str(row.material_category or '') == '1' or any(word in name for word in ('批准', '注册证', '批件'))
            if not approval and '修订说明' not in name:
                continue
            chunks = self._load_parsed_submission_map(project_id, [row.doc_id]).get(str(row.doc_id), [])
            if not isinstance(chunks, list):
                continue
            for chunk in chunks:
                if not isinstance(chunk, dict):
                    continue
                regions = chunk.get('source_regions') or chunk.get('source') or [
                    {'page': chunk.get('page'), 'bbox_pdf': line['bbox'], 'coordinate_unit': 'pdf_point'}
                    for line in chunk.get('lines', []) if line.get('bbox')]
                if chunk.get('text'):
                    materials.append({'text': chunk['text'], 'source_file': name, 'is_approval': approval,
                                      'source_regions': regions})
                # Word 结构化材料的正文可能只有表题；核对表内原文时保留所在块及表位置。
                for index, table in enumerate(chunk.get('tables') or []):
                    if not isinstance(table, dict) or chunk.get('tables_in_text'):
                        continue
                    rows = [table.get('headers', [])] + (table.get('rows') or [])
                    text = '\n'.join('\t'.join(str(cell) if cell is not None else '' for cell in row) for row in rows if isinstance(row, list))
                    if text:
                        materials.append({'text': '\n'.join(filter(None, [table.get('caption'), text])),
                                          'source_file': name, 'is_approval': approval,
                                          'source_regions': table.get('source_regions') or [{
                                              'chunk_id': chunk.get('chunk_id'), 'section_name': chunk.get('section_name'),
                                              'table_index': index, 'coordinate_unit': 'document_table'}]})
        return validity_cross_checks(form, materials)

    def _record_parse_attempt(self, project_id, attempt, doc_id=''):
        """尝试记录独立于业务回滚，历史仍存于现有 manifest JSON。"""
        task_id = parse_task_id.get()
        if task_id:
            from agent.agent_backend.services.runtime_task_store import RuntimeTaskStore
            task = RuntimeTaskStore(connection=self.db_conn, ensure_schema=False).get_task(task_id)
            if not task or task['status'] not in ('pending','running'):
                return attempt  # 中断后迟到线程不能覆盖新业务尝试。
        attempt = {**attempt, 'task_id': parse_task_id.get(), 'attempted_at': self._now().isoformat()}
        manifest = self._load_submission_manifest(project_id)
        if doc_id:
            target = manifest.setdefault('doc_meta', {}).setdefault(doc_id, {})
            target['latest_attempt'] = attempt
        else:
            target = manifest
            target['application_form_attempt'] = attempt
        target.setdefault('parse_attempts', []).append(attempt)
        self._save_submission_manifest(project_id, manifest)
        return attempt

    def _persist_parse_task_content(self, session, project_id, data):
        """内容与现有任务结果同事务提交，避免中断恢复后旧线程继续覆盖结果。"""
        task_id = parse_task_id.get()
        if not task_id:
            return
        task = session.query(RuntimeTask).filter(RuntimeTask.task_id == task_id, RuntimeTask.project_id == project_id).with_for_update().first()
        if task is None or task.status != 'running':
            raise ParseFailure('task_interrupted')
        if task.task_type == 'parse_submissions_batch':
            result = self._safe_json_load(task.result_json, {})
            progress = result.setdefault('data', {})
            progress.setdefault('success', []).append(data)
            progress['pending'] = [doc_id for doc_id in progress.get('pending', []) if doc_id != data.get('doc_id')]
            result.update(content_status='partial' if progress.get('pending') or progress.get('failed') or any(x.get('content_status') == 'partial' for x in progress['success']) else 'success', content_available=True)
        else:
            result = {'ok': True, 'project_id': project_id, 'task_type': task.task_type, 'source_doc_id': task.source_doc_id,
                      'content_status': data['content_status'], 'content_available': True, 'data': data}
        task.result_json = self._json(result)
        task.updated_at = time.time()

    def _project_parse_attempt_tasks(self, project_id):
        from agent.agent_backend.services.runtime_task_store import RuntimeTaskStore
        session = self.db_conn.get_session()
        try:
            row = session.query(FilingChangeApplicationForm).filter_by(project_id=project_id).first()
            persisted = list(self._safe_json_load(row.form_json, {}).get('latest_task_attempts', {}).values()) if row else []
        finally:
            session.close()
        current = RuntimeTaskStore(connection=self.db_conn, ensure_schema=False).list_project_parse_tasks(project_id, 'filing_change_review')
        tasks = {t['task_id']:t for t in persisted + current}
        return sorted(tasks.values(), key=lambda t:float(t.get('created_at') or 0), reverse=True)

    def _merge_runtime_parse_attempts(self, project_id, doc_meta):
        """中断和启动失败发生在业务事务外，也应进入审评取数。"""
        from agent.agent_backend.services.runtime_task_store import RuntimeTaskStore
        tasks = self._project_parse_attempt_tasks(project_id)
        seen = set()
        for task in tasks:
            if task['task_type'] not in ('parse_submission', 'parse_submissions_batch'):
                continue
            data = (task.get('result') or {}).get('data') or {}
            ids = [task['source_doc_id']] if task.get('source_doc_id') else (task.get('payload') or {}).get('doc_ids', [])
            per_file = {x['doc_id']: x for x in data.get('success', []) + data.get('failed', []) if x.get('doc_id')}
            for doc_id in ids:
                if doc_id in seen:
                    continue
                seen.add(doc_id)
                meta = doc_meta.setdefault(doc_id, {})
                previous = meta.get('latest_attempt') or {}
                if task['task_type'] == 'parse_submissions_batch' and previous.get('task_id') == task['task_id'] and previous.get('content_status') in ('success', 'partial', 'failed'):
                    # 批任务中断不能抹掉已逐文件提交的真实结果。
                    continue
                previous_time = datetime.fromisoformat(previous['attempted_at']).replace(tzinfo=ZoneInfo('Asia/Shanghai')).timestamp() if previous.get('attempted_at') else 0
                if previous_time > float(task.get('created_at') or 0):
                    # 当前任务异常终止仍优先；较新的同步尝试不能被旧任务覆盖。
                    if previous.get('task_id') != task['task_id'] or task['status'] != 'failed':
                        continue
                attempt = per_file.get(doc_id) or data
                status = attempt.get('content_status') or (task.get('result') or {}).get('content_status') or ('failed' if task['status'] == 'failed' else 'pending' if task['status'] in ('pending', 'running') else 'not_parsed')
                meta['latest_attempt'] = {**attempt, 'content_status': status, 'task_id': task['task_id'],
                                          'message': attempt.get('message') or task.get('error_message') or task.get('message')}
        return doc_meta

    def _application_form_attempt(self, project_id):
        from agent.agent_backend.services.runtime_task_store import RuntimeTaskStore
        # manifest 原子替换，状态快照不等待长解析锁；补偿仍由持锁路径执行。
        path = self._submission_manifest_path(project_id)
        manifest = self._safe_json_load(path.read_text(encoding='utf-8'), {}) if path.exists() else {}
        previous = manifest.get('application_form_attempt', {})
        tasks = self._project_parse_attempt_tasks(project_id)
        task = next((t for t in tasks if t['task_type'] in ('import_application_form', 'parse_application_form')), None)
        if task is None:
            return previous
        previous_time = datetime.fromisoformat(previous['attempted_at']).replace(tzinfo=ZoneInfo('Asia/Shanghai')).timestamp() if previous.get('attempted_at') else 0
        if previous_time > float(task.get('created_at') or 0) and (previous.get('task_id') != task['task_id'] or task['status'] != 'failed'):
            return previous
        data = (task.get('result') or {}).get('data') or {}
        source = (task.get('payload') or {}).get('application_form_source') or {}
        if task['task_type'] == 'import_application_form':
            source = {**source, 'source_file_name':(task.get('payload') or {}).get('original_file_name',''),
                      'source_file_id':task.get('source_doc_id','')}
        return {**source, **data, 'task_id': task['task_id'],
                'content_status': data.get('content_status') or (task.get('result') or {}).get('content_status') or ('failed' if task['status'] == 'failed' else 'pending' if task['status'] in ('pending', 'running') else 'not_parsed'),
                'message': data.get('message') or task.get('error_message') or task.get('message')}

    def application_form_task_source(self, project_id):
        # 任务准入持有项目锁时保存实际原件来源，恢复时不借用后来替换的文件。
        session = self.db_conn.get_session()
        try:
            row = session.query(FilingChangeApplicationForm).filter_by(project_id=project_id).first()
            if row is None or not row.original_file_id:
                return {}
            source = self._application_form_source(project_id, row.original_file_id)
            return {k:source[k] for k in ('source_file_id','source_file_name','source_file_type','source_file_size') if k in source}
        finally:
            session.close()

    def matches_active_application_upload(self, project_id, upload_file, task):
        """只比较当前项目活跃导入的真实字节；不建立摘要或按文件名猜测。"""
        if upload_file is None or task.get('project_id') != project_id or task.get('task_type') != 'import_application_form' or task.get('status') not in ('pending', 'running'):
            return False
        payload = task.get('payload') or {}
        name = str(payload.get('staging_file') or '')
        if not name or Path(name).name != name or not name.startswith('application-form-'):
            return False
        if Path(str(getattr(upload_file,'filename',''))).suffix.lower() != Path(name).suffix.lower():
            return False
        directory = self._project_path(project_id) / '.staging' / 'application_form'
        source = self._managed_file_path(directory / name, directory)
        reader = getattr(upload_file, 'stream', upload_file)
        position = None
        try:
            position = reader.tell()
            reader.seek(0)
            with source.open('rb') as previous:
                while True:
                    chunk = reader.read(1024 * 1024)
                    if previous.read(1024 * 1024) != chunk:
                        return False
                    if not chunk:
                        return True
        except (OSError, AttributeError, ValueError):
            # 活跃文件刚完成并已清理时返回忙碌，不能误认成相同文件。
            return False
        finally:
            if position is not None:
                reader.seek(position)

    def stage_application_form_import(self, project_id: str, upload_file) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """将申请表上传流原子落到项目受管 staging，不在 HTTP 请求中执行 OCR。"""
        if upload_file is None or not str(getattr(upload_file, "filename", "") or "").strip():
            return False, "file is required", None
        original_file_name = Path(str(getattr(upload_file, "filename", "") or "")).name
        ext = Path(original_file_name).suffix.lower()
        if ext not in self.APPLICATION_FORM_EXTENSIONS:
            return False, "申请表仅支持 doc、docx、pdf 文件", None
        try:
            limit_mb = max(1, int(os.getenv("MAX_CONTENT_LENGTH_MB", "512") or "512"))
        except (TypeError, ValueError):
            limit_mb = 512
        max_bytes = limit_mb * 1024 * 1024

        with self._submission_manifest_lock(project_id):
            session = self.db_conn.get_session()
            try:
                project_exists = (
                    session.query(FilingChangeProject)
                    .filter(FilingChangeProject.project_id == project_id, FilingChangeProject.deleted == False)
                    .first()
                    is not None
                )
            finally:
                session.close()
            if not project_exists:
                return False, "project not found", None

            staging_dir = self._project_path(project_id) / ".staging" / "application_form"
            staging_name = f"application-form-{uuid.uuid4().hex}{ext}"
            staging_path = staging_dir / staging_name
            temp_path = staging_dir / f".{staging_name}.{uuid.uuid4().hex}.pending"
            total = 0
            try:
                staging_dir.mkdir(parents=True, exist_ok=True)
                reader = getattr(upload_file, "stream", upload_file)
                with open(temp_path, "xb") as stream:
                    while True:
                        chunk = reader.read(1024 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > max_bytes:
                            raise ParseFailure('file_too_large')
                        stream.write(chunk)
                    stream.flush()
                    os.fsync(stream.fileno())
                if total <= 0:
                    raise ParseFailure('empty_file')
                os.replace(str(temp_path), str(staging_path))
            except Exception as exc:
                temp_path.unlink(missing_ok=True)
                staging_path.unlink(missing_ok=True)
                try:
                    staging_dir.rmdir()
                except OSError:
                    pass
                attempt = self._record_parse_attempt(project_id, failure(exc, 'upload'))
                log_failure(exc, project_id, original_file_name, 'upload')
                return False, (str(exc) if total > max_bytes else attempt['message']), attempt
            return True, "success", {
                "staging_file": staging_name,
                "original_file_name": original_file_name,
                "file_size": total,
                "file_type": ext.lstrip("."),
            }

    def import_staged_application_form(
        self,
        project_id: str,
        staging_file: str,
        original_file_name: str,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """仅允许读取当前项目 staging 中由系统生成的申请表文件。"""
        normalized_name = str(staging_file or "").strip()
        safe_original_name = Path(str(original_file_name or "")).name
        if (
            not normalized_name
            or Path(normalized_name).name != normalized_name
            or not normalized_name.startswith("application-form-")
            or Path(normalized_name).suffix.lower() not in self.APPLICATION_FORM_EXTENSIONS
            or not safe_original_name
            or Path(safe_original_name).suffix.lower() != Path(normalized_name).suffix.lower()
        ):
            return False, "invalid staged application form", None
        staging_dir = self._project_path(project_id) / ".staging" / "application_form"
        staging_path = self._managed_file_path(staging_dir / normalized_name, staging_dir)
        if staging_path is None or not staging_path.is_file():
            attempt = failure(ParseFailure('file_access'), 'staging')
            return False, attempt['message'], attempt

        class _StagedUpload:
            filename = safe_original_name

            @staticmethod
            def read() -> bytes:
                return staging_path.read_bytes()

        return self.import_application_form(project_id, _StagedUpload())

    def cleanup_application_form_staging(self, project_id: str, staging_file: str) -> None:
        normalized_name = str(staging_file or "").strip()
        if not normalized_name or Path(normalized_name).name != normalized_name:
            return
        staging_dir = self._project_path(project_id) / ".staging" / "application_form"
        staging_path = self._managed_file_path(staging_dir / normalized_name, staging_dir)
        if staging_path is not None:
            staging_path.unlink(missing_ok=True)
        try:
            staging_dir.rmdir()
            staging_dir.parent.rmdir()
        except OSError:
            pass

    def _import_application_form_once(self, project_id: str, upload_file, mode='replace', source_file_id='') -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        if upload_file is None or not str(getattr(upload_file, "filename", "") or "").strip():
            return False, "file is required", None
        original_file_name = Path(str(getattr(upload_file, "filename", "") or "")).name
        ext = Path(original_file_name).suffix.lower()
        if ext not in self.APPLICATION_FORM_EXTENSIONS:
            return False, "申请表仅支持 doc、docx、pdf 文件", None
        content = b''
        project_dir = self._project_path(project_id) / "application_form"
        file_id = source_file_id if mode == 'reparse' else f"app_form_{uuid.uuid4().hex[:12]}"
        save_path = project_dir / f"{file_id}{ext}"
        transaction_started = False
        data = None
        session = self.db_conn.get_session()
        failure_step = 'upload'
        try:
            project = (
                session.query(FilingChangeProject)
                .filter(FilingChangeProject.project_id == project_id, FilingChangeProject.deleted == False)
                .with_for_update()
                .first()
            )
            if project is None:
                return False, "project not found", None
            manifest = self._load_submission_manifest(project_id)
            transaction = {
                'attempt_id': uuid.uuid4().hex, 'mode': mode,
                'source_file_id': file_id, 'source_file_name': original_file_name,
                'new_path': str(save_path.relative_to(self.root_dir)),
                'previous_source': manifest.get('application_form_source'),
                'task_id': parse_task_id.get(), 'attempted_at': self._now().isoformat(),
            }
            manifest['application_form_transaction'] = transaction
            self._save_submission_manifest(project_id, manifest)
            transaction_started = True
            content = upload_file.read()
            if not content:
                raise ParseFailure('empty_file')
            if mode != 'reparse':
                self._install_file_payloads([(save_path, bytes(content))])
            failure_step = 'application_form'
            try:
                parsed = self.form_parser.parse_form_file(str(save_path))
                def bind_source(node):
                    # 只处理本次新解析：逐值、选项、事项和冲突证据均关联同一原件。
                    if isinstance(node, dict):
                        if any(key in node for key in ('field_type', 'source_file', 'source_text', 'source_regions')):
                            node['source_file'] = original_file_name
                            node['source_file_id'] = file_id
                        for value in node.values():
                            bind_source(value)
                    elif isinstance(node, list):
                        for value in node:
                            bind_source(value)
                bind_source(parsed.get('form_json', {}))
                parsed.update(form_outcome(parsed))
                log_diagnostics(parsed['parse_diagnostics'], project_id, file_id)
            except Exception as exc:
                raise
            failure_step = 'persist'
            now = self._now()
            row = (
                session.query(FilingChangeApplicationForm)
                .filter(FilingChangeApplicationForm.project_id == project_id)
                .order_by(FilingChangeApplicationForm.updated_at.desc())
                .first()
            )
            if row is None:
                row = FilingChangeApplicationForm(
                    project_id=project_id,
                    original_file_id=file_id,
                    form_json=self._json(parsed.get("form_json", {})),
                    raw_text=str(parsed.get("raw_text", "") or ""),
                    parse_status=parsed['content_status'],
                    confidence=float(parsed.get("confidence")) if parsed.get("confidence") is not None else None,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
            else:
                if (mode == 'replace' and row.original_file_id and
                        not parsed['parse_diagnostics'].get('replacement_eligible', False)):
                    # partial 仅表示部分识别；残留文字不能单独授权清理有效原件。
                    # 复用当前事务恢复和失败尝试记录，原文/位置留在诊断中供核对。
                    raise ParseFailure('form_content_unconfirmed', {
                        **parsed['parse_diagnostics'], 'status':'failed', 'content_available':False,
                        'form_content_status':'unconfirmed_content', 'raw_text':parsed.get('raw_text',''),
                    })
                old_file_id = str(row.original_file_id or "").strip()
                old_paths = list(project_dir.glob(f"{old_file_id}.*")) if old_file_id and old_file_id != file_id else []
                self._quarantine_paths(old_paths, "application-form")
                row.original_file_id = file_id
                from agent.agent_backend.services.filing_form_revision import merge_parse
                parsed['form_json'] = merge_parse(self._safe_json_load(row.form_json, {}), parsed.get('form_json', {}), mode=mode,
                                                diagnostics=parsed.get('parse_diagnostics', {}))
                row.form_json = self._json(parsed.get("form_json", {}))
                row.raw_text = str(parsed.get("raw_text", "") or "")
                row.parse_status = parsed['content_status']
                if parsed.get("confidence") is not None:
                    row.confidence = float(parsed.get("confidence"))
                row.updated_at = now
            manifest = self._load_submission_manifest(project_id)
            manifest['application_form_source'] = {
                **{k: parsed[k] for k in ('content_status', 'content_available', 'parse_diagnostics', 'message')},
                'source_file_id': file_id, 'source_file_name': original_file_name,
                'source_file_type': ext.lstrip('.'), 'source_file_size': len(content),
                # 与现有原件事务共同提交/回滚，保留逐页文字、区域及表格证据。
                'parsed_pages': (parsed.get('pdf_parse_result') or {}).get('pages', []),
            }
            manifest['application_form_transaction']['source'] = manifest['application_form_source']
            manifest['application_form_transaction']['attempt'] = {
                k: v for k, v in manifest['application_form_source'].items() if k != 'parsed_pages'}
            self._save_submission_manifest(project_id, manifest)
            parsed['form_json'] = self._check_form_validity(project_id, parsed.get('form_json', {}), session)
            parsed['form_json']['_parse_attempt_id'] = transaction['attempt_id']
            row.form_json = self._json(parsed['form_json'])
            self._persist_parse_task_content(session, project_id, {**{k: parsed[k] for k in ('content_status', 'content_available', 'parse_diagnostics', 'message')}, 'original_file_id': file_id, 'source_file_name': original_file_name})
            data = {
                "project_id": project_id,
                "form_json": parsed.get("form_json", {}),
                "raw_text": str(parsed.get("raw_text", "") or ""),
                "parse_status": parsed['content_status'],
                **{k: parsed[k] for k in ('content_status', 'content_available', 'parse_diagnostics', 'message')},
                "confidence": parsed.get("confidence"),
                "original_file_id": file_id,
                "updated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            }
            session.commit()
        except Exception as exc:
            session.rollback()
            session.close()
            attempt = failure(exc, failure_step)
            log_failure(exc, project_id, file_id, attempt['stage'])
            try:
                committed = self._recover_application_form_transaction(project_id, attempt) if transaction_started else False
            except Exception as restore_exc:
                log_failure(exc, project_id, file_id, failure_step)
                log_failure(restore_exc, project_id, file_id, 'rollback')
                attempt = failure(restore_exc, 'rollback')
                return False, attempt['message'], attempt
            if not committed:
                if transaction_started:
                    attempt = self._load_submission_manifest(project_id)['application_form_attempt']
                else:
                    attempt = self._record_parse_attempt(project_id, {**attempt, 'source_file_name': original_file_name, 'source_file_id': file_id})
                return False, attempt['message'], attempt
        finally:
            session.close()

        try:
            self._recover_application_form_transaction(project_id)
        except Exception as exc:
            log_failure(exc, project_id, file_id, 'cleanup')
            message = '申请表内容已保存，但临时备份清理失败，请联系管理员。'
            return False, message, {**data, 'message': message, 'cleanup_failed': True}
        response = dict(data or {})
        response.update(
            {
                "original_file_id": file_id,
                "source_file_name": original_file_name,
                "source_file_type": ext.lstrip("."),
                "source_file_size": len(content),
                "parser_type": "drug_supplement_pdf" if ext == ".pdf" else "application_form_word",
            }
        )
        return True, "success", response

    def import_application_form_word(self, project_id: str, upload_file) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """保留历史方法别名，兼容旧版调用方。"""
        return self.import_application_form(project_id, upload_file)

    def parse_application_form(self, project_id: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        with self._submission_manifest_lock(project_id):
            ok, msg, form_data = self.get_application_form(project_id)
            if not ok or form_data is None:
                return False, msg, None
            import io
            file_id = str(form_data.get('original_file_id', ''))
            try:
                paths = [p for p in (self._project_path(project_id) / 'application_form').glob(f'{file_id}.*')
                         if p.suffix.lower() in self.APPLICATION_FORM_EXTENSIONS and p.is_file()] if file_id else []
                if len(paths) != 1:
                    raise FileNotFoundError()
                upload = io.BytesIO(paths[0].read_bytes())
                source = form_data.get('effective_source') or {}
                name = Path(source.get('source_file_name') or paths[0].name).name
                upload.filename = name if Path(name).suffix.lower() == paths[0].suffix.lower() else paths[0].name
                return self._import_application_form_once(project_id, upload, mode='reparse', source_file_id=file_id)
            except Exception as exc:
                attempt = failure(exc, 'application_form')
                log_failure(exc, project_id, file_id, 'application_form')
                self._record_parse_attempt(project_id, attempt)
                return False, attempt['message'], attempt

    def upload_submission_files(self, project_id: str, files: List[Any], material_category: str, material_sub_category: str = "") -> Tuple[bool, str, Dict[str, Any]]:
        if not files:
            return False, "files is required", {}
        with self._submission_manifest_lock(project_id):
            now = self._now()
            created: List[Dict[str, Any]] = []
            category = str(material_category or "").strip()
            sub_category = str(material_sub_category or "").strip()
            manifest_path = self._submission_manifest_path(project_id)
            manifest_snapshot = self._snapshot_file(manifest_path)
            file_state: List[Dict[str, Any]] = []
            committed = False
            session = self.db_conn.get_session()
            try:
                project = (
                    session.query(FilingChangeProject)
                    .filter(FilingChangeProject.project_id == project_id, FilingChangeProject.deleted == False)
                    .first()
                )
                if project is None:
                    return False, "project not found", {}

                project_dir = self._project_dir(project_id) / "submissions"
                manifest = self._load_submission_manifest(project_id)
                manifest.setdefault("doc_meta", {})
                file_payloads: List[Tuple[Path, bytes]] = []
                for file_obj in files:
                    file_name = str(getattr(file_obj, "filename", "") or "").strip()
                    if not file_name:
                        continue
                    content = file_obj.read()
                    ext = Path(file_name).suffix.lower()
                    upload_items = [{"name": file_name, "bytes": content}]
                    if ext == ".zip":
                        upload_items = []
                        try:
                            from io import BytesIO
                            with zipfile.ZipFile(BytesIO(content)) as zf:
                                for info in zf.infolist():
                                    if info.is_dir():
                                        continue
                                    inner_name = str(info.filename or "")
                                    if not inner_name:
                                        continue
                                    upload_items.append({"name": inner_name, "bytes": zf.read(info)})
                        except Exception:
                            upload_items = [{"name": file_name, "bytes": content}]

                    for item in upload_items:
                        item_name = str(item.get("name", ""))
                        item_bytes = bytes(item.get("bytes", b""))
                        auto_cat, auto_sub_cat, quality_tags = self._auto_classify_submission(item_name)
                        use_cat = category or auto_cat
                        use_sub = sub_category or auto_sub_cat
                        catalog_node = self._catalog_index().get(use_sub or use_cat, {})
                        doc_id = f"fcs_{uuid.uuid4().hex[:16]}"
                        item_ext = Path(item_name).suffix.lower()
                        safe_name = f"{doc_id}_{Path(item_name).name}"
                        file_path = project_dir / safe_name
                        file_payloads.append((file_path, item_bytes))
                        session.add(
                            FilingChangeSubmissionFile(
                                project_id=project_id,
                                doc_id=doc_id,
                                file_name=item_name,
                                file_type=item_ext.lstrip(".") or "bin",
                                material_category=str(use_cat or "8"),
                                storage_path=str(file_path),
                                parse_status="pending",
                                chunk_status="pending",
                                index_status="pending",
                                created_at=now,
                            )
                        )
                        manifest["doc_meta"][doc_id] = {
                            "task_type": "extend_validity_period",
                            "material_category": use_cat or "8",
                            "material_sub_category": use_sub,
                            "required_flag": self._required_flag_by_level(catalog_node.get("required_level", "")),
                            "applicable_flag": True,
                            "not_applicable_reason": "",
                            "review_enabled": True,
                            "file_size": len(item_bytes),
                            "upload_user": "system",
                            "quality_tags": quality_tags,
                            "extracted_json": {},
                            "evidence_refs": [],
                            "auto_classified": not bool(category),
                        }
                        created.append({"doc_id": doc_id, "file_name": item_name, "material_category": use_cat, "material_sub_category": use_sub})

                if not created:
                    return False, "no valid files", {}
                file_state = self._install_file_payloads(file_payloads)
                self._save_submission_manifest(project_id, manifest)
                session.commit()
                committed = True
            except Exception as exc:
                session.rollback()
                try:
                    self._restore_file_snapshot(manifest_path, manifest_snapshot)
                    self._rollback_file_payloads(file_state)
                except Exception as restore_exc:
                    return False, f"{exc}; rollback failed: {restore_exc}", {}
                return False, str(exc), {}
            finally:
                session.close()

            if committed:
                try:
                    self._finalize_file_payloads(file_state)
                except Exception as exc:
                    return False, f"上传已提交，但临时备份清理失败: {exc}", {}
            return True, "success", {"created": created, "count": len(created)}

    def list_submissions(self, project_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        page = max(int(payload.get("page", 1) or 1), 1)
        page_size = min(max(int(payload.get("page_size", 20) or 20), 1), 200)
        category_filter = str(payload.get("material_category", "")).strip()
        sub_category_filter = str(payload.get("material_sub_category", "")).strip()
        with self._submission_manifest_lock(project_id):
            session = self.db_conn.get_session()
            try:
                project = (
                    session.query(FilingChangeProject)
                    .filter(FilingChangeProject.project_id == project_id, FilingChangeProject.deleted == False)
                    .first()
                )
                if project is None:
                    return {"list": [], "total": 0, "page": page, "page_size": page_size, "project_exists": False}
                manifest = self._load_submission_manifest(project_id)
                doc_meta = manifest.get("doc_meta", {})
                rows = (
                    session.query(FilingChangeSubmissionFile)
                    .filter(FilingChangeSubmissionFile.project_id == project_id)
                    .order_by(FilingChangeSubmissionFile.created_at.desc())
                    .all()
                )
                for row in rows:
                    doc_meta.setdefault(row.doc_id, {})
                doc_meta = self._merge_runtime_parse_attempts(project_id, doc_meta)
                data = [
                    {
                        "doc_id": r.doc_id,
                        "file_name": r.file_name,
                        "file_type": r.file_type,
                        "material_category": (doc_meta.get(r.doc_id, {}) or {}).get("material_category", r.material_category),
                        "material_sub_category": (doc_meta.get(r.doc_id, {}) or {}).get("material_sub_category", ""),
                        "parse_status": r.parse_status,
                        "latest_attempt": (doc_meta.get(r.doc_id, {}) or {}).get('latest_attempt', {}),
                        "parse_diagnostics": (doc_meta.get(r.doc_id, {}) or {}).get('parse_diagnostics', {}),
                        "chunk_status": r.chunk_status,
                        "index_status": r.index_status,
                        "review_enabled": bool((doc_meta.get(r.doc_id, {}) or {}).get("review_enabled", True)),
                        "required_flag": bool((doc_meta.get(r.doc_id, {}) or {}).get("required_flag", False)),
                        "applicable_flag": bool((doc_meta.get(r.doc_id, {}) or {}).get("applicable_flag", True)),
                        "not_applicable_reason": str((doc_meta.get(r.doc_id, {}) or {}).get("not_applicable_reason", "")),
                        "file_size": int((doc_meta.get(r.doc_id, {}) or {}).get("file_size", 0) or 0),
                        "quality_tags": (doc_meta.get(r.doc_id, {}) or {}).get("quality_tags", []),
                        "automatic_categories": (doc_meta.get(r.doc_id, {}) or {}).get('automatic_categories', []),
                        "classification_difference": (doc_meta.get(r.doc_id, {}) or {}).get('classification_difference', False),
                        "auto_classified": (doc_meta.get(r.doc_id, {}) or {}).get('auto_classified', True),
                        "used_in_review": False,
                        "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else "",
                    }
                    for r in rows
                ]
                if category_filter:
                    data = [x for x in data if str(x.get("material_category", "")) == category_filter]
                if sub_category_filter:
                    data = [x for x in data if str(x.get("material_sub_category", "")) == sub_category_filter]
                total = len(data)
                start = (page - 1) * page_size
                return {"list": data[start:start + page_size], "total": total, "page": page, "page_size": page_size}
            finally:
                session.close()

    def delete_submission(self, project_id: str, doc_id: str) -> Tuple[bool, str]:
        with self._submission_manifest_lock(project_id):
            session = self.db_conn.get_session()
            manifest_path = self._submission_manifest_path(project_id)
            manifest_snapshot = self._snapshot_file(manifest_path)
            quarantine_dir = self.root_dir / ".trash" / f"submission-{uuid.uuid4().hex}"
            moved: List[Tuple[Path, Path]] = []
            committed = False
            try:
                project = (
                    session.query(FilingChangeProject)
                    .filter(FilingChangeProject.project_id == project_id, FilingChangeProject.deleted == False)
                    .first()
                )
                if project is None:
                    return False, "project not found"
                row = (
                    session.query(FilingChangeSubmissionFile)
                    .filter(FilingChangeSubmissionFile.project_id == project_id, FilingChangeSubmissionFile.doc_id == doc_id)
                    .first()
                )
                if row is None:
                    return False, "submission not found"

                project_path = self._project_path(project_id)
                related_paths: List[Path] = []
                storage_path = self._managed_file_path(row.storage_path, project_path / "submissions")
                if storage_path is not None:
                    related_paths.append(storage_path)
                for raw_path, managed_root in [
                    (project_path / "parsed" / f"{doc_id}.json", project_path / "parsed"),
                    (project_path / "parsed" / f"{doc_id}.md", project_path / "parsed"),
                    (project_path / "stability" / f"{doc_id}.json", project_path / "stability"),
                ]:
                    safe_path = self._managed_file_path(raw_path, managed_root)
                    if safe_path is not None:
                        related_paths.append(safe_path)
                charts_dir = project_path / "charts"
                if charts_dir.exists():
                    for path in charts_dir.iterdir():
                        if not path.name.startswith(str(doc_id)):
                            continue
                        safe_path = self._managed_file_path(path, charts_dir)
                        if safe_path is not None:
                            related_paths.append(safe_path)
                quarantine_dir, moved = self._quarantine_paths(related_paths, "submission")

                manifest = self._load_submission_manifest(project_id)
                manifest.setdefault("doc_meta", {}).pop(doc_id, None)
                self._save_submission_manifest(project_id, manifest)
                session.delete(row)
                session.commit()
                committed = True
            except Exception as exc:
                session.rollback()
                try:
                    self._restore_file_snapshot(manifest_path, manifest_snapshot)
                    self._restore_quarantined_paths(moved)
                    self._finalize_quarantine(quarantine_dir)
                except Exception as restore_exc:
                    return False, f"{exc}; rollback failed: {restore_exc}"
                return False, str(exc)
            finally:
                session.close()

            if committed:
                try:
                    self._finalize_quarantine(quarantine_dir)
                except Exception as exc:
                    return False, f"资料数据已删除，但隔离文件清理失败: {exc}"
            return True, "success"

    def parse_submission(self, project_id: str, doc_id: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        with self._submission_manifest_lock(project_id):
            failure_step = 'parse'
            session = self.db_conn.get_session()
            manifest_path = self._submission_manifest_path(project_id)
            manifest_snapshot = self._snapshot_file(manifest_path)
            file_state: List[Dict[str, Any]] = []
            committed = False
            try:
                project = (
                    session.query(FilingChangeProject)
                    .filter(FilingChangeProject.project_id == project_id, FilingChangeProject.deleted == False)
                    .first()
                )
                if project is None:
                    return False, "project not found", None
                row = (
                    session.query(FilingChangeSubmissionFile)
                    .filter(FilingChangeSubmissionFile.project_id == project_id, FilingChangeSubmissionFile.doc_id == doc_id)
                    .first()
                )
                if row is None:
                    return False, "submission not found", None

                parsed = self.material_service.parse_file(row.storage_path)
                result = outcome(parsed)
                result.update(task_id=parse_task_id.get(), source_doc_id=doc_id, attempted_at=self._now().isoformat())
                diagnostics = result['parse_diagnostics']
                log_diagnostics(diagnostics, project_id, doc_id)
                parse_status = result['content_status']
                failure_step = 'persist'
                md_text = self._build_submission_markdown(row.file_name, parsed)
                parsed_dir = self._project_dir(project_id) / "parsed"
                file_state = self._install_file_payloads(
                    [
                        (parsed_dir / f"{doc_id}.json", self._json(parsed).encode("utf-8")),
                        (parsed_dir / f"{doc_id}.md", md_text.encode("utf-8")),
                    ]
                )

                manifest = self._load_submission_manifest(project_id)
                manifest.setdefault("doc_meta", {})
                doc_meta = manifest["doc_meta"].get(doc_id, {})
                fresh = self._extract_structured_payload(row.file_name, parsed)
                old = doc_meta.get("extracted_json") or {}
                for key in old.get('manual_fields', []):
                    fresh.setdefault('automatic_candidates', {})[key] = fresh.get(key)
                    fresh[key] = old.get(key)
                fresh['manual_fields'] = old.get('manual_fields', [])
                doc_meta["extracted_json"] = fresh
                from agent.agent_backend.services.filing_change_extraction_service import CATEGORY
                recognized = [CATEGORY[k] for k in fresh.get('document_types', []) if k in CATEGORY]
                doc_meta['automatic_categories'] = recognized
                selected_category = doc_meta.get('material_sub_category') or doc_meta.get('material_category')
                doc_meta['classification_difference'] = bool(recognized and selected_category not in recognized)
                if doc_meta.get('auto_classified', False) and recognized:
                    doc_meta['material_category'] = recognized[0].split('.')[0]
                    doc_meta['material_sub_category'] = recognized[0] if '.' in recognized[0] else ''
                doc_meta["parse_status"] = parse_status
                doc_meta["parse_diagnostics"] = diagnostics
                doc_meta['latest_attempt'] = result
                doc_meta.setdefault('parse_attempts', []).append(result)
                manifest["doc_meta"][doc_id] = doc_meta
                self._save_submission_manifest(project_id, manifest)

                row.parse_status = parse_status
                row.chunk_status = parse_status
                row.index_status = parse_status
                self._persist_parse_task_content(session, project_id, {'doc_id': doc_id, 'parsed_chunks': len(parsed), **result})
                session.commit()
                committed = True
            except Exception as exc:
                session.rollback()
                try:
                    self._restore_file_snapshot(manifest_path, manifest_snapshot)
                    self._rollback_file_payloads(file_state)
                except Exception as restore_exc:
                    log_failure(exc, project_id, doc_id, failure_step)
                    log_failure(restore_exc, project_id, doc_id, 'rollback')
                    attempt = failure(restore_exc, 'rollback')
                    return False, attempt['message'], attempt
                attempt = failure(exc, failure_step)
                log_failure(exc, project_id, doc_id, attempt['stage'])
                attempt = self._record_parse_attempt(project_id, {**attempt, 'source_doc_id': doc_id}, doc_id)
                return False, attempt['message'], {'doc_id': doc_id, **attempt}
            finally:
                session.close()

            if committed:
                try:
                    self._finalize_file_payloads(file_state)
                except Exception as exc:
                    log_failure(exc, project_id, doc_id, 'cleanup')
                    message = '解析内容已保存，但临时备份清理失败，请联系管理员。'
                    return False, message, {'doc_id': doc_id, **result, 'message': message, 'cleanup_failed': True}
            return True, result['message'], {"doc_id": doc_id, "parsed_chunks": len(parsed), "markdown_file": f"{doc_id}.md", **result}

    def submission_task_targets(self, project_id, payload):
        """受理时确定文件主键；空集合与历史范围不明均不表示未来所有文件。"""
        session = self.db_conn.get_session()
        try:
            ids = [r.doc_id for r in session.query(FilingChangeSubmissionFile).filter_by(project_id=project_id).all()]
            requested = set(payload.get('doc_ids') or [])
            return ids if payload.get('all') or not requested else [i for i in ids if i in requested]
        finally:
            session.close()

    def batch_parse_submissions(self, project_id: str, payload: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
        doc_ids = payload.get("doc_ids", [])
        parse_all = bool(payload.get("all", False))
        empty_result = {"total": 0, "success": [], "failed": []}
        with self._submission_manifest_lock(project_id):
            session = self.db_conn.get_session()
            try:
                project = (
                    session.query(FilingChangeProject)
                    .filter(FilingChangeProject.project_id == project_id, FilingChangeProject.deleted == False)
                    .first()
                )
                if project is None:
                    return False, "project not found", empty_result
                rows = (
                    session.query(FilingChangeSubmissionFile)
                    .filter(FilingChangeSubmissionFile.project_id == project_id)
                    .all()
                )
                requested_ids = set(doc_ids)
                target_doc_ids = [
                    row.doc_id
                    for row in rows
                    if row.doc_id in requested_ids or (not payload.get('targets_resolved') and (parse_all or not doc_ids))
                ]
            finally:
                session.close()
            success, failed = [], []
            task_id = parse_task_id.get()
            def publish_progress(remaining):
                if not task_id:
                    return
                from agent.agent_backend.services.runtime_task_store import RuntimeTaskStore
                data = {'total': len(target_doc_ids), 'success': success, 'failed': failed, 'pending': remaining}
                content_status = ('partial' if remaining or failed or any(x.get('content_status') == 'partial' for x in success) else 'success') if success else ('pending' if remaining else 'failed')
                data.update(content_status=content_status, content_available=bool(success))
                saved = RuntimeTaskStore(connection=self.db_conn, ensure_schema=False).update_task(
                    task_id, payload={'doc_ids': target_doc_ids, 'all': False},
                    result={'ok': False, 'project_id': project_id, 'task_type': 'parse_submissions_batch',
                            'content_status': content_status, 'content_available': bool(success), 'data': data})
                if not saved:
                    raise RuntimeError('批量任务已中断，停止后续文件解析')
            publish_progress(target_doc_ids)
            for index, doc_id in enumerate(target_doc_ids):
                ok, msg, data = self.parse_submission(project_id, doc_id)
                if ok or (data or {}).get('content_available'):
                    success.append({"doc_id": doc_id, 'operation_ok': bool(ok), **(data or {})})
                else:
                    failed.append({"doc_id": doc_id, "error": msg, **(data or {})})
                publish_progress(target_doc_ids[index + 1:])
            status = 'failed' if not success else ('partial' if failed or any(x.get('content_status') == 'partial' for x in success) else 'success')
            message = {'failed': '批量解析失败，没有可用的新结果，请查看各文件原因。', 'partial': '批量解析部分成功，请查看失败文件和缺失页面。', 'success': '批量解析成功'}[status]
            cleanup_failed = any(x.get('cleanup_failed') for x in success)
            if cleanup_failed:
                message = '批量解析已有内容保存，但部分文件临时备份清理失败，请联系管理员。'
            return bool(success) and not cleanup_failed, message, {"total": len(target_doc_ids), "success": success, "failed": failed, 'content_status': status, 'content_available': bool(success)}

    def get_submission_original_file(self, project_id: str, doc_id: str):
        """仅由资料记录定位原件，在删除锁内打开；不接受客户端文件路径。"""
        with self._submission_manifest_lock(project_id):
            session = self.db_conn.get_session()
            try:
                project = session.query(FilingChangeProject).filter_by(project_id=project_id, deleted=False).first()
                row = session.query(FilingChangeSubmissionFile).filter_by(project_id=project_id, doc_id=doc_id).first() if project else None
                if row is None:
                    return False, '原件不存在或已删除', None
                path = self._managed_file_path(row.storage_path, self._project_path(project_id) / 'submissions')
                if path is None:
                    return False, '原件不存在或无法读取', None
                try:
                    stream = path.open('rb')
                except OSError:
                    return False, '原件不存在或无法读取', None
                name = str(row.file_name or path.name).replace('\\', '/').split('/')[-1]
                name = name.replace('\r', '').replace('\n', '').replace('\x00', '') or 'original.bin'
                return True, 'success', {'stream': stream, 'file_name': name}
            finally:
                session.close()

    def get_submission_parsed_markdown(self, project_id: str, doc_id: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        with self._submission_manifest_lock(project_id):
            session = self.db_conn.get_session()
            try:
                project = (
                    session.query(FilingChangeProject)
                    .filter(FilingChangeProject.project_id == project_id, FilingChangeProject.deleted == False)
                    .first()
                )
                row = (
                    session.query(FilingChangeSubmissionFile)
                    .filter(FilingChangeSubmissionFile.project_id == project_id, FilingChangeSubmissionFile.doc_id == doc_id)
                    .first()
                    if project is not None
                    else None
                )
                if project is None or row is None:
                    return False, "submission not found", None
            finally:
                session.close()

            parsed_dir = self._project_path(project_id) / "parsed"
            md_path = parsed_dir / f"{doc_id}.md"
            json_path = parsed_dir / f"{doc_id}.json"
            parsed = self._safe_json_load(json_path.read_text(encoding="utf-8"), []) if json_path.exists() else []
            doc_meta = self._load_submission_manifest(project_id).get('doc_meta', {})
            doc_meta.setdefault(doc_id, {})
            meta = self._merge_runtime_parse_attempts(project_id, doc_meta).get(doc_id, {})
            latest_attempt = meta.get('latest_attempt', {})
            from agent.agent_backend.services.filing_numeric_revision import numeric_cells, content_attempt, active_confirmations
            saved_numeric = meta.get('numeric_revision') or {}
            source_payload = {"parsed_chunks": parsed, "parse_diagnostics": next((p['parse_diagnostics'] for p in parsed if p.get('parse_diagnostics')), {}),
                              'numeric_review': {'source_attempt': content_attempt(meta), 'revision': saved_numeric.get('revision', 0),
                                                 'cells': numeric_cells(parsed), 'items': active_confirmations(meta),
                                                 'stale': bool(saved_numeric and saved_numeric.get('source_attempt') != content_attempt(meta))},
                              'latest_attempt': latest_attempt, 'result_origin': 'previous_success' if latest_attempt.get('content_status') == 'failed' else 'current',
                              'content_task_id': next((x.get('task_id', '') for x in reversed(meta.get('parse_attempts', [])) if x.get('content_status') in ('success', 'partial')), '')}
            prefix = '> 此前成功结果：本次解析失败，以下内容不代表本次尝试成功。\n\n' if source_payload['result_origin'] == 'previous_success' else ''
            if json_path.exists() and (meta.get('parse_revision') or active_confirmations(meta)):
                try:
                    effective, resolved = self._apply_parse_revision(meta, {**meta, 'doc_id': doc_id}, 'submission', json_path, parsed)
                except ValueError:
                    # 历史修订冲突仍可打开原始证据并撤销确认，不能伪称已应用。
                    return True, 'success', {'doc_id': doc_id, **source_payload,
                        'revision_warning': '人工修订存在冲突或已失效。以下为原始解析；请重新核对，必要时取消该表数值勾选并保存，再修订表格。',
                        'markdown': prefix + self._build_submission_markdown(doc_id, parsed)}
                if resolved or active_confirmations(meta):
                    return True, 'success', {'doc_id': doc_id, **source_payload, 'original_chunks': parsed,
                        'parsed_chunks': effective, 'manual_resolution_count': len(resolved),
                        'markdown': prefix + self._build_submission_markdown(doc_id, effective)}
            if md_path.exists():
                return True, "success", {"doc_id": doc_id, "markdown": prefix + md_path.read_text(encoding="utf-8", errors="ignore"), **source_payload}
            if json_path.exists():
                parsed = self._safe_json_load(json_path.read_text(encoding="utf-8", errors="ignore"), [])
                return True, "success", {"doc_id": doc_id, "markdown": prefix + self._build_submission_markdown(doc_id, parsed), **source_payload}
            return False, "parsed result not found", None

    def confirm_submission_numeric_cells(self, project_id: str, doc_id: str, payload: Dict[str, Any]):
        from agent.agent_backend.services.filing_numeric_revision import content_attempt, apply_confirmations
        with self._submission_manifest_lock(project_id):
            session = self.db_conn.get_session()
            try:
                project = session.query(FilingChangeProject).filter_by(project_id=project_id, deleted=False).first()
                row = session.query(FilingChangeSubmissionFile).filter_by(project_id=project_id, doc_id=doc_id).first() if project else None
                if row is None:
                    return False, 'submission not found', None
            finally:
                session.close()
            manifest = self._load_submission_manifest(project_id)
            meta = manifest.get('doc_meta', {}).get(doc_id, {})
            source = content_attempt(meta)
            previous = meta.get('numeric_revision') or {}
            revision = previous.get('revision', 0)
            if not source or payload.get('source_attempt') != source:
                return False, '解析内容已变化或缺少来源，请重新读取并核对原件', {'code': 'numeric_source_conflict'}
            if type(payload.get('expected_revision')) is not int or payload['expected_revision'] != revision:
                return False, '人工确认已被更新，请重新读取后核对，当前输入未保存', {'code': 'numeric_revision_conflict'}
            path = self._project_path(project_id) / 'parsed' / f'{doc_id}.json'
            try:
                chunks = json.loads(path.read_text(encoding='utf-8'))
                items = payload.get('items')
                apply_confirmations(chunks, items)
                from agent.agent_backend.services.filing_parse_resolution import apply_compatible_resolutions
                from agent.agent_backend.services.filing_parse_readiness import source_issues
                generic = meta.get('parse_revision') or {}
                generic_source = {**meta, 'doc_id': doc_id, 'parse_status': row.parse_status}
                if self._parse_identity_matches(generic.get('source_identity'), self._parse_source_identity(generic_source, path)):
                    apply_compatible_resolutions(chunks, source_issues(generic_source, 'submission', chunks),
                                                 generic.get('items', []), items)
                # 只接收修订所需字段，候选与坐标来自服务器原始解析，不相信客户端回传。
                items = [{key: item[key] for key in ('key', 'original_text', 'value', 'reason')} for item in items]
                saved = {'source_attempt': source, 'revision': revision + 1, 'items': items,
                         'confirmed_at': self._now().isoformat()}
                if previous:
                    meta.setdefault('numeric_confirmation_history', []).append(previous)
                meta['numeric_revision'] = saved
                self._save_submission_manifest(project_id, manifest)
            except ValueError as exc:
                return False, str(exc), {'code': 'numeric_confirmation_invalid'}
            except Exception:
                return False, '确认值保存失败，原始资料及历史结果保持不变，请重试', {'code': 'numeric_confirmation_save_failed'}
            return True, '已保存人工确认；仅在明确启动的新分析中使用，不修改历史结果', saved

    def compare_submission_materials(self, project_id: str, payload: Dict[str, Any]) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        initial_doc_id = str(payload.get("initial_doc_id", "")).strip()
        supplement_doc_id = str(payload.get("supplement_doc_id", "")).strip()
        if not initial_doc_id or not supplement_doc_id:
            return False, "initial_doc_id and supplement_doc_id are required", None
        if initial_doc_id == supplement_doc_id:
            return False, "请选择两份不同的材料进行比对", None
        with self._submission_manifest_lock(project_id):
            session = self.db_conn.get_session()
            try:
                project = (
                    session.query(FilingChangeProject)
                    .filter(FilingChangeProject.project_id == project_id, FilingChangeProject.deleted == False)
                    .first()
                )
                if project is None:
                    return False, "project not found", None
                rows = (
                    session.query(FilingChangeSubmissionFile)
                    .filter(
                        FilingChangeSubmissionFile.project_id == project_id,
                        FilingChangeSubmissionFile.doc_id.in_([initial_doc_id, supplement_doc_id]),
                    )
                    .all()
                )
                row_map = {r.doc_id: r.file_name for r in rows}
                if initial_doc_id not in row_map or supplement_doc_id not in row_map:
                    return False, "材料不存在或不属于当前项目", None
            finally:
                session.close()

            ok1, _, d1 = self.get_submission_parsed_markdown(project_id, initial_doc_id)
            ok2, _, d2 = self.get_submission_parsed_markdown(project_id, supplement_doc_id)
            if not ok1 or not ok2:
                return False, "请先解析两份材料后再进行自动比对", None

            text1 = str((d1 or {}).get("markdown", "") or "")
            text2 = str((d2 or {}).get("markdown", "") or "")
            lines1 = self._normalize_compare_lines(text1)
            lines2 = self._normalize_compare_lines(text2)

            sm = difflib.SequenceMatcher(None, lines1, lines2)
            opcodes = sm.get_opcodes()
            diff_items: List[Dict[str, Any]] = []
            for tag, i1, i2, j1, j2 in opcodes:
                if tag == "equal":
                    continue
                old_chunk = " ".join(lines1[i1:i2]).strip()
                new_chunk = " ".join(lines2[j1:j2]).strip()
                item_type = "修改"
                if tag == "insert":
                    item_type = "新增"
                elif tag == "delete":
                    item_type = "删除"
                diff_items.append(
                    {
                        "type": item_type,
                        "old_text": old_chunk[:240],
                        "new_text": new_chunk[:240],
                        "old_range": f"{i1 + 1}-{i2}",
                        "new_range": f"{j1 + 1}-{j2}",
                    }
                )
            diff_items = diff_items[:80]
            added = sum(1 for x in diff_items if x.get("type") == "新增")
            deleted = sum(1 for x in diff_items if x.get("type") == "删除")
            changed = sum(1 for x in diff_items if x.get("type") == "修改")
            similarity = round(sm.ratio(), 4)
            return True, "success", {
                "initial_doc": {"doc_id": initial_doc_id, "file_name": row_map[initial_doc_id]},
                "supplement_doc": {"doc_id": supplement_doc_id, "file_name": row_map[supplement_doc_id]},
                "summary": {
                    "similarity": similarity,
                    "diff_count": len(diff_items),
                    "added_count": added,
                    "deleted_count": deleted,
                    "changed_count": changed,
                },
                "diff_items": diff_items,
            }

    @staticmethod
    def _normalize_compare_lines(markdown_text: str) -> List[str]:
        lines: List[str] = []
        for raw in str(markdown_text or "").splitlines():
            line = str(raw or "").strip()
            if not line:
                continue
            if line.startswith("# 解析结果：") or line.startswith("## Chunk "):
                continue
            line = re.sub(r"\s+", " ", line)
            lines.append(line)
        return lines

    def _build_submission_markdown(self, file_name: str, parsed_chunks: List[Dict[str, Any]]) -> str:
        lines: List[str] = []
        last_section_path: List[str] = []
        heading_numbers: List[int] = []
        for item in parsed_chunks or []:
            if item.get('parse_diagnostics'):
                lines.append(f"#### 第 {item.get('page')} 页")
                if item.get('status') in ('partial', 'failed'):
                    reasons = '; '.join(e.get('reason', '') for e in item.get('errors', []))
                    lines.append(f"> 第 {item.get('page')} 页解析{('部分成功' if item.get('content_available') else '未完成')}：{reasons or item.get('page_kind', '')}")
            text = str((item or {}).get("text", "")).strip()
            section_path = [str(x or "").strip() for x in ((item or {}).get("section_path") or []) if str(x or "").strip()]
            if section_path:
                shared_depth = 0
                while (
                    shared_depth < len(last_section_path)
                    and shared_depth < len(section_path)
                    and last_section_path[shared_depth] == section_path[shared_depth]
                ):
                    shared_depth += 1
                for level, title in enumerate(section_path[shared_depth:], start=shared_depth + 1):
                    if len(heading_numbers) >= level:
                        heading_numbers = heading_numbers[:level]
                        heading_numbers[level - 1] += 1
                    else:
                        heading_numbers.append(0)
                        heading_numbers[level - 1] = 1
                    number_prefix = ".".join(str(x) for x in heading_numbers)
                    lines.append(f'{"#" * min(6, max(1, level))} {number_prefix} {title}')
                last_section_path = section_path
                heading_prefix = "\n".join(section_path).strip()
                if heading_prefix and text.startswith(heading_prefix):
                    text = text[len(heading_prefix):].lstrip()
                elif text.startswith(section_path[-1]):
                    text = text[len(section_path[-1]):].lstrip()
            normalized_text = text.strip()
            rendered_tables: List[str] = []
            for table in ((item or {}).get("tables") or []):
                if not isinstance(table, dict):
                    continue
                markdown = self._render_submission_table_markdown(table)
                if markdown:
                    rendered_tables.append(markdown)
            pure_heading_placeholder = False
            if section_path:
                joined_path = "\n".join(section_path).strip()
                last_title = section_path[-1].strip()
                pure_heading_placeholder = normalized_text in {joined_path, last_title, ""}
            if normalized_text and not pure_heading_placeholder:
                lines.append(normalized_text)
                lines.append("")
            elif not rendered_tables:
                continue
            for markdown in rendered_tables:
                if item.get('tables_in_text') or markdown in normalized_text:
                    continue
                lines.append(markdown)
                lines.append("")
            for block in self._extract_markdown_table_blocks(str((item or {}).get("text", "") or "")):
                if block in normalized_text:
                    continue
                lines.append(block)
                lines.append("")
        return "\n".join(lines).strip() + "\n"

    @staticmethod
    def _render_submission_table_markdown(table: Dict[str, Any]) -> str:
        if not isinstance(table, dict):
            return ""
        structured = table.get("structured_data", {}) if isinstance(table.get("structured_data", {}), dict) else {}
        table_type = str(table.get("table_type", "") or "")
        parts: List[str] = []
        if table_type == "stability_result" and structured:
            metadata = structured.get("metadata", {}) if isinstance(structured.get("metadata", {}), dict) else {}
            ordered_meta_keys = ["批号", "规格", "考察条件", "包装", "试验开始时间", "贮藏室编号", "放置地点", "留样数量", "留样地点"]
            meta_lines = []
            for key in ordered_meta_keys:
                value = str(metadata.get(key, "") or "").strip()
                if value:
                    meta_lines.append(f"- {key}：{value}")
            if meta_lines:
                parts.extend(meta_lines)
                parts.append("")
            headers = [str(x or "").strip() for x in (structured.get("headers", []) or [])]
            rows = structured.get("rows", []) or []
            normalized_rows = []
            for row in rows:
                if not isinstance(row, list):
                    continue
                normalized_rows.append([str(x or "").strip() for x in row[: len(headers)]])
            table_md = FilingChangeReviewService._markdown_table(headers, normalized_rows)
            if table_md:
                parts.append(table_md)
            return "\n".join(parts).strip()
        if structured:
            headers = [str(x or "").strip() for x in (structured.get("headers", []) or [])]
            rows = structured.get("rows", []) or []
            normalized_rows = []
            for row in rows:
                if not isinstance(row, list):
                    continue
                normalized_rows.append([str(x or "").strip() for x in row[: len(headers)]])
            table_md = FilingChangeReviewService._markdown_table(headers, normalized_rows)
            if table_md:
                return table_md
        return str(table.get("markdown", "") or "").strip()

    @staticmethod
    def _markdown_table(headers: List[str], rows: List[List[str]]) -> str:
        clean_headers = [str(x or "").strip() for x in (headers or [])]
        if not clean_headers:
            return ""
        width = len(clean_headers)
        lines = [
            "| " + " | ".join(FilingChangeReviewService._escape_markdown_table_cell(cell) for cell in clean_headers) + " |",
            "| " + " | ".join(["---"] * width) + " |",
        ]
        for row in rows or []:
            normalized = list(row[:width]) + [""] * max(0, width - len(row))
            lines.append(
                "| " + " | ".join(FilingChangeReviewService._escape_markdown_table_cell(cell) for cell in normalized[:width]) + " |"
            )
        return "\n".join(lines)

    @staticmethod
    def _escape_markdown_table_cell(value: Any) -> str:
        return str(value or "").replace("|", "\\|").replace("\n", "<br>").strip()

    def update_submission_metadata(self, project_id: str, doc_id: str, payload: Dict[str, Any]) -> Tuple[bool, str]:
        with self._submission_manifest_lock(project_id):
            session = self.db_conn.get_session()
            try:
                project = (
                    session.query(FilingChangeProject)
                    .filter(FilingChangeProject.project_id == project_id, FilingChangeProject.deleted == False)
                    .first()
                )
                row = (
                    session.query(FilingChangeSubmissionFile)
                    .filter(FilingChangeSubmissionFile.project_id == project_id, FilingChangeSubmissionFile.doc_id == doc_id)
                    .first()
                    if project is not None
                    else None
                )
                if project is None or row is None:
                    return False, "submission not found"
            finally:
                session.close()
            manifest = self._load_submission_manifest(project_id)
            doc_meta = manifest.get("doc_meta", {}).get(doc_id)
            if doc_meta is None:
                return False, "submission not found"
            if "material_category" in payload or "material_sub_category" in payload:
                doc_meta["auto_classified"] = False
            for key in ["material_category", "material_sub_category", "not_applicable_reason"]:
                if key in payload:
                    doc_meta[key] = payload.get(key)
            for key in ["review_enabled", "applicable_flag", "required_flag"]:
                if key in payload:
                    doc_meta[key] = bool(payload.get(key))
            manifest["doc_meta"][doc_id] = doc_meta
            try:
                self._save_submission_manifest(project_id, manifest)
            except Exception as exc:
                return False, str(exc)
            return True, "success"

    def set_category_not_applicable(self, project_id: str, payload: Dict[str, Any]) -> Tuple[bool, str]:
        category_code = str(payload.get("category_code", "")).strip()
        reason = str(payload.get("reason", "")).strip()
        if not category_code:
            return False, "category_code is required"
        with self._submission_manifest_lock(project_id):
            session = self.db_conn.get_session()
            try:
                project = (
                    session.query(FilingChangeProject)
                    .filter(FilingChangeProject.project_id == project_id, FilingChangeProject.deleted == False)
                    .first()
                )
                if project is None:
                    return False, "project not found"
            finally:
                session.close()
            manifest = self._load_submission_manifest(project_id)
            manifest.setdefault("category_meta", {})
            manifest["category_meta"][category_code] = {
                "applicable_flag": False,
                "not_applicable_reason": reason,
                "updated_at": self._now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            try:
                self._save_submission_manifest(project_id, manifest)
            except Exception as exc:
                return False, str(exc)
            return True, "success"

    def get_submission_catalog(self, project_id: str) -> Dict[str, Any]:
        with self._submission_manifest_lock(project_id):
            session = self.db_conn.get_session()
            try:
                project = (
                    session.query(FilingChangeProject)
                    .filter(FilingChangeProject.project_id == project_id, FilingChangeProject.deleted == False)
                    .first()
                )
                if project is None:
                    return {"catalog": [], "category_meta": {}, "project_exists": False}
            finally:
                session.close()
            manifest = self._load_submission_manifest(project_id)
            return {"catalog": self.SUBMISSION_CATALOG, "category_meta": manifest.get("category_meta", {})}

    def check_submission_completeness(self, project_id: str) -> Dict[str, Any]:
        with self._submission_manifest_lock(project_id):
            session = self.db_conn.get_session()
            try:
                project = (
                    session.query(FilingChangeProject)
                    .filter(FilingChangeProject.project_id == project_id, FilingChangeProject.deleted == False)
                    .first()
                )
                if project is None:
                    return {
                        "uploaded_codes": [],
                        "missing_items": [],
                        "not_applicable_issues": [],
                        "pass": False,
                        "project_exists": False,
                    }
                rows = (
                    session.query(FilingChangeSubmissionFile)
                    .filter(FilingChangeSubmissionFile.project_id == project_id)
                    .all()
                )
            finally:
                session.close()
            manifest = self._load_submission_manifest(project_id)
            doc_meta = manifest.get("doc_meta", {})
            category_meta = manifest.get("category_meta", {})
            from agent.agent_backend.services.filing_change_extraction_service import is_submission, content_completeness
            selected = [{**doc_meta.get(r.doc_id, {}), 'doc_id':r.doc_id, 'file_name':r.file_name,
                         'parse_status':r.parse_status, 'material_category':doc_meta.get(r.doc_id, {}).get('material_category', r.material_category)} for r in rows]
            selected = [r for r in selected if is_submission(r)]
            selected_ids = {r['doc_id'] for r in selected}
            content_missing, parse_issues = content_completeness(selected)
            uploaded_codes = set()
            form_session = self.db_conn.get_session()
            try:
                form_row = form_session.query(FilingChangeApplicationForm).filter(
                    FilingChangeApplicationForm.project_id == project_id).order_by(FilingChangeApplicationForm.updated_at.desc()).first()
                form_values = {}
                if form_row:
                    try:
                        # 目录检查与页面/执行使用同一有效修订，不能只查旧OCR字段。
                        form_values, _, _ = self._effective_form_revision(project_id, form_row)
                    except ValueError:
                        # 失效/冲突修订不能作为申请信息齐全的依据，也不能报500。
                        parse_issues.append({'code': 'parse_revision_invalid', 'source_kind': 'application_form',
                                             'message': '申请表修订无法应用，请重新核对原页。'})
                if (form_values.get('item_6_generic_name') or {}).get('value') and (form_values.get('item_5_application_matter_category') or {}).get('selected_values'):
                    uploaded_codes.add('application_info')
            finally:
                form_session.close()
            for r in rows:
                if r.doc_id not in selected_ids: continue
                meta = doc_meta.get(r.doc_id, {})
                code = str(meta.get("material_sub_category") or meta.get("material_category") or r.material_category or "")
                if code:
                    uploaded_codes.add(code)
                    uploaded_codes.add(code.split(".")[0])
            missing = []
            na_required = []
            for node in self.SUBMISSION_CATALOG:
                level = str(node.get("required_level", ""))
                code = str(node.get("code", ""))
                children = node.get("children", []) or []
                if children:
                    for child in children:
                        c_code = str(child.get("code", ""))
                        c_level = str(child.get("required_level", ""))
                        if c_level == "required" and c_code not in uploaded_codes and not category_meta.get(c_code, {}).get("applicable_flag", True):
                            if not str(category_meta.get(c_code, {}).get("not_applicable_reason", "")).strip():
                                na_required.append({"code": c_code, "label": child.get("label", ""), "message": "未上传且未填写不适用原因"})
                        elif c_level == "required" and c_code not in uploaded_codes:
                            missing.append({"code": c_code, "label": child.get("label", ""), "message": f"缺少{child.get('label', '')}"})
                if level == "required" and code not in uploaded_codes:
                    meta = category_meta.get(code, {})
                    if meta.get("applicable_flag", True):
                        missing.append({"code": code, "label": node.get("label", ""), "message": f"缺少{node.get('label', '')}"})
                    elif not str(meta.get("not_applicable_reason", "")).strip():
                        na_required.append({"code": code, "label": node.get("label", ""), "message": "需填写不适用原因"})
            return {
                "uploaded_codes": sorted(uploaded_codes),
                "missing_items": missing,
                "not_applicable_issues": na_required,
                "content_missing": content_missing, "parse_issues": parse_issues,
                "not_applicable": [{'code':code, 'reason':meta.get('not_applicable_reason')} for code,meta in category_meta.items() if not meta.get('applicable_flag',True) and meta.get('not_applicable_reason')],
                "selected_doc_ids": sorted(selected_ids),
                "counts": {'file_missing':len(missing),'parse_issues':len(parse_issues),'content_missing':len(content_missing), 'not_applicable':sum(1 for m in category_meta.values() if not m.get('applicable_flag',True) and m.get('not_applicable_reason'))},
                "pass": not (missing or na_required or content_missing or parse_issues),
            }

    def _extract_structured_payload(self, file_name: str, parsed_chunks: List[Dict[str, Any]]) -> Dict[str, Any]:
        # 正文与表格分流：表格 chunk 的 caption/Markdown 不再混入叙述性正文预览。
        narrative_text = "\n".join(
            str((x or {}).get("text", ""))
            for x in (parsed_chunks or [])
            if isinstance(x, dict) and not ((x or {}).get("tables") or [])
        )
        markdown_tables: List[Dict[str, Any]] = []
        structured_tables: List[Dict[str, Any]] = []
        attachments: List[str] = []
        for item in parsed_chunks or []:
            if not isinstance(item, dict):
                continue
            section_title = " > ".join([str(x or "") for x in (item.get("section_path") or []) if str(x or "").strip()])
            item_text = str(item.get("text", "") or "")
            for raw_line in item_text.splitlines():
                line = str(raw_line or "").strip()
                if re.match(r"^附件\d+[：:]", line):
                    attachments.append(line)
            for table in item.get("tables", []) or []:
                if not isinstance(table, dict):
                    continue
                structured_data = table.get("structured_data", {}) if isinstance(table.get("structured_data", {}), dict) else {}
                if structured_data:
                    structured_tables.append(
                        {
                            "section_title": section_title,
                            "table_type": table.get("table_type", ""),
                            "caption": table.get("caption", ""),
                            "structured_data": structured_data,
                        }
                    )
                markdown = str(table.get("markdown", "") or "").strip()
                if not markdown:
                    continue
                parsed_table = self._parse_markdown_table_block(markdown)
                if parsed_table.get("headers"):
                    markdown_tables.append(
                        {
                            "section_title": section_title,
                            "headers": parsed_table.get("headers", []),
                            "rows": parsed_table.get("rows", []),
                            "markdown": markdown,
                            "data_source_type": "structured_table_object",
                            "source_table": table,
                            "page": table.get('page', item.get('page')),
                            "bbox_pdf": table.get('bbox_pdf'),
                        }
                    )
            for block in self._extract_markdown_table_blocks(str(item.get("text", "") or "")):
                if item.get('tables_in_text'):
                    continue
                parsed_table = self._parse_markdown_table_block(block)
                if parsed_table.get("headers"):
                    markdown_tables.append(
                        {
                            "section_title": section_title,
                            "headers": parsed_table.get("headers", []),
                            "rows": parsed_table.get("rows", []),
                            "markdown": block,
                            "data_source_type": "embedded_markdown_text",
                        }
                    )
        deduped_markdown_tables: List[Dict[str, Any]] = []
        seen_markdown_tables = set()
        # 同一表格可能同时存在于 tables[] 和 chunk.text，仅保留一份，优先结构化表对象。
        markdown_tables.sort(key=lambda item: 0 if item.get("data_source_type") == "structured_table_object" else 1)
        for table in markdown_tables:
            signature = json.dumps(
                {
                    "section_title": table.get("section_title", ""),
                    "headers": table.get("headers", []),
                    "rows": table.get("rows", []),
                    "page": table.get('page'),
                    "bbox_pdf": table.get('bbox_pdf'),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            if signature in seen_markdown_tables:
                continue
            seen_markdown_tables.add(signature)
            deduped_markdown_tables.append(table)
        markdown_tables = deduped_markdown_tables

        from agent.agent_backend.services.filing_change_extraction_service import extract_document
        facts_payload = extract_document(file_name, parsed_chunks)
        low_name = str(file_name or "").lower()
        if self._looks_like_stability_content(file_name, parsed_chunks):
            sample_sources = []
            stability_tables = []
            stability_records = []
            quality_standard_revisions = []
            report_explanations = []
            standard_version_timelines = []
            stability_specifications = set()
            report_specifications = set()
            packaging_values = set()
            standard_versions = set()
            for table in structured_tables:
                table_type = str(table.get("table_type", "") or "")
                payload = table.get("structured_data", {}) if isinstance(table.get("structured_data", {}), dict) else {}
                if table_type == "sample_source":
                    sample_sources.extend(payload.get("records", []) or [])
                elif table_type == "stability_result":
                    stability_tables.append(payload)
                    stability_records.extend(payload.get("records", []) or [])
                    specification = str(payload.get("specification", "") or "").strip()
                    packaging = str(payload.get("packaging", "") or "").strip()
                    if specification:
                        stability_specifications.add(specification)
                    if packaging:
                        packaging_values.add(packaging)
                elif table_type == "quality_standard_revision":
                    quality_standard_revisions.extend(payload.get("records", []) or [])
                elif table_type == "report_explanation":
                    report_explanations.extend(payload.get("records", []) or [])
                    for row in payload.get("records", []) or []:
                        if isinstance(row, dict):
                            spec_value = str(row.get("规格", "") or "").strip()
                            if spec_value:
                                report_specifications.add(spec_value)
                            std_value = str(row.get("质量标准", "") or "").strip()
                            if std_value:
                                standard_versions.add(std_value)
                elif table_type == "standard_version_timeline":
                    standard_version_timelines.extend(payload.get("records", []) or [])
                    for row in payload.get("records", []) or []:
                        if isinstance(row, dict):
                            std_value = str(row.get("检测标准", "") or "").strip()
                            if std_value:
                                standard_versions.add(std_value)
            return {
                **facts_payload,
                "document_type": "extend_validity_period_material",
                "sample_sources": sample_sources,
                "stability_tables": stability_tables,
                "stability_records": stability_records,
                "quality_standard_revisions": quality_standard_revisions,
                "report_explanations": report_explanations,
                "standard_version_timelines": standard_version_timelines,
                "batches": sorted(
                    {
                        str(item.get("batch_no", "") or "").strip()
                        for item in stability_records
                        if str(item.get("batch_no", "") or "").strip()
                    }
                ),
                "storage_conditions": sorted(
                    {
                        str(item.get("storage_condition", "") or "").strip()
                        for item in stability_records
                        if str(item.get("storage_condition", "") or "").strip()
                    }
                ),
                "time_points": sorted(
                    {
                        str(item.get("time_point", "") or "").strip()
                        for item in stability_records
                        if str(item.get("time_point", "") or "").strip()
                    }
                ),
                "indicators": sorted(
                    {
                        str(item.get("indicator", "") or "").strip()
                        for item in stability_records
                        if str(item.get("indicator", "") or "").strip()
                    }
                ),
                "specifications": sorted(stability_specifications | report_specifications),
                "stability_specifications": sorted(stability_specifications),
                "report_specifications": sorted(report_specifications),
                "packaging_values": sorted(packaging_values),
                "standard_versions": sorted(standard_versions),
                "attachments": list(dict.fromkeys(attachments)),
                "markdown_tables": markdown_tables,
                "raw_text_preview": narrative_text[:2000],
                "narrative_text_preview": narrative_text[:2000],
                "numeric_analysis_source": "structured_stability_table",
            }
        return {**facts_payload, "raw_text_preview": narrative_text[:1000], "narrative_text_preview": narrative_text[:1000], "markdown_tables": markdown_tables}

    def _load_parsed_submission_map(self, project_id: str, doc_ids: List[str]) -> Dict[str, Any]:
        with self._submission_manifest_lock(project_id):
            return self._load_parsed_submission_snapshot(project_id, doc_ids)

    def _load_parsed_submission_snapshot(self, project_id: str, doc_ids: List[str]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        metadata = self._load_submission_manifest(project_id).get('doc_meta', {})
        parsed_dir = self._project_dir(project_id) / "parsed"
        for doc_id in doc_ids or []:
            path = parsed_dir / f"{doc_id}.json"
            if path.exists():
                out[str(doc_id)] = self._safe_json_load(path.read_text(encoding="utf-8", errors="ignore"), [])
                meta = metadata.get(str(doc_id), {})
                source = {**meta, 'doc_id': str(doc_id)}
                out[str(doc_id)], _ = self._apply_parse_revision(meta, source, 'submission', path, out[str(doc_id)])
            else:
                out[str(doc_id)] = []
        return out

    def _list_enabled_reference_materials(self, task_type: str = "extend_validity_period") -> List[Dict[str, Any]]:
        manifest = self._load_reference_manifest()
        meta_map = manifest.get("doc_meta", {})
        session = self.db_conn.get_session()
        try:
            rows = session.query(FilingChangeReferenceMaterial).filter(FilingChangeReferenceMaterial.task_type == task_type).all()
            out = []
            for r in rows:
                meta = meta_map.get(r.doc_id, {})
                if not bool(meta.get("enabled", True)):
                    continue
                out.append(
                    {
                        "doc_id": r.doc_id,
                        "title": r.title,
                        "material_type": meta.get("material_type", r.material_type),
                        "drug_category": meta.get("drug_category", "通用资料"),
                        "applicable_change_item": meta.get("applicable_change_item", ""),
                    }
                )
            return out
        finally:
            session.close()

    def upload_reference_materials(self, files: List[Any], material_type: str, task_type: str, payload: Optional[Dict[str, Any]] = None) -> Tuple[bool, str, Dict[str, Any]]:
        if not files:
            return False, "files is required", {}
        with self._reference_manifest_lock():
            base_dir = self.root_dir / "reference_materials"
            now = self._now()
            created: List[Dict[str, Any]] = []
            payload = payload or {}
            manifest_path = self._reference_manifest_path()
            manifest_snapshot = self._snapshot_file(manifest_path)
            manifest = self._load_reference_manifest()
            manifest.setdefault("doc_meta", {})
            drug_category = str(payload.get("drug_category", "通用资料")).strip() or "通用资料"
            applicable_change_item = str(payload.get("applicable_change_item", "延长药品有效期")).strip() or "延长药品有效期"
            applicable_registration_classification = str(payload.get("applicable_registration_classification", "")).strip()
            publisher = str(payload.get("publisher", "")).strip()
            version = str(payload.get("version", "")).strip()
            enabled = bool(payload.get("enabled", True))
            remark = str(payload.get("remark", "")).strip()
            file_state: List[Dict[str, Any]] = []
            committed = False
            session = self.db_conn.get_session()
            try:
                file_payloads: List[Tuple[Path, bytes]] = []
                for file_obj in files:
                    name = str(getattr(file_obj, "filename", "") or "").strip()
                    if not name:
                        continue
                    doc_id = f"fcrm_{uuid.uuid4().hex[:16]}"
                    save_path = base_dir / f"{doc_id}_{Path(name).name}"
                    file_payloads.append((save_path, bytes(file_obj.read())))
                    row = FilingChangeReferenceMaterial(
                        doc_id=doc_id,
                        title=Path(name).stem,
                        material_type=str(material_type or "other"),
                        task_type=str(task_type or "extend_validity_period"),
                        storage_path=str(save_path),
                        parse_status="pending",
                        index_status="pending",
                        created_at=now,
                    )
                    session.add(row)
                    manifest["doc_meta"][doc_id] = {
                        "drug_category": drug_category,
                        "material_type": str(material_type or "其他"),
                        "applicable_change_item": applicable_change_item,
                        "applicable_registration_classification": applicable_registration_classification,
                        "publisher": publisher,
                        "version": version,
                        "enabled": enabled,
                        "remark": remark,
                        "extracted_json": {},
                        "parse_status": "pending",
                    }
                    created.append({"doc_id": doc_id, "title": row.title})
                if not created:
                    return False, "no valid files", {}
                file_state = self._install_file_payloads(file_payloads)
                self._save_reference_manifest(manifest)
                session.commit()
                committed = True
            except Exception as exc:
                session.rollback()
                try:
                    self._restore_file_snapshot(manifest_path, manifest_snapshot)
                    self._rollback_file_payloads(file_state)
                except Exception as restore_exc:
                    return False, f"{exc}; rollback failed: {restore_exc}", {}
                return False, str(exc), {}
            finally:
                session.close()

            if committed:
                try:
                    self._finalize_file_payloads(file_state)
                except Exception as exc:
                    return False, f"上传已提交，但临时备份清理失败: {exc}", {}
            return True, "success", {"created": created, "count": len(created)}

    def list_reference_materials(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        page = max(int(payload.get("page", 1) or 1), 1)
        page_size = min(max(int(payload.get("page_size", 20) or 20), 1), 200)
        task_type = str(payload.get("task_type", "")).strip()
        drug_category = str(payload.get("drug_category", "")).strip()
        material_type = str(payload.get("material_type", "")).strip()
        applicable_change_item = str(payload.get("applicable_change_item", "")).strip()
        enabled_raw = payload.get("enabled", None)
        enabled_filter: Optional[bool] = None
        if enabled_raw is not None and enabled_raw != "":
            if isinstance(enabled_raw, str):
                enabled_filter = enabled_raw.strip().lower() in {"1", "true", "yes", "on", "是", "启用"}
            else:
                enabled_filter = bool(enabled_raw)
        with self._reference_manifest_lock():
            manifest = self._load_reference_manifest()
            doc_meta = manifest.get("doc_meta", {})
            session = self.db_conn.get_session()
            try:
                query = session.query(FilingChangeReferenceMaterial)
                if task_type:
                    query = query.filter(FilingChangeReferenceMaterial.task_type == task_type)
                rows = query.order_by(FilingChangeReferenceMaterial.created_at.desc()).all()
                data = []
                for r in rows:
                    meta = doc_meta.get(r.doc_id, {})
                    item = {
                        "doc_id": r.doc_id,
                        "title": r.title,
                        "material_type": meta.get("material_type", r.material_type),
                        "task_type": r.task_type,
                        "drug_category": meta.get("drug_category", "通用资料"),
                        "applicable_change_item": meta.get("applicable_change_item", ""),
                        "applicable_registration_classification": meta.get("applicable_registration_classification", ""),
                        "publisher": meta.get("publisher", ""),
                        "version": meta.get("version", ""),
                        "enabled": bool(meta.get("enabled", True)),
                        "remark": meta.get("remark", ""),
                        "parse_status": r.parse_status,
                        "parse_diagnostics": meta.get("parse_diagnostics", {}),
                        "latest_attempt": meta.get("latest_attempt", {}),
                        "index_status": r.index_status,
                        "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else "",
                    }
                    data.append(item)
                if drug_category:
                    data = [x for x in data if str(x.get("drug_category", "")) == drug_category]
                if material_type:
                    data = [x for x in data if str(x.get("material_type", "")) == material_type]
                if applicable_change_item:
                    data = [x for x in data if str(x.get("applicable_change_item", "")) == applicable_change_item]
                if enabled_filter is not None:
                    data = [x for x in data if bool(x.get("enabled", True)) == enabled_filter]
                total = len(data)
                start = (page - 1) * page_size
                return {"list": data[start:start + page_size], "total": total, "page": page, "page_size": page_size}
            finally:
                session.close()

    def delete_reference_material(self, doc_id: str) -> Tuple[bool, str]:
        with self._reference_manifest_lock():
            session = self.db_conn.get_session()
            manifest_path = self._reference_manifest_path()
            manifest_snapshot = self._snapshot_file(manifest_path)
            quarantine_dir = self.root_dir / ".trash" / f"reference-{uuid.uuid4().hex}"
            moved: List[Tuple[Path, Path]] = []
            committed = False
            try:
                row = session.query(FilingChangeReferenceMaterial).filter(FilingChangeReferenceMaterial.doc_id == doc_id).first()
                if row is None:
                    return False, "reference material not found"
                reference_dir = self.root_dir / "reference_materials"
                parsed_dir = reference_dir / "parsed"
                related_paths: List[Path] = []
                for raw_path, managed_root in [
                    (row.storage_path, reference_dir),
                    (parsed_dir / f"{doc_id}.json", parsed_dir),
                    (parsed_dir / f"{doc_id}.md", parsed_dir),
                ]:
                    safe_path = self._managed_file_path(raw_path, managed_root)
                    if safe_path is not None:
                        related_paths.append(safe_path)
                quarantine_dir, moved = self._quarantine_paths(related_paths, "reference")
                manifest = self._load_reference_manifest()
                manifest.setdefault("doc_meta", {}).pop(doc_id, None)
                self._save_reference_manifest(manifest)
                session.delete(row)
                session.commit()
                committed = True
            except Exception as exc:
                session.rollback()
                try:
                    self._restore_file_snapshot(manifest_path, manifest_snapshot)
                    self._restore_quarantined_paths(moved)
                    self._finalize_quarantine(quarantine_dir)
                except Exception as restore_exc:
                    return False, f"{exc}; rollback failed: {restore_exc}"
                return False, str(exc)
            finally:
                session.close()

            if committed:
                try:
                    self._finalize_quarantine(quarantine_dir)
                except Exception as exc:
                    return False, f"参考资料已删除，但隔离文件清理失败: {exc}"
            return True, "success"

    def parse_reference_material(self, doc_id: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        with self._reference_manifest_lock():
            session = self.db_conn.get_session()
            manifest_path = self._reference_manifest_path()
            manifest_snapshot = self._snapshot_file(manifest_path)
            file_state: List[Dict[str, Any]] = []
            committed = False
            stage = 'parse'
            try:
                row = session.query(FilingChangeReferenceMaterial).filter(FilingChangeReferenceMaterial.doc_id == doc_id).first()
                if row is None:
                    return False, "reference material not found", None
                parsed = self.material_service.parse_file(row.storage_path)
                result = outcome(parsed)
                diagnostics = result['parse_diagnostics']
                log_diagnostics(diagnostics, '', doc_id)
                parse_status = result['content_status']
                md_text = self._build_submission_markdown(row.title, parsed)
                stage = 'persist'
                parsed_dir = self.root_dir / "reference_materials" / "parsed"
                file_state = self._install_file_payloads(
                    [
                        (parsed_dir / f"{doc_id}.json", self._json(parsed).encode("utf-8")),
                        (parsed_dir / f"{doc_id}.md", md_text.encode("utf-8")),
                    ]
                )
                manifest = self._load_reference_manifest()
                manifest.setdefault("doc_meta", {})
                meta = manifest["doc_meta"].get(doc_id, {})
                meta["parse_status"] = parse_status
                meta["parse_diagnostics"] = diagnostics
                meta["latest_attempt"] = {**result, 'task_id': parse_task_id.get()}
                meta["extracted_json"] = self._extract_structured_payload(row.title, parsed)
                manifest["doc_meta"][doc_id] = meta
                self._save_reference_manifest(manifest)
                row.parse_status = parse_status
                row.index_status = parse_status
                session.commit()
                committed = True
            except Exception as exc:
                session.rollback()
                try:
                    self._restore_file_snapshot(manifest_path, manifest_snapshot)
                    self._rollback_file_payloads(file_state)
                except Exception as restore_exc:
                    log_failure(restore_exc, '', doc_id, 'rollback')
                    failed_result = failure(restore_exc, stage='rollback')
                    return False, failed_result['message'], failed_result
                log_failure(exc, '', doc_id, 'reference_parse')
                failed_result = failure(exc, stage=stage)
                try:
                    manifest = self._load_reference_manifest()
                    manifest.setdefault('doc_meta', {}).setdefault(doc_id, {})['latest_attempt'] = failed_result
                    self._save_reference_manifest(manifest)
                except Exception as record_exc:
                    log_failure(record_exc, '', doc_id, 'reference_attempt_persist')
                return False, failed_result['message'], failed_result
            finally:
                session.close()

            if committed:
                try:
                    self._finalize_file_payloads(file_state)
                except Exception as exc:
                    log_failure(exc, '', doc_id, 'reference_cleanup')
                    return False, '解析内容已保存，但临时备份清理失败，请联系管理员核查。', {"doc_id": doc_id, **result}
            return True, result['message'], {"doc_id": doc_id, "parsed_chunks": len(parsed), **result}

    def update_reference_material_metadata(self, doc_id: str, payload: Dict[str, Any]) -> Tuple[bool, str]:
        with self._reference_manifest_lock():
            session = self.db_conn.get_session()
            try:
                row = session.query(FilingChangeReferenceMaterial).filter(FilingChangeReferenceMaterial.doc_id == doc_id).first()
                if row is None:
                    return False, "reference material not found"
            finally:
                session.close()
            manifest = self._load_reference_manifest()
            meta = manifest.get("doc_meta", {}).get(doc_id)
            if meta is None:
                return False, "reference material not found"
            for key in [
                "drug_category",
                "material_type",
                "applicable_change_item",
                "applicable_registration_classification",
                "publisher",
                "version",
                "remark",
            ]:
                if key in payload:
                    meta[key] = payload.get(key)
            if "enabled" in payload:
                meta["enabled"] = bool(payload.get("enabled"))
            manifest["doc_meta"][doc_id] = meta
            try:
                self._save_reference_manifest(manifest)
            except Exception as exc:
                return False, str(exc)
            return True, "success"

    def get_reference_material_parsed_markdown(self, doc_id: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        with self._reference_manifest_lock():
            session = self.db_conn.get_session()
            try:
                row = session.query(FilingChangeReferenceMaterial).filter(FilingChangeReferenceMaterial.doc_id == doc_id).first()
                if row is None:
                    return False, "reference material not found", None
            finally:
                session.close()
            parsed_dir = self.root_dir / "reference_materials" / "parsed"
            md_path = parsed_dir / f"{doc_id}.md"
            json_path = parsed_dir / f'{doc_id}.json'
            meta = self._load_reference_manifest().get('doc_meta', {}).get(doc_id, {})
            if json_path.exists() and meta.get('parse_revision'):
                original = self._safe_json_load(json_path.read_text(encoding='utf-8'), [])
                effective, resolved = self._apply_parse_revision(meta, {**meta, 'doc_id': doc_id}, 'reference', json_path, original)
                if resolved:
                    return True, 'success', {'doc_id': doc_id, 'original_chunks': original,
                        'parsed_chunks': effective, 'manual_resolution_count': len(resolved),
                        'markdown': self._build_submission_markdown(doc_id, effective)}
            if not md_path.exists():
                return False, "parsed result not found", None
            return True, "success", {"doc_id": doc_id, "markdown": md_path.read_text(encoding="utf-8", errors="ignore")}

    def get_reference_taxonomy(self) -> Dict[str, Any]:
        return {
            "drug_categories": self.REFERENCE_DRUG_CATEGORIES,
            "material_types": self.REFERENCE_MATERIAL_TYPES,
            "change_items": self.REFERENCE_CHANGE_ITEMS,
        }

    def create_rule(self, payload: Dict[str, Any]) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        return self.rule_service.create_rule(payload)

    def list_rules(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        return self.rule_service.list_rules(payload)

    def update_rule(self, rule_id: str, payload: Dict[str, Any]) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        return self.rule_service.update_rule(rule_id, payload)

    def delete_rule(self, rule_id: str) -> Tuple[bool, str]:
        return self.rule_service.delete_rule(rule_id)

    def import_rules(self, upload_file) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        return self.rule_service.import_rules(upload_file)

    def start_review(self, project_id: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        ok, msg, data = self.prepare_review_run(project_id)
        if not ok or not data:
            return ok, msg, data
        run_id = str(data.get("run_id", ""))
        ok2, msg2, result = self.execute_review_run(project_id, run_id)
        return ok2, msg2, {"run_id": run_id, "result": result}

    def project_task_creation_lock(self, project_id: str) -> _CrossProcessManifestLock:
        """暴露项目级跨进程锁，用于将运行记录与异步任务的创建串行化。"""
        return self._submission_manifest_lock(project_id)

    def prepare_review_run(self, project_id: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        with self._submission_manifest_lock(project_id):
            return self._prepare_review_run_once(project_id)

    def get_parse_readiness(self, project_id: str) -> Dict[str, Any]:
        """复用项目锁核对实际输入；管理库启用不等于审评采用。"""
        from agent.agent_backend.services.filing_parse_readiness import source_issues, readiness_result
        with self._submission_manifest_lock(project_id):
            session = self.db_conn.get_session()
            try:
                project = session.query(FilingChangeProject).filter(
                    FilingChangeProject.project_id == project_id, FilingChangeProject.deleted == False).first()
                if project is None:
                    return {'project_exists': False, **readiness_result([{'code': 'project_not_found',
                            'message': '项目不存在', 'source_kind': 'project', 'issue_key': 'project'}])}
                rows = session.query(FilingChangeSubmissionFile).filter(
                    FilingChangeSubmissionFile.project_id == project_id).all()
                manifest = self._load_submission_manifest(project_id)
                metadata = manifest.get('doc_meta', {})
                for row in rows:
                    metadata.setdefault(row.doc_id, {})
                metadata = self._merge_runtime_parse_attempts(project_id, metadata)
                _, _, form = self._get_application_form_once(project_id, readonly=True)
                form = form or {}
                effective = form.get('effective_source') or {}
                form_source = {**form, 'parse_diagnostics': effective.get('parse_diagnostics', {})}
                form_evidence = manifest.get('application_form_source') or {}
                form_pages = (form_evidence.get('parsed_pages') or []) if form_evidence.get('source_file_id') == form.get('original_file_id') else []
                issues = source_issues(form_source, 'application_form', form_pages)
                if form.get('original_file_id') and manifest.get('application_form_review'):
                    try:
                        _, review_meta, review_source, review_path, review_pages = self._parse_review_context(
                            project_id, 'application_form', form['original_file_id'])
                        _, resolved = self._apply_parse_revision(review_meta, review_source, 'application_form', review_path, review_pages)
                    except ValueError:
                        resolved = set()
                        issues.append({'source_kind': 'application_form', 'doc_id': form['original_file_id'],
                            'file_name': effective.get('source_file_name', ''), 'issue_key': 'revision_conflict',
                            'code': 'parse_revision_conflict', 'page': None, 'bbox_pdf': [],
                            'message': '申请表人工修订存在冲突或无法应用，请进入原页核对修正；正式审评尚不能启动。'})
                    issues = [i for i in issues if i['issue_key'] not in resolved]
                if not str(form.get('raw_text') or '').strip() and not any(
                        isinstance(value, dict) and (value.get('value') or value.get('selected_values'))
                        for value in (form.get('form_json') or {}).values()):
                    issues.append({'source_kind': 'application_form', 'issue_key': 'form_content',
                        'code': 'parsed_content_missing', 'message': '申请表没有可用内容，请上传并解析申请表。',
                        'page': None, 'bbox_pdf': []})
                inputs = [{'kind': 'application_form', 'file_id': form.get('original_file_id', ''),
                           'updated_at': form.get('updated_at', ''), 'attempt': form.get('latest_attempt') or {},
                           'form_json': form.get('form_json') or {}}]

                def inspect(row, meta, kind, path, label):
                    source = {**meta, 'doc_id': row.doc_id, 'file_name': label, 'parse_status': row.parse_status}
                    chunks = self._safe_json_load(path.read_text(encoding='utf-8'), []) if path.is_file() else []
                    problems = source_issues(source, kind, chunks if isinstance(chunks, list) else [])
                    if isinstance(chunks, list) and chunks:
                        try:
                            _, resolved = self._apply_parse_revision(meta, source, kind, path, chunks)
                        except ValueError:
                            resolved = set()
                            problems.append({'source_kind': kind, 'doc_id': row.doc_id, 'file_name': label,
                                'issue_key': 'revision_conflict', 'code': 'parse_revision_conflict',
                                'message': '人工修订存在冲突或已失效，请打开解析结果重新核对；不能使用相互覆盖的修订启动审评。',
                                'page': None, 'bbox_pdf': []})
                        problems = [issue for issue in problems if issue['issue_key'] not in resolved]
                    if not isinstance(chunks, list) or not any(isinstance(chunk, dict) and (
                            str(chunk.get('text') or chunk.get('raw_text') or '').strip() or
                            any(str(cell.get('text') or '').strip() for table in chunk.get('tables', [])
                                for cell in table.get('cells', []))) for chunk in chunks):
                        problems.append({'source_kind': kind, 'doc_id': row.doc_id, 'file_name': label,
                            'issue_key': 'parsed_content', 'code': 'parsed_content_missing',
                            'message': '未找到有效解析内容，请重新解析。', 'page': None, 'bbox_pdf': []})
                    issues.extend(problems)
                    stat = path.stat() if path.is_file() else None
                    inputs.append({'kind': kind, 'doc_id': row.doc_id,
                        'attempt': meta.get('latest_attempt') or {}, 'parse_status': row.parse_status,
                        'parsed_mtime_ns': stat.st_mtime_ns if stat else None,
                        'metadata': {key: meta.get(key) for key in ('numeric_revision', 'parse_revision',
                                     'extracted_json', 'material_category', 'material_sub_category')}})

                # 与实际审评所用清单一致；不能用分页接口只检查前200份资料。
                for row in sorted(rows, key=lambda r: r.doc_id):
                    meta = metadata.get(row.doc_id, {})
                    if bool(meta.get('review_enabled', True)):
                        inspect(row, meta, 'submission', self._project_dir(project_id) / 'parsed' / f'{row.doc_id}.json', row.file_name)
                # 当前orchestrator仅采用项目事实和规则库；reference参数没有正文消费路径。
                # 不能把全部启用资料当成本轮依据，更不能因无关资料未解析而阻塞。
                # 后续增加实际参考检索时，须同时接入所用清单、解析检查及输入身份。
                completeness = self.check_submission_completeness(project_id)
                for index, missing in enumerate((completeness.get('missing_items') or []) +
                                                (completeness.get('not_applicable_issues') or [])):
                    issues.append({'source_kind': 'catalog', 'issue_key': f'material:{index}',
                        'code': 'required_material_missing', 'message': missing.get('message') or missing.get('label') or str(missing),
                        'page': None, 'bbox_pdf': []})
                return {'project_exists': True, **readiness_result(issues), '_input_identity': inputs,
                        'reference_usage': {'status': 'not_used', 'doc_ids': [],
                            'message': '当前备案审评采用申报资料与规则库，未自动采用参考资料正文；启用不表示已引用。'}}
            finally:
                session.close()

    @staticmethod
    def _parse_source_identity(source, path):
        stat = path.stat() if path.is_file() else None
        return {'doc_id': source.get('doc_id', ''), 'attempt': source.get('latest_attempt') or {},
                'parsed_mtime_ns': str(stat.st_mtime_ns) if stat else None,
                'parsed_size': stat.st_size if stat else None}

    @staticmethod
    def _parse_identity_matches(left, right):
        # 纳秒时间戳超过JS安全整数范围；对外用字符串，历史服务器整数无损兼容。
        # 不把浏览器已舍入的数值四舍五入回来，否则会削弱来源变化校验。
        if not isinstance(left, dict) or not isinstance(right, dict):
            return False
        def normalize(value):
            result = dict(value)
            if type(result.get('parsed_mtime_ns')) is int:
                result['parsed_mtime_ns'] = str(result['parsed_mtime_ns'])
            return result
        return normalize(left) == normalize(right)

    def _apply_parse_revision(self, meta, source, kind, path, chunks):
        from agent.agent_backend.services.filing_parse_readiness import source_issues
        from agent.agent_backend.services.filing_parse_resolution import apply_compatible_resolutions
        from agent.agent_backend.services.filing_numeric_revision import active_confirmations, resolved_numeric_issues
        saved = meta.get('parse_revision') or {}
        replay_source = {'parse_status': saved.get('source_status', 'not_parsed'), **source}
        issues = source_issues(replay_source, kind, chunks)
        items = saved.get('items', []) if self._parse_identity_matches(saved.get('source_identity'), self._parse_source_identity(source, path)) else []
        confirmations = active_confirmations(meta) if kind == 'submission' else []
        updated, resolved, _ = apply_compatible_resolutions(chunks, issues, items, confirmations)
        if confirmations:
            resolved |= resolved_numeric_issues(chunks, issues, meta)
        return updated, resolved

    def _parse_review_context(self, project_id, kind, doc_id):
        """调用方持项目和参考资料锁；文件路径只能取自所属数据库记录。"""
        if kind not in ('application_form', 'submission', 'reference'):
            raise ValueError('该来源暂不支持区域修订，请使用申请表字段核对或重新解析')
        session = self.db_conn.get_session()
        try:
            project = session.query(FilingChangeProject).filter_by(project_id=project_id, deleted=False).first()
            if project is None:
                raise ValueError('项目不存在')
            if kind == 'application_form':
                row = (session.query(FilingChangeApplicationForm).filter_by(project_id=project_id)
                       .order_by(FilingChangeApplicationForm.updated_at.desc()).first())
                if row is None or not doc_id or row.original_file_id != doc_id:
                    raise ValueError('申请表原件已变化或不属于该项目')
                manifest = self._load_submission_manifest(project_id)
                evidence = manifest.get('application_form_source') or {}
                if evidence.get('source_file_id') != doc_id:
                    raise ValueError('历史申请表缺少对应原页证据，请重新解析')
                root = self._project_path(project_id) / 'application_form'
                candidates = [p for p in root.glob(f'{doc_id}.*') if p.suffix.lower() in self.APPLICATION_FORM_EXTENSIONS]
                path = self._managed_file_path(candidates[0], root) if len(candidates) == 1 else None
                if path is None or not path.is_file():
                    raise ValueError('申请表原件不存在或无法读取')
                source = {**evidence, 'doc_id': doc_id, 'file_name': evidence.get('source_file_name', ''),
                          'parse_status': row.parse_status, 'latest_attempt': self._application_form_attempt(project_id)}
                chunks = evidence.get('parsed_pages') or []
                if not isinstance(chunks, list):
                    raise ValueError('申请表原页证据格式无效，请重新解析')
                return manifest, manifest.setdefault('application_form_review', {}), source, path, chunks
            if kind == 'submission':
                row = session.query(FilingChangeSubmissionFile).filter_by(project_id=project_id, doc_id=doc_id).first()
                manifest = self._load_submission_manifest(project_id)
                path = self._project_path(project_id) / 'parsed' / f'{doc_id}.json'
            else:
                row = session.query(FilingChangeReferenceMaterial).filter_by(doc_id=doc_id, task_type='extend_validity_period').first()
                manifest = self._load_reference_manifest()
                path = self.root_dir / 'reference_materials' / 'parsed' / f'{doc_id}.json'
            if row is None:
                raise ValueError('资料不存在或不属于该项目范围')
            meta = manifest.setdefault('doc_meta', {}).setdefault(doc_id, {})
            if kind == 'submission':
                live = self._merge_runtime_parse_attempts(project_id, {doc_id: dict(meta)})[doc_id]
            else:
                live = meta
            source = {**live, 'doc_id': doc_id, 'file_name': row.file_name if kind == 'submission' else row.title,
                      'parse_status': row.parse_status}
            chunks = self._safe_json_load(path.read_text(encoding='utf-8'), []) if path.is_file() else []
            if not isinstance(chunks, list):
                raise ValueError('解析内容格式无效，请重新解析')
            return manifest, meta, source, path, chunks
        finally:
            session.close()

    def get_parse_review(self, project_id, kind, doc_id):
        from agent.agent_backend.services.filing_parse_readiness import source_issues
        with self._submission_manifest_lock(project_id), self._reference_manifest_lock():
            try:
                _, meta, source, path, chunks = self._parse_review_context(project_id, kind, doc_id)
                identity = self._parse_source_identity(source, path)
                saved = meta.get('parse_revision') or {}
                revision_error = None
                try:
                    effective, resolved = self._apply_parse_revision(meta, source, kind, path, chunks)
                except ValueError:
                    # 仅核对读取降级到原始证据；审评读取仍拒绝无效覆盖层。
                    effective, resolved = chunks, set()
                    revision_error = {'code': 'parse_revision_invalid',
                        'message': '已保存修订存在冲突或已不适用，当前显示原始解析。请核对并修正修订记录；原记录仍保留，不能据此启动审评。'}
                from agent.agent_backend.services.filing_numeric_revision import resolved_numeric_issues
                resolved |= resolved_numeric_issues(chunks, source_issues(source, kind, chunks), meta)
                return True, 'success', {'source_kind': kind, 'doc_id': doc_id, 'file_name': source['file_name'],
                    'editable': True, 'revision_error': revision_error,
                    'source_identity': identity, 'revision': saved.get('revision', 0),
                    'original_chunks': chunks, 'effective_chunks': effective,
                    'issues': [{**issue, 'resolved': issue['issue_key'] in resolved}
                               for issue in source_issues(source, kind, chunks)],
                    'items': saved.get('items', []) if self._parse_identity_matches(saved.get('source_identity'), identity) else [],
                    'stale': bool(saved and not self._parse_identity_matches(saved.get('source_identity'), identity))}
            except ValueError as exc:
                return False, str(exc), {'code': 'parse_review_unavailable'}

    def get_parse_review_page(self, project_id, kind, doc_id, page_no, payload):
        """从受管原件渲染核对页，坐标与解析器相同；不接受任意文件路径。"""
        import base64
        import math
        import fitz
        from agent.agent_backend.utils.parser.pdf_page_extractor import page_geometry
        with self._submission_manifest_lock(project_id), self._reference_manifest_lock():
            try:
                _, _, source, parsed_path, _ = self._parse_review_context(project_id, kind, doc_id)
                identity = self._parse_source_identity(source, parsed_path)
                if not isinstance(payload, dict) or not self._parse_identity_matches(payload.get('source_identity'), identity):
                    return False, '解析来源已变化，请重新读取核对内容。', {'code': 'parse_revision_conflict'}
                if type(page_no) is not int or page_no < 1:
                    raise ValueError('页码无效')
                session = self.db_conn.get_session()
                try:
                    if kind == 'application_form':
                        row = session.query(FilingChangeApplicationForm).filter_by(project_id=project_id, original_file_id=doc_id).first()
                        root = self._project_path(project_id) / 'application_form'
                        candidates = [p for p in root.glob(f'{doc_id}.*') if p.suffix.lower() in self.APPLICATION_FORM_EXTENSIONS]
                        original_path = candidates[0] if row and len(candidates) == 1 else None
                    elif kind == 'submission':
                        row = session.query(FilingChangeSubmissionFile).filter_by(project_id=project_id, doc_id=doc_id).first()
                        root = self._project_path(project_id) / 'submissions'
                        original_path = row.storage_path if row else None
                    else:
                        row = session.query(FilingChangeReferenceMaterial).filter_by(doc_id=doc_id, task_type='extend_validity_period').first()
                        root = self.root_dir / 'reference_materials'
                        original_path = row.storage_path if row else None
                    path = self._managed_file_path(original_path, root) if original_path else None
                    if path is None or not path.is_file():
                        raise ValueError('原件不存在或无法读取')
                    with fitz.open(path) as doc:
                        if not doc.is_pdf or doc.needs_pass:
                            raise ValueError('原页核对仅支持可读取的PDF；其他格式请查看原文件')
                        if page_no > len(doc):
                            raise ValueError('页码超出原件范围')
                        page = doc[page_no - 1]
                        rotation = page.rotation
                        page.set_rotation(0)
                        media, crop, _ = page_geometry(page)
                        visible = crop & fitz.Rect(media.x0, 0, media.x1, media.height)
                        if visible != crop:
                            page.set_cropbox(visible)
                            page_geometry(page)
                        # 限制预览像素，避免巨幅原页占满内存；不更改解析分辨率。
                        scale = min(1.5, math.sqrt(4000000 / page.rect.get_area()))
                        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                        return True, 'success', {'page': page_no, 'page_count': len(doc),
                            'page_bbox': list(page.rect), 'source_rotation': rotation,
                            'coordinate_unit': 'pdf_point', 'coordinate_origin': 'top_left',
                            'image_width': pix.width, 'image_height': pix.height,
                            'image_data_url': 'data:image/png;base64,' + base64.b64encode(pix.tobytes('png')).decode('ascii'),
                            'source_identity': identity}
                finally:
                    session.close()
            except ValueError as exc:
                return False, str(exc), {'code': 'parse_page_unavailable'}
            except Exception:
                return False, '原页预览失败，请查看原件或重试。', {'code': 'parse_page_unavailable'}

    def save_parse_review(self, project_id, kind, doc_id, payload):
        from agent.agent_backend.services.filing_parse_readiness import source_issues
        from agent.agent_backend.services.filing_parse_resolution import apply_compatible_resolutions
        from agent.agent_backend.services.filing_numeric_revision import active_confirmations
        with self._submission_manifest_lock(project_id), self._reference_manifest_lock():
            try:
                manifest, meta, source, path, chunks = self._parse_review_context(project_id, kind, doc_id)
                identity = self._parse_source_identity(source, path)
                saved = meta.get('parse_revision') or {}
                if (not isinstance(payload, dict) or not self._parse_identity_matches(payload.get('source_identity'), identity) or
                        type(payload.get('expected_revision')) is not int or payload['expected_revision'] != saved.get('revision', 0)):
                    return False, '解析或修订已变化，请重新读取核对；当前输入未保存。', {'code': 'parse_revision_conflict'}
                effective, resolved, items = apply_compatible_resolutions(chunks, source_issues(source, kind, chunks),
                    payload.get('items'), active_confirmations(meta) if kind == 'submission' else [])
                from agent.agent_backend.services.filing_numeric_revision import resolved_numeric_issues
                resolved |= resolved_numeric_issues(chunks, source_issues(source, kind, chunks), meta)
                revision = {'source_identity': identity, 'revision': saved.get('revision', 0) + 1,
                            'source_status': source.get('parse_status'),
                            'items': items, 'confirmed_at': self._now().isoformat()}
                if saved:
                    meta.setdefault('parse_revision_history', []).append(saved)
                meta['parse_revision'] = revision
                if kind == 'application_form':
                    from agent.agent_backend.utils.parser.drug_supplement_pdf_parser import build_pdf_form_from_pages
                    rebuilt = build_pdf_form_from_pages(effective, source_file=source['file_name'])
                    # 允许分步保存：其他尚未处理的原字段问题继续阻塞审评，
                    # 但不能禁止保存本区域已完成的修订。新增/改变的归属问题拒绝。
                    def field_evidence(error, page):
                        return (page, error.get('code'), error.get('text'),
                                error.get('bbox_pdf'), error.get('candidate_items'))
                    pending_field_evidence = [
                        field_evidence(error, chunk.get('page'))
                        for ci, chunk in enumerate(chunks)
                        for ei, error in enumerate(chunk.get('errors', []))
                        if error.get('code') in ('field_region_crossing', 'field_region_unassigned')
                        and f'chunk:{ci}:error:{ei}' not in resolved]
                    if any(field_evidence(error, error.get('page')) not in pending_field_evidence
                           for error in rebuilt.get('unassigned_regions', [])):
                        raise ValueError('修订产生了新的字段归属问题，请核对完整字段；其他尚未处理的原问题仍保留')
                    fields = FilingChangeFormParserService().form_from_pdf_result(rebuilt)
                    def bind(node):
                        if isinstance(node, dict):
                            if 'field_type' in node or 'source_regions' in node:
                                node.update(source_file=source['file_name'], source_file_id=doc_id)
                            for value in node.values():
                                bind(value)
                        elif isinstance(node, list):
                            for value in node:
                                bind(value)
                    bind(fields['form_json'])
                    revision.update(form_json=fields['form_json'], raw_text=fields['raw_text'])
                    self._save_submission_manifest(project_id, manifest)
                    return True, '已保存申请表区域修订；人工字段和原始解析保留。', {'revision': revision['revision'], 'resolved_issue_keys': sorted(resolved)}
                # 自动提取缓存在修订副本上重建；显式人工字段仍优先保留。
                fresh = self._extract_structured_payload(source['file_name'], effective)
                previous = meta.get('extracted_json') or {}
                for key in previous.get('manual_fields', []):
                    fresh.setdefault('automatic_candidates', {})[key] = fresh.get(key)
                    fresh[key] = previous.get(key)
                fresh['manual_fields'] = previous.get('manual_fields', [])
                # 不覆盖原始提取缓存；读取有效修订时使用该副本，过期后自动失效。
                revision['extracted_json'] = fresh
                if kind == 'submission':
                    self._save_submission_manifest(project_id, manifest)
                else:
                    self._save_reference_manifest(manifest)
                return True, '已保存人工修订；原始解析和历史报告保持不变。', {'revision': revision['revision'], 'resolved_issue_keys': sorted(resolved)}
            except ValueError as exc:
                return False, str(exc), {'code': 'parse_revision_invalid'}
            except Exception:
                return False, '修订保存失败，输入未保存，请重试。', {'code': 'parse_revision_save_failed'}

    def _prepare_review_run_once(self, project_id: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        session = self.db_conn.get_session()
        now = self._now()
        run_id = f"fcrr_{uuid.uuid4().hex[:16]}"
        try:
            project = (
                session.query(FilingChangeProject)
                .filter(FilingChangeProject.project_id == project_id, FilingChangeProject.deleted == False)
                .first()
            )
            if project is None:
                return False, "project not found", None
            readiness = self.get_parse_readiness(project_id)
            if not readiness['ready']:
                return False, '请先处理解析问题并补齐必需资料，再启动审评。', {
                    key: value for key, value in readiness.items() if not key.startswith('_')}
            project.review_status = "running"
            project.updated_at = now
            run_row = FilingChangeReviewRun(
                run_id=run_id,
                project_id=project_id,
                task_type=project.task_type,
                status="running",
                started_at=now,
                finished_at=None,
                result_json=self._json({'input_identity': readiness['_input_identity']}),
                error_message="",
            )
            session.add(run_row)
            session.commit()
        except Exception as exc:
            session.rollback()
            session.close()
            return False, str(exc), None
        finally:
            session.close()
        return True, "success", {"run_id": run_id}

    def execute_review_run(self, project_id: str, run_id: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        with self._submission_manifest_lock(project_id):
            readiness = self.get_parse_readiness(project_id)
            session = self.db_conn.get_session()
            try:
                run = session.query(FilingChangeReviewRun).filter(
                    FilingChangeReviewRun.project_id == project_id, FilingChangeReviewRun.run_id == run_id).first()
                if run is None or run.status != 'running':
                    return False, '审评运行不存在或已结束，不能重复执行。', {'code': 'review_run_not_active'}
                saved = self._safe_json_load(run.result_json, {}) if run else {}
            finally:
                session.close()
            if not readiness['ready'] or saved.get('input_identity') != readiness['_input_identity']:
                self.mark_review_run_interrupted(project_id, run_id, '审评资料已变化或解析尚未就绪，请重新核对后启动。')
                return False, '审评资料已变化或解析尚未就绪，请重新核对后启动。', {
                    **{key: value for key, value in readiness.items() if not key.startswith('_')},
                    'code': 'parse_review_required'}
            return self._run_review_once(project_id, run_id)

    def mark_review_run_interrupted(
        self,
        project_id: str,
        run_id: str,
        message: str,
    ) -> Tuple[bool, str, Dict[str, Any]]:
        """将确认中断的审评运行从 running 原子转为 failed，不覆盖已完成结果。"""
        normalized_message = str(message or "").strip() or "任务执行进程已中断，请重新发起任务"
        with self._submission_manifest_lock(project_id):
            session = self.db_conn.get_session()
            now = self._now()
            try:
                active_project = (
                    session.query(FilingChangeProject)
                    .filter(FilingChangeProject.project_id == project_id, FilingChangeProject.deleted == False)
                    .first()
                )
                if active_project is None:
                    return False, "project not found", {
                        "run_id": run_id,
                        "project_id": project_id,
                        "run_updated": False,
                        "project_updated": False,
                    }
                run_updated = (
                    session.query(FilingChangeReviewRun)
                    .filter(
                        FilingChangeReviewRun.run_id == run_id,
                        FilingChangeReviewRun.project_id == project_id,
                        FilingChangeReviewRun.status == "running",
                    )
                    .update(
                        {
                            FilingChangeReviewRun.status: "failed",
                            FilingChangeReviewRun.finished_at: now,
                            FilingChangeReviewRun.error_message: normalized_message,
                        },
                        synchronize_session=False,
                    )
                )
                project_updated = 0
                if run_updated:
                    other_running_count = (
                        session.query(FilingChangeReviewRun)
                        .filter(
                            FilingChangeReviewRun.project_id == project_id,
                            FilingChangeReviewRun.run_id != run_id,
                            FilingChangeReviewRun.status == "running",
                        )
                        .count()
                    )
                    if other_running_count == 0:
                        project_updated = (
                            session.query(FilingChangeProject)
                            .filter(
                                FilingChangeProject.project_id == project_id,
                                FilingChangeProject.deleted == False,
                                FilingChangeProject.review_status == "running",
                            )
                            .update(
                                {
                                    FilingChangeProject.review_status: "failed",
                                    FilingChangeProject.updated_at: now,
                                },
                                synchronize_session=False,
                            )
                        )
                session.commit()
                return True, "success", {
                    "run_id": run_id,
                    "project_id": project_id,
                    "run_updated": bool(run_updated),
                    "project_updated": bool(project_updated),
                }
            except Exception as exc:
                session.rollback()
                return False, str(exc), {
                    "run_id": run_id,
                    "project_id": project_id,
                    "run_updated": False,
                    "project_updated": False,
                }
            finally:
                session.close()

    def _run_review_once(self, project_id: str, run_id: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        session = self.db_conn.get_session()
        started_now = self._now()
        report_snapshots: Dict[Path, Tuple[bool, bytes]] = {}
        try:
            active_project = (
                session.query(FilingChangeProject)
                .filter(FilingChangeProject.project_id == project_id, FilingChangeProject.deleted == False)
                .first()
            )
            if active_project is None:
                return False, "project not found", None
            form_ok, _, form_data = self.get_application_form(project_id)
            submission_rows = session.query(FilingChangeSubmissionFile).filter(FilingChangeSubmissionFile.project_id == project_id).all()
            manifest = self._load_submission_manifest(project_id)
            doc_meta = manifest.get("doc_meta", {})
            rules_data = self.rule_service.list_rules({"enabled": True, "task_type": "extend_validity_period"})
            for row in submission_rows:
                doc_meta.setdefault(row.doc_id, {})
            doc_meta = self._merge_runtime_parse_attempts(project_id, doc_meta)
            selected_submissions = [
                {
                    "doc_id": r.doc_id,
                    "file_name": r.file_name,
                    "material_category": (doc_meta.get(r.doc_id, {}) or {}).get("material_category", r.material_category),
                    "material_sub_category": (doc_meta.get(r.doc_id, {}) or {}).get("material_sub_category", ""),
                    "storage_path": r.storage_path,
                    "quality_tags": (doc_meta.get(r.doc_id, {}) or {}).get("quality_tags", []),
                    "extracted_json": (doc_meta.get(r.doc_id, {}) or {}).get("extracted_json", {}),
                    "parse_status": r.parse_status,
                    "latest_attempt": (doc_meta.get(r.doc_id, {}) or {}).get('latest_attempt', {}),
                    "parse_diagnostics": (doc_meta.get(r.doc_id, {}) or {}).get('parse_diagnostics', {}),
                }
                for r in submission_rows
                if bool((doc_meta.get(r.doc_id, {}) or {}).get("review_enabled", True))
            ]
            parsed_map = self._load_parsed_submission_map(project_id, [x.get("doc_id", "") for x in selected_submissions])
            for submission in selected_submissions:
                meta = doc_meta.get(submission['doc_id'], {})
                saved = meta.get('parse_revision') or {}
                path = self._project_path(project_id) / 'parsed' / f"{submission['doc_id']}.json"
                source = {**meta, **submission}
                resolved = set()
                if saved and self._parse_identity_matches(saved.get('source_identity'), self._parse_source_identity(source, path)):
                    from agent.agent_backend.services.filing_parse_readiness import source_issues
                    original = self._safe_json_load(path.read_text(encoding='utf-8'), [])
                    _, resolved = self._apply_parse_revision(meta, source, 'submission', path, original)
                    pending = [i for i in source_issues(source, 'submission', original) if i['issue_key'] not in resolved]
                    submission['parse_resolution'] = {'revision': saved['revision'],
                        'manually_reviewed': bool(resolved) and not pending, 'unresolved_count': len(pending),
                        'original_status': submission['parse_status']}
                if meta.get('numeric_revision') and path.is_file():
                    from agent.agent_backend.services.filing_numeric_revision import resolved_numeric_issues
                    from agent.agent_backend.services.filing_parse_readiness import source_issues
                    original = self._safe_json_load(path.read_text(encoding='utf-8'), [])
                    problems = source_issues(source, 'submission', original)
                    numeric_resolved = resolved_numeric_issues(original, problems, meta)
                    if numeric_resolved:
                        resolved |= numeric_resolved
                        pending = [i for i in problems if i['issue_key'] not in resolved]
                        submission['parse_resolution'] = {'revision': max(saved.get('revision', 0), meta['numeric_revision'].get('revision', 0)),
                            'numeric_revision': meta['numeric_revision'].get('revision', 0),
                            'manually_reviewed': not pending, 'unresolved_count': len(pending),
                            'original_status': submission['parse_status']}
                if any(chunk.get('manual_content_applied') for chunk in parsed_map[submission['doc_id']]) or any(cell.get('manual_confirmation') for chunk in parsed_map[submission['doc_id']]
                       for table in chunk.get('tables', []) for cell in table.get('cells', [])):
                    old = submission.get('extracted_json') or {}
                    fresh = self._extract_structured_payload(submission['file_name'], parsed_map[submission['doc_id']])
                    for field in old.get('manual_fields', []):
                        fresh.setdefault('automatic_candidates', {})[field] = fresh.get(field)
                        fresh[field] = old.get(field)
                    fresh['manual_fields'] = old.get('manual_fields', [])
                    submission['extracted_json'] = fresh
            completeness = self.check_submission_completeness(project_id)
            run_result = self.orchestrator.run(
                project_id=project_id,
                application_form=form_data if form_ok else {},
                submissions=selected_submissions,
                rules=rules_data.get("list", []),
                references=[],  # 当前没有参考正文消费路径，不伪造采用清单。
                parsed_submission_map=parsed_map,
                completeness=completeness,
            )

            finished_now = self._now()
            run_result['reference_usage'] = {'status': 'not_used', 'doc_ids': [],
                'message': '本轮未自动采用参考资料正文；证据清单仅列实际使用的项目资料与规则。'}
            run_result.update(run_id=run_id, review_completed_at=finished_now.strftime('%Y-%m-%d %H:%M:%S'))

            result_row = FilingChangeReviewResult(
                run_id=run_id,
                project_id=project_id,
                formal_review_json=self._json(run_result.get("formal_review", {})),
                category_suggestion_json=self._json(run_result.get("change_category_suggestion", {})),
                quality_check_json=self._json(run_result.get("quality_standard_check", {})),
                stability_analysis_json=self._json(run_result.get("stability_trend_analysis", {})),
                risk_points_json=self._json(run_result.get("risk_points", [])),
                evidence_json=self._json(run_result.get("evidence_refs", [])),
                conclusion_json=self._json(run_result),
                created_at=started_now,
            )
            session.add(result_row)

            report_snapshots = self._snapshot_report_files(project_id, run_id)
            run_result["report_generated_at"] = self._now().strftime("%Y-%m-%d %H:%M:%S")
            report = self.report_service.generate_report(
                project_id=project_id,
                run_id=run_id,
                result_payload=run_result,
            )
            run_result['report_manual_revision'] = 0
            run_result['review_report_markdown'] = report.get('markdown', '')
            result_row.conclusion_json = self._json(run_result)
            report_row = FilingChangeReviewReport(
                report_id=f"fcrpt_{uuid.uuid4().hex[:16]}",
                project_id=project_id,
                run_id=run_id,
                report_type="review_report",
                report_content=report.get("markdown", ""),
                report_file_path=report.get("word_path", ""),
                created_at=datetime.strptime(run_result["report_generated_at"], "%Y-%m-%d %H:%M:%S"),
                updated_at=datetime.strptime(run_result["report_generated_at"], "%Y-%m-%d %H:%M:%S"),
            )
            session.add(report_row)

            run_row = session.query(FilingChangeReviewRun).filter(FilingChangeReviewRun.run_id == run_id).first()
            if run_row:
                execution_input = self._safe_json_load(run_row.result_json, {}).get('input_identity')
                run_row.status = "completed"
                run_row.finished_at = finished_now
                # 保留本轮已检查的输入身份，仅存运行记录，不扩散到报告或接口正文。
                run_row.result_json = self._json({**run_result, 'input_identity': execution_input})

            project_row = session.query(FilingChangeProject).filter(FilingChangeProject.project_id == project_id).first()
            if project_row:
                project_row.review_status = "completed"
                project_row.report_status = "generated"
                project_row.updated_at = finished_now
            session.commit()
            return True, "success", run_result
        except Exception as exc:
            session.rollback()
            error_message = str(exc)
            if report_snapshots:
                try:
                    self._restore_report_file_snapshots(report_snapshots)
                except Exception as restore_exc:
                    error_message = f"{error_message}; report file rollback failed: {restore_exc}"
            run_row = session.query(FilingChangeReviewRun).filter(FilingChangeReviewRun.run_id == run_id).first()
            project_row = session.query(FilingChangeProject).filter(FilingChangeProject.project_id == project_id).first()
            failure_result = None
            if isinstance(exc, (LLMContextError, LLMExecutionError)):
                failure_result = {"run_id": run_id, "review_complete": False, "error": exc.as_dict(),
                                  "incomplete_stages": [exc.stage, "complete_report"],
                                  "partial_result": getattr(exc, 'partial_result', {})}
            if run_row:
                run_row.status = "failed"
                run_row.finished_at = self._now()
                run_row.error_message = error_message
                if failure_result is not None:
                    run_row.result_json = self._json(failure_result)
            if project_row:
                project_row.review_status = "failed"
                project_row.updated_at = self._now()
            try:
                session.commit()
            except Exception as status_exc:
                session.rollback()
                return False, f"{error_message}; failed to persist failure status: {status_exc}", None
            if isinstance(exc, (LLMContextError, LLMExecutionError)):
                return False, error_message, failure_result
            return False, error_message, None
        finally:
            session.close()

    def get_review_history(self, project_id: str) -> Dict[str, Any]:
        session = self.db_conn.get_session()
        try:
            project = (
                session.query(FilingChangeProject)
                .filter(FilingChangeProject.project_id == project_id, FilingChangeProject.deleted == False)
                .first()
            )
            if project is None:
                return {"list": [], "project_exists": False}
            rows = (
                session.query(FilingChangeReviewRun)
                .filter(FilingChangeReviewRun.project_id == project_id)
                .order_by(FilingChangeReviewRun.started_at.desc())
                .all()
            )
            return {
                "list": [
                    {
                        "run_id": r.run_id,
                        "status": r.status,
                        "task_type": r.task_type,
                        "started_at": r.started_at.strftime("%Y-%m-%d %H:%M:%S") if r.started_at else "",
                        "finished_at": r.finished_at.strftime("%Y-%m-%d %H:%M:%S") if r.finished_at else "",
                        "error_message": r.error_message or "",
                    }
                    for r in rows
                ]
            }
        finally:
            session.close()

    def _review_input_state(self, project_id, stored):
        """只读比较历史运行与当前资料；未知不能伪装成当前有效。"""
        identity = stored.get('input_identity') if isinstance(stored, dict) else None
        if not isinstance(identity, list) or not identity:
            return {'status': 'unknown', 'message': '此前结果未记录完整资料身份，不能确认适用于当前资料。'}
        try:
            current = self.get_parse_readiness(project_id)
        except Exception:
            return {'status': 'unknown', 'message': '当前资料状态暂无法核对；以下仍为此前审评结果。'}
        if current.get('_input_identity') != identity or not current.get('ready'):
            return {'status': 'stale', 'message': '资料或解析状态已变化；以下为此前结果，不代表当前资料已完成审评。'}
        return {'status': 'current', 'message': '本轮资料身份与当前一致；审评结论仍以本轮结果为准。'}

    def get_run_result(self, run_id: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        session = self.db_conn.get_session()
        try:
            run_row = session.query(FilingChangeReviewRun).filter(FilingChangeReviewRun.run_id == run_id).first()
            if run_row is None:
                return False, "run result not found", None
            project = (
                session.query(FilingChangeProject)
                .filter(FilingChangeProject.project_id == run_row.project_id, FilingChangeProject.deleted == False)
                .first()
            )
            if project is None:
                return False, "run result not found", None
            stored = self._safe_json_load(run_row.result_json, {})
            if not isinstance(stored, dict):
                stored = {}
            if run_row.status != 'completed' or stored.get('review_complete') is False:
                partial = stored.get('partial_result')
                partial = partial if isinstance(partial, dict) else {}
                # 失败记录不带出历史报告/人工确认，原异常全文也不进入读取响应。
                allowed = ('formal_review', 'consistency_check', 'quality_standard_check', 'stability_trend_analysis',
                           'rule_results', 'evidence_refs', 'field_check')
                visible = {key: partial[key] for key in allowed if key in partial}
                error = stored.get('error') if isinstance(stored.get('error'), dict) else {}
                safe_error = {key: error[key] for key in ('code', 'stage', 'actual_chars', 'limit_chars', 'http_status', 'retryable') if key in error}
                return True, 'success', {
                    **visible, 'run_id': run_id, 'project_id': run_row.project_id,
                    'execution_status': run_row.status, 'review_complete': False,
                    'partial_result': visible, 'completed_stages': list(visible),
                    'incomplete_stages': stored.get('incomplete_stages') or ['未完成范围未记录，需核对本次任务'],
                    'error': safe_error or {'code': 'review_incomplete', 'stage': 'unknown'},
                    'failure_message': '本次审评未完成；以下仅为已保存的部分结果，不代表完整结论。',
                }
            row = session.query(FilingChangeReviewResult).filter(FilingChangeReviewResult.run_id == run_id,
                    FilingChangeReviewResult.project_id == run_row.project_id).first()
            if row is None:
                return False, "run result not found", None
            result = result_from_rows(row, run_row, self._safe_json_load)
            result.update(execution_status=run_row.status, review_complete=True)
            result['input_state'] = self._review_input_state(run_row.project_id, stored)
            return True, 'success', result
        finally:
            session.close()

    def generate_report(self, run_id: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        lookup_session = self.db_conn.get_session()
        try:
            lookup_run = lookup_session.query(FilingChangeReviewRun).filter(FilingChangeReviewRun.run_id == run_id).first()
            if lookup_run is None:
                return False, "run not found", None
            if lookup_run.status != 'completed' or self._safe_json_load(lookup_run.result_json, {}).get('review_complete') is False:
                return False, "本次审评未完成，不能生成完整报告", None
            project_id = str(lookup_run.project_id or "")
        finally:
            lookup_session.close()
        with self._submission_manifest_lock(project_id):
            return self._generate_report_once(run_id)

    def _generate_report_once(self, run_id: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        session = self.db_conn.get_session()
        report_snapshots: Dict[Path, Tuple[bool, bytes]] = {}
        committed = False
        try:
            run_row = session.query(FilingChangeReviewRun).filter(FilingChangeReviewRun.run_id == run_id).first()
            if run_row is None:
                return False, "run not found", None
            if run_row.status != 'completed' or self._safe_json_load(run_row.result_json, {}).get('review_complete') is False:
                return False, "本次审评未完成，不能生成完整报告", None
            project = (
                session.query(FilingChangeProject)
                .filter(FilingChangeProject.project_id == run_row.project_id, FilingChangeProject.deleted == False)
                .first()
            )
            if project is None:
                return False, "run not found", None
            result_row = session.query(FilingChangeReviewResult).filter(FilingChangeReviewResult.run_id == run_id).first()
            if result_row is None:
                return False, "run result not found", None
            payload = result_from_rows(result_row, run_row, self._safe_json_load)
            report_snapshots = self._snapshot_report_files(result_row.project_id, run_id)
            payload["report_generated_at"] = self._now().strftime("%Y-%m-%d %H:%M:%S")
            report = self.report_service.generate_report(
                project_id=result_row.project_id,
                run_id=run_id,
                result_payload=payload,
            )
            ext = self._safe_json_load(result_row.conclusion_json, {})
            ext.update(review_report_markdown=report.get('markdown', ''), report_generated_at=payload['report_generated_at'], report_manual_revision=(payload.get('manual_confirmation') or {}).get('revision', 0))
            result_row.conclusion_json = self._json(ext)
            now = datetime.strptime(payload["report_generated_at"], "%Y-%m-%d %H:%M:%S")
            report_row = (
                session.query(FilingChangeReviewReport)
                .filter(FilingChangeReviewReport.run_id == run_id)
                .order_by(FilingChangeReviewReport.updated_at.desc())
                .first()
            )
            if report_row is None:
                report_row = FilingChangeReviewReport(
                    report_id=f"fcrpt_{uuid.uuid4().hex[:16]}",
                    project_id=result_row.project_id,
                    run_id=run_id,
                    report_type="review_report",
                    report_content=report.get("markdown", ""),
                    report_file_path=report.get("word_path", ""),
                    created_at=now,
                    updated_at=now,
                )
                session.add(report_row)
            else:
                report_row.report_content = report.get("markdown", "")
                report_row.report_file_path = report.get("word_path", "")
                report_row.updated_at = now
            session.commit()
            committed = True
            return True, "success", {
                "report_id": report_row.report_id,
                "project_id": report_row.project_id,
                "run_id": run_id,
                "report_type": report_row.report_type,
                "report_content": report_row.report_content,
                "report_file_path": report_row.report_file_path,
                "input_state": self._review_input_state(run_row.project_id, self._safe_json_load(run_row.result_json, {})),
                "updated_at": report_row.updated_at.strftime("%Y-%m-%d %H:%M:%S") if report_row.updated_at else "",
            }
        except Exception as exc:
            if not committed:
                session.rollback()
                try:
                    self._restore_report_file_snapshots(report_snapshots)
                except Exception as restore_exc:
                    return False, f"{exc}; report file rollback failed: {restore_exc}", None
            return False, str(exc), None
        finally:
            session.close()

    def get_run_evidence(self, run_id: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        # 证据详情只需要 conclusion/evidence 两列。不经由 get_run_result
        # 解析稳定性、质量、形式审查等全部大 JSON，避免同一数据
        # 在证据页被无关地构造一遍。
        session = self.db_conn.get_session()
        try:
            row = session.query(FilingChangeReviewResult).filter(FilingChangeReviewResult.run_id == run_id).first()
            if row is None:
                return False, "run result not found", None
            run_row = session.query(FilingChangeReviewRun).filter(FilingChangeReviewRun.run_id == run_id).first()
            project = (
                session.query(FilingChangeProject)
                .filter(FilingChangeProject.project_id == row.project_id, FilingChangeProject.deleted == False)
                .first()
            )
            if run_row is None or project is None:
                return False, "run result not found", None
            ext = self._safe_json_load(row.conclusion_json, {})
            return True, "success", {
                "run_id": run_id,
                "matched_rules": ext.get("matched_rules", []) if isinstance(ext, dict) else [],
                "rule_results": ext.get("rule_results", []) if isinstance(ext, dict) else [],
                "rule_summary": ext.get("rule_summary", {}) if isinstance(ext, dict) else {},
                "evidence_refs": self._safe_json_load(row.evidence_json, []),
                "module_evidence": ext.get("module_evidence", {}) if isinstance(ext, dict) else {},
                "llm_calls": ext.get("llm_calls", []) if isinstance(ext, dict) else [],
            }
        finally:
            session.close()

    def generate_correction_notice(self, run_id: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        session = self.db_conn.get_session()
        try:
            row = session.query(FilingChangeReviewResult).filter(FilingChangeReviewResult.run_id == run_id).first()
            if row is None:
                return False, "run result not found", None
            run_row = session.query(FilingChangeReviewRun).filter(FilingChangeReviewRun.run_id == run_id).first()
            project = (
                session.query(FilingChangeProject)
                .filter(FilingChangeProject.project_id == row.project_id, FilingChangeProject.deleted == False)
                .first()
            )
            if run_row is None or project is None:
                return False, "run result not found", None
            ext = self._safe_json_load(row.conclusion_json, {})
            draft = ext.get("correction_notice_draft", {}) if isinstance(ext, dict) else {}
        finally:
            session.close()
        if not draft:
            draft = {"title": "补正通知书草稿", "items": [], "draft_text": "暂无需要补正内容。"}
        return True, "success", {"run_id": run_id, "correction_notice_draft": draft}

    def manual_confirm_run(self, run_id: str, payload: Dict[str, Any]):
        ok, message, result = self.get_run_result(run_id)
        if not ok:
            return False, message, None
        if result.get('review_complete') is not True:
            return False, "本次审评未完成，仅支持只读查看部分结果", None
        with self._submission_manifest_lock(result['project_id']):
            return self._manual_confirm_run_once(run_id, payload)

    def _manual_confirm_run_once(self, run_id: str, payload: Dict[str, Any]) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        session = self.db_conn.get_session()
        try:
            run_row = session.query(FilingChangeReviewRun).filter(FilingChangeReviewRun.run_id == run_id).first()
            if run_row is None:
                return False, "run not found", None
            if run_row.status != 'completed' or self._safe_json_load(run_row.result_json, {}).get('review_complete') is False:
                return False, "本次审评未完成，仅支持只读查看部分结果", None
            project = (
                session.query(FilingChangeProject)
                .filter(FilingChangeProject.project_id == run_row.project_id, FilingChangeProject.deleted == False)
                .first()
            )
            if project is None:
                return False, "run not found", None
            result_json = self._safe_json_load(run_row.result_json, {})
            previous = result_json.get('manual_confirmation') or {}
            revision = previous.get('revision', 0)
            expected = payload.get('expected_revision')
            if (previous and expected is None) or (expected is not None and expected != revision):
                return False, '人工意见已更新或页面未提供版本，请刷新核对后再保存', None
            result_json["manual_confirmation"] = {
                "revision": revision + 1, "run_id": run_id,
                "confirmed": bool(payload.get("confirmed", False)),
                "comment": str(payload.get("comment", "")).strip(),
                "reviewer": str(payload.get("reviewer", "")).strip(),
                "confirmed_at": self._now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            run_row.result_json = self._json(result_json)
            result_row = session.query(FilingChangeReviewResult).filter(FilingChangeReviewResult.run_id == run_id).first()
            if result_row is not None:
                conclusion_json = self._safe_json_load(result_row.conclusion_json, {})
                if not isinstance(conclusion_json, dict):
                    conclusion_json = {}
                conclusion_json["manual_confirmation"] = result_json["manual_confirmation"]
                result_row.conclusion_json = self._json(conclusion_json)
            session.commit()
            return True, "success", {"run_id": run_id, "manual_confirmation": result_json["manual_confirmation"]}
        except Exception as exc:
            session.rollback()
            return False, str(exc), None
        finally:
            session.close()

    def get_project_latest_report(self, project_id: str, run_id: str = "") -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        session = self.db_conn.get_session()
        try:
            project = (
                session.query(FilingChangeProject)
                .filter(FilingChangeProject.project_id == project_id, FilingChangeProject.deleted == False)
                .first()
            )
            if project is None:
                return False, "project not found", None
            query = session.query(FilingChangeReviewReport).filter(FilingChangeReviewReport.project_id == project_id)
            if run_id:
                run = session.query(FilingChangeReviewRun).filter(FilingChangeReviewRun.project_id == project_id, FilingChangeReviewRun.run_id == run_id).first()
                if run is None:
                    return False, 'run not found in project', None
                stored = self._safe_json_load(run.result_json, {})
                if run.status != 'completed' or not isinstance(stored, dict) or stored.get('review_complete') is False:
                    return False, '本次审评未完成，不能读取完整报告', None
                query = query.filter(FilingChangeReviewReport.run_id == run_id)
            report_row = query.order_by(FilingChangeReviewReport.updated_at.desc()).first()
            if report_row is None:
                return True, "success", {
                    "report_id": "",
                    "project_id": project_id,
                    "run_id": "",
                    "report_type": "",
                    "report_content": "",
                    "report_file_path": "",
                }
            report_run = session.query(FilingChangeReviewRun).filter(
                FilingChangeReviewRun.run_id == report_row.run_id,
                FilingChangeReviewRun.project_id == project_id).first()
            stored = self._safe_json_load(report_run.result_json, {}) if report_run else {}
            if report_run is None or report_run.status != 'completed' or not isinstance(stored, dict) or stored.get('review_complete') is False:
                return False, '本次审评未完成，不能读取完整报告', None
            return True, "success", {
                "report_id": report_row.report_id,
                "project_id": report_row.project_id,
                "run_id": report_row.run_id,
                "report_type": report_row.report_type,
                "report_content": report_row.report_content,
                "report_file_path": report_row.report_file_path,
                "input_state": self._review_input_state(project_id, stored),
            }
        finally:
            session.close()

    def export_report_word(self, report_id: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        session = self.db_conn.get_session()
        try:
            report_row = session.query(FilingChangeReviewReport).filter(FilingChangeReviewReport.report_id == report_id).first()
            if report_row is None:
                return False, "report not found", None
            run_row = session.query(FilingChangeReviewRun).filter(FilingChangeReviewRun.run_id == report_row.run_id,
                    FilingChangeReviewRun.project_id == report_row.project_id).first()
            if run_row is None or run_row.status != 'completed' or self._safe_json_load(run_row.result_json, {}).get('review_complete') is False:
                return False, "本次审评未完成，不能导出完整报告", None
            project = (
                session.query(FilingChangeProject)
                .filter(FilingChangeProject.project_id == report_row.project_id, FilingChangeProject.deleted == False)
                .first()
            )
            if project is None:
                return False, "report not found", None
            result_row = session.query(FilingChangeReviewResult).filter(FilingChangeReviewResult.run_id == report_row.run_id).first()
            if result_row is not None:
                saved = self._safe_json_load(result_row.conclusion_json, {})
                revision = (saved.get('manual_confirmation') or {}).get('revision', 0)
                if revision != saved.get('report_manual_revision', 0):
                    ok, message, generated = self.generate_report(report_row.run_id)
                    if not ok:
                        return False, message, None
                    return True, 'success', {'file_path': generated['report_file_path']}
            path = str(report_row.report_file_path or "").strip()
            if not path or not os.path.exists(path):
                return False, "report file not found", None
            return True, "success", {"file_path": path}
        finally:
            session.close()
