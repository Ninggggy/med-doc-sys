#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import sys
from pathlib import Path


def _bootstrap_repo_root() -> Path:
    script_path = Path(__file__).resolve()
    repo_root = script_path.parents[3]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    return repo_root


REPO_ROOT = _bootstrap_repo_root()

from sqlalchemy import inspect, text  # noqa: E402

from agent.agent_backend.database.mysql.mysql_conn import MysqlConnection  # noqa: E402


TABLE_NAME = "pre_review_section_concern"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Drop legacy pre_review_section_concern table after rule migration is completed."
    )
    parser.add_argument("--dry-run", action="store_true", help="Only print the detected table status.")
    args = parser.parse_args()

    db = MysqlConnection()
    engine = db.engine
    inspector = inspect(engine)

    try:
        if TABLE_NAME not in set(inspector.get_table_names()):
            print(f"[info] table not found: {TABLE_NAME}")
            return 0

        with engine.connect() as conn:
            row_count = int(conn.execute(text(f"SELECT COUNT(*) FROM `{TABLE_NAME}`")).scalar() or 0)
        print(f"[info] table={TABLE_NAME} row_count={row_count}")

        if args.dry_run:
            print("[dry-run] table was not dropped")
            return 0

        if row_count > 0:
            print(f"[error] table {TABLE_NAME} still contains {row_count} rows; migrate or clear it first")
            return 1

        with engine.begin() as conn:
            conn.execute(text(f"DROP TABLE IF EXISTS `{TABLE_NAME}`"))
        print(f"[summary] dropped table {TABLE_NAME}")
        return 0
    except Exception as exc:
        print(f"[error] drop table failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
