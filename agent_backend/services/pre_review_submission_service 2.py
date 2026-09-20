import json
import os
import re
from copy import deepcopy
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote, urlparse

from sqlalchemy import and_

from agent.agent_backend.database.mysql.db_model import (
    FileInfo,
    PreReviewProject,
    PreReviewSubmissionFile,
    PreReviewSubmissionSectionContent,
)
from agent.agent_backend.utils.parser.parser_manager import ParserManager


def _load_parse_submission_pdf_to_payload():
    from agent.agent_backend.utils.parser.ctd_paser import parse_submission_pdf_to_payload

    return parse_submission_pdf_to_payload


class PreReviewSubmissionService:
    def __init__(self, owner):
        self.owner = owner

    @staticmethod
    def _appendix_asset_root_dir() -> str:
        return os.path.normpath(
            os.path.join(os.path.dirname(__file__), "..", "data", "submissions", "appendix_assets")
        )

    def _appendix_asset_dir(self, doc_id: str) -> str:
        return os.path.join(self._appendix_asset_root_dir(), str(doc_id or "").strip())

    def _cleanup_appendix_assets(self, doc_id: str) -> None:
        appendix_dir = self._appendix_asset_dir(doc_id)
        if not appendix_dir or not os.path.isdir(appendix_dir):
            return
        for root, dirs, files in os.walk(appendix_dir, topdown=False):
            for file_name in files:
                try:
                    os.remove(os.path.join(root, file_name))
                except Exception:
                    pass
            for dir_name in dirs:
                try:
                    os.rmdir(os.path.join(root, dir_name))
                except Exception:
                    pass
        try:
            os.rmdir(appendix_dir)
        except Exception:
            pass

    @staticmethod
    def _extract_appendix_links(text: Any) -> List[Dict[str, str]]:
        content = str(text or "").strip()
        if not content:
            return []
        out: List[Dict[str, str]] = []
        seen = set()

        def add_link(url: str, label: str = "") -> None:
            normalized_url = str(url or "").strip().rstrip(").,;]")
            if not normalized_url or normalized_url in seen:
                return
            seen.add(normalized_url)
            out.append({"url": normalized_url, "label": str(label or "").strip()})

        for match in re.finditer(r"\[([^\]]{0,120})\]\((https?://[^\s)]+)\)", content, flags=re.IGNORECASE):
            add_link(match.group(2), match.group(1))
        for match in re.finditer(r"(?P<url>https?://[^\s<>\"]+)", content, flags=re.IGNORECASE):
            add_link(match.group("url"))
        return out

    @staticmethod
    def _appendix_link_supported(url: str) -> Tuple[bool, str, str]:
        parsed = urlparse(str(url or "").strip())
        file_name = os.path.basename(unquote(parsed.path or "")).strip()
        ext = file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
        if not ext:
            return False, "", file_name
        return ParserManager.is_supported(ext), ext, file_name

    def _download_appendix_asset(self, doc_id: str, url: str, file_name: str) -> Tuple[bool, str, str]:
        import requests

        appendix_dir = self._appendix_asset_dir(doc_id)
        os.makedirs(appendix_dir, exist_ok=True)
        safe_name = self.owner._safe_display_name(
            file_name or os.path.basename(urlparse(url).path) or "appendix.bin"
        )
        target_path = os.path.join(appendix_dir, safe_name)
        try:
            response = requests.get(url, timeout=60, stream=True)
            response.raise_for_status()
            with open(target_path, "wb") as fh:
                for chunk in response.iter_content(chunk_size=1024 * 64):
                    if chunk:
                        fh.write(chunk)
            return True, "ok", target_path
        except Exception as exc:
            return False, str(exc), ""

    def _parse_appendix_asset_text(self, file_path: str, ext_hint: str = "") -> str:
        ext = str(ext_hint or "").strip().lower()
        if not ext and "." in os.path.basename(file_path):
            ext = os.path.basename(file_path).rsplit(".", 1)[-1].lower()
        if ext == "pdf":
            parse_submission_pdf_to_payload = _load_parse_submission_pdf_to_payload()
            payload = parse_submission_pdf_to_payload(file_path=file_path)
            section_rows = self.owner._extract_section_content_rows_from_payload(payload)
            merged_text = self.owner._preview_text_blocks(
                [str(item.get("content", "")).strip() for item in section_rows]
            )
            if merged_text:
                return merged_text
            units = payload.get("review_units", []) if isinstance(payload, dict) else []
            return self.owner._preview_text_blocks(
                [str(item.get("text", "")).strip() for item in units if isinstance(item, dict)]
            )
        chunks = self.owner._parse_submission_file(file_path=file_path, ext_hint=ext)
        return self.owner._preview_text_blocks(
            [str(item.get("text", "")).strip() for item in chunks if isinstance(item, dict)]
        )

    @staticmethod
    def _inject_appendix_block(text: str, url: str, appendix_block: str) -> str:
        original_text = str(text or "")
        block_text = str(appendix_block or "").strip()
        if not original_text or not block_text:
            return original_text
        lines = original_text.splitlines()
        for index, line in enumerate(lines):
            if str(url or "").strip() and str(url or "").strip() in line:
                injected_lines = lines[: index + 1] + ["", block_text, ""] + lines[index + 1 :]
                return "\n".join(injected_lines).strip()
        return f"{original_text.rstrip()}\n\n{block_text}\n".strip()

    def _enrich_payload_with_remote_appendices(
        self,
        payload: Any,
        *,
        project_id: str,
        doc_id: str,
        fallback_section_id: str = "",
        fallback_section_name: str = "",
    ) -> Any:
        if not isinstance(payload, dict):
            return payload

        section_rows = self.owner._extract_section_content_rows_from_payload(
            payload=payload,
            fallback_section_id=fallback_section_id,
            fallback_section_name=fallback_section_name,
        )
        if not section_rows:
            return payload

        changed = False
        enriched_content_map: Dict[str, str] = {}
        appendix_records: List[Dict[str, Any]] = []

        for row in section_rows:
            if not isinstance(row, dict):
                continue
            section_id = str(row.get("section_id", "") or "").strip()
            original_content = str(row.get("content", "") or "").strip()
            if not section_id or not original_content:
                continue
            enriched_content = original_content
            appendix_context = bool(re.search(r"(附件|附录|appendix)", original_content, flags=re.IGNORECASE))
            for link in self._extract_appendix_links(original_content):
                url = str(link.get("url", "") or "").strip()
                label = str(link.get("label", "") or "").strip()
                if not appendix_context and not re.search(r"(附件|附录|appendix)", label, flags=re.IGNORECASE):
                    continue
                supported, ext, file_name = self._appendix_link_supported(url)
                if not supported:
                    continue
                ok_download, download_msg, local_path = self._download_appendix_asset(
                    doc_id=doc_id,
                    url=url,
                    file_name=file_name or f"appendix.{ext}",
                )
                if not ok_download:
                    appendix_records.append(
                        {
                            "section_id": section_id,
                            "url": url,
                            "status": "download_failed",
                            "message": download_msg,
                        }
                    )
                    continue
                appendix_text = self._parse_appendix_asset_text(local_path, ext_hint=ext)
                if not appendix_text:
                    appendix_records.append(
                        {
                            "section_id": section_id,
                            "url": url,
                            "status": "parse_empty",
                            "asset_file_path": local_path,
                        }
                    )
                    continue
                appendix_title = label or os.path.basename(local_path) or file_name or "附件"
                appendix_block = (
                    f"> 附录解析：{appendix_title}\n"
                    f"> 来源链接：{url}\n\n"
                    f"{appendix_text.strip()}"
                )
                enriched_content = self._inject_appendix_block(enriched_content, url, appendix_block)
                appendix_records.append(
                    {
                        "section_id": section_id,
                        "url": url,
                        "status": "parsed",
                        "asset_file_path": local_path,
                        "asset_name": os.path.basename(local_path),
                    }
                )
            if enriched_content != original_content:
                changed = True
                enriched_content_map[section_id] = enriched_content

        if not changed:
            # Persist an explicit empty list as the enrichment completion marker.
            # Without it, every later read treats a normal document with no
            # appendices as legacy data and repeats the scan and database sync.
            payload["appendix_downloads"] = appendix_records
            return payload

        updated_payload = dict(payload)
        updated_sections: List[Dict[str, Any]] = []
        existing_sections = payload.get("sections", []) if isinstance(payload.get("sections", []), list) else []
        for item in existing_sections:
            if not isinstance(item, dict):
                updated_sections.append(item)
                continue
            section_id = str(
                item.get("section_id") or item.get("section_code") or item.get("code") or ""
            ).strip()
            if section_id and section_id in enriched_content_map:
                updated_item = dict(item)
                new_content = enriched_content_map[section_id]
                updated_item["content"] = new_content
                updated_item["content_preview"] = self.owner._preview(new_content, 320)
                updated_item["char_count"] = len(new_content)
                updated_sections.append(updated_item)
            else:
                updated_sections.append(item)
        if not updated_sections:
            for item in section_rows:
                if not isinstance(item, dict):
                    continue
                section_id = str(item.get("section_id", "") or "").strip()
                content = enriched_content_map.get(section_id, str(item.get("content", "") or "").strip())
                updated_sections.append(
                    {
                        "section_id": section_id,
                        "section_code": str(item.get("section_code", "") or section_id).strip() or section_id,
                        "section_name": str(item.get("section_name", "") or section_id).strip() or section_id,
                        "content": content,
                        "content_preview": self.owner._preview(content, 320),
                        "char_count": len(content),
                    }
                )
        if updated_sections:
            updated_payload["sections"] = updated_sections
        updated_payload["appendix_downloads"] = appendix_records
        return updated_payload

    def _cleanup_failed_submission_item(self, session, project_id: str, doc_id: str) -> None:
        normalized_doc_id = str(doc_id or "").strip()
        if not normalized_doc_id:
            return
        row = (
            session.query(PreReviewSubmissionFile)
            .filter(
                and_(
                    PreReviewSubmissionFile.project_id == project_id,
                    PreReviewSubmissionFile.doc_id == normalized_doc_id,
                )
            )
            .first()
        )
        file_path = str(getattr(row, "file_path", "") or "").strip() if row is not None else ""
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                pass
        parsed_path = os.path.join(self.owner.SUBMISSION_PARSED_DIR, f"{normalized_doc_id}.json")
        if os.path.exists(parsed_path):
            try:
                os.remove(parsed_path)
            except Exception:
                pass
        edit_path = self.owner._submission_edit_path(normalized_doc_id)
        if os.path.exists(edit_path):
            try:
                os.remove(edit_path)
            except Exception:
                pass
        try:
            self.owner.submission_vector_store.delete_by_doc(normalized_doc_id)
        except Exception:
            pass
        self.owner._submission_payload_cache.pop(
            self.owner._submission_payload_cache_key(project_id, normalized_doc_id),
            None,
        )
        self.owner._invalidate_structured_project_payload_cache(project_id=project_id, doc_id=normalized_doc_id)
        self._cleanup_appendix_assets(normalized_doc_id)
        session.query(PreReviewSubmissionSectionContent).filter(
            PreReviewSubmissionSectionContent.doc_id == normalized_doc_id
        ).delete(synchronize_session=False)
        session.query(FileInfo).filter(FileInfo.doc_id == normalized_doc_id).delete(synchronize_session=False)
        session.query(PreReviewSubmissionFile).filter(
            PreReviewSubmissionFile.doc_id == normalized_doc_id
        ).delete(synchronize_session=False)

    def upload_submission(
        self,
        project_id: str,
        file_obj,
        material_category: str = "other",
        section_id: str = "",
        upload_mode: str = "",
        parse_mode: str = "",
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        if file_obj is None or not getattr(file_obj, "filename", ""):
            return False, "file is required", None
        parse_submission_pdf_to_payload = _load_parse_submission_pdf_to_payload()

        session = self.owner.db_conn.get_session()
        try:
            project = (
                session.query(PreReviewProject)
                .filter(and_(PreReviewProject.project_id == project_id, PreReviewProject.is_deleted == 0))
                .first()
            )
            if project is None:
                return False, "project not found", None

            display_name = self.owner._safe_display_name(file_obj.filename)
            if not display_name:
                return False, "invalid file name", None
            normalized_mode = str(upload_mode or "").strip().lower()
            normalized_parse_mode = str(parse_mode or "").strip().lower()
            if normalized_mode not in {"", "zip", "section", "single"}:
                return False, f"unsupported upload mode: {upload_mode}", None
            if normalized_parse_mode not in {"", "quick", "detailed"}:
                return False, f"unsupported parse mode: {parse_mode}", None
            resolved_section_id = self.owner.ctd_sections.normalize_section_id(section_id)
            if (
                self.owner._is_ctd_structure_project(project)
                and normalized_mode in {"", "single"}
                and not resolved_section_id
            ):
                resolved_section_id = (
                    "3.2"
                    if self.owner.ctd_sections.get_section("3.2", leaf_only=False)
                    else self.owner._default_branch_root_for_review_domain(material_category)
                )
            if self.owner._is_ctd_structure_project(project) and resolved_section_id == "3" and self.owner.ctd_sections.get_section("3.2", leaf_only=False):
                resolved_section_id = "3.2"
            project_catalog = None
            if self.owner._is_ctd_structure_project(project):
                project_catalog = self.owner._load_project_section_catalog(session, project_id)
            section_map = project_catalog.get("section_map", {}) if isinstance(project_catalog, dict) else {}
            section_meta = section_map.get(resolved_section_id) if resolved_section_id else None
            raw_bytes = file_obj.read()
            file_obj.stream.seek(0)
            is_zip_file = self.owner._is_zip_file_name(display_name)
            if normalized_mode == "zip":
                if not is_zip_file:
                    return False, "zip mode requires a .zip file", None
                upload_items = self.owner._extract_zip_submission_items(
                    raw_bytes,
                    catalog=project_catalog,
                    branch_root=resolved_section_id if self.owner._is_supported_ctd_split_root(resolved_section_id) else "",
                )
                if not upload_items:
                    return False, "zip contains no supported submission files", None
            else:
                if is_zip_file:
                    return False, "single/section mode does not accept zip files", None
                if normalized_mode == "section" and not resolved_section_id:
                    return False, "section mode requires section_id", None
                if (
                    normalized_mode == "section"
                    and self.owner._is_ctd_structure_project(project)
                    and resolved_section_id
                    and not isinstance(section_meta, dict)
                ):
                    mounted_section_meta = self.owner._upsert_dynamic_project_section(
                        session=session,
                        project_id=project_id,
                        section_id=resolved_section_id,
                        section_name_hint="",
                    )
                    if isinstance(mounted_section_meta, dict) and str(mounted_section_meta.get("section_id", "") or "").strip():
                        section_meta = mounted_section_meta
                    else:
                        return False, f"section not found: {resolved_section_id}", None
                upload_items = [
                    {
                        "display_name": display_name,
                        "path": display_name,
                        "file_bytes": raw_bytes,
                        "section_meta": section_meta or {},
                        "explicit_section_id": resolved_section_id,
                    }
                ]

            saved_items: List[Dict[str, Any]] = []
            for item in upload_items:
                saved_items.append(
                    self.owner._create_submission_row(
                        session=session,
                        project_id=project_id,
                        display_name=str(item.get("display_name", "")),
                        file_bytes=item.get("file_bytes", b""),
                        material_category=material_category,
                        section_meta=item.get("section_meta", {}) if isinstance(item.get("section_meta"), dict) else {},
                        relative_path=str(item.get("path", "") or item.get("display_name", "")),
                        explicit_section_id=str(item.get("explicit_section_id", "") or ""),
                        strict_ctd_mapping=self.owner._is_ctd_structure_project(project),
                    )
                )
            session.commit()

            mounted_dynamic_section_count = 0
            rebuilt_project_section_count = 0
            for item in saved_items:
                ok_payload, parse_msg, parsed_payload = self.load_submission_parsed_payload(
                    project_id=project_id,
                    doc_id=str(item.get("doc_id", "")),
                    parse_mode_hint=normalized_parse_mode if normalized_mode in {"single", "section"} else "",
                )
                if not ok_payload:
                    self._cleanup_failed_submission_item(session, project_id, str(item.get("doc_id", "")))
                    session.commit()
                    return False, parse_msg, None
                if isinstance(parsed_payload, dict):
                    item["parse_quality"] = parsed_payload.get("parse_quality", {}) if isinstance(parsed_payload.get("parse_quality", {}), dict) else {}
                    item["parser_warnings"] = parsed_payload.get("parser_warnings", []) if isinstance(parsed_payload.get("parser_warnings", []), list) else []
                if self.owner._is_ctd_structure_project(project):
                    requested_section_id = self.owner.ctd_sections.normalize_section_id(
                        str(item.get("section_id", "") or resolved_section_id or "")
                    )
                    should_rebuild_catalog = normalized_mode == "single" and not requested_section_id
                    rebuild_stats = (
                        self.owner._rebuild_project_ctd_sections_from_payload(
                            session=session,
                            project_id=project_id,
                            payload=parsed_payload,
                            prune_missing_dynamic=False,
                        )
                        if should_rebuild_catalog
                        else {"created": 0, "updated": 0, "deleted": 0}
                    )
                    if isinstance(rebuild_stats, dict):
                        rebuilt_project_section_count += int(rebuild_stats.get("created", 0) or 0)
                        rebuilt_project_section_count += int(rebuild_stats.get("updated", 0) or 0)
                        rebuilt_project_section_count += int(rebuild_stats.get("deleted", 0) or 0)
                    if not rebuilt_project_section_count:
                        mounted_dynamic_section_count += int(
                            self.owner._mount_project_sections_from_payload(
                                session=session,
                                project_id=project_id,
                                payload=parsed_payload,
                            )
                            or 0
                        )
            if mounted_dynamic_section_count > 0 or rebuilt_project_section_count > 0:
                session.commit()

            if len(saved_items) == 1:
                single = saved_items[0]
                return True, "submission uploaded", {
                    **single,
                    "is_chunked": True,
                    "upload_mode": normalized_mode or ("zip" if is_zip_file else "single"),
                    "parse_mode": normalized_parse_mode or "quick",
                    "mounted_dynamic_section_count": mounted_dynamic_section_count,
                    "rebuilt_project_section_count": rebuilt_project_section_count,
                }
            return True, "submission uploaded", {
                "items": saved_items,
                "count": len(saved_items),
                "upload_mode": normalized_mode or ("zip" if is_zip_file else "single"),
                "parse_mode": normalized_parse_mode or "quick",
                "mounted_dynamic_section_count": mounted_dynamic_section_count,
                "rebuilt_project_section_count": rebuilt_project_section_count,
            }
        except Exception as exc:
            session.rollback()
            return False, f"upload submission failed: {str(exc)}", None
        finally:
            session.close()

    def get_submission_content(
        self,
        project_id: str,
        doc_id: str,
        compact: bool = False,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        session = self.owner.db_conn.get_session()
        try:
            project = (
                session.query(PreReviewProject)
                .filter(and_(PreReviewProject.project_id == project_id, PreReviewProject.is_deleted == 0))
                .first()
            )
            if project is not None and self.owner._is_ctd_structure_project(project):
                payload = self.owner._build_structured_project_payload(
                    session,
                    project_id,
                    source_doc_id=doc_id,
                    compact=compact,
                )
                section_items = payload.get("sections", []) if isinstance(payload.get("sections", []), list) else []
                if compact:
                    statistics = payload.get("statistics", {}) if isinstance(payload.get("statistics", {}), dict) else {}
                    return True, "success", {
                        "doc_id": doc_id,
                        "project_id": project_id,
                        "chunk_size": int(statistics.get("review_unit_total", 0) or 0),
                        "review_unit_count": int(statistics.get("review_unit_total", 0) or 0),
                        "section_count": len(section_items),
                        "sections": section_items,
                        "content_source": "parsed_content",
                        "response_mode": "compact",
                    }
                display_lines = [
                    str(x.get("display_content", "") or x.get("cleaned_markdown", "") or x.get("content", "")).strip()
                    for x in section_items
                    if isinstance(x, dict) and str(x.get("display_content", "") or x.get("cleaned_markdown", "") or x.get("content", "")).strip()
                ]
                return True, "success", {
                    "doc_id": doc_id,
                    "project_id": project_id,
                    "content": "\n\n".join(
                        [str(x.get("text", "")).strip() for x in payload.get("review_units", []) if str(x.get("text", "")).strip()]
                    ),
                    "display_content": "\n\n".join(display_lines),
                    "chunk_size": len(payload.get("review_units", [])),
                    "review_unit_count": len(payload.get("review_units", [])),
                    "section_count": len(section_items),
                    "chapter_structure": payload.get("chapter_structure", []),
                    "sections": section_items,
                    "leaf_sibling_groups": payload.get("leaf_sibling_groups", []),
                    "content_source": "parsed_content",
                }
        finally:
            session.close()

        ok, msg, chunks = self.load_doc_chunks(project_id=project_id, doc_id=doc_id)
        if not ok:
            return False, msg, None

        edited = self.owner._load_submission_edit(doc_id)
        lines = [edited] if edited.strip() else [str(c.get("text", "")).strip() for c in chunks if str(c.get("text", "")).strip()]
        ok_payload, _, payload = self.load_submission_parsed_payload(project_id=project_id, doc_id=doc_id)
        chapter_structure = []
        section_list = []
        leaf_sibling_groups = []
        if ok_payload and isinstance(payload, dict):
            chapter_structure = payload.get("chapter_structure") or []
            section_list = payload.get("sections") or []
            leaf_sibling_groups = payload.get("leaf_sibling_groups") or []
        if isinstance(section_list, list):
            normalized_sections = []
            for item in section_list:
                if not isinstance(item, dict):
                    continue
                raw_content = str(item.get("content", "") or "").strip()
                normalized_sections.append(
                    {
                        **item,
                        "raw_content": raw_content,
                        "cleaned_markdown": "",
                        "display_content": raw_content,
                    }
                )
            section_list = normalized_sections

        display_lines = []
        if isinstance(section_list, list) and section_list:
            for item in section_list:
                if not isinstance(item, dict):
                    continue
                text = str(item.get("display_content", "") or item.get("content", "") or "").strip()
                if text:
                    display_lines.append(text)
        if compact:
            compact_payload = self.owner._compact_structured_project_payload(
                {
                    "sections": section_list,
                    "statistics": {
                        "section_total": len(section_list) if isinstance(section_list, list) else 0,
                        "review_unit_total": len(chunks),
                    },
                }
            )
            return True, "success", {
                "doc_id": doc_id,
                "project_id": project_id,
                "chunk_size": len(chunks),
                "review_unit_count": len(chunks),
                "section_count": len(compact_payload.get("sections", [])),
                "sections": compact_payload.get("sections", []),
                "content_source": "parsed_content",
                "response_mode": "compact",
            }
        return True, "success", {
            "doc_id": doc_id,
            "project_id": project_id,
            "content": "\n\n".join(lines),
            "display_content": "\n\n".join(display_lines) if display_lines else "\n\n".join(lines),
            "chunk_size": len(chunks),
            "review_unit_count": len(chunks),
            "section_count": len(section_list) if isinstance(section_list, list) else 0,
            "chapter_structure": chapter_structure,
            "sections": section_list,
            "leaf_sibling_groups": leaf_sibling_groups,
            "content_source": "parsed_content",
        }

    def save_submission_content(
        self,
        project_id: str,
        doc_id: str,
        content: str,
        section_id: str = "",
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        normalized_doc_id = str(doc_id or "").strip()
        normalized_content = str(content or "")
        normalized_section_id = self.owner.ctd_sections.normalize_section_id(section_id)
        session = self.owner.db_conn.get_session()
        try:
            project = (
                session.query(PreReviewProject)
                .filter(and_(PreReviewProject.project_id == project_id, PreReviewProject.is_deleted == 0))
                .first()
            )
            if project is None:
                return False, "project not found", None
            file_row = (
                session.query(PreReviewSubmissionFile)
                .filter(
                    and_(
                        PreReviewSubmissionFile.project_id == project_id,
                        PreReviewSubmissionFile.doc_id == normalized_doc_id,
                        PreReviewSubmissionFile.is_deleted == 0,
                    )
                )
                .first()
            )
            if file_row is None:
                return False, "submission file not found", None

            if self.owner._is_ctd_structure_project(project):
                target_section_id = normalized_section_id or self.owner.ctd_sections.normalize_section_id(
                    getattr(file_row, "section_id", "") or ""
                )
                if not target_section_id:
                    return False, "section_id is required for structured CTD submissions", None
                section_meta = self.owner.ctd_sections.get_section(target_section_id, leaf_only=False) or {}
                target_section_name = (
                    str(section_meta.get("section_name", "") or "").strip()
                    or str(getattr(file_row, "section_name", "") or "").strip()
                    or target_section_id
                )
                session.query(PreReviewSubmissionSectionContent).filter(
                    and_(
                        PreReviewSubmissionSectionContent.project_id == project_id,
                        PreReviewSubmissionSectionContent.doc_id == normalized_doc_id,
                        PreReviewSubmissionSectionContent.section_id == target_section_id,
                    )
                ).delete(synchronize_session=False)

                content_chunks = self.owner._split_submission_content_for_storage(normalized_content)
                now = self.owner._now()
                for chunk_index, chunk_text in enumerate(content_chunks, start=1):
                    session.add(
                        PreReviewSubmissionSectionContent(
                            doc_id=normalized_doc_id,
                            project_id=project_id,
                            section_id=target_section_id,
                            section_code=target_section_id,
                            section_name=target_section_name,
                            chunk_index=chunk_index,
                            content=chunk_text,
                            content_preview=self.owner._preview(normalized_content, 320) if chunk_index == 1 else "",
                            source_parser="manual_markdown_edit",
                            create_time=now,
                            update_time=now,
                        )
                    )
                session.commit()
                self.owner._submission_payload_cache.pop(
                    self.owner._submission_payload_cache_key(project_id, normalized_doc_id),
                    None,
                )
                self.owner._invalidate_structured_project_payload_cache(project_id=project_id, doc_id=normalized_doc_id)
                return True, "submission content saved", {
                    "project_id": project_id,
                    "doc_id": normalized_doc_id,
                    "section_id": target_section_id,
                    "section_name": target_section_name,
                    "chunk_size": len(content_chunks),
                    "saved_mode": "section_content_row",
                }

            self.owner._save_submission_edit(normalized_doc_id, normalized_content)
            self.owner._submission_payload_cache.pop(
                self.owner._submission_payload_cache_key(project_id, normalized_doc_id),
                None,
            )
            self.owner._invalidate_structured_project_payload_cache(project_id=project_id, doc_id=normalized_doc_id)
            return True, "submission content saved", {
                "project_id": project_id,
                "doc_id": normalized_doc_id,
                "saved_mode": "document_edit_file",
            }
        except Exception as exc:
            session.rollback()
            return False, f"save submission content failed: {str(exc)}", None
        finally:
            session.close()

    def get_submission_file_info(self, project_id: str, doc_id: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        session = self.owner.db_conn.get_session()
        try:
            row = (
                session.query(PreReviewSubmissionFile)
                .filter(
                    and_(
                        PreReviewSubmissionFile.project_id == project_id,
                        PreReviewSubmissionFile.doc_id == doc_id,
                        PreReviewSubmissionFile.is_deleted == 0,
                    )
                )
                .first()
            )
            if row is None:
                return False, "submission file not found", None
            file_path = str(getattr(row, "file_path", "") or "").strip()
            if not file_path or not os.path.exists(file_path):
                return False, "submission file path not exists", None
            return True, "success", {
                "project_id": project_id,
                "doc_id": doc_id,
                "file_name": str(getattr(row, "file_name", "") or ""),
                "file_path": file_path,
                "file_type": str(getattr(row, "file_type", "") or ""),
                "material_category": str(getattr(row, "material_category", "") or ""),
                "section_id": str(getattr(row, "section_id", "") or ""),
                "section_name": str(getattr(row, "section_name", "") or ""),
            }
        finally:
            session.close()

    def get_submission_asset_file_info(
        self,
        project_id: str,
        doc_id: str,
        asset_path: str,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        ok, msg, data = self.get_submission_file_info(project_id=project_id, doc_id=doc_id)
        if not ok or not isinstance(data, dict):
            return ok, msg, data
        base_dir = os.path.dirname(str(data.get("file_path", "") or "").strip())
        normalized_asset_path = str(asset_path or "").replace("\\", "/").lstrip("/").strip()
        if not base_dir or not normalized_asset_path:
            return False, "submission asset not found", None
        candidate_path = os.path.normpath(os.path.join(base_dir, normalized_asset_path))
        try:
            base_dir_real = os.path.realpath(base_dir)
            candidate_real = os.path.realpath(candidate_path)
        except Exception:
            return False, "submission asset not found", None
        if not candidate_real.startswith(base_dir_real):
            return False, "submission asset not found", None
        if not os.path.exists(candidate_real) or not os.path.isfile(candidate_real):
            return False, "submission asset not found", None
        return True, "success", {
            **data,
            "asset_path": normalized_asset_path,
            "asset_file_path": candidate_real,
            "asset_name": os.path.basename(candidate_real),
        }

    def get_submission_sections(
        self,
        project_id: str,
        doc_id: str,
        compact: bool = False,
        read_only: bool = False,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        session = self.owner.db_conn.get_session()
        try:
            project = (
                session.query(PreReviewProject)
                .filter(and_(PreReviewProject.project_id == project_id, PreReviewProject.is_deleted == 0))
                .first()
            )
            if project is None:
                return False, "project not found", None
            if self.owner._is_ctd_structure_project(project):
                payload = self.owner._build_structured_project_payload(
                    session,
                    project_id,
                    source_doc_id=doc_id,
                    compact=compact,
                    allow_parse_fallback=not read_only,
                )
                return True, "success", {
                    "project_id": project_id,
                    "doc_id": doc_id,
                    "sections": payload.get("sections", []),
                    "statistics": payload.get("statistics", {}),
                    **({} if compact else {
                        "chapter_structure": payload.get("chapter_structure", []),
                        "review_units": payload.get("review_units", []),
                    }),
                }
            if read_only:
                content_rows = (
                    session.query(PreReviewSubmissionSectionContent)
                    .filter(
                        and_(
                            PreReviewSubmissionSectionContent.project_id == project_id,
                            PreReviewSubmissionSectionContent.doc_id == doc_id,
                        )
                    )
                    .order_by(
                        PreReviewSubmissionSectionContent.section_id.asc(),
                        PreReviewSubmissionSectionContent.chunk_index.asc(),
                        PreReviewSubmissionSectionContent.id.asc(),
                    )
                    .all()
                )
                merged_rows = self.owner._merge_submission_section_content_rows(content_rows)
                sections = []
                for item in merged_rows:
                    content = str(item.get("content", "") or "").strip()
                    sections.append(
                        {
                            "section_id": str(item.get("section_id", "") or "").strip(),
                            "section_code": str(item.get("section_code", "") or "").strip(),
                            "section_name": str(item.get("section_name", "") or "").strip(),
                            "content_preview": self.owner._preview(content, 320),
                            "char_count": len(content),
                        }
                    )
                return True, "success", {
                    "project_id": project_id,
                    "doc_id": doc_id,
                    "sections": sections,
                    "review_units": [],
                    "parse_quality": {},
                    "parser_warnings": [],
                    "statistics": {
                        "section_total": len(sections),
                        "review_unit_total": sum(1 for item in sections if int(item.get("char_count", 0) or 0) > 0),
                    },
                }
        finally:
            session.close()

        ok, msg, payload = self.load_submission_parsed_payload(project_id=project_id, doc_id=doc_id)
        if not ok:
            return False, msg, None
        section_rows = self.owner._extract_section_content_rows_from_payload(payload)
        sections = []
        for item in section_rows:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content", "") or "").strip()
            sections.append(
                {
                    "section_id": str(item.get("section_id", "") or "").strip(),
                    "section_code": str(item.get("section_code", "") or "").strip(),
                    "section_name": str(item.get("section_name", "") or "").strip(),
                    "content": content,
                    "content_preview": self.owner._preview(content, 320),
                    "char_count": len(content),
                }
            )
        return True, "success", {
            "project_id": project_id,
            "doc_id": doc_id,
            "sections": sections,
            "review_units": [] if compact else (payload.get("review_units", []) if isinstance(payload, dict) else []),
            "parse_quality": payload.get("parse_quality", {}) if isinstance(payload, dict) and isinstance(payload.get("parse_quality", {}), dict) else {},
            "parser_warnings": payload.get("parser_warnings", []) if isinstance(payload, dict) and isinstance(payload.get("parser_warnings", []), list) else [],
            "statistics": {
                "section_total": len(sections),
                "review_unit_total": len(payload.get("review_units", [])) if isinstance(payload, dict) and isinstance(payload.get("review_units", []), list) else 0,
            },
        }

    def get_submission_section_diagnostics(self, project_id: str, doc_id: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        ok, msg, data = self.get_submission_sections(
            project_id=project_id,
            doc_id=doc_id,
            compact=True,
            read_only=True,
        )
        if not ok or not isinstance(data, dict):
            return ok, msg, data

        diagnostics: List[Dict[str, Any]] = []
        mounted_section_ids: List[str] = []
        replayable_section_ids: List[str] = []
        total_char_count = 0
        parse_quality = data.get("parse_quality", {}) if isinstance(data.get("parse_quality", {}), dict) else {}
        parser_warnings = data.get("parser_warnings", []) if isinstance(data.get("parser_warnings", []), list) else []
        if not parse_quality and not parser_warnings:
            # Diagnostics is a read endpoint. Only consult the in-memory parsed
            # payload when available; never invoke the parsing/sync path here.
            cache_key = self.owner._submission_payload_cache_key(project_id, doc_id)
            parsed_payload = self.owner._submission_payload_cache.get(cache_key)
            if isinstance(parsed_payload, dict):
                parse_quality = parsed_payload.get("parse_quality", {}) if isinstance(parsed_payload.get("parse_quality", {}), dict) else {}
                parser_warnings = parsed_payload.get("parser_warnings", []) if isinstance(parsed_payload.get("parser_warnings", []), list) else []
        for item in data.get("sections", []) if isinstance(data.get("sections", []), list) else []:
            if not isinstance(item, dict):
                continue
            section_id = str(item.get("section_id", "") or "").strip()
            section_name = str(item.get("section_name", "") or "").strip()
            char_count = int(item.get("char_count", 0) or 0)
            content_preview = str(item.get("content_preview", "") or "").strip()
            has_content = char_count > 0
            can_replay = bool(section_id and has_content)
            if section_id:
                mounted_section_ids.append(section_id)
                if can_replay:
                    replayable_section_ids.append(section_id)
            total_char_count += char_count
            diagnostics.append(
                {
                    "section_id": section_id,
                    "section_name": section_name,
                    "char_count": char_count,
                    "has_content": has_content,
                    "can_replay": can_replay,
                    "content_preview": content_preview,
                }
            )

        return True, "success", {
            "project_id": project_id,
            "doc_id": doc_id,
            "mounted_section_ids": mounted_section_ids,
            "replayable_section_ids": replayable_section_ids,
            "section_diagnostics": diagnostics,
            "parse_quality": parse_quality,
            "parser_warnings": parser_warnings,
            "statistics": {
                **(data.get("statistics", {}) if isinstance(data.get("statistics", {}), dict) else {}),
                "mounted_section_total": len(mounted_section_ids),
                "replayable_section_total": len(replayable_section_ids),
                "total_char_count": total_char_count,
            },
        }

    def load_doc_chunks(self, project_id: str, doc_id: str) -> Tuple[bool, str, List[Dict[str, Any]]]:
        session = self.owner.db_conn.get_session()
        try:
            project = (
                session.query(PreReviewProject)
                .filter(and_(PreReviewProject.project_id == project_id, PreReviewProject.is_deleted == 0))
                .first()
            )
            if project is not None and self.owner._is_ctd_structure_project(project):
                payload = self.owner._build_structured_project_payload(session, project_id, source_doc_id=doc_id)
                units = payload.get("review_units", []) if isinstance(payload, dict) else []
                return True, "ok", units if isinstance(units, list) else []
        finally:
            session.close()

        ok, msg, payload = self.load_submission_parsed_payload(project_id=project_id, doc_id=doc_id)
        if not ok:
            return False, msg, []
        if isinstance(payload, dict):
            units = payload.get("review_units")
            if isinstance(units, list):
                return True, "ok", units
            sections = payload.get("sections")
            if isinstance(sections, list):
                fallback = []
                for idx, s in enumerate(sections, start=1):
                    if not isinstance(s, dict):
                        continue
                    text = str(s.get("content", "")).strip()
                    if not text:
                        continue
                    fallback.append(
                        {
                            "chunk_id": str(s.get("section_id", f"sec_{idx}")),
                            "section_id": str(s.get("section_id", f"sec_{idx}")),
                            "section_code": str(s.get("code", "")),
                            "section_name": str(s.get("title", "")),
                            "page": s.get("page_start"),
                            "page_start": s.get("page_start"),
                            "page_end": s.get("page_end"),
                            "text": text,
                        }
                    )
                return True, "ok", fallback
        if isinstance(payload, list):
            return True, "ok", payload
        return True, "ok", []

    def load_submission_parsed_payload(self, project_id: str, doc_id: str, parse_mode_hint: str = "") -> Tuple[bool, str, Any]:
        cache_key = self.owner._submission_payload_cache_key(project_id, doc_id)
        if cache_key in self.owner._submission_payload_cache:
            cached = deepcopy(self.owner._submission_payload_cache[cache_key])
            if isinstance(cached, dict) and isinstance(cached.get("appendix_downloads"), list):
                return True, "ok", cached
            cached_payload = cached
        else:
            cached_payload: Any = None

        parsed_path = os.path.join(self.owner.SUBMISSION_PARSED_DIR, f"{doc_id}.json")
        if cached_payload is None and os.path.exists(parsed_path):
            try:
                with open(parsed_path, "r", encoding="utf-8") as fh:
                    cached_payload = json.load(fh)
                self.owner._submission_payload_cache[cache_key] = deepcopy(cached_payload)
            except Exception:
                cached_payload = None

        parse_submission_pdf_to_payload = _load_parse_submission_pdf_to_payload()

        session = self.owner.db_conn.get_session()
        try:
            project = (
                session.query(PreReviewProject)
                .filter(and_(PreReviewProject.project_id == project_id, PreReviewProject.is_deleted == 0))
                .first()
            )
            file_row = (
                session.query(PreReviewSubmissionFile)
                .filter(
                    and_(
                        PreReviewSubmissionFile.doc_id == doc_id,
                        PreReviewSubmissionFile.project_id == project_id,
                        PreReviewSubmissionFile.is_deleted == 0,
                    )
                )
                .first()
            )
            if file_row is None:
                return False, "source doc not found", []

            ext_hint = (file_row.file_type or "").strip()
            if not ext_hint and file_row.file_name and "." in file_row.file_name:
                ext_hint = file_row.file_name.rsplit(".", 1)[-1].lower()
            bound_section_id = self.owner.ctd_sections.normalize_section_id(file_row.section_id)
            bound_section_name = str(file_row.section_name or bound_section_id).strip() or bound_section_id
            branch_root = self.owner._branch_root_from_section_id(bound_section_id)
            is_explicit_leaf_binding = bool(bound_section_id and branch_root and bound_section_id != branch_root)
            normalized_parse_mode = str(parse_mode_hint or "").strip().lower()
            if normalized_parse_mode not in {"quick", "detailed"}:
                normalized_parse_mode = ""
            if not normalized_parse_mode and isinstance(cached_payload, dict):
                cached_parse_mode = str(cached_payload.get("parser_mode", "") or "").strip().lower()
                if cached_parse_mode in {"quick", "detailed"}:
                    normalized_parse_mode = cached_parse_mode
            if not normalized_parse_mode:
                existing_parser = (
                    session.query(PreReviewSubmissionSectionContent.source_parser)
                    .filter(PreReviewSubmissionSectionContent.doc_id == doc_id)
                    .order_by(PreReviewSubmissionSectionContent.id.desc())
                    .first()
                )
                existing_parser_name = str(existing_parser[0] if existing_parser else "" or "").strip().lower()
                if existing_parser_name == "ctd_deepseek_ocr":
                    normalized_parse_mode = "detailed"
            if not normalized_parse_mode:
                normalized_parse_mode = "quick"

            payload: Any = cached_payload
            project_catalog: Optional[Dict[str, Any]] = None
            effective_split_roots: List[str] = []

            def parse_ctd_single_file_payload(split_roots: List[str]) -> Any:
                normalized_roots = [
                    str(item or "").strip()
                    for item in split_roots or []
                    if str(item or "").strip()
                ]
                if not normalized_roots:
                    return {}
                parsed_payloads: List[Dict[str, Any]] = []
                for root_section_id in normalized_roots:
                    if normalized_parse_mode == "detailed":
                        parsed = self.owner.ctd_submission_service.parse_single_ctd_file_detailed(
                            file_path=file_row.file_path,
                            root_section_id=root_section_id,
                            catalog=project_catalog or {},
                            file_type=ext_hint,
                        )
                    else:
                        parsed = self.owner.ctd_submission_service.parse_single_ctd_file(
                            file_path=file_row.file_path,
                            root_section_id=root_section_id,
                            catalog=project_catalog or {},
                            file_type=ext_hint,
                        )
                    if isinstance(parsed, dict) and (
                        parsed.get("review_units") or parsed.get("sections")
                    ):
                        parsed_payloads.append(parsed)
                if not parsed_payloads:
                    return {}
                if len(parsed_payloads) == 1:
                    return parsed_payloads[0]
                return self.owner.ctd_submission_service.merge_leaf_section_payloads(
                    payloads=parsed_payloads,
                    source_file=file_row.file_path,
                    parser_mode=normalized_parse_mode,
                )

            if payload is None:
                if project is not None and self.owner._is_ctd_structure_project(project):
                    project_catalog = self.owner._load_project_section_catalog(session, project_id)
                    project_section_map = (
                        project_catalog.get("section_map", {})
                        if isinstance(project_catalog.get("section_map", {}), dict)
                        else {}
                    )
                    bound_section_meta = project_section_map.get(bound_section_id, {}) if bound_section_id else {}
                    effective_split_roots = self.owner.ctd_submission_service.resolve_single_ctd_split_roots(
                        file_path=file_row.file_path,
                        requested_section_id=bound_section_id or branch_root,
                        catalog=project_catalog,
                        file_type=ext_hint,
                    )
                    if is_explicit_leaf_binding:
                        if normalized_parse_mode == "detailed":
                            payload = self.owner.ctd_submission_service.parse_bound_section_file_detailed(
                                file_path=file_row.file_path,
                                file_type=ext_hint,
                                section_meta=bound_section_meta,
                            )
                        else:
                            payload = self.owner.ctd_submission_service.parse_bound_section_file(
                                file_path=file_row.file_path,
                                file_type=ext_hint,
                                section_meta=bound_section_meta,
                            )
                    elif effective_split_roots:
                        payload = parse_ctd_single_file_payload(effective_split_roots)
                    elif self.owner._is_supported_ctd_split_root(branch_root):
                        section_meta = (
                            bound_section_meta
                            if bound_section_meta
                            else {
                                "section_id": branch_root,
                                "section_name": branch_root,
                                "parent_section_id": "",
                            }
                        )
                        if normalized_parse_mode == "detailed":
                            payload = self.owner.ctd_submission_service.parse_bound_section_file_detailed(
                                file_path=file_row.file_path,
                                file_type=ext_hint,
                                section_meta=section_meta,
                            )
                        else:
                            payload = self.owner.ctd_submission_service.parse_bound_section_file(
                                file_path=file_row.file_path,
                                file_type=ext_hint,
                                section_meta=section_meta,
                            )
                    elif ext_hint.lower() == "pdf":
                        payload = parse_submission_pdf_to_payload(file_path=file_row.file_path)
                    else:
                        chunks = self.owner._parse_submission_file(file_path=file_row.file_path, ext_hint=ext_hint)
                        payload = {"review_units": chunks}
                elif ext_hint.lower() == "pdf":
                    payload = parse_submission_pdf_to_payload(file_path=file_row.file_path)
                    if not isinstance(payload, dict):
                        payload = {"review_units": []}
                else:
                    chunks = self.owner._parse_submission_file(file_path=file_row.file_path, ext_hint=ext_hint)
                    payload = {"review_units": chunks}
            if project is not None and self.owner._is_ctd_structure_project(project):
                project_catalog = project_catalog or self.owner._load_project_section_catalog(session, project_id)
                if not effective_split_roots:
                    effective_split_roots = self.owner.ctd_submission_service.resolve_single_ctd_split_roots(
                        file_path=file_row.file_path,
                        requested_section_id=bound_section_id or branch_root,
                        catalog=project_catalog,
                        file_type=ext_hint,
                    )
            if effective_split_roots:
                if not (
                    isinstance(payload, dict)
                    and str(payload.get("structure_type", "")).strip()
                    in {"ctd_leaf_section_payload_v1", "ctd_docx_leaf_section_payload_v1"}
                ):
                    payload = parse_ctd_single_file_payload(effective_split_roots)

            if (
                project is not None
                and self.owner._is_ctd_structure_project(project)
                and isinstance(payload, dict)
                and self.owner._is_supported_ctd_split_root(branch_root)
                and not is_explicit_leaf_binding
                and not effective_split_roots
            ):
                normalized_payload = self.owner._normalize_ctd_payload_to_leaf_sections(payload, branch_root)
                if isinstance(normalized_payload, dict) and normalized_payload is not payload:
                    payload = normalized_payload

            if is_explicit_leaf_binding:
                payload = self.owner._coerce_payload_to_explicit_section_binding(
                    payload=payload,
                    section_id=bound_section_id,
                    section_name=bound_section_name,
                )
            elif (
                project is not None
                and self.owner._is_ctd_structure_project(project)
                and bound_section_id
                and branch_root
                and bound_section_id == branch_root
                and not self.owner._is_supported_ctd_split_root(branch_root)
                and not effective_split_roots
            ):
                payload = self.owner._coerce_payload_to_explicit_section_binding(
                    payload=payload,
                    section_id=bound_section_id,
                    section_name=bound_section_name,
                )

            if not isinstance(payload, (list, dict)):
                payload = {"review_units": []}
            if isinstance(payload, dict) and effective_split_roots:
                payload.setdefault("parser_mode", normalized_parse_mode)
            if isinstance(payload, dict):
                payload = self._enrich_payload_with_remote_appendices(
                    payload,
                    project_id=project_id,
                    doc_id=str(doc_id or "").strip(),
                    fallback_section_id=bound_section_id,
                    fallback_section_name=bound_section_name,
                )

            with open(parsed_path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)

            self.owner._sync_submission_section_content_rows(session, file_row, payload)

            units = payload.get("review_units", []) if isinstance(payload, dict) else []
            if not isinstance(units, list):
                units = []
            if not units:
                units = [
                    {
                        "chunk_id": item.get("section_id"),
                        "section_id": item.get("section_id"),
                        "section_code": item.get("section_code"),
                        "section_name": item.get("section_name"),
                        "text": item.get("content"),
                    }
                    for item in self.owner._extract_section_content_rows_from_payload(
                        payload,
                        fallback_section_id=bound_section_id,
                        fallback_section_name=bound_section_name,
                    )
                ]

            chunk_ids = [str(c.get("chunk_id", idx + 1)) for idx, c in enumerate(units) if isinstance(c, dict)]
            file_row.is_chunked = True
            file_row.chunk_ids = ";".join(chunk_ids)
            file_row.chunk_size = len(units)
            session.commit()
            self.owner._submission_payload_cache[cache_key] = deepcopy(payload)
            return True, "ok", deepcopy(payload)
        except Exception as exc:
            session.rollback()
            return False, f"load submission parsed payload failed: {str(exc)}", {}
        finally:
            session.close()
