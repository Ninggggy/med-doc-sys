from __future__ import annotations

from pathlib import Path
from typing import Dict

from agent.agent_backend.utils.parser.ctd_ocr_parser import (
    DeepSeekOCRDocumentClient,
    _clean_body,
    _render_deepseek_markdown_document,
    _strip_page_markers,
)


def parse_bound_section_with_deepseek_ocr_to_payload(
    file_path: str,
    *,
    section_id: str,
    section_name: str,
    parent_section_id: str = "",
) -> Dict[str, object]:
    pdf_path = Path(file_path).expanduser().resolve()
    ocr_client = DeepSeekOCRDocumentClient("deepseek_ocr")
    merged_text, page_total = _render_deepseek_markdown_document(str(pdf_path), ocr_client)
    content = _strip_page_markers(_clean_body(merged_text)).strip()
    return {
        "title": section_name,
        "source_file": str(pdf_path),
        "structure_type": "bound_section_file_payload_v1",
        "parser_mode": "detailed",
        "source_parser": "ctd_deepseek_ocr",
        "sections": [
            {
                "section_id": section_id,
                "section_code": section_id,
                "section_name": section_name,
                "title_path": [section_name],
                "parent_section_id": parent_section_id,
                "content": content,
                "content_preview": content[:320],
                "char_count": len(content),
                "page_start": 1 if content and page_total else None,
                "page_end": page_total if content and page_total else None,
            }
        ],
        "review_units": [
            {
                "chunk_id": section_id,
                "section_id": section_id,
                "section_code": section_id,
                "section_name": section_name,
                "parent_section_id": parent_section_id,
                "page": 1 if content and page_total else None,
                "page_start": 1 if content and page_total else None,
                "page_end": page_total if content and page_total else None,
                "text": content,
                "title_path": [section_name],
                "unit_order": 1,
                "unit_type": "bound_section_content",
                "char_count": len(content),
            }
        ],
        "statistics": {
            "section_total": 1 if content else 0,
            "review_unit_total": 1 if content else 0,
        },
    }
