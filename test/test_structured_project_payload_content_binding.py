import json
import tempfile
import time
import unittest
from types import SimpleNamespace

from agent.agent_backend.database.mysql.db_model import (
    PreReviewSubmissionFile,
    PreReviewSubmissionSectionContent,
)
from agent.agent_backend.services.pre_review_service import PreReviewService
from agent.agent_backend.services.pre_review_submission_service import PreReviewSubmissionService


class _Query:
    def __init__(self, rows):
        self.rows = rows

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def all(self):
        return list(self.rows)


class _Session:
    def __init__(self, files, contents):
        self.files = files
        self.contents = contents

    def query(self, model):
        if model is PreReviewSubmissionFile:
            return _Query(self.files)
        if model is PreReviewSubmissionSectionContent:
            return _Query(self.contents)
        raise AssertionError(f"unexpected query model: {model}")


def _catalog_fixture():
    parent = {
        "section_id": "3.2.p.2",
        "section_code": "3.2.p.2",
        "section_name": "产品开发",
        "title_path": ["制剂", "产品开发"],
        "parent_section_id": "3.2.p",
        "section_rules": [],
        "children_sections": [],
    }
    child_1 = {
        "section_id": "3.2.p.2.1",
        "section_code": "3.2.p.2.1",
        "section_name": "处方组成",
        "title_path": ["制剂", "产品开发", "处方组成"],
        "parent_section_id": "3.2.p.2",
        "section_rules": [],
        "children_sections": [],
    }
    child_2 = {
        "section_id": "3.2.p.2.2",
        "section_code": "3.2.p.2.2",
        "section_name": "制剂",
        "title_path": ["制剂", "产品开发", "制剂"],
        "parent_section_id": "3.2.p.2",
        "section_rules": [],
        "children_sections": [],
    }
    chapter_parent = {**parent, "children_sections": [dict(child_1), dict(child_2)]}
    return {
        "chapter_structure": [chapter_parent],
        "all_sections": [parent, child_1, child_2],
        "flat_sections": [child_1, child_2],
        "section_map": {
            "3.2.p.2": parent,
            "3.2.p.2.1": child_1,
            "3.2.p.2.2": child_2,
        },
    }


def _submission_file(section_id="3.2.p.2"):
    return SimpleNamespace(
        id=1,
        doc_id="doc_parent",
        project_id="project_parent",
        file_name="ctd.docx",
        file_type="docx",
        material_category="pharmacy",
        section_id=section_id,
        section_code=section_id,
        section_name="产品开发",
        section_path="[]",
    )


def _service_fixture(merged_rows, *, read_only_result=(False, "not ready", {})):
    service = object.__new__(PreReviewService)
    service._structured_project_payload_cache = {}
    service._load_project_section_catalog = lambda session, project_id: _catalog_fixture()
    service._load_manual_concern_map = lambda session, project_id: {}
    service._merge_catalog_with_manual_concerns = lambda value, manual: value
    service._merge_submission_section_content_rows = lambda rows: list(merged_rows)
    service._rewrite_submission_markdown_asset_refs = lambda project_id, doc_id, text: text
    service.submission_service = SimpleNamespace(
        read_submission_parsed_payload=lambda project_id, doc_id: read_only_result
    )
    return service


class StructuredProjectPayloadContentBindingTests(unittest.TestCase):
    def test_intermediate_chapter_content_is_not_dropped_by_leaf_catalog(self) -> None:
        parent = {
            "section_id": "3.2.p.2",
            "section_code": "3.2.p.2",
            "section_name": "产品开发",
            "title_path": ["制剂", "产品开发"],
            "parent_section_id": "3.2.p",
            "section_rules": [],
            "children_sections": [],
        }
        child_1 = {
            "section_id": "3.2.p.2.1",
            "section_code": "3.2.p.2.1",
            "section_name": "处方组成",
            "title_path": ["制剂", "产品开发", "处方组成"],
            "parent_section_id": "3.2.p.2",
            "section_rules": [],
            "children_sections": [],
        }
        child_2 = {
            "section_id": "3.2.p.2.2",
            "section_code": "3.2.p.2.2",
            "section_name": "制剂",
            "title_path": ["制剂", "产品开发", "制剂"],
            "parent_section_id": "3.2.p.2",
            "section_rules": [],
            "children_sections": [],
        }
        chapter_parent = {**parent, "children_sections": [dict(child_1), dict(child_2)]}
        catalog = {
            "chapter_structure": [chapter_parent],
            "all_sections": [parent, child_1, child_2],
            "flat_sections": [child_1, child_2],
            "section_map": {
                "3.2.p.2": parent,
                "3.2.p.2.1": child_1,
                "3.2.p.2.2": child_2,
            },
        }

        service = object.__new__(PreReviewService)
        service._structured_project_payload_cache = {}
        service._load_project_section_catalog = lambda session, project_id: catalog
        service._load_manual_concern_map = lambda session, project_id: {}
        service._merge_catalog_with_manual_concerns = lambda value, manual: value
        service._merge_submission_section_content_rows = lambda rows: [
            {
                "doc_id": "doc_parent",
                "section_id": "3.2.p.2",
                "section_code": "3.2.p.2",
                "section_name": "产品开发",
                "content": "这是 Word 中 3.2.P.2 的真实正文。",
                "content_preview": "这是 Word 中 3.2.P.2 的真实正文。",
                "source_parser": "ctd_docx_markdown",
            }
        ]
        service._rewrite_submission_markdown_asset_refs = lambda project_id, doc_id, text: text
        service._load_submission_edit = lambda doc_id: ""
        service._load_submission_parsed_payload = lambda project_id, doc_id: (True, "ok", {})

        file_row = SimpleNamespace(
            id=1,
            doc_id="doc_parent",
            project_id="project_parent",
            file_name="ctd.docx",
            file_type="docx",
            material_category="pharmacy",
            section_id="3.2",
            section_code="3.2",
            section_name="主体数据",
            section_path="[]",
        )
        content_row = SimpleNamespace(doc_id="doc_parent")
        payload = service._build_structured_project_payload(
            _Session([file_row], [content_row]),
            "project_parent",
            source_doc_id="doc_parent",
        )

        by_section = {item["section_id"]: item for item in payload["sections"]}
        self.assertIn("3.2.p.2", by_section)
        self.assertIn("真实正文", by_section["3.2.p.2"]["content"])
        self.assertEqual(by_section["3.2.p.2.1"]["content"], "")
        self.assertIn("3.2.p.2", [item["section_id"] for item in payload["review_units"]])
        self.assertIn("真实正文", payload["chapter_structure"][0]["content"])

    def test_parent_binding_with_persisted_child_rows_never_uses_parse_fallback(self) -> None:
        merged_rows = [
            {
                "doc_id": "doc_parent",
                "section_id": "3.2.p.2.1",
                "section_code": "3.2.p.2.1",
                "section_name": "处方组成",
                "content": "子章节一的持久化正文",
                "content_preview": "子章节一的持久化正文",
                "source_parser": "ctd_docx_markdown",
            },
            {
                "doc_id": "doc_parent",
                "section_id": "3.2.p.2.2",
                "section_code": "3.2.p.2.2",
                "section_name": "制剂",
                "content": "子章节二的持久化正文",
                "content_preview": "子章节二的持久化正文",
                "source_parser": "ctd_docx_markdown",
            },
        ]
        service = _service_fixture(merged_rows)

        def forbidden_fallback(*args, **kwargs):
            raise AssertionError("已有持久化子章节时不得再解析文档或调用 OCR")

        service._load_submission_edit = forbidden_fallback
        service._load_submission_parsed_payload = forbidden_fallback
        service.submission_service.read_submission_parsed_payload = forbidden_fallback
        session = _Session([_submission_file()], [SimpleNamespace(doc_id="doc_parent")])

        compact_payload = service._build_structured_project_payload(
            session,
            "project_parent",
            source_doc_id="doc_parent",
            compact=True,
        )
        full_payload = service._build_structured_project_payload(
            session,
            "project_parent",
            source_doc_id="doc_parent",
            compact=False,
        )

        self.assertEqual(
            [item["section_id"] for item in compact_payload["sections"]],
            ["3.2.p.2.1", "3.2.p.2.2"],
        )
        full_by_section = {item["section_id"]: item for item in full_payload["sections"]}
        self.assertEqual(full_by_section["3.2.p.2"]["content"], "")
        self.assertIn("子章节一", full_by_section["3.2.p.2.1"]["content"])
        self.assertIn("子章节二", full_by_section["3.2.p.2.2"]["content"])

    def test_compact_and_full_cache_are_isolated_and_compact_is_small(self) -> None:
        body = "长文档章节正文。" * 8000
        merged_rows = [
            {
                "doc_id": "doc_parent",
                "section_id": "3.2.p.2.1",
                "section_code": "3.2.p.2.1",
                "section_name": "处方组成",
                "content": body,
                "content_preview": body[:320],
                "source_parser": "ctd_docx_markdown",
            }
        ]
        service = _service_fixture(merged_rows)

        def forbidden_fallback(*args, **kwargs):
            raise AssertionError("缓存回归不应进入解析兜底")

        service._load_submission_edit = forbidden_fallback
        service._load_submission_parsed_payload = forbidden_fallback
        service.submission_service.read_submission_parsed_payload = forbidden_fallback
        session = _Session([_submission_file()], [SimpleNamespace(doc_id="doc_parent")])

        compact_payload = service._build_structured_project_payload(
            session,
            "project_parent",
            source_doc_id="doc_parent",
            compact=True,
        )
        full_payload = service._build_structured_project_payload(
            session,
            "project_parent",
            source_doc_id="doc_parent",
            compact=False,
        )

        compact_section = compact_payload["sections"][0]
        self.assertEqual(
            set(compact_section),
            {
                "section_id",
                "section_code",
                "section_name",
                "title_path",
                "parent_section_id",
                "raw_content",
                "cleaned_markdown",
                "content_preview",
                "char_count",
            },
        )
        self.assertNotIn("chapter_structure", compact_payload)
        self.assertNotIn("review_units", compact_payload)
        self.assertIn("chapter_structure", full_payload)
        self.assertIn("review_units", full_payload)
        full_section = next(item for item in full_payload["sections"] if item["section_id"] == "3.2.p.2.1")
        self.assertIn("attached_files", full_section)
        self.assertIn("paragraph_blocks", full_section)

        compact_size = len(json.dumps(compact_payload, ensure_ascii=False, default=str).encode("utf-8"))
        full_size = len(json.dumps(full_payload, ensure_ascii=False, default=str).encode("utf-8"))
        self.assertLess(compact_size, full_size * 0.5)

        compact_key = service._structured_project_payload_cache_key(
            "project_parent", "doc_parent", compact=True
        )
        full_key = service._structured_project_payload_cache_key(
            "project_parent", "doc_parent", compact=False
        )
        self.assertNotEqual(compact_key, full_key)
        self.assertIn(compact_key, service._structured_project_payload_cache)
        self.assertIn(full_key, service._structured_project_payload_cache)

        compact_payload["sections"][0]["raw_content"] = "调用方污染"
        compact_again = service._build_structured_project_payload(
            session,
            "project_parent",
            source_doc_id="doc_parent",
            compact=True,
        )
        self.assertNotEqual(compact_again["sections"][0]["raw_content"], "调用方污染")

        service._invalidate_structured_project_payload_cache(
            project_id="project_parent", doc_id="doc_parent"
        )
        self.assertNotIn(compact_key, service._structured_project_payload_cache)
        self.assertNotIn(full_key, service._structured_project_payload_cache)

    def test_compact_missing_content_returns_reparse_status_without_full_parser(self) -> None:
        service = _service_fixture([])
        service._load_submission_edit = lambda doc_id: ""
        read_calls = []

        def read_only_missing(project_id, doc_id):
            read_calls.append((project_id, doc_id))
            return False, "submission content not ready; reparse required", {}

        def forbidden_full_parser(*args, **kwargs):
            raise AssertionError("章节浏览不得调用完整解析、OCR 或远程附录链路")

        service.submission_service.read_submission_parsed_payload = read_only_missing
        service._load_submission_parsed_payload = forbidden_full_parser
        session = _Session([_submission_file()], [])

        started = time.perf_counter()
        payload = service._build_structured_project_payload(
            session,
            "project_parent",
            source_doc_id="doc_parent",
            compact=True,
        )
        elapsed = time.perf_counter() - started

        self.assertLess(elapsed, 0.5)
        self.assertEqual(read_calls, [("project_parent", "doc_parent")])
        self.assertFalse(payload["content_ready"])
        self.assertEqual(payload["content_status"], "reparse_required")
        self.assertEqual(payload["content_unavailable_doc_ids"], ["doc_parent"])

    def test_read_only_payload_helper_does_not_delegate_to_parser_when_file_is_missing(self) -> None:
        class ExistingQuery:
            def filter(self, *args, **kwargs):
                return self

            def first(self):
                return (1,)

        class ExistingSession:
            def query(self, *args, **kwargs):
                return ExistingQuery()

            def close(self):
                return None

        class ExistingDb:
            def get_session(self):
                return ExistingSession()

        with tempfile.TemporaryDirectory() as parsed_dir:
            owner = SimpleNamespace(
                db_conn=ExistingDb(),
                _submission_payload_cache={},
                _submission_payload_cache_key=lambda project_id, doc_id: f"{project_id}::{doc_id}",
                SUBMISSION_PARSED_DIR=parsed_dir,
            )
            submission_service = PreReviewSubmissionService(owner)

            def forbidden_parser(*args, **kwargs):
                raise AssertionError("纯读 helper 不得委托给解析方法")

            submission_service.load_submission_parsed_payload = forbidden_parser
            started = time.perf_counter()
            ok, message, payload = submission_service.read_submission_parsed_payload(
                project_id="project_parent",
                doc_id="doc_parent",
            )
            elapsed = time.perf_counter() - started

        self.assertLess(elapsed, 0.5)
        self.assertFalse(ok)
        self.assertIn("reparse required", message)
        self.assertEqual(payload, {})


if __name__ == "__main__":
    unittest.main()
