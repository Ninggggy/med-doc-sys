#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
from pathlib import Path
from typing import List

import requests


def _pick_dir(script_dir: Path, arg_dir: str) -> Path:
    if arg_dir:
        p = Path(arg_dir)
        if not p.is_absolute():
            p = (script_dir / p).resolve()
        return p
    return script_dir / "指导原则"


def _list_files(folder: Path, limit: int) -> List[Path]:
    allow = {".pdf", ".doc", ".docx", ".txt", ".md"}
    files = [x for x in folder.iterdir() if x.is_file() and x.suffix.lower() in allow]
    files.sort(key=lambda x: x.name)
    if limit > 0:
        files = files[:limit]
    return files


def _normalize_prefix(prefix: str) -> str:
    p = (prefix or "").strip()
    if not p:
        return ""
    if not p.startswith("/"):
        p = "/" + p
    return p.rstrip("/")


def main() -> int:
    parser = argparse.ArgumentParser(description="批量调用知识库上传 HTTP 接口")
    parser.add_argument("--base-url", default="http://127.0.0.1:5001", help="后端地址")
    parser.add_argument("--api-prefix", default="", help="接口前缀，如 /api")
    parser.add_argument("--dir", default="", help="文件目录，默认 script/指导原则")
    parser.add_argument("--classification", default="指导原则", help="上传分类")
    parser.add_argument("--affect-range", default="全局", help="影响范围")
    parser.add_argument("--limit", type=int, default=0, help="仅上传前 N 个文件，0 表示全部")
    args = parser.parse_args()

    script_dir = Path(__file__).resolve().parent
    target_dir = _pick_dir(script_dir, args.dir)
    base_url = args.base_url.rstrip("/")
    api_prefix = _normalize_prefix(args.api_prefix)
    upload_url = f"{base_url}{api_prefix}/knowledge/upload"

    if not target_dir.exists() or not target_dir.is_dir():
        print(f"[error] 目录不存在: {target_dir}")
        return 1

    files = _list_files(target_dir, args.limit)
    if not files:
        print(f"[error] 目录下无可上传文件: {target_dir}")
        return 1

    print(f"[info] upload_url={upload_url}")
    print(f"[info] upload_dir={target_dir}")
    print(f"[info] total_files={len(files)}")

    ok = 0
    failed = 0
    for i, file_path in enumerate(files, start=1):
        print(f"[{i}/{len(files)}] 上传: {file_path.name}")
        try:
            with file_path.open("rb") as f:
                r = requests.post(
                    upload_url,
                    files={"file": (file_path.name, f)},
                    data={
                        "classification": args.classification,
                        "affect_range": args.affect_range,
                    },
                    timeout=300,
                )
            r.raise_for_status()
            body = r.json()
            code = int(body.get("code", 500))
            if code != 200:
                raise RuntimeError(body.get("message", "upload failed"))
            data = body.get("data", {}) if isinstance(body.get("data", {}), dict) else {}
            print(f"  -> success, doc_id={data.get('doc_id', '')}")
            ok += 1
        except Exception as e:
            print(f"  -> failed: {e}")
            failed += 1

    print(f"\n[summary] success={ok}, failed={failed}, total={len(files)}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
