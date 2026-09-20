#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _bootstrap_repo_root() -> Path:
    script_path = Path(__file__).resolve()
    repo_root = script_path.parents[3]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    return repo_root


_bootstrap_repo_root()

from agent.agent_backend.script.split_ctd_markdown_to_ectd import TEST_DIR, build_ectd_directories  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Build an eCTD directory tree from CTD catalog nodes.")
    parser.add_argument(
        "--branch-root",
        nargs="+",
        default=["3.2.s"],
        help="One or more CTD branch roots, for example: 原料药 / 制剂",
    )
    parser.add_argument(
        "--output-dir",
        default=str(TEST_DIR / "ectd"),
        help="Target eCTD directory.",
    )
    args = parser.parse_args()

    manifest = build_ectd_directories(
        output_dir=Path(args.output_dir),
        branch_roots=args.branch_root,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
