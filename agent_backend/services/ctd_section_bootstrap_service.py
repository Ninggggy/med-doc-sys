import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from agent.agent_backend.database.mysql.db_model import (
    PreReviewProject,
    PreReviewProjectSection,
)
from agent.agent_backend.database.mysql.mysql_conn import MysqlConnection
from agent.agent_backend.services.ctd_section_service import CTDSectionService


logger = logging.getLogger("agent.pre_review.section_bootstrap")
RAW_DATA_DIR = str(Path(__file__).resolve().parents[1] / "data" / "raw_data")


class CTDSectionBootstrapService:
    """Bootstrap static CTD catalog rows for chemical-drug projects.

    The chapter tree itself comes from `data/base_data/ctd_tree_catalog.json`
    via `CTDSectionService`. This bootstrap only ensures DB rows exist for
    active 化药 projects so migrated environments can render the chapter tree
    immediately after startup.
    """

    def __init__(self) -> None:
        self.db_conn = MysqlConnection()
        self.ctd_sections = CTDSectionService(raw_data_dir=RAW_DATA_DIR)

    @staticmethod
    def _now() -> datetime:
        return datetime.now()

    @staticmethod
    def _safe_json_list(value: Any) -> str:
        return json.dumps(value if isinstance(value, list) else [], ensure_ascii=False)

    @staticmethod
    def _normalize_registration_scope(value: Any) -> str:
        text = str(value or "").strip()
        if text in {"化药", "化学药"}:
            return "化药"
        return text

    @classmethod
    def _is_ctd_structure_project(cls, project: PreReviewProject) -> bool:
        return cls._normalize_registration_scope(getattr(project, "registration_scope", "") or "") in {"化药"}

    def _seed_missing_project_sections(self, session, project_id: str) -> int:
        project_key = str(project_id or "").strip()
        if not project_key:
            return 0

        pending_ids = {
            str(getattr(row, "section_id", "") or "").strip()
            for row in list(getattr(session, "new", []) or [])
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
        # Each project owns its own catalog snapshot. Once a project has any
        # section rows, do not backfill "missing" static rows at bootstrap time,
        # otherwise startup may re-pollute a project that was rebuilt from its
        # actual submission document tree.
        if pending_ids or existing_ids:
            return 0

        catalog = self.ctd_sections.get_catalog()
        now = self._now()
        created = 0
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
                    root_section_id=str(item.get("root_section_id", "") or section_id).strip() or section_id,
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
            created += 1
        return created

    def bootstrap(self) -> Dict[str, Any]:
        session = self.db_conn.get_session()
        try:
            projects = (
                session.query(PreReviewProject)
                .filter(PreReviewProject.is_deleted == 0)
                .all()
            )
            seeded_projects: List[str] = []
            created_rows = 0
            for project in projects:
                if not self._is_ctd_structure_project(project):
                    continue
                added = self._seed_missing_project_sections(
                    session,
                    str(getattr(project, "project_id", "") or "").strip(),
                )
                if added > 0:
                    seeded_projects.append(str(getattr(project, "project_id", "") or "").strip())
                    created_rows += added
            if created_rows > 0:
                session.commit()
            else:
                session.rollback()
            result = {
                "seeded_project_count": len(seeded_projects),
                "created_section_rows": created_rows,
                "project_ids": seeded_projects,
            }
            logger.info("CTD section bootstrap completed: %s", result)
            return result
        except Exception:
            session.rollback()
            logger.exception("CTD section bootstrap failed")
            raise
        finally:
            session.close()
