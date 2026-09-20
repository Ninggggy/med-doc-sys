import json
import logging
import os
import socket
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, Optional
from weakref import WeakSet

from sqlalchemy import and_, inspect, or_, text
from sqlalchemy.exc import OperationalError, ProgrammingError

from agent.agent_backend.database.mysql.db_model import PreReviewUploadTask, RuntimeTask
from agent.agent_backend.database.mysql.mysql_conn import MysqlConnection


logger = logging.getLogger(__name__)
_UNSET = object()
_process_instance_lock = threading.Lock()
_process_instance_pid: Optional[int] = None
_process_instance_id = ""
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = max(
    1.0,
    float(os.getenv("RUNTIME_TASK_HEARTBEAT_INTERVAL_SECONDS", "10") or 10),
)
DEFAULT_STALE_AFTER_SECONDS = max(
    DEFAULT_HEARTBEAT_INTERVAL_SECONDS * 3,
    float(os.getenv("RUNTIME_TASK_STALE_AFTER_SECONDS", "120") or 120),
)


def get_process_instance_id() -> str:
    """按当前 PID 惰性生成 Worker 标识。

    Gunicorn --preload 会在 master 中先导入模块再 fork，
    因此不能把导入时生成的常量当作 Worker 标识。
    """
    global _process_instance_pid, _process_instance_id
    current_pid = os.getpid()
    if _process_instance_pid == current_pid and _process_instance_id:
        return _process_instance_id
    with _process_instance_lock:
        if _process_instance_pid != current_pid or not _process_instance_id:
            _process_instance_pid = current_pid
            _process_instance_id = f"{socket.gethostname()}:{current_pid}:{uuid.uuid4().hex[:12]}"
    return _process_instance_id


class RuntimeTaskStore:
    """使用共享数据库保存后台任务状态。

    实例本身不持有任务内存缓存，每次查询都读取数据库，
    因此可被不同 Worker 和重启后的新进程共享。
    """

    _schema_lock = threading.Lock()
    _initialized_engines = WeakSet()

    def __init__(self, connection=None, ensure_schema: bool = True):
        self.db = connection or MysqlConnection()
        if ensure_schema:
            self._ensure_schema()

    def _ensure_schema(self) -> None:
        engine = self.db.engine
        if engine in self._initialized_engines:
            return
        with self._schema_lock:
            if engine in self._initialized_engines:
                return
            try:
                RuntimeTask.__table__.create(bind=engine, checkfirst=True)
            except (OperationalError, ProgrammingError):
                # 多 Worker 同时首次启动时可能并发创建表；
                # 如果表已经由另一进程创建，则可继续使用。
                if not inspect(engine).has_table(RuntimeTask.__tablename__):
                    raise
            self._ensure_runtime_task_lease_columns(engine)
            self._initialized_engines.add(engine)

    @staticmethod
    def _ensure_runtime_task_lease_columns(engine) -> None:
        column_names = {str(item.get("name", "")) for item in inspect(engine).get_columns(RuntimeTask.__tablename__)}
        column_ddl = {
            "worker_id": "VARCHAR(128) NULL",
            "heartbeat_at": "DOUBLE NULL" if engine.dialect.name == "mysql" else "FLOAT NULL",
        }
        for column_name, ddl_type in column_ddl.items():
            if column_name in column_names:
                continue
            try:
                with engine.begin() as connection:
                    connection.execute(
                        text(f"ALTER TABLE runtime_task ADD COLUMN {column_name} {ddl_type}")
                    )
            except (OperationalError, ProgrammingError):
                refreshed_columns = {
                    str(item.get("name", ""))
                    for item in inspect(engine).get_columns(RuntimeTask.__tablename__)
                }
                if column_name not in refreshed_columns:
                    raise

        existing_indexes = {
            str(item.get("name", ""))
            for item in inspect(engine).get_indexes(RuntimeTask.__tablename__)
        }
        for index_name in {"ix_runtime_task_worker_id", "ix_runtime_task_heartbeat_at"}:
            if index_name in existing_indexes:
                continue
            index = next((item for item in RuntimeTask.__table__.indexes if item.name == index_name), None)
            if index is None:
                continue
            try:
                index.create(bind=engine, checkfirst=True)
            except (OperationalError, ProgrammingError):
                refreshed_indexes = {
                    str(item.get("name", ""))
                    for item in inspect(engine).get_indexes(RuntimeTask.__tablename__)
                }
                if index_name not in refreshed_indexes:
                    raise

    @staticmethod
    def _json_dumps(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, default=str)

    def _begin_write_transaction(self, session) -> None:
        # SQLite 忽略 SELECT ... FOR UPDATE，需在读-改-写 JSON 前
        # 显式获取写锁，否则多线程/多进程追加日志会相互覆盖。
        if self.db.engine.dialect.name == "sqlite":
            session.execute(text("BEGIN IMMEDIATE"))

    @staticmethod
    def _json_loads(value: Any, default: Any) -> Any:
        if value in (None, ""):
            return default
        try:
            return json.loads(value)
        except Exception:
            logger.warning("runtime task JSON is invalid; fallback to default value")
            return default

    def _append_log_to_row(
        self,
        row: RuntimeTask,
        event_payload: Optional[Dict[str, Any]],
        *,
        max_logs: int,
        assign_sequence: bool,
        timestamp: float,
    ) -> None:
        logs = self._json_loads(row.logs_json, [])
        if not isinstance(logs, list):
            logs = []
        item = dict(event_payload or {})
        item.setdefault("time", time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp)))
        if assign_sequence:
            item["seq"] = int(row.log_offset or 0) + len(logs)
        logs.append(item)
        limit = max(1, int(max_logs or 1))
        if len(logs) > limit:
            removed = len(logs) - limit
            logs = logs[-limit:]
            row.log_offset = int(row.log_offset or 0) + removed
        row.logs_json = self._json_dumps(logs)

    def _mirror_pre_review_upload_task(
        self,
        session,
        row: RuntimeTask,
        *,
        status: str,
        message: str,
        result: Any = _UNSET,
        error_message: str = "",
        timestamp: float,
    ) -> None:
        if str(row.domain or "") != "pre_review" or str(row.task_type or "") != "upload_submission":
            return
        upload_row = (
            session.query(PreReviewUploadTask)
            .filter(PreReviewUploadTask.task_id == str(row.task_id or ""))
            .with_for_update()
            .one_or_none()
        )
        if upload_row is None:
            return
        upload_row.status = str(status or "")
        upload_row.message = str(message or "")
        upload_row.error_message = str(error_message or "")
        if result is not _UNSET:
            upload_row.result_json = self._json_dumps(result)
        current_time = datetime.fromtimestamp(timestamp)
        upload_row.update_time = current_time
        upload_row.finish_time = current_time if status in {"completed", "failed"} else None

    @classmethod
    def _serialize_row(cls, row: RuntimeTask) -> Dict[str, Any]:
        return {
            "task_id": str(row.task_id or ""),
            "domain": str(row.domain or ""),
            "project_id": str(row.project_id or ""),
            "run_id": str(row.run_id or ""),
            "source_doc_id": str(row.source_doc_id or ""),
            "section_id": str(row.section_id or ""),
            "task_type": str(row.task_type or "run"),
            "status": str(row.status or "pending"),
            "message": str(row.message or ""),
            "payload": cls._json_loads(row.payload_json, {}),
            "result": cls._json_loads(row.result_json, None),
            "logs": cls._json_loads(row.logs_json, []),
            "log_offset": int(row.log_offset or 0),
            "error_message": str(row.error_message or ""),
            "worker_id": str(row.worker_id or ""),
            "heartbeat_at": float(row.heartbeat_at or 0),
            "created_at": float(row.created_at or 0),
            "updated_at": float(row.updated_at or 0),
            "finished_at": float(row.finished_at or 0),
        }

    def create_task(
        self,
        *,
        task_id: str,
        domain: str,
        project_id: str,
        task_type: str = "run",
        run_id: str = "",
        source_doc_id: str = "",
        section_id: str = "",
        status: str = "pending",
        message: str = "",
        payload: Optional[Dict[str, Any]] = None,
        worker_id: Optional[str] = None,
        now: Optional[float] = None,
    ) -> Dict[str, Any]:
        normalized_task_id = str(task_id or "").strip()
        normalized_domain = str(domain or "").strip()
        if not normalized_task_id or not normalized_domain:
            raise ValueError("task_id and domain are required")

        current_ts = float(now if now is not None else time.time())
        session = self.db.get_session()
        try:
            row = RuntimeTask(
                task_id=normalized_task_id,
                domain=normalized_domain,
                task_type=str(task_type or "run").strip() or "run",
                project_id=str(project_id or "").strip(),
                run_id=str(run_id or "").strip() or None,
                source_doc_id=str(source_doc_id or "").strip() or None,
                section_id=str(section_id or "").strip() or None,
                status=str(status or "pending").strip() or "pending",
                message=str(message or ""),
                payload_json=self._json_dumps(payload or {}),
                result_json=None,
                logs_json="[]",
                log_offset=0,
                error_message="",
                worker_id=str(worker_id or get_process_instance_id()).strip() or None,
                heartbeat_at=current_ts,
                created_at=current_ts,
                updated_at=current_ts,
                finished_at=None,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return self._serialize_row(row)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def append_log(
        self,
        task_id: str,
        event_payload: Optional[Dict[str, Any]],
        *,
        max_logs: int,
        assign_sequence: bool = False,
        now: Optional[float] = None,
    ) -> bool:
        current_ts = float(now if now is not None else time.time())
        session = self.db.get_session()
        try:
            self._begin_write_transaction(session)
            row = (
                session.query(RuntimeTask)
                .filter(RuntimeTask.task_id == str(task_id or "").strip())
                .with_for_update()
                .one_or_none()
            )
            if row is None:
                return False
            if str(row.status or "") in {"completed", "failed"}:
                # 终态不可逆，防止已被中断恢复的任务又被旧 Worker 改回完成。
                return False
            self._append_log_to_row(
                row,
                event_payload,
                max_logs=max_logs,
                assign_sequence=assign_sequence,
                timestamp=current_ts,
            )
            row.updated_at = current_ts
            session.commit()
            return True
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def finish_task(
        self,
        task_id: str,
        *,
        status: str,
        message: str = "",
        result: Any = _UNSET,
        error_message: str = "",
        final_log: Optional[Dict[str, Any]] = None,
        max_logs: int = 500,
        assign_sequence: bool = True,
        worker_id: Optional[str] = None,
        now: Optional[float] = None,
    ) -> bool:
        normalized_status = str(status or "").strip()
        if normalized_status not in {"completed", "failed"}:
            raise ValueError("finish status must be completed or failed")
        current_ts = float(now if now is not None else time.time())
        normalized_worker_id = str(worker_id or get_process_instance_id()).strip()
        session = self.db.get_session()
        try:
            self._begin_write_transaction(session)
            row = (
                session.query(RuntimeTask)
                .filter(RuntimeTask.task_id == str(task_id or "").strip())
                .with_for_update()
                .one_or_none()
            )
            if row is None or str(row.status or "") != "running":
                return False
            if str(row.worker_id or "").strip() != normalized_worker_id:
                return False
            if final_log is not None:
                self._append_log_to_row(
                    row,
                    final_log,
                    max_logs=max_logs,
                    assign_sequence=assign_sequence,
                    timestamp=current_ts,
                )
            row.status = normalized_status
            row.message = str(message or "")
            row.error_message = str(error_message or "")
            if result is not _UNSET:
                row.result_json = self._json_dumps(result)
            row.updated_at = current_ts
            row.heartbeat_at = current_ts
            row.finished_at = current_ts
            self._mirror_pre_review_upload_task(
                session,
                row,
                status=normalized_status,
                message=str(message or ""),
                result=result,
                error_message=str(error_message or ""),
                timestamp=current_ts,
            )
            self._persist_filing_attempt(session, row)
            session.commit()
            return True
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def update_task(self, task_id: str, *, now: Optional[float] = None, **fields) -> bool:
        current_ts = float(now if now is not None else time.time())
        session = self.db.get_session()
        try:
            self._begin_write_transaction(session)
            row = (
                session.query(RuntimeTask)
                .filter(RuntimeTask.task_id == str(task_id or "").strip())
                .with_for_update()
                .one_or_none()
            )
            if row is None:
                return False
            if str(row.status or "") in {"completed", "failed"}:
                return False

            scalar_fields = {
                "status",
                "message",
                "project_id",
                "run_id",
                "source_doc_id",
                "section_id",
                "task_type",
                "error_message",
                "worker_id",
            }
            for field_name in scalar_fields:
                if field_name in fields:
                    setattr(row, field_name, fields[field_name])
            if "payload" in fields:
                row.payload_json = self._json_dumps(fields["payload"])
            if "result" in fields:
                row.result_json = self._json_dumps(fields["result"])
            if "heartbeat_at" in fields:
                row.heartbeat_at = fields["heartbeat_at"]

            row.updated_at = current_ts
            if str(row.status or "") in {"completed", "failed"}:
                row.finished_at = current_ts
                row.heartbeat_at = current_ts
            elif "status" in fields:
                row.finished_at = None
            session.commit()
            return True
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def claim_task(
        self,
        task_id: str,
        *,
        worker_id: Optional[str] = None,
        now: Optional[float] = None,
    ) -> bool:
        current_ts = float(now if now is not None else time.time())
        normalized_worker_id = str(worker_id or get_process_instance_id()).strip()
        session = self.db.get_session()
        try:
            self._begin_write_transaction(session)
            row = (
                session.query(RuntimeTask)
                .filter(RuntimeTask.task_id == str(task_id or "").strip())
                .with_for_update()
                .one_or_none()
            )
            if row is None or str(row.status or "") != "pending":
                return False
            owner = str(row.worker_id or "").strip()
            if owner and owner != normalized_worker_id:
                return False
            row.status = "running"
            row.worker_id = normalized_worker_id or None
            row.heartbeat_at = current_ts
            row.updated_at = current_ts
            row.finished_at = None
            self._mirror_pre_review_upload_task(
                session,
                row,
                status="running",
                message="上传解析任务已启动",
                error_message="",
                timestamp=current_ts,
            )
            session.commit()
            return True
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def heartbeat(
        self,
        task_id: str,
        *,
        worker_id: Optional[str] = None,
        now: Optional[float] = None,
    ) -> bool:
        current_ts = float(now if now is not None else time.time())
        normalized_worker_id = str(worker_id or get_process_instance_id()).strip()
        session = self.db.get_session()
        try:
            count = (
                session.query(RuntimeTask)
                .filter(
                    RuntimeTask.task_id == str(task_id or "").strip(),
                    RuntimeTask.status == "running",
                    RuntimeTask.worker_id == normalized_worker_id,
                )
                .update(
                    {
                        RuntimeTask.heartbeat_at: current_ts,
                        RuntimeTask.updated_at: current_ts,
                    },
                    synchronize_session=False,
                )
            )
            session.commit()
            return bool(count)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    @contextmanager
    def heartbeat_context(
        self,
        task_id: str,
        *,
        worker_id: Optional[str] = None,
        interval_seconds: float = DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    ):
        normalized_worker_id = str(worker_id or get_process_instance_id()).strip()
        claimed = self.claim_task(task_id, worker_id=normalized_worker_id)
        if not claimed:
            yield False
            return

        stop_event = threading.Event()
        interval = max(1.0, float(interval_seconds or DEFAULT_HEARTBEAT_INTERVAL_SECONDS))

        def _heartbeat_worker() -> None:
            while not stop_event.wait(interval):
                try:
                    if not self.heartbeat(task_id, worker_id=normalized_worker_id):
                        return
                except Exception:
                    logger.exception("runtime task heartbeat failed task_id=%s", task_id)

        heartbeat_thread = threading.Thread(
            target=_heartbeat_worker,
            name=f"runtime-task-heartbeat-{task_id}",
            daemon=True,
        )
        heartbeat_thread.start()
        try:
            yield True
        finally:
            stop_event.set()
            heartbeat_thread.join(timeout=1.0)

    def recover_stale_active_tasks(
        self,
        *,
        domain: str,
        stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS,
        now: Optional[float] = None,
    ) -> list[str]:
        current_ts = float(now if now is not None else time.time())
        cutoff = current_ts - max(1.0, float(stale_after_seconds or DEFAULT_STALE_AFTER_SECONDS))
        normalized_domain = str(domain or "").strip()
        message = "任务执行进程已中断，请重新发起任务"
        session = self.db.get_session()
        try:
            self._begin_write_transaction(session)
            rows = (
                session.query(RuntimeTask)
                .filter(
                    RuntimeTask.domain == normalized_domain,
                    RuntimeTask.status.in_(["pending", "running"]),
                    or_(
                        RuntimeTask.heartbeat_at < cutoff,
                        and_(RuntimeTask.heartbeat_at.is_(None), RuntimeTask.updated_at < cutoff),
                    ),
                )
                .with_for_update()
                .all()
            )
            for row in rows:
                log_item = {
                    "stage": "task_interrupted",
                    "message": message,
                }
                self._append_log_to_row(
                    row,
                    log_item,
                    max_logs=300 if normalized_domain == "filing_change_review" else 500,
                    assign_sequence=normalized_domain == "pre_review",
                    timestamp=current_ts,
                )
                if not row.result_json:
                    payload = self._json_loads(row.payload_json, {})
                    result_style = (
                        str(payload.get("result_style", "") or "").strip()
                        if isinstance(payload, dict)
                        else ""
                    )
                    if result_style == "application":
                        interrupted_result = {
                            "success": False,
                            "message": message,
                            "data": None,
                        }
                    elif result_style == "service":
                        interrupted_result = {
                            "ok": False,
                            "message": message,
                            "data": None,
                        }
                    else:
                        interrupted_result = {
                            "ok": False,
                            "message": message,
                            "project_id": str(row.project_id or ""),
                            "interrupted": True,
                        }
                    if normalized_domain == 'filing_change_review' and row.task_type != 'review':
                        interrupted_result.update(content_status='failed', content_available=False)
                    row.result_json = self._json_dumps(interrupted_result)
                elif normalized_domain == 'filing_change_review' and row.task_type == 'parse_submissions_batch':
                    interrupted_result = self._json_loads(row.result_json, {})
                    data = interrupted_result.get('data') or {}
                    data.setdefault('failed', []).extend(
                        {'doc_id': doc_id, 'content_status': 'failed', 'content_available': False, 'code': 'task_interrupted', 'error': message}
                        for doc_id in data.pop('pending', []))
                    available = bool(data.get('success'))
                    data.update(content_status=('partial' if data.get('failed') else 'success') if available else 'failed', content_available=available)
                    interrupted_result.update(ok=False, interrupted=True, message=message, data=data,
                                              content_status=data['content_status'], content_available=available)
                    row.result_json = self._json_dumps(interrupted_result)
                row.status = "failed"
                row.message = message
                row.error_message = message
                row.updated_at = current_ts
                row.finished_at = current_ts
                self._mirror_pre_review_upload_task(
                    session,
                    row,
                    status="failed",
                    message=message,
                    result=self._json_loads(row.result_json, None),
                    error_message=message,
                    timestamp=current_ts,
                )
            for row in rows:
                self._persist_filing_attempt(session, row)
            recovered_task_ids = [str(row.task_id or "") for row in rows]
            session.commit()
            return recovered_task_ids
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def get_latest_project_task(
        self,
        *,
        domain: str,
        project_id: str,
        task_types: list[str],
        include_terminal: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """返回项目最新的指定类型任务元数据，不读取大型 JSON 列。"""
        normalized_domain = str(domain or "").strip()
        normalized_project_id = str(project_id or "").strip()
        normalized_task_types = sorted(
            {
                str(item or "").strip()
                for item in (task_types or [])
                if str(item or "").strip()
            }
        )
        if not normalized_domain or not normalized_project_id or not normalized_task_types:
            return None

        session = self.db.get_session()
        try:
            projection = (
                RuntimeTask.task_id,
                RuntimeTask.task_type,
                RuntimeTask.project_id,
                RuntimeTask.run_id,
                RuntimeTask.source_doc_id,
                RuntimeTask.section_id,
                RuntimeTask.status,
                RuntimeTask.message,
                RuntimeTask.error_message,
                RuntimeTask.created_at,
                RuntimeTask.updated_at,
                RuntimeTask.finished_at,
            )
            base_filters = (
                RuntimeTask.domain == normalized_domain,
                RuntimeTask.project_id == normalized_project_id,
                RuntimeTask.task_type.in_(normalized_task_types),
            )
            row = (
                session.query(*projection)
                .filter(*base_filters, RuntimeTask.status.in_(["pending", "running"]))
                .order_by(RuntimeTask.created_at.desc(), RuntimeTask.id.desc())
                .first()
            )
            if row is None and include_terminal:
                row = (
                    session.query(*projection)
                    .filter(*base_filters, RuntimeTask.status.in_(["completed", "failed"]))
                    .order_by(
                        RuntimeTask.finished_at.desc(),
                        RuntimeTask.updated_at.desc(),
                        RuntimeTask.id.desc(),
                    )
                    .first()
                )
            if row is None:
                return None
            return {
                "task_id": str(row[0] or ""),
                "task_type": str(row[1] or ""),
                "project_id": str(row[2] or ""),
                "run_id": str(row[3] or ""),
                "source_doc_id": str(row[4] or ""),
                "section_id": str(row[5] or ""),
                "status": str(row[6] or "pending"),
                "message": str(row[7] or ""),
                "error_message": str(row[8] or ""),
                "created_at": float(row[9] or 0),
                "updated_at": float(row[10] or 0),
                "finished_at": float(row[11] or 0),
                "done": str(row[6] or "") in {"completed", "failed"},
            }
        finally:
            session.close()

    def list_project_parse_tasks(self, project_id: str, domain: str) -> list:
        """复用任务记录恢复每次解析尝试；不读取日志全文。"""
        session = self.db.get_session()
        try:
            columns = ('task_id', 'domain', 'project_id', 'source_doc_id', 'task_type', 'status', 'message', 'error_message', 'created_at', 'updated_at', 'result_json', 'payload_json')
            rows = session.query(*(getattr(RuntimeTask, name) for name in columns)).filter(
                RuntimeTask.project_id == project_id, RuntimeTask.domain == domain,
                RuntimeTask.task_type.in_(['import_application_form', 'parse_application_form', 'parse_submission', 'parse_submissions_batch']),
            ).order_by(RuntimeTask.created_at.desc(), RuntimeTask.id.desc()).all()
            result = []
            for row in rows:
                item = dict(zip(columns, row))
                item['result'] = self._json_loads(item.pop('result_json'), None)
                item['payload'] = self._json_loads(item.pop('payload_json'), {})
                result.append(item)
            return result
        finally:
            session.close()

    def get_task(self, task_id: str, domain: str = "") -> Optional[Dict[str, Any]]:
        session = self.db.get_session()
        try:
            query = session.query(RuntimeTask).filter(RuntimeTask.task_id == str(task_id or "").strip())
            if str(domain or "").strip():
                query = query.filter(RuntimeTask.domain == str(domain or "").strip())
            row = query.one_or_none()
            return self._serialize_row(row) if row is not None else None
        finally:
            session.close()

    def get_snapshot(self, task_id: str, *, domain: str = "", cursor: int = 0) -> Optional[Dict[str, Any]]:
        task = self.get_task(task_id, domain=domain)
        if task is None:
            return None
        logs = task.get("logs", []) if isinstance(task.get("logs", []), list) else []
        log_offset = int(task.get("log_offset", 0) or 0)
        try:
            start = max(0, int(cursor))
        except Exception:
            start = 0
        relative_start = max(0, start - log_offset)
        task["logs"] = logs[relative_start:]
        task["cursor"] = start
        task["next_cursor"] = log_offset + len(logs)
        task["done"] = str(task.get("status", "") or "") in {"completed", "failed"}
        return task

    def _persist_filing_attempt(self, session, task):
        """诊断存入既有业务JSON，与运行记录清理同事务；仅保留各目标最新事实。"""
        if task.domain != 'filing_change_review' or task.task_type not in (
                'import_application_form','parse_application_form','parse_submission','parse_submissions_batch'):
            return
        from datetime import datetime
        from sqlalchemy import inspect
        from agent.agent_backend.database.mysql.db_model import FilingChangeApplicationForm, FilingChangeProject, FilingChangeSubmissionFile
        if not inspect(session.connection()).has_table(FilingChangeProject.__tablename__):
            return  # 通用任务库也可独立使用，尚无备案业务模块及业务记录。
        project = session.query(FilingChangeProject).filter_by(project_id=task.project_id, deleted=False).with_for_update().first()
        if project is None:
            return
        row = session.query(FilingChangeApplicationForm).filter_by(project_id=task.project_id).with_for_update().first()
        if row is None:
            row = FilingChangeApplicationForm(project_id=task.project_id, form_json='{}', parse_status='not_parsed',
                                             created_at=datetime.now(), updated_at=datetime.now())
            session.add(row)
        form = self._json_loads(row.form_json, {})
        attempts = form.setdefault('latest_task_attempts', {})
        data = self._serialize_row(task)
        # 运行日志不进入业务记录；内容、原因、目标和尝试时间仍保留。
        data.pop('logs', None)
        data.pop('logs_json', None)
        def diagnosis(value):
            if not isinstance(value, dict):
                return value
            keys = ('ok','message','error','code','content_status','content_available','interrupted',
                    'doc_id','task_id','source_file_id','source_file_name','source_file_type','source_file_size','original_file_id','parse_diagnostics')
            out = {key:value[key] for key in keys if key in value}
            if isinstance(value.get('data'), dict):
                out['data'] = diagnosis(value['data'])
            for key in ('success','failed'):
                if isinstance(value.get(key), list):
                    out[key] = [diagnosis(item) for item in value[key]]
            return out
        data['result'] = diagnosis(data.get('result'))
        if task.task_type in ('import_application_form','parse_application_form'):
            targets = ['application_form']
        elif task.source_doc_id:
            targets = [task.source_doc_id]
        else:
            targets = (data.get('payload') or {}).get('doc_ids', [])
            if 'doc_ids' not in (data.get('payload') or {}) or ((data.get('payload') or {}).get('all') and not targets):
                data['target_scope_note'] = '历史批任务未记录受理文件范围，不能关联到具体文件。'
                # 保留任务异常本身，但不猜测任何文件归属。
                targets = ['unknown_scope:' + task.task_id]
        for target in targets:
            previous = attempts.get(target, {})
            if float(previous.get('created_at') or 0) <= float(data.get('created_at') or 0):
                attempts[target] = data
        row.form_json = self._json_dumps(form)

    def prune_finished(self, *, domain: str, retention_seconds: int, now: Optional[float] = None) -> int:
        current_ts = float(now if now is not None else time.time())
        cutoff = current_ts - max(0, int(retention_seconds or 0))
        session = self.db.get_session()
        try:
            self._begin_write_transaction(session)
            # 与删除使用同一条件，任何待删记录都必须先保存诊断；失败时整笔回滚。
            expired = session.query(RuntimeTask).filter(RuntimeTask.domain == domain,
                    RuntimeTask.status.in_(['completed','failed']),
                    or_(RuntimeTask.finished_at < cutoff,
                        and_(RuntimeTask.finished_at.is_(None), RuntimeTask.updated_at < cutoff))).with_for_update().all()
            for row in expired:
                self._persist_filing_attempt(session, row)
            count = (
                session.query(RuntimeTask)
                .filter(
                    RuntimeTask.domain == str(domain or "").strip(),
                    RuntimeTask.status.in_(["completed", "failed"]),
                    or_(
                        RuntimeTask.finished_at < cutoff,
                        and_(RuntimeTask.finished_at.is_(None), RuntimeTask.updated_at < cutoff),
                    ),
                )
                .delete(synchronize_session=False)
            )
            session.commit()
            return int(count or 0)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def delete_by_project(self, project_id: str, *, domain: str = "") -> int:
        session = self.db.get_session()
        try:
            query = session.query(RuntimeTask).filter(RuntimeTask.project_id == str(project_id or "").strip())
            if str(domain or "").strip():
                query = query.filter(RuntimeTask.domain == str(domain or "").strip())
            count = query.delete(synchronize_session=False)
            session.commit()
            return int(count or 0)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def delete_task(self, task_id: str, *, include_upload_task: bool = False) -> int:
        session = self.db.get_session()
        try:
            self._begin_write_transaction(session)
            if include_upload_task:
                session.query(PreReviewUploadTask).filter(
                    PreReviewUploadTask.task_id == str(task_id or "").strip()
                ).delete(synchronize_session=False)
            count = (
                session.query(RuntimeTask)
                .filter(RuntimeTask.task_id == str(task_id or "").strip())
                .delete(synchronize_session=False)
            )
            session.commit()
            return int(count or 0)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
