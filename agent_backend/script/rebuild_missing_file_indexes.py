#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import sys
from pathlib import Path
from typing import List, Tuple


def _bootstrap_repo_root() -> Path:
    script_path = Path(__file__).resolve()
    repo_root = script_path.parents[3]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    return repo_root


REPO_ROOT = _bootstrap_repo_root()

from sqlalchemy import and_  # noqa: E402

from agent.agent_backend.database.mysql.db_model import FileInfo  # noqa: E402
from agent.agent_backend.database.mysql.mysql_conn import MysqlConnection  # noqa: E402
from agent.agent_backend.services.knowledge_service import KnowledgeService  # noqa: E402


def _load_rows(classification: str = "", limit: int = 0) -> List[FileInfo]:
    db = MysqlConnection()
    session = db.get_session()
    try:
        query = session.query(FileInfo).filter(FileInfo.is_deleted == 0)
        if classification:
            query = query.filter(FileInfo.classification == classification)
        rows = query.order_by(FileInfo.id.asc()).all()
        if limit > 0:
            rows = rows[:limit]
        return rows
    finally:
        session.close()


def _refresh_row(doc_id: str) -> FileInfo | None:
    db = MysqlConnection()
    session = db.get_session()
    try:
        return (
            session.query(FileInfo)
            .filter(and_(FileInfo.doc_id == doc_id, FileInfo.is_deleted == 0))
            .first()
        )
    finally:
        session.close()


def _is_missing_index(service: KnowledgeService, row: FileInfo) -> Tuple[bool, str, int]:
    index_status = str(getattr(row, "index_status", "pending") or "pending").strip()
    vector_count = int(getattr(row, "vector_count", 0) or 0)
    if index_status != "completed":
        return True, f"index_status={index_status}", vector_count
    try:
        has_doc = bool(service.rag.store.has_doc(row.doc_id))
    except Exception as exc:
        return True, f"has_doc_check_failed={exc}", vector_count
    if not has_doc:
        return True, "vector_store_missing_doc", vector_count
    if vector_count <= 0:
        try:
            real_count = len(service.rag.store.list_by_doc(row.doc_id))
        except Exception:
            real_count = 0
        if real_count <= 0:
            return True, "vector_count_zero", real_count
        return False, "db_vector_count_stale", real_count
    return False, "completed", vector_count


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild only missing knowledge indexes from file_info.")
    parser.add_argument("--classification", default="", help="Only rebuild a specific classification.")
    parser.add_argument("--limit", type=int, default=0, help="Maximum number of file rows to inspect. 0 means all.")
    parser.add_argument("--dry-run", action="store_true", help="Only print files considered missing.")
    args = parser.parse_args()

    service = KnowledgeService()
    rows = _load_rows(classification=args.classification, limit=int(args.limit or 0))
    print(f"[info] repo_root={REPO_ROOT}")
    print(f"[info] total_rows={len(rows)}")

    missing_rows: List[Tuple[FileInfo, str, int]] = []
    for row in rows:
        missing, reason, vector_count = _is_missing_index(service, row)
        if missing:
            missing_rows.append((row, reason, vector_count))

    print(f"[info] missing_rows={len(missing_rows)}")
    if args.dry_run:
        for row, reason, vector_count in missing_rows:
            print(
                f"[dry-run] doc_id={row.doc_id} classification={row.classification} "
                f"status={getattr(row, 'index_status', 'pending')} vector_count={vector_count} reason={reason}"
            )
        return 0

    success = 0
    failed = 0
    for idx, (row, reason, vector_count) in enumerate(missing_rows, start=1):
        print(
            f"[{idx}/{len(missing_rows)}] rebuild-missing doc_id={row.doc_id} "
            f"classification={row.classification} status={getattr(row, 'index_status', 'pending')} "
            f"vector_count={vector_count} reason={reason}"
        )
        try:
            service.file_service.update_index_status(
                doc_id=row.doc_id,
                index_status="pending",
                index_error="",
                indexed_at=None,
                vector_count=0,
            )
            service._run_async_parse(
                doc_id=row.doc_id,
                file_path=row.file_path,
                classification=row.classification or "",
                ext_hint=row.file_type or "",
                force=True,
            )
            refreshed = _refresh_row(row.doc_id)
            if refreshed is None:
                raise RuntimeError(f"file row missing after rebuild: {row.doc_id}")
            print(
                f"  -> status={getattr(refreshed, 'index_status', 'pending')} "
                f"indexed_at={getattr(refreshed, 'indexed_at', None)} "
                f"vector_count={int(getattr(refreshed, 'vector_count', 0) or 0)}"
            )
            if str(getattr(refreshed, "index_status", "")).strip() != "completed":
                raise RuntimeError(getattr(refreshed, "index_error", "") or "index not completed")
            success += 1
        except Exception as exc:
            failed += 1
            print(f"  -> failed: {exc}")

    print(f"[summary] success={success} failed={failed} total={len(missing_rows)}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
