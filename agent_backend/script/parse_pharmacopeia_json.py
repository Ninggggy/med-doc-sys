from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

import sys


def _bootstrap_repo_root() -> Path:
    script_path = Path(__file__).resolve()
    repo_root = script_path.parents[3]
    repo_root_str = str(repo_root)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)
    return repo_root


_bootstrap_repo_root()

from agent.agent_backend.services.pharmacopeia_service import PharmacopeiaService  # noqa: E402
from agent.agent_backend.database.mysql.db_model import PharmacopeiaEntry  # noqa: E402


def _build_transient_row(service: PharmacopeiaService, drug_name: str, affect_range: str, payload: Dict[str, Any], source_file_name: str) -> PharmacopeiaEntry:
    row = PharmacopeiaEntry(
        entry_id=f"preview_{drug_name}"[:60],
        drug_name=drug_name,
        affect_range=affect_range,
        source_file_name=source_file_name,
        is_deleted=0,
    )
    service._fill_entity(row, drug_name, affect_range, payload, source_file_name=source_file_name)
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse pharmacopeia JSON into per-drug index rows")
    parser.add_argument("json_file")
    parser.add_argument("--affect-range", default="化学药")
    parser.add_argument("--output-json", default="")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    input_path = Path(args.json_file).expanduser().resolve()
    if not input_path.exists():
        raise FileNotFoundError(f"json file not found: {input_path}")

    payload = json.loads(input_path.read_text(encoding="utf-8", errors="ignore"))
    if not isinstance(payload, dict):
        raise ValueError("json root must be object")

    service = PharmacopeiaService()
    result: List[Dict[str, Any]] = []
    for idx, (drug_name, raw_fields) in enumerate(payload.items(), start=1):
        if args.limit and idx > int(args.limit):
            break
        if not isinstance(raw_fields, dict):
            raw_fields = {"other": str(raw_fields)}
        row = _build_transient_row(
            service=service,
            drug_name=str(drug_name or "").strip(),
            affect_range=str(args.affect_range or "").strip() or "化学药",
            payload=dict(raw_fields),
            source_file_name=input_path.name,
        )
        parsed_rows = service._build_index_rows(row)
        result.append(
            {
                "drug_name": row.drug_name,
                "affect_range": row.affect_range,
                "doc_id": service._index_doc_id(row),
                "row_count": len(parsed_rows),
                "rows": parsed_rows,
            }
        )

    output_path = Path(args.output_json).expanduser().resolve() if args.output_json else input_path.with_name(f"{input_path.stem}.pharmacopeia.parsed.json")
    output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] parsed: {output_path}")


if __name__ == "__main__":
    main()
