import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent.agent_backend.database.mysql.db_model import (
    PreReviewProject,
    PreReviewRun,
    PreReviewSectionConclusion,
    PreReviewSectionOutput,
    PreReviewSectionTrace,
)
from agent.agent_backend.services.pre_review_service import PreReviewService


class _Query:
    def __init__(self, rows=None, first_value=None):
        self.rows = list(rows or [])
        self.first_value = first_value

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def all(self):
        return list(self.rows)

    def first(self):
        return self.first_value


class _Session:
    def __init__(self, mapping):
        self.mapping = mapping
        # Existing read-path fixtures represent a valid, non-deleted owning project.
        self.mapping.setdefault(PreReviewProject, ("first", SimpleNamespace(project_id="project-1", is_deleted=0)))

    def query(self, model, *args):
        value = self.mapping.get(model, [])
        if isinstance(value, tuple) and len(value) == 2 and value[0] == "first":
            return _Query(first_value=value[1])
        return _Query(rows=value)

    def close(self):
        pass


class _Connection:
    def __init__(self, session):
        self.session = session

    def get_session(self):
        return self.session


class PreReviewReadPathRegressionTests(unittest.TestCase):
    def test_incomplete_run_cannot_export_either_report(self):
        for summary in ({"review_complete": False}, {"execution_status": "failed"}):
            run = SimpleNamespace(project_id="project-1", summary=json.dumps(summary))
            service = object.__new__(PreReviewService)
            session = _Session({PreReviewRun: ("first", run)})
            service.db_conn = _Connection(session)
            for export in (service.export_report_word, service.export_review_conclusions_report):
                ok, message, payload = export("failed-run")
                self.assertFalse(ok)
                self.assertIn("未完成", message)
                self.assertIsNone(payload)

    def test_failed_run_http_exports_and_download_reject(self):
        from flask import Flask
        from agent.agent_backend.controller import pre_review_controller as controller
        service = object.__new__(PreReviewService)
        run = SimpleNamespace(project_id="project-1", summary='{"review_complete":false}')
        service.db_conn = _Connection(_Session({PreReviewRun: ("first", run)}))
        app = Flask("pre-review-incomplete-export")
        app.register_blueprint(controller.pre_review_bp)
        with patch.object(controller, "get_pre_review_service", return_value=service), app.test_client() as client:
            for method, suffix in (("post", "export"), ("post", "review-conclusions/export"), ("get", "review-conclusions/download")):
                response = getattr(client, method)("/pre-review/runs/failed-run/" + suffix)
                self.assertEqual(response.status_code, 400)
                self.assertIn("未完成", response.get_json(force=True)["message"])

    def test_historical_summary_does_not_become_failed(self):
        for summary in ('{}', '{"review_complete":true}', '{"execution_status":"completed"}'):
            self.assertFalse(PreReviewService._run_has_incomplete_review(SimpleNamespace(summary=summary)))

    def test_section_conclusion_ordering_uses_compact_read_only_sections(self):
        run = SimpleNamespace(project_id="project-1", source_doc_id="doc-1")
        service = object.__new__(PreReviewService)
        service.db_conn = _Connection(
            _Session(
                {
                    PreReviewRun: ("first", run),
                    PreReviewSectionConclusion: [],
                    PreReviewSectionOutput: [],
                }
            )
        )
        calls = []

        def get_sections(*, project_id, doc_id):
            calls.append((project_id, doc_id))
            return True, "success", {"sections": [{"section_id": "3.2.p.2"}]}

        service.get_submission_section_overview = get_sections

        self.assertEqual(service.get_section_conclusions("run-1"), [])
        self.assertEqual(calls, [("project-1", "doc-1")])

    def test_run_overview_does_not_request_full_document_or_full_traces(self):
        run = SimpleNamespace(
            run_id="run-1",
            project_id="project-1",
            source_doc_id="doc-1",
            strategy="chapter_review",
            summary="{}",
        )
        service = object.__new__(PreReviewService)
        service.db_conn = _Connection(_Session({PreReviewRun: ("first", run)}))
        section_calls = []
        trace_calls = []

        def get_sections(*, project_id, doc_id):
            section_calls.append((project_id, doc_id))
            return True, "success", {
                "content_ready": True,
                "sections": [
                    {
                        "section_id": "3.2.p.2",
                        "section_code": "3.2.p.2",
                        "section_name": "产品开发",
                        "title_path": ["制剂", "产品开发"],
                        "parent_section_id": "3.2.p",
                        "char_count": 2_000_000,
                        "content_preview": "短摘要",
                    }
                ]
            }

        def get_traces(*, run_id, section_id="", compact=False):
            trace_calls.append((run_id, section_id, compact))
            return []

        service.get_submission_section_overview = get_sections
        service.get_section_conclusions = lambda run_id, section_id="": []
        service.get_standardized_section_outputs = lambda run_id, section_id="": []
        service.get_section_traces = get_traces
        service._normalize_text_list = lambda value: list(value or [])
        service._resolve_pre_review_mode = lambda config: "chapter_review"

        ok, _, payload = service.get_run_section_overview("run-1")

        self.assertTrue(ok)
        self.assertEqual(section_calls, [("project-1", "doc-1")])
        self.assertEqual(trace_calls, [("run-1", "", True)])
        self.assertEqual(payload["review_units"], [])
        self.assertEqual(payload["chapter_structure"], [])
        self.assertEqual(
            set(payload["sections"][0]),
            {
                "section_id",
                "section_code",
                "section_name",
                "title_path",
                "parent_section_id",
                "char_count",
                "content_preview",
            },
        )
        overview_size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        self.assertLess(overview_size, 16 * 1024)
        self.assertNotIn("raw_content", payload["sections"][0])
        self.assertNotIn("cleaned_markdown", payload["sections"][0])

    def test_compact_trace_never_reads_artifact_or_enriches_materials(self):
        trace_row = SimpleNamespace(
            run_id="run-1",
            section_id="3.2.p.2",
            trace_json="{}",
            create_time=SimpleNamespace(strftime=lambda fmt: "2026-08-18 10:00:00"),
        )
        service = object.__new__(PreReviewService)
        service.db_conn = _Connection(_Session({PreReviewSectionTrace: [trace_row]}))
        service._summarize_source_breakdown = lambda materials: {"drug_data": len(materials)}
        service._enrich_retrieved_materials = lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("紧凑 trace 不得富化检索原文")
        )
        raw_trace = {
            "trace_schema": "chapter_review_v1",
            "coordination": {
                "retrieval": {
                    "hit_count": 1,
                    "grouped_doc_count": 1,
                    "documents": [{"text": "检索原文" * 50000}],
                },
                "prompt": "大模型上下文" * 50000,
            },
            "memory": {"hit_count": 2, "items": [{"text": "记忆原文" * 50000}]},
            "agent": {"findings_count": 3, "score": 0.8, "findings": ["A" * 500000]},
            "retrieved_materials": [{"source_type": "drug_data", "text": "资料" * 100000}],
            "trace_artifact": {"file_path": "/path/that/must/not/be/read.json"},
        }

        with patch(
            "agent.agent_backend.services.pre_review_service.PreReviewRepository.load_trace_payload",
            return_value=raw_trace,
        ), patch(
            "agent.agent_backend.services.pre_review_service.os.path.exists",
            side_effect=AssertionError("紧凑 trace 不得读取 artifact"),
        ):
            result = service.get_section_traces("run-1", compact=True)

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["response_mode"], "compact")
        self.assertNotIn("retrieved_materials", result[0])
        self.assertNotIn("trace_artifact", result[0])
        full_size = len(json.dumps(raw_trace, ensure_ascii=False).encode("utf-8"))
        compact_size = len(json.dumps(result[0], ensure_ascii=False).encode("utf-8"))
        self.assertLess(compact_size, full_size * 0.01)
        self.assertNotIn("documents", result[0]["coordination"]["retrieval"])
        self.assertNotIn("items", result[0]["memory"])
        self.assertNotIn("findings", result[0]["agent"])

    def test_historical_overview_survives_missing_source_content(self):
        run = SimpleNamespace(
            run_id="run-history",
            project_id="project-history",
            source_doc_id="deleted-doc",
            strategy="chapter_review",
            summary="{}",
        )
        service = object.__new__(PreReviewService)
        service.db_conn = _Connection(_Session({PreReviewRun: ("first", run)}))
        service.get_submission_section_overview = lambda **kwargs: (
            False,
            "submission content not ready; reparse required",
            None,
        )
        service.get_ctd_section_catalog = lambda project_id="": {
            "flat_sections": [
                {"section_id": "3.2.p.2", "section_name": "产品开发"}
            ]
        }
        service.get_section_conclusions = lambda run_id, section_id="": [
            {
                "section_id": "3.2.p.2.9",
                "section_name": "历史动态章节",
                "conclusion": "已持久化结论",
                "risk_level": "low",
                "standard_output": {},
            }
        ]
        service.get_standardized_section_outputs = lambda run_id, section_id="": []
        service.get_section_traces = lambda **kwargs: []
        service._normalize_text_list = lambda value: list(value or [])
        service._normalize_section_review_output = lambda payload, **kwargs: {
            "section_id": kwargs.get("section_id", ""),
            "conclusion": kwargs.get("fallback_conclusion", ""),
        }
        service._resolve_pre_review_mode = lambda config: "chapter_review"

        ok, _, payload = service.get_run_section_overview("run-history")

        self.assertTrue(ok)
        self.assertFalse(payload["source_content_available"])
        self.assertEqual(payload["overview_warnings"][0]["code"], "source_content_unavailable")
        self.assertIn("3.2.p.2.9", payload["reviewed_section_ids"])
        self.assertIn(
            "3.2.p.2.9",
            [item["section_id"] for item in payload["sections"]],
        )
        self.assertEqual(
            payload["conclusion_by_section_id"]["3.2.p.2.9"]["conclusion"],
            "已持久化结论",
        )

    def test_compact_content_contract_remains_nine_fields(self):
        payload = PreReviewService._compact_structured_project_payload(
            {
                "sections": [
                    {
                        "section_id": "3.2.p.2",
                        "section_name": "产品开发",
                        "content": "正文",
                    }
                ]
            }
        )

        self.assertEqual(
            set(payload["sections"][0]),
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


if __name__ == "__main__":
    unittest.main()
