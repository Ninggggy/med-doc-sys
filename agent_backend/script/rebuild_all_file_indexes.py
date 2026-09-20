#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import sys
from pathlib import Path
from typing import Iterable, List


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


def _iter_rows(
    classification: str = "",
    doc_ids: Iterable[str] | None = None,
    only_failed: bool = False,
    only_pending: bool = False,
    limit: int = 0,
) -> List[FileInfo]:
    db = MysqlConnection()
    session = db.get_session()
    try:
        query = session.query(FileInfo).filter(FileInfo.is_deleted == 0)
        if classification:
            query = query.filter(FileInfo.classification == classification)
        doc_ids = [str(x).strip() for x in (doc_ids or []) if str(x).strip()]
        if doc_ids:
            query = query.filter(FileInfo.doc_id.in_(doc_ids))
        if only_failed:
            query = query.filter(FileInfo.index_status == "failed")
        if only_pending:
            query = query.filter(FileInfo.index_status.in_(["pending", "running"]))
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild knowledge file_info index state and Milvus vectors.")
    parser.add_argument("--classification", default="", help="Only rebuild a specific knowledge classification.")
    parser.add_argument("--doc-id", action="append", default=[], help="Only rebuild the specified doc_id. Repeatable.")
    parser.add_argument("--limit", type=int, default=0, help="Maximum number of files to rebuild. 0 means all.")
    parser.add_argument("--only-failed", action="store_true", help="Only rebuild files with index_status=failed.")
    parser.add_argument("--only-pending", action="store_true", help="Only rebuild files with index_status in pending/running.")
    parser.add_argument("--reset-status", action="store_true", help="Reset file_info index fields before rebuild.")
    parser.add_argument("--dry-run", action="store_true", help="Print matching files only.")
    args = parser.parse_args()

    rows = _iter_rows(
        classification=args.classification,
        doc_ids=args.doc_id,
        only_failed=bool(args.only_failed),
        only_pending=bool(args.only_pending),
        limit=int(args.limit or 0),
    )
    print(f"[info] repo_root={REPO_ROOT}")
    print(f"[info] matched_files={len(rows)}")
    if not rows:
        return 0

    service = KnowledgeService()
    if args.dry_run:
        for row in rows:
            print(
                f"[dry-run] doc_id={row.doc_id} classification={row.classification} "
                f"status={getattr(row, 'index_status', 'pending')} file_path={row.file_path}"
            )
        return 0

    ok_count = 0
    failed_count = 0
    for idx, row in enumerate(rows, start=1):
        print(
            f"[{idx}/{len(rows)}] rebuild doc_id={row.doc_id} "
            f"classification={row.classification} file_name={row.file_name}"
        )
        try:
            if args.reset_status:
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
            ok_count += 1
        except Exception as exc:
            failed_count += 1
            print(f"  -> failed: {exc}")

    print(
        f"[summary] success={ok_count} failed={failed_count} total={len(rows)}"
    )
    return 0 if failed_count == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
