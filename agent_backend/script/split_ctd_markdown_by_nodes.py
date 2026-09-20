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

from agent.agent_backend.script.split_ctd_markdown_to_ectd import TEST_DIR, build_ectd_from_markdown  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Split one CTD markdown file into section markdown files under eCTD nodes.")
    parser.add_argument(
        "--input-md",
        default=str(TEST_DIR / "sub_e018f27f52ab4bba_CTD11_parsed.md"),
        help="Source markdown file for the CTD document.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(TEST_DIR / "ectd"),
        help="Target eCTD directory.",
    )
    parser.add_argument(
        "--zip-output",
        default="",
        help="Optional zip output path. Leave empty to skip zip generation.",
    )
    args = parser.parse_args()

    manifest = build_ectd_from_markdown(
        markdown_path=Path(args.input_md),
        output_dir=Path(args.output_dir),
        zip_output_path=Path(args.zip_output) if str(args.zip_output or "").strip() else None,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
