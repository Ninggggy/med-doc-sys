from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from agent.agent_backend.database.mysql.db_model import PreReviewPatchRegistry


class P52PatchRegistryService:
    """管理 P52 反馈优化候选 patch。"""

    def __init__(self, db_conn: Any) -> None:
        self.db_conn = db_conn

    @staticmethod
    def _now() -> datetime:
        return datetime.now()

    @staticmethod
    def _normalize_row(row: PreReviewPatchRegistry) -> Dict[str, Any]:
        payload = {}
        try:
            payload = json.loads(getattr(row, "payload_json", "") or "{}")
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        return {
            "patch_id": str(getattr(row, "patch_id", "") or "").strip(),
            "run_id": str(getattr(row, "run_id", "") or "").strip(),
            "section_id": str(getattr(row, "section_id", "") or "").strip(),
            "patch_type": str(getattr(row, "patch_type", "") or "").strip(),
            "target_agent": str(getattr(row, "target_agent", "") or "").strip(),
            "target_scope": str(getattr(row, "target_scope", "") or "").strip(),
            "trigger_condition": str(getattr(row, "trigger_condition", "") or "").strip(),
            "patch_content": str(getattr(row, "patch_content", "") or "").strip(),
            "source_feedback_key": str(getattr(row, "source_feedback_key", "") or "").strip(),
            "status": str(getattr(row, "status", "") or "").strip(),
            "version": int(getattr(row, "version", 1) or 1),
            "payload": payload,
            "create_time": row.create_time.strftime("%Y-%m-%d %H:%M:%S") if getattr(row, "create_time", None) else "",
            "update_time": row.update_time.strftime("%Y-%m-%d %H:%M:%S") if getattr(row, "update_time", None) else "",
        }

    def create_patches(
        self,
        *,
        run_id: str,
        section_id: str,
        feedback_key: str,
        patches: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        session = self.db_conn.get_session()
        try:
            version = 1
            items: List[Dict[str, Any]] = []
            for patch in patches:
                if not isinstance(patch, dict):
                    continue
                patch_id = str(patch.get("patch_id", "") or f"p52_patch_{uuid.uuid4().hex[:12]}")
                row = PreReviewPatchRegistry(
                    patch_id=patch_id,
                    run_id=run_id,
                    section_id=section_id or None,
                    patch_type=str(patch.get("patch_type", "") or "rule_patch"),
                    target_agent=str(patch.get("target_agent", "") or "p52_feedback_optimize"),
                    target_scope=str(patch.get("target_scope", "") or section_id),
                    trigger_condition=str(patch.get("trigger_condition", "") or ""),
                    patch_content=str(patch.get("patch_content", "") or "").strip(),
                    source_feedback_key=feedback_key,
                    status=str(patch.get("status", "") or "candidate"),
                    version=int(patch.get("version", version) or version),
                    payload_json=json.dumps(patch, ensure_ascii=False),
                    create_time=self._now(),
                    update_time=self._now(),
                )
                session.add(row)
                session.flush()
                items.append(self._normalize_row(row))
                version += 1
            session.commit()
            return items
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def list_patches(self, *, run_id: str, section_id: str, status: str = "") -> List[Dict[str, Any]]:
        session = self.db_conn.get_session()
        try:
            query = session.query(PreReviewPatchRegistry).filter(
                PreReviewPatchRegistry.run_id == str(run_id or "").strip(),
                PreReviewPatchRegistry.section_id == str(section_id or "").strip(),
            )
            if status:
                query = query.filter(PreReviewPatchRegistry.status == str(status or "").strip())
            rows = query.order_by(PreReviewPatchRegistry.update_time.desc(), PreReviewPatchRegistry.id.desc()).all()
            return [self._normalize_row(row) for row in rows]
        finally:
            session.close()

    def get_patch(self, *, run_id: str, section_id: str, patch_id: str) -> Optional[Dict[str, Any]]:
        session = self.db_conn.get_session()
        try:
            row = (
                session.query(PreReviewPatchRegistry)
                .filter(
                    PreReviewPatchRegistry.run_id == str(run_id or "").strip(),
                    PreReviewPatchRegistry.section_id == str(section_id or "").strip(),
                    PreReviewPatchRegistry.patch_id == str(patch_id or "").strip(),
                )
                .order_by(PreReviewPatchRegistry.id.desc())
                .first()
            )
            if row is None:
                return None
            return self._normalize_row(row)
        finally:
            session.close()

    def update_patch_status(
        self,
        *,
        run_id: str,
        section_id: str,
        patch_id: str,
        status: str,
        operator: str = "",
        comment: str = "",
    ) -> Optional[Dict[str, Any]]:
        session = self.db_conn.get_session()
        try:
            row = (
                session.query(PreReviewPatchRegistry)
                .filter(
                    PreReviewPatchRegistry.run_id == str(run_id or "").strip(),
                    PreReviewPatchRegistry.section_id == str(section_id or "").strip(),
                    PreReviewPatchRegistry.patch_id == str(patch_id or "").strip(),
                )
                .order_by(PreReviewPatchRegistry.id.desc())
                .first()
            )
            if row is None:
                return None
            payload = {}
            try:
                payload = json.loads(getattr(row, "payload_json", "") or "{}")
            except Exception:
                payload = {}
            if not isinstance(payload, dict):
                payload = {}
            payload["status_update"] = {
                "status": str(status or "").strip(),
                "operator": str(operator or "").strip(),
                "comment": str(comment or "").strip(),
                "update_time": self._now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            row.status = str(status or "").strip()
            row.payload_json = json.dumps(payload, ensure_ascii=False)
            row.update_time = self._now()
            session.commit()
            return self._normalize_row(row)
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
