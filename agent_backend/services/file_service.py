import os
import shutil
import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import and_
from werkzeug.utils import secure_filename

from agent.agent_backend.database.mysql.mysql_conn import MysqlConnection
from agent.agent_backend.database.mysql.db_model import FileInfo
from agent.agent_backend.config.settings import settings
from agent.agent_backend.memory.storage.vector_store import VectorStore
from agent.agent_backend.utils.file_util import ensure_dir_exists

UPLOAD_DIR = settings.upload_dir
PARSED_DIR = settings.parse_dir
ensure_dir_exists(UPLOAD_DIR)
ensure_dir_exists(PARSED_DIR)


class FileService:
    def __init__(self):
        self.db_conn = MysqlConnection()
        self.vector_store = VectorStore(settings.vector_collection)

    @staticmethod
    def _now_str() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def _to_dict(row: FileInfo) -> Dict[str, Any]:
        return {
            "id": row.id,
            "doc_id": row.doc_id,
            "file_name": row.file_name,
            "file_path": row.file_path,
            "file_type": row.file_type,
            "classification": row.classification,
            "affect_range": row.affect_range,
            "profession_classification": getattr(row, "profession_classification", "other") or "other",
            "registration_scope": getattr(row, "registration_scope", "other") or "other",
            "registration_path": getattr(row, "registration_path", "") or "",
            "experience_type": getattr(row, "experience_type", "other") or "other",
            "is_chunked": bool(row.is_chunked),
            "chunk_ids": row.chunk_ids or "",
            "chunk_size": row.chunk_size or 0,
            "index_status": getattr(row, "index_status", "pending") or "pending",
            "index_error": getattr(row, "index_error", "") or "",
            "indexed_at": row.indexed_at.strftime("%Y-%m-%d %H:%M:%S") if getattr(row, "indexed_at", None) else None,
            "vector_count": int(getattr(row, "vector_count", 0) or 0),
            "is_deleted": bool(row.is_deleted),
            "create_time": row.create_time,
            "review_status": row.review_status,
            "review_time": row.review_time.strftime("%Y-%m-%d %H:%M:%S") if row.review_time else None,
        }

    def _build_doc_id(self) -> str:
        return uuid.uuid4().hex[:16]

    @staticmethod
    def _display_name(file_name: str) -> str:
        raw = os.path.basename(str(file_name or "")).strip()
        return raw

    @staticmethod
    def _storage_name(doc_id: str, display_name: str) -> str:
        ext = ""
        if "." in display_name:
            ext = display_name.rsplit(".", 1)[-1].lower().strip()
        base = secure_filename(display_name)
        if not base:
            base = "file"
        if ext and "." not in base:
            base = f"{base}.{ext}"
        return f"{doc_id}_{base}"

    def get_file_by_doc_id(self, doc_id: str) -> Optional[Dict[str, Any]]:
        session = self.db_conn.get_session()
        try:
            row = (
                session.query(FileInfo)
                .filter(and_(FileInfo.doc_id == doc_id, FileInfo.is_deleted == 0))
                .first()
            )
            return self._to_dict(row) if row else None
        finally:
            session.close()

    def upload_file(
        self,
        file_obj,
        classification: str = "other",
        affect_range: str = "other",
        profession_classification: str = "other",
        registration_scope: str = "other",
        registration_path: str = "",
        experience_type: str = "other",
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        if file_obj is None or not getattr(file_obj, "filename", ""):
            return False, "No selected file", None

        doc_id = self._build_doc_id()
        origin_name = self._display_name(file_obj.filename)
        if not origin_name:
            return False, "Invalid file name", None
        file_type = origin_name.rsplit(".", 1)[-1].lower() if "." in origin_name else ""
        saved_name = self._storage_name(doc_id, origin_name)
        target_path = os.path.join(UPLOAD_DIR, saved_name)
        file_obj.save(target_path)

        session = self.db_conn.get_session()
        try:
            entity = FileInfo(
                doc_id=doc_id,
                file_name=origin_name,
                file_path=target_path,
                file_type=file_type,
                classification=classification or "other",
                affect_range=affect_range or "other",
                profession_classification=profession_classification or "other",
                registration_scope=registration_scope or "other",
                registration_path=registration_path or "",
                experience_type=experience_type or "other",
                is_chunked=0,
                chunk_ids="",
                chunk_size=0,
                index_status="pending",
                index_error="",
                indexed_at=None,
                vector_count=0,
                is_deleted=0,
                create_time=self._now_str(),
                review_status=0,
                review_time=None,
            )
            session.add(entity)
            session.commit()
            return True, "File uploaded successfully", self._to_dict(entity)
        except Exception as e:
            session.rollback()
            if os.path.exists(target_path):
                os.remove(target_path)
            return False, f"File upload failed: {str(e)}", None
        finally:
            session.close()

    def add_file(
        self,
        file_path: str,
        file_name: Optional[str] = None,
        classification: str = "other",
        affect_range: str = "other",
        profession_classification: str = "other",
        registration_scope: str = "other",
        registration_path: str = "",
        experience_type: str = "other",
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        if not os.path.exists(file_path):
            return False, f"File path does not exist: {file_path}", None

        doc_id = self._build_doc_id()
        origin_name = self._display_name(file_name or os.path.basename(file_path))
        if not origin_name:
            return False, "Invalid file name", None
        file_type = origin_name.rsplit(".", 1)[-1].lower() if "." in origin_name else ""
        saved_name = self._storage_name(doc_id, origin_name)
        target_path = os.path.join(UPLOAD_DIR, saved_name)
        shutil.copy2(file_path, target_path)

        session = self.db_conn.get_session()
        try:
            entity = FileInfo(
                doc_id=doc_id,
                file_name=origin_name,
                file_path=target_path,
                file_type=file_type,
                classification=classification or "other",
                affect_range=affect_range or "other",
                profession_classification=profession_classification or "other",
                registration_scope=registration_scope or "other",
                registration_path=registration_path or "",
                experience_type=experience_type or "other",
                is_chunked=0,
                chunk_ids="",
                chunk_size=0,
                index_status="pending",
                index_error="",
                indexed_at=None,
                vector_count=0,
                is_deleted=0,
                create_time=self._now_str(),
                review_status=0,
                review_time=None,
            )
            session.add(entity)
            session.commit()
            return True, "File added successfully", self._to_dict(entity)
        except Exception as e:
            session.rollback()
            if os.path.exists(target_path):
                os.remove(target_path)
            return False, f"Add file failed: {str(e)}", None
        finally:
            session.close()

    def update_file(self, doc_id: str, update_fields: Dict[str, Any]) -> Tuple[bool, str]:
        allow_fields = {
            "file_name",
            "classification",
            "affect_range",
            "profession_classification",
            "registration_scope",
            "registration_path",
            "experience_type",
            "file_path",
            "file_type",
        }
        payload = {k: v for k, v in update_fields.items() if k in allow_fields and v is not None}
        if not payload:
            return False, "No valid fields to update"

        session = self.db_conn.get_session()
        try:
            row = (
                session.query(FileInfo)
                .filter(and_(FileInfo.doc_id == doc_id, FileInfo.is_deleted == 0))
                .first()
            )
            if row is None:
                return False, f"File not found: {doc_id}"

            # Keep physical path and metadata consistent when renaming file_name.
            if "file_name" in payload:
                new_name = self._display_name(str(payload["file_name"]))
                if not new_name:
                    return False, "Invalid file_name"
                old_path = row.file_path
                new_path = os.path.join(UPLOAD_DIR, self._storage_name(row.doc_id, new_name))
                if old_path != new_path and os.path.exists(old_path):
                    os.replace(old_path, new_path)
                row.file_name = new_name
                row.file_path = new_path
                row.file_type = new_name.rsplit(".", 1)[-1].lower() if "." in new_name else row.file_type
                payload.pop("file_name", None)

            for k, v in payload.items():
                setattr(row, k, v)
            session.commit()
            return True, "File updated successfully"
        except Exception as e:
            session.rollback()
            return False, f"Update file failed: {str(e)}"
        finally:
            session.close()

    def delete_file(self, doc_id: str) -> Tuple[bool, str]:
        # 保留内部旧调用的二元返回；页面使用详细结果区分停用与清理完成。
        ok, message, _ = self.delete_file_detailed(doc_id)
        return ok, message

    @staticmethod
    def _deletion_data(row: FileInfo) -> Dict[str, Any]:
        completed = row.index_status == "cleanup_completed"
        try:
            stored = json.loads(row.index_error or "{}")
        except (TypeError, ValueError):
            stored = {}
        if not isinstance(stored, dict):
            stored = {}
        return {
            "doc_id": row.doc_id, "file_name": row.file_name,
            "delete_state": "deleted" if row.is_deleted else "not_deleted",
            "cleanup_state": "completed" if completed else "pending",
            "pending_steps": [] if completed else stored.get("pending_steps", ["source", "parsed", "vectors"]),
            "failures": [] if completed else stored.get("failures", []),
        }

    def delete_file_detailed(self, doc_id: str) -> Tuple[bool, str, Dict[str, Any]]:
        """先持久化停用，再清理。提交状态未知时绝不触碰任何资源。"""
        unknown = {"doc_id": doc_id, "delete_state": "unknown", "cleanup_state": "not_started",
                   "pending_steps": ["source", "parsed", "vectors"], "failures": []}
        session = None
        try:
            session = self.db_conn.get_session()
            row = session.query(FileInfo).filter(FileInfo.doc_id == doc_id).with_for_update().first()
            if row is None:
                return False, "资料不存在", {**unknown, "delete_state": "not_found"}
            if not row.is_deleted:
                row.is_deleted = True
                row.is_chunked = False
                row.chunk_ids = ""
                row.chunk_size = 0
                row.index_status = "cleanup_pending"
                row.index_error = json.dumps({"pending_steps": ["source", "parsed", "vectors"], "failures": []})
                row.indexed_at = None
                session.commit()
        except Exception:
            if session is not None:
                try:
                    session.rollback()
                except Exception:
                    pass
        finally:
            if session is not None:
                session.close()

        # 新会话核对权威状态，也覆盖服务器提交成功但客户端丢失确认的情况。
        check = None
        try:
            check = self.db_conn.get_session()
            row = check.query(FileInfo).filter(FileInfo.doc_id == doc_id).first()
            if row is None or not row.is_deleted:
                return False, "删除未提交，原文件已保留", {**unknown, "delete_state": "not_deleted"}
            data = self._deletion_data(row)
            source_path = row.file_path
        except Exception:
            return False, "删除状态待核查，未执行资源清理", unknown
        finally:
            if check is not None:
                check.close()
        if data["cleanup_state"] == "completed":
            return True, "资料已删除，相关文件和索引已清理", data

        failures = []
        pending = []
        # 只使用数据库记录的原件路径及合法doc_id；从不递归删除目录。
        for step in ("source", "parsed", "vectors"):
            try:
                if step == "vectors":
                    self.vector_store.delete_by_doc(doc_id)
                else:
                    if step == "parsed" and (os.path.basename(doc_id) != doc_id or doc_id in {".", ".."}):
                        raise ValueError("invalid document identifier")
                    path = source_path if step == "source" else os.path.join(PARSED_DIR, f"{doc_id}.json")
                    if path:
                        try:
                            os.remove(path)
                        except FileNotFoundError:
                            pass
            except Exception as exc:
                pending.append(step)
                failures.append({"step": step, "code": "cleanup_failed", "exception_type": type(exc).__name__})

        update = None
        try:
            update = self.db_conn.get_session()
            row = update.query(FileInfo).filter(FileInfo.doc_id == doc_id, FileInfo.is_deleted == True).with_for_update().first()
            if row is None:
                raise RuntimeError("deletion record unavailable")
            row.index_status = "cleanup_pending" if pending else "cleanup_completed"
            row.index_error = json.dumps({"pending_steps": pending, "failures": failures})
            if "vectors" not in pending:
                row.vector_count = 0
            update.commit()
        except Exception:
            if update is not None:
                try:
                    update.rollback()
                except Exception:
                    pass
            # 起始cleanup_pending已提交；下次可安全重试，即使资源实际上已不存在。
            return True, "资料已停用，清理状态待确认，请重试", {
                **data, "cleanup_state": "pending", "pending_steps": ["source", "parsed", "vectors"],
                "failures": [{"step": "status", "code": "cleanup_status_unconfirmed"}],
            }
        finally:
            if update is not None:
                update.close()
        result = {**data, "cleanup_state": "pending" if pending else "completed",
                  "pending_steps": pending, "failures": failures}
        message = "资料已停用，部分资源待清理" if pending else "资料已删除，相关文件和索引已清理"
        return True, message, result

    def cleanup_after_index_task(self, doc_id: str) -> bool:
        """任务退出后重核删除状态，并重试清理旧任务晚写的资源。"""
        from agent.agent_backend.services.knowledge_source_validity import KnowledgeSourceValidationError
        session = None
        try:
            session = self.db_conn.get_session()
            row = session.query(FileInfo).filter(FileInfo.doc_id == doc_id).with_for_update().first()
            if row is None:
                raise KnowledgeSourceValidationError()
            if not row.is_deleted:
                return False
            row.index_status = "cleanup_pending"
            row.index_error = json.dumps({"pending_steps": ["source", "parsed", "vectors"], "failures": []})
            row.is_chunked = False
            row.chunk_ids = ""
            row.chunk_size = 0
            session.commit()
        except Exception:
            if session is not None:
                session.rollback()
            raise KnowledgeSourceValidationError() from None
        finally:
            if session is not None:
                session.close()
        self.delete_file_detailed(doc_id)
        return True

    def list_deletion_cleanups(self, page: int = 1, page_size: int = 20) -> Dict[str, Any]:
        page, page_size = max(1, int(page)), min(100, max(1, int(page_size)))
        session = self.db_conn.get_session()
        try:
            query = session.query(FileInfo).filter(FileInfo.is_deleted == True, FileInfo.index_status == "cleanup_pending")
            total = query.count()
            rows = query.order_by(FileInfo.id.desc()).offset((page - 1) * page_size).limit(page_size).all()
            return {"list": [self._deletion_data(row) for row in rows], "total": total,
                    "page": page, "page_size": page_size}
        finally:
            session.close()

    def _paginate(self, query, page: int, page_size: int) -> Dict[str, Any]:
        page = max(1, int(page))
        page_size = max(1, int(page_size))
        total = query.count()
        rows = (
            query.order_by(FileInfo.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return {
            "list": [self._to_dict(r) for r in rows],
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": (total + page_size - 1) // page_size,
        }

    def query_by_file_name(self, file_name: str, page: int = 1, page_size: int = 10) -> Dict[str, Any]:
        session = self.db_conn.get_session()
        try:
            query = session.query(FileInfo).filter(
                and_(FileInfo.is_deleted == 0, FileInfo.file_name.like(f"%{file_name}%"))
            )
            return self._paginate(query, page, page_size)
        finally:
            session.close()

    def query_by_doc_id(self, doc_id: str, page: int = 1, page_size: int = 10) -> Dict[str, Any]:
        session = self.db_conn.get_session()
        try:
            query = session.query(FileInfo).filter(
                and_(FileInfo.is_deleted == 0, FileInfo.doc_id.like(f"%{doc_id}%"))
            )
            return self._paginate(query, page, page_size)
        finally:
            session.close()

    def query_by_classification(self, classification: str, page: int = 1, page_size: int = 10) -> Dict[str, Any]:
        session = self.db_conn.get_session()
        try:
            query = session.query(FileInfo).filter(
                and_(FileInfo.is_deleted == 0, FileInfo.classification == classification)
            )
            return self._paginate(query, page, page_size)
        finally:
            session.close()

    def update_chunk_status(self, doc_id: str, is_chunked: int, chunk_ids: str = "", chunk_size: Optional[int] = None) -> Tuple[bool, str]:
        session = self.db_conn.get_session()
        try:
            row = (
                session.query(FileInfo)
                .filter(and_(FileInfo.doc_id == doc_id, FileInfo.is_deleted == 0))
                .with_for_update()
                .first()
            )
            if row is None:
                return False, f"File not found: {doc_id}"

            row.is_chunked = 1 if int(is_chunked) else 0
            row.chunk_ids = chunk_ids or ""
            if chunk_size is None:
                row.chunk_size = len([x for x in row.chunk_ids.split(";") if x]) if row.chunk_ids else 0
            else:
                row.chunk_size = int(chunk_size)
            session.commit()
            return True, "Chunk status updated successfully"
        except Exception as e:
            session.rollback()
            return False, f"Update chunk status failed: {str(e)}"
        finally:
            session.close()

    def update_index_status(
        self,
        doc_id: str,
        index_status: str,
        index_error: str = "",
        indexed_at: Optional[datetime] = None,
        vector_count: Optional[int] = None,
    ) -> Tuple[bool, str]:
        session = self.db_conn.get_session()
        try:
            row = (
                session.query(FileInfo)
                .filter(and_(FileInfo.doc_id == doc_id, FileInfo.is_deleted == 0))
                .with_for_update()
                .first()
            )
            if row is None:
                return False, f"File not found: {doc_id}"
            row.index_status = str(index_status or "pending").strip() or "pending"
            row.index_error = str(index_error or "").strip()
            row.indexed_at = indexed_at
            if vector_count is not None:
                row.vector_count = int(vector_count or 0)
            session.commit()
            return True, "Index status updated successfully"
        except Exception as e:
            session.rollback()
            return False, f"Update index status failed: {str(e)}"
        finally:
            session.close()

    def update_review_status(self, doc_id: str, review_status: int) -> Tuple[bool, str]:
        session = self.db_conn.get_session()
        try:
            row = (
                session.query(FileInfo)
                .filter(and_(FileInfo.doc_id == doc_id, FileInfo.is_deleted == 0))
                .first()
            )
            if row is None:
                return False, f"File not found: {doc_id}"

            row.review_status = int(review_status)
            row.review_time = datetime.now() if int(review_status) else None
            session.commit()
            return True, "Review status updated successfully"
        except Exception as e:
            session.rollback()
            return False, f"Update review status failed: {str(e)}"
        finally:
            session.close()


