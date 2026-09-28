#!/usr/bin/env python3
"""计算交付镜像的可复现源码摘要。

构建和交付校验必须同时调用本文件，禁止各自实现一套 hash 算法。
"""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
from typing import Iterable


COMMON_EXCLUDED_PARTS = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "node_modules",
    "dist",
}
COMMON_EXCLUDED_SUFFIXES = {".pyc", ".pyo"}


def _is_under(relative_path: str, prefix: str) -> bool:
    return relative_path == prefix or relative_path.startswith(f"{prefix}/")


def _should_exclude(relative_path: str) -> bool:
    path = Path(relative_path)
    if path.name == ".DS_Store" or path.suffix.lower() in COMMON_EXCLUDED_SUFFIXES:
        return True
    if any(part in COMMON_EXCLUDED_PARTS for part in path.parts):
        return True
    return any(
        _is_under(relative_path, prefix)
        for prefix in (
            "agent_backend/data",
            "agent_backend/llm/log",
            "agent_backend/llm/temp",
        )
    )


def _iter_input_files(source_root: Path, inputs: Iterable[str]) -> list[Path]:
    result: list[Path] = []
    for input_name in inputs:
        candidate = source_root / input_name
        if candidate.is_symlink() or candidate.is_file():
            if not _should_exclude(candidate.relative_to(source_root).as_posix()):
                result.append(candidate)
            continue
        if not candidate.is_dir():
            raise FileNotFoundError(f"交付摘要输入不存在：{candidate}")
        for directory, directory_names, file_names in os.walk(candidate):
            directory_path = Path(directory)
            directory_names[:] = sorted(
                name
                for name in directory_names
                if not _should_exclude(
                    (directory_path / name).relative_to(source_root).as_posix()
                )
            )
            for file_name in sorted(file_names):
                path = directory_path / file_name
                relative_path = path.relative_to(source_root).as_posix()
                if not _should_exclude(relative_path):
                    result.append(path)
    return sorted(set(result), key=lambda path: path.relative_to(source_root).as_posix())


def calculate_source_digest(source_root: Path, inputs: Iterable[str]) -> str:
    """对路径、文件长度和原始字节做稳定 SHA-256，不受 mtime 影响。"""
    source_root = source_root.resolve()
    digest = hashlib.sha256()
    for path in _iter_input_files(source_root, inputs):
        relative_path = path.relative_to(source_root).as_posix().encode("utf-8")
        if path.is_symlink():
            content = b"symlink\0" + os.readlink(path).encode("utf-8")
        else:
            content = path.read_bytes()
        digest.update(relative_path)
        digest.update(b"\0")
        digest.update(str(len(content)).encode("ascii"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}"


def calculate_delivery_source_digests(source_root: Path) -> dict[str, str]:
    return {
        "backend": calculate_source_digest(
            source_root,
            (
                "__init__.py",
                "agent_backend",
                *(f"prototype/ocr_local_trial/{name}.py" for name in (
                    "common", "adapters", "observe", "imaging", "pipeline",
                    "replay", "runtime_queue", "experimental_parser", "model_host",
                    "model_bridge", "content_inputs", "content_recovery",
                    "short_symbols", "segmentation", "text_roles", "product_consumer",
                )),
                "task/change_review/审评规则_变更有效期和贮藏条件_技术审评要点版.json",
                "deploy/backend-entrypoint.sh",
                "deploy/backend.delivery.Dockerfile",
            ),
        ),
        "frontend": calculate_source_digest(
            source_root,
            (
                "agent_fronted",
                "deploy/frontend.nginx.conf",
                "deploy/frontend.delivery.Dockerfile",
            ),
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="输出交付镜像构建应使用的源码摘要")
    parser.add_argument(
        "--source-root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="agent 源码根目录",
    )
    args = parser.parse_args()
    digests = calculate_delivery_source_digests(args.source_root)
    print(f"BACKEND_SOURCE_DIGEST={digests['backend']}")
    print(f"FRONTEND_SOURCE_DIGEST={digests['frontend']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
