from pathlib import Path
from typing import Any, Dict, List

from agent.agent_backend.utils.parser import ParserManager
from agent.agent_backend.services.filing_change_submission_parser import parse_filing_change_docx
from agent.agent_backend.services.filing_parse_outcome import check_file, ParseFailure


class FilingChangeMaterialService:
    def parse_file(self, file_path: str) -> List[Dict[str, Any]]:
        check_file(file_path)
        path = Path(file_path)
        ext = path.suffix.lower().lstrip(".")
        structured_error = None
        if ext == "docx":
            try:
                parsed = parse_filing_change_docx(str(path))
                if parsed:
                    has_structured_table = any(
                        any(isinstance(table, dict) and str(table.get("table_type", "")).strip() for table in (item.get("tables") or []))
                        for item in parsed
                        if isinstance(item, dict)
                    )
                    if has_structured_table:
                        return parsed
            except Exception as exc:
                structured_error = exc
        if ParserManager.is_supported(ext):
            try:
                return ParserManager.parse(str(path), ext_hint=ext)
            except Exception as exc:
                if structured_error is not None:
                    raise exc from structured_error
                raise
        if ext not in ('txt', 'md', 'csv', 'json', 'xml'):
            raise ParseFailure('unsupported_format')
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ParseFailure('format_mismatch') from exc
        return [{"chunk_id": "raw_1", "text": text, "summary": text[:200]}]
