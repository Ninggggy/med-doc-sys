#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import json
import re
import sys
import uuid
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Set

from sqlalchemy import inspect, text


def _bootstrap_repo_root() -> Path:
    script_path = Path(__file__).resolve()
    repo_root = script_path.parents[3]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    return repo_root


REPO_ROOT = _bootstrap_repo_root()

from agent.agent_backend.database.mysql.db_model import (  # noqa: E402
    PreReviewProject,
    PreReviewSectionRule,
)
from agent.agent_backend.database.mysql.mysql_conn import MysqlConnection  # noqa: E402


GLOBAL_SECTION_RULE_PROJECT_ID = "__global_section_rules__"
GLOBAL_SECTION_RULE_PROJECT_NAME = "GLOBAL_SECTION_RULES"
VERSION2_FILE = REPO_ROOT / "agent" / "agent_backend" / "markdown" / "version2.md"


def _now() -> datetime:
    return datetime.now()


def _dedupe_text_list(items: List[object]) -> List[str]:
    out: List[str] = []
    seen: Set[str] = set()
    for item in items or []:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return out


def _parse_concern_points(raw: object) -> List[str]:
    if isinstance(raw, list):
        return _dedupe_text_list(raw)
    text = str(raw or "").strip()
    if not text:
        return []
    try:
        payload = json.loads(text)
    except Exception:
        payload = None
    if isinstance(payload, list):
        return _dedupe_text_list(payload)
    if isinstance(payload, dict):
        for key in ("review_rules", "rules", "concern_points"):
            value = payload.get(key)
            if isinstance(value, list):
                return _dedupe_text_list(value)
    return _dedupe_text_list([segment for segment in text.replace("\r", "\n").split("\n") if segment.strip()])


def _row_value(row: object, key: str) -> object:
    if isinstance(row, dict):
        return row.get(key)
    if hasattr(row, "get") and callable(getattr(row, "get")):
        try:
            return row.get(key)
        except Exception:
            pass
    return getattr(row, key, None)


def _ensure_global_rule_project(session) -> str:
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

    now = _now()
    bootstrap_session = MysqlConnection().get_session()
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


def _load_section_name_lookup() -> Dict[str, str]:
    if not VERSION2_FILE.exists():
        return {}
    text = VERSION2_FILE.read_text(encoding="utf-8")
    match = re.search(r"(\[\s*\{.*\}\s*\])", text, flags=re.DOTALL)
    if not match:
        return {}
    try:
        outline = json.loads(match.group(1))
    except Exception:
        return {}

    out: Dict[str, str] = {}

    def walk(nodes):
        for item in nodes or []:
            if not isinstance(item, dict):
                continue
            section_id = str(item.get("section_id", "") or "").strip()
            section_name = str(item.get("section_name", "") or section_id).strip() or section_id
            if section_id and section_id not in out:
                out[section_id] = section_name
            walk(item.get("children_sections") or [])

    walk(outline if isinstance(outline, list) else [])
    return out


def _build_legacy_payload_summary(session_name_map: Dict[str, str], concern_rows, manual_rows):
    legacy_rules_by_section: Dict[str, List[str]] = defaultdict(list)
    legacy_projects_by_section: Dict[str, Set[str]] = defaultdict(set)
    concern_count_by_section: Dict[str, int] = defaultdict(int)
    manual_count_by_section: Dict[str, int] = defaultdict(int)

    for row in concern_rows:
        section_id = str(_row_value(row, "section_id") or "").strip()
        project_id = str(_row_value(row, "project_id") or "").strip()
        concern_points = _parse_concern_points(_row_value(row, "concern_points") or "")
        if not section_id or not concern_points:
            continue
        legacy_rules_by_section[section_id].extend(concern_points)
        concern_count_by_section[section_id] += len(concern_points)
        if project_id:
            legacy_projects_by_section[section_id].add(project_id)
        if section_id not in session_name_map:
            session_name_map[section_id] = str(_row_value(row, "section_name") or section_id).strip() or section_id

    for row in manual_rows:
        section_id = str(_row_value(row, "section_id") or "").strip()
        project_id = str(_row_value(row, "project_id") or "").strip()
        rule_text = str(_row_value(row, "rule_text") or "").strip()
        if not section_id or not rule_text:
            continue
        legacy_rules_by_section[section_id].append(rule_text)
        manual_count_by_section[section_id] += 1
        if project_id:
            legacy_projects_by_section[section_id].add(project_id)
        if section_id not in session_name_map:
            session_name_map[section_id] = str(_row_value(row, "section_name") or section_id).strip() or section_id

    return legacy_rules_by_section, legacy_projects_by_section, concern_count_by_section, manual_count_by_section


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Migrate legacy project-level concerns and manual section rules into global section review rules."
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview migration result without writing to database.")
    parser.add_argument(
        "--keep-legacy",
        action="store_true",
        help="Keep legacy concern rows and project-level manual rules after migration.",
    )
    args = parser.parse_args()

    db = MysqlConnection()
    session = db.get_session()
    engine = db.engine
    try:
        global_project_id = _ensure_global_rule_project(session)
        section_name_map = _load_section_name_lookup()
        inspector = inspect(engine)
        has_legacy_concern_table = TABLE_NAME in set(inspector.get_table_names()) if (TABLE_NAME := "pre_review_section_concern") else False

        legacy_concern_rows = []
        if has_legacy_concern_table:
            with engine.connect() as conn:
                legacy_concern_rows = list(
                    conn.execute(
                        text(
                            "SELECT id, project_id, section_id, section_code, section_name, concern_points "
                            f"FROM {TABLE_NAME} ORDER BY project_id ASC, id ASC"
                        )
                    ).mappings()
                )
        legacy_manual_rule_rows = (
            session.query(PreReviewSectionRule)
            .filter(
                PreReviewSectionRule.project_id != global_project_id,
                PreReviewSectionRule.source_type == "manual",
                PreReviewSectionRule.is_active == 1,
            )
            .order_by(
                PreReviewSectionRule.project_id.asc(),
                PreReviewSectionRule.section_id.asc(),
                PreReviewSectionRule.id.asc(),
            )
            .all()
        )
        existing_global_manual_rows = (
            session.query(PreReviewSectionRule)
            .filter(
                PreReviewSectionRule.project_id == global_project_id,
                PreReviewSectionRule.source_type == "manual",
            )
            .order_by(PreReviewSectionRule.section_id.asc(), PreReviewSectionRule.id.asc())
            .all()
        )

        (
            legacy_rules_by_section,
            legacy_projects_by_section,
            concern_count_by_section,
            manual_count_by_section,
        ) = _build_legacy_payload_summary(
            section_name_map,
            legacy_concern_rows,
            legacy_manual_rule_rows,
        )

        touched_section_ids = sorted(legacy_rules_by_section.keys())
        if not touched_section_ids:
            print("[info] no legacy concerns or project-level manual rules found")
            if args.dry_run:
                return 0
            session.rollback()
            return 0

        global_existing_by_section: Dict[str, List[PreReviewSectionRule]] = defaultdict(list)
        for row in existing_global_manual_rows:
            section_id = str(getattr(row, "section_id", "") or "").strip()
            if section_id:
                global_existing_by_section[section_id].append(row)

        created_rule_count = 0
        deleted_legacy_concern_rows = len(legacy_concern_rows)
        deleted_legacy_manual_rows = len(legacy_manual_rule_rows)

        print(f"[info] repo_root={REPO_ROOT}")
        print(f"[info] touched_sections={len(touched_section_ids)}")
        print(f"[info] legacy_concern_rows={len(legacy_concern_rows)}")
        print(f"[info] legacy_project_manual_rows={len(legacy_manual_rule_rows)}")
        print(f"[info] existing_global_manual_rows={len(existing_global_manual_rows)}")

        for section_id in touched_section_ids:
            section_name = section_name_map.get(section_id, section_id)
            existing_global_rules = [
                str(getattr(row, "rule_text", "") or "").strip()
                for row in global_existing_by_section.get(section_id, [])
                if str(getattr(row, "rule_text", "") or "").strip()
            ]
            merged_rules = _dedupe_text_list(existing_global_rules + legacy_rules_by_section.get(section_id, []))
            project_refs = sorted(legacy_projects_by_section.get(section_id, set()))
            print(
                "[section] "
                f"section_id={section_id} "
                f"section_name={section_name} "
                f"rules={len(merged_rules)} "
                f"legacy_projects={len(project_refs)} "
                f"legacy_concern_points={concern_count_by_section.get(section_id, 0)} "
                f"legacy_manual_rules={manual_count_by_section.get(section_id, 0)}"
            )

            if args.dry_run:
                continue

            for row in global_existing_by_section.get(section_id, []):
                session.delete(row)

            now = _now()
            for index, rule_text in enumerate(merged_rules, start=1):
                rule_code = f"manual_rule__{section_id.replace('.', '_')}__{index:02d}"
                payload = {
                    "section_id": section_id,
                    "section_name": section_name,
                    "rule_code": rule_code,
                    "rule_text": rule_text,
                    "source_type": "manual",
                    "source_ref": "legacy_rule_migration",
                    "legacy_projects": project_refs,
                }
                session.add(
                    PreReviewSectionRule(
                        rule_id=f"section_rule_{uuid.uuid4().hex[:16]}",
                        project_id=global_project_id,
                        section_id=section_id,
                        section_name=section_name,
                        rule_code=rule_code,
                        rule_text=rule_text,
                        source_type="manual",
                        source_ref="legacy_rule_migration",
                        is_active=True,
                        payload_json=json.dumps(payload, ensure_ascii=False),
                        create_time=now,
                        update_time=now,
                    )
                )
                created_rule_count += 1

        if args.dry_run:
            session.rollback()
            print("[dry-run] database changes were not committed")
            return 0

        if not args.keep_legacy:
            if legacy_concern_rows and has_legacy_concern_table:
                session.execute(text(f"DELETE FROM {TABLE_NAME}"))
            if legacy_manual_rule_rows:
                legacy_manual_ids = [int(getattr(row, "id", 0) or 0) for row in legacy_manual_rule_rows if int(getattr(row, "id", 0) or 0) > 0]
                if legacy_manual_ids:
                    (
                        session.query(PreReviewSectionRule)
                        .filter(PreReviewSectionRule.id.in_(legacy_manual_ids))
                        .delete(synchronize_session=False)
                    )
        else:
            deleted_legacy_concern_rows = 0
            deleted_legacy_manual_rows = 0

        session.commit()
        print(
            "[summary] "
            f"migrated_sections={len(touched_section_ids)} "
            f"created_global_manual_rules={created_rule_count} "
            f"deleted_legacy_concern_rows={deleted_legacy_concern_rows} "
            f"deleted_legacy_project_manual_rows={deleted_legacy_manual_rows} "
            f"keep_legacy={bool(args.keep_legacy)}"
        )
        return 0
    except Exception as exc:
        session.rollback()
        print(f"[error] migration failed: {exc}")
        return 1
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
